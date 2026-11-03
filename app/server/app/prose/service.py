"""Authorization, durable request receipts and public prose projections."""
from copy import deepcopy
from datetime import timedelta
from sqlalchemy import select, func
from ..models import (GenerationBatch, GenerationChapterVersion, GenerationAcceptance,
    Task, ModelProfile, Consent, OperationPlan, Book, Branch, Revision, ContentBlock,
    Progress, Outbox, Idempotency, uid, now)
from ..domain import (owned, validate_anchor, idempotent, digest, canonical, is_tail,
    is_import_root, utf16_len, make_anchor)
from ..errors import Problem
from ..schemas import Operation
from . import PROTOCOL, is_imported_history, require_writable
from .policy import freeze, profile, budget
from ..execution_prompts import freeze_prompts
from ..model_budget import call_budget_exceeded, input_budget_exceeded, output_budget_exceeded

TERMINAL = {'succeeded', 'failed', 'cancelled'}

def load(db, generation_id, owner, *, lock=False):
    probe = db.scalar(select(GenerationBatch).where(GenerationBatch.id == generation_id,
        GenerationBatch.owner_id == owner))
    if probe is None:
        raise Problem('RESOURCE_NOT_FOUND', '写作记录不存在或不可访问', 404)
    owned(db, Book, probe.book_id, owner)
    task = owned(db, Task, probe.task_id, owner, lock=lock)
    query = select(GenerationBatch).where(GenerationBatch.id == generation_id, GenerationBatch.owner_id == owner)
    batch = db.scalar((query.with_for_update() if lock else query).execution_options(populate_existing=True))
    if batch is None or task.result.get('deleted'):
        raise Problem('RESOURCE_NOT_FOUND', '写作记录不存在或不可访问', 404)
    return batch, task

def receipt(batch):
    return {'generationId': batch.id, 'taskId': batch.task_id, 'protocol': PROTOCOL}

def chapters(db, batch, version=None):
    return list(db.scalars(select(GenerationChapterVersion).where(
        GenerationChapterVersion.generation_id == batch.id,
        GenerationChapterVersion.owner_id == batch.owner_id,
        GenerationChapterVersion.candidate_version == (version or batch.candidate_version))
        .order_by(GenerationChapterVersion.chapter_index)))

def stream_name(task_id, version, index):
    return f'prose-stream-{task_id}-{version}-{index}.json'

def event(db, task, kind, payload):
    task.event_seq += 1
    from ..models import TaskEvent
    db.add(TaskEvent(task_id=task.id, seq=task.event_seq, type=kind, payload=payload))

def reserve_authorization(db, task, frozen, request, analyses, *, previous=None):
    live = owned(db, ModelProfile, frozen['id'], task.owner_id, lock=True)
    if live.status != 'active' or not live.key_cipher:
        raise Problem('NO_PROVIDER', '模型通道已停用，未创建调用')
    caps = budget(profile(frozen), request['chapterLength'], analyses, request['chapterCount'])
    if (caps['maxCalls'] > 100 or call_budget_exceeded(live, caps['maxCalls'])
            or input_budget_exceeded(live, caps['inputTokens'])
            or output_budget_exceeded(live, caps['outputTokens'])):
        raise Problem('BUDGET_NOT_AUTHORIZED', '当前额度不能完成所选章数与篇幅，请调整设置或主动减少章数', 403)
    expires = now() + timedelta(minutes=60)
    hashed = digest(canonical({'request': request, 'caps': caps, 'profileVersion': frozen['version']}))
    plan = OperationPlan(id=uid(), owner_id=task.owner_id, profile_id=live.id,
        profile_version=frozen['version'], plan_hash=hashed, payload=deepcopy(request), details=caps, expires_at=expires)
    consent = Consent(id=uid(), owner_id=task.owner_id, plan_id=plan.id, profile_id=live.id,
        profile_version=frozen['version'], plan_hash=hashed, max_calls=caps['maxCalls'],
        input_limit=caps['inputTokens'], output_limit=caps['outputTokens'], expires_at=expires, task_id=task.id)
    db.add_all([plan, consent])
    task.payload = {**task.payload, 'profileId': live.id, 'consentId': consent.id}
    task.checkpoint = {**task.checkpoint, 'completionPolicy': caps['completionPolicy'],
        'executionPrompts':task.checkpoint['executionPrompts'] if 'executionPrompts' in task.checkpoint else freeze_prompts(), 'repairRemaining': 1, 'saveOnly': False}
    return caps

