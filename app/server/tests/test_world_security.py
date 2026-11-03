"""X04/X12 HTTP, PostgreSQL cascade and private-journal security regressions.

Uses the dedicated *_test database through conftest, temporary private storage,
and deterministic fixtures only. No real model adapter may be called.
"""
from copy import deepcopy
from datetime import timedelta
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db import Base, SessionLocal
from app.errors import Problem
from app.models import Branch, Idempotency, Task, TaskEvent, User, uid, now
from app.providers import ChatCompletionsAdapter, MockAdapter
from app.storage import clean_orphan_files, journal, mark_synced, private_path, write_private
from app.worker import process as process_reader
from app.world.jobs import persist_stream
from app.world.models import World, WorldBase, WorldMemory
from app.world.worker import process
from .helpers import BASE
from .test_world import API, action, post, read_line, start


PRIVATE_WORLD = "X04_PRIVATE_WORLD_TITLE"
PRIVATE_ACTION = "X04_PRIVATE_PENDING_ACTION"
PRIVATE_STREAM = "X04_PRIVATE_STREAM_BODY"
LATE_BODY = "X12_RECEIVED_AFTER_WORLD_DELETION"
DELETED_PRIVATE_PHRASE = "X12_PRIVATE_FACT_CACHED_IN_TASK_CHECKPOINT"
PRIVATE_RECEIPT_STATE = "X12_PRIVATE_DESCRIPTION_IN_SUCCESSFUL_RECEIPT"


