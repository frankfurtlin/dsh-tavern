"""Finite prose batch execution. Replay saves received bytes, never resends."""
from copy import deepcopy
import json
import re
from sqlalchemy import select
from ..db import SessionLocal
from ..models import GenerationBatch, GenerationChapterVersion
from ..domain import digest, canonical
from ..storage import private_path
from ..errors import Problem
from . import service, stream
from .policy import LENGTHS
from .facts import from_review
from .events import from_review as events_from_review, fold as fold_events
from .accepted_event_state import combined_state
from .accepted_basis import prompt_basis
from .presentation import project, record, bounded_json_structure, unique_object
from ..execution_prompts import frozen_prompt

def chapter_context(base, earlier, chapter, frozen, instruction,prompt_text=None):
    """Keep the last chapter exact, and older titles/endings explicitly bounded.

    Confirmed source facts and their evidence stay intact. These endings are
    quotations, not generated summaries or new knowledge.
    """
    from ..completion_policy import input_ceiling
    from ..context_wire import wire_json
    from ..worker import PROMPTS
    from .policy import profile
    for excerpt_length in (120, 64, 32, 0):
        prior = [{'index': row['index'], 'title': row['title'], 'ending': row['text'][-excerpt_length:] if excerpt_length else ''}
            for row in earlier[:-1]]
        if earlier:
            prior.append(earlier[-1])
        events = [row['eventLedger'] for row in earlier if row.get('eventLedger')]
        value = {**base, 'instruction': instruction, 'previousChapters': prior, 'generatedFactLedger': [{'chapterIndex':row['index'],'delta':row['factDelta']} for row in earlier if row.get('factDelta')],
            'generatedEventLedger':events, 'generatedEventState':fold_events(events), 'chapter': chapter}
        if base.get('acceptedEventBasis') is not None:
            value['acceptedEventBasis']=prompt_basis(base['acceptedEventBasis'])
            value['combinedEventState']=combined_state(base['acceptedEventBasis'],earlier,base.get('acceptedCandidateBindings',[]),base['acceptedCandidateScope'])
        cap = input_ceiling(profile(frozen), LENGTHS[chapter['length']][3], world=True)
        if len(wire_json(value).encode()) + len((prompt_text if prompt_text is not None else (PROMPTS/'prose_chapter.txt').read_text('utf-8')).encode()) + 256 <= cap:
            return value
    raise Problem('CONTEXT_BUDGET_EXCEEDED', '必要事实和前一完整章超过窗口；已完成章保留，未追加调用')

def phase(task_id, name):
    from ..book_deletion import lock_task
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        stream.received_guard(db, task)
        batch = db.scalar(select(GenerationBatch).where(GenerationBatch.task_id == task.id))
        task.result = {**task.result, 'phase': name}
        service.event(db, task, 'phase', {'candidateVersion': batch.candidate_version, 'phase': name})
        db.commit()


def review_context(base, earlier, chapter, body, frozen, output_limit,prompt_text=None):
    """Review actual prior chapters and source facts, within the frozen limit.

    Review failure is an explicit soft warning. It must not discard a chapter
    that has already met the structural completeness rules.
    """
    from ..completion_policy import input_ceiling
    from ..context_wire import wire_json
    from ..worker import PROMPTS
    from .policy import profile
    value = {'text': body, 'prefix': base['prefix'], 'previousChapters': earlier,
        'confirmedFacts': base.get('confirmedFacts', []), 'evidence': base.get('evidence', []),
        'characterNames': base.get('characterNames', {}), 'sourcePositions': base.get('sourcePositions', {}),
        'scope': base.get('scope', {}), 'generatedFactLedger': [{'chapterIndex':row['index'],'delta':row['factDelta']} for row in earlier if row.get('factDelta')],
        'generatedEventLedger':[row['eventLedger'] for row in earlier if row.get('eventLedger')], 'chapter': chapter, '_structured': True}
    value['generatedEventState'] = fold_events(value['generatedEventLedger'])
    if base.get('acceptedEventBasis') is not None:
        value.update({key:deepcopy(base[key]) for key in ('acceptedEventBasis','acceptedCandidateBindings','acceptedCandidateScope')})
        value['acceptedEventBasis']=prompt_basis(base['acceptedEventBasis'])
        value['combinedEventState']=combined_state(base['acceptedEventBasis'],earlier,base['acceptedCandidateBindings'],base['acceptedCandidateScope'])
    cap = input_ceiling(profile(frozen), output_limit, world=True)
    if len(wire_json(value).encode()) + len((prompt_text if prompt_text is not None else (PROMPTS/'prose_review.txt').read_text('utf-8')).encode()) + 256 > cap:
        raise Problem('CONTEXT_BUDGET_EXCEEDED', '审阅上下文超过窗口；正文保留，本次不追加调用')
    return value

