"""Portable-equivalent owned PostgreSQL rows; no provider or journal access."""
from copy import deepcopy
from datetime import timedelta
import json
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import (User, Task, TaskEvent, GenerationBatch, GenerationChapterVersion,
    GenerationAcceptance, UsageRecord, ModelProfile, Consent, Revision, Branch, uid, now)
from app.domain import digest
from .helpers import BASE, read_chapter, observe
from .test_prose_generations import isolated_prose


META = {'targetBranchId': None, 'lastAcceptedRevisionId': None, 'eventCursor': 41,
    'state': 'failed', 'phase': 'stopped-with-draft', 'providerState': 'completed',
    'persistenceState': 'saved', 'usageState': 'known', 'cancelRequested': True,
    'errorCode': 'CHAPTER_INCOMPLETE'}
OLD_META = {**META, 'eventCursor': 16, 'state': 'result-unknown', 'phase': 'historical-transport-unknown',
    'providerState': 'unknown', 'usageState': 'unknown', 'errorCode': 'PROVIDER_STATUS_UNKNOWN'}


def imported(client, reader, demo, *, metadata=True):
    anchor = read_chapter(client, demo)['blocks'][1]['endAnchor']; observe(client, reader, anchor)
    task_id, batch_id, historical_profile = uid(), uid(), uid()
    request = {'protocol': 'reader-prose-v1', 'originAnchor': anchor, 'operation': 'counterfactual',
        'chapterCount': 2, 'chapterLength': 'short', 'instruction': '合成历史草稿。',
        'profileId': historical_profile, 'expectedHeadRevisionId': anchor['revisionId'],
        'target': {'mode': 'new_child_branch', 'name': '历史草稿的正文副本'}}
    with SessionLocal() as db:
        owner = db.scalar(select(User.id).where(User.username == 'reader_one'))
        checkpoint = {'portableHistoricalCurrent': deepcopy(META),
            'proseVersionStates': {'1': deepcopy(OLD_META)}} if metadata else {}
        task = Task(id=task_id, owner_id=owner, task_type='story_flow',
            payload={'protocol': 'reader-prose-v1', 'bookId': demo['bookId'], 'anchor': anchor,
                'operation': 'counterfactual', 'importedHistory': True}, checkpoint=checkpoint,
            result={'importedHistory': True, 'phase': 'restored-terminal'}, state='succeeded',
            persistence_state='saved', provider_state='not_started', usage_state='unknown',
            result_outcome='completed', event_seq=7, lease_owner=None, lease_expires_at=None)
        batch = GenerationBatch(id=batch_id, owner_id=owner, task_id=task_id, book_id=demo['bookId'],
            origin_anchor=anchor, request=request, profile_snapshot={'id': historical_profile,
                'name': '历史模型显示项', 'model_id': 'old-model', 'version': 3, 'historical': True},
            candidate_version=2, accepted_prefix=0, version_times={
                '1': (now()-timedelta(days=2)).isoformat(), '2': (now()-timedelta(days=1)).isoformat()})
        db.add(task); db.flush(); db.add(batch); db.flush()
        db.add(TaskEvent(task_id=task_id, seq=7, type='phase',
            payload={'candidateVersion': 2, 'phase': 'historical-record', 'marker': 'keep-original-event'}))
        for version in (1, 2):
            for index in (1, 2):
                text = '这份合成草稿记录了渡口的线索，角色当场核对后离开。\n\n' * 40 if index == 1 else '这是保留的未完第二章。'
                db.add(GenerationChapterVersion(owner_id=owner, generation_id=batch_id,
                    candidate_version=version, chapter_index=index, content_id=uid(),
                    title=f'历史{version}章{index}', text=text, content_hash=digest(text),
                    stream_version=2, snapshot_version=4,
                    completeness='complete' if index == 1 else 'partial', durability='saved', hints=[]))
        db.commit()
    return {'generationId': batch_id, 'taskId': task_id}, request


def snapshot(client, receipt, **params):
    response = client.get(BASE + '/generations/' + receipt['generationId'], params=params)
    assert response.status_code == 200, response.text
    return response.json()


def original_rows(receipt):
    with SessionLocal() as db:
        task = db.get(Task, receipt['taskId']); batch = db.get(GenerationBatch, receipt['generationId'])
        return {'task': {'state': task.state, 'createdAt': task.created_at.isoformat(),
            'errorCode': task.error_code, 'resultOutcome': task.result_outcome,
            'payload': deepcopy(task.payload), 'checkpoint': deepcopy(task.checkpoint),
            'result': deepcopy(task.result), 'eventSeq': task.event_seq, 'cancelRequested': task.cancel_requested,
            'providerState': task.provider_state, 'persistenceState': task.persistence_state,
            'usageState': task.usage_state, 'leaseOwner': task.lease_owner},
            'request': deepcopy(batch.request), 'candidateVersion': batch.candidate_version,
            'chapters': [(c.id, c.candidate_version, c.chapter_index, c.title, c.text, c.content_hash)
                for c in db.scalars(select(GenerationChapterVersion).where(
                    GenerationChapterVersion.generation_id == batch.id).order_by(GenerationChapterVersion.id))],
            'taskEvents': db.scalar(select(func.count()).select_from(TaskEvent)),
            'eventRows': [(event.id, event.seq, event.type, deepcopy(event.payload), event.created_at.isoformat())
                for event in db.scalars(select(TaskEvent).order_by(TaskEvent.id))],
            'usage': db.scalar(select(func.count()).select_from(UsageRecord)),
            'consent': db.scalar(select(func.count()).select_from(Consent)),
            'profiles': db.scalar(select(func.count()).select_from(ModelProfile))}