@pytest.fixture(autouse=True)
def isolated_storage_and_mock_only(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_path", tmp_path / "private")
    monkeypatch.setattr(settings, "ai_mode", "mock")

    def forbid_real(*args, **kwargs):
        raise AssertionError("security tests must never call a real adapter")

    def fast_stream(adapter, profile, prompt, data, limit, *, on_delta,
                    cancel_check, **kwargs):
        result = adapter.generate(profile, prompt, data, limit)
        for offset in range(0, len(result["text"]), 64):
            cancel_check()
            on_delta(result["text"][offset:offset + 64])
        return result

    monkeypatch.setattr(ChatCompletionsAdapter, "generate", forbid_real)
    monkeypatch.setattr(ChatCompletionsAdapter, "generate_stream", forbid_real)
    monkeypatch.setattr(MockAdapter, "generate_stream", fast_stream)


def _login_other(client, username="security_other"):
    client.headers.pop("Authorization", None)
    created = client.post(BASE + "/auth/register", json={
        "username": username, "password": "reader-password-123"})
    assert created.status_code == 201, created.text
    result = client.post(BASE + "/auth/login", json={
        "username": username, "password": "reader-password-123",
        "clientType": "android", "deviceName": "security-fixture"})
    assert result.status_code == 200, result.text
    credentials = result.json()
    client.headers["Authorization"] = "Bearer " + credentials["accessToken"]
    return credentials


def _owned_tables():
    return [table for table in Base.metadata.tables.values()
            if "owner_id" in table.c] + [
                table for table in WorldBase.metadata.tables.values()
                if table.schema == "world" and "owner_id" in table.c]


def _snapshot(owners):
    """Capture actual rows and fixture files, including budgets and idempotency."""
    owners = tuple(owners)
    with SessionLocal() as db:
        rows = {
            table.fullname: [deepcopy(dict(row)) for row in db.execute(
                select(table).where(table.c.owner_id.in_(owners)).order_by(
                    *table.primary_key.columns)).mappings()]
            for table in _owned_tables()
        }
        rows["users"] = [dict(row) for row in db.execute(
            select(User.__table__).where(User.id.in_(owners)).order_by(User.id)
        ).mappings()]
        task_ids = db.scalars(select(Task.id).where(Task.owner_id.in_(owners))).all()
        rows["task_events"] = [deepcopy(dict(row)) for row in db.execute(
            select(TaskEvent.__table__).where(TaskEvent.task_id.in_(task_ids))
            .order_by(TaskEvent.id)).mappings()]
    files = {}
    for owner in owners:
        folder = settings.storage_path / owner
        if folder.exists():
            files.update({str(path.relative_to(settings.storage_path)): path.read_bytes()
                          for path in folder.rglob("*") if path.is_file()})
    return {"rows": rows, "files": files}


@pytest.fixture
def target(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    renamed = client.patch(API + "/worlds/" + world["id"], json={
        "expectedVersion": world["version"], "name": PRIVATE_WORLD})
    assert renamed.status_code == 200, renamed.text
    world.update(renamed.json())
    candidate = post(client, f"/worlds/{world['id']}/lines/{line['id']}/write", {
        "chapterCount": 1, "expectedCheckpointId": line["headCheckpointId"]},
        "security-candidate")
    process(candidate["taskId"])
    candidate_view = client.get(API + f"/worlds/{world['id']}/turns/{candidate['turnId']}")
    assert candidate_view.status_code == 200 and candidate_view.json()["chapters"], candidate_view.text
    line = read_line(client, world, line)
    pending = action(client, world, line, PRIVATE_ACTION, "security-pending")
    persist_stream(pending["taskId"], "generation:scene:1", PRIVATE_STREAM)
    visible_task = client.get(API + "/tasks/" + pending["taskId"])
    assert visible_task.status_code == 200
    assert visible_task.json()["stream"]["text"] == PRIVATE_STREAM
    receipt = client.get(API + f"/worlds/{world['id']}/lines/{line['id']}/actions/security-pending")
    assert receipt.status_code == 200 and receipt.json()["taskId"] == pending["taskId"]
    return {"world": world, "line": line, "candidate": candidate,
            "pending": pending, "demo": demo, "owner": reader["user"]["id"]}


WORLD_RESOURCE_CASES = (
    "source_filter", "create", "world_read", "world_edit", "world_delete",
    "prepare", "begin", "lines", "line_read", "line_edit", "line_delete",
    "history", "graph", "checkpoint", "checkpoint_events", "action",
    "receipt", "fork", "write", "turn", "drafts", "accept", "rewrite",
    "correction", "publish", "task", "task_cancel", "task_events",
)
READER_TASK_CASES = ("reader_task", "reader_cancel", "reader_events", "reader_diagnostics")


def _request_case(case, data, *, route_world=None, route_line=None):
    world, line = data["world"], data["line"]
    root = f"/worlds/{route_world or world['id']}"
    line_root = root + "/lines/" + (route_line or line["id"])
    checkpoint = line["headCheckpointId"]
    candidate = data["candidate"]["turnId"]
    task_id = data["pending"]["taskId"]
    mapping = {
        "source_filter": ("GET", API + "/sources?bookId=" + data["demo"]["bookId"], None),
        "create": ("POST", API + "/worlds", {"anchor": world["sourceAnchor"]}),
        "world_read": ("GET", API + root, None),
        "world_edit": ("PATCH", API + root, {"expectedVersion": world["version"], "name": "stolen"}),
        "world_delete": ("DELETE", API + root, None),
        "prepare": ("POST", API + root + "/prepare", {}),
        "begin": ("POST", API + root + "/begin", {"characterId": world["characters"][0]["id"]}),
        "lines": ("GET", API + root + "/lines", None),
        "line_read": ("GET", API + line_root, None),
        "line_edit": ("PATCH", API + line_root, {"expectedVersion": line["version"], "name": "stolen"}),
        "line_delete": ("DELETE", API + line_root, None),
        "history": ("GET", API + line_root + "/history", None),
        "graph": ("GET", API + root + "/graph", None),
        "checkpoint": ("GET", API + root + "/checkpoints/" + checkpoint, None),
        "checkpoint_events": ("GET", API + root + "/checkpoints/" + checkpoint + "/events", None),
        "action": ("POST", API + line_root + "/actions", {"message": "attempted unauthorized action",
            "kind": "action", "expectedCheckpointId": checkpoint, "turnId": "unauthorized-action"}),
        "receipt": ("GET", API + line_root + "/actions/security-pending", None),
        "fork": ("POST", API + root + "/fork", {"checkpointId": checkpoint, "mode": "continue"}),
        "write": ("POST", API + line_root + "/write", {"chapterCount": 1, "expectedCheckpointId": checkpoint}),
        "turn": ("GET", API + root + "/turns/" + candidate, None),
        "drafts": ("GET", API + line_root + "/drafts", None),
        "accept": ("POST", API + root + "/turns/" + candidate + "/accept", {
            "chapterCount": 1, "expectedCheckpointId": checkpoint}),
        "rewrite": ("POST", API + root + "/rewrite", {"checkpointId": checkpoint, "instruction": "stolen rewrite"}),
        "correction": ("POST", API + root + "/corrections", {"expectedCheckpointId": checkpoint,
            "field": "rule:pace", "value": "detailed", "reason": "unauthorized correction"}),
        "publish": ("POST", API + root + "/checkpoints/" + checkpoint + "/publish", {}),
        "task": ("GET", API + "/tasks/" + task_id, None),
        "task_cancel": ("POST", API + "/tasks/" + task_id + "/cancel", {}),
        "task_events": ("GET", API + "/tasks/" + task_id + "/events?afterSeq=0", None),
        "reader_task": ("GET", BASE + "/tasks/" + task_id, None),
        "reader_cancel": ("POST", BASE + "/tasks/" + task_id + "/cancel", {}),
        "reader_events": ("GET", BASE + "/tasks/" + task_id + "/events?afterSeq=0", None),
        "reader_diagnostics": ("GET", BASE + "/tasks/" + task_id + "/diagnostics", None),
    }
    return mapping[case]


def _assert_denied_without_side_effect(client, case, data, owners, **scope):
    method, path, body = _request_case(case, data, **scope)
    before = _snapshot(owners)
    headers = {"Idempotency-Key": "security-denied-" + case,
               "Last-Event-ID": data["pending"]["taskId"] + ":0"}
    response = client.request(method, path, json=body, headers=headers)
    assert response.status_code == 404, (case, response.status_code, response.text)
    assert response.json()["code"] == "RESOURCE_NOT_FOUND", response.text
    for private in (PRIVATE_WORLD, PRIVATE_ACTION, PRIVATE_STREAM, data["world"]["id"],
                    data["line"]["id"], data["candidate"]["turnId"], data["pending"]["taskId"]):
        assert private not in response.text, (case, response.text)
    assert _snapshot(owners) == before, "denied endpoint changed rows, budgets, events or private files: " + case


@pytest.mark.parametrize("case", WORLD_RESOURCE_CASES + READER_TASK_CASES)
def test_X04_every_world_resource_is_404_to_another_owner_without_side_effect(
        client, target, case):
    other = _login_other(client)
    _assert_denied_without_side_effect(client, case, target,
                                      [target["owner"], other["user"]["id"]])


@pytest.mark.parametrize("case", READER_TASK_CASES)
def test_X04_owner_cannot_bypass_world_task_projection_through_reader_routes(client, target, case):
    _assert_denied_without_side_effect(client, case, target, [target["owner"]])


def test_X04_collection_routes_exclude_other_owners_and_world_tasks(client, target):
    other = _login_other(client)
    before = _snapshot([target["owner"], other["user"]["id"]])
    for path in (API + "/worlds", API + "/sources", BASE + "/tasks"):
        result = client.get(path)
        assert result.status_code == 200, result.text
        assert result.json()["items"] == []
        assert PRIVATE_WORLD not in result.text and PRIVATE_STREAM not in result.text
    assert _snapshot([target["owner"], other["user"]["id"]]) == before


SCOPE_CASES = ("line_read", "line_edit", "line_delete", "history", "checkpoint",
               "checkpoint_events", "action", "receipt", "fork", "write", "turn",
               "drafts", "accept", "rewrite", "correction", "publish")


@pytest.mark.parametrize("case", SCOPE_CASES)
def test_X04_owned_resource_ids_cannot_be_used_under_another_owned_world(client, target, case):
    created = post(client, "/worlds", {"anchor": target["world"]["sourceAnchor"]}, "scope-other-world")
    process(created["taskId"])
    assert client.get(API + "/worlds/" + created["worldId"]).status_code == 200
    _assert_denied_without_side_effect(client, case, target, [target["owner"]],
                                      route_world=created["worldId"])


def test_X12_account_deletion_waits_for_world_lease_then_cleans_both_schemas_and_journals(
        client, reader, target):
    world, line = target["world"], target["line"]
    owner = target["owner"]
    published = post(client, f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",
                     {}, "security-publication")
    assert published["revisionId"]
    with SessionLocal() as db:
        db.add(WorldMemory(owner_id=owner, world_id=world["id"], line_id=line["id"],
            checkpoint_id=line["headCheckpointId"], first_checkpoint_id=line["headCheckpointId"],
            last_checkpoint_id=line["headCheckpointId"], level=1,
            text="X12_PRIVATE_ARCHIVED_MEMORY", event_ids=[]))
        pending = db.get(Task, target["pending"]["taskId"])
        pending.state = "running"
        pending.lease_expires_at = now() + timedelta(minutes=2)
        from app.scenes.models import SceneProfile, SceneImage, CharacterLook,CharacterArtwork
        from app.providers import encrypt_key
        scene_profile=SceneProfile(owner_id=owner,key_cipher=encrypt_key('TEST-ONLY-NOT-REAL'))
        db.add(scene_profile);db.flush()
        scene_image=SceneImage(owner_id=owner,world_id=world['id'],line_id=line['id'],checkpoint_id=line['headCheckpointId'],profile_id=scene_profile.id,profile_version=1,scene_hash='f'*64,generation=1,status='failed',descriptor={},image={})
        db.add(scene_image);db.flush()
        artwork=CharacterArtwork(owner_id=owner,world_id=world['id'],entity_id='security-fixture',source_kind='user_upload',license_note='Owned synthetic cascade fixture',descriptor={'kind':'character','entityId':'security-fixture'},image={},full_image=b'owned-artwork-body',avatar_image=b'owned-avatar-body')
        db.add(artwork);db.flush()
        db.add(CharacterLook(owner_id=owner,world_id=world['id'],entity_id='security-fixture',selected_scene_id=scene_image.id,selected_artwork_id=artwork.id))
        db.commit()

    survivor = _login_other(client, "security_survivor")
    survivor_demo = client.post(BASE + "/books/demo", json={}, headers={"Idempotency-Key": "survivor-demo"})
    assert survivor_demo.status_code == 201, survivor_demo.text
    survivor_profile = client.post(BASE + "/model-profiles", json={
        "name": "security-survivor-fixture", "baseUrl": "mock://deterministic",
        "modelId": "fixture-v1", "key": "TEST-KEY-NOT-REAL", "contextWindow": 32768,
        "callBudget": 100, "inputBudget": 10000000, "outputBudget": 1000000})
    assert survivor_profile.status_code == 201, survivor_profile.text
    start(client, survivor, survivor_demo.json(), survivor_profile.json())
    survivor_id = survivor["user"]["id"]
    write_private(survivor_id, "survivor-fixture.txt", b"X12_SURVIVOR_FILE")
    survivor_before = _snapshot([survivor_id])
    owned_before = _snapshot([owner])
    for table in _owned_tables():
        if table.schema == "world":
            assert owned_before["rows"][table.fullname], "world cascade test did not seed " + table.fullname
    for table in ("books", "sources", "branches", "revisions", "content_blocks", "progress",
                  "model_profiles", "operation_plans", "consents", "tasks", "usage",
                  "idempotency", "user_settings", "device_sessions"):
        assert owned_before["rows"][table], "reader cleanup test did not seed " + table
    assert any("journal-" in name for name in owned_before["files"])

    client.headers["Authorization"] = "Bearer " + reader["accessToken"]
    deleted = client.post(BASE + "/auth/delete-account", json={
        "currentPassword": "reader-password-123", "confirmation": "删除我的账号"})
    assert deleted.status_code == 202, deleted.text
    cleanup = deleted.json()
    for path in (BASE + "/auth/me", API + "/worlds/" + world["id"],
                 API + "/tasks/" + target["pending"]["taskId"] + "/events"):
        assert client.get(path).status_code == 401
    process_reader(cleanup["taskId"])
    with SessionLocal() as db:
        assert db.get(User, owner).status == "deleting"
        assert db.get(World, world["id"]) is not None
        assert db.get(Task, cleanup["taskId"]).state == "queued"
        task_ids = db.scalars(select(Task.id).where(Task.owner_id == owner)).all()
        usage_before_cancel = len(db.execute(select(Base.metadata.tables["usage"]).where(
            Base.metadata.tables["usage"].c.owner_id == owner)).all())
    assert (settings.storage_path / owner).exists(), "cleanup must wait for live world lease"
    with patch.object(MockAdapter, "generate", side_effect=AssertionError("cancel must not invoke a model")):
        process(target["pending"]["taskId"])
    with SessionLocal() as db:
        assert db.get(Task, target["pending"]["taskId"]).state == "cancelled"
        assert len(db.execute(select(Base.metadata.tables["usage"]).where(
            Base.metadata.tables["usage"].c.owner_id == owner)).all()) == usage_before_cancel
    process_reader(cleanup["taskId"])

    cleared = _snapshot([owner])
    assert not cleared["rows"]["users"] and not cleared["files"]
    assert all(not rows for rows in cleared["rows"].values()), cleared["rows"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id.in_(task_ids))) == 0
    assert not (settings.storage_path / owner).exists()
    assert (settings.storage_path / ".deleted" / owner).exists()
    assert _snapshot([survivor_id]) == survivor_before, "deleting account changed another owner's data"
    # Cleanup receipts intentionally survive as opaque status-only capabilities.
    receipt = client.get(BASE + "/auth/deletion-receipts/" + cleanup["receiptId"])
    assert receipt.status_code == 200 and receipt.json()["status"] == "completed", receipt.text
    assert set(receipt.json()) == {"status", "deadline", "completedAt"}
    assert owner not in receipt.text and PRIVATE_STREAM not in receipt.text
    with pytest.raises(Problem) as stopped:
        journal(owner, target["pending"]["taskId"], uid(), {"text": LATE_BODY})
    assert stopped.value.code == "TASK_CANCELLED"
    with pytest.raises(Problem) as stopped:
        write_private(owner, "late-account-body.txt", LATE_BODY.encode())
    assert stopped.value.code == "TASK_CANCELLED"
    persist_stream(target["pending"]["taskId"], "generation:scene:1", LATE_BODY, final=True)
    assert not (settings.storage_path / owner).exists()


@pytest.mark.parametrize("deletion_point", ("streaming", "journal_write"))
def test_X12_delete_during_generation_blocks_late_stream_journal_and_checkpoint(
        client, reader, demo, profile, deletion_point):
    world, line = start(client, reader, demo, profile)
    job = action(client, world, line, "观察渡口", "delete-in-generation")
    owner = reader["user"]["id"]
    original_write = write_private
    observed = {"deleted": False, "generationCall": None, "billableCalls": None}

    def delete_world():
        # on_delta now queues a bounded publication; deletion must happen after
        # its real DB mirror, while generate_stream still has not returned.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                pending = db.get(Task, job["taskId"])
                assert db.get(User, pending.owner_id).status == "active"
                published = pending.checkpoint.get("worldStream", {}).get("text")
            if published == LATE_BODY:
                break
            time.sleep(.005)
        with SessionLocal() as db:
            task = db.get(Task, job["taskId"])
            assert task.provider_state == "running"
            assert task.checkpoint["worldStream"]["text"] == LATE_BODY
            observed["generationCall"] = task.checkpoint["calls"]["generation:scene:1"]["callId"]
            assert any(private_path(owner, f"journal-{job['taskId']}-{call['callId']}.json").exists()
                       for stage, call in task.checkpoint["calls"].items() if stage.startswith("analysis:"))
            # The cache contains private fact wording independently of visible prose.
            for world_task in db.scalars(select(Task).where(Task.owner_id == owner,
                    Task.payload["worldId"].astext == world["id"])):
                world_task.checkpoint = {**world_task.checkpoint,
                    "worldDeniedPhrases": [DELETED_PRIVATE_PHRASE]}
            observed["billableCalls"] = db.scalar(select(func.count()).select_from(
                Base.metadata.tables["usage"]).where(
                    Base.metadata.tables["usage"].c.task_id == job["taskId"]))
            db.commit()
        response = client.delete(API + "/worlds/" + world["id"])
        assert response.status_code == 200 and response.json()["deleted"], response.text
        observed["deleted"] = True
        with SessionLocal() as db:
            for world_task in db.scalars(select(Task).where(Task.owner_id == owner,
                    Task.payload["worldId"].astext == world["id"])):
                assert "worldStream" not in world_task.checkpoint
                assert "worldDeniedPhrases" not in world_task.checkpoint
                assert DELETED_PRIVATE_PHRASE not in str(world_task.checkpoint)
                assert db.scalar(select(func.count()).select_from(TaskEvent).where(
                    TaskEvent.task_id == world_task.id)) == 0

    def received_stream(adapter, p, prompt, data, limit, *, on_delta, cancel_check, **kwargs):
        cancel_check()
        result = adapter.generate(p, prompt, data, limit)
        result["text"] = LATE_BODY
        on_delta(LATE_BODY)
        if deletion_point == "streaming":
            delete_world()
            # Uncooperative/late receipt: return after deletion despite cancellation.
            with pytest.raises(Problem) as blocked_stream:
                persist_stream(job["taskId"], "generation:scene:1", LATE_BODY, final=True)
            assert blocked_stream.value.code == "TASK_CANCELLED"
        return result

    def write_after_marker_check(owner_id, name, body):
        if deletion_point == "journal_write" and not observed["deleted"] and owner_id == owner:
            with SessionLocal() as db:
                call = db.get(Task, job["taskId"]).checkpoint.get("calls", {}).get("generation:scene:1")
            if call and name == f"journal-{job['taskId']}-{call['callId']}.json":
                # journal() has checked the marker; DELETE now linearizes before write_private.
                delete_world()
        return original_write(owner_id, name, body)

    with patch.object(MockAdapter, "generate_stream", received_stream), \
            patch("app.storage.write_private", write_after_marker_check):
        process(job["taskId"])
    assert observed["deleted"], "test never reached deletion while generation was in flight"
    with SessionLocal() as db:
        for table in _owned_tables():
            if table.schema == "world":
                assert db.scalar(select(func.count()).select_from(table).where(table.c.owner_id == owner)) == 0
        task = db.get(Task, job["taskId"])
        assert task.state == "cancelled" and task.cancel_requested
        assert not task.checkpoint.get("worldStream")
        assert "worldDeniedPhrases" not in task.checkpoint
        assert DELETED_PRIVATE_PHRASE not in str(task.checkpoint)
        assert task.result.get("deleted") is True
        assert LATE_BODY not in str(task.result)
        assert all(LATE_BODY not in str(event.payload) for event in db.scalars(
            select(TaskEvent).where(TaskEvent.task_id == job["taskId"])))
        assert all(DELETED_PRIVATE_PHRASE not in str(event.payload) for event in db.scalars(
            select(TaskEvent).where(TaskEvent.task_id == job["taskId"])))
        assert db.scalar(select(func.count()).select_from(Base.metadata.tables["usage"]).where(
            Base.metadata.tables["usage"].c.task_id == job["taskId"])) == observed["billableCalls"]
        assert db.get(Branch, demo["branchId"]).head_revision_id == demo["revisionId"]
    assert private_path(owner, "world-deleted-" + job["taskId"] + ".marker").exists()
    assert not list((settings.storage_path / owner).glob("journal-" + job["taskId"] + "-*.json"))
    for path in (API + "/worlds/" + world["id"], API + "/tasks/" + job["taskId"],
                 API + "/tasks/" + job["taskId"] + "/events",
                 BASE + "/tasks/" + job["taskId"],
                 API + f"/worlds/{world['id']}/lines/{line['id']}/actions/delete-in-generation"):
        response = client.get(path)
        assert response.status_code == 404 and LATE_BODY not in response.text, response.text
    with pytest.raises(Problem) as blocked_stream:
        persist_stream(job["taskId"], "generation:scene:1", LATE_BODY, final=True)
    assert blocked_stream.value.code == "TASK_CANCELLED"
    with pytest.raises(Problem) as stopped:
        journal(owner, job["taskId"], uid(), {"text": LATE_BODY})
    assert stopped.value.code == "TASK_CANCELLED"
    with patch.object(MockAdapter, "generate", side_effect=AssertionError("deleted task must not regenerate")):
        process(job["taskId"])
    with SessionLocal() as db:
        assert not db.get(Task, job["taskId"]).checkpoint.get("worldStream")
    assert not list((settings.storage_path / owner).glob("journal-" + job["taskId"] + "-*.json"))


def _receipt_record(owner, scope, key):
    with SessionLocal() as db:
        receipt = db.scalar(select(Idempotency).where(Idempotency.owner_id == owner,
            Idempotency.scope == "world:" + scope, Idempotency.key == key))
        assert receipt is not None, (scope, key)
        return {"id": receipt.id, "key": receipt.key, "payloadHash": receipt.payload_hash,
                "result": deepcopy(receipt.result), "expiresAt": receipt.expires_at}


@pytest.mark.parametrize("operation", ("fork", "correction"))
def test_X12_deleted_world_successful_receipt_becomes_tombstone_and_never_replays_private_state(
        client, target, operation):
    world, line, owner = target["world"], target["line"], target["owner"]
    correction_body = {"expectedCheckpointId": line["headCheckpointId"],
        "entityId": line["head"]["state"]["playerId"], "field": "description",
        "value": PRIVATE_RECEIPT_STATE, "reason": "fixture description correction"}
    corrected = post(client, f"/worlds/{world['id']}/corrections", correction_body,
                     "private-receipt-correction")
    if operation == "correction":
        path, body, key = f"/worlds/{world['id']}/corrections", correction_body, "private-receipt-correction"
        original = corrected
    else:
        path = f"/worlds/{world['id']}/fork"
        body = {"checkpointId": corrected["headCheckpointId"], "mode": "continue"}
        key = "private-receipt-fork"
        original = post(client, path, body, key)
    assert PRIVATE_RECEIPT_STATE in str(original), "fixture must seed actual private state in the successful receipt"
    scope = operation + ":" + world["id"]
    receipt_before = _receipt_record(owner, scope, key)
    assert receipt_before["result"] == original
    assert client.delete(API + "/worlds/" + world["id"]).status_code == 200
    tombstone = _receipt_record(owner, scope, key)
    assert tombstone["id"] == receipt_before["id"]
    assert tombstone["key"] == receipt_before["key"]
    assert tombstone["payloadHash"] == receipt_before["payloadHash"]
    assert tombstone["result"] == {"worldId": world["id"], "deleted": True}
    with SessionLocal() as db:
        assert all(PRIVATE_RECEIPT_STATE not in str(receipt.result) for receipt in db.scalars(
            select(Idempotency).where(Idempotency.owner_id == owner,
                                      Idempotency.scope.like("world:%"))))
    before_replay = _snapshot([owner])
    for _ in range(2):
        response = client.post(API + path, json=body, headers={"Idempotency-Key": key})
        assert response.status_code == 404, (operation, response.status_code, response.text)
        assert PRIVATE_RECEIPT_STATE not in response.text
        assert "head" not in response.json() and "state" not in response.json()
        assert _snapshot([owner]) == before_replay, "deleted receipt replay changed rows, fees or files"


def test_X12_world_create_old_key_cannot_recreate_deleted_world_or_add_task_or_fee(client, target):
    world, owner = target["world"], target["owner"]
    # These are the exact payload and key used by start(), including default rules.
    original_body = {"anchor": world["sourceAnchor"], "rules": {}}
    key = "world-create"
    before = _receipt_record(owner, "create", key)
    assert before["result"]["worldId"] == world["id"]
    before_delete = _snapshot([owner])
    assert before_delete["rows"]["usage"], "fee-preservation fixture must include actual mock call settlement"
    assert client.delete(API + "/worlds/" + world["id"]).status_code == 200
    after_delete = _snapshot([owner])
    assert after_delete["rows"]["usage"] == before_delete["rows"]["usage"]
    assert after_delete["rows"]["model_profiles"] == before_delete["rows"]["model_profiles"]
    receipt = _receipt_record(owner, "create", key)
    assert receipt["id"] == before["id"] and receipt["payloadHash"] == before["payloadHash"]
    assert receipt["result"] == {"worldId": world["id"], "deleted": True}
    for body, expected in ((original_body, 404),
                           ({**original_body, "name": "attempt to reuse deleted creation key"}, 409),
                           (original_body, 404)):
        result = client.post(API + "/worlds", json=body, headers={"Idempotency-Key": key})
        assert result.status_code == expected, (result.status_code, result.text)
        assert PRIVATE_WORLD not in result.text and PRIVATE_STREAM not in result.text
        assert _snapshot([owner]) == after_delete, "creation replay recreated data, added tasks or changed fees"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(World).where(World.owner_id == owner)) == 0


