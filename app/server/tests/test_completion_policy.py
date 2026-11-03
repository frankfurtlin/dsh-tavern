"""Frozen completion policy keeps authorization, private reasoning and safety gates intact.

HTTP transport is a local mock; these tests are not real MiMo quality evidence.
"""
from copy import deepcopy
import json
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import func, select
from app.config import settings
from app.db import SessionLocal
from app.models import ModelProfile, OperationPlan, RoleSession, Task, UsageRecord
from app.providers import MockAdapter
from app.worker import call_provider, process
from .helpers import BASE, observe, read_chapter, submit
from .test_reader import analyzed_session
from .test_regressions import queue_operation

PRIVATE_REASONING = 'PRIVATE-THINKING-NOT-FOR-JOURNAL-OR-USER'


def mimo_profile(profile):
    with SessionLocal() as db:
        row = db.get(ModelProfile, profile['id'])
        row.base_url = 'https://opencode.ai/zen/go/v1'
        row.model_id = 'mimo-v2.6-flash'
        row.supports_json = True
        row.version += 1
        db.commit()


def simulated_http(monkeypatch, profile, *, unknown=False, change=None):
    calls = []
    factory = httpx.AsyncClient
    with SessionLocal() as db:
        configured = db.get(ModelProfile, profile['id'])
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        from app.context_wire import decode_context
        data = decode_context(json.loads(body['messages'][1]['content']))
        prompt = body['messages'][0]['content']
        limit = body.get('max_tokens', body.get('max_completion_tokens'))
        result = MockAdapter().generate(configured, prompt, data, limit)
        if change:
            change(prompt, data, result)
        response = {'choices': [{'message': {'content': result['text'], 'reasoning_content': PRIVATE_REASONING},
                                 'finish_reason': result['finishReason']}]}
        if not unknown:
            response['usage'] = {'prompt_tokens': 7, 'completion_tokens': 73,
                                 'completion_tokens_details': {'reasoning_tokens': 41}}
        if body.get('stream'):
            frames = [{'choices': [{'delta': {'reasoning_content': PRIVATE_REASONING}, 'finish_reason': None}]},
                      {'choices': [{'delta': {'content': result['text']}, 'finish_reason': result['finishReason']}]}]
            if 'usage' in response:
                frames.append({'choices': [], 'usage': response['usage']})
            wire = ''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames) + 'data: [DONE]\n\n'
            return httpx.Response(200, content=wire, headers={'content-type': 'text/event-stream'})
        return httpx.Response(200, json=response)
    monkeypatch.setattr(settings, 'ai_mode', 'real')
    monkeypatch.setattr('app.providers.validate_url', lambda _: ('opencode.ai', '93.184.216.34'))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs))
    return calls


def generation_payload(client, reader, demo, profile):
    anchor = read_chapter(client, demo)['blocks'][1]['endAnchor']
    observe(client, reader, anchor)
    return {'operation': 'counterfactual', 'profileId': profile['id'], 'anchor': anchor,
            'expectedHeadRevisionId': anchor['revisionId'], 'instruction': '留下救人'}


def approve(client, profile, plan):
    return client.post(BASE + '/consents', json={'planId': plan['planId'], 'planHash': plan['planHash'],
        'profileId': profile['id'], 'maxCalls': plan['maxCalls'],
        'inputTokens': plan['inputTokens'], 'outputTokens': plan['outputTokens']})


