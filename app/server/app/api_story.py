"""Reader-facing finite story runs and saved story-line timeline."""
from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy import select
from .db import get_db
from .security import current_user
from .schemas import Operation, StoryRunInput
from .models import Task, Book, Branch, Revision, ContentBlock, Progress
from .domain import owned, idempotent, is_tail, is_import_root, validate_anchor, anchor_order, anchor_label
from .story_flow import create_flow, flow_result
from .prose import is_prose
from .errors import Problem
from .api_books import paginate

router = APIRouter()


def timeline_prefix(db, branch, revision, owner):
    """Only chapter identities inherited from the frozen fork belong to the trunk.

    A retained part of the fork chapter shares its chapter identity. Newly written
    chapters always get new identities, including forks from an AI continuation.
    Reading manifests remain complete; this is only a graph presentation boundary.
    """
    if not branch.parent_branch_id or not branch.fork_anchor:
        return 0, None
    base = owned(db, Revision, branch.fork_anchor["revisionId"], owner)
    boundary = anchor_order(base, branch.fork_anchor)[0]
    count = 0
    for current, inherited in zip(revision.manifest, base.manifest[:boundary+1]):
        if (current["id"] != inherited["id"] or not current["blocks"]
                or current["blocks"] != inherited["blocks"][:len(current["blocks"])]):
            break
        count += 1
        if len(current["blocks"]) < len(inherited["blocks"]):
            break
    return count, base.manifest[count-1]["id"] if count else None


@router.post("/story-runs", status_code=202)
def start_story(data: StoryRunInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    if data.operation not in {"continue_at_tail", "continue_from_anchor", "counterfactual"} or not data.anchor:
        raise Problem("OPERATION_NOT_ENABLED", "请选择续写位置")
    def create():
        branch, revision, block = validate_anchor(db, data.anchor.model_dump(), user.id, paragraph=True)
        book = owned(db, Book, branch.book_id, user.id)
        normalized = Operation.model_validate(data.model_dump(exclude={"maxCalls"}))
        if data.operation == "continue_at_tail" and is_tail(revision, data.anchor.model_dump(), block) and not is_import_root(db, book, branch):
            normalized.target.mode, normalized.target.branchId = "append_existing", branch.id
        elif normalized.target.mode == "new_child_branch" and normalized.target.name in {"新的故事线", ""}:
            normalized.target.name = "如果故事在这里转弯" if data.operation == "counterfactual" else "另一种故事" if data.operation == "continue_from_anchor" else "原故事之后"
        return create_flow(db, user.id, normalized, call_limit=data.maxCalls)
    request_payload = data.model_dump()
    if data.maxCalls is None:
        # Keep the unchanged-request identity used by clients before this
        # optional transport ceiling existed. An actual ceiling stays hashed.
        request_payload.pop("maxCalls")
    return idempotent(db, user.id, "story-run", request.headers.get("idempotency-key"), request_payload, create)


@router.get("/story-runs")
def story_runs(branchId: str, user=Depends(current_user), db=Depends(get_db)):
    owned(db, Branch, branchId, user.id)
    rows = list(db.scalars(select(Task).where(Task.owner_id == user.id, Task.task_type == "story_flow",
        Task.payload["flowKind"].astext == "generation",
        Task.payload["operation"]["anchor"]["branchId"].astext == branchId).order_by(Task.created_at.desc(), Task.id.desc()).limit(100)))
    return {"items": [flow_result(t) for t in rows], "nextCursor": None}


def owned_flow(db, owner, run_id, *, lock=False):
    task = owned(db, Task, run_id, owner, lock=lock)
    if task.task_type != "story_flow" or is_prose(task):
        raise Problem("RESOURCE_NOT_FOUND", "任务不存在", 404)
    return task


@router.get("/story-runs/{run_id}")
def story_run(run_id: str, user=Depends(current_user), db=Depends(get_db)):
    return flow_result(owned_flow(db, user.id, run_id))


@router.post("/story-runs/{run_id}/cancel")
def cancel_story(run_id: str, user=Depends(current_user), db=Depends(get_db)):
    task = owned_flow(db, user.id, run_id, lock=True)
    if task.state not in {"succeeded", "failed", "cancelled"}:
        task.cancel_requested = True
        child = db.get(Task, task.checkpoint.get("currentTaskId")) if task.checkpoint.get("currentTaskId") else None
        if child and child.owner_id == user.id:
            child.cancel_requested = True
    db.commit()
    return flow_result(task)


@router.get("/books/{book_id}/timeline")
def story_timeline(book_id: str, branchId: str | None = None, chapterOffset: int = Query(0, ge=0),
                   chapterLimit: int = Query(20, ge=1, le=100), user=Depends(current_user), db=Depends(get_db)):
    book = owned(db, Book, book_id, user.id)
    branches = list(db.scalars(select(Branch).where(Branch.owner_id == user.id, Branch.book_id == book_id).order_by(Branch.created_at, Branch.id)))
    if branchId and not any(b.id == branchId for b in branches):
        raise Problem("RESOURCE_NOT_FOUND", "故事线不存在", 404)
    items = []
    for branch in branches:
        revision = owned(db, Revision, branch.head_revision_id, user.id)
        progress = db.scalar(select(Progress).where(Progress.owner_id == user.id, Progress.branch_id == branch.id,
            Progress.revision_id == revision.id))
        boundary = anchor_order(revision, progress.max_observed_anchor) if progress else (-1, -1, -1)
        inherited, shared_through = timeline_prefix(db, branch, revision, user.id)
        offset = max(chapterOffset, inherited)
        sliced = revision.manifest[offset:offset+chapterLimit]
        block_ids = [bid for c in sliced for bid in c["blocks"]]
        sources = dict(db.execute(select(ContentBlock.id, ContentBlock.source).where(ContentBlock.owner_id == user.id,
            ContentBlock.id.in_(block_ids))).all()) if block_ids else {}
        chapters = [{"id": c["id"], "title": c["title"] if offset+i <= boundary[0] else f"第 {offset+i+1} 章",
            "position": offset+i, "source": "ai" if any(sources.get(bid) != "original" for bid in c["blocks"]) else "original",
            "readable": True, "observed": offset+i <= boundary[0]} for i, c in enumerate(sliced)]
        items.append({"id": branch.id, "name": branch.name, "parentBranchId": branch.parent_branch_id,
            "forkAnchor": branch.fork_anchor, "status": branch.status, "headRevisionId": revision.id, "version": branch.version,
            "forkLabel": anchor_label(db, branch.fork_anchor, user.id),
            "lastReadLabel": anchor_label(db, progress.anchor, user.id) if progress else None,
            "chapters": chapters, "chaptersTotal": len(revision.manifest),
            "inheritedChapterCount": inherited, "sharedThroughChapterId": shared_through,
            "nextChapterOffset": offset+chapterLimit if offset+chapterLimit < len(revision.manifest) else None})
    return {"bookId": book.id, "originalBranchId": book.original_branch_id,
        "currentBranchId": branchId or book.original_branch_id, "branches": items}
