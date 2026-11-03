import os
import json
import tempfile
from pathlib import Path
from .config import settings
from .domain import digest
from .errors import Problem

_deleted_world_cleanup_cursor = None


def write_private(owner_id, name, data):
    tombstone = settings.storage_path / ".deleted" / owner_id
    if tombstone.exists():
        raise Problem("TASK_CANCELLED", "账号已清理")
    root = settings.storage_path / owner_id
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / name
    # Reclaimed EPUB parsers can publish the same content-addressed image.
    # Each writer owns its temporary file; one replace cannot consume another
    # writer's pending file. The final path still changes atomically.
    descriptor, temporary_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if tombstone.exists():
            raise Problem("TASK_CANCELLED", "账号已清理")
        # Windows readers can briefly deny atomic replacement while serving a
        # received journal. Keep the complete old file until replacement works;
        # never truncate it or turn a sharing collision into a lost receipt.
        for attempt in range(6):
            if tombstone.exists():raise Problem('TASK_CANCELLED','账号已清理')
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                if getattr(error,'winerror',None) not in (5,32) or attempt==5:raise
                if tombstone.exists():raise Problem('TASK_CANCELLED','账号已清理')
                import time
                time.sleep(.01*(attempt+1))
    finally:
        temporary.unlink(missing_ok=True)
    if tombstone.exists():
        path.unlink(missing_ok=True)
        raise Problem("TASK_CANCELLED", "账号已清理")
    return name


def _write_task_journal(owner_id, task_id, name, data):
    markers = [private_path(owner_id, prefix+task_id+".marker") for prefix in ("world-deleted-", "book-deleted-")]
    if any(marker.exists() for marker in markers):
        raise Problem("TASK_CANCELLED", "内容已删除")
    file_ref = write_private(owner_id, name, data)
    if any(marker.exists() for marker in markers):
        private_path(owner_id, file_ref).unlink(missing_ok=True)
        raise Problem("TASK_CANCELLED", "内容已删除")
    return file_ref


def journal(owner_id, task_id, call_id, result):
    data = json.dumps({"taskId": task_id, "callId": call_id, "result": result, "hash": digest(json.dumps(result, ensure_ascii=False, sort_keys=True))}, ensure_ascii=False).encode()
    return _write_task_journal(owner_id, task_id, f"journal-{task_id}-{call_id}.json", data)


def mark_synced(owner_id, task_id, calls):
    from datetime import datetime, timezone
    for call in calls.values():
        path=private_path(owner_id,f"journal-{task_id}-{call['callId']}.json")
        try:
            value=json.loads(path.read_text("utf-8"))
            value["syncedAt"]=datetime.now(timezone.utc).isoformat()
            _write_task_journal(owner_id,task_id,path.name,json.dumps(value,ensure_ascii=False).encode())
        except FileNotFoundError:
            # A lifecycle cleanup may remove the optional recovery file while
            # the completed task is being marked. Do not turn success into failure.
            continue
        except Problem as exc:
            if exc.code != "TASK_CANCELLED":
                raise
            return


def clean_deleted_world_journals(owner_id=None, world_id=None):
    """Retry committed world deletions without depending on the HTTP process.

    A pending task is the durable cleanup outbox. Ordinary journal writes still
    need no database connection: the marker's before/after checks fence writers,
    and this purge removes a receipt written in the commit-to-marker interval.
    """
    import logging
    from sqlalchemy import select, case
    from .db import SessionLocal
    from .models import Task

    criteria = [Task.task_type.like("world_%"),
                Task.result["deleted"].astext == "true",
                Task.result["worldId"].astext == Task.payload["worldId"].astext,
                Task.checkpoint["worldPrivateCleanupPending"].astext == "true"]
    if owner_id is not None:
        criteria.append(Task.owner_id == owner_id)
    if world_id is not None:
        criteria.append(Task.payload["worldId"].astext == world_id)
    global _deleted_world_cleanup_cursor
    with SessionLocal() as db:
        query = select(Task.id).where(*criteria)
        if owner_id is None and world_id is None:
            # Rotate finite batches, so a persistent disk fault on early task
            # IDs cannot starve another owner's later pending deletions.
            if _deleted_world_cleanup_cursor is not None:
                query = query.order_by(case((Task.id > _deleted_world_cleanup_cursor, 0), else_=1))
            query = query.order_by(Task.id)
            query = query.limit(20)
        else:
            query = query.order_by(Task.id)
        task_ids = list(db.scalars(query))
        if task_ids and owner_id is None and world_id is None:
            _deleted_world_cleanup_cursor = task_ids[-1]

    complete = True
    for task_id in task_ids:
        with SessionLocal() as db:
            task = db.scalar(select(Task).where(Task.id == task_id, *criteria)
                             .with_for_update(skip_locked=True))
            if task is None:
                # Another cleanup may own the row. The caller can safely retry;
                # never acknowledge that locked pending work has completed.
                complete = False
                continue
            try:
                if not (settings.storage_path / ".deleted" / task.owner_id).exists():
                    write_private(task.owner_id, "world-deleted-" + task.id + ".marker", b"deleted")
                for call in task.checkpoint.get("calls", {}).values():
                    private_path(task.owner_id, "journal-" + task.id + "-" + call["callId"] + ".json").unlink(missing_ok=True)
                # Incremental public-body journals are separate from call receipts.
                # Keep their unlink in the committed tombstone outbox transaction:
                # any disk failure must retain pending cleanup for owner retry.
                root = private_path(task.owner_id, "world-deleted-" + task.id + ".marker").parent
                for stream_path in root.glob("journal-stream-" + task.id + "-*.json"):
                    stream_path.unlink(missing_ok=True)
                task.checkpoint = {key: value for key, value in task.checkpoint.items()
                                   if key != "worldPrivateCleanupPending"}
                db.commit()
            except (OSError, Problem) as error:
                db.rollback()
                complete = False
                logging.getLogger("reader.storage").warning(
                    "deleted world private cleanup pending task=%s error=%s", task_id, type(error).__name__)
    return complete