def test_imported_current_and_old_metadata_are_read_only_without_any_overlay(client, reader, demo, monkeypatch):
    receipt, _ = imported(client, reader, demo)
    before = original_rows(receipt)
    def forbidden(*args, **kwargs):
        raise AssertionError('Imported history must never read a stream journal')
    monkeypatch.setattr('app.prose.stream.overlay', forbidden)
    current = snapshot(client, receipt); old = snapshot(client, receipt, candidateVersion=1)
    assert current['importedHistory'] and current['readOnly']
    assert old['importedHistory'] and old['readOnly']
    assert {key: current[key] for key in META} == META
    assert {key: old[key] for key in OLD_META} == OLD_META
    assert current['chapters'][0]['canAccept'] and not current['chapters'][1]['canAccept']
    assert all(not chapter['canAccept'] for chapter in old['chapters'])
    assert 'profileSnapshot' not in current and 'key_cipher' not in json.dumps(current)
    assert original_rows(receipt) == before


@pytest.mark.parametrize('operation', ['save', 'rewrite'])
def test_imported_save_and_rewrite_reject_before_profile_or_authorization(client, reader, demo, monkeypatch, operation):
    receipt, _ = imported(client, reader, demo); before = original_rows(receipt)
    def forbidden(*args, **kwargs):
        raise AssertionError('Historical display profile cannot reserve authorization')
    monkeypatch.setattr('app.prose.service.reserve_authorization', forbidden)
    data = {'candidateVersion': 2, 'expectedAcceptedPrefix': 0, 'instruction': '使用当前模型另开。'} if operation == 'rewrite' else {}
    response = client.post(BASE + '/generations/' + receipt['generationId'] + '/' + operation,
        json=data, headers={'Idempotency-Key': 'history-' + operation})
    assert response.status_code == 409 and response.json()['code'] == 'IMPORTED_HISTORY_READ_ONLY', response.text
    assert original_rows(receipt) == before


def test_imported_missing_metadata_is_historical_unknown_not_completed(client, reader, demo):
    receipt, _ = imported(client, reader, demo, metadata=False); before = original_rows(receipt)
    for params in ({}, {'candidateVersion': 1}):
        view = snapshot(client, receipt, **params)
        assert view['state'] == 'historical-unknown' and view['phase'] == 'historical-unknown', view
        assert view['importedHistory'] and view['readOnly']
        assert view['errorCode'] is None and view['eventCursor'] == 0
        assert view['providerState'] == view['persistenceState'] == view['usageState'] == 'unknown'
    assert original_rows(receipt) == before


def test_accept_imported_saved_prefix_copies_content_without_touching_history_task(client, reader, demo, monkeypatch):
    receipt, request = imported(client, reader, demo); before = original_rows(receipt)
    def forbidden(*args, **kwargs):
        raise AssertionError('Imported complete draft acceptance cannot call or recover a provider')
    monkeypatch.setattr('app.worker.call_provider', forbidden)
    with SessionLocal() as db:
        source_head = db.get(Branch, demo['branchId']).head_revision_id
        revisions = db.scalar(select(func.count()).select_from(Revision))
    body = {'candidateVersion': 2, 'prefixCount': 1, 'expectedAcceptedPrefix': 0,
        'expectedHeadRevisionId': request['expectedHeadRevisionId'], 'target': 'child', 'sourcePolicy': 'requireUnchanged'}
    response = client.post(BASE + '/generations/' + receipt['generationId'] + '/accept', json=body,
        headers={'Idempotency-Key': 'copy-imported-prefix'})
    assert response.status_code == 200, response.text
    repeated = client.post(BASE + '/generations/' + receipt['generationId'] + '/accept', json=body,
        headers={'Idempotency-Key': 'copy-imported-prefix'})
    assert repeated.status_code == 200 and repeated.json() == response.json()
    after = original_rows(receipt)
    assert after == before
    with SessionLocal() as db:
        assert db.get(Branch, demo['branchId']).head_revision_id == source_head
        assert db.get(GenerationBatch, receipt['generationId']).accepted_prefix == 1
        assert db.scalar(select(func.count()).select_from(GenerationAcceptance)) == 1
        assert db.scalar(select(func.count()).select_from(Revision)) == revisions + 1
        revision = db.get(Revision, response.json()['revisionId'])
        assert revision.provenance['profileId'] == request['profileId']
        assert revision.provenance['profileVersion'] == 3
        assert response.json()['branchId'] != demo['branchId']
