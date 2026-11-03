"""A recoverable turn retains its line; replaced legacy work may not spend again.

Only deterministic fixture accounts in the isolated reader_test database are used.
The replacement seed represents the pending pointer written by the old release;
it is not an extra API that permits bypassing the corrected concurrency contract.
"""
from copy import deepcopy
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db import SessionLocal, engine
from app.errors import Problem
from app.models import Task, UsageRecord
from app.world import worker
from app.world.models import WorldLine, WorldTurn
from .test_world import API, fast_fixture_stream, post, start


@pytest.fixture(autouse=True)
def isolated_mock_only():
    assert settings.ai_mode == "mock"
    assert engine.url.database == "reader_test"


def usage(task_id):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id))


def request_turn(client, world_id, line, kind, key):
    root = API + f"/worlds/{world_id}/lines/{line['id']}"
    if kind == "action":
        path = root + "/actions"
        body = {"message": "我问眼前的人，这封信将送到哪里？", "kind": "dialogue",
                "expectedCheckpointId": line["headCheckpointId"], "turnId": key}
    else:
        path = root + "/write"
        body = {"instruction": "继续当前故事并写出一章。", "chapterCount": 1,
                "expectedCheckpointId": line["headCheckpointId"]}
    return client.post(path, json=body, headers={"Idempotency-Key": key})


def recoverable_turn(client, world, line, kind):
    if kind == "rewrite":
        job = post(client, f"/worlds/{world['id']}/rewrite",
                   {"checkpointId": line["headCheckpointId"], "instruction": "另一种表达"}, "recoverable-old")
        line = client.get(API + f"/worlds/{world['id']}/lines/{job['lineId']}").json()
    else:
        response = request_turn(client, world["id"], line, kind, "recoverable-old")
        assert response.status_code == 202, response.text
        job = response.json()
    with patch.object(worker, "save_visibility", side_effect=OperationalError("visibility", {}, Exception("synthetic disconnect"))):
        worker.process(job["taskId"])
    with SessionLocal() as db:
        task = db.get(Task, job["taskId"])
        assert task.state == "failed" and task.persistence_state == "recoverable"
        saved = db.get(WorldLine, job["lineId"])
        assert saved.pending_task_id == task.id and saved.head_checkpoint_id == line["headCheckpointId"]
    assert usage(job["taskId"]) == (0 if kind == "rewrite" else 1)
    return job, line


def replace_legacy_pending(client, world, line, old_job, next_kind):
    # Simulate the legacy release losing the failed/recoverable pending slot.
    # B itself is queued through the actual API with an independent turn/key.
    with SessionLocal() as db:
        saved = db.get(WorldLine, line["id"])
        assert saved.pending_task_id == old_job["taskId"]
        saved.pending_task_id = None
        db.commit()
    response = request_turn(client, world["id"], line, next_kind, "replacement")
    assert response.status_code == 202, response.text
    return response.json()


@pytest.mark.parametrize("old_kind", ["action", "write"])
@pytest.mark.parametrize("next_kind", ["action", "write"])
def test_recoverable_pending_rejects_second_turn_then_resumes_original(client, reader, demo, profile, old_kind, next_kind):
    world, line = start(client, reader, demo, profile)
    old, line = recoverable_turn(client, world, line, old_kind)
    with SessionLocal() as db:
        before_tasks = db.scalar(select(func.count()).select_from(Task))
        before_plan = deepcopy(db.get(WorldTurn, old["turnId"]).plan)
    response = request_turn(client, world["id"], line, next_kind, "replacement")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "WORLD_TURN_PENDING"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == before_tasks
        saved = db.get(WorldLine, line["id"])
        assert saved.pending_task_id == old["taskId"] and saved.head_checkpoint_id == line["headCheckpointId"]
        assert db.get(WorldTurn, old["turnId"]).plan == before_plan
    replay = request_turn(client, world["id"], line, old_kind, "recoverable-old")
    assert replay.status_code == 202 and replay.json()["taskId"] == old["taskId"]
    assert usage(old["taskId"]) == 1
    claimed = worker.claim("pending-recovery-fixture")
    assert str(claimed) == old["taskId"]
    worker.process(claimed)
    with SessionLocal() as db:
        task, saved = db.get(Task, old["taskId"]), db.get(WorldLine, line["id"])
        assert task.state == "succeeded" and task.persistence_state == "saved"
        assert saved.pending_task_id is None
        current = db.get(WorldTurn, old["turnId"])
        assert current.plan == before_plan
        if old_kind == "write":
            assert saved.head_checkpoint_id == line["headCheckpointId"]
            assert current.status == "candidate" and len(current.result["chapters"]) == 1
        else:
            assert saved.head_checkpoint_id != line["headCheckpointId"] and current.status == "committed"
    assert usage(old["taskId"]) == 3
    worker.process(claimed)
    assert usage(old["taskId"]) == 3


