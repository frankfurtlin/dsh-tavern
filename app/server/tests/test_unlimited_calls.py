"""Unlimited account calls preserve usage and finite task/token authorization."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.errors import Problem
from app.models import ModelProfile, Task, UsageRecord
from app.model_budget import call_budget_exceeded, remaining_calls
from app.world.jobs import create_job
from .helpers import BASE, submit


@pytest.mark.parametrize('limit,used,additional,exceeded,remaining', [
    (0, 217, 100, False, 100),
    (0, 2147483646, 1, False, 100),
    (217, 216, 1, False, 1),
    (217, 217, 1, True, 0),
])
def test_unlimited_and_finite_calls(limit, used, additional, exceeded, remaining):
    profile = SimpleNamespace(call_budget=limit, calls_reserved=used)
    assert call_budget_exceeded(profile, additional) is exceeded
    assert remaining_calls(profile, 100) == remaining


def enable_unlimited(client, profile):
    with SessionLocal() as db:
        p = db.get(ModelProfile, profile['id'])
        p.calls_reserved = 217
        db.commit()
    response = client.patch(BASE + '/model-profiles/' + profile['id'], json={
        'expectedVersion': profile['version'], 'callBudget': 0})
    assert response.status_code == 200, response.text
    return response.json()


def test_patch_unlimited_preserves_usage_tokens_and_can_restore_limit(client, reader, profile):
    result = enable_unlimited(client, profile)
    assert result['callBudget'] == 0 and result['callsReserved'] == 217
    for key in ('inputBudget', 'outputBudget', 'inputReserved', 'outputReserved'):
        assert result[key] == profile[key]
    negative = client.patch(BASE + '/model-profiles/' + profile['id'], json={
        'expectedVersion': result['version'], 'callBudget': -1})
    assert negative.status_code == 422
    restored = client.patch(BASE + '/model-profiles/' + profile['id'], json={
        'expectedVersion': result['version'], 'callBudget': 300})
    assert restored.status_code == 200
    assert restored.json()['callBudget'] == 300 and restored.json()['callsReserved'] == 217


def test_reader_worker_can_call_after_old_limit_and_still_records_usage(client, reader, profile):
    profile = enable_unlimited(client, profile)
    result, _, _ = submit(client, profile, {'operation': 'connection_test', 'profileId': profile['id']})
    assert result['state'] == 'succeeded', result
    with SessionLocal() as db:
        assert db.get(ModelProfile, profile['id']).calls_reserved == 218
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 1


def test_world_unlimited_does_not_remove_task_or_token_limits(client, reader, profile):
    enable_unlimited(client, profile)
    with SessionLocal() as db:
        p = db.get(ModelProfile, profile['id'])
        task = create_job(db, p.owner_id, 'world_scene_binding', {}, ['analysis'], max_calls=1)
        assert task.id
        with pytest.raises(Problem) as limited:
            create_job(db, p.owner_id, 'world_scene_binding', {}, ['analysis', 'analysis'], max_calls=1)
        assert limited.value.code == 'BUDGET_NOT_AUTHORIZED'
        p.input_budget = 1
        with pytest.raises(Problem) as tokens:
            create_job(db, p.owner_id, 'world_scene_binding', {}, ['analysis'], max_calls=1)
        assert tokens.value.code == 'BUDGET_NOT_AUTHORIZED'


def test_unlimited_story_flow_remains_finite(client, reader, demo, profile):
    from .test_story_flow import start, finish
    profile = enable_unlimited(client, profile)
    run, _ = start(client, reader, demo, profile, count=1)
    finish(run)
    result = client.get(BASE + '/story-runs/' + run['id']).json()
    assert result['state'] == 'succeeded', result
    assert result['completedChapters'] == result['requestedChapters'] == 1
    with SessionLocal() as db:
        assert db.get(ModelProfile, profile['id']).calls_reserved > 217
        assert db.scalar(select(func.count()).select_from(UsageRecord)) < 100
