"""Completed mock provider receipts arriving AFTER an actual world DELETE.

This tests PostgreSQL/HTTP and two live threads, not supplier cancellation,
literary quality or physical account purge. Only synthetic pytest fixtures run.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
from sqlalchemy import func, select, text

from app.config import settings
from app.db import SessionLocal, engine
from app.errors import Problem
from app.models import Branch, Consent, ModelProfile, Progress, Task, TaskEvent, UsageRecord
from app.providers import ChatCompletionsAdapter, MockAdapter
from app.storage import private_path
from app.worker import settle_usage
from app.world.models import WorldBase
from app.world.worker import claim, process
from .test_world import API, action, fast_fixture_stream, start


LATE_TEXT = "SYNTHETIC_COMPLETED_RECEIPT_AFTER_WORLD_DELETE"


def _budget(row):
    return (row.calls_reserved, row.input_reserved, row.output_reserved)


def _record(task_id, owner, profile_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        usage = db.scalar(select(UsageRecord).where(UsageRecord.task_id == task_id,
            UsageRecord.operation == "generation:scene:1"))
        consent = db.get(Consent, usage.consent_id)
        world_tables = [table for table in WorldBase.metadata.tables.values()
            if table.schema == "world" and "owner_id" in table.c]
        assert {table.fullname for table in world_tables} == {"world.instances", "world.lines",
            "world.checkpoints", "world.turns", "world.events", "world.memories", "world.publications",
            "world.scene_profiles", "world.scene_images", "world.character_looks", "world.character_artworks"}
        return {
            "task": {"state": task.state, "persistenceState": task.persistence_state,
                "providerState": task.provider_state, "usageState": task.usage_state,
                "checkpoint": deepcopy(task.checkpoint), "result": deepcopy(task.result)},
            "usage": {"id": usage.id, "callId": usage.provider_call_id,
                "status": usage.status, "state": usage.usage_state,
                "inputTokens": usage.input_tokens, "outputTokens": usage.output_tokens,
                "reservedInput": usage.reserved_input, "reservedOutput": usage.reserved_output},
            "consent": list(_budget(consent)),
            "profile": list(_budget(db.get(ModelProfile, profile_id))),
            "usageRows": db.scalar(select(func.count()).select_from(UsageRecord).where(
                UsageRecord.owner_id == owner)),
            "taskEvents": db.scalar(select(func.count()).select_from(TaskEvent).where(
                TaskEvent.task_id == task_id)),
            "worldRows": {table.fullname: db.scalar(select(func.count()).select_from(table).where(
                table.c.owner_id == owner)) for table in world_tables},
        }


def _observation(kind, value):
    """Optional evidence only; normal CI has no filesystem output dependency."""
    folder = os.environ.get("WORLD_INFLIGHT_OBSERVATIONS")
    if folder:
        destination = Path(folder)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / ("late-" + kind + ".json")).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("receipt_kind", ["known", "not_sent"])
@pytest.mark.parametrize("flush_before_delete", [False, True])
def test_world_delete_keeps_only_once_settled_late_receipt_without_resurrecting_content(
        client, reader, demo, profile, receipt_kind, flush_before_delete, tmp_path, monkeypatch):
    assert settings.ai_mode == "mock" and engine.dialect.name == "postgresql"
    assert engine.url.database and engine.url.database.endswith("_test")
    monkeypatch.setattr(settings, "storage_path", tmp_path / "private")
    with patch.object(ChatCompletionsAdapter, "generate", side_effect=AssertionError("real provider forbidden")), \
            patch.object(ChatCompletionsAdapter, "generate_stream", side_effect=AssertionError("real stream forbidden")):
        world, line = start(client, reader, demo, profile)
        owner = reader["user"]["id"]
        with SessionLocal() as db:
            source_before = (db.get(Branch, demo["branchId"]).head_revision_id,
                [(p.id, deepcopy(p.anchor), deepcopy(p.max_observed_anchor), p.revision_id,
                    p.version, deepcopy(p.device_sequences)) for p in db.scalars(
                    select(Progress).where(Progress.owner_id == owner).order_by(Progress.id))])
        queued = action(client, world, line, key="late-delete-" + receipt_kind)
        task_id = queued["taskId"]
        claimed = claim("world-delete-late-receipt")
        assert claimed == task_id
        entered, release, published = Event(), Event(), Event()
        from app.world import jobs as world_jobs
        original_persist = world_jobs.persist_stream

        def persisted_stream(*args, **kwargs):
            result = original_persist(*args, **kwargs)
            if not kwargs.get('final'):
                published.set()
            return result
        actual_calls = []
        original_generate = MockAdapter.generate
        receipt = {"text": LATE_TEXT, "usage": {"state": "known", "inputTokens": 11,
            "outputTokens": 13}, "latencyMs": 7, "finishReason": "stop"}
        if receipt_kind == "not_sent":
            receipt = {"notSent": "PROVIDER_HOST_UNAVAILABLE",
                "usage": {"state": "known", "inputTokens": 0, "outputTokens": 0}}
        observed = {"receiptKind": receipt_kind, "providerPid": None}

        def recorded_generate(adapter, *args, **kwargs):
            actual_calls.append("generate:" + args[1].splitlines()[0])
            return original_generate(adapter, *args, **kwargs)

        def completed_late(adapter, p, prompt, data, limit, *, on_delta, cancel_check, **kwargs):
            actual_calls.append("stream:" + prompt.splitlines()[0])
            cancel_check()
            if receipt_kind == "known":
                # A complete mock response is already received; cancellation
                # cannot un-send it. It returns after DELETE has committed.
                on_delta(LATE_TEXT)
                if flush_before_delete:
                    # Deterministically cover a clean buffer.close followed by
                    # deletion rejecting the final stream mirror.
                    assert published.wait(15), 'initial stream publication did not finish'
            with SessionLocal() as db:
                observed["providerPid"] = db.scalar(text("select pg_backend_pid()"))
                entered.set()
                assert release.wait(15), "provider receipt barrier was not released"
            if receipt_kind == "not_sent":
                raise Problem("PROVIDER_HOST_UNAVAILABLE", "synthetic confirmed pre-send rejection")
            return deepcopy(receipt)

        with patch.object(MockAdapter, "generate", recorded_generate), \
                patch.object(MockAdapter, "generate_stream", completed_late), \
                patch.object(world_jobs, "persist_stream", persisted_stream), \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="late-delete-provider") as pool:
            old_executor = pool.submit(process, claimed)
            try:
                assert entered.wait(15), "generation did not reach a durable intent"
                with SessionLocal() as db:
                    observed["observerPid"] = db.scalar(text("select pg_backend_pid()"))
                assert observed["providerPid"] != observed["observerPid"]
                observed["beforeDelete"] = _record(task_id, owner, profile["id"])
                assert observed["beforeDelete"]["usage"]["status"] == "intent"
                assert observed["beforeDelete"]["usage"]["state"] == "unknown"
                assert len(observed["beforeDelete"]["worldRows"]) == 11
                for table in ("world.instances", "world.lines", "world.checkpoints", "world.turns"):
                    assert observed["beforeDelete"]["worldRows"][table] > 0
                response = client.delete(API + "/worlds/" + world["id"])
                assert response.status_code == 200 and response.json() == {"deleted": True}, response.text
                observed["deleteResponse"] = response.json()
                observed["afterDeleteBeforeReceipt"] = _record(task_id, owner, profile["id"])
                assert not any(observed["afterDeleteBeforeReceipt"]["worldRows"].values())
                assert observed["afterDeleteBeforeReceipt"]["taskEvents"] == 0
            finally:
                release.set()
            old_executor.result(timeout=20)

        observed["afterReceipt"] = _record(task_id, owner, profile["id"])
        observed["actualCalls"] = list(actual_calls)
        _observation(receipt_kind, observed)
        after, before = observed["afterReceipt"], observed["beforeDelete"]
        assert (after["usage"]["status"], after["usage"]["state"],
            after["usage"]["inputTokens"], after["usage"]["outputTokens"]) == (
                "not_sent" if receipt_kind == "not_sent" else "completed", "known",
                receipt["usage"]["inputTokens"], receipt["usage"]["outputTokens"])
        release_budget = [int(receipt_kind == "not_sent"),
            before["usage"]["reservedInput"] - receipt["usage"]["inputTokens"],
            before["usage"]["reservedOutput"] - receipt["usage"]["outputTokens"]]
        for budget in ("profile", "consent"):
            assert after[budget] == [value - refund for value, refund in zip(before[budget], release_budget)]
        assert after["usageRows"] == before["usageRows"]
        assert len(actual_calls) == 2, "only the existing director and narrator attempts were authorized"
        assert after["taskEvents"] == 0
        assert not any(after["worldRows"].values())
        assert after["task"]["state"] == "cancelled" and after["task"]["persistenceState"] == "saved"
        assert after["task"]["result"] == {"worldId": world["id"], "deleted": True}
        assert "worldStream" not in after["task"]["checkpoint"]
        assert LATE_TEXT not in json.dumps(after, ensure_ascii=False)
        assert private_path(owner, "world-deleted-" + task_id + ".marker").exists()
        assert not list((settings.storage_path / owner).glob("journal-*"))
        for path in ("/worlds/" + world["id"], "/tasks/" + task_id, "/tasks/" + task_id + "/events"):
            response = client.get(API + path)
            assert response.status_code == 404 and LATE_TEXT not in response.text
        with SessionLocal() as db:
            source_after = (db.get(Branch, demo["branchId"]).head_revision_id,
                [(p.id, deepcopy(p.anchor), deepcopy(p.max_observed_anchor), p.revision_id,
                    p.version, deepcopy(p.device_sequences)) for p in db.scalars(
                    select(Progress).where(Progress.owner_id == owner).order_by(Progress.id))])
        assert source_after == source_before
        # Replay of the already received accounting result must not add another
        # provider call, release quota twice or restore a task event/body.
        settle_usage(task_id, after["usage"]["callId"], deepcopy(receipt))
        settle_usage(task_id, after["usage"]["callId"], deepcopy(receipt))
        with patch.object(MockAdapter, "generate", side_effect=AssertionError("deleted task cannot call again")), \
                patch.object(MockAdapter, "generate_stream", side_effect=AssertionError("deleted task cannot stream again")):
            process(claimed)
        observed["afterReplay"] = _record(task_id, owner, profile["id"])
        assert observed["afterReplay"] == after
        observed["passed"] = True
        _observation(receipt_kind, observed)