def create(db, owner, book_id, data, key):
    owned(db, Book, book_id, owner)
    if data.originAnchor.bookId != book_id:
        raise Problem('RESOURCE_NOT_FOUND', '写作起点不属于这本故事', 404)
    def make():
        from ..api_ai import resolve_operation
        live = owned(db, ModelProfile, data.profileId, owner, lock=True)
        op = Operation(operation=data.operation, profileId=data.profileId, chapterCount=data.chapterCount,
            anchor=data.originAnchor, instruction=data.instruction,
            expectedHeadRevisionId=data.expectedHeadRevisionId, target=data.target)
        if data.selection is not None:
            from .selection_generation import source
            source(db, owner, book_id, data.selection.model_dump(), data.expectedHeadRevisionId)
        _, _, missing = resolve_operation(db, op, owner, live, inline_boundary=True,
            pinned_revision=data.selection is not None and data.selection.sourcePolicy == 'forkPinned')
        branch, revision, block = validate_anchor(db, data.originAnchor.model_dump(), owner)
        from .accepted_basis import collect_accepted_basis,SCHEMA
        accepted_basis = collect_accepted_basis(db,owner,data.originAnchor.model_dump(),revision)
        if data.target.mode == 'append_existing' and (data.target.branchId != branch.id
                or data.operation != 'continue_at_tail' or is_import_root(db, owned(db, Book, book_id, owner), branch)
                or not is_tail(revision, data.originAnchor.model_dump(), block)):
            raise Problem('TARGET_INVALID', '这个起点需要创建子线')
        if db.scalar(select(func.count()).select_from(Task).where(Task.owner_id == owner,
                Task.task_type == 'story_flow', Task.state.notin_(TERMINAL))) >= 3:
            raise Problem('CONCURRENCY_LIMIT', '请先等待当前写作完成', 429)
        task = Task(id=uid(), owner_id=owner, task_type='story_flow',
            payload={**op.model_dump(), 'bookId': book_id, 'protocol': PROTOCOL,'acceptedBasisSchema':SCHEMA},
            checkpoint={'calls': {}, 'proseStreamRefs': [], 'acceptedEventBasis':accepted_basis},
            result={'phase': 'preparing', 'requestedChapters': data.chapterCount, 'completedChapters': 0})
        request = {**data.model_dump(),'acceptedBasisSchema':SCHEMA}
        frozen = freeze(live)
        reserve_authorization(db, task, frozen, request, len(missing))
        if data.selection is not None:
            prompts = deepcopy(task.checkpoint['executionPrompts'])
            prompts['prose_chapter'] = prompts['prose_selection']
            task.checkpoint = {**task.checkpoint, 'executionPrompts': prompts}
        batch = GenerationBatch(id=uid(), owner_id=owner, task_id=task.id, book_id=book_id,
            origin_anchor=data.originAnchor.model_dump(), request=request, profile_snapshot=frozen,
            candidate_version=1, accepted_prefix=0,
            target_branch_id=branch.id if data.target.mode == 'append_existing' else None,
            version_times={'1': now().isoformat()})
        db.add(task); db.flush(); db.add(batch); db.flush()
        make_version(db, batch, task, 1)
        event(db, task, 'phase', {'candidateVersion': 1, 'phase': 'preparing'})
        return receipt(batch)
    return idempotent(db, owner, 'prose-create:' + book_id, key, data.model_dump(), make)

