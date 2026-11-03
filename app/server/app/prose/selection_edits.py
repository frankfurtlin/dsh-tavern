"""Immutable, scoped selection revisions. Manual editing never dispatches a model."""
from copy import deepcopy
from sqlalchemy import select
from ..domain import owned, validate_anchor, anchor_order, utf16_slice, utf16_len, digest, canonical, idempotent
from ..errors import Problem
from ..models import Book, Branch, Revision, ContentBlock, uid

SCHEMA = 'selection-edit-v1'
FIELDS = {'baseRevisionId', 'startAnchor', 'endAnchor', 'blockId', 'replacementText', 'sourceProofs'}


def materialize(source, start, end, replacement, block_id, lookup):
    if (start['revisionId'] != source.id or end['revisionId'] != source.id
            or start['branchId'] != source.branch_id or end['branchId'] != source.branch_id
            or start['bookId'] != end['bookId'] or start['chapterId'] != end['chapterId']
            or start['start'] != start['end'] or end['start'] != end['end']):
        raise ValueError('selection edit scope')
    ci, first, _ = anchor_order(source, start)
    ei, last, _ = anchor_order(source, end)
    if ci != ei or first > last or first == last and start['end'] > end['end']:
        raise ValueError('selection edit order')
    ids = source.manifest[ci]['blocks'][first:last+1]
    if len(ids) > 1000:
        raise ValueError('selection edit limit')
    rows = [lookup(ContentBlock, identifier) for identifier in ids]
    if any(row is None or row.owner_id != source.owner_id or row.book_id != start['bookId']
            or row.chapter_id != start['chapterId'] or digest(row.text) != row.content_hash for row in rows):
        raise ValueError('selection edit source bytes')
    try:
        prefix = utf16_slice(rows[0].text, 0, start['end'])
        suffix = utf16_slice(rows[-1].text, end['end'], utf16_len(rows[-1].text))
    except Problem as error:
        raise ValueError('selection edit unicode boundary') from error
    manifest = deepcopy(source.manifest)
    if replacement is None:
        if block_id is not None:
            raise ValueError('selection edit restore block')
        return manifest, None, [{'blockId': row.id, 'contentHash': row.content_hash} for row in rows]
    if not isinstance(replacement, str) or len(replacement) > 1_000_000 or not block_id or block_id in [i for c in source.manifest for i in c['blocks']]:
        raise ValueError('selection edit replacement')
    text = prefix + replacement + suffix
    manifest[ci]['blocks'] = manifest[ci]['blocks'][:first] + [block_id] + manifest[ci]['blocks'][last+1:]
    return manifest, text, [{'blockId': row.id, 'contentHash': row.content_hash} for row in rows]


def validate_selection_revision(revision, lookup, *, verify_hash=True):
    provenance = revision.provenance
    if not isinstance(provenance, dict):
        raise ValueError('selection edit provenance')
    marker, proof = provenance.get('selectionEditSchema'), provenance.get('selectionEdit')
    if marker is None and proof is None and provenance.get('kind') != 'selection_edit':
        return
    if marker != SCHEMA or provenance.get('kind') != 'selection_edit' or not isinstance(proof, dict) or set(proof) != FIELDS:
        raise ValueError('selection edit proof shape')
    source = lookup(Revision, proof['baseRevisionId'])
    parent = lookup(Revision, revision.parent_revision_id)
    branch = lookup(Branch, revision.branch_id)
    if (not source or not parent or not branch or source.owner_id != revision.owner_id
            or parent.owner_id != revision.owner_id or branch.owner_id != revision.owner_id
            or branch.parent_branch_id != source.branch_id or branch.book_id != proof['startAnchor']['bookId']
            or branch.fork_anchor != proof['startAnchor']):
        raise ValueError('selection edit ancestry')
    if parent.id != source.id:
        prior = parent.provenance.get('selectionEdit')
        if (parent.branch_id != revision.branch_id or parent.sequence + 1 != revision.sequence
                or parent.provenance.get('selectionEditSchema') != SCHEMA or not isinstance(prior, dict)
                or any(prior.get(k) != proof[k] for k in ('baseRevisionId', 'startAnchor', 'endAnchor'))):
            raise ValueError('selection edit candidate family')
    elif revision.sequence != 1:
        raise ValueError('selection edit first sequence')
    manifest, text, proofs = materialize(source, proof['startAnchor'], proof['endAnchor'],
        proof['replacementText'], proof['blockId'], lookup)
    expected_hash = source.content_hash if text is None else digest(canonical(manifest))
    if revision.manifest != manifest or proof['sourceProofs'] != proofs or verify_hash and revision.content_hash != expected_hash:
        raise ValueError('selection edit exact manifest')
    if text is not None:
        block = lookup(ContentBlock, proof['blockId'])
        if (not block or block.owner_id != revision.owner_id or block.book_id != branch.book_id
                or block.chapter_id != proof['startAnchor']['chapterId'] or block.source != 'manual'
                or block.html is not None or block.text != text or block.content_hash != digest(text)):
            raise ValueError('selection edit exact replacement bytes')


