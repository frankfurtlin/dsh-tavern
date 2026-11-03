"""Draft preservation, finite calls and explicit reader acceptance for advisory reviews."""
from unittest.mock import patch
from sqlalchemy import select, func
from app.db import SessionLocal
from app.errors import Problem
from app.models import Task, UsageRecord, Candidate, Revision
from app.providers import MockAdapter
from .helpers import BASE, accept
from .test_story_flow import start, finish


def test_review_transport_failure_retains_complete_prose_without_resending(client, reader, demo, profile):
    run, _ = start(client, reader, demo, profile, count=1)
    original = MockAdapter.generate
    def unavailable(adapter, p, prompt, data, limit):
        if prompt.startswith('validation-'):
            raise Problem('PROVIDER_STATUS_UNKNOWN', 'PRIVATE-ERROR-MESSAGE')
        return original(adapter, p, prompt, data, limit)
    with patch.object(MockAdapter, 'generate', unavailable):
        finish(run)
    result = client.get(BASE+'/story-runs/'+run['id']).json()
    assert result['state'] == 'succeeded' and result['errorCode'] is None
    candidate = client.get(BASE+'/candidates/'+result['candidateId']).json()
    assert candidate['text'] and candidate['canAccept']
    assert candidate['reviewWarnings'] == [{'code': 'CHECK_UNAVAILABLE', 'chapterIndex': 1}]
    with SessionLocal() as db:
        task = db.get(Task, result['currentTaskId'])
        assert task.provider_state == 'unknown'
        before = db.scalar(select(func.count()).select_from(UsageRecord))
    with patch.object(MockAdapter, 'generate', side_effect=AssertionError('must not resend')):
        finish(run)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before
    response = accept(client, {'result': {'candidateId': candidate['id']}}, key='retain-reviewed-prose')
    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.get(Revision, response.json()['revisionId']).provenance['reviewWarnings'] == candidate['reviewWarnings']
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before


def test_unknown_review_preserves_first_draft_and_stops_remaining_model_calls(client, reader, demo, profile):
    run, _ = start(client, reader, demo, profile, count=3)
    original = MockAdapter.generate
    prompts = []
    def unavailable(adapter, p, prompt, data, limit):
        prompts.append(prompt.splitlines()[0])
        if prompt.startswith('validation-'):
            raise Problem('PROVIDER_STATUS_UNKNOWN', 'PRIVATE-ERROR-MESSAGE')
        return original(adapter, p, prompt, data, limit)
    with patch.object(MockAdapter, 'generate', unavailable):
        finish(run)
    result = client.get(BASE+'/story-runs/'+run['id']).json()
    candidate = client.get(BASE+'/candidates/'+result['candidateId']).json()
    assert result['state'] == 'failed' and result['completedChapters'] == 1
    assert candidate['text'] and candidate['partial'] and not candidate['canAccept']
    assert candidate['reviewWarnings'] == [{'code': 'CHECK_UNAVAILABLE', 'chapterIndex': 1}]
    assert sum('chapter-' in p for p in prompts) == 1 and sum('validation-' in p for p in prompts) == 1
    with patch.object(MockAdapter, 'generate', side_effect=AssertionError('must not resend')):
        finish(run)


def test_short_draft_is_visible_owned_and_cannot_pretend_to_be_complete(client, reader, demo, profile):
    run, _ = start(client, reader, demo, profile, count=1)
    original = MockAdapter.generate
    def short(adapter, p, prompt, data, limit):
        result = original(adapter, p, prompt, data, limit)
        if prompt.startswith('counterfactual-'):
            result['text'] = '<script>alert("draft")</script>故事未完。'
        return result
    with patch.object(MockAdapter, 'generate', short):
        finish(run)
    result = client.get(BASE+'/story-runs/'+run['id']).json()
    candidate = client.get(BASE+'/candidates/'+result['candidateId']).json()
    assert candidate['text'] == '<script>alert("draft")</script>故事未完。' and not candidate['canAccept']
    assert accept(client, {'result': {'candidateId': candidate['id']}}, key='short-draft').status_code == 422
    client.post(BASE+'/auth/register', json={'username':'another_draft_reader','password':'reader-password-123'})
    login = client.post(BASE+'/auth/login', json={'username':'another_draft_reader','password':'reader-password-123'}).json()
    client.headers['Authorization'] = 'Bearer '+login['accessToken']
    assert client.get(BASE+'/candidates/'+candidate['id']).status_code == 404


def test_old_blocked_candidate_is_previewable_but_requires_trusted_complete_checkpoint(client, reader, demo, profile):
    run, _ = start(client, reader, demo, profile, count=1)
    finish(run)
    result = client.get(BASE+'/story-runs/'+run['id']).json()
    with SessionLocal() as db:
        candidate = db.get(Candidate, result['candidateId'])
        task = db.get(Task, candidate.task_id)
        candidate.outcome, candidate.validation_status = 'blocked', 'blocked'
        task.checkpoint = {key: value for key, value in task.checkpoint.items() if key not in {'generationReviewPolicy', 'reviewWarnings'}}
        db.commit()
    saved = client.get(BASE+'/candidates/'+result['candidateId']).json()
    assert saved['text'] and not saved['canAccept']
    assert accept(client, {'result': {'candidateId': saved['id']}}, key='untrusted-old-candidate').status_code == 422


def test_review_interruption_remains_stopped_after_candidate_persistence_failure(client, reader, demo, profile):
    import app.worker as worker
    from app.worker import process
    run, _ = start(client, reader, demo, profile, count=3)
    process(run['id'])
    with SessionLocal() as db:
        child_id = db.get(Task, run['id']).checkpoint['currentTaskId']
    original, original_event = MockAdapter.generate, worker.add_event
    def unavailable(adapter, p, prompt, data, limit):
        if prompt.startswith('validation-'):
            raise Problem('PROVIDER_STATUS_UNKNOWN', 'PRIVATE-ERROR-MESSAGE')
        return original(adapter, p, prompt, data, limit)
    def crash(db, task, kind, payload):
        if kind == 'candidate_saved':
            raise RuntimeError('simulated persistence failure after chapter checkpoint')
        return original_event(db, task, kind, payload)
    with patch.object(MockAdapter, 'generate', unavailable), patch.object(worker, 'add_event', crash):
        process(child_id)
    with SessionLocal() as db:
        task = db.get(Task, child_id)
        assert task.persistence_state == 'recoverable' and task.checkpoint['reviewInterrupted']
        before = db.scalar(select(func.count()).select_from(UsageRecord))
    with patch.object(MockAdapter, 'generate', side_effect=AssertionError('must not issue any more calls')):
        process(child_id)
        process(run['id'])
    result = client.get(BASE+'/story-runs/'+run['id']).json()
    candidate = client.get(BASE+'/candidates/'+result['candidateId']).json()
    assert candidate['text'] and candidate['partial'] and not candidate['canAccept']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before