def parse_body(text, index):
    value = project(text, index)
    return value['title'], value['text']

def local_completeness(body, finish_reason, length, presentation=None, *, selection=False):
    # Structural completeness is separate from quality. A normally terminated
    # short/repetitive chapter remains readable and explicitly acceptable.
    if finish_reason not in ('stop', 'completed'):
        return False, 'CHAPTER_INTERRUPTED'
    if presentation and (presentation['pending'] or presentation['format'] == 'unrecognized'):
        return False, 'CHAPTER_FORMAT'
    if not body.strip():
        return False, 'NO_OUTPUT'
    # Unprojected control/JSON payloads are retained as a draft; they cannot
    # become reading text. Their projection is handled before this check.
    if body.lstrip().lower().startswith(('{', '[', '<html', '```')) or '<think' in body.lower():
        return False, 'CHAPTER_FORMAT'
    hints = quality_hints(body, length, selection=selection)
    return True, hints[0]['code'] if hints else None


def quality_hints(body, length, *, selection=False):
    visible = re.sub(r'\s+', '', body)
    result = []
    if not selection and len(visible) < LENGTHS[length][2]:
        result.append({'code':'CHAPTER_TOO_SHORT'})
    if not selection and len(visible) > LENGTHS[length][1] * 2:
        result.append({'code':'CHAPTER_TOO_LONG'})
    paragraphs = [paragraph.strip() for paragraph in body.splitlines() if paragraph.strip()]
    if paragraphs and len(set(paragraphs)) < len(paragraphs):
        result.append({'code':'CHAPTER_REPETITIVE'})
    if (re.search(r'(?:请|请选择|轮到你|还要|是否)(?:[^。！？\n]{0,24})(?:选择|继续|行动|A[/、／ ]B)', body[-220:])
            or re.search(r'(?:故事梗概|章节摘要|本章概要)\s*[:：]', body[:100])
            or any(phrase in body for phrase in ('命运的齿轮开始转动','一场前所未有的风暴','新的篇章就此展开'))):
        result.append({'code':'CHAPTER_PROGRESS_UNCERTAIN'})
    return result


def received_chapter(output, index, length, *, selection=False):
    presentation = project(output.get('text', ''), index)
    complete, code = local_completeness(presentation['text'], output.get('finishReason'), length, presentation, selection=selection)
    return presentation['title'], presentation['text'], complete, code

def review_hints(result):
    try:
        value = review_json(result)
        if not isinstance(value, dict) or type(value.get('completeChapter')) is not bool:
            raise ValueError()
        hints = []
        if not value['completeChapter']:
            hints.append({'code': 'CHAPTER_PROGRESS_UNCERTAIN'})
        for code in value.get('issues', []):
            if code in {'CONTINUITY', 'SPOILER', 'CHARACTER_VOICE', 'CHAPTER_PROGRESS_UNCERTAIN'}:
                hints.append({'code': code})
        return list({item['code']: item for item in hints}.values())
    except (ValueError, KeyError, TypeError, RecursionError):
        return [{'code': 'CHECK_UNAVAILABLE'}]

def review_json(result):
    raw = result['text']
    if not isinstance(raw, str) or len(raw) > 100_000 or not bounded_json_structure(raw):
        raise ValueError('review format')
    return json.loads(raw, object_pairs_hook=unique_object)

def review_complete(result):
    try:
        value = review_json(result)
        return value['completeChapter'] if (result.get('finishReason') in ('stop', 'completed')
            and isinstance(value, dict) and type(value.get('completeChapter')) is bool) else None
    except (ValueError, KeyError, TypeError, RecursionError):
        return None

def take_repair(task_id):
    from ..book_deletion import lock_task
    from ..worker import assert_task_active
    with SessionLocal() as db:
        task = lock_task(db, task_id); assert_task_active(db, task)
        if task.checkpoint.get('repairRemaining', 0) <= 0:
            return False
        task.checkpoint = {**task.checkpoint, 'repairRemaining': 0}
        db.commit()
        return True