def test_X12_deleting_one_world_preserves_another_owned_world_successful_receipts(client, target):
    owner, deleted_world = target["owner"], target["world"]
    create_body = {"anchor": deleted_world["sourceAnchor"], "name": "X12_surviving_world"}
    create_key = "surviving-world-create"
    created = post(client, "/worlds", create_body, create_key)
    process(created["taskId"])
    world = client.get(API + "/worlds/" + created["worldId"]).json()
    begin_body = {"characterId": next(c["id"] for c in world["characters"] if c["available"])}
    begin_key = "surviving-world-begin"
    begun = post(client, f"/worlds/{world['id']}/begin", begin_body, begin_key)
    process(begun["taskId"])
    line = read_line(client, world, {"id": begun["lineId"]})
    correction_body = {"expectedCheckpointId": line["headCheckpointId"],
        "entityId": line["head"]["state"]["playerId"], "field": "description",
        "value": "X12_SURVIVING_PRIVATE_DESCRIPTION", "reason": "surviving world fixture"}
    correction_key = "surviving-world-correction"
    corrected = post(client, f"/worlds/{world['id']}/corrections", correction_body, correction_key)
    fork_body = {"checkpointId": corrected["headCheckpointId"], "mode": "continue"}
    fork_key = "surviving-world-fork"
    forked = post(client, f"/worlds/{world['id']}/fork", fork_body, fork_key)
    assert "X12_SURVIVING_PRIVATE_DESCRIPTION" in str(forked)
    calls = [
        ("/worlds", create_body, create_key, "create", created, 202),
        (f"/worlds/{world['id']}/begin", begin_body, begin_key, "begin:" + world["id"], begun, 202),
        (f"/worlds/{world['id']}/corrections", correction_body, correction_key,
         "correction:" + world["id"], corrected, 200),
        (f"/worlds/{world['id']}/fork", fork_body, fork_key, "fork:" + world["id"], forked, 200),
    ]
    surviving_receipts = {key: _receipt_record(owner, scope, key)
                          for _, _, key, scope, _, _ in calls}
    assert client.delete(API + "/worlds/" + deleted_world["id"]).status_code == 200
    after_delete = _snapshot([owner])
    for path, body, key, scope, original, status in calls:
        assert _receipt_record(owner, scope, key) == surviving_receipts[key]
        response = client.post(API + path, json=body, headers={"Idempotency-Key": key})
        assert response.status_code == status and response.json() == original, response.text
        assert _snapshot([owner]) == after_delete, "surviving receipt replay submitted duplicate work"


