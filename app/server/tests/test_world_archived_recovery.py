"""Archive and explicit stop must also stop durable database-fault recovery."""
from copy import deepcopy
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.models import Task, UsageRecord
from app.world.models import World, WorldLine
from app.world import worker
from .helpers import read_chapter, observe
from .test_world import API, fast_fixture_stream, post, start


def usage(task_id):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id))


def recoverable_preparation(client, reader, demo, profile):
    anchor = read_chapter(client, demo)["blocks"][-1]["endAnchor"]
    observe(client, reader, anchor)
    job = post(client, "/worlds", {"anchor": anchor}, "recoverable-prepare")
    original = worker.add_event

    def failed_ready(db, task, kind, payload):
        if kind == "world_ready":
            raise OperationalError("commit-ready", {}, Exception("synthetic database disconnect"))
        return original(db, task, kind, payload)

    with patch.object(worker, "add_event", failed_ready):
        worker.process(job["taskId"])
    with SessionLocal() as db:
        task, world = db.get(Task, job["taskId"]), db.get(World, job["worldId"])
        assert task.state == "failed" and task.persistence_state == "recoverable"
        assert task.checkpoint["preparedBatches"] == task.payload["preparationBatches"] == 1
        assert world.status == "failed" and world.prepare_task_id == task.id
    return job


def archive_world(client, world_id):
    current = client.get(API + "/worlds/" + world_id).json()
    result = client.patch(API + "/worlds/" + world_id, json={"expectedVersion": current["version"], "status": "archived"})
    assert result.status_code == 200 and result.json()["status"] == "archived"


def test_archiving_recoverable_preparation_keeps_world_archived_and_stops_reclaim(client, reader, demo, profile):
    job = recoverable_preparation(client, reader, demo, profile)
    before = usage(job["taskId"])
    archive_world(client, job["worldId"])
    claimed = worker.claim("archive-recovery-fixture")
    assert str(claimed) == job["taskId"]
    worker.process(claimed)
    with SessionLocal() as db:
        assert db.get(World, job["worldId"]).status == "archived"
        task = db.get(Task, job["taskId"])
        assert task.cancel_requested and task.state == "cancelled" and task.persistence_state == "saved"
    assert usage(job["taskId"]) == before
    assert worker.claim("archive-recovery-fixture") is None


def test_legacy_archived_preparation_without_cancel_bit_cannot_publish_ready(client, reader, demo, profile):
    job = recoverable_preparation(client, reader, demo, profile)
    archive_world(client, job["worldId"])
    with SessionLocal() as db:
        # Previous releases omitted this bit on archived recoverable jobs.
        db.get(Task, job["taskId"]).cancel_requested = False
        db.commit()
    before = usage(job["taskId"])
    worker.process(worker.claim("legacy-archive-fixture"))
    with SessionLocal() as db:
        assert db.get(World, job["worldId"]).status == "archived"
        task = db.get(Task, job["taskId"])
        assert task.state in ("failed", "cancelled") and task.persistence_state == "saved"
        assert task.error_code in ("WORLD_ARCHIVED", "TASK_CANCELLED")
    assert usage(job["taskId"]) == before


@pytest.mark.parametrize("legacy_cancel_bit", [False, True])
def test_replaced_completed_batches_cannot_publish_or_fail_new_preparation(client, reader, demo, profile, legacy_cancel_bit):
    job = recoverable_preparation(client, reader, demo, profile)
    new = post(client, f"/worlds/{job['worldId']}/prepare", {}, "explicit-new-prepare")
    assert new["taskId"] != job["taskId"]
    if legacy_cancel_bit:
        with SessionLocal() as db:
            db.get(Task, job["taskId"]).cancel_requested = False
            db.commit()
    before = usage(job["taskId"])
    worker.process(worker.claim("replaced-prepare-fixture"))
    with SessionLocal() as db:
        world = db.get(World, job["worldId"])
        assert world.prepare_task_id == new["taskId"] and world.status == "preparing"
        assert db.get(Task, new["taskId"]).state == "queued"
        assert db.get(Task, job["taskId"]).state == "cancelled"
    assert usage(job["taskId"]) == before
    claimed = worker.claim("replaced-prepare-fixture")
    assert str(claimed) == new["taskId"]
    worker.process(claimed)
    assert client.get(API + "/worlds/" + job["worldId"]).json()["status"] == "active"


@pytest.mark.parametrize("stop", ["world", "legacy-world", "line", "delete", "cancel"])
def test_recoverable_rewrite_does_not_spend_or_commit_after_stop(client, reader, demo, profile, stop):
    world, parent = start(client, reader, demo, profile)
    job = post(client, f"/worlds/{world['id']}/rewrite", {"checkpointId": parent["headCheckpointId"], "instruction": "另一种表达"}, "recoverable-rewrite")
    with patch.object(worker, "save_visibility", side_effect=OperationalError("visibility", {}, Exception("synthetic disconnect"))):
        worker.process(job["taskId"])
    with SessionLocal() as db:
        task = db.get(Task, job["taskId"])
        assert task.state == "failed" and task.persistence_state == "recoverable"
        line = db.get(WorldLine, job["lineId"])
        assert line.pending_task_id == task.id
        frozen_head = line.head_checkpoint_id
        frozen_parent = deepcopy(db.get(WorldLine, parent["id"]).head_checkpoint_id)
    assert usage(job["taskId"]) == 0
    if stop in ("world", "legacy-world"):
        archive_world(client, world["id"])
        if stop == "legacy-world":
            with SessionLocal() as db:
                db.get(Task, job["taskId"]).cancel_requested = False
                db.commit()
    elif stop == "cancel":
        assert client.post(API + "/tasks/" + job["taskId"] + "/cancel").status_code == 200
    else:
        root = API + f"/worlds/{world['id']}/lines/{job['lineId']}"
        current = client.get(root).json()
        response = client.delete(root) if stop == "delete" else client.patch(root, json={"expectedVersion": current["version"], "status": "archived"})
        assert response.status_code == 200
    claimed = worker.claim("rewrite-stop-fixture")
    assert str(claimed) == job["taskId"]
    worker.process(claimed)
    with SessionLocal() as db:
        task = db.get(Task, job["taskId"])
        if stop == "legacy-world":
            assert not task.cancel_requested and task.state == "failed" and task.error_code == "WORLD_ARCHIVED"
        else:
            assert task.cancel_requested and task.state == "cancelled"
        assert task.persistence_state == "saved"
        line = db.get(WorldLine, job["lineId"])
        assert line.head_checkpoint_id == frozen_head and line.pending_task_id is None
        assert db.get(WorldLine, parent["id"]).head_checkpoint_id == frozen_parent
        if stop in ("world", "legacy-world"):
            assert db.get(World, world["id"]).status == "archived"
    assert usage(job["taskId"]) == 0
