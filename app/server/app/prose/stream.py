"""A private durable stream precedes its database mirror and public events."""
from copy import deepcopy
import json
import re
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from ..db import SessionLocal
from ..domain import canonical, digest, utf16_len
from ..models import GenerationBatch, GenerationChapterVersion, User
from ..storage import _write_task_journal, private_path
from ..errors import Problem
from ..task_lease import assert_lease, LeaseLost
from . import service
from .facts import validate_delta
from .events import validate as validate_events
from .presentation import record, validate_record
from ..provider_secrets import reject_echo

STAGE = re.compile(r'^generation:prose:v(\d+):c(\d+)(?::repair)?$')

def received_guard(db, task):
    assert_lease(task)
    user = db.get(User, task.owner_id)
    if task.result.get('deleted') or not user or user.status != 'active':
        raise Problem('TASK_CANCELLED', '内容已清理')

def context(db, task, stage):
    match = STAGE.fullmatch(stage)
    if not match:
        raise Problem('BUDGET_NOT_AUTHORIZED', '正文阶段无效', 403)
    version, index = map(int, match.groups())
    batch = db.scalar(select(GenerationBatch).where(GenerationBatch.task_id == task.id,
        GenerationBatch.owner_id == task.owner_id))
    row = db.scalar(select(GenerationChapterVersion).where(GenerationChapterVersion.generation_id == batch.id,
        GenerationChapterVersion.candidate_version == version, GenerationChapterVersion.chapter_index == index)) if batch else None
    if not batch or not row or batch.candidate_version != version or index <= batch.accepted_prefix:
        raise LeaseLost()
    ctx = {'owner': task.owner_id, 'taskId': task.id, 'generationId': batch.id,
        'candidateVersion': version, 'chapterIndex': index, 'contentId': row.content_id,
        'title': row.title, 'text': row.text, 'streamVersion': row.stream_version,
        'attemptKind': 'repair' if stage.endswith(':repair') else 'original',
        'snapshotVersion': row.snapshot_version, 'completeness': 'partial', 'hints': [],
        'name': service.stream_name(task.id, version, index)}
    # In-memory only. Neither the envelope nor its public/portable projection
    # includes this guard input; it uses the request's frozen credential.
    cipher = batch.profile_snapshot.get('key_cipher')
    if cipher:
        from ..providers import decrypt_key
        ctx['credentialGuard'] = decrypt_key(cipher)
    saved = read(ctx)
    if saved and saved['snapshotVersion'] > ctx['snapshotVersion']:
        ctx.update(text=saved['text'], title=saved['title'], streamVersion=saved['streamVersion'],
            snapshotVersion=saved['snapshotVersion'])
    return ctx

def read(ctx):
    path = private_path(ctx['owner'], ctx['name'])
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text('utf-8'))
        expected = {key: ctx[key] for key in ('taskId', 'generationId', 'candidateVersion', 'chapterIndex', 'contentId')}
        if any(value.get(key) != val for key, val in expected.items()):
            raise ValueError()
        body = value['value']
        if (value.get('hash') != digest(canonical(body)) or not isinstance(body.get('text'), str)
                or body.get('contentHash') != digest(body['text'])
                or not isinstance(body.get('title'), str) or len(body['title']) > 100
                or type(body.get('streamVersion')) is not int or body['streamVersion'] < 1
                or type(body.get('snapshotVersion')) is not int or body['snapshotVersion'] < 1
                or body.get('completeness') not in ('partial', 'complete')
                or not isinstance(body.get('hints'), list)
                or any(not isinstance(hint, dict) or set(hint) != {'code'} or not isinstance(hint['code'], str)
                    for hint in body['hints'])):
            raise ValueError()
        if body.get('factDelta') is not None:validate_delta(body['factDelta'],body['text'])
        if body.get('eventLedger') is not None:
            validate_events(body['eventLedger'], body['text'])
            if body['eventLedger']['chapterIndex'] != ctx['chapterIndex']:
                raise ValueError()
        if body.get('rawPresentation') is not None:
            validate_record(body['rawPresentation'], body['text'], body['title'])
            if body['rawPresentation']['chapterIndex'] != ctx['chapterIndex']:
                raise ValueError()
        return body
    except (ValueError, KeyError, TypeError):
        raise Problem('JOURNAL_INVALID', '正文持久文件校验失败', 409) from None

