"""Actual parallel PostgreSQL transactions, with deterministic provider fixtures."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock, current_thread
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.main import app
from app.models import Branch, ModelProfile, Task, UsageRecord, User, uid
from app.providers import MockAdapter
from app.worker import claim as reader_claim, process as reader_process
from app.world.models import World, WorldCheckpoint, WorldLine, WorldPublication, WorldTurn
from app.world.worker import claim as world_claim, process
from .test_regressions import queue_operation
from .test_world import API, fast_fixture_stream, post, read_line, start


def simultaneous_requests(client, method, path, bodies, keys):
    barrier = Barrier(len(bodies))
    authorization = client.headers["Authorization"]

    def send(index):
        with TestClient(app, raise_server_exceptions=False) as device:
            barrier.wait(timeout=10)
            response = device.request(method, API + path, json=bodies[index],
                headers={"Authorization": authorization, "Idempotency-Key": keys[index]})
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=len(bodies)) as pool:
        return list(pool.map(send, range(len(bodies))))


@pytest.mark.parametrize("same_turn", [False, True], ids=["competing-inputs", "repeated-turn"])
def test_parallel_actions_only_create_and_commit_one_turn(client, reader, demo, profile, same_turn):
    world, line = start(client, reader, demo, profile)
    parent = line["headCheckpointId"]
    with SessionLocal() as db:
        before_usage = db.scalar(select(func.count()).select_from(UsageRecord))
        before_turns = db.scalar(select(func.count()).select_from(WorldTurn))
    bodies = [{"message": "我问梅舟信的来历", "kind": "dialogue",
               "expectedCheckpointId": parent, "turnId": "race" if same_turn else f"race-{i}"}
              for i in range(2)]
    # Same turnId with distinct HTTP keys also has exactly one durable receipt.
    results = simultaneous_requests(client, "POST", f"/worlds/{world['id']}/lines/{line['id']}/actions",
                                    bodies, ["submit-a", "submit-b"])
    if same_turn:
        assert [status for status, _ in results] == [202, 202], results
        assert results[0][1] == results[1][1]
    else:
        assert sorted(status for status, _ in results) == [202, 409], results
        assert next(body for status, body in results if status == 409)["code"] == "WORLD_TURN_PENDING"
    winner = next(body for status, body in results if status == 202)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WorldTurn)) == before_turns + 1
        assert db.get(WorldLine, line["id"]).pending_task_id == winner["taskId"]
    process(winner["taskId"])
    completed = read_line(client, world, line)
    assert completed["head"]["sequence"] == line["head"]["sequence"] + 1
    assert completed["head"]["parentCheckpointId"] == parent
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before_usage + 3
        assert db.get(WorldLine, line["id"]).pending_task_id is None


def test_parallel_prefix_accept_commits_candidate_once(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    writing = post(client, f"/worlds/{world['id']}/lines/{line['id']}/write",
                   {"chapterCount": 3, "expectedCheckpointId": line["headCheckpointId"]}, "write-prefix")
    process(writing["taskId"])
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(WorldCheckpoint))
        usage = db.scalar(select(func.count()).select_from(UsageRecord))
        scenes = db.get(WorldTurn, writing["turnId"]).result["chapters"]
    body = {"chapterCount": 2, "expectedCheckpointId": line["headCheckpointId"]}
    results = simultaneous_requests(client, "POST", f"/worlds/{world['id']}/turns/{writing['turnId']}/accept",
                                    [body, body], ["accept-a", "accept-b"])
    assert sorted(status for status, _ in results) == [200, 409], results
    assert next(body for status, body in results if status == 409)["code"] == "WORLD_ALREADY_ACCEPTED"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WorldCheckpoint)) == before + 2
        assert db.get(WorldLine, line["id"]).head_checkpoint_id == scenes[1]["id"]
        assert db.get(WorldTurn, writing["turnId"]).result["acceptedCount"] == 2
        assert db.get(WorldCheckpoint, scenes[2]["id"]) is None
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == usage


def test_parallel_publication_different_keys_yields_one_reader_branch(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    with SessionLocal() as db:
        original_head = db.get(Branch, demo["branchId"]).head_revision_id
        before = db.scalar(select(func.count()).select_from(Branch))
    results = simultaneous_requests(client, "POST",
        f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish", [{}, {}], ["publish-a", "publish-b"])
    assert [status for status, _ in results] == [200, 200], results
    assert results[0][1] == results[1][1]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WorldPublication)) == 1
        assert db.scalar(select(func.count()).select_from(Branch)) == before + 1
        assert db.get(Branch, demo["branchId"]).head_revision_id == original_head


def test_reader_and_world_share_last_calls_without_overspend_or_partial_head(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    with SessionLocal() as db:
        row = db.get(ModelProfile, profile["id"])
        initial_calls = row.calls_reserved
        # Four calls are reserved by the world contract, including its one
        # optional correction. Two readers racing with the normal three-stage
        # world turn must still never exceed the shared four-call remainder.
        row.call_budget = initial_calls + 4
        db.commit()
    turn = post(client, f"/worlds/{world['id']}/lines/{line['id']}/actions",
                {"message": "我向梅舟询问信件", "kind": "dialogue",
                 "expectedCheckpointId": line["headCheckpointId"], "turnId": "shared-quota"}, "shared-quota")
    reader_task = queue_operation(client, profile, {"operation": "connection_test", "profileId": profile["id"]})
    reader_task_two = queue_operation(client, profile, {"operation": "connection_test", "profileId": profile["id"]})
    barrier = Barrier(3)
    arrived, arrival_lock = set(), Lock()
    original = MockAdapter.generate

    def together(adapter, model, prompt, data, limit):
        if prompt.startswith(("world-director-", "connection-v1")):
            with arrival_lock:
                arrived.add("world" if prompt.startswith("world-director-") else "reader")
            barrier.wait(timeout=10)
        return original(adapter, model, prompt, data, limit)

    with patch.object(MockAdapter, "generate", together), ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(process, turn["taskId"]), pool.submit(reader_process, reader_task), pool.submit(reader_process, reader_task_two)]
        for future in futures:
            future.result(timeout=30)
    assert arrived == {"world", "reader"}
    with SessionLocal() as db:
        row = db.get(ModelProfile, profile["id"])
        assert row.calls_reserved == initial_calls + 4 == row.call_budget
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == initial_calls + 4
        assert db.get(Task, reader_task).state == "succeeded"
        assert db.get(Task, reader_task_two).state == "succeeded"
        failed = db.get(Task, turn["taskId"])
        assert failed.state == "failed" and failed.error_code == "BUDGET_NOT_AUTHORIZED"
        assert db.get(WorldLine, line["id"]).head_checkpoint_id == line["headCheckpointId"]
        draft = db.get(WorldTurn, turn["turnId"])
        assert draft.status == "draft" and draft.draft_text


@pytest.mark.parametrize("capacity", [2, 4])
def test_parallel_world_claims_preserve_owner_global_limits_and_reader_queue(client, reader, monkeypatch, capacity):
    from app.config import settings
    monkeypatch.setattr(settings, "world_task_concurrency", capacity)
    with SessionLocal() as db:
        owner = reader["user"]["id"]
        others = [User(id=uid(), username=f"worker-{i}", password_hash="fixture") for i in range(4)]
        db.add_all(others)
        db.flush()
        tasks = [Task(id=uid(), owner_id=who, task_type="world_prepare", payload={}, checkpoint={}, result={})
                 for who in [owner, owner, *[other.id for other in others]]]
        old = Task(id=uid(), owner_id=owner, task_type="connection_test", payload={}, checkpoint={}, result={})
        db.add_all([*tasks, old])
        db.commit()
        legacy_id = old.id
    barrier = Barrier(5)

    def acquire(index):
        barrier.wait(timeout=10)
        return world_claim(f"parallel-{index}")

    with ThreadPoolExecutor(max_workers=5) as pool:
        claimed = list(pool.map(acquire, range(5)))
    assert len({value for value in claimed if value}) == capacity
    assert claimed.count(None) == 5 - capacity
    with SessionLocal() as db:
        active = list(db.scalars(select(Task).where(Task.id.in_([value for value in claimed if value]))))
        assert len({value.owner_id for value in active}) == capacity
        assert db.get(Task, legacy_id).state == "queued"
    assert reader_claim("independent-reader") == legacy_id
    assert world_claim("global-capacity-full") is None


def test_archive_while_turn_is_committing_has_no_lock_cycle_or_partial_checkpoint(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    turn = post(client, f"/worlds/{world['id']}/lines/{line['id']}/actions",
                {"message": "我询问下一班渡船", "kind": "dialogue",
                 "expectedCheckpointId": line["headCheckpointId"], "turnId": "archive-race"}, "archive-race")
    locked, release = Event(), Event()
    original_scalar = Session.scalar
    authorization = client.headers["Authorization"]

    def pause_final_commit(session, statement, *args, **kwargs):
        result = original_scalar(session, statement, *args, **kwargs)
        if (current_thread().name.startswith("world-finisher") and isinstance(result, Task) and
                result.id == turn["taskId"] and result.result.get("phase") == "saving" and
                getattr(statement, "_for_update_arg", None) is not None and not locked.is_set()):
            locked.set()
            assert release.wait(10), "commit barrier was not released"
        return result

    def archive():
        with TestClient(app, raise_server_exceptions=False) as device:
            response = device.patch(API + "/worlds/" + world["id"],
                headers={"Authorization": authorization},
                json={"expectedVersion": world["version"], "status": "archived"})
            return response.status_code, response.json()

    with patch.object(Session, "scalar", pause_final_commit), \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="world-finisher") as writer, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="world-archiver") as lifecycle:
        completed = writer.submit(process, turn["taskId"])
        try:
            assert locked.wait(10), "test did not reach the real commit transaction"
            archived = lifecycle.submit(archive)
            deadline = time.monotonic() + 8
            waiting = False
            while time.monotonic() < deadline:
                with SessionLocal() as db:
                    waiting = bool(db.scalar(text("""
                        SELECT count(*) FROM pg_stat_activity
                        WHERE datname=current_database() AND pid<>pg_backend_pid()
                          AND wait_event_type='Lock'
                          AND (query LIKE '%world.instances%' OR query LIKE '%tasks%')
                    """)))
                if waiting:
                    break
                time.sleep(.02)
            assert waiting, "archive never waited on the in-flight database transaction"
        finally:
            release.set()
        completed.result(timeout=15)
        status, result = archived.result(timeout=15)
    assert status == 200 and result["status"] == "archived", (status, result)
    with SessionLocal() as db:
        assert db.get(Task, turn["taskId"]).state == "succeeded"
        saved_line = db.get(WorldLine, line["id"])
        saved = db.get(WorldCheckpoint, saved_line.head_checkpoint_id)
        assert saved.parent_checkpoint_id == line["headCheckpointId"]
        assert saved.sequence == line["head"]["sequence"] + 1
        assert db.get(WorldTurn, turn["turnId"]).checkpoint_id == saved.id
        assert saved_line.pending_task_id is None
        assert db.get(World, world["id"]).status == "archived"