@pytest.mark.parametrize("operation", ("action", "write", "accept", "publish"))
def test_X04_successful_cached_resource_receipt_cannot_replay_under_another_owned_world(
        client, reader, demo, profile, operation):
    world, line = start(client, reader, demo, profile)
    owner = reader["user"]["id"]
    parent_id = line["headCheckpointId"]
    key = "successful-scope-" + operation
    if operation == "action":
        path = f"/worlds/{world['id']}/lines/{line['id']}/actions"
        body = {"message": "观察眼前的渡口", "kind": "observe",
                "expectedCheckpointId": parent_id, "turnId": "completed-scope-action"}
        original = post(client, path, body, key)
        process(original["taskId"])
        assert client.get(API + "/tasks/" + original["taskId"]).json()["state"] == "succeeded"
        assert read_line(client, world, line)["headCheckpointId"] != parent_id
        scope, status = "action:" + line["id"], 202
    elif operation in ("write", "accept"):
        write_body = {"chapterCount": 1, "expectedCheckpointId": parent_id}
        written = post(client, f"/worlds/{world['id']}/lines/{line['id']}/write",
                       write_body, key if operation == "write" else "scope-accept-preparation")
        process(written["taskId"])
        assert client.get(API + "/tasks/" + written["taskId"]).json()["state"] == "succeeded"
        candidate = client.get(API + f"/worlds/{world['id']}/turns/{written['turnId']}").json()
        assert candidate["chapters"], "scope replay test requires a completed real database candidate"
        if operation == "write":
            path = f"/worlds/{world['id']}/lines/{line['id']}/write"
            body, original = write_body, written
            scope, status = "write:" + line["id"], 202
        else:
            path = f"/worlds/{world['id']}/turns/{written['turnId']}/accept"
            body = {"chapterCount": 1, "expectedCheckpointId": parent_id}
            original = post(client, path, body, key)
            assert read_line(client, world, line)["headCheckpointId"] == original["checkpointId"]
            scope, status = "accept:" + written["turnId"], 200
    else:
        path = f"/worlds/{world['id']}/checkpoints/{parent_id}/publish"
        body = {}
        original = post(client, path, body, key)
        assert client.get(BASE + "/books/" + original["bookId"]).status_code == 200
        with SessionLocal() as db:
            assert db.get(Branch, original["branchId"]).head_revision_id == original["revisionId"]
        scope, status = "publish:" + parent_id, 200
    stored_receipt = _receipt_record(owner, scope, key)
    assert stored_receipt["result"] == original

    other = post(client, "/worlds", {"anchor": world["sourceAnchor"],
                 "name": "same_owner_other_scope"}, "other-world-for-cached-scope")
    process(other["taskId"])
    other_world = client.get(API + "/worlds/" + other["worldId"])
    assert other_world.status_code == 200 and other_world.json()["status"] == "active"
    assert other["worldId"] != world["id"]
    before = _snapshot([owner])
    # Positive control: the old completed receipt genuinely replays under its own world.
    replay = client.post(API + path, json=body, headers={"Idempotency-Key": key})
    assert replay.status_code == status and replay.json() == original, replay.text
    assert _snapshot([owner]) == before

    wrong_path = path.replace("/worlds/" + world["id"] + "/",
                              "/worlds/" + other["worldId"] + "/", 1)
    assert wrong_path != path
    for _ in range(2):
        denied = client.post(API + wrong_path, json=body, headers={"Idempotency-Key": key})
        assert denied.status_code == 404 and denied.json()["code"] == "RESOURCE_NOT_FOUND", denied.text
        assert world["id"] not in denied.text and line["id"] not in denied.text
        assert _snapshot([owner]) == before, "cached cross-world request changed states, calls, fees or files"
        assert _receipt_record(owner, scope, key) == stored_receipt


