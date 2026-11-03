from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy import select, inspect
from ..db import get_db
from ..security import current_user
from ..domain import owned, idempotent
from ..models import Book, Branch, GenerationBatch, Idempotency
from ..errors import Problem
from . import PROTOCOL, is_imported_history, require_writable
from .schemas import CreateGeneration, AcceptGeneration, RewriteGeneration, SelectionEdit, SelectionRevisionAction, AcceptSelectionGeneration
from . import service

router = APIRouter()

@router.post('/books/{book_id}/selection-edits', status_code=201)
def edit_selection(book_id: str, data: SelectionEdit, request: Request,
        user=Depends(current_user), db=Depends(get_db)):
    from .selection_edits import apply_selection
    return apply_selection(db, user.id, book_id, data, request.headers.get('idempotency-key'))

@router.get('/branches/{branch_id}/revision-history')
def revision_history(branch_id: str, beforeSequence: int | None = Query(None, ge=1),
        limit: int = Query(20, ge=1, le=50), user=Depends(current_user), db=Depends(get_db)):
    from .selection_edits import history
    return history(db, user.id, branch_id, beforeSequence, limit)

@router.get('/revisions/{revision_id}/selection-edit')
def selection_detail(revision_id: str, user=Depends(current_user), db=Depends(get_db)):
    from .selection_edits import detail
    return detail(db, user.id, revision_id)

@router.post('/branches/{branch_id}/selection-revisions', status_code=201)
def selection_revision(branch_id: str, data: SelectionRevisionAction, request: Request,
        user=Depends(current_user), db=Depends(get_db)):
    from .selection_edits import select_revision
    return select_revision(db, user.id, branch_id, data, request.headers.get('idempotency-key'))

@router.get('/generations/capabilities')
def capabilities(user=Depends(current_user), db=Depends(get_db)):
    inspector = inspect(db.connection())
    return {'protocol': PROTOCOL, 'ready': all(inspector.has_table(table)
        for table in ('generation_batches', 'generation_chapter_versions', 'generation_acceptances')), 'selectionWriting': True}

@router.get('/generations/request-status')
def request_status(requestId: str = Query(min_length=1, max_length=160), bookId: str = Query(),
        user=Depends(current_user), db=Depends(get_db)):
    owned(db, Book, bookId, user.id)
    rows = list(db.scalars(select(Idempotency).where(Idempotency.owner_id == user.id,
        Idempotency.key == requestId, Idempotency.scope.like('prose-%')).order_by(Idempotency.created_at, Idempotency.id)))
    for row in rows:
        value = row.result or {}
        generation_id = value.get('generationId')
        batch = db.scalar(select(GenerationBatch).where(GenerationBatch.id == generation_id,
            GenerationBatch.owner_id == user.id, GenerationBatch.book_id == bookId)) if generation_id else None
        if batch and row.scope in ('prose-create:' + bookId, 'prose-rewrite:' + batch.id):
            return {'found': True, 'receipt': service.receipt(batch)}
    return {'found': False}

@router.post('/books/{book_id}/generations', status_code=202)
def create(book_id: str, data: CreateGeneration, request: Request, user=Depends(current_user), db=Depends(get_db)):
    return service.create(db, user.id, book_id, data, request.headers.get('idempotency-key'))

@router.get('/generations')
def history(bookId: str, branchId: str, cursor: str | None = None, selections: bool = False,
        sourceRevisionId: str | None = None,
        startBlockId: str | None = None, endBlockId: str | None = None,
        startOffset: int | None = Query(None, ge=0), endOffset: int | None = Query(None, ge=0),
        user=Depends(current_user), db=Depends(get_db)):
    owned(db, Book, bookId, user.id)
    branch = owned(db, Branch, branchId, user.id)
    if branch.book_id != bookId:
        raise Problem('RESOURCE_NOT_FOUND', '故事线不属于这本故事', 404)
    from ..api_books import paginate
    rows, next_cursor = paginate(db, GenerationBatch, user.id, cursor, 20, filters=(
        GenerationBatch.book_id == bookId,
        (GenerationBatch.origin_anchor['branchId'].astext == branchId) | (GenerationBatch.target_branch_id == branchId),
        GenerationBatch.request.has_key('selection') if selections else ~GenerationBatch.request.has_key('selection'),
        *((GenerationBatch.origin_anchor['revisionId'].astext == sourceRevisionId,) if sourceRevisionId else ()),
        *((GenerationBatch.request['selection']['startAnchor']['blockId'].astext == startBlockId,) if startBlockId else ()),
        *((GenerationBatch.request['selection']['endAnchor']['blockId'].astext == endBlockId,) if endBlockId else ()),
        *((GenerationBatch.request['selection']['startAnchor']['start'].astext == str(startOffset),) if startOffset is not None else ()),
        *((GenerationBatch.request['selection']['endAnchor']['end'].astext == str(endOffset),) if endOffset is not None else ())))
    items = []
    for row in rows:
        batch, task = service.load(db, row.id, user.id)
        state = service.historical_metadata(task)['state'] if is_imported_history(task) else task.state
        items.append({'id': batch.id, 'createdAt': batch.created_at.isoformat(), 'state': state})
    return {'items': items, 'nextCursor': next_cursor}