def clean_synced_journals():
    import re
    from datetime import datetime, timezone, timedelta
    root=settings.storage_path.resolve()
    if not root.exists():
        return
    for folder in root.iterdir():
        if not folder.is_dir() or not re.fullmatch(r"[a-f0-9]{32}",folder.name):
            continue
        for path in folder.glob("journal-*.json"):
            try:
                value=json.loads(path.read_text("utf-8"))
                if value.get("syncedAt") and datetime.fromisoformat(value["syncedAt"])<datetime.now(timezone.utc)-timedelta(hours=24):
                    path.unlink()
            except (ValueError,OSError):
                continue


def private_path(owner_id, name):
    root = (settings.storage_path / owner_id).resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise ValueError("Invalid private reference")
    return path


def clean_orphan_files():
    """Remove only unreferenced files older than 24h; never erase recovery journals."""
    import re
    import time
    from sqlalchemy import select, text
    from .db import SessionLocal
    from .models import SourceDocument, Task
    protected={}
    with SessionLocal() as db:
        for source in db.scalars(select(SourceDocument)):
            protected.setdefault(source.owner_id,set()).update([source.file_ref,source.normalized_ref,*source.source_map.get('assets',{}).values()])
        for task in db.scalars(select(Task).where(Task.task_type=='import')):
            if task.persistence_state=='recoverable' or task.state not in {'failed','cancelled'} and task.result.get('importStatus')!='reading_ready':
                protected.setdefault(task.owner_id,set()).update(filter(None,[task.payload.get('fileRef'),task.checkpoint.get('parsedRef')]))
                # EPUB assets created before confirmation are declared in parsed data.
                parsed=task.checkpoint.get('parsedRef')
                if parsed:
                    path=private_path(task.owner_id,parsed)
                    if path.exists():protected[task.owner_id].update(json.loads(path.read_text('utf-8')).get('assets',{}).values())
        for task in db.scalars(select(Task).where(Task.payload['protocol'].astext == 'reader-prose-v1')):
            protected.setdefault(task.owner_id, set()).update(task.checkpoint.get('proseStreamRefs', []))
        # Scene bytes share the owner's private directory with imported books.
        # A paid image remains referenced even when it is older than the orphan
        # retention window. Protect both the displayed image and its crop source.
        if db.scalar(text("SELECT to_regclass('world.scene_images')")) is not None:
            from .scenes.models import SceneImage
            from .scenes.characters import source_name
            for scene in db.execute(select(SceneImage.owner_id, SceneImage.world_id, SceneImage.id,
                    SceneImage.image['file'].astext.label('file_ref'))):
                refs = protected.setdefault(scene.owner_id, set())
                if scene.file_ref:
                    refs.add(scene.file_ref)
                # A received image can be durable before its database receipt
                # is committed. Its deterministic file is still recoverable.
                refs.add('scene-' + scene.world_id + '-' + scene.id + '.enc')
                refs.add(source_name(scene))
    root=settings.storage_path.resolve()
    if not root.exists():return
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink() or not re.fullmatch(r'[a-f0-9]{32}',folder.name):continue
        for path in folder.iterdir():
            if path.is_symlink() or not path.is_file() or path.name.startswith(('journal-', 'world-deleted-', 'book-deleted-', 'book-usage-')) or path.name in protected.get(folder.name,set()):continue
            try:
                if path.stat().st_mtime<time.time()-86400:path.unlink()
            except OSError:continue
