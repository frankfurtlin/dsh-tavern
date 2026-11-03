"""Validated new-copy restore in one SQL transaction, never worker replay.

Only the explicit domain registry is writable. This adapter owns a fresh
Session transaction; HTTP callers must finish authentication/preview first.
Source files use newly allocated names and are removed on transaction failure.
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re
from bs4 import BeautifulSoup

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, select

from .. import models as reader
from ..db import SessionLocal
from ..domain import boundary_hash, canonical, digest, utf16_slice, validate_anchor
from ..storage import private_path, write_private
from ..importer import normalize
from ..world import models as world
from ..world.state import checkpoint_hash
from .domain_codec import decode_inline, read_text_frame
from .domain_schema import JSON_FOREIGN, ROW_SPECS, SPEC_BY_KIND
from .errors import ArchiveError
from .manifest import pointer_get
from .package import validate_package_files
from .remap import build_restore_plan,fictional_fields_path,authored_source_key
from .world_schema import checkpoint as validate_world_checkpoint, definition as validate_world_definition
from ..assets.models import AssetVariant
from .asset_domain import variant_values,validate_restored_images
from .prose_history import validate_checkpoint,validate_generation_rows
from .world_ids import remap_world_local_ids
from .reader_ids import remap_reader_local_ids
from .reader_corrections import validate_reader_corrections
from .world_scenes import scenes,check_hashes,validate_candidates
from . import playback_domain,stream_history
from . import accepted_basis
from . import play_controls


def _date(value):
    if not isinstance(value, str):
        raise ArchiveError("BACKUP_ROW_INVALID")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise ArchiveError("BACKUP_ROW_INVALID") from error
    if result.tzinfo is None:
        raise ArchiveError("BACKUP_ROW_INVALID")
    return result


def _nested(value, root, files, row_type=None, parts=("fields",)):
    if fictional_fields_path(row_type,parts):return deepcopy(value)
    if isinstance(value, list):
        return [_nested(child,root,files,row_type,parts+(str(i),)) for i,child in enumerate(value)]
    if not isinstance(value, dict):
        return deepcopy(value)
    result = {key: _nested(child,root,files,row_type,parts+(key,)) for key, child in value.items()
              if key not in {"portableTextRef", "portableInline"}}
    if "portableTextRef" in value:
        if "text" in value:
            raise ArchiveError("BACKUP_TEXT_REFERENCE_INVALID")
        result["text"] = read_text_frame(root, value["portableTextRef"], files)
    if "portableInline" in value:
        if "html" in value:
            raise ArchiveError("BACKUP_INLINE_INVALID")
        result["html"] = decode_inline(value["portableInline"])
    return result


def _fields(spec, record, root, files):
    fields = record.get("fields")
    bodies = record.get("bodies")
    expected = set(spec.fields) - set(spec.prose)
    if spec.kind in {"resume_target", "playback_position"}: expected.remove("story_id")
    if spec.kind == 'world_chat' and isinstance(fields, dict) and set(fields) == expected-{'status','updated_at'}:
        fields = {**fields, 'status': 'active', 'updated_at': record.get('createdAt', record.get('created_at'))}
    if spec.kind=="task" and isinstance(fields,dict) and set(fields)==expected-{"checkpoint"}:
        # The earlier v1 writer omitted display-only checkpoint metadata.
        fields={**fields,"checkpoint":{}}
    if not isinstance(fields, dict) or set(fields) != expected or not isinstance(bodies, dict) or set(bodies) != set(spec.prose):
        raise ArchiveError("BACKUP_ROW_FIELDS_INVALID")
    values = _nested(fields,root,files,spec.model.__name__)
    for name, frame in bodies.items(): values[name] = read_text_frame(root, frame, files)
    for name, value in list(values.items()):
        column = spec.model.__table__.c[name]
        if value is None:
            if not column.nullable: raise ArchiveError("BACKUP_ROW_INVALID")
        elif isinstance(column.type, Boolean):
            if type(value) is not bool: raise ArchiveError("BACKUP_ROW_INVALID")
        elif isinstance(column.type, Integer):
            if type(value) is not int or not -2147483648 <= value <= 2147483647: raise ArchiveError("BACKUP_ROW_INVALID")
        elif isinstance(column.type, DateTime):
            values[name] = _date(value)
        elif isinstance(column.type, String):
            if not isinstance(value, str) or (column.type.length and len(value) > column.type.length): raise ArchiveError("BACKUP_ROW_INVALID")
        elif isinstance(column.type, JSON):
            if not isinstance(value, (dict, list)): raise ArchiveError("BACKUP_ROW_INVALID")
    if spec.kind=="task" and any(not isinstance(values.get(key),dict) for key in ("payload","result","checkpoint")):
        raise ArchiveError("BACKUP_ROW_INVALID")
    return values


def _reference_closure(values, spec, ids, task_types):
    def check(kind, identifier):
        if kind=="checkpoint" and spec.kind=="world_turn" and identifier in ids.get("world_scene",set()):return
        if identifier is not None and identifier not in ids.get(kind, set()):
            raise ArchiveError("BACKUP_REFERENCE_UNDECLARED")
    for name, kind in spec.foreign.items():
        if spec.kind=="role_session" and name=="snapshot_id" and values[name]=="" and values["status"]=="needs_refresh":continue
        check(kind, values.get(name))
    playback_domain.references(spec.kind, values, lambda name, kind: check(kind, values[name]))
    if spec.kind=='play_control':check(play_controls.scope_type(values['scope_kind']),values['scope_id'])
    if spec.kind=="asset_ref":
        kind="paragraph" if values["scope"].get("kind")=="book" else "checkpoint"
        check(kind,values["content_id"])
        if kind=="paragraph":check(kind,values["paragraph_id"])
    task_type = values.get("task_type") if spec.kind == "task" else task_types.get(values.get("task_id")) if spec.kind == "task_event" else None
    if spec.kind in {"character_alias", "character_scope_revision"}:
        kind = {"reader": "branch", "world": "world"}.get(values["scope_kind"])
        if kind is None: raise ArchiveError("BACKUP_SCOPE_INVALID")
        check(kind, values["scope_id"])
    def walk(value,parts=("fields",)):
        if fictional_fields_path(spec.model.__name__,parts):return
        if isinstance(value, list):
            for i,child in enumerate(value):walk(child,parts+(str(i),))
        elif isinstance(value, dict):
            for key, child in value.items():
                if authored_source_key(spec.model.__name__,parts,key):continue
                if spec.kind=='task' and key=='acceptedEventBasis':
                    for _,kind,identifier in accepted_basis.references(child):check(kind,identifier)
                    continue
                if spec.kind=='task' and key=='acceptedSourceSnapshotId':check('knowledge_snapshot',child);continue
                if spec.kind=='task' and key=='acceptedCandidateBindings':
                    accepted_basis.validate_bindings(child)
                    for binding in child:check('generation_batch',binding['runId'])
                    continue
                if key == "turnId" and spec.kind in {"task", "task_event"} and not (task_type or "").startswith("world_"): continue
                if key == "scopeId" and value.get("kind") in {"reader", "world"} and isinstance(child, str): check("branch" if value["kind"] == "reader" else "world", child)
                elif key=="candidateId" and isinstance(child,str) and child in ids.get("generation_batch",set()):check("generation_batch",child)
                elif key=="contentId" and isinstance(child,str) and child in ids.get("stream_content",set()):check("stream_content",child)
                elif key in JSON_FOREIGN and isinstance(child, str): check(JSON_FOREIGN[key], child)
                elif key=="chapterIds" and isinstance(child,list):
                    for identifier in child:check("chapter",identifier)
                elif key=="eventId" and isinstance(child,str) and spec.kind in {"world_turn","checkpoint","world_event"}:
                    if child not in ids.get("world_event_token",set()):check("world_event",child)
                elif key in {"event_ids", "eventIds", "resolvedEvents", "reachedEvents"} and isinstance(child, list):
                    for identifier in child:
                        if identifier not in ids.get("world_event_token", set()): check("world_event", identifier)
                elif key in {"worldCheckpointId", "forkCheckpointId", "headCheckpointId"} and isinstance(child, str): check("checkpoint", child)
                else:walk(child,parts+(key,))
    walk(values)


def _refresh_anchors(value, blocks, revisions, *, refresh=True):
    if isinstance(value, list):
        for child in value: _refresh_anchors(child, blocks, revisions, refresh=refresh)
    elif isinstance(value, dict):
        if {"bookId", "branchId", "revisionId", "chapterId", "blockId", "start", "end", "offsetUnit", "quoteHash"} <= set(value):
            block = blocks.get(value["blockId"]); revision = revisions.get(value["revisionId"])
            if block is None or revision is None or value["offsetUnit"] != "utf16" or type(value["start"]) is not int or type(value["end"]) is not int:
                raise ArchiveError("BACKUP_ANCHOR_INVALID")
            if block.book_id != value["bookId"] or block.chapter_id != value["chapterId"] or revision.branch_id != value["branchId"] or not any(c["id"] == block.chapter_id and block.id in c["blocks"] for c in revision.manifest):
                raise ArchiveError("BACKUP_ANCHOR_INVALID")
            selected = utf16_slice(block.text, value["start"], value["end"])
            expected = boundary_hash(block.text, revision.id, block.id, value["start"]) if value["start"] == value["end"] else digest(selected)
            if not refresh and value["quoteHash"] != expected:
                raise ArchiveError("BACKUP_ANCHOR_INVALID")
            if refresh: value["quoteHash"] = expected
        for child in value.values(): _refresh_anchors(child, blocks, revisions, refresh=refresh)


def _identity_origin(identity):
    source = identity.source_ref
    if source.get("kind") == "reader-proof" and all(source.get(key) is not None for key in ("blockId", "start", "originalLabel")):
        return "proof:" + digest(canonical({key: source[key] for key in ("blockId", "start", "originalLabel")}))
    if source.get("kind") == "world" and source.get("worldId") and source.get("entityId"):
        return "world:" + source["worldId"] + ":" + source["entityId"]
    if source.get("kind") == "reader" and source.get("branchId") and source.get("entityId"):
        return "reader:" + source["branchId"] + ":" + source["entityId"]
    return "portable:" + identity.id


def _relationships(rows, token_worlds=None, scene_metadata=None, *, refresh_anchors=True):
    def acyclic(records,parent_name,code):
        finished=set()
        for identifier in records:
            chain=set();current=identifier
            while current is not None and current not in finished:
                if current in chain or current not in records:raise ArchiveError(code)
                chain.add(current);current=getattr(records[current],parent_name)
            finished.update(chain)
    books, branches, revisions = rows.get("book", {}), rows.get("branch", {}), rows.get("revision", {})
    acyclic(branches,"parent_branch_id","BACKUP_READER_GRAPH_INVALID")
    acyclic(revisions,"parent_revision_id","BACKUP_READER_GRAPH_INVALID")
    blocks = rows.get("paragraph", {})
    for book in books.values():
        branch = branches.get(book.original_branch_id); revision = revisions.get(book.original_revision_id)
        if not branch or branch.book_id != book.id or not revision or revision.branch_id != branch.id:
            raise ArchiveError("BACKUP_READER_GRAPH_INVALID")
    for branch in branches.values():
        head = revisions.get(branch.head_revision_id); parent = branches.get(branch.parent_branch_id)
        if not head or head.branch_id != branch.id or (parent and parent.book_id != branch.book_id):
            raise ArchiveError("BACKUP_READER_GRAPH_INVALID")
        if branch.fork_anchor and (parent is None or branch.fork_anchor.get("bookId")!=branch.book_id or branch.fork_anchor.get("branchId")!=parent.id):
            raise ArchiveError("BACKUP_READER_GRAPH_INVALID")
    from ..prose.selection_edits import validate_selection_families
    try:
        validate_selection_families(revisions.values(), blocks.values(), lambda model, identifier:
            (blocks if model is reader.ContentBlock else branches if model is reader.Branch else revisions).get(identifier), verify_hash=not refresh_anchors)
    except (ValueError, KeyError, TypeError) as error:
        raise ArchiveError('BACKUP_READER_GRAPH_INVALID') from error
    for revision in revisions.values():
        from ..prose.prefix_fragments import validate_revision_fragments
        try:
            validate_revision_fragments(revision, lambda model, identifier:
                (blocks if model is reader.ContentBlock else revisions).get(identifier))
        except (ValueError, KeyError, TypeError) as error:
            raise ArchiveError('BACKUP_READER_GRAPH_INVALID') from error
        branch = branches[revision.branch_id]
        parent = revisions.get(revision.parent_revision_id)
        if parent and branches[parent.branch_id].book_id != branch.book_id:
            raise ArchiveError("BACKUP_READER_GRAPH_INVALID")
        if not isinstance(revision.manifest, list) or not revision.manifest:
            raise ArchiveError("BACKUP_READER_GRAPH_INVALID")
        seen = set()
        for chapter in revision.manifest:
            if not isinstance(chapter, dict) or set(chapter) != {"id", "title", "blocks"} or not isinstance(chapter["title"], str) or not isinstance(chapter["blocks"], list):
                raise ArchiveError("BACKUP_READER_GRAPH_INVALID")
            for identifier in chapter["blocks"]:
                block = blocks.get(identifier)
                if block is None or block.book_id != branch.book_id or block.chapter_id != chapter["id"] or identifier in seen:
                    raise ArchiveError("BACKUP_READER_GRAPH_INVALID")
                seen.add(identifier)
    worlds, lines, points = rows.get("world", {}), rows.get("world_line", {}), rows.get("checkpoint", {})
    acyclic(lines,"parent_line_id","BACKUP_WORLD_GRAPH_INVALID")
    acyclic(points,"parent_checkpoint_id","BACKUP_WORLD_GRAPH_INVALID")
    # Parent-tree intervals authorize evidence in O(1), including shared trunk
    # checkpoints, without an O(n虏) scan of every historical parent chain.
    children={identifier:[] for identifier in points};roots=[]
    for identifier,point in points.items():
        if point.parent_checkpoint_id is None:roots.append(identifier)
        elif point.parent_checkpoint_id in children:children[point.parent_checkpoint_id].append(identifier)
        else:raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
    starts,ends={},{};tick=0;stack=[(identifier,False) for identifier in roots]
    while stack:
        identifier,leaving=stack.pop()
        if leaving:ends[identifier]=tick;continue
        starts[identifier]=tick;tick+=1;stack.append((identifier,True))
        stack.extend((child,False) for child in children[identifier])
    def ancestor(first,last):
        return first in starts and last in starts and starts[first]<=starts[last]<ends[first]
    play_controls.validate(rows, ancestor)
    for item in worlds.values():
        line = lines.get(item.active_line_id)
        if item.active_line_id and (line is None or line.world_id != item.id): raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
        if item.status == "active": validate_world_definition(item.definition)
    for line in lines.values():
        head = points.get(line.head_checkpoint_id); parent = lines.get(line.parent_line_id)
        if head is None or head.world_id != line.world_id or head.line_id != line.id or (parent and parent.world_id != line.world_id):
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
        fork = points.get(line.fork_checkpoint_id)
        if line.fork_checkpoint_id and (fork is None or fork.world_id != line.world_id): raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
        selected=line.reading_position.get("checkpointId")
        if selected is not None:
            head_id=line.head_checkpoint_id
            while head_id is not None and head_id!=selected:head_id=points[head_id].parent_checkpoint_id
            if head_id!=selected:raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
    tokens_by_world = {}
    for identifier, world_id in (token_worlds or {}).items(): tokens_by_world.setdefault(world_id,set()).add(identifier)
    for point in points.values():
        tokens=tokens_by_world.get(point.world_id,set())
        validate_world_checkpoint(point.state,point.definition,event_tokens=tokens if token_worlds is not None else None)
        parent = points.get(point.parent_checkpoint_id)
        if lines[point.line_id].world_id != point.world_id or (parent and parent.world_id != point.world_id):
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
        for entity in point.state["entities"].values():
            for proof in entity.get("relationEvidence",{}).values():
                proof_point=points.get(proof["checkpointId"])
                if not proof_point or proof_point.world_id!=point.world_id or not ancestor(proof_point.id,point.id):
                    raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
    for event in rows.get("world_event", {}).values():
        if points[event.checkpoint_id].world_id != event.world_id:
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
        if event.data.get("id") is not None and token_worlds is not None and token_worlds.get(event.data["id"]) != event.world_id:
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
    for memory in rows.get("memory", {}).values():
        if lines[memory.line_id].world_id != memory.world_id or points[memory.checkpoint_id].line_id != memory.line_id:
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
        if any(points[key].world_id != memory.world_id for key in (memory.checkpoint_id, memory.first_checkpoint_id, memory.last_checkpoint_id)):
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
        if not ancestor(memory.first_checkpoint_id,memory.last_checkpoint_id) or not ancestor(memory.last_checkpoint_id,memory.checkpoint_id):
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
        if token_worlds is not None and any(token_worlds.get(key)!=memory.world_id and (rows.get("world_event",{}).get(key) is None or rows["world_event"][key].world_id!=memory.world_id) for key in memory.event_ids):
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
    def check_memory(value,world_id):
        if isinstance(value,list):
            for entry in value:check_memory(entry,world_id)
        elif isinstance(value,dict):
            for key,child in value.items():
                if key in {"firstCheckpointId","lastCheckpointId","checkpointId"} and isinstance(child,str) and points[child].world_id!=world_id:
                    raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
                if key=="eventIds" and isinstance(child,list) and token_worlds is not None:
                    for identifier in child:
                        event=rows.get("world_event",{}).get(identifier)
                        if token_worlds.get(identifier)!=world_id and (event is None or event.world_id!=world_id):raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
                check_memory(child,world_id)
    for point in points.values():check_memory(point.memory,point.world_id)
    for turn in rows.get("world_turn", {}).values():
        if lines[turn.line_id].world_id != turn.world_id or points[turn.parent_checkpoint_id].world_id != turn.world_id:
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
        if turn.checkpoint_id and (points[turn.checkpoint_id].world_id != turn.world_id or points[turn.checkpoint_id].line_id != turn.line_id):
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
        task = rows["task"][turn.task_id]
        if task.payload.get("worldId") != turn.world_id or task.payload.get("lineId") != turn.line_id:
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
    validate_candidates(rows,scene_metadata or {},token_worlds or {},ancestor)
    for publication in rows.get("world_publication", {}).values():
        if points[publication.checkpoint_id].world_id != publication.world_id or branches[publication.branch_id].book_id != publication.book_id or revisions[publication.revision_id].branch_id != publication.branch_id:
            raise ArchiveError("BACKUP_WORLD_GRAPH_INVALID")
        if any(publication.anchor.get(key)!=getattr(publication,column) for key,column in (("bookId","book_id"),("branchId","branch_id"),("revisionId","revision_id"))):
            raise ArchiveError("BACKUP_READER_GRAPH_INVALID")
    for kind in ("progress", "note", "knowledge_snapshot", "analysis_chunk", "role_session"):
        for record in rows.get(kind, {}).values():
            if branches[record.branch_id].book_id != record.book_id:
                raise ArchiveError("BACKUP_READER_GRAPH_INVALID")
            revision_id = getattr(record, "revision_id", getattr(record, "pinned_revision_id", None))
            if revision_id and revisions[revision_id].branch_id != record.branch_id:
                raise ArchiveError("BACKUP_READER_GRAPH_INVALID")
            for name in ("anchor","max_observed_anchor","boundary","pinned_anchor"):
                anchor=getattr(record,name,None)
                if anchor and (anchor.get("bookId")!=record.book_id or anchor.get("branchId")!=record.branch_id or anchor.get("revisionId")!=revision_id):
                    raise ArchiveError("BACKUP_READER_GRAPH_INVALID")
    for records in rows.values():
        for item in records.values():
            for column in item.__table__.columns:
                if column.name not in {"id", "owner_id", "created_at"}: _refresh_anchors(getattr(item, column.name), blocks, revisions, refresh=refresh_anchors)


def _committed_rows(rows, owner):
    """A separate connection checks an unknown COMMIT acknowledgement.

    True means every newly allocated row is present and owned; False means
    none were committed. Partial or unavailable observations remain unknown.
    Never remove files merely because the client saw a transaction exception.
    """
    observed = []
    try:
        with SessionLocal() as check:
            for records in rows.values():
                for instance in records.values():
                    primary=[getattr(instance,column.name) for column in instance.__table__.primary_key.columns]
                    actual = check.get(type(instance),primary[0] if len(primary)==1 else tuple(primary))
                    observed.append(actual is not None and (not hasattr(actual, "owner_id") or actual.owner_id == owner))
        if observed and all(observed): return True
        if not any(observed): return False
    except Exception:
        return None
    return None


def restore_domain(db, prepared, target_owner, *, fault=None, authorize=None, record_receipt=None):
    """Restore only a fully validated, explicitly confirmed private stage.

    Preview ownership/expiry and explicit confirmation are the router's duty.
    Revalidation here fences staged-file changes. A new Session is mandatory;
    this function commits once and returns only after all rows/files succeed.
    """
    if db.in_transaction(): raise ArchiveError("BACKUP_RESTORE_SESSION_BUSY")
    root = prepared.files_root
    documents = validate_package_files(root, prepared.manifest, prepared.limits)
    if any('acceptedEventBasis' in record.get('fields',{}).get('checkpoint',{}) or 'acceptedBasisSchema' in record.get('fields',{}).get('payload',{}) or 'acceptedBasisSchema' in record.get('fields',{}).get('request',{}) for document in documents.values() for record in document.get('rows',[]) if record.get('rowType') in ('Task','GenerationBatch')):
        from .preflight import preflight_worlds
        preflight_worlds(prepared)
    files = {entry["path"]: entry for entry in prepared.manifest["files"]}
    for document in documents.values():
        for record in document.get("rows",[]):
            if record.get("rowType")=="WorldTurn":check_hashes(scenes(_nested(record["fields"],root,files,"WorldTurn")["result"]))
    plan = build_restore_plan(prepared.manifest, documents, target_owner, prepared.limits)
    files = {entry["path"]: entry for entry in prepared.manifest["files"]}
    ids = {}
    for (kind, _), identifier in plan.id_map.items(): ids.setdefault(kind, set()).add(identifier)
    task_types = {item.new_id: pointer_get(plan.documents[item.path], item.id_pointer.rsplit("/", 1)[0])["fields"].get("task_type")
                  for item in plan.historical_objects if item.kind == "task"}
    if len(plan.roots) != 1: raise ArchiveError("BACKUP_ROOT_INVALID")
    if plan.roots[0]["type"] == "world":
        story_id = "world-" + plan.roots[0]["id"]
    else:
        story = next((item for item in plan.active_objects if item.kind == "story" and item.new_id == plan.roots[0]["id"]), None)
        if story is None: raise ArchiveError("BACKUP_ROOT_INVALID")
        record = pointer_get(plan.documents[story.path], story.id_pointer.rsplit("/", 1)[0])
        if set(record.get("fields", {})) != {"bookId"} or record["fields"]["bookId"] not in ids.get("book", set()):
            raise ArchiveError("BACKUP_ROOT_INVALID")
        story_id = "book-" + record["fields"]["bookId"]
    staged, rows, assets, result, token_worlds, profiles, contents = [], {}, {}, None, {}, set(), {}
    scene_metadata={};stream_contents={}
    try:
        with db.begin():
            if authorize is not None:
                previous = authorize(db)
                if previous is not None:
                    return previous
            user = db.scalar(select(reader.User).where(reader.User.id == target_owner, reader.User.status == "active").with_for_update())
            if user is None: raise ArchiveError("BACKUP_OWNER_NOT_ACTIVE")
            for item in plan.active_objects + plan.historical_objects:
                parent_pointer = item.id_pointer.rsplit("/", 1)[0]
                record = pointer_get(plan.documents[item.path], parent_pointer)
                if record.get("owner_id") != target_owner or record.get("id") != item.new_id:
                    raise ArchiveError("BACKUP_ROW_INVALID")
                if record.get("rowType") == "semanticEvent" and item.kind == "world_event_token":
                    if set(record.get("fields", {})) != {"worldId"} or record["fields"]["worldId"] not in ids.get("world", set()):
                        raise ArchiveError("BACKUP_REFERENCE_UNDECLARED")
                    token_worlds[item.new_id]=record["fields"]["worldId"]
                    continue
                if record.get("rowType")=="historicalProfile" and item.kind=="historical_profile":
                    if record.get("fields")!={"historical":True}:raise ArchiveError("BACKUP_GENERATION_PROFILE_INVALID")
                    profiles.add(item.new_id);continue
                if record.get("rowType")=="candidateScene" and item.kind=="world_scene":
                    value=record.get("fields",{})
                    if set(value)!={"worldId","turnId","sceneIndex","parentCheckpointId"} or value["worldId"] not in ids.get("world",set()) or value["turnId"] not in ids.get("world_turn",set()) or type(value["sceneIndex"]) is not int or not 0<=value["sceneIndex"]<10 or value["parentCheckpointId"] not in ids.get("checkpoint",set())|ids.get("world_scene",set()):
                        raise ArchiveError("BACKUP_WORLD_SCENE_INVALID")
                    scene_metadata[item.new_id]=value;continue
                if record.get("rowType")=="candidateContent" and item.kind=="chapter_content":
                    value=record.get("fields",{})
                    if set(value)!={"generationId","chapterIndex"} or value["generationId"] not in ids.get("generation_batch",set()) or type(value["chapterIndex"]) is not int or not 1<=value["chapterIndex"]<=10:raise ArchiveError("BACKUP_GENERATION_INVALID")
                    contents[item.new_id]=value;continue
                if record.get("rowType")=="publicStreamContent" and item.kind=="stream_content":
                    value=record.get("fields",{})
                    if set(value)!={"taskId","chapterIndex"} or value["taskId"] not in ids.get("task",set()) or type(value["chapterIndex"]) is not int or not 1<=value["chapterIndex"]<=20:stream_history.invalid()
                    stream_contents[item.new_id]=value;continue
                if record.get("rowType") in {"story", "chapter", "frozenParagraph"}:
                    if item.kind not in {"story", "chapter", "paragraph"}: raise ArchiveError("BACKUP_ROW_INVALID")
                    if record["rowType"] == "frozenParagraph": read_text_frame(root, record["bodies"]["text"], files)
                    continue
                if record.get("rowType") == "asset" and item.kind == "asset":
                    entry = files.get(record["fields"]["path"])
                    if entry is None or entry["kind"] != "asset" or entry["sha256"] != record["fields"]["sha256"]:
                        raise ArchiveError("BACKUP_ASSET_INVALID")
                    assets[item.new_id] = entry; continue
                if record.get("rowType")=="AssetVariant" and item.kind=="asset_variant":
                    values=variant_values(record,root,files)
                    if values["asset_id"] not in ids.get("asset",set()):raise ArchiveError("BACKUP_REFERENCE_UNDECLARED")
                    rows.setdefault(item.kind,{})[item.new_id]=AssetVariant(**values)
                    continue
                spec = SPEC_BY_KIND.get(item.kind)
                if spec is None or record.get("rowType") != spec.model.__name__:
                    raise ArchiveError("BACKUP_ROW_TYPE_INVALID")
                values = _fields(spec, record, root, files)
                if spec.kind=='character_artwork':
                    from .character_artworks import decode as decode_artwork
                    values.update(decode_artwork(record,root,files))
                _reference_closure(values, spec, ids, task_types)
                if spec.kind == "paragraph":
                    values["html"] = decode_inline(record.get("inline"))
                    if digest(values["text"]) != values["content_hash"]: raise ArchiveError("BACKUP_BODY_HASH_MISMATCH")
                    if values["html"] is not None and normalize(BeautifulSoup(values["html"],"html.parser").get_text("",strip=False))!=values["text"]:
                        raise ArchiveError("BACKUP_BODY_INLINE_MISMATCH")
                if spec.kind == "source":
                    text = read_text_frame(root, record["sourceText"], files)
                    name = "portable-source-" + item.new_id + ".txt"
                    values["file_ref"] = values["normalized_ref"] = name
                    values["format"] = "txt"; values["encoding"] = "utf-8"
                    values["file_hash"] = digest(text)
                    write_private(target_owner, name, text.encode("utf-8")); staged.append(name)
                    values["source_map"]["portableOriginalFormat"] = record["fields"]["format"]
                    values["source_map"]["blocks"] = {entry["id"]: _nested(entry["value"], root, files) for entry in record.get("maps", {}).get("blocks", [])}
                    values["source_map"]["assets"] = {entry["id"]: "portable-asset-" + entry["id"] + ".png" for entry in record.get("maps", {}).get("assets", [])}
                if spec.kind == "world":
                    values["source"]["assets"] = {}
                    if values["source_book_id"] is None and values["source"].get("kind") == "book":
                        values["source"]["anchor"] = None; values["source"]["sourceStatus"] = "source-not-imported"
                    for entry in record.get("maps", {}).get("assets", []):
                        # Existing frozen-source representation is bounded to
                        # one image at a time; the portable file never Base64.
                        import base64
                        image = assets.get(entry["id"])
                        if image is None: raise ArchiveError("BACKUP_ASSET_REFERENCE_MISSING")
                        values["source"]["assets"][entry["id"]] = base64.b64encode((root / image["path"]).read_bytes()).decode("ascii")
                if spec.kind in {"resume_target", "playback_position"}:
                    link = record["storyRoot"]
                    if link["type"] not in {"book", "world"} or link["id"] not in ids.get(link["type"], set()): raise ArchiveError("BACKUP_REFERENCE_UNDECLARED")
                    values["story_id"] = link["type"] + "-" + link["id"]
                    values["context"]["storyId"] = values["story_id"]
                playback_domain.decode(spec.kind, values, record)
                if spec.kind == "task":
                    previous = values["state"]
                    validate_checkpoint(values["checkpoint"])
                    values.update(state="succeeded" if previous == "succeeded" else "cancelled", cancel_requested=True,
                                  provider_state="not_started", persistence_state="saved", usage_state="historical", lease_owner=None, lease_expires_at=None)
                    values["payload"] = {**values["payload"], "importedHistory": True}
                    values["result"] = {**values["result"], "importedHistory": True, "originalState": previous}
                if spec.kind == "candidate" and values["status"] != "accepted": values["status"] = "rejected"
                if spec.kind == "world_turn":
                    values["turn_key"] = "portable-" + item.new_id
                    if not values["checkpoint_id"]: values["status"] = "cancelled"
                    values["result"] = {**values["result"], "importedHistory": True}
                if spec.kind == "role_message" and values["status"] not in {"saved", "partial", "interrupted", "stopped"}:
                    values["status"] = "cancelled"
                if spec.kind=='world_chat':values.update(snapshot={},denied_phrases=[],pending_task_id=None,character_name='')
                if spec.kind=='world_chat_message' and values['role']=='assistant' and values['status'] in {'partial','stopped','failed','cancelled'}:values['status']='interrupted'
                if spec.kind=='play_control':values['profile_id']=None
                instance = spec.model(id=item.new_id, created_at=_date(record["createdAt"]), **values)
                if hasattr(spec.model, "owner_id"): instance.owner_id = target_owner
                rows.setdefault(item.kind, {})[item.new_id] = instance
            for identifier, image in assets.items():
                name = "portable-asset-" + identifier + ".png"
                write_private(target_owner, name, (root / image["path"]).read_bytes()); staged.append(name)
            _relationships(rows, token_worlds,scene_metadata)
            try:remap_reader_local_ids(rows,plan.id_map)
            except ArchiveError:
                raise
            except (KeyError,TypeError,ValueError) as exc:
                raise ArchiveError("BACKUP_READER_LOCAL_REFERENCE_INVALID") from exc
            try:remap_world_local_ids(rows,plan.id_map)
            except (KeyError,TypeError,ValueError) as exc:
                raise ArchiveError("BACKUP_WORLD_LOCAL_REFERENCE_INVALID") from exc
            _relationships(rows, token_worlds,scene_metadata)
            from .character_artworks import validate as validate_artworks
            validate_artworks(rows)
            validate_reader_corrections(rows)
            accepted_basis.validate_rows(rows,rebase=True)
            validate_generation_rows(rows,profiles,contents)
            stream_history.validate_restored(rows,stream_contents)
            for identity in rows.get("character_identity", {}).values(): identity.origin_key = _identity_origin(identity)
            for revision in rows.get("revision", {}).values(): revision.content_hash = digest(canonical(revision.manifest))
            from ..prose.selection_edits import validate_selection_families
            try:
                validate_selection_families(rows.get('revision', {}).values(), rows.get('paragraph', {}).values(), lambda model, identifier:
                    rows.get("paragraph" if model is reader.ContentBlock else "branch" if model is reader.Branch else "revision", {}).get(identifier))
            except (ValueError, KeyError, TypeError) as error:
                raise ArchiveError('BACKUP_READER_GRAPH_INVALID') from error
            for snapshot in rows.get("knowledge_snapshot", {}).values(): snapshot.scope_hash = digest(canonical({"portable": snapshot.id, "boundary": snapshot.boundary}))
            for chunk in rows.get("analysis_chunk", {}).values(): chunk.cache_hash = digest("portable:" + chunk.id)
            for point in rows.get("checkpoint", {}).values(): point.content_hash = checkpoint_hash(point.state, point.definition, point.text, point.parent_checkpoint_id)
            for scoped in rows.get("character_scope_revision", {}).values(): scoped.scope_key = scoped.scope_kind + ":portable:" + scoped.id
            # Flush by explicit FK layers; plain string cyclic head links are
            # already validated and do not require disabling constraints.
            order = ["book", "source", "branch", "revision", "paragraph", "character_identity", "character_alias", "character_scope_revision",
                     "progress", "note", "resume_target", "knowledge_snapshot", "analysis_chunk", "task", "task_event", "candidate","generation_batch","chapter_version","generation_acceptance", "role_session", "role_message",
                     "world", "world_line", "checkpoint", "character_artwork", "character_look", "world_turn", "world_event", "memory", "world_publication",'world_chat','world_chat_message','play_control','play_memory_revision',"asset","asset_variant","asset_ref", "playback_position", "comparison_exposure"]
            for index, kind in enumerate(order):
                db.add_all(list(rows.get(kind, {}).values())); db.flush()
                if fault is not None: fault("after-flush", kind, index)
            play_controls.rebuild_sources(db,rows,target_owner)
            validate_restored_images(db,target_owner,rows)
            playback_domain.validate(db,target_owner,rows)
            for kind in ("progress", "note", "role_session", "world_publication"):
                for instance in rows.get(kind, {}).values():
                    for name in ("anchor", "max_observed_anchor", "pinned_anchor"):
                        anchor = getattr(instance, name, None)
                        if anchor: validate_anchor(db, anchor, target_owner)
            if fault is not None: fault("before-commit", None, len(order))
            result = {"storyId": story_id, "idMap": plan.id_map, "roots": plan.roots,
                      "counts": {kind: len(records) for kind, records in rows.items()},
                      "historicalTasksReplayed": False, "creditTransferred": False, "sourceStatus": plan.detached_sources}
            if record_receipt is not None: record_receipt(db, result)
        return result
    except BaseException:
        try:
            if db.in_transaction(): db.rollback()
        except Exception:
            # A COMMIT acknowledgement can fail after PostgreSQL committed.
            # Recheck through a fresh connection, rather than deleting its files.
            pass
        committed = _committed_rows(rows, target_owner)
        if committed is True and result is not None:
            return result
        if committed is False:
            for name in staged:
                path = private_path(target_owner, name)
                # Only this import's newly allocated filenames are removable.
                if not re.fullmatch(r"portable-(source|asset)-[a-f0-9]{32}\.(txt|png)", name): raise RuntimeError("Invalid portable cleanup allocation")
                path.unlink(missing_ok=True)
        # Unknown outcome retains these allocated files. The standard orphan
        # cleaner first reads live SourceDocument references, so it cannot
        # erase a committed copy while the acknowledgement is being checked.
        raise
