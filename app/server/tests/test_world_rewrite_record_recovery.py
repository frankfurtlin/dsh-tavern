"""One real rewrite transaction rollback/reclaim, fresh _test PostgreSQL/mock only."""
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db import SessionLocal, engine
from app.domain import digest
from app.models import Task, TaskEvent, UsageRecord
from app.providers import MockAdapter
from app.storage import private_path
from app.world import worker
from app.world.memory import context
from app.world.models import World, WorldCheckpoint, WorldEvent, WorldLine, WorldMemory, WorldTurn
from .test_world_checkpoint_restore import API, EXPECTED, archive_content, event_content, raw, scenario


@pytest.fixture(autouse=True)
def require_mock_test_database():
    assert settings.ai_mode == "mock"
    assert engine.url.database and engine.url.database.endswith("_test")


def row_counts(db, owner):
    return tuple(db.scalar(select(func.count()).select_from(model).where(model.owner_id == owner))
                 for model in (WorldCheckpoint, WorldMemory, WorldEvent))


def usage_rows(db, task_id):
    return [(u.id, u.provider_call_id, u.status, u.usage_state, u.input_tokens, u.output_tokens)
            for u in db.scalars(select(UsageRecord).where(UsageRecord.task_id == task_id).order_by(UsageRecord.id))]


def received_journals(task):
    receipts = {}
    for stage, call in task.checkpoint["calls"].items():
        saved = json.loads(private_path(task.owner_id, f"journal-{task.id}-{call['callId']}.json").read_text("utf-8"))
        assert saved["taskId"] == task.id and saved["callId"] == call["callId"]
        assert saved["hash"] == digest(json.dumps(saved["result"], ensure_ascii=False, sort_keys=True))
        receipts[stage] = (call["callId"], saved["hash"])
    return receipts


def test_structured_rewrite_is_readable_and_preserves_state_and_original(scenario):
    client, owner, ids, calls, fixtures = scenario
    selected = fixtures['after']
    result = client.post(API+f"/worlds/{ids['world']}/rewrite",json={
        'checkpointId':ids['after'],'instruction':'保留结果，只改文字'},headers={'Idempotency-Key':'structured-rewrite'}).json()
    prose = '\n'.join([json.dumps({'kind':'narration','text':'这封信已经交到旅人手中。'},ensure_ascii=False),
                       json.dumps({'kind':'speech','speakerId':'npc','text':'明天港口见。'},ensure_ascii=False)])
    def provider(task,stage,prompt,data,limit):
        return {'text':prose if prompt=='world_narrator' else '{"consistent":true,"issues":[]}', 'finishReason':'stop'}
    with patch.object(worker,'call_provider',side_effect=provider):worker.process(result['taskId'])
    with SessionLocal() as db:
        task=db.get(Task,result['taskId']);assert task.state=='succeeded',task.error_code
        rewritten=db.get(WorldCheckpoint,task.result['checkpointId'])
        original=db.get(WorldCheckpoint,ids['after'])
        assert rewritten.state==original.state==selected['state']
        assert rewritten.definition==original.definition
        assert rewritten.text=='这封信已经交到旅人手中。\n\n旅人说：“明天港口见。”'
        assert original.text==selected['text']
        assert rewritten.memory['sceneTranscript']['checkpointId']==rewritten.id
        assert rewritten.memory['sceneTranscript']['segments'][1]['speakerId']=='npc'