def test_mimo_total_completion_plan_is_frozen_and_actual_http_keeps_reasoning_private(client, reader, demo, profile, monkeypatch):
    mimo_profile(profile)
    payload = generation_payload(client, reader, demo, profile)
    planned = client.post(BASE + '/operation-plans', json=payload).json()
    assert planned['outputTokens'] == (planned['batchAnalysisBlocks'] + 2) * 2048
    assert planned['inputTokens'] == planned['maxCalls'] * 12000
    assert planned['completionPolicy']['stages']['analysis'] == {'totalCompletionTokens': 2048, 'thinking': 'disabled'}
    approved = approve(client, profile, planned)
    assert approved.status_code == 201
    result = client.post(BASE + '/generation-tasks', json={**payload, 'consentId': approved.json()['consentId']},
                         headers={'Idempotency-Key': uuid4().hex})
    assert result.status_code == 202, result.text
    task_id = result.json()['taskId']
    with SessionLocal() as db:
        assert db.get(Task, task_id).checkpoint['completionPolicy'] == planned['completionPolicy']
    calls = simulated_http(monkeypatch, profile)
    process(task_id)
    task = client.get(BASE + '/tasks/' + task_id).json()
    assert task['resultOutcome'] == 'complete', task
    assert len(calls) == planned['maxCalls']
    assert all(body['thinking'] == {'type': 'disabled'} and body['max_tokens'] == 2048 for body in calls)
    assert all(body['response_format'] == {'type': 'json_object'} for body in calls if body['messages'][0]['content'].startswith(('analysis-', 'validation-')))
    diagnostics = client.get(BASE + '/tasks/' + task_id + '/diagnostics').json()
    assert all(call['outputLimit'] == 2048 and call['thinking'] == 'disabled' for call in diagnostics['parameters']['calls'])
    with SessionLocal() as db:
        usage = list(db.scalars(select(UsageRecord).where(UsageRecord.task_id == task_id)))
        assert all(row.output_tokens == 73 and row.reserved_output == 2048 for row in usage)
        assert db.get(ModelProfile, profile['id']).output_reserved == len(calls) * 73
        from app.storage import private_path
        for entry in db.get(Task, task_id).checkpoint['calls'].values():
            journal = private_path(db.get(Task, task_id).owner_id, f"journal-{task_id}-{entry['callId']}.json")
            if journal.exists():
                assert PRIVATE_REASONING not in journal.read_text('utf-8')
    assert PRIVATE_REASONING not in json.dumps(task) + json.dumps(diagnostics)


@pytest.mark.parametrize('phase', ['consent', 'task', 'queued'])
def test_changed_completion_policy_is_not_silently_applied_to_previous_authorization(client, reader, demo, profile, monkeypatch, phase):
    mimo_profile(profile)
    payload = generation_payload(client, reader, demo, profile)
    planned = client.post(BASE + '/operation-plans', json=payload).json()
    if phase == 'consent':
        with SessionLocal() as db:
            row = db.get(OperationPlan, planned['planId'])
            row.details = {key: value for key, value in row.details.items() if key != 'completionPolicy'}
            db.commit()
        response = approve(client, profile, planned)
        assert response.status_code == 403 and response.json()['code'] == 'CONSENT_REQUIRED'
    elif phase == 'task':
        approved = approve(client, profile, planned).json()
        with SessionLocal() as db:
            row = db.get(OperationPlan, planned['planId'])
            policy = deepcopy(row.details['completionPolicy']); policy['version'] = 'obsolete'
            row.details = {**row.details, 'completionPolicy': policy}; db.commit()
        response = client.post(BASE + '/generation-tasks', json={**payload, 'consentId': approved['consentId']},
                               headers={'Idempotency-Key': uuid4().hex})
        assert response.status_code == 403 and response.json()['code'] == 'CONSENT_REQUIRED'
    else:
        task_id = queue_operation(client, profile, payload)
        with SessionLocal() as db:
            row = db.get(Task, task_id)
            row.checkpoint = {key: value for key, value in row.checkpoint.items() if key != 'completionPolicy'}
            db.commit()
        calls = simulated_http(monkeypatch, profile)
        process(task_id)
        assert calls == []
        task = client.get(BASE + '/tasks/' + task_id).json()
        assert task['errorCode'] == 'CONSENT_REQUIRED'
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
        assert db.get(ModelProfile, profile['id']).calls_reserved == 0


def test_connection_remains_non_thinking_32_and_small_window_is_accounted_per_stage(client, reader, demo, profile, monkeypatch):
    mimo_profile(profile)
    with SessionLocal() as db:
        db.get(ModelProfile, profile['id']).context_window = 4096; db.commit()
    planned = client.post(BASE + '/operation-plans', json={'operation': 'connection_test', 'profileId': profile['id']}).json()
    assert planned['outputTokens'] == 32 and planned['inputTokens'] == 4096 - 32 - 512
    calls = simulated_http(monkeypatch, profile)
    task, _, _ = submit(client, profile, {'operation': 'connection_test', 'profileId': profile['id']})
    assert task['state'] == 'succeeded'
    assert len(calls) == 1 and calls[0]['max_tokens'] == 32 and calls[0]['thinking'] == {'type': 'disabled'}


