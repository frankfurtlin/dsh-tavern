"""X12 deletion commit/file boundaries; dedicated _test DB and mock receipts only."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, engine
from app.errors import Problem
from app.models import Branch, Progress, Task, UsageRecord
from app.providers import MockAdapter
from app import storage
from app.world import worker
from app.world.models import World
from .test_world import API, action, start
from .test_world_security import _login_other, _snapshot, isolated_storage_and_mock_only


@pytest.fixture(autouse=True)
def require_isolated_mock_database():
    assert settings.ai_mode == "mock"
    assert engine.url.database and engine.url.database.endswith("_test")


def tasks_for(owner, world_id):
    with SessionLocal() as db:
        return [(task.id, deepcopy(task.checkpoint)) for task in db.scalars(
            select(Task).where(Task.owner_id == owner,
                               Task.payload["worldId"].astext == world_id).order_by(Task.id))]


def usage_and_source(owner, demo):
    with SessionLocal() as db:
        return {
            "usage": [deepcopy(dict(row)) for row in db.execute(select(UsageRecord.__table__)
                .where(UsageRecord.owner_id == owner).order_by(UsageRecord.id)).mappings()],
            "sourceHead": db.get(Branch, demo["branchId"]).head_revision_id,
            "progress": [deepcopy(dict(row)) for row in db.execute(select(Progress.__table__)
                .where(Progress.owner_id == owner).order_by(Progress.id)).mappings()],
        }


def assert_private_cleanup(owner, world_id):
    for task_id, checkpoint in tasks_for(owner, world_id):
        assert not checkpoint.get("worldPrivateCleanupPending")
        assert storage.private_path(owner, "world-deleted-" + task_id + ".marker").exists()
        assert not list((settings.storage_path / owner).glob("journal-" + task_id + "-*.json"))
        with pytest.raises(Problem) as blocked:
            storage.journal(owner, task_id, "late-synthetic-call", {"text": "private late body"})
        assert blocked.value.code == "TASK_CANCELLED"


def test_delete_commit_rollback_preserves_received_journals_and_same_task_recovery(
        client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    owner = reader["user"]["id"]
    job = action(client, world, line, "观察河岸", "delete-rollback-recovery")
    with patch.object(worker, "commit_turn", side_effect=OperationalError(
            "controlled turn commit", {}, RuntimeError("synthetic disconnect"))):
        worker.process(job["taskId"])
    with SessionLocal() as db:
        task = db.get(Task, job["taskId"])
        assert (task.state, task.persistence_state) == ("failed", "recoverable")
        assert task.checkpoint["worldStream"]["text"]
        calls = deepcopy(task.checkpoint["calls"])
    before = _snapshot([owner])
    assert any("journal-" in name for name in before["files"])
    original_commit = Session.commit
    failures = []

    def fail_delete_commit(db):
        if any(isinstance(row, World) and row.id == world["id"] for row in db.deleted):
            failures.append(world["id"])
            raise OperationalError("controlled world DELETE commit", {}, RuntimeError("synthetic disconnect"))
        return original_commit(db)

    with patch.object(Session, "commit", fail_delete_commit):
        response = client.delete(API + "/worlds/" + world["id"])
    assert response.status_code == 503, response.text
    assert failures == [world["id"]]
    assert _snapshot([owner]) == before, "DELETE rollback altered rows, private prose, receipt, budgets or journal bytes"
    assert client.get(API + "/worlds/" + world["id"]).status_code == 200
    assert not list((settings.storage_path / owner).glob("world-deleted-*.marker"))
    settled_before = usage_and_source(owner, demo)
    with patch.object(MockAdapter, "generate", side_effect=AssertionError("journal recovery must not call a provider")), \
            patch.object(MockAdapter, "generate_stream", side_effect=AssertionError("journal recovery must not stream again")):
        worker.process(job["taskId"])
    with SessionLocal() as db:
        task = db.get(Task, job["taskId"])
        assert task.state == "succeeded"
        assert task.checkpoint["calls"] == calls
    assert usage_and_source(owner, demo) == settled_before


def test_committed_delete_file_failure_is_retryable_by_owner_without_new_usage(
        client, reader, demo, profile):
    world, _ = start(client, reader, demo, profile)
    owner = reader["user"]["id"]
    owner_token = reader["accessToken"]
    before = usage_and_source(owner, demo)
    original_write = storage.write_private
    failures = []

    def fail_marker(owner_id, name, body):
        if owner_id == owner and name.startswith("world-deleted-"):
            failures.append(name)
            raise OSError("controlled post-commit marker write failure")
        return original_write(owner_id, name, body)

    with patch.object(storage, "write_private", fail_marker):
        response = client.delete(API + "/worlds/" + world["id"])
    assert response.status_code == 503, response.text
    assert failures
    assert response.json()["details"] == {"worldId": world["id"], "deleted": True, "cleanupPending": True}
    assert response.json()["retryable"] is True
    assert client.get(API + "/worlds/" + world["id"]).status_code == 404
    assert all(checkpoint.get("worldPrivateCleanupPending") for _, checkpoint in tasks_for(owner, world["id"]))
    assert usage_and_source(owner, demo) == before
    _login_other(client, "atomicity_other_owner")
    assert client.delete(API + "/worlds/" + world["id"]).status_code == 404
    client.headers["Authorization"] = "Bearer " + owner_token
    with patch.object(MockAdapter, "generate", side_effect=AssertionError("DELETE retry must not call a provider")):
        retried = client.delete(API + "/worlds/" + world["id"])
        assert retried.status_code == 200 and retried.json() == {"deleted": True}, retried.text
    assert_private_cleanup(owner, world["id"])
    assert usage_and_source(owner, demo) == before


def test_maintenance_retries_committed_deletion_after_unlink_failure(
        client, reader, demo, profile):
    world, _ = start(client, reader, demo, profile)
    owner = reader["user"]["id"]
    before = usage_and_source(owner, demo)
    original_unlink = Path.unlink
    failures = []

    def fail_journal_unlink(path, *args, **kwargs):
        if path.parent == settings.storage_path / owner and path.name.startswith("journal-"):
            failures.append(path.name)
            raise OSError("controlled committed journal unlink failure")
        return original_unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", fail_journal_unlink):
        response = client.delete(API + "/worlds/" + world["id"])
    assert response.status_code == 503, response.text
    assert failures
    assert client.get(API + "/worlds/" + world["id"]).status_code == 404
    assert any(checkpoint.get("worldPrivateCleanupPending") for _, checkpoint in tasks_for(owner, world["id"]))
    # The request is over: maintenance alone must consume the committed outbox,
    # including files that a late received-result writer left before the marker.
    maintenance = getattr(storage, "clean_deleted_world_journals", None)
    assert callable(maintenance), "committed deletion has no durable private-file cleanup retry"
    maintenance()
    assert_private_cleanup(owner, world["id"])
    stable = _snapshot([owner])
    maintenance()
    assert _snapshot([owner]) == stable, "completed cleanup was repeated or revived deleted private data"
    assert usage_and_source(owner, demo) == before