def make_version(db, batch, task, version, *, inherited=()):
    by_index = {chapter.chapter_index: chapter for chapter in inherited}
    from .facts import validate_delta
    ledger=deepcopy(task.checkpoint.get('proseFactLedger',{}))
    event_ledger = deepcopy(task.checkpoint.get('proseEventLedger', {}))
    presentation_ledger = deepcopy(task.checkpoint.get('prosePresentationLedger', {}))
    for prior in by_index.values():
        events = event_ledger.get(str(prior.candidate_version), {}).get(str(prior.chapter_index))
        if events is not None:
            from .events import validate
            event_ledger.setdefault(str(version), {})[str(prior.chapter_index)] = validate(events, prior.text)
        delta=ledger.get(str(prior.candidate_version),{}).get(str(prior.chapter_index))
        if delta is not None:ledger.setdefault(str(version),{})[str(prior.chapter_index)]=validate_delta(delta,prior.text)
        presentation = presentation_ledger.get(str(prior.candidate_version), {}).get(str(prior.chapter_index))
        if presentation is not None:
            from .presentation import validate_record
            presentation_ledger.setdefault(str(version), {})[str(prior.chapter_index)] = validate_record(presentation, prior.text, prior.title)
    for index in range(1, batch.request['chapterCount'] + 1):
        prior = by_index.get(index)
        row = GenerationChapterVersion(id=uid(), owner_id=batch.owner_id, generation_id=batch.id,
            candidate_version=version, chapter_index=index, content_id=prior.content_id if prior else uid(),
            title=prior.title if prior else f'第 {index} 章', text=prior.text if prior else '',
            content_hash=prior.content_hash if prior else None,
            stream_version=prior.stream_version if prior else 1,
            snapshot_version=prior.snapshot_version if prior else 0,
            completeness=prior.completeness if prior else 'partial',
            durability=prior.durability if prior else 'unconfirmed', hints=deepcopy(prior.hints) if prior else [])
        db.add(row)
    task.checkpoint = {**task.checkpoint, 'proseFactLedger':ledger, 'proseEventLedger':event_ledger, 'prosePresentationLedger': presentation_ledger, 'proseStreamRefs': task.checkpoint.get('proseStreamRefs', []) +
        [stream_name(task.id, version, index) for index in range(batch.accepted_prefix + 1, batch.request['chapterCount'] + 1)]}

def historical_metadata(task, version=None):
    metadata = (task.checkpoint.get('portableHistoricalCurrent', {}) if version is None
        else task.checkpoint.get('proseVersionStates', {}).get(str(version), {}))
    defaults = {'state': 'historical-unknown', 'phase': 'historical-unknown',
        'providerState': 'unknown', 'persistenceState': 'unknown', 'usageState': 'unknown',
        'cancelRequested': False, 'errorCode': None, 'targetBranchId': None,
        'lastAcceptedRevisionId': None, 'eventCursor': 0}
    return {**defaults, **{key: metadata[key] for key in defaults if key in metadata}} if isinstance(metadata, dict) else defaults