def test_unknown_usage_retains_total_completion_and_journal_replay_never_sends_again(client, reader, demo, profile, monkeypatch):
    mimo_profile(profile)
    payload = generation_payload(client, reader, demo, profile)
    calls = simulated_http(monkeypatch, profile, unknown=True)
    task, _, _ = submit(client, profile, payload)
    assert task['resultOutcome'] == 'complete' and task['usageState'] == 'unknown', task
    original_count = len(calls)
    with SessionLocal() as db:
        rows = list(db.scalars(select(UsageRecord).where(UsageRecord.task_id == task['id'])))
        assert all(row.output_tokens is None and row.reserved_output == 2048 for row in rows)
        assert db.get(ModelProfile, profile['id']).output_reserved == 2048 * original_count
        row = db.get(Task, task['id'])
        old = deepcopy(row.checkpoint['completionPolicy']); old['version'] = 'obsolete'
        row.checkpoint = {**row.checkpoint, 'completionPolicy': old}; db.commit()
    result = call_provider(task['id'], 'generation', 'counterfactual', {}, 2048)
    assert result['text'] and len(calls) == original_count
    with SessionLocal() as db:
        assert db.get(ModelProfile, profile['id']).output_reserved == 2048 * original_count


@pytest.mark.parametrize('failure', ['object-wrapper', 'physical-directive', 'truncated-verdict', 'unknown-evidence'])
def test_frozen_policy_role_still_refuses_bad_output_or_verdict_and_keeps_fixed_session(client, reader, demo, profile, monkeypatch, failure):
    mimo_profile(profile)
    created, _, _ = analyzed_session(client, reader, demo, profile)
    session = created.json()
    fixed = client.get(BASE + '/role-sessions/' + session['id']).json()
    with SessionLocal() as db: fixed_snapshot_id = db.get(RoleSession, session['id']).snapshot_id
    text = ('我记得眼前的灯影与码头，只把能够确认的事情慢慢理清。心里纵有担忧，也不会替谁编造未见的往事。'
            '倘若你的问题还缺依据，我会照实说清，不将猜测当作事实，也不会把外部读者当成同行的伙伴。')
    def change(prompt, data, result):
        if prompt.startswith('role_chat-'):
            result['text'] = json.dumps({'character': '林渡', 'content': text}, ensure_ascii=False) if failure == 'object-wrapper' else ('你先来帮我推船。' + text if failure == 'physical-directive' else text)
        elif prompt.startswith('validation-'):
            if failure == 'truncated-verdict': result['finishReason'] = 'length'
            elif failure == 'unknown-evidence':
                verdict = json.loads(result['text']); verdict['evidenceIds'] = ['not-in-this-scope']; result['text'] = json.dumps(verdict)
    calls = simulated_http(monkeypatch, profile, change=change)
    task, _, _ = submit(client, profile, {'operation': 'role_chat', 'profileId': profile['id'], 'sessionId': session['id'],
        'message': '你现在如何理解这件事？', 'turnId': uuid4().hex})
    assert task['result']['replySaved'] is False, task
    assert task['resultOutcome'] == ('invalid_format' if failure == 'object-wrapper' else 'blocked'), task
    assert len(calls) == (1 if failure in {'object-wrapper', 'physical-directive'} else 2)
    assert calls[0]['max_tokens'] == 1024 and calls[0]['thinking'] == {'type': 'disabled'}
    if len(calls) == 2: assert calls[1]['max_tokens'] == 2048
    assert client.get(BASE + '/role-sessions/' + session['id'] + '/messages').json()['items'] == []
    after = client.get(BASE + '/role-sessions/' + session['id']).json()
    assert after['pinnedAnchor'] == fixed['pinnedAnchor']
    with SessionLocal() as db:
        stored = db.get(RoleSession, session['id'])
        assert stored.pending_task_id is None and stored.snapshot_id == fixed_snapshot_id


