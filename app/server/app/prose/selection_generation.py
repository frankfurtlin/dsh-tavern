"""Selection candidates use the durable prose transport and exact edit family."""
from copy import deepcopy
from sqlalchemy import select
from ..domain import owned, validate_anchor, anchor_order, utf16_slice, utf16_len, digest
from ..errors import Problem
from ..models import Book, Branch, Revision, ContentBlock, GenerationBatch, GenerationChapterVersion
from .selection_edits import materialize, apply_selection


def source(db, owner, book_id, selection, expected_head=None):
    start, end = selection['startAnchor'], selection['endAnchor']
    branch, revision, _ = validate_anchor(db, start, owner)
    other_branch, other_revision, _ = validate_anchor(db, end, owner)
    branch = owned(db, Branch, branch.id, owner, lock=True)
    book = owned(db, Book, book_id, owner, lock=True)
    if branch.book_id != book.id or other_branch.id != branch.id or other_revision.id != revision.id:
        raise Problem('SELECTION_SCOPE', '选区两端须属于同一本书和同一个版本')
    if branch.status != 'active' or book.status != 'active':
        raise Problem('BRANCH_ARCHIVED', '先恢复归档内容；已收到的写法仍保留', 409)
    if expected_head is not None and branch.head_revision_id != expected_head:
        raise Problem('REVISION_CONFLICT', '来源已更新，原输入保留', 409)
    if selection['sourcePolicy'] == 'requireUnchanged' and branch.head_revision_id != revision.id:
        raise Problem('REVISION_CONFLICT', '原文已有新版本，请明确从旧版本另开修订', 409)
    try:
        materialize(revision, start, end, None, None, db.get)
        first, last = anchor_order(revision, start), anchor_order(revision, end)
        pieces = []
        for index in range(first[1], last[1] + 1):
            block = owned(db, ContentBlock, revision.manifest[first[0]]['blocks'][index], owner)
            pieces.append(utf16_slice(block.text, start['end'] if index == first[1] else 0,
                end['end'] if index == last[1] else utf16_len(block.text)))
    except (ValueError, KeyError, TypeError) as error:
        raise Problem('SELECTION_INVALID', '选区顺序或字符边界无效，未创建模型请求') from error
    text = '\n\n'.join(pieces)
    if not text.strip():
        raise Problem('SELECTION_EMPTY', '请先选择需要扩写或替换的文字')
    return text


def guard_task(db, task):
    batch = db.scalar(select(GenerationBatch).where(GenerationBatch.task_id == task.id,
        GenerationBatch.owner_id == task.owner_id))
    if batch and batch.request.get('selection'):
        source(db, task.owner_id, batch.book_id, batch.request['selection'])


def context(db, batch):
    selection = batch.request.get('selection')
    if selection is None:
        return None
    return {'purpose': selection['purpose'], 'originalText': source(db, batch.owner_id, batch.book_id, selection),
        'startAnchor': deepcopy(selection['startAnchor']), 'endAnchor': deepcopy(selection['endAnchor'])}


def replacement(selection, original, body):
    return original + '\n\n' + body if selection['purpose'] == 'expand' else body