def final(task_id, *, partial=False, recovered=False):
    from ..book_deletion import lock_task
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        stream.received_guard(db, task)
        batch = db.scalar(select(GenerationBatch).where(GenerationBatch.task_id == task.id))
        complete = sum(row.completeness == 'complete' and row.durability == 'saved' for row in service.chapters(db, batch))
        task.state = 'cancelled' if task.cancel_requested else 'failed' if partial or complete < batch.request['chapterCount'] else 'succeeded'
        task.persistence_state = 'saved'
        task.result_outcome = 'complete' if task.state == 'succeeded' else 'partial'
        if task.state == 'succeeded':
            task.error_code = None
        elif task.error_code is None:
            task.error_code = 'CHAPTER_INCOMPLETE'
        task.result = {**task.result, 'phase': 'completed' if task.state == 'succeeded' else 'stopped',
            'completedChapters': complete, 'recoveredWithoutProvider': recovered}
        service.event(db, task, 'terminal', {'candidateVersion': batch.candidate_version,
            'outcome': task.result_outcome, 'providerState': task.provider_state,
            'persistenceState': task.persistence_state, 'acceptedPrefix': batch.accepted_prefix,
            'fee': {'certainty': 'unknown'}})
        db.commit()

def restore(task_id):
    """Only existing known receipts and stream files participate in save replay."""
    from ..book_deletion import lock_task
    from ..worker import settle_usage
    with SessionLocal() as db:
        task = lock_task(db, task_id); stream.received_guard(db, task)
        batch = db.scalar(select(GenerationBatch).where(GenerationBatch.task_id == task.id))
        calls = dict(task.checkpoint.get('calls', {}))
        from ..models import KnowledgeSnapshot
        snapshot=db.get(KnowledgeSnapshot,task.checkpoint.get('snapshotId')) if task.checkpoint.get('snapshotId') else None
        subjects={character['id'] for character in snapshot.data.get('characters',[]) if isinstance(character,dict) and isinstance(character.get('id'),str)} if snapshot else set()
        owner = task.owner_id; version = batch.candidate_version; length = batch.request['chapterLength']; selection = bool(batch.request.get('selection'))
        prior_events = [value for key, value in sorted(task.checkpoint.get('proseEventLedger', {}).get(str(version), {}).items(), key=lambda item:int(item[0])) if int(key) <= batch.accepted_prefix]
        indices = range(batch.accepted_prefix + 1, batch.request['chapterCount'] + 1)
        contexts = [stream.context(db, task, f'generation:prose:v{version}:c{index}') for index in indices]
        db.commit()
    results = {}
    for stage, call in calls.items():
        path = private_path(owner, f"journal-{task_id}-{call['callId']}.json")
        if not path.exists():
            continue
        saved = json.loads(path.read_text('utf-8'))
        if (saved.get('taskId') != str(task_id) or saved.get('callId') != call['callId']
                or saved.get('hash') != digest(json.dumps(saved['result'], ensure_ascii=False, sort_keys=True))):
            raise Problem('JOURNAL_INVALID', '已接收结果校验失败', 409)
        results[stage] = saved['result']
        settle_usage(task_id, call['callId'], saved['result'])
    for ctx in contexts:
        index = ctx['chapterIndex']
        stage = f'generation:prose:v{version}:c{index}'
        original = results.get(stage)
        repaired = results.get(stage + ':repair')
        output = repaired or original
        if original and repaired and not original.get('notSent'):
            if received_chapter(original, index, length, selection=selection)[2] and not received_chapter(repaired, index, length, selection=selection)[2]:
                output = original
        if output and not output.get('notSent'):
            title, body, complete, code = received_chapter(output, index, length, selection=selection)
            review_stage = f'validation:prose:v{version}:c{index}' + (':repair' if output is repaired else '')
            reviewed = results.get(review_stage)
            reviewed_ok = reviewed and not reviewed.get('notSent')
            hints = review_hints(reviewed) if reviewed_ok else [{'code': 'CHECK_UNAVAILABLE'}]
            if complete and reviewed_ok and review_complete(reviewed) is False and not any(hint.get('code') == 'CHAPTER_PROGRESS_UNCERTAIN' for hint in hints):
                hints.append({'code': 'CHAPTER_PROGRESS_UNCERTAIN'})
            for hint in quality_hints(body, length, selection=selection) + ([{'code':code}] if code else []):
                if hint not in hints:hints.append(hint)
            previous = stream.read(ctx)
            raw_presentation = previous.get('rawPresentation') if previous else None
            for kind, receipt in (('original', original), ('repair', repaired)):
                if receipt and not receipt.get('notSent'):
                    raw_presentation = record(receipt['text'], index, kind=kind, finish_reason=receipt.get('finishReason'), previous=raw_presentation)
            selected_kind = 'repair' if output is repaired else 'original'
            raw_presentation = record(output['text'], index, kind=selected_kind, finish_reason=output.get('finishReason'), previous=raw_presentation)
            events = events_from_review(reviewed, body, index, subjects, prior_ids=[entry['id'] for ledger in prior_events for entry in ledger['events']])
            stream.persist(ctx, body, complete=complete, title=title, hints=hints, final=True,
                fact_delta=from_review(reviewed,body,subjects), raw_presentation=raw_presentation, event_ledger=events)
            prior_events.append(events)
        else:
            value = stream.read(ctx)
            if value:
                ctx['snapshotVersion'] = max(ctx['snapshotVersion'], value['snapshotVersion'])
                ctx['streamVersion'] = value['streamVersion']; ctx['text'] = value['text']
                stream.persist(ctx, value['text'], complete=value['completeness'] == 'complete',
                    title=value['title'], hints=value['hints'], final=True, fact_delta=value.get('factDelta'),
                    raw_presentation=value.get('rawPresentation'), event_ledger=value.get('eventLedger'))
                if value.get('eventLedger'):prior_events.append(value['eventLedger'])
    final(task_id, recovered=True)

