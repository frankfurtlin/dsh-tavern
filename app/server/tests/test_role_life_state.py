"""Unknown or malformed life states cannot unlock new or previously queued turns."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db import SessionLocal
from app.knowledge import visible_characters
from app.models import KnowledgeSnapshot, RoleMessage, UsageRecord
from app.schemas import FactOutput
from app.worker import process
from .helpers import BASE, observe, read_chapter
from .test_reader import analyzed_session


def test_life_state_requires_reliable_boolean_at_fixed_time():
    cases = [
        (False, 'confirmed', 'at_scope', False, 'ROLE_DECEASED'),
        (True, 'confirmed', 'at_scope', True, None),
        (False, 'confirmed', 'future', True, None),
        ('false', 'confirmed', 'at_scope', False, 'ROLE_STATE_UNCERTAIN'),
        (0, 'confirmed', 'at_scope', False, 'ROLE_STATE_UNCERTAIN'),
        (None, 'confirmed', 'at_scope', False, 'ROLE_STATE_UNCERTAIN'),
        (False, 'uncertain', 'at_scope', False, 'ROLE_STATE_UNCERTAIN'),
        (False, 'uncertain', 'past', False, 'ROLE_STATE_UNCERTAIN'),
        (True, 'confirmed', 'unknown', False, 'ROLE_STATE_UNCERTAIN'),
    ]
    for value, status, temporal, available, reason in cases:
        snapshot = SimpleNamespace(data={'characters': [{'id': 'c1', 'displayName': '阿岑'}],
            'facts': [{'subjectId': 'c1', 'predicate': 'alive', 'value': value,
                       'status': status, 'temporalRelation': temporal}]})
        character = visible_characters(snapshot)[0]
        assert (character['available'], character['reasonCode']) == (available, reason)
    snapshot.data['facts'] = []
    assert visible_characters(snapshot)[0]['available'] is True


def test_explicit_alive_wire_rejects_boolean_substitutes():
    fact = {'i': 'f1', 's': 'c1', 'p': 'alive', 'v': False, 'c': 'explicit', 'e': ['e1']}
    assert FactOutput.model_validate(fact).value is False
    for value in ['false', 'true', 0, 1, None]:
        with pytest.raises(ValidationError):
            FactOutput.model_validate({**fact, 'v': value})
    assert FactOutput.model_validate({**fact, 'c': 'uncertain', 'v': None}).value is None
    assert FactOutput.model_validate({**fact, 'p': 'action', 'v': 'false'}).value == 'false'


def test_old_invalid_snapshot_blocks_session_plan_and_queued_turn_without_call(client, reader, demo, profile):
    session, characters, anchor = analyzed_session(client, reader, demo, profile, name='阿岑')
    assert session.status_code == 201
    sid = session.json()['id']
    payload = {'operation': 'role_chat', 'profileId': profile['id'], 'sessionId': sid,
               'message': '现在你准备做什么？', 'turnId': 'queued-before-state-check'}
    plan = client.post(BASE + '/operation-plans', json=payload).json()
    consent = client.post(BASE + '/consents', json={'planId': plan['planId'], 'planHash': plan['planHash'],
        'profileId': profile['id'], 'maxCalls': plan['maxCalls'], 'inputTokens': plan['inputTokens'],
        'outputTokens': plan['outputTokens']}).json()
    submitted = client.post(BASE + '/role-sessions/' + sid + '/turns',
        json={**payload, 'consentId': consent['consentId']}, headers={'Idempotency-Key': 'life-state-queued'})
    assert submitted.status_code == 202
    with SessionLocal() as db:
        snapshot = db.get(KnowledgeSnapshot, characters['snapshotId'])
        original = deepcopy(snapshot.data)
        cid = session.json()['characterId']
        snapshot.data = {**original, 'facts': original['facts'] + [{'subjectId': cid, 'predicate': 'alive',
            'value': 'false', 'status': 'confirmed', 'temporalRelation': 'at_scope'}]}
        before = db.scalar(select(func.count()).select_from(UsageRecord))
        db.commit()
    refused = client.post(BASE + '/operation-plans', json={**payload, 'turnId': 'new-after-state-check'})
    assert refused.status_code == 422 and refused.json()['code'] == 'ROLE_UNAVAILABLE'
    refused = client.post(BASE + '/role-sessions', json={'bookId': demo['bookId'], 'characterId': cid,
        'snapshotId': characters['snapshotId'], 'anchor': anchor})
    assert refused.status_code == 422 and refused.json()['code'] == 'ROLE_UNAVAILABLE'
    process(submitted.json()['taskId'])
    task = client.get(BASE + '/tasks/' + submitted.json()['taskId']).json()
    assert task['state'] == 'failed' and task['errorCode'] == 'ROLE_UNAVAILABLE'
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before
        assert db.scalar(select(func.count()).select_from(RoleMessage)) == 0
        snapshot = db.get(KnowledgeSnapshot, characters['snapshotId'])
        snapshot.data = original
        db.commit()
    future = read_chapter(client, demo, 1)['blocks'][-1]['endAnchor']
    observe(client, reader, future, expected=1, sequence=2)
    allowed = client.post(BASE + '/operation-plans', json={**payload, 'turnId': 'legal-past-session'})
    assert allowed.status_code == 201, allowed.text