def snapshot(db, batch, task, version=None):
    version = version or batch.candidate_version
    if str(version) not in batch.version_times:
        raise Problem('RESOURCE_NOT_FOUND', '候选版本不存在', 404)
    rows = chapters(db, batch, version)
    current = version == batch.candidate_version
    imported = is_imported_history(task)
    frozen = task.checkpoint.get('proseVersionStates', {}).get(str(version), {}) if not current else {}
    if imported:
        frozen = historical_metadata(task, None if current else version)
    accepted = batch.accepted_prefix if current else db.scalar(select(func.coalesce(func.max(GenerationAcceptance.prefix_count), 0))
        .where(GenerationAcceptance.generation_id == batch.id, GenerationAcceptance.candidate_version == version))
    complete = sum(row.completeness == 'complete' and row.durability == 'saved' for row in rows)
    projected = []
    event_metadata_unavailable = False
    for row in rows:
        value = {'index': row.chapter_index, 'contentId': row.content_id, 'title': row.title,
            'text': row.text, 'contentHash': row.content_hash, 'streamVersion': row.stream_version,
            'snapshotVersion': row.snapshot_version, 'completeness': row.completeness,
            'durability': row.durability, 'canAccept': bool(current and row.chapter_index > accepted
                and row.completeness == 'complete' and row.durability == 'saved' and row.content_hash == digest(row.text)),
            'hints': deepcopy(row.hints), 'factDelta':deepcopy(task.checkpoint.get('proseFactLedger',{}).get(str(version),{}).get(str(row.chapter_index)))}
        presentation = task.checkpoint.get('prosePresentationLedger', {}).get(str(version), {}).get(str(row.chapter_index))
        events = task.checkpoint.get('proseEventLedger', {}).get(str(version), {}).get(str(row.chapter_index))
        if events is not None:
            from .events import validate
            try:value['eventLedger'] = validate(events, row.text)
            except (ValueError,TypeError):
                event_metadata_unavailable = True
                if not any(hint.get('code')=='CHECK_UNAVAILABLE' for hint in value['hints']):value['hints'].append({'code':'CHECK_UNAVAILABLE'})
        if presentation is not None:
            from .presentation import validate_record
            value['rawPresentation'] = validate_record(presentation, row.text, row.title)
        if row.chapter_index > accepted and not imported:
            from .stream import overlay
            retained = overlay(batch, task, row)
            if retained:
                value.update({key: retained[key] for key in ('title', 'text', 'contentHash', 'streamVersion',
                    'snapshotVersion', 'completeness', 'durability', 'canAccept', 'hints')})
                if 'factDelta' in retained:value['factDelta']=deepcopy(retained['factDelta'])
                else:value['factDelta']=None
                if 'eventLedger' in retained:value['eventLedger']=deepcopy(retained['eventLedger'])
                else:value.pop('eventLedger',None)
                if 'rawPresentation' in retained:value['rawPresentation']=deepcopy(retained['rawPresentation'])
                else:value.pop('rawPresentation',None)
        projected.append(value)
    complete = sum(row['completeness'] == 'complete' and row['durability'] == 'saved' for row in projected)
    from .events import fold
    event_state_status='unavailable' if event_metadata_unavailable else 'complete'
    try:event_state = fold([row['eventLedger'] for row in projected if row.get('eventLedger')])
    except (ValueError,TypeError):
        event_state=fold([]);event_state_status='unavailable'
        for row in projected:
            if not any(hint.get('code')=='CHECK_UNAVAILABLE' for hint in row['hints']):row['hints']=[*row['hints'],{'code':'CHECK_UNAVAILABLE'}]
    state = 'result-unknown' if task.error_code == 'PROVIDER_STATUS_UNKNOWN' else task.state
    from .accepted_basis import display_basis,validate_basis_contract
    basis = task.checkpoint.get('acceptedEventBasis')
    basis_unavailable=False
    try:validate_basis_contract(batch,task)
    except Problem:basis=None;basis_unavailable=True
    public_basis=None
    if basis is not None:
        try:public_basis=display_basis(basis,db)
        except (ValueError,TypeError,KeyError,AttributeError):basis_unavailable=True
    return {'id': batch.id, 'taskId': task.id, 'protocol': PROTOCOL, 'bookId': batch.book_id,
        'importedHistory': imported, 'readOnly': imported,
        'scopeAnchor': deepcopy(batch.origin_anchor), 'createdAt': batch.created_at.isoformat(),
        'candidateVersion': version, 'requestedChapters': batch.request['chapterCount'],
        'completedChapters': complete, 'acceptedPrefix': accepted,
        'targetBranchId': batch.target_branch_id if current else frozen.get('targetBranchId', batch.target_branch_id),
        'lastAcceptedRevisionId': batch.last_accepted_revision_id if current else frozen.get('lastAcceptedRevisionId', batch.last_accepted_revision_id),
        'eventCursor': frozen.get('eventCursor', task.event_seq), 'state': state if current and not imported else frozen.get('state', 'completed'),
        'phase': task.result.get('phase', 'preparing') if current and not imported else frozen.get('phase', 'historical'),
        'providerState': frozen.get('providerState', task.provider_state),
        'persistenceState': frozen.get('persistenceState', task.persistence_state),
        'usageState': frozen.get('usageState', task.usage_state),
        'cancelRequested': frozen.get('cancelRequested', task.cancel_requested),
        'errorCode': task.error_code if current and not imported else frozen.get('errorCode'),
        'chapters': projected, 'eventState':event_state, 'eventStateStatus':event_state_status,
        **({'acceptedEventBasis':public_basis} if public_basis is not None else {}),
        **({'acceptedBasisUnavailable':True} if basis_unavailable else {}),
        'fee': {'amount': None, 'currency': None, 'certainty': 'unknown'},
        **({'selection':deepcopy(batch.request['selection'])} if batch.request.get('selection') else {})}