@pytest.mark.parametrize("deletion_point", ("before_sync_write", "before_journal_read"))
def test_X12_sync_of_received_journal_cannot_resurrect_deleted_world_and_marker_survives_cleanup(
        client, reader, demo, profile, deletion_point):
    world, line = start(client, reader, demo, profile)
    owner = reader["user"]["id"]
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.owner_id == owner,
            Task.payload["lineId"].astext == line["id"], Task.task_type == "world_turn"))
        assert task.state == "succeeded" and task.persistence_state == "saved"
        task_id = task.id
        calls = deepcopy(task.checkpoint["calls"])
        task.checkpoint = {**task.checkpoint, "worldDeniedPhrases": [DELETED_PRIVATE_PHRASE]}
        db.commit()
    received_path = private_path(owner, f"journal-{task_id}-{calls['generation:scene:1']['callId']}.json")
    received = json.loads(received_path.read_text("utf-8"))
    original_body = received["result"]["text"]
    assert len(original_body) > 100, "race fixture requires a genuine received narrator body"
    before_delete = _snapshot([owner])
    original_write, original_read = write_private, Path.read_text
    observed = {"deleted": False, "afterDelete": None}

    def delete_and_capture():
        response = client.delete(API + "/worlds/" + world["id"])
        assert response.status_code == 200 and response.json()["deleted"], response.text
        observed["deleted"] = True
        observed["afterDelete"] = _snapshot([owner])
        assert not received_path.exists()

    def write_after_received_body_was_read(owner_id, name, body):
        if (deletion_point == "before_sync_write" and not observed["deleted"] and
                owner_id == owner and name == received_path.name):
            value = json.loads(body)
            assert value["result"]["text"] == original_body and "syncedAt" in value
            delete_and_capture()
        return original_write(owner_id, name, body)

    def read_after_exists_then_deleted(path, *args, **kwargs):
        if (deletion_point == "before_journal_read" and not observed["deleted"] and
                path == received_path):
            assert path.exists()
            delete_and_capture()
        return original_read(path, *args, **kwargs)

    with patch("app.storage.write_private", write_after_received_body_was_read), \
            patch.object(Path, "read_text", read_after_exists_then_deleted):
        mark_synced(owner, task_id, calls)
    assert observed["deleted"], "sync race never reached the actual journal access"
    assert _snapshot([owner]) == observed["afterDelete"], "late sync changed deleted state or restored private files"
    assert observed["afterDelete"]["rows"]["usage"] == before_delete["rows"]["usage"]
    assert observed["afterDelete"]["rows"]["model_profiles"] == before_delete["rows"]["model_profiles"]
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task.state == "succeeded" and task.persistence_state == "saved"
        assert task.result == {"worldId": world["id"], "deleted": True}
        assert "worldStream" not in task.checkpoint and "worldDeniedPhrases" not in task.checkpoint
        assert DELETED_PRIVATE_PHRASE not in str(task.checkpoint)
        assert db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == task_id)) == 0
        assert db.scalar(select(func.count()).select_from(World).where(World.owner_id == owner)) == 0
    assert not received_path.exists()

    # Exercise the real orphan scavenger against an aged deletion marker, not a stub.
    marker = private_path(owner, "world-deleted-" + task_id + ".marker")
    assert marker.exists()
    old_time = (now() - timedelta(days=2)).timestamp()
    os.utime(marker, (old_time, old_time))
    clean_orphan_files()
    assert marker.exists() and marker.read_bytes() == b"deleted"
    mark_synced(owner, task_id, calls)
    with pytest.raises(Problem) as stopped:
        journal(owner, task_id, uid(), {"text": original_body})
    assert stopped.value.code == "TASK_CANCELLED"
    with pytest.raises(Problem) as blocked_stream:
        persist_stream(task_id, "generation:scene:1", original_body, final=True)
    assert blocked_stream.value.code == "TASK_CANCELLED"
    assert not list((settings.storage_path / owner).glob("journal-" + task_id + "-*.json"))
    assert _snapshot([owner]) == observed["afterDelete"], "scavenger removed the guard or a late body reappeared"