def validate_selection_families(revisions, blocks, lookup, *, verify_hash=True):
    """Manual blocks must have one introduction, including non-head candidates.

    `manual` is reserved for this revision protocol. It is an independent witness
    when every member's JSON provenance is stripped from an otherwise valid pack.
    This is structural validation, not authentication of a user-rewritten library.
    """
    witnessed = set()
    for revision in revisions:
        validate_selection_revision(revision, lookup, verify_hash=verify_hash)
        if revision.provenance.get('selectionEditSchema') == SCHEMA:
            identifier = revision.provenance['selectionEdit']['blockId']
            if identifier is not None:
                if identifier in witnessed:
                    raise ValueError('selection edit duplicate introduction')
                witnessed.add(identifier)
    manual = {block.id for block in blocks if block.source == 'manual'}
    if manual != witnessed:
        raise ValueError('selection edit missing introduction')


def apply_selection(db, owner, book_id, data, key, *, intent_scope=None, intent_data=None, generation_source=None):
    book = owned(db, Book, book_id, owner)
    def apply():
        start, end = data.startAnchor.model_dump(), data.endAnchor.model_dump()
        source_branch, source, _ = validate_anchor(db, start, owner)
        end_branch, end_source, _ = validate_anchor(db, end, owner)
        source_branch = owned(db, Branch, source_branch.id, owner, lock=True)
        if source_branch.book_id != book_id or end_branch.id != source_branch.id or end_source.id != source.id:
            raise Problem('SELECTION_SCOPE', '选区两端须属于同一正文版本')
        if book.status != 'active' or source_branch.status != 'active':
            raise Problem('BRANCH_ARCHIVED', '请先恢复归档故事，再修改正文', 409)
        if source_branch.head_revision_id != data.expectedSourceHeadRevisionId or data.sourcePolicy == 'requireUnchanged' and source.id != source_branch.head_revision_id:
            raise Problem('REVISION_CONFLICT', '来源已更新，请查看差异或明确从旧版本另开故事线', 409)
        target = owned(db, Branch, data.targetBranchId, owner, lock=True) if data.targetBranchId else None
        prior = owned(db, Revision, target.head_revision_id, owner) if target else None
        if target:
            if target.status != 'active':
                raise Problem('BRANCH_ARCHIVED', '请先恢复归档故事线，再修改正文', 409)
            proof = prior.provenance.get('selectionEdit', {})
            if (target.book_id != book_id or target.parent_branch_id != source_branch.id
                    or prior.provenance.get('selectionEditSchema') != SCHEMA
                    or proof.get('baseRevisionId') != source.id or proof.get('startAnchor') != start or proof.get('endAnchor') != end):
                raise Problem('SELECTION_TARGET', '此候选属于另一组选区，请另开修订')
            if target.head_revision_id != data.expectedTargetHeadRevisionId:
                raise Problem('REVISION_CONFLICT', '目标候选已更新，原输入保留', 409)
        elif data.expectedTargetHeadRevisionId is not None:
            raise Problem('SELECTION_TARGET', '新修订不能指定已有目标版本')
        block_id = uid() if data.replacementText is not None else None
        try:
            manifest, text, proofs = materialize(source, start, end, data.replacementText, block_id, db.get)
        except (ValueError, KeyError, TypeError) as error:
            raise Problem('SELECTION_INVALID', '选区顺序、字符边界或来源校验无效') from error
        if text is not None:
            db.add(ContentBlock(id=block_id, owner_id=owner, book_id=book_id, chapter_id=start['chapterId'],
                text=text, html=None, content_hash=digest(text), source='manual'))
        revision_id = uid()
        if not target:
            target = Branch(id=uid(), owner_id=owner, book_id=book_id, parent_branch_id=source_branch.id,
                fork_anchor=deepcopy(start), name=data.name, head_revision_id=revision_id)
            db.add(target); db.flush()
        else:
            target.head_revision_id = revision_id; target.version += 1
        revision = Revision(id=revision_id, owner_id=owner, branch_id=target.id,
            parent_revision_id=prior.id if prior else source.id, sequence=prior.sequence+1 if prior else 1,
            manifest=manifest, content_hash=source.content_hash if text is None else digest(canonical(manifest)), provenance={'kind':'selection_edit',
                'selectionEditSchema':SCHEMA, 'selectionEdit':{'baseRevisionId':source.id,
                    'startAnchor':start, 'endAnchor':end, 'blockId':block_id,
                    'replacementText':data.replacementText, 'sourceProofs':proofs}})
        if generation_source is not None:
            revision.provenance = {**revision.provenance, 'generationSource':deepcopy(generation_source)}
        db.add(revision); db.flush()
        validate_selection_revision(revision, db.get)
        return {'branchId':target.id, 'revisionId':revision.id, 'version':target.version,
            'sequence':revision.sequence, 'baseRevisionId':source.id, 'restored':text is None}
    return idempotent(db, owner, intent_scope or 'prose-selection-edit:'+book_id, key,
        data.model_dump() if intent_data is None else intent_data, apply)