def test_role_plan_adds_each_stage_input_window_and_insufficient_validation_window_sends_nothing(client, reader, demo, profile, monkeypatch):
    mimo_profile(profile)
    created, _, _ = analyzed_session(client, reader, demo, profile)
    session = created.json()
    with SessionLocal() as db:
        configured = db.get(ModelProfile, profile['id']); configured.context_window = 6000; configured.version += 1; db.commit()
        previous_usage = db.scalar(select(func.count()).select_from(UsageRecord))
    payload = {'operation': 'role_chat', 'profileId': profile['id'], 'sessionId': session['id'],
               'message': '如何理解眼前的事情？', 'turnId': uuid4().hex}
    planned = client.post(BASE + '/operation-plans', json=payload)
    assert planned.status_code == 201, planned.text
    assert planned.json()['inputTokens'] == (6000 - 1024 - 512) + (6000 - 2048 - 512)
    assert planned.json()['outputTokens'] == 1024 + 2048
    with SessionLocal() as db:
        configured = db.get(ModelProfile, profile['id']); configured.context_window = 2400; configured.version += 1; db.commit()
    calls = simulated_http(monkeypatch, profile)
    rejected = client.post(BASE + '/operation-plans', json=payload)
    assert rejected.status_code == 422 and rejected.json()['code'] == 'CONTEXT_BUDGET_EXCEEDED'
    assert calls == []
    with SessionLocal() as db: assert db.scalar(select(func.count()).select_from(UsageRecord)) == previous_usage


def test_frozen_policy_does_not_coerce_bad_analysis_subject_type_into_a_valid_snapshot(client, reader, demo, profile, monkeypatch):
    mimo_profile(profile)
    payload = generation_payload(client, reader, demo, profile)
    def bad_subject(prompt, data, result):
        if prompt.startswith('analysis-'):
            parsed = json.loads(result['text'])
            assert parsed['facts']
            parsed['facts'][0]['subjectRef'] = []
            result['text'] = json.dumps(parsed, ensure_ascii=False)
    calls = simulated_http(monkeypatch, profile, change=bad_subject)
    task, _, _ = submit(client, profile, payload)
    assert task['state'] == 'failed' and task['errorCode'] == 'ANALYSIS_INVALID', task
    assert len(calls) == 1 and calls[0]['thinking'] == {'type': 'disabled'}
    from app.models import KnowledgeSnapshot, Candidate
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(Candidate)) == 0


def test_adapter_explicit_enabled_mode_is_private_and_invalid_mode_never_sends(client, reader, profile, monkeypatch):
    """Transport-only capability check; production's frozen policy stays disabled."""
    mimo_profile(profile)
    calls = simulated_http(monkeypatch, profile)
    with SessionLocal() as db:
        configured = db.get(ModelProfile, profile['id'])
        from app.providers import ChatCompletionsAdapter
        from app.errors import Problem
        adapter = ChatCompletionsAdapter()
        result = adapter.generate(configured, 'connection-v1', {}, 128, thinking_mode='enabled')
        assert calls[0]['thinking'] == {'type': 'enabled'} and calls[0]['max_tokens'] == 128
        assert result['usage']['outputTokens'] == 73
        assert PRIVATE_REASONING not in json.dumps(result)
        with pytest.raises(Problem) as rejected:
            adapter.generate(configured, 'connection-v1', {}, 128, thinking_mode='low')
        assert rejected.value.code == 'CONSENT_REQUIRED'
    assert len(calls) == 1


def test_multicamera_reservation_fits_configured_window_without_changing_prose_cap():
    from types import SimpleNamespace
    from app.completion_policy import input_ceiling
    profile=SimpleNamespace(context_window=131072)
    assert input_ceiling(profile,16384,world=True)==96000
    assert input_ceiling(profile,16384,world=True,vision=True)==114176
    profile.context_window=32768
    assert input_ceiling(profile,16384,world=True,vision=True)==15872
