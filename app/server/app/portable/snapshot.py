"""Authorized reader/world snapshots with explicit fields and reference closure."""
import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile

from sqlalchemy import or_, select

from .. import models as reader
from ..api_stories import publication_book_ids, publication_branches, related_worlds, story_root
from ..storage import private_path
from ..world import models as world
from .domain_codec import TextPacks, encode_inline
from .domain_schema import JSON_FOREIGN, SPEC_BY_MODEL
from .errors import ArchiveError
from .manifest import ArchiveLimits, encode_json, pointer_parts, validate_manifest
from .package import _remove_owned_stage, validate_package_files
from .publication import project_publications
from .asset_domain import collect_images
from .prose_history import safe_checkpoint,profile_fields,capture_received
from .remap import fictional_fields_path,authored_source_key
from .world_scenes import register_candidates,scenes
from . import playback_domain,stream_history
from . import accepted_basis
from . import play_controls


def _value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_value(child) for child in value]
    return deepcopy(value)


def _part(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def source_timepoint(source):
    """Display-only source location, with no reader IDs or valid anchor shape."""
    anchor = source.get("anchor") or {}
    for index, chapter in enumerate(source.get("chapters", [])):
        if chapter["id"] == anchor.get("chapterId"):
            blocks = chapter.get("blocks", [])
            position = next((i + 1 for i, block in enumerate(blocks) if (block.get("blockId") if isinstance(block, dict) else block) == anchor.get("blockId")), None)
            return {"chapterTitle": chapter["title"], "chapter": index + 1, "paragraph": position,
                    "offset": anchor.get("end"), "offsetUnit": "utf16"}
    return {"offset": anchor.get("end"), "offsetUnit": "utf16"} if anchor else None


@dataclass
class DomainSnapshot:
    root: Path
    parent: Path
    manifest: dict

    def close(self):
        _remove_owned_stage(self.root, self.parent, "reader-bk-snapshot-")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class SnapshotWriter:
    def __init__(self, root, owner):
        self.root, self.owner = root, owner
        self.prose = TextPacks(root)
        self.files, self.objects, self.references, self.external = [], [], [], []
        self.records, self.index, self.ids = [], 0, set()
        self.asset_bytes = {}
        self.task_types = {}
        self.event_tokens = {}

    def add(self, kind, identifier, row_type, fields, bodies=None, **extra):
        key = (kind, identifier)
        if key in self.ids:
            raise ArchiveError("BACKUP_OBJECT_DUPLICATE")
        self.ids.add(key)
        if row_type == "Task": self.task_types[identifier] = fields.get("task_type")
        self.records.append({"id": identifier, "owner_id": self.owner, "rowType": row_type, "fields": _value(fields),
                             "bodies": bodies or {}, **extra, "_kind": kind})

    def asset(self, identifier, raw, license_note):
        if len(raw) > ArchiveLimits().max_image_bytes:
            raise ArchiveError("BACKUP_ASSET_INVALID")
        digest = hashlib.sha256(raw).hexdigest()
        if identifier in self.asset_bytes:
            if self.asset_bytes[identifier] != digest:
                raise ArchiveError("BACKUP_ASSET_REFERENCE_CONFLICT")
            return
        self.asset_bytes[identifier] = digest
        path = f"assets/{digest}.png"
        target = self.root / path; target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(raw)
            self.files.append({"path": path, "kind": "asset", "mediaType": "image/png", "size": len(raw), "sha256": digest, "license": license_note})
        self.add("asset", identifier, "asset", {"path": path, "mediaType": "image/png", "sha256": digest, "license": license_note})

    def nested_prose(self, value, row_type=None, parts=("fields",)):
        if fictional_fields_path(row_type,parts):
            return deepcopy(value)
        if isinstance(value, list):
            return [self.nested_prose(child,row_type,parts+(str(i),)) for i,child in enumerate(value)]
        if not isinstance(value, dict):
            return value
        output = {}
        for key, child in value.items():
            if key == "text" and isinstance(child, str):
                output["portableTextRef"] = self.prose.write(child)
            elif key == "html":
                output["portableInline"] = encode_inline(child)
            else:
                output[key] = self.nested_prose(child,row_type,parts+(key,))
        return output

    def _references(self, row, document, prefix, lookup):
        origin = {"type": row["_kind"], "id": row["id"]}
        spec = SPEC_BY_MODEL.get(lookup.get(row["rowType"]))
        def reference(pointer, kind, identifier, container=None, key=None):
            if identifier is None:
                return
            if kind=="checkpoint" and (kind,identifier) not in self.ids and ("world_scene",identifier) in self.ids and row["rowType"] in {"WorldTurn","candidateScene"}:
                kind="world_scene"
            if (kind, identifier) not in self.ids:
                # Frozen sources explicitly detach absent live source objects.
                # The name/timepoint remain; there is no dangling clickable ID.
                frozen = pointer.startswith(prefix + "/fields/source/") or pointer.startswith(prefix + "/fields/source_ref/")
                publication = ((row["rowType"] == "SourceDocument" and row["fields"].get("source_map", {}).get("kind") in {"world_publication", "detached_publication"} and pointer.startswith(prefix + "/fields/source_map/")) or
                               (row["rowType"] == "Revision" and row["fields"].get("provenance", {}).get("operation") == "world_publication" and pointer.startswith(prefix + "/fields/provenance/")))
                identity_source = row["rowType"] == "CharacterIdentity" and pointer.startswith(prefix + "/fields/source_ref/")
                deleted_task=row["rowType"]=="Task" and fields.get("error_code")=="BOOK_DELETED"
                if (frozen and kind in {"book", "branch", "revision"}) or (publication and kind in {"world", "world_line", "checkpoint"}) or (identity_source and kind in {"world", "world_line", "checkpoint", "paragraph", "chapter"}) or (deleted_task and kind=="book"):
                    label = row.get("sourceLabel", "来源未导入")
                    self.external.append({"from": origin, "field": pointer, "label": label, "timepoint": deepcopy(row.get("sourceTimepoint"))})
                    container[key] = None
                    return
                raise ArchiveError("BACKUP_REFERENCE_MISSING")
            self.references.append({"from": origin, "to": {"type": kind, "id": identifier}, "field": pointer, "required": True})
        fields = row["fields"]
        task_type = fields.get("task_type")
        if row["rowType"] == "TaskEvent":
            task_type = self.task_types.get(fields["task_id"])
        if spec:
            for name, kind in spec.foreign.items():
                if name in fields:
                    if row["rowType"]=="RoleSession" and name=="snapshot_id" and fields[name]=="" and fields["status"]=="needs_refresh":continue
                    reference(prefix + "/fields/" + name, kind, fields[name], fields, name)
            playback_domain.references(spec.kind, fields, lambda name, kind:
                reference(prefix+"/fields/"+name, kind, fields[name], fields, name))
        if row["rowType"]=="AssetVariant":reference(prefix+"/fields/asset_id","asset",fields["asset_id"],fields,"asset_id")
        if row["rowType"]=="AssetPlacement":
            kind="paragraph" if fields["scope"]["kind"]=="book" else "checkpoint"
            reference(prefix+"/fields/content_id",kind,fields["content_id"],fields,"content_id")
            if kind=="paragraph":reference(prefix+"/fields/paragraph_id",kind,fields["paragraph_id"],fields,"paragraph_id")
        if row["rowType"] in {"CharacterAlias", "CharacterScopeRevision"}:
            kind = {"reader": "branch", "world": "world"}.get(fields["scope_kind"])
            if not kind:
                raise ArchiveError("BACKUP_SCOPE_INVALID")
            reference(prefix + "/fields/scope_id", kind, fields["scope_id"], fields, "scope_id")
        if row['rowType']=='PlayControl':
            reference(prefix+'/fields/scope_id', play_controls.scope_type(fields['scope_kind']), fields['scope_id'], fields, 'scope_id')
        world_id = fields.get("world_id")
        def event_reference(pointer, identifier, container, key):
            token = self.event_tokens.get((world_id, identifier))
            if token is not None:
                container[key] = token
                reference(pointer, "world_event_token", token, container, key)
            else:
                # Legacy hand-authored rows may record actual event row IDs;
                # normal production memory records semantic data.id instead.
                reference(pointer, "world_event", identifier, container, key)
        def walk(value, pointer):
            if fictional_fields_path(row["rowType"],tuple(pointer_parts(pointer[len(prefix):]))):return
            if isinstance(value, list):
                for index, child in enumerate(value): walk(child, pointer + "/" + str(index))
            elif isinstance(value, dict):
                for key, child in list(value.items()):
                    if authored_source_key(row['rowType'],tuple(pointer_parts(pointer[len(prefix):])),key):continue
                    at = pointer + "/" + _part(key)
                    if row['rowType']=='Task' and key=='acceptedEventBasis':
                        for path,kind,identifier in accepted_basis.references(child):reference(at+path,kind,identifier)
                        continue
                    if row['rowType']=='Task' and key=='acceptedSourceSnapshotId':
                        reference(at,'knowledge_snapshot',child);continue
                    if row['rowType']=='Task' and key=='acceptedCandidateBindings':
                        accepted_basis.validate_bindings(child)
                        for index,binding in enumerate(child):reference(at+'/'+str(index)+'/runId','generation_batch',binding['runId'])
                        continue
                    if key == "turnId" and row["rowType"] in {"Task", "TaskEvent"} and not (task_type or "").startswith("world_"):
                        # Reader chat turnId is a client request key scoped by
                        # its new session, never a world.turns primary key.
                        continue
                    if key == "scopeId" and value.get("kind") in {"reader", "world"} and isinstance(child, str):
                        reference(at, "branch" if value["kind"] == "reader" else "world", child, value, key)
                    elif key=="candidateId" and isinstance(child,str) and ("generation_batch",child) in self.ids:
                        reference(at,"generation_batch",child,value,key)
                    elif key=="contentId" and isinstance(child,str) and ("stream_content",child) in self.ids:
                        reference(at,"stream_content",child,value,key)
                    elif key in JSON_FOREIGN and isinstance(child, str):
                        reference(at, JSON_FOREIGN[key], child, value, key)
                    elif key in {"worldCheckpointId", "forkCheckpointId", "headCheckpointId"} and isinstance(child, str):
                        reference(at, "checkpoint", child, value, key)
                    elif key=="id" and row["rowType"]=="WorldTurn" and len(pointer_parts(at[len(prefix):]))==5 and pointer_parts(at[len(prefix):])[:3]==["fields","result","chapters"]:
                        reference(at,"checkpoint" if ("checkpoint",child) in self.ids else "world_scene",child,value,key)
                    elif key == "id" and isinstance(child, str) and ((row["rowType"] == "WorldEvent" and at == prefix + "/fields/data/id") or (row["rowType"] == "WorldCheckpoint" and "/fields/state/queue/" in at) or (row["rowType"]=="WorldTurn" and "/fields/result/chapters/" in at and ("/state/queue/" in at or "/events/" in at))):
                        event_reference(at, child, value, key)
                    elif key == "id" and isinstance(child, str) and ("/fields/manifest/" in at or "/fields/source/chapters/" in at):
                        reference(at, "chapter", child, value, key)
                    elif key == "blocks" and isinstance(child, list) and all(isinstance(item, str) for item in child):
                        for index, identifier in enumerate(child): reference(at + "/" + str(index), "paragraph", identifier, child, index)
                    elif key=="chapterIds" and isinstance(child,list):
                        for index,identifier in enumerate(child):reference(at+"/"+str(index),"chapter",identifier,child,index)
                    elif key=="eventId" and isinstance(child,str) and row["rowType"] in {"WorldTurn","WorldCheckpoint","WorldEvent"}:
                        event_reference(at,child,value,key)
                    elif key in {"event_ids", "eventIds", "resolvedEvents", "reachedEvents"} and isinstance(child, list):
                        for index, identifier in enumerate(child): event_reference(at + "/" + str(index), identifier, child, index)
                    else:
                        walk(child, at)
        walk(fields, prefix + "/fields")
        for map_name, entries in row.get("maps", {}).items():
            target_kind = {"blocks": "paragraph", "assets": "asset"}[map_name]
            for index, entry in enumerate(entries):
                reference(prefix + "/maps/" + map_name + "/" + str(index) + "/id", target_kind, entry["id"], entry, "id")
                walk(entry["value"], prefix + "/maps/" + map_name + "/" + str(index) + "/value")
        if row.get("inline") is not None:
            walk(row["inline"], prefix + "/inline")
        if row.get("storyRoot"):
            reference(prefix + "/storyRoot/id", row["storyRoot"]["type"], row["storyRoot"]["id"], row["storyRoot"], "id")

    def finish(self, root_kind, root_id):
        # 256 rows per JSON pack keeps a large book under the ZIP entry cap.
        lookup = {spec.model.__name__: spec.model for spec in SPEC_BY_MODEL.values()}
        for start in range(0, len(self.records), 256):
            path = f"data/rows-{self.index:06d}.json"; self.index += 1
            selected = self.records[start:start + 256]
            for index, row in enumerate(selected):
                prefix = f"/rows/{index}"
                self.objects.append({"type": row["_kind"], "id": row["id"], "path": path, "idPointer": prefix + "/id", "ownerPointer": prefix + "/owner_id"})
                self._references(row, path, prefix, lookup)
            clean = [{key: value for key, value in row.items() if key != "_kind"} for row in selected]
            raw = encode_json({"rows": clean})
            if len(raw) > ArchiveLimits().max_json_bytes:
                raise ArchiveError("BACKUP_JSON_SIZE_LIMIT")
            target = self.root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
            self.files.append({"path": path, "kind": "data", "mediaType": "application/json", "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        self.prose.close(); self.files.extend(self.prose.entries)
        value = {"schemaVersion": 1, "appVersion": "reader-world-next-v1", "exportedAt": datetime.now(timezone.utc).isoformat(), "objectType": root_kind,
                 "roots": [{"type": "story" if root_kind == "story" else "world", "id": root_id}], "files": self.files,
                 "objects": self.objects, "references": self.references, "externalSources": self.external}
        return validate_manifest(value)


def collect_domain(db, owner, story_id, include_source):
    if db.scalar(select(reader.User).where(reader.User.id == owner, reader.User.status == "active").with_for_update(read=True)) is None:
        raise ArchiveError("BACKUP_OWNER_NOT_ACTIVE")
    kind, root = story_root(db, owner, story_id)
    worlds = related_worlds(db, kind, root, owner)
    book_ids = {root.id} if kind == "book" else set()
    families={}
    for item in worlds:
        published = publication_book_ids(db, item, owner)
        if not include_source and kind == "world":
            family=publication_branches(db,owner,item)
            legacy=set(db.scalars(select(reader.Branch.id).where(reader.Branch.owner_id==owner,reader.Branch.book_id==item.source_book_id,reader.Branch.id.in_(family))))
            if legacy:families[item.source_book_id]={"branches":legacy}
            else:published.discard(item.source_book_id)
        book_ids.update(published)
        if include_source and item.source_book_id: book_ids.add(item.source_book_id)
    def rows(model, filters):
        return list(db.scalars(select(model).where(model.owner_id == owner, *filters).order_by(model.created_at, model.id)))
    books = rows(reader.Book, [reader.Book.id.in_(book_ids), reader.Book.status != "deleted"])
    book_ids = {item.id for item in books}; world_ids = {item.id for item in worlds}
    branches = rows(reader.Branch, [reader.Branch.book_id.in_(book_ids)])
    branch_ids = {item.id for item in branches}
    role_sessions = rows(reader.RoleSession, [reader.RoleSession.book_id.in_(book_ids)])
    session_ids = {item.id for item in role_sessions}
    role_messages=rows(reader.RoleMessage,[reader.RoleMessage.session_id.in_(session_ids)])
    historical_tasks={item.task_id for item in role_messages if item.task_id}|{item.origin_operation_id for item in branches if item.origin_operation_id}
    tasks = rows(reader.Task, [or_(reader.Task.payload["bookId"].astext.in_(book_ids), reader.Task.payload["anchor"]["bookId"].astext.in_(book_ids),
        reader.Task.payload["operation"]["anchor"]["bookId"].astext.in_(book_ids),reader.Task.result["bookId"].astext.in_(book_ids),reader.Task.result["worldId"].astext.in_(world_ids),
        reader.Task.payload["worldId"].astext.in_(world_ids), reader.Task.payload["sessionId"].astext.in_(session_ids),reader.Task.id.in_(historical_tasks))])
    task_ids = {item.id for item in tasks}
    selected = {reader.Book: books, reader.Branch: branches, reader.RoleSession: role_sessions, reader.Task: tasks, world.World: worlds}
    # Future starts use the latest saved author settings. Export detached world
    # rows with that effective definition; immutable historical checkpoints and
    # the source database remain untouched. Restored worlds need no live draft
    # table to retain their starts, fields, actions, plot and suggested model.
    from ..authoring.service import effective_definition
    from ..authoring.models import AuthorDraft
    selected[world.World]=[
        world.World(**{column.key:deepcopy(getattr(item,column.key)) for column in world.World.__table__.columns
                       if column.key!='definition'}, definition=effective_definition(db,item))
        if db.get(AuthorDraft,item.id) else item for item in worlds]
    for model in [reader.SourceDocument, reader.ContentBlock, reader.Progress, reader.Bookmark, reader.KnowledgeSnapshot, reader.AnalysisChunk]:
        selected[model] = rows(model, [model.book_id.in_(book_ids)])
    selected[reader.Revision] = rows(reader.Revision, [reader.Revision.branch_id.in_(branch_ids)])
    selected[reader.RoleMessage] = role_messages
    selected[reader.Candidate] = rows(reader.Candidate, [reader.Candidate.task_id.in_(task_ids)])
    selected[reader.GenerationBatch]=rows(reader.GenerationBatch,[reader.GenerationBatch.book_id.in_(book_ids)])
    generation_ids={item.id for item in selected[reader.GenerationBatch]}
    selected[reader.GenerationChapterVersion]=rows(reader.GenerationChapterVersion,[reader.GenerationChapterVersion.generation_id.in_(generation_ids)])
    selected[reader.GenerationAcceptance]=rows(reader.GenerationAcceptance,[reader.GenerationAcceptance.generation_id.in_(generation_ids)])
    selected[reader.ResumeTarget] = rows(reader.ResumeTarget, [reader.ResumeTarget.story_id.in_(["book-" + identifier for identifier in book_ids] + ["world-" + identifier for identifier in world_ids])])
    selected[reader.TaskEvent] = list(db.scalars(select(reader.TaskEvent).join(reader.Task).where(reader.Task.owner_id == owner, reader.TaskEvent.task_id.in_(task_ids)).order_by(reader.TaskEvent.created_at, reader.TaskEvent.id)))
    for model in [world.WorldLine, world.WorldCheckpoint, world.WorldTurn, world.WorldEvent, world.WorldMemory, world.WorldPublication]:
        selected[model] = rows(model, [model.world_id.in_(world_ids)])
    play_controls.collect(selected, rows, world_ids)
    from .character_artworks import collect as collect_artworks
    collect_artworks(db,owner,selected,world_ids)
    aliases = rows(reader.CharacterAlias, [or_((reader.CharacterAlias.scope_kind == "reader") & reader.CharacterAlias.scope_id.in_(branch_ids), (reader.CharacterAlias.scope_kind == "world") & reader.CharacterAlias.scope_id.in_(world_ids))])
    identities = {item.identity_id for item in aliases} | {item.character_identity_id for item in role_sessions if item.character_identity_id}
    identities.update(ref["identityId"] for item in worlds for ref in item.source.get("characterRefs", []))
    identities.update(ref["identityId"] for source in selected[reader.SourceDocument]
                      for name in ("characterBindings", "characterAliasBindings") for ref in source.source_map.get(name, []))
    selected[reader.CharacterIdentity] = rows(reader.CharacterIdentity, [reader.CharacterIdentity.id.in_(identities)])
    selected[reader.CharacterAlias] = aliases
    selected[reader.CharacterScopeRevision] = rows(reader.CharacterScopeRevision, [or_((reader.CharacterScopeRevision.scope_kind == "reader") & reader.CharacterScopeRevision.scope_id.in_(branch_ids), (reader.CharacterScopeRevision.scope_kind == "world") & reader.CharacterScopeRevision.scope_id.in_(world_ids))])
    for old_book,entry in families.items():
        entry["source"]=next((item for item in selected[reader.SourceDocument] if item.book_id==old_book),None)
        if entry["source"] is None:raise ArchiveError("BACKUP_PUBLICATION_INVALID")
    if families:selected=project_publications(selected,families)
    playback_domain.collect(db, owner, selected)
    return kind, root, selected


def build_domain_snapshot(db, owner, story_id, work_root: Path, *, include_source=False) -> DomainSnapshot:
    """Use a fresh repeatable-read Session for a consistent authorized snapshot."""
    kind, root_object, selected = collect_domain(db, owner, story_id, include_source)
    capture_received(selected)
    work_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="reader-bk-snapshot-", dir=work_root))
    snapshot = DomainSnapshot(root, work_root, {})
    writer = SnapshotWriter(root, owner)
    try:
        if kind == "book": writer.add("story", root_object.id, "story", {"bookId": root_object.id})
        batches={item.task_id:item for item in selected[reader.GenerationBatch]}
        contents={}
        for row in selected[reader.GenerationChapterVersion]:
            slot={"generationId":row.generation_id,"chapterIndex":row.chapter_index}
            if row.content_id in contents and contents[row.content_id]!=slot:raise ArchiveError("BACKUP_GENERATION_INVALID")
            contents[row.content_id]=slot
        for identifier,slot in sorted(contents.items()):writer.add("chapter_content",identifier,"candidateContent",slot)
        public_streams=stream_history.register(selected,writer,reader)
        play_controls.capture_received(selected, public_streams)
        profiles=set()
        def profile_ids(value,row_type,parts):
            if fictional_fields_path(row_type,parts):return
            if isinstance(value,list):
                for index,child in enumerate(value):profile_ids(child,row_type,parts+(str(index),))
            elif isinstance(value,dict):
                if isinstance(value.get("profileId"),str):profiles.add(value["profileId"])
                for key,child in value.items():profile_ids(child,row_type,parts+(key,))
        for model,records in selected.items():
            for row in records:
                if model is reader.GenerationBatch:profiles.add(profile_fields(row.profile_snapshot)[0])
                for name in SPEC_BY_MODEL[model].fields:
                    if model is reader.Task and name in {"payload","checkpoint"}:continue
                    profile_ids(getattr(row,name),model.__name__,("fields",name))
        for identifier in sorted(profiles):writer.add("historical_profile",identifier,"historicalProfile",{"historical":True})
        tokens = {(item.world_id, item.data["id"]) for item in selected[world.WorldEvent] if isinstance(item.data.get("id"), str)}
        tokens.update((item.world_id, event["id"]) for item in selected[world.WorldCheckpoint] for event in item.state.get("queue", []))
        register_candidates(selected,writer,world)
        for turn in selected[world.WorldTurn]:
            for value in scenes(turn.result):
                tokens.update((turn.world_id,event["id"]) for event in value["events"]+value["state"].get("queue",[]))
        for world_id, token in sorted(tokens):
            qualified = world_id + ":" + token
            writer.event_tokens[(world_id, token)] = qualified
            writer.add("world_event_token", qualified, "semanticEvent", {"worldId": world_id})
        chapters = {}
        paragraphs = {item.id for item in selected[reader.ContentBlock]}
        for revision in selected[reader.Revision]:
            for chapter in revision.manifest: chapters.setdefault(chapter["id"], {"title": chapter["title"]})
        for item in selected[world.World]:
            for chapter in item.source.get("chapters", []): chapters.setdefault(chapter["id"], {"title": chapter["title"]})
            # Older full-text scripts froze chapter IDs on paragraphs but did
            # not include a chapter index. Close those existing references in
            # the archive without altering the source or a live checkpoint.
            for block in item.source.get('blocks', []) + item.source.get('referenceBlocks', []):
                chapters.setdefault(block['chapterId'], {'title':item.source_title})
        for identifier, fields in chapters.items(): writer.add("chapter", identifier, "chapter", fields)
        for item in selected[world.World]:
            for block in item.source.get("blocks", []) + item.source.get("referenceBlocks", []):
                if block["blockId"] not in paragraphs:
                    writer.add("paragraph", block["blockId"], "frozenParagraph", {"chapterId": block["chapterId"], "source": block.get("source", "original")}, {"text": writer.prose.write(block["text"])})
                    paragraphs.add(block["blockId"])
        for model, rows in selected.items():
            spec = SPEC_BY_MODEL[model]
            for item in rows:
                fields = {name: _value(getattr(item, name)) for name in spec.fields}
                bodies = {name: writer.prose.write(fields.pop(name)) for name in spec.prose}
                extra = {"createdAt": item.created_at.isoformat()}
                if model is reader.ContentBlock: extra["inline"] = encode_inline(item.html)
                if model is reader.Task:
                    fields["payload"] = {key: value for key, value in fields["payload"].items() if key in {"bookId", "branchId", "anchor", "worldId", "lineId", "turnId", "sessionId", "chapterCount","protocol","acceptedBasisSchema",'chatId','chatTurnKey','parentCheckpointId'}}
                    fields["result"] = {key: value for key, value in fields["result"].items() if key in {"bookId", "branchId", "revisionId", "worldId", "lineId", "turnId", "candidateId", "generationId","chapterCount", "phase", "outcome", "message",'chatId'}}
                    fields["checkpoint"]=safe_checkpoint(item,batches.get(item.id),public_streams[item.id])
                    # A preparation task is display-only history. Source omission
                    # must detach its old clickable anchor together with the world.
                    included_books={row.id for row in selected[reader.Book]}
                    if item.task_type.startswith("world_"):
                        for part in ("payload", "result"):
                            value=fields[part];anchor=value.get("anchor")
                            old_book=value.get("bookId") or (anchor or {}).get("bookId")
                            if old_book and old_book not in included_books:
                                if "bookId" in value:value["bookId"]=None
                                if "anchor" in value:value["anchor"]=None
                                for key in ("branchId", "revisionId"):
                                    if key in value:value[key]=None
                                value["sourceStatus"]="source-not-imported"
                if model is reader.GenerationBatch:
                    identifier,display=profile_fields(item.profile_snapshot)
                    fields["profile_snapshot"]={"profileId":identifier,**display,"historical":True}
                if model is reader.ResumeTarget:
                    prefix, identifier = fields.pop("story_id").split("-", 1)
                    extra["storyRoot"] = {"type": prefix, "id": identifier}
                playback_domain.encode(spec.kind, fields, extra)
                if spec.kind=='character_artwork':
                    from .character_artworks import encode as encode_artwork
                    fields['descriptor'].pop('sourceSceneId',None)
                    extra['artworkBlobs']=encode_artwork(writer,item)
                if model is reader.SourceDocument:
                    source_map = fields["source_map"]
                    extra["maps"] = {name: [{"id": key, "value": value} for key, value in source_map.pop(name, {}).items()] for name in ["blocks", "assets"]}
                    for asset in extra["maps"]["assets"]:
                        writer.asset(asset["id"], private_path(owner, asset["value"]).read_bytes(), "User supplied source illustration; private recovery")
                        asset["value"] = None
                    if hasattr(item,"_portable_source_text"):extra["sourceText"]=writer.prose.write(item._portable_source_text)
                    else:
                        with private_path(owner, item.normalized_ref).open("r", encoding="utf-8") as source:
                            extra["sourceText"] = writer.prose.write(source.read())
                if model is world.World:
                    source = fields["source"]
                    extra["sourceLabel"] = item.source_title; extra["sourceTimepoint"] = source_timepoint(source)
                    assets = source.pop("assets", {})
                    extra["maps"] = {"assets": [{"id": key, "value": None} for key in assets]}
                    for identifier, encoded in assets.items():
                        if not isinstance(encoded, str) or len(encoded) > 16 * 1024**2:
                            raise ArchiveError("BACKUP_ASSET_INVALID")
                        writer.asset(identifier, base64.b64decode(encoded, validate=True), "Frozen source illustration; private recovery")
                    if fields["source_book_id"] and ("book", fields["source_book_id"]) not in {(SPEC_BY_MODEL[cls].kind, row.id) for cls, records in selected.items() for row in records}:
                        fields["source_book_id"] = None
                        # Source timepoint is display-only after source omission.
                        source["detachedSourceTimepoint"] = deepcopy(extra["sourceTimepoint"])
                fields = writer.nested_prose(fields,model.__name__)
                writer.add(spec.kind, item.id, model.__name__, fields, bodies, **extra)
        collect_images(db,owner,selected,writer)
        snapshot.manifest = writer.finish("story" if kind == "book" else "world", root_object.id)
        validate_package_files(root, snapshot.manifest, ArchiveLimits())
        return snapshot
    except BaseException:
        writer.prose.close(); snapshot.close()
        raise