def persist_raw(ctx, raw, *, finish_reason=None, kind=None, **options):
    reject_echo(raw, ctx.get('credentialGuard'))
    retained = read(ctx)
    previous = retained.get('rawPresentation') if retained else None
    projected = record(raw, ctx['chapterIndex'], kind=kind or ctx.get('attemptKind', 'original'),
        finish_reason=finish_reason, previous=previous)
    body = projected['presentation']
    reject_echo(body['text'], ctx.get('credentialGuard'))
    reject_echo(body['title'], ctx.get('credentialGuard'))
    return persist(ctx, body['text'], title=body['title'], raw_presentation=projected, **options)


def persist(ctx, text, *, complete=False, title=None, hints=None, final=False, fact_delta=None, raw_presentation=None, event_ledger=None):
    retained = read(ctx)
    if retained and retained['snapshotVersion'] >= ctx['snapshotVersion']:
        # Another retained fragment can be newer than a long-lived upstream
        # context. A later finally/replay must not reuse that flush number for
        # different bytes or silently replace a text epoch.
        ctx.update(text=retained['text'], title=retained['title'],
            snapshotVersion=retained['snapshotVersion'], streamVersion=retained['streamVersion'])
    previous = ctx['text']
    if not text.startswith(previous):
        ctx['streamVersion'] += 1
    ctx['snapshotVersion'] += 1
    value = {'title': title or ctx['title'], 'text': text, 'streamVersion': ctx['streamVersion'],
        'snapshotVersion': ctx['snapshotVersion'], 'completeness': 'complete' if complete else 'partial',
        'hints': deepcopy(hints or []), 'contentHash': digest(text), 'final': final}
    if fact_delta is not None:value['factDelta']=validate_delta(fact_delta,text)
    if event_ledger is not None:
        value['eventLedger'] = validate_events(event_ledger, text)
        if event_ledger['chapterIndex'] != ctx['chapterIndex']:
            raise ValueError('event chapter binding')
    if raw_presentation is not None:
        value['rawPresentation'] = validate_record(raw_presentation, text, value['title'])
    envelope = {key: ctx[key] for key in ('taskId', 'generationId', 'candidateVersion', 'chapterIndex', 'contentId')}
    envelope.update(value=value, hash=digest(canonical(value)))
    # No new database transaction is required to retain a received fragment.
    # Account/book tombstones fence the write before and after atomic replace.
    _write_task_journal(ctx['owner'], ctx['taskId'], ctx['name'], canonical(envelope).encode())
    ctx.update(text=text, title=value['title'])
    try:
        mirror(ctx, value, final=final)
    except SQLAlchemyError:
        # A complete provider result must still reach its ordinary result
        # journal; failure of this optional mirror cannot throw it away.
        return False
    return True