def process(task_id):
    from ..book_deletion import lock_task
    from ..worker import assert_task_active, ensure_snapshot, generation_context, call_provider
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        stream.received_guard(db, task)
        batch = db.scalar(select(GenerationBatch).where(GenerationBatch.task_id == task.id))
        recovering = (task.checkpoint.get('saveOnly') or task.error_code == 'PERSISTENCE_UNAVAILABLE'
            or task.checkpoint.get('proseStartedVersion') == batch.candidate_version
                and bool(task.checkpoint.get('calls')))
        if not recovering:
            from .accepted_basis import validate_basis_contract
            validate_basis_contract(batch,task)
            assert_task_active(db, task)
            task.checkpoint = {**task.checkpoint, 'proseStartedVersion': batch.candidate_version}
            db.commit()
    if recovering:
        restore(task_id)
        return
    snapshot_id = ensure_snapshot(task_id)
    with SessionLocal() as db:
        from ..models import KnowledgeSnapshot
        task = lock_task(db, task_id); assert_task_active(db, task)
        batch = db.scalar(select(GenerationBatch).where(GenerationBatch.task_id == task.id))
        snapshot = db.get(KnowledgeSnapshot, snapshot_id)
        if snapshot.completeness != 'complete':
            raise Problem('AI_CONTEXT_NOT_READY', '当前上下文未完成，候选保留')
        base_data = generation_context(db, task, snapshot)
        if batch.request.get('selection'):
            from .selection_generation import context as selection_context
            base_data['selection'] = selection_context(db, batch)
        task.checkpoint = {**task.checkpoint,'snapshotId':snapshot.id}
        basis=task.checkpoint.get('acceptedEventBasis')
        if basis is not None:
            from .accepted_basis import candidate_bindings,database_roots
            from ..domain import prefix_blocks,validate_anchor
            _,basis_revision,_=validate_anchor(db,batch.origin_anchor,task.owner_id)
            roots=database_roots(db,task.owner_id,basis_revision,batch.book_id)
            visible_prefix=[{'sourceParagraphId':roots.get(part['blockId'],part['blockId']),'text':part['text']} for part in prefix_blocks(db,basis_revision,batch.origin_anchor,task.owner_id)]
            scope=batch.id
            bindings=task.checkpoint.get('acceptedCandidateBindings')
            if bindings is None:
                bindings=candidate_bindings(basis,snapshot,scope,visible_prefix,roots)
                task.checkpoint={**task.checkpoint,'acceptedCandidateBindings':bindings}
            base_data.update({'acceptedEventBasis':deepcopy(basis),'acceptedCandidateBindings':deepcopy(bindings),'acceptedCandidateScope':scope})
        request = dict(batch.request); version = batch.candidate_version
        frozen = dict(batch.profile_snapshot)
        chapter_prompt=frozen_prompt(task,'prose_chapter');review_prompt=frozen_prompt(task,'prose_review')
        policy = task.checkpoint['completionPolicy']['stages']
        start = batch.accepted_prefix + 1
        db.commit()
    for index in range(start, request['chapterCount'] + 1):
        phase(task_id, 'writing')
        with SessionLocal() as db:
            task = lock_task(db, task_id); assert_task_active(db, task)
            batch = db.scalar(select(GenerationBatch).where(GenerationBatch.task_id == task.id))
            ledger=task.checkpoint.get('proseFactLedger',{}).get(str(version),{})
            events=task.checkpoint.get('proseEventLedger',{}).get(str(version),{})
            earlier = [{'index': row.chapter_index, 'contentId':row.content_id, 'title': row.title, 'text': row.text, 'factDelta':ledger.get(str(row.chapter_index)), 'eventLedger':events.get(str(row.chapter_index))}
                for row in service.chapters(db, batch) if row.chapter_index < index and row.completeness == 'complete']
            data = chapter_context(base_data, earlier,
                {'index': index, 'count': request['chapterCount'], 'length': request['chapterLength'],
                    'targetMin': LENGTHS[request['chapterLength']][0], 'targetMax': LENGTHS[request['chapterLength']][1]},
                frozen, request['instruction'],chapter_prompt)
            db.commit()
        stage = f'generation:prose:v{version}:c{index}'
        output = call_provider(task_id, stage, 'prose_chapter', data, policy['generation']['totalCompletionTokens'])
        title, body, complete, code = received_chapter(output, index, request['chapterLength'], selection=bool(request.get('selection')))
        repaired = False
        if not complete and output.get('finishReason') in ('stop', 'completed'):
            if take_repair(task_id):
                output = call_provider(task_id, stage + ':repair', 'prose_chapter',
                    {**data, 'repair': {'code': code, 'receivedDraft': body}}, policy['generation']['totalCompletionTokens'])
                title, body, complete, code = received_chapter(output, index, request['chapterLength'], selection=bool(request.get('selection')))
                repaired = True
        phase(task_id, 'reviewing')
        hints = []
        semantic_complete = None
        reviewed = None
        try:
            reviewed = call_provider(task_id, f'validation:prose:v{version}:c{index}' + (':repair' if repaired else ''),
                'prose_review', review_context(base_data, earlier, data['chapter'], body, frozen, policy['validation']['totalCompletionTokens'],review_prompt),
                policy['validation']['totalCompletionTokens'])
            hints = review_hints(reviewed)
            semantic_complete = review_complete(reviewed)
        except Problem as error:
            if error.code in {'TASK_CANCELLED', 'BUDGET_NOT_AUTHORIZED', 'CONSENT_REQUIRED'}:
                raise
            hints = [{'code': 'CHECK_UNAVAILABLE'}]
        if complete and semantic_complete is False and not any(hint.get('code') == 'CHAPTER_PROGRESS_UNCERTAIN' for hint in hints):
            hints.append({'code': 'CHAPTER_PROGRESS_UNCERTAIN'})
        for hint in quality_hints(body, request['chapterLength'], selection=bool(request.get('selection'))) + ([{'code':code}] if code else []):
            if hint not in hints:hints.append(hint)
        prior_events = [row['eventLedger'] for row in earlier if row.get('eventLedger')]
        events = events_from_review(reviewed, body, index, base_data.get('characterNames', {}),
            prior_ids=[entry['id'] for ledger in prior_events for entry in ledger['events']])
        if fold_events(prior_events + [events])['hints'] and not any(hint['code'] == 'CONTINUITY' for hint in hints):
            hints.append({'code':'CONTINUITY'})
        phase(task_id, 'saving')
        with SessionLocal() as db:
            task = lock_task(db, task_id); assert_task_active(db, task)
            ctx = stream.context(db, task, stage)
            db.commit()
        if not stream.persist_raw(ctx, output['text'], finish_reason=output.get('finishReason'),
                kind='repair' if repaired else 'original', complete=complete, hints=hints, final=True,
                fact_delta=from_review(reviewed,body,base_data.get('characterNames',{})), event_ledger=events):
            raise Problem('PERSISTENCE_UNAVAILABLE', '正文已接收，等待保存恢复', 503)
        if not complete:
            final(task_id, partial=True)
            return
    final(task_id)