def test_rewrite_flushed_records_rollback_and_same_task_journal_recovery(scenario):
    client, owner, ids, calls, fixtures = scenario
    # Deliberately inconsistent owner/world links are guarded DB isolation baits,
    # not model output. They must not be copied from the replaced checkpoint.
    registered = client.post("/api/reader/v1/auth/register", json={
        "username": "restore_bait_" + uuid4().hex[:16], "password": "synthetic-bait-password-123"})
    assert registered.status_code == 201, registered.text
    other_owner = registered.json()["id"]
    other_world = uuid4().hex
    extra_text = "补档蓝舟原始档案：旅人还记得交信时的完整对话。"
    base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(World(id=other_world, owner_id=owner, name="隔离诱饵世界", source_title="合成来源",
                     source_book_id=None, source={"anchor": {}, "title": "合成来源", "blocks": []},
                     definition=deepcopy(fixtures["after"]["definition"]), status="active"))
        db.flush()
        for i, label in enumerate(("Before", "After", "Sibling")):
            db.get(WorldMemory, ids["archive" + label]).created_at = base_time + timedelta(minutes=i)
            db.get(WorldEvent, ids["event" + label]).created_at = base_time + timedelta(minutes=i)
        private_event = db.scalar(select(WorldEvent).where(WorldEvent.owner_id == owner,
            WorldEvent.checkpoint_id == ids["after"], WorldEvent.visible_to == ["npc"]))
        private_event.created_at = base_time + timedelta(minutes=4)
        db.add(WorldMemory(owner_id=owner, world_id=ids["world"], line_id=ids["line"],
            checkpoint_id=ids["after"], first_checkpoint_id=ids["before"], last_checkpoint_id=ids["after"],
            level=1, text=extra_text, event_ids=["event-before", "event-after"],
            created_at=base_time + timedelta(minutes=3)))
        for label, bait_owner, bait_world in (("foreign-owner", other_owner, ids["world"]),
                                             ("foreign-world", owner, other_world)):
            db.add(WorldMemory(owner_id=bait_owner, world_id=bait_world, line_id=ids["line"],
                checkpoint_id=ids["after"], first_checkpoint_id=ids["after"], last_checkpoint_id=ids["after"],
                level=1, text="隔离诱饵蓝舟-" + label, event_ids=[label],
                created_at=base_time + timedelta(minutes=10)))
            db.add(WorldEvent(owner_id=bait_owner, world_id=bait_world, checkpoint_id=ids["after"],
                entity_id="npc", type="background_progress", data={"id": label, "description": "隔离诱饵-" + label,
                "audienceIds": ["alice"], "audiencePlayerId": "alice", "audienceAll": False},
                visible_to=["alice"], minute=90, created_at=base_time + timedelta(minutes=10)))
        db.commit()
        selected_archives = sorted([archive_content(m) for m in db.scalars(select(WorldMemory).where(
            WorldMemory.owner_id == owner, WorldMemory.world_id == ids["world"], WorldMemory.checkpoint_id == ids["after"]))],
            key=lambda m: (m[4], m[5]))
        selected_events = sorted([event_content(e) for e in db.scalars(select(WorldEvent).where(
            WorldEvent.owner_id == owner, WorldEvent.world_id == ids["world"], WorldEvent.checkpoint_id == ids["after"]))],
            key=lambda e: e[4]["id"])
        assert len(selected_archives) == 3 and {m[4] for m in selected_archives} == {1, 2}
        assert len(selected_events) == 2 and any(e[5] == ["npc"] for e in selected_events)
        frozen_checkpoints = {label: raw(db.get(WorldCheckpoint, ids[label])) for label in ("before", "after", "sibling")}
        frozen_archives = {m.id: archive_content(m) for m in db.scalars(select(WorldMemory))}
        frozen_events = {e.id: event_content(e) for e in db.scalars(select(WorldEvent))}
        original_line = db.get(WorldLine, ids["line"])
        original_head_version = (original_line.head_checkpoint_id, original_line.version)

    body = {"checkpointId": ids["after"], "instruction": "保留发生的结果和全部档案，只改写正文"}
    response = client.post(API + f'/worlds/{ids["world"]}/rewrite', json=body,
                           headers={"Idempotency-Key": "rewrite-record-rollback"})
    assert response.status_code == 202, response.text
    job = response.json()
    with SessionLocal() as db:
        baseline = row_counts(db, owner)  # Includes the fork node made by the real rewrite API.
        line = db.get(WorldLine, job["lineId"])
        rewrite_head_version = (line.head_checkpoint_id, line.version)
        assert line.head_checkpoint_id == ids["before"] and line.pending_task_id == job["taskId"]
    injected = []
    restore = worker.restore_rewrite_records

    def flush_then_fail(db, checkpoint, selected):
        restore(db, checkpoint, selected)
        db.flush()
        memory_ids = list(db.scalars(select(WorldMemory.id).where(WorldMemory.checkpoint_id == checkpoint.id)))
        event_ids = list(db.scalars(select(WorldEvent.id).where(WorldEvent.checkpoint_id == checkpoint.id)))
        injected.append({"checkpointId": checkpoint.id, "memoryIds": memory_ids, "eventIds": event_ids})
        assert len(memory_ids) == 3 and len(event_ids) == 2
        assert db.get(WorldLine, job["lineId"]).head_checkpoint_id == checkpoint.id
        assert db.get(Task, job["taskId"]).state == "succeeded"
        raise OperationalError("synthetic post-archive flush commit failure", {}, RuntimeError("one controlled fault"))

    first_claim = worker.claim("rewrite-record-first-claim")
    assert first_claim is not None and str(first_claim) == job["taskId"]
    with patch.object(worker, "restore_rewrite_records", flush_then_fail):
        worker.process(first_claim)
    assert len(injected) == 1, "fault was not reached after the real archive/event flush"
    with SessionLocal() as db:
        task = db.get(Task, job["taskId"])
        turn = db.get(WorldTurn, job["turnId"])
        line = db.get(WorldLine, job["lineId"])
        assert (task.state, task.persistence_state, task.error_code) == ("failed", "recoverable", "PERSISTENCE_UNAVAILABLE")
        assert (line.head_checkpoint_id, line.version) == rewrite_head_version
        assert line.pending_task_id == task.id and turn.status == "draft" and turn.checkpoint_id is None
        draft = turn.draft_text
        assert draft and draft == task.checkpoint["worldStream"]["text"]
        assert row_counts(db, owner) == baseline
        assert db.get(WorldCheckpoint, injected[0]["checkpointId"]) is None
        assert all(db.get(WorldMemory, value) is None for value in injected[0]["memoryIds"])
        assert all(db.get(WorldEvent, value) is None for value in injected[0]["eventIds"])
        assert not list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id, TaskEvent.type == "turn_committed")))
        receipts = received_journals(task)
        initial_usage = usage_rows(db, task.id)
        assert len(receipts) == len(initial_usage) == len(calls) == 2
        assert {stage for stage in receipts} == {"generation:rewrite", "validation:world:1"}
    read = client.get(API + f'/worlds/{ids["world"]}/turns/{job["turnId"]}')
    assert read.status_code == 200 and read.json()["draftText"] == draft and read.json()["status"] == "draft"
    task_read = client.get(API + f'/tasks/{job["taskId"]}')
    assert task_read.status_code == 200 and task_read.json()["persistenceState"] == "recoverable"

    def no_new_provider(*args, **kwargs):
        raise AssertionError("same-task recovery must consume received journals, not call the provider")

    second_claim = worker.claim("rewrite-record-recovery-claim")
    assert second_claim is not None and str(second_claim) == job["taskId"]
    assert second_claim.lease_token != first_claim.lease_token
    with patch.object(MockAdapter, "generate", no_new_provider), patch.object(MockAdapter, "generate_stream", no_new_provider):
        worker.process(second_claim)
    with SessionLocal() as db:
        task = db.get(Task, job["taskId"])
        turn = db.get(WorldTurn, job["turnId"])
        line = db.get(WorldLine, job["lineId"])
        restored = db.get(WorldCheckpoint, line.head_checkpoint_id)
        assert (task.state, task.persistence_state, task.error_code) == ("succeeded", "saved", None)
        assert line.pending_task_id is None and line.version == rewrite_head_version[1] + 1
        assert turn.status == "committed" and turn.checkpoint_id == restored.id
        assert restored.id != injected[0]["checkpointId"] and restored.parent_checkpoint_id == ids["before"]
        assert restored.kind == "rewrite" and restored.text == draft and restored.sequence == 5
        for field in ("state", "definition", "memory", "action", "options", "changes", "title", "summary"):
            assert getattr(restored, field) == fixtures["after"][field], field
        copied_archive_rows = list(db.scalars(select(WorldMemory).where(WorldMemory.checkpoint_id == restored.id)))
        assert all(m.line_id == job["lineId"] for m in copied_archive_rows)
        copied_archives = sorted([archive_content(m) for m in copied_archive_rows], key=lambda m: (m[4], m[5]))
        copied_events = sorted([event_content(e) for e in db.scalars(select(WorldEvent).where(
            WorldEvent.checkpoint_id == restored.id))], key=lambda e: e[4]["id"])
        assert copied_archives == selected_archives and copied_events == selected_events
        assert row_counts(db, owner) == tuple(a + b for a, b in zip(baseline, (1, 3, 2)))
        assert len(list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task.id, TaskEvent.type == "turn_committed")))) == 1
        assert usage_rows(db, task.id) == initial_usage and received_journals(task) == receipts and len(calls) == 2
        original = db.get(WorldLine, ids["line"])
        assert (original.head_checkpoint_id, original.version) == original_head_version
        assert all(raw(db.get(WorldCheckpoint, ids[label])) == value for label, value in frozen_checkpoints.items())
        assert all(archive_content(db.get(WorldMemory, key)) == value for key, value in frozen_archives.items())
        assert all(event_content(db.get(WorldEvent, key)) == value for key, value in frozen_events.items())
        recalled = context(db, restored, db.get(World, ids["world"]), "蓝舟")
        # Same relevance score; this asserts real retrieval order, not a sorted comparison.
        archive_order = [m["text"] for m in recalled["recalledEpisodes"]]
        assert archive_order == [extra_text, EXPECTED["archives"]["after"], EXPECTED["archives"]["before"]]
        event_order = [e["description"] for e in recalled["recalledEvents"]]
        assert event_order == [EXPECTED["events"]["before"], EXPECTED["events"]["after"], "只有旅人知道的隐秘后台进展"]
        public = client.get(API + f'/worlds/{ids["world"]}/checkpoints/{restored.id}/events')
        assert public.status_code == 200, public.text
        assert sorted(e["description"] for e in public.json()["items"]) == sorted(EXPECTED["events"][name] for name in ("before", "after"))
        assert "只有旅人知道的隐秘后台进展" not in public.text
        assert "隔离诱饵" not in json.dumps(recalled, ensure_ascii=False) and EXPECTED["archives"]["sibling"] not in archive_order
        final_counts = row_counts(db, owner)
        restored_id = restored.id
    # Terminal replay and the original idempotency key cannot append a second copy.
    replay = client.post(API + f'/worlds/{ids["world"]}/rewrite', json=body,
                         headers={"Idempotency-Key": "rewrite-record-rollback"})
    assert replay.status_code == 202 and replay.json() == job
    with patch.object(MockAdapter, "generate", no_new_provider), patch.object(MockAdapter, "generate_stream", no_new_provider):
        worker.process(second_claim)
    with SessionLocal() as db:
        assert row_counts(db, owner) == final_counts and usage_rows(db, job["taskId"]) == initial_usage
        assert db.get(WorldLine, job["lineId"]).head_checkpoint_id == restored_id and len(calls) == 2
    if os.environ.get("WORLD_RESTORE_ARTIFACT_DIR"):
        output = Path(os.environ["WORLD_RESTORE_ARTIFACT_DIR"])
        output.mkdir(parents=True, exist_ok=True)
        (output / "observed-rewrite-record-recovery.json").write_text(json.dumps({
            "taskId": job["taskId"], "rollbackCounts": baseline, "finalCounts": final_counts,
            "flushedThenRolledBack": injected, "restoredCheckpointId": restored_id,
            "archiveOrder": archive_order, "eventOrder": event_order,
            "selectedArchivesCopied": len(copied_archives), "selectedEventsCopied": len(copied_events),
            "providerCallsBeforeAfter": [2, len(calls)], "usageRowsBeforeAfter": [len(initial_usage), len(initial_usage)],
            "journalCallIds": {stage: receipt[0] for stage, receipt in receipts.items()},
            "scope": "controlled post-flush OperationalError; normal same-task claim/journal recovery; mock; not a real DB outage or UI/native test"
        }, ensure_ascii=False, indent=2), encoding="utf-8")