def mirror(ctx, value, *, final=False):
    from ..book_deletion import lock_task
    with SessionLocal() as db:
        task = lock_task(db, ctx['taskId'])
        if not task:
            raise LeaseLost()
        received_guard(db, task)
        batch = db.scalar(select(GenerationBatch).where(GenerationBatch.id == ctx['generationId'],
            GenerationBatch.owner_id == ctx['owner']).with_for_update())
        row = db.scalar(select(GenerationChapterVersion).where(
            GenerationChapterVersion.generation_id == ctx['generationId'],
            GenerationChapterVersion.candidate_version == ctx['candidateVersion'],
            GenerationChapterVersion.chapter_index == ctx['chapterIndex']).with_for_update())
        if not batch or not row or row.content_id != ctx['contentId'] or batch.candidate_version != ctx['candidateVersion']:
            raise LeaseLost()
        if row.chapter_index <= batch.accepted_prefix:
            return
        if value['snapshotVersion'] <= row.snapshot_version:
            return
        old_text, old_epoch = row.text, row.stream_version
        row.title, row.text = value['title'], value['text']
        row.stream_version, row.snapshot_version = value['streamVersion'], value['snapshotVersion']
        row.completeness, row.hints = value['completeness'], deepcopy(value['hints'])
        row.content_hash = value['contentHash'] if final else None
        row.durability = 'saved' if final else 'journaled'
        if value.get('factDelta') is None and row.text != old_text:
            ledger = deepcopy(task.checkpoint.get('proseFactLedger', {}))
            if str(row.chapter_index) in ledger.get(str(ctx['candidateVersion']), {}):
                ledger[str(ctx['candidateVersion'])].pop(str(row.chapter_index))
                task.checkpoint = {**task.checkpoint, 'proseFactLedger':ledger}
        if value.get('eventLedger') is None and row.text != old_text:
            ledger = deepcopy(task.checkpoint.get('proseEventLedger', {}))
            if str(row.chapter_index) in ledger.get(str(ctx['candidateVersion']), {}):
                ledger[str(ctx['candidateVersion'])].pop(str(row.chapter_index))
                task.checkpoint = {**task.checkpoint, 'proseEventLedger':ledger}
        if value.get('rawPresentation') is not None:
            ledger = deepcopy(task.checkpoint.get('prosePresentationLedger', {}))
            ledger.setdefault(str(ctx['candidateVersion']), {})[str(row.chapter_index)] = validate_record(value['rawPresentation'], row.text, row.title)
            task.checkpoint = {**task.checkpoint, 'prosePresentationLedger': ledger}
        elif str(row.chapter_index) in task.checkpoint.get('prosePresentationLedger', {}).get(str(ctx['candidateVersion']), {}):
            # Legacy/internal text replacements have no received raw source.
            # Never bind an older receipt to different reading bytes.
            ledger = deepcopy(task.checkpoint['prosePresentationLedger'])
            ledger[str(ctx['candidateVersion'])].pop(str(row.chapter_index))
            task.checkpoint = {**task.checkpoint, 'prosePresentationLedger': ledger}
        payload = {'candidateVersion': ctx['candidateVersion'], 'contentId': row.content_id,
            'chapterIndex': row.chapter_index, 'streamVersion': row.stream_version}
        if row.stream_version == old_epoch and row.text.startswith(old_text) and row.text != old_text:
            service.event(db, task, 'text_delta', {**payload, 'startUtf16': utf16_len(old_text),
                'delta': row.text[len(old_text):], 'durability': 'journaled'})
        elif row.text != old_text or row.stream_version != old_epoch:
            service.event(db, task, 'phase', {**payload, 'phase': 'snapshot_replaced'})
        if final:
            if value.get('eventLedger') is not None:
                ledger = deepcopy(task.checkpoint.get('proseEventLedger', {}))
                ledger.setdefault(str(ctx['candidateVersion']), {})[str(row.chapter_index)] = validate_events(value['eventLedger'], row.text)
                task.checkpoint = {**task.checkpoint, 'proseEventLedger':ledger}
            if value.get('factDelta') is not None:
                ledger=deepcopy(task.checkpoint.get('proseFactLedger',{}));ledger.setdefault(str(ctx['candidateVersion']),{})[str(row.chapter_index)]=validate_delta(value['factDelta'],row.text);task.checkpoint={**task.checkpoint,'proseFactLedger':ledger}
            for hint in row.hints:
                service.event(db, task, 'content_hint', {**payload, 'code': hint['code']})
            service.event(db, task, 'chapter_saved', {**payload, 'snapshotVersion': row.snapshot_version,
                'completeness': row.completeness, 'durability': 'saved'})
            task.result = {**task.result, 'completedChapters': sum(r.completeness == 'complete'
                and r.durability == 'saved' for r in service.chapters(db, batch))}
        db.commit()

def overlay(batch, task, row):
    """Owner-only snapshot fallback for bytes received while its mirror failed."""
    ctx = {'owner': batch.owner_id, 'taskId': task.id, 'generationId': batch.id,
        'candidateVersion': row.candidate_version, 'chapterIndex': row.chapter_index,
        'contentId': row.content_id, 'name': service.stream_name(task.id, row.candidate_version, row.chapter_index)}
    value = read(ctx)
    if not value or value['snapshotVersion'] <= row.snapshot_version:
        return None
    return {**value, 'durability': 'journaled', 'canAccept': False}