def accept(db, owner, generation_id, data, key):
    from .accepted_projection import project_accepted_text
    from .accepted_proof import create_accepted_proof
    batch, task = load(db, generation_id, owner, lock=True)
    def commit():
        if batch.request.get('selection'):
            raise Problem('SELECTION_ACCEPT_REQUIRED', '这份写法用于选区修订，请从选区候选应用', 409)
        from .accepted_basis import validate_basis_contract
        validate_basis_contract(batch,task)
        if data.candidateVersion != batch.candidate_version or data.expectedAcceptedPrefix != batch.accepted_prefix:
            raise Problem('VERSION_CONFLICT', '候选或接受位置已变化，草稿保留', 409,
                {'candidateVersion': batch.candidate_version, 'acceptedPrefix': batch.accepted_prefix})
        rows = chapters(db, batch)
        if not batch.accepted_prefix < data.prefixCount <= batch.request['chapterCount']:
            raise Problem('PREFIX_INVALID', '只能接受新的连续前缀', 409)
        chosen = rows[batch.accepted_prefix:data.prefixCount]
        if ([row.chapter_index for row in rows] != list(range(1, batch.request['chapterCount'] + 1))
                or [row.chapter_index for row in chosen] != list(range(batch.accepted_prefix + 1, data.prefixCount + 1))
                or len(chosen) != data.prefixCount - batch.accepted_prefix or any(row.completeness != 'complete'
                or row.durability != 'saved' or row.content_hash != digest(row.text) for row in chosen)):
            raise Problem('CANDIDATE_UNACCEPTABLE', '只有完整且已保存的连续章节可以接受', 409)
        projections = [project_accepted_text(row.text, 'server-v1')['segments'] for row in chosen]
        if any(not parts for parts in projections):
            raise Problem('CANDIDATE_UNACCEPTABLE', '这次正文没有可接受的段落，候选仍保留', 409)
        source, base, anchor_block = validate_anchor(db, batch.origin_anchor, owner)
        if batch.origin_anchor['start'] != batch.origin_anchor['end']:
            raise Problem('ANCHOR_INVALID','候选需要明确的字符起点，原文仍保留')
        book = owned(db, Book, batch.book_id, owner)
        if source.status != 'active' or book.status != 'active':
            raise Problem('BRANCH_ARCHIVED', '请先恢复归档内容', 409)
        first = batch.accepted_prefix == 0
        paragraph_prefixes = []
        if first:
            if data.expectedHeadRevisionId != base.id:
                raise Problem('VERSION_CONFLICT', '接受起点与候选不一致', 409)
            source = owned(db, Branch, source.id, owner, lock=True)
            if data.sourcePolicy == 'requireUnchanged' and source.head_revision_id != base.id:
                raise Problem('VERSION_CONFLICT', '故事线已更新，候选仍保留', 409,
                    {'currentHeadRevisionId': source.head_revision_id, 'canForkPinnedRevision': True})
            append = data.target == 'append'
            if append and (data.sourcePolicy != 'requireUnchanged' or batch.request['target']['mode'] != 'append_existing'
                    or batch.request['operation'] != 'continue_at_tail' or is_import_root(db, book, source)
                    or not is_tail(base, batch.origin_anchor, anchor_block)):
                raise Problem('TARGET_INVALID', '这个起点不能追加原线', 409)
            if not append and batch.request['target']['mode'] == 'append_existing' and data.sourcePolicy != 'forkPinned':
                raise Problem('TARGET_INVALID', '自然末尾续写使用已选故事线', 409)
            target = source if append else Branch(id=uid(), owner_id=owner, book_id=book.id,
                parent_branch_id=source.id, fork_anchor=deepcopy(batch.origin_anchor),
                name=batch.request['target']['name'], head_revision_id=uid(), origin_operation_id=task.id)
            if not append:
                db.add(target); db.flush()
            manifest = []
            stopped = False
            for chapter in base.manifest:
                keep = []
                for block_id in chapter['blocks']:
                    if block_id == anchor_block.id:
                        if batch.origin_anchor['end'] == utf16_len(anchor_block.text):
                            keep.append(block_id)
                        elif batch.origin_anchor['end'] > 0:
                            from .prefix_fragments import create_fragment
                            fragment, proof = create_fragment(db, owner, book.id, base,
                                anchor_block, batch.origin_anchor['end'])
                            keep.append(fragment.id)
                            paragraph_prefixes.append(proof)
                        stopped = True
                        break
                    keep.append(block_id)
                if keep:
                    manifest.append({**chapter, 'blocks': keep})
                if stopped:
                    break
        else:
            if data.target != 'append' or data.sourcePolicy != 'requireUnchanged':
                raise Problem('TARGET_INVALID', '首次接受后目标故事线固定', 409)
            target = owned(db, Branch, batch.target_branch_id, owner, lock=True)
            if target.book_id != book.id or target.status != 'active':
                raise Problem('RESOURCE_NOT_FOUND', '已接受故事线不可用', 404)
            if (data.expectedHeadRevisionId != batch.last_accepted_revision_id
                    or target.head_revision_id != batch.last_accepted_revision_id):
                raise Problem('VERSION_CONFLICT', '目标线已在另一处更新，剩余候选保留', 409,
                    {'currentHeadRevisionId': target.head_revision_id, 'acceptedPrefix': batch.accepted_prefix})
            base = owned(db, Revision, batch.last_accepted_revision_id, owner)
            manifest = deepcopy(base.manifest)
            append = True
        prefix_ids = {block_id for chapter in manifest for block_id in chapter['blocks']}
        new_blocks, chapter_ids, accepted_proofs = [], [], []
        for row, parts in zip(chosen, projections):
            chapter_id = uid(); ids = []
            for part in parts:
                paragraph = part['text']
                block = ContentBlock(id=uid(), owner_id=owner, book_id=book.id, chapter_id=chapter_id,
                    text=paragraph, html=None, content_hash=digest(paragraph), source='ai')
                db.add(block); new_blocks.append(block); ids.append(block.id)
            from .chapter_number import chapter_title
            manifest.append({'id': chapter_id, 'title': chapter_title(row.title, len(manifest)+1), 'blocks': ids}); chapter_ids.append(chapter_id)
            accepted_proofs.append(create_accepted_proof(row.text, row.content_id,
                row.chapter_index, chapter_id, ids))
        revision = Revision(id=uid(), owner_id=owner, branch_id=target.id, parent_revision_id=base.id,
            sequence=base.sequence + 1 if append else 1, manifest=manifest, content_hash=digest(canonical(manifest)),
            provenance={'type': 'ai', 'operation': batch.request['operation'], 'taskId': task.id,
                'generationId': batch.id, 'candidateVersion': batch.candidate_version,
                'acceptedFrom': batch.accepted_prefix + 1, 'acceptedThrough': data.prefixCount,
                'acceptedProofSchema': 'accepted-content-proof-v1',
                'profileId': batch.profile_snapshot['id'], 'profileVersion': batch.profile_snapshot['version']})
        if paragraph_prefixes:
            from .prefix_fragments import SCHEMA as PREFIX_SCHEMA
            revision.provenance = {**revision.provenance, 'paragraphPrefixSchema':PREFIX_SCHEMA,
                'paragraphPrefixes':paragraph_prefixes}
        db.add(revision); target.head_revision_id = revision.id; target.version += 1
        batch.accepted_prefix = data.prefixCount; batch.target_branch_id = target.id
        batch.last_accepted_revision_id = revision.id; db.flush()
        result = {'generationId': batch.id, 'candidateId': batch.id, 'candidateVersion': batch.candidate_version,
            'acceptedPrefix': batch.accepted_prefix, 'branchId': target.id, 'revisionId': revision.id,
            'created': first and not append, 'committed': True,
            'readingAnchor': make_anchor(book.id, target.id, revision.id, new_blocks[0]),
            'chapterCount': len(chosen), 'chapterIds': chapter_ids,
            'acceptedProofSchema': 'accepted-content-proof-v1', 'acceptedProofs': accepted_proofs}
        db.add(GenerationAcceptance(owner_id=owner, generation_id=batch.id,
            candidate_version=batch.candidate_version, prefix_count=data.prefixCount, revision_id=revision.id, result=result))
        prior = db.scalar(select(Progress).where(Progress.owner_id == owner, Progress.branch_id == base.branch_id,
            Progress.revision_id == base.id))
        if prior and prefix_ids:
            old = prior.max_observed_anchor
            block_id = old['blockId'] if old['blockId'] in prefix_ids else manifest[-len(chosen)-1]['blocks'][-1]
            boundary = owned(db, ContentBlock, block_id, owner)
            end = old['end'] if old['blockId'] == block_id else utf16_len(boundary.text)
            inherited = make_anchor(book.id, target.id, revision.id, boundary, end)
            db.add(Progress(owner_id=owner, book_id=book.id, branch_id=target.id, revision_id=revision.id,
                anchor=inherited, max_observed_anchor=inherited, device_sequences={}))
        db.add(Outbox(owner_id=owner, event_type='story_committed', payload={'branchId': target.id,
            'revisionId': revision.id, 'baseRevisionId': base.id}))
        if not is_imported_history(task):
            event(db, task, 'phase', {'candidateVersion': batch.candidate_version, 'phase': task.result.get('phase'),
                'acceptedPrefix': batch.accepted_prefix})
        return result
    return idempotent(db, owner, 'prose-accept:' + generation_id, key, data.model_dump(), commit)