def validate_historical(rows):
    """Declared AI selection origins must match their exact immutable candidate."""
    lookup = lambda model, identifier: rows.get({Revision:'revision', ContentBlock:'paragraph', Branch:'branch'}[model], {}).get(identifier)
    batches = rows.get('generation_batch', {})
    versions = list(rows.get('chapter_version', {}).values())
    for batch in batches.values():
        selection = batch.request.get('selection')
        if not selection:
            continue
        base = lookup(Revision, selection['startAnchor']['revisionId'])
        if not base or batch.origin_anchor != selection['endAnchor'] or batch.accepted_prefix:
            raise ValueError('selection generation source')
        materialize(base, selection['startAnchor'], selection['endAnchor'], None, None, lookup)
    for revision in rows.get('revision', {}).values():
        origin = revision.provenance.get('generationSource')
        if origin is None:
            continue
        if not isinstance(origin, dict) or set(origin) != {'generationId','candidateVersion','contentId'}:
            raise ValueError('selection generation origin')
        # JSON booleans and floats compare equal to Python integers. Provenance
        # is an authenticated typed identity, not a coercible numeric hint.
        if (type(origin['candidateVersion']) is not int or origin['candidateVersion'] < 1
                or any(type(origin[field]) is not str or not origin[field]
                    or len(origin[field]) > 160 for field in ('generationId', 'contentId'))):
            raise ValueError('selection generation origin type')
        batch = batches.get(origin['generationId'])
        selection = batch.request.get('selection') if batch else None
        chapter = next((row for row in versions if batch and row.generation_id == batch.id
            and row.candidate_version == origin['candidateVersion'] and row.chapter_index == 1
            and row.content_id == origin['contentId']), None)
        proof = revision.provenance.get('selectionEdit', {})
        if (not selection or not chapter or chapter.completeness != 'complete' or chapter.durability != 'saved'
                or chapter.content_hash != digest(chapter.text) or proof.get('startAnchor') != selection['startAnchor']
                or proof.get('endAnchor') != selection['endAnchor']):
            raise ValueError('selection generation candidate')
        base = lookup(Revision, proof['baseRevisionId'])
        first, last = anchor_order(base, selection['startAnchor']), anchor_order(base, selection['endAnchor'])
        pieces = []
        for index in range(first[1], last[1] + 1):
            block = lookup(ContentBlock, base.manifest[first[0]]['blocks'][index])
            pieces.append(utf16_slice(block.text, selection['startAnchor']['end'] if index == first[1] else 0,
                selection['endAnchor']['end'] if index == last[1] else utf16_len(block.text)))
        if proof.get('replacementText') != replacement(selection, '\n\n'.join(pieces), chapter.text):
            raise ValueError('selection generation exact body')


def accept(db, owner, generation_id, data, key):
    from . import service
    from .schemas import SelectionEdit
    batch, task = service.load(db, generation_id, owner, lock=True)
    selection = batch.request.get('selection')
    if not selection or str(data.candidateVersion) not in batch.version_times:
        raise Problem('SELECTION_NOT_FOUND', '这份记录没有对应的选区写法', 404)
    row = db.scalar(select(GenerationChapterVersion).where(
        GenerationChapterVersion.generation_id == batch.id,
        GenerationChapterVersion.owner_id == owner,
        GenerationChapterVersion.candidate_version == data.candidateVersion,
        GenerationChapterVersion.chapter_index == 1))
    if not row or row.completeness != 'complete' or row.durability != 'saved' or row.content_hash != digest(row.text):
        raise Problem('CANDIDATE_UNACCEPTABLE', '完整保存后才能应用；已收到的片段仍可查看', 409)
    # Exact original bytes remain independent of current HEAD. Lifecycle and CAS
    # checks happen inside apply_selection's idempotent callback, so lost ACK
    # replay can still retrieve its committed receipt after archive.
    revision = owned(db, Revision, selection['startAnchor']['revisionId'], owner)
    first, last = anchor_order(revision, selection['startAnchor']), anchor_order(revision, selection['endAnchor'])
    pieces = []
    for index in range(first[1], last[1] + 1):
        block = owned(db, ContentBlock, revision.manifest[first[0]]['blocks'][index], owner)
        pieces.append(utf16_slice(block.text, selection['startAnchor']['end'] if index == first[1] else 0,
            selection['endAnchor']['end'] if index == last[1] else utf16_len(block.text)))
    edit = SelectionEdit(startAnchor=selection['startAnchor'], endAnchor=selection['endAnchor'],
        replacementText=replacement(selection, '\n\n'.join(pieces), row.text),
        expectedSourceHeadRevisionId=data.expectedSourceHeadRevisionId, sourcePolicy=data.sourcePolicy,
        targetBranchId=data.targetBranchId, expectedTargetHeadRevisionId=data.expectedTargetHeadRevisionId,
        name='选区扩写' if selection['purpose'] == 'expand' else '替换写法')
    provenance = {'generationId': batch.id, 'candidateVersion': data.candidateVersion, 'contentId': row.content_id}
    return apply_selection(db, owner, batch.book_id, edit, key,
        intent_scope='prose-selection-accept:' + batch.id, intent_data=data.model_dump(),
        generation_source=provenance)
