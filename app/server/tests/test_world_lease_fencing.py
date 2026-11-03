"""Reclaim the SAME task while its old mock executor is still in the provider.

These are fault regressions, not real-provider or literary-quality tests.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Event, current_thread
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal, engine
from app.models import Task, UsageRecord, RoleMessage, now
from app.errors import Problem
from app.providers import MockAdapter
from app.world.models import World, WorldLine, WorldTurn
from app.world.worker import claim, process
from app.worker import claim as reader_claim, process as reader_process, renew_lease
from app.task_lease import leased_task, LeaseLost
from .helpers import observe, read_chapter
from .test_world import API, action, fast_fixture_stream, post, start
from .test_regressions import queue_operation
from .test_role_streaming import prepare_chat, send


def snapshot(task_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        world = db.get(World, task.payload["worldId"]) if task.payload.get("worldId") else None
        line = db.get(WorldLine, task.payload["lineId"]) if task.payload.get("lineId") else None
        turn = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id))
        return {"state": task.state, "errorCode": task.error_code, "phase": task.result.get("phase"),
                "worldStatus": world.status if world else None, "head": line.head_checkpoint_id if line else None,
                "checkpoint": deepcopy(task.checkpoint), "result": deepcopy(task.result), "eventSeq": task.event_seq,
                "providerState": task.provider_state, "usageState": task.usage_state,
                "worldDefinition": deepcopy(world.definition) if world else None,
                "turnDraft": turn.draft_text if turn else None, "turnResult": deepcopy(turn.result) if turn else None,
                "messages": [(m.role, m.text, m.status) for m in db.scalars(select(RoleMessage).where(
                    RoleMessage.task_id == task_id).order_by(RoleMessage.sequence))],
                "pending": line.pending_task_id if line else None,
                "calls": db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id))}


@pytest.mark.parametrize("kind", ["world_prepare", "world_turn", "world_write", "world_rewrite", "connection_test", "role_chat"])
def test_reclaimed_world_task_rejects_old_executor_late_work(client, reader, demo, profile, kind):
    assert settings.ai_mode == "mock" and engine.url.database == "reader_test"
    if kind == "world_prepare":
        anchor = read_chapter(client, demo)["blocks"][-1]["endAnchor"]
        observe(client, reader, anchor)
        queued = post(client, "/worlds", {"anchor": anchor, "rules": {}}, "lease-world-create")
    elif kind.startswith("world_"):
        world, line = start(client, reader, demo, profile)
        if kind == "world_write":
            queued = post(client, f"/worlds/{world['id']}/lines/{line['id']}/write", {
                "chapterCount": 3, "expectedCheckpointId": line["headCheckpointId"]}, "lease-world-write")
        elif kind == "world_rewrite":
            queued = post(client, f"/worlds/{world['id']}/rewrite", {
                "checkpointId": line["headCheckpointId"], "instruction": "更细腻"}, "lease-world-rewrite")
        else:
            queued = action(client, world, line, key="lease-world-action")
    elif kind == "connection_test":
        queued = {"taskId": queue_operation(client, profile, {"operation": kind, "profileId": profile["id"]})}
    else:
        queued = {"taskId": send(client, prepare_chat(client, reader, demo, profile), turn="lease-role")}
    task_id = queued["taskId"]
    take, run = (claim, process) if kind.startswith("world_") else (reader_claim, reader_process)
    claimed_a = take("same-worker")
    assert claimed_a == task_id
    entered, release = Event(), Event()
    original = MockAdapter.generate

    def paused(adapter, *args, **kwargs):
        result = original(adapter, *args, **kwargs)
        if current_thread().name.startswith("old-world-lease") and not entered.is_set():
            entered.set()
            assert release.wait(10), "old executor provider barrier was not released"
        return result

    def paused_stream(adapter, *args, on_delta, **kwargs):
        result = original(adapter, *args)
        on_delta("租约丢失之前已经收到的文字。")
        entered.set()
        assert release.wait(10), "old streaming provider barrier was not released"
        # Simulate a fully received response after the new executor has finished.
        # The old finalizer cannot publish the remaining receipt as new prose.
        return result

    with patch.object(MockAdapter, "generate", paused), patch.object(MockAdapter, "generate_stream", paused_stream), ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-world-lease") as pool:
        old = pool.submit(run, claimed_a)
        try:
            assert entered.wait(10), "old executor never persisted a provider intent"
            with SessionLocal() as db:
                task = db.get(Task, task_id)
                assert task.provider_state == "running" and len(task.checkpoint["calls"]) == 1
                task.lease_expires_at = now() - timedelta(seconds=1)
                db.commit()
            claimed_b = take("same-worker")
            assert claimed_b == task_id and claimed_b.lease_token != claimed_a.lease_token
            run(claimed_b)
            reclaimed = snapshot(task_id)
            assert reclaimed["state"] == "failed" and reclaimed["errorCode"] == "PROVIDER_STATUS_UNKNOWN"
            assert reclaimed["calls"] == 1
        finally:
            release.set()
        old.result(timeout=15)
    finished = snapshot(task_id)
    # The receipt for the already-sent original call may legitimately settle.
    # It cannot authorize stale prose/review stages or rewrite the new terminal.
    assert finished == reclaimed, {"reclaimed": reclaimed, "afterOldExecutor": finished}
    with SessionLocal() as db:
        usage = db.scalar(select(UsageRecord).where(UsageRecord.task_id == task_id))
        assert usage.status == "completed" and usage.usage_state in {"known", "estimated"}
        assert usage.input_tokens >= 0 and usage.output_tokens >= 0


@pytest.mark.parametrize("world_queue", [True, False])
def test_reclaimed_task_dispatch_and_heartbeat_keep_new_lease(client, reader, world_queue):
    """A queued pool thread must carry A's token, not read B's token on startup."""
    with SessionLocal() as db:
        task = Task(owner_id=reader["user"]["id"], task_type="world_prepare" if world_queue else "import", payload={})
        db.add(task); db.commit(); task_id = task.id
    take, run = (claim, process) if world_queue else (reader_claim, reader_process)
    a = take("same-worker")
    with SessionLocal() as db:
        db.get(Task, task_id).lease_expires_at = now() - timedelta(seconds=1)
        db.commit()
    b = take("same-worker")
    assert a == b == task_id and a.lease_token != b.lease_token
    with SessionLocal() as db:
        before = db.get(Task, task_id)
        expected = (before.state, before.event_seq, before.lease_owner, before.lease_expires_at, deepcopy(before.checkpoint))
    assert renew_lease(task_id, a.lease_token) is False
    run(a)
    with SessionLocal() as db:
        current = db.get(Task, task_id)
        assert (current.state, current.event_seq, current.lease_owner, current.lease_expires_at, current.checkpoint) == expected
        assert not db.scalar(select(UsageRecord.id).where(UsageRecord.task_id == task_id))
    assert renew_lease(task_id, b.lease_token) is True


def test_old_reader_failure_cannot_overwrite_new_success_or_stream(client, reader, demo, profile):
    from app.worker import fail_task, persist_reply_stream, record_validation, transition, call_provider
    task_id = send(client, prepare_chat(client, reader, demo, profile), turn="lease-late-failure")
    a = reader_claim("same-worker")
    with SessionLocal() as db:
        db.get(Task, task_id).lease_expires_at = now() - timedelta(seconds=1)
        db.commit()
    b = reader_claim("same-worker")
    reader_process(b)
    before = snapshot(task_id)
    assert before["state"] == "succeeded" and before["messages"][-1][2] == "saved"

    @leased_task
    def late_writes(stale):
        fail_task(stale, "PROVIDER_STATUS_UNKNOWN")
        persist_reply_stream(stale, "旧进程的最后文字", check_scope=False)
        for operation in (lambda: record_validation(stale, "validation", {"status": "unknown", "reason": "provider_unavailable"}),
                          lambda: transition(stale, "validating")):
            with pytest.raises(LeaseLost):
                operation()
    late_writes(a)
    assert snapshot(task_id) == before

    @leased_task
    def terminal_is_read_only(current):
        with pytest.raises(LeaseLost):
            call_provider(current, "generation:extra", "role_chat", {}, 1024)
        # A received stage can still be read; a new stage above adds no usage.
        assert call_provider(current, "generation", "role_chat", {}, 1024)["text"]
    terminal_is_read_only(b)
    assert snapshot(task_id) == before


def test_old_world_execution_cannot_write_while_new_lease_is_running(client, reader, demo, profile):
    from app.world.jobs import phase, persist_stream
    from app.world.worker import fail, save_visibility, produce_scene
    world, line = start(client, reader, demo, profile)
    task_id = action(client, world, line, key="lease-running-world")["taskId"]
    a = claim("same-worker")
    with SessionLocal() as db:
        db.get(Task, task_id).lease_expires_at = now() - timedelta(seconds=1)
        db.commit()
    b = claim("same-worker")
    assert a.lease_token != b.lease_token
    before = snapshot(task_id)
    assert before["state"] == "preparing"

    @leased_task
    def late_writes(stale):
        fail(stale, "WORLD_EXECUTION_FAILED")
        persist_stream(stale, "generation:scene:1", "旧进程的最后文字", final=True)
        for operation in (lambda: phase(stale, "saving"), lambda: save_visibility(stale, {}, {}),
                          lambda: produce_scene(stale, 1, None, None, "旧动作")):
            with pytest.raises(LeaseLost):
                operation()
    late_writes(a)
    assert snapshot(task_id) == before
    process(b)
    assert snapshot(task_id)["state"] == "succeeded"


@pytest.mark.parametrize("world_queue", [True, False])
def test_old_provider_failure_does_not_add_diagnostic_to_reclaimed_task(client, reader, demo, profile, world_queue):
    if world_queue:
        anchor = read_chapter(client, demo)["blocks"][-1]["endAnchor"]
        observe(client, reader, anchor)
        task_id = post(client, "/worlds", {"anchor": anchor, "rules": {}}, "lease-late-diagnostic")["taskId"]
    else:
        task_id = queue_operation(client, profile, {"operation": "connection_test", "profileId": profile["id"]})
    take, run = (claim, process) if world_queue else (reader_claim, reader_process)
    a = take("same-worker")
    entered, release = Event(), Event()
    def failed_provider(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        raise Problem("PROVIDER_STATUS_UNKNOWN", "isolated injected late failure", 502,
                      {"reason": "timeout", "exceptionType": "TimeoutError", "latencyMs": 1})
    with patch.object(MockAdapter, "generate", failed_provider), ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(run, a)
        try:
            assert entered.wait(10)
            with SessionLocal() as db:
                db.get(Task, task_id).lease_expires_at = now() - timedelta(seconds=1)
                db.commit()
            b = take("same-worker")
            run(b)
            before = snapshot(task_id)
            assert before["state"] == "failed" and "providerFailure" not in before["checkpoint"]
        finally:
            release.set()
        old.result(timeout=15)
    assert snapshot(task_id) == before
    with SessionLocal() as db:
        assert db.scalar(select(UsageRecord).where(UsageRecord.task_id == task_id)).status == "intent"
