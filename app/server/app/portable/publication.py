"""Read-only export projection for historical publications inside a source book.

Omitting the original novel still preserves the world's own published reader
family. The projection allocates a portable carrier, never mutates ORM rows,
and includes only paragraphs referenced by that family's frozen revisions.
"""
from copy import deepcopy
from types import SimpleNamespace

from .. import models as reader
from ..domain import canonical,digest
from ..world import models as world
from .domain_codec import encode_inline
from .domain_schema import SPEC_BY_MODEL
from .errors import ArchiveError


def clone(model,item):
    fields={name:deepcopy(getattr(item,name)) for name in SPEC_BY_MODEL[model].fields}
    for name in ("id","owner_id","created_at","html","file_ref","normalized_ref","full_image","avatar_image"):
        if hasattr(item,name):fields[name]=deepcopy(getattr(item,name))
    return SimpleNamespace(**fields)


def project_publications(selected,families):
    for old_book,entry in families.items():
        family=entry["branches"];source=entry["source"]
        original=next(item for item in selected[reader.Book] if item.id==old_book)
        original_branches={item.id for item in selected[reader.Branch] if item.book_id==old_book}
        branches=[item for item in selected[reader.Branch] if item.book_id==old_book and item.id in family]
        revisions=[item for item in selected[reader.Revision] if item.branch_id in family]
        if not branches or not revisions:raise ArchiveError("BACKUP_PUBLICATION_INVALID")
        branch_ids={item.id for item in branches};revision_ids={item.id for item in revisions}
        block_ids={identifier for item in revisions for chapter in item.manifest for identifier in chapter["blocks"]}
        blocks=[item for item in selected[reader.ContentBlock] if item.book_id==old_book and item.id in block_ids]
        if len(blocks)!=len(block_ids):raise ArchiveError("BACKUP_PUBLICATION_INVALID")
        root=min(branches,key=lambda item:(item.created_at,item.id))
        initial=min((item for item in revisions if item.branch_id==root.id),key=lambda item:(item.sequence,item.created_at,item.id))
        carrier="publication-carrier:"+old_book
        source_id="publication-source:"+old_book
        membership={}
        for revision in sorted(revisions,key=lambda item:(item.created_at,item.sequence,item.id)):
            for chapter in revision.manifest:
                for identifier in chapter["blocks"]:membership.setdefault(identifier,(revision.branch_id,revision.id,chapter["id"]))
        def rebook(value):
            if isinstance(value,list):return [rebook(item) for item in value]
            if not isinstance(value,dict):return deepcopy(value)
            output={key:rebook(child) for key,child in value.items()}
            if output.get("bookId")==old_book:output["bookId"]=carrier
            if {"bookId","branchId","revisionId","chapterId","blockId","start","end","quoteHash"}<=set(output):
                if output["blockId"] not in block_ids:raise ArchiveError("BACKUP_PUBLICATION_SCOPE_INVALID")
                if output["branchId"] not in branch_ids or output["revisionId"] not in revision_ids:
                    output["branchId"],output["revisionId"],output["chapterId"]=membership[output["blockId"]]
            return output
        sessions=[item for item in selected[reader.RoleSession] if item.book_id==old_book and item.branch_id in branch_ids]
        session_ids={item.id for item in sessions}
        world_ids={item.id for item in selected[world.World]}
        required_tasks={item.origin_operation_id for item in branches if item.origin_operation_id}
        required_tasks.update(item.task_id for item in selected[reader.RoleMessage] if item.session_id in session_ids and item.task_id)
        def operation(task):return task.payload.get("operation") if isinstance(task.payload.get("operation"),dict) else {}
        def source_book(task):
            return (task.payload.get("bookId") or (task.payload.get("anchor") or {}).get("bookId") or
                (operation(task).get("anchor") or {}).get("bookId") or task.result.get("bookId"))
        keep_tasks=set()
        for task in selected[reader.Task]:
            if (task.payload.get("worldId") in world_ids or task.payload.get("sessionId") in session_ids or
                task.payload.get("branchId") in branch_ids or (task.payload.get("anchor") or {}).get("branchId") in branch_ids or
                (operation(task).get("anchor") or {}).get("branchId") in branch_ids or
                task.result.get("branchId") in branch_ids or task.result.get("revisionId") in revision_ids or task.id in required_tasks):keep_tasks.add(task.id)
        other_tasks={task.id for task in selected[reader.Task] if source_book(task)!=old_book}
        other_sessions={session.id for session in selected[reader.RoleSession] if session.book_id!=old_book}
        kept_generations={item.id for item in selected[reader.GenerationBatch] if item.book_id!=old_book or item.origin_anchor.get("branchId") in branch_ids}
        for model,items in list(selected.items()):
            output=[]
            for item in items:
                if model is reader.Book and item.id==old_book:continue
                if model is reader.SourceDocument and item.book_id==old_book:continue
                if model is reader.ContentBlock and item.book_id==old_book and item.id not in block_ids:continue
                if model in {reader.Branch,reader.Progress,reader.Bookmark,reader.KnowledgeSnapshot,reader.AnalysisChunk,reader.RoleSession} and item.book_id==old_book:
                    scope=item.id if model is reader.Branch else item.branch_id
                    if scope not in branch_ids:continue
                if model is reader.Revision and item.branch_id in original_branches and item.id not in revision_ids:continue
                if model is reader.CharacterAlias and item.scope_kind=="reader" and item.scope_id in original_branches and item.scope_id not in branch_ids:continue
                if model is reader.CharacterScopeRevision and item.scope_kind=="reader" and item.scope_id in original_branches and item.scope_id not in branch_ids:continue
                if model is reader.RoleMessage and item.session_id not in other_sessions|session_ids:continue
                if model is reader.Task and source_book(item)==old_book and item.id not in keep_tasks:continue
                if model in {reader.TaskEvent,reader.Candidate} and item.task_id not in other_tasks|keep_tasks:continue
                if model is reader.GenerationBatch and item.id not in kept_generations:continue
                if model in {reader.GenerationChapterVersion,reader.GenerationAcceptance} and item.generation_id not in kept_generations:continue
                if model is reader.ResumeTarget and item.context.get("bookId")==old_book and item.context.get("readerBranchId") not in branch_ids:continue
                projected=clone(model,item)
                if getattr(projected,"book_id",None)==old_book:projected.book_id=carrier
                if model is reader.Branch:
                    if item.parent_branch_id not in branch_ids:projected.parent_branch_id=None;projected.fork_anchor=None
                if model is reader.Revision:
                    if item.parent_revision_id not in revision_ids:projected.parent_revision_id=None
                    if projected.provenance.get("parentRevisionId") not in revision_ids:projected.provenance.pop("parentRevisionId",None)
                if model is not world.World and model is not reader.CharacterIdentity:
                    for name in SPEC_BY_MODEL[model].fields:
                        if isinstance(getattr(projected,name),(dict,list)):setattr(projected,name,rebook(getattr(projected,name)))
                if model is reader.ResumeTarget and item.story_id=="book-"+old_book:
                    projected.story_id="book-"+carrier;projected.context["storyId"]=projected.story_id
                output.append(projected)
            selected[model]=output
        public_text="\n\n".join(item.text for item in sorted(blocks,key=lambda item:(item.created_at,item.id)))
        book=clone(reader.Book,original);book.id=carrier;book.title=original.title[:180]+" · 已发布故事"
        book._portable_original_book=old_book
        book.source_id=source_id;book.original_branch_id=root.id;book.original_revision_id=initial.id
        book.source_hash=digest(canonical(sorted(revision_ids)));book.source_label="本人已发布的公开阅读故事 · 来源小说未导入"
        selected[reader.Book].append(book)
        # Only referenced illustrations from the original SourceDocument go
        # into this new carrier; the original normalized file is never read.
        used_assets=set()
        def asset_ids(value):
            if isinstance(value,list):
                for child in value:asset_ids(child)
            elif isinstance(value,dict):
                if "assetId" in value:used_assets.add(value["assetId"])
                for child in value.values():asset_ids(child)
        for block in blocks:asset_ids(encode_inline(block.html))
        source_map={"kind":"detached_publication","assets":{key:value for key,value in source.source_map.get("assets",{}).items() if key in used_assets},
            "blocks":{key:value for key,value in source.source_map.get("blocks",{}).items() if key in block_ids},
            "characterBindings":[entry for entry in source.source_map.get("characterBindings",[]) if entry.get("blockId") in block_ids],
            "characterAliasBindings":[entry for entry in source.source_map.get("characterAliasBindings",[]) if entry.get("branchId") in branch_ids]}
        projected=clone(reader.SourceDocument,source);projected.id=source_id;projected.book_id=carrier
        projected.file_hash=digest(public_text);projected.format="txt";projected.encoding="utf-8";projected.source_map=source_map
        projected._portable_source_text=public_text
        selected[reader.SourceDocument].append(projected)
    needed=set()
    def identities(value):
        if isinstance(value,list):
            for child in value:identities(child)
        elif isinstance(value,dict):
            for key,child in value.items():
                if key in {"identityId","characterIdentityId","sourceCharacterId"} and isinstance(child,str):needed.add(child)
                identities(child)
    for model,items in selected.items():
        if model is reader.CharacterIdentity:continue
        for item in items:
            for name in SPEC_BY_MODEL[model].fields:
                value=getattr(item,name)
                if name in {"identity_id","character_identity_id"} and value:needed.add(value)
                if isinstance(value,(dict,list)):identities(value)
    selected[reader.CharacterIdentity]=[item for item in selected[reader.CharacterIdentity] if item.id in needed]
    return selected