@pytest.mark.parametrize("old_kind", ["action", "write"])
@pytest.mark.parametrize("next_kind", ["action", "write"])
def test_explicit_cancel_acknowledges_recoverable_turn_before_new_turn(client, reader, demo, profile, old_kind, next_kind):
    world, line = start(client, reader, demo, profile)
    old, line = recoverable_turn(client, world, line, old_kind)
    assert client.post(API + "/tasks/" + old["taskId"] + "/cancel").status_code == 200
    claimed = worker.claim("cancel-pending-fixture")
    assert str(claimed) == old["taskId"]
    worker.process(claimed)
    with SessionLocal() as db:
        task, saved = db.get(Task, old["taskId"]), db.get(WorldLine, line["id"])
        assert task.state == "cancelled" and task.persistence_state == "saved" and task.cancel_requested
        assert saved.pending_task_id is None and saved.head_checkpoint_id == line["headCheckpointId"]
    response = request_turn(client, world["id"], line, next_kind, "after-cancel")
    assert response.status_code == 202 and response.json()["taskId"] != old["taskId"]
    replay = request_turn(client, world["id"], line, old_kind, "recoverable-old")
    assert replay.status_code == 202 and replay.json()["taskId"] == old["taskId"]
    assert usage(old["taskId"]) == 1
    with SessionLocal() as db:
        assert db.get(WorldLine, line["id"]).pending_task_id == response.json()["taskId"]


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("next_kind", ["action", "write"])
def test_saved_terminal_pending_pointer_does_not_block_new_turn(client, reader, demo, profile, terminal, next_kind):
    world, line = start(client, reader, demo, profile)
    response = request_turn(client, world["id"], line, "action", "saved-terminal")
    assert response.status_code == 202
    old = response.json()
    if terminal == "cancelled":
        assert client.post(API + "/tasks/" + old["taskId"] + "/cancel").status_code == 200
        worker.process(old["taskId"])
    elif terminal == "failed":
        with patch.object(worker, "produce_scene", side_effect=Problem("WORLD_OUTPUT_INCOMPLETE", "fixture permanent stop")):
            worker.process(old["taskId"])
    else:
        worker.process(old["taskId"])
    with SessionLocal() as db:
        task = db.get(Task, old["taskId"])
        assert task.state == terminal and task.persistence_state == "saved"
        # Also exercise an old retained pointer to a genuinely saved terminal.
        db.get(WorldLine, line["id"]).pending_task_id = task.id
        db.commit()
    line = client.get(API + f"/worlds/{world['id']}/lines/{line['id']}").json()
    before = usage(old["taskId"])
    newer = request_turn(client, world["id"], line, next_kind, "after-terminal")
    assert newer.status_code == 202, newer.text
    assert newer.json()["taskId"] != old["taskId"]
    assert usage(old["taskId"]) == before


@pytest.mark.parametrize("old_kind", ["action", "write", "rewrite"])
@pytest.mark.parametrize("next_kind", ["action", "write"])
def test_reclaimed_legacy_replaced_turn_cannot_authorize_more_stages(client, reader, demo, profile, old_kind, next_kind):
    world, line = start(client, reader, demo, profile)
    old, line = recoverable_turn(client, world, line, old_kind)
    newer = replace_legacy_pending(client, world, line, old, next_kind)
    before = usage(old["taskId"])
    with SessionLocal() as db:
        old_turn = db.get(WorldTurn, old["turnId"])
        frozen = deepcopy({"plan": old_turn.plan, "result": old_turn.result, "draft": old_turn.draft_text})
    claimed = worker.claim("legacy-replacement-fixture")
    assert str(claimed) == old["taskId"]
    worker.process(claimed)
    assert usage(old["taskId"]) == before
    with SessionLocal() as db:
        task, saved = db.get(Task, old["taskId"]), db.get(WorldLine, line["id"])
        assert task.state == "failed" and task.persistence_state == "saved"
        assert task.error_code == "WORLD_CHECKPOINT_CONFLICT"
        assert saved.pending_task_id == newer["taskId"] and saved.head_checkpoint_id == line["headCheckpointId"]
        assert db.get(Task, newer["taskId"]).state == "queued"
        old_turn = db.get(WorldTurn, old["turnId"])
        assert {"plan": old_turn.plan, "result": old_turn.result, "draft": old_turn.draft_text} == frozen
    assert usage(newer["taskId"]) == 0


@pytest.mark.parametrize("replace_before", ["generation", "review"])
def test_legacy_pointer_change_between_stages_stops_next_call(client, reader, demo, profile, replace_before):
    world, line = start(client, reader, demo, profile)
    response = request_turn(client, world["id"], line, "action", "old-active")
    assert response.status_code == 202
    old, newer = response.json(), []
    original = worker.save_visibility if replace_before == "generation" else worker.review_scene

    def replaced(*args, **kwargs):
        newer.append(replace_legacy_pending(client, world, line, old, "action"))
        return original(*args, **kwargs)

    target = "save_visibility" if replace_before == "generation" else "review_scene"
    with patch.object(worker, target, replaced):
        worker.process(old["taskId"])
    assert len(newer) == 1
    assert usage(old["taskId"]) == (1 if replace_before == "generation" else 2)
    with SessionLocal() as db:
        task, saved = db.get(Task, old["taskId"]), db.get(WorldLine, line["id"])
        assert task.state == "failed" and task.error_code == "WORLD_CHECKPOINT_CONFLICT"
        assert saved.head_checkpoint_id == line["headCheckpointId"] and saved.pending_task_id == newer[0]["taskId"]
        assert db.get(Task, newer[0]["taskId"]).state == "queued"
    assert usage(newer[0]["taskId"]) == 0