def rewrite(db, owner, generation_id, data, key):
    batch, task = load(db, generation_id, owner, lock=True)
    require_writable(task)
    def make():
        from .accepted_basis import validate_basis_contract
        validate_basis_contract(batch,task)
        if task.state not in TERMINAL or task.persistence_state == 'recoverable':
            raise Problem('GENERATION_RUNNING', '请先停止或保存当前候选', 409)
        if data.candidateVersion != batch.candidate_version or data.expectedAcceptedPrefix != batch.accepted_prefix:
            raise Problem('VERSION_CONFLICT', '候选版本已变化', 409)
        if batch.accepted_prefix >= batch.request['chapterCount']:
            raise Problem('CANDIDATE_ACCEPTED', '本批章节已全部接受', 409)
        old = chapters(db, batch)
        prior_view = snapshot(db, batch, task)
        metadata = {name: prior_view[name] for name in ('targetBranchId', 'lastAcceptedRevisionId', 'eventCursor',
            'state', 'phase', 'providerState', 'persistenceState', 'usageState', 'cancelRequested', 'errorCode')}
        task.checkpoint = {**task.checkpoint, 'proseVersionStates': {
            **task.checkpoint.get('proseVersionStates', {}), str(batch.candidate_version): metadata}}
        batch.request = {**batch.request, 'instruction': data.instruction}
        remaining = {**batch.request, 'chapterCount': batch.request['chapterCount'] - batch.accepted_prefix}
        reserve_authorization(db, task, batch.profile_snapshot, remaining, 0)
        batch.candidate_version += 1
        batch.version_times = {**batch.version_times, str(batch.candidate_version): now().isoformat()}
        make_version(db, batch, task, batch.candidate_version, inherited=old[:batch.accepted_prefix])
        task.state = 'queued'; task.error_code = None; task.cancel_requested = False
        task.provider_state = 'not_started'; task.persistence_state = 'pending'; task.result_outcome = None
        task.payload = {**task.payload, 'instruction': data.instruction}
        task.result = {**task.result, 'phase': 'preparing', 'completedChapters': batch.accepted_prefix}
        event(db, task, 'phase', {'candidateVersion': batch.candidate_version, 'phase': 'preparing'})
        return receipt(batch)
    return idempotent(db, owner, 'prose-rewrite:' + generation_id, key, data.model_dump(), make)