def detail(db, owner, revision_id):
    revision = owned(db, Revision, revision_id, owner)
    branch = owned(db, Branch, revision.branch_id, owner)
    if revision.provenance.get('selectionEditSchema') != SCHEMA:
        raise Problem('SELECTION_NOT_FOUND', '这个版本没有选区修订', 404)
    validate_selection_revision(revision, db.get)
    proof = revision.provenance['selectionEdit']
    source = owned(db, Revision, proof['baseRevisionId'], owner)
    source_branch = owned(db, Branch, source.branch_id, owner)
    first = anchor_order(source, proof['startAnchor'])
    last = anchor_order(source, proof['endAnchor'])
    pieces = []
    for index in range(first[1], last[1]+1):
        block = db.get(ContentBlock, source.manifest[first[0]]['blocks'][index])
        pieces.append(utf16_slice(block.text, proof['startAnchor']['end'] if index == first[1] else 0,
            proof['endAnchor']['end'] if index == last[1] else utf16_len(block.text)))
    return {'bookId':branch.book_id, 'branchId':branch.id, 'revisionId':revision.id,
        'sequence':revision.sequence, 'sourceHeadRevisionId':source_branch.head_revision_id,
        'targetHeadRevisionId':branch.head_revision_id, 'baseRevisionId':source.id,
        'startAnchor':deepcopy(proof['startAnchor']), 'endAnchor':deepcopy(proof['endAnchor']),
        'originalText':'\n\n'.join(pieces), 'replacementText':proof['replacementText'],
        **({'generationSource':deepcopy(revision.provenance['generationSource'])} if 'generationSource' in revision.provenance else {})}


def select_revision(db, owner, branch_id, data, key):
    """Reapply a pinned family candidate or restore, with one durable receipt."""
    from .schemas import SelectionEdit
    branch = owned(db, Branch, branch_id, owner)
    identifier = data.candidateRevisionId if data.action == 'select_candidate' else data.expectedTargetHeadRevisionId
    basis = owned(db, Revision, identifier, owner)
    if basis.branch_id != branch.id or basis.provenance.get('selectionEditSchema') != SCHEMA:
        raise Problem('SELECTION_TARGET', '只能选择这组选区修订的候选')
    validate_selection_revision(basis, db.get)
    proof = basis.provenance['selectionEdit']
    edit = SelectionEdit(startAnchor=proof['startAnchor'], endAnchor=proof['endAnchor'],
        replacementText=proof['replacementText'] if data.action == 'select_candidate' else None,
        expectedSourceHeadRevisionId=data.expectedSourceHeadRevisionId, sourcePolicy=data.sourcePolicy,
        targetBranchId=branch.id, expectedTargetHeadRevisionId=data.expectedTargetHeadRevisionId)
    return apply_selection(db, owner, branch.book_id, edit, key,
        intent_scope='prose-selection-revision:'+branch.id, intent_data=data.model_dump(),
        generation_source=basis.provenance.get('generationSource') if data.action == 'select_candidate' else None)


def history(db, owner, branch_id, before, limit):
    branch = owned(db, Branch, branch_id, owner)
    filters = [Revision.owner_id == owner, Revision.branch_id == branch.id]
    if before is not None:
        filters.append(Revision.sequence < before)
    rows = list(db.scalars(select(Revision).where(*filters).order_by(Revision.sequence.desc(), Revision.id.desc()).limit(limit+1)))
    items = [{'revisionId':row.id, 'sequence':row.sequence, 'createdAt':row.created_at.isoformat(),
        'kind':row.provenance.get('kind'), 'restored':row.provenance.get('selectionEditSchema') == SCHEMA and row.provenance.get('selectionEdit', {}).get('replacementText') is None,
        'startAnchor':row.provenance.get('selectionEdit', {}).get('startAnchor'),
        'endAnchor':row.provenance.get('selectionEdit', {}).get('endAnchor')} for row in rows[:limit]]
    return {'branchId':branch.id, 'headRevisionId':branch.head_revision_id, 'items':items,
        'nextBeforeSequence':rows[limit-1].sequence if len(rows)>limit else None}