@router.get('/generations/{generation_id}/versions')
def versions(generation_id: str, user=Depends(current_user), db=Depends(get_db)):
    batch, _ = service.load(db, generation_id, user.id, lock=True)
    return {'items': [{'candidateVersion': int(version), 'createdAt': created}
        for version, created in sorted(batch.version_times.items(), key=lambda item: int(item[0]))]}

@router.get('/generations/{generation_id}')
def read(generation_id: str, candidateVersion: int | None = Query(default=None, ge=1),
        user=Depends(current_user), db=Depends(get_db)):
    # The task row serializes the chapter mirror and eventCursor in one view.
    batch, task = service.load(db, generation_id, user.id, lock=True)
    return service.snapshot(db, batch, task, candidateVersion)

@router.post('/generations/{generation_id}/accept')
def accept(generation_id: str, data: AcceptGeneration, request: Request,
        user=Depends(current_user), db=Depends(get_db)):
    return service.accept(db, user.id, generation_id, data, request.headers.get('idempotency-key'))

@router.post('/generations/{generation_id}/selection-accept', status_code=201)
def accept_selection(generation_id: str, data: AcceptSelectionGeneration, request: Request,
        user=Depends(current_user), db=Depends(get_db)):
    from .selection_generation import accept
    return accept(db, user.id, generation_id, data, request.headers.get('idempotency-key'))

@router.post('/generations/{generation_id}/rewrite', status_code=202)
def rewrite(generation_id: str, data: RewriteGeneration, request: Request,
        user=Depends(current_user), db=Depends(get_db)):
    return service.rewrite(db, user.id, generation_id, data, request.headers.get('idempotency-key'))

@router.post('/generations/{generation_id}/cancel')
def cancel(generation_id: str, request: Request, user=Depends(current_user), db=Depends(get_db)):
    batch, task = service.load(db, generation_id, user.id, lock=True)
    def apply():
        if task.state not in service.TERMINAL:
            task.cancel_requested = True
            if task.state == 'queued':
                task.state = 'cancelled'; task.persistence_state = 'saved'; task.result_outcome = 'cancelled'
                task.result = {**task.result, 'phase': 'cancelled'}
                service.event(db, task, 'terminal', {'candidateVersion': batch.candidate_version,
                    'outcome': 'cancelled', 'providerState': task.provider_state,
                    'persistenceState': task.persistence_state, 'acceptedPrefix': batch.accepted_prefix,
                    'fee': {'certainty': 'unknown'}})
        return {**service.receipt(batch), 'cancelRequested': task.cancel_requested, 'state': task.state}
    return idempotent(db, user.id, 'prose-cancel:' + batch.id, request.headers.get('idempotency-key'),
        {'candidateVersion': batch.candidate_version}, apply)

@router.post('/generations/{generation_id}/save', status_code=202)
def save(generation_id: str, request: Request, user=Depends(current_user), db=Depends(get_db)):
    batch, task = service.load(db, generation_id, user.id, lock=True)
    require_writable(task)
    def apply():
        if task.state not in service.TERMINAL:
            raise Problem('GENERATION_RUNNING', '当前正文仍在接收，请先停止或等待', 409)
        if task.state == 'succeeded' and task.persistence_state == 'saved':
            return {**service.receipt(batch), 'state': task.state}
        from ..storage import private_path
        if not any(private_path(user.id, service.stream_name(task.id, batch.candidate_version, index)).exists()
                for index in range(batch.accepted_prefix + 1, batch.request['chapterCount'] + 1)):
            raise Problem('NO_SAVED_RESULT', '没有可恢复的已接收正文；不会重新调用模型', 409)
        task.checkpoint = {**task.checkpoint, 'saveOnly': True}
        task.state = 'queued'; task.persistence_state = 'recoverable'
        service.event(db, task, 'phase', {'candidateVersion': batch.candidate_version, 'phase': 'saving'})
        return {**service.receipt(batch), 'state': task.state}
    return idempotent(db, user.id, 'prose-save:' + batch.id, request.headers.get('idempotency-key'),
        {'candidateVersion': batch.candidate_version}, apply)
