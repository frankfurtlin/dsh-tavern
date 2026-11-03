"""Positive line lifecycle cases on the isolated PostgreSQL/mock backend."""
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Branch, UsageRecord
from app.world.models import World
from .test_world import API, fast_fixture_stream, post, read_line, start


def edit(client, world, line, **values):
    response = client.patch(API + f"/worlds/{world['id']}/lines/{line['id']}",
                            json={"expectedVersion": line["version"], **values})
    assert response.status_code == 200, response.text
    return response.json()


def fork(client, world, line):
    return post(client, f"/worlds/{world['id']}/fork",
                {"checkpointId": line["headCheckpointId"], "mode": "continue", "name": "保留的子线"}, "line-fork")


def delete(client, world, line):
    response = client.delete(API + f"/worlds/{world['id']}/lines/{line['id']}")
    assert response.status_code == 200, response.text


def active(client, world):
    return client.get(API + "/worlds/" + world["id"]).json()["activeLineId"]


def calls():
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(UsageRecord))


def test_delete_current_keeps_archived_line_accessible_and_restore_reuses_it(client, reader, demo, profile):
    world, parent = start(client, reader, demo, profile)
    child = fork(client, world, parent)
    archived = edit(client, world, parent, status="archived")
    before = calls()
    delete(client, world, child)
    assert active(client, world) == parent["id"]
    assert read_line(client, world, parent) == archived
    blocked = client.post(API + f"/worlds/{world['id']}/lines/{parent['id']}/actions", json={
        "message": "不能向归档原线写入", "kind": "action", "expectedCheckpointId": parent["headCheckpointId"], "turnId": "archived-action"},
        headers={"Idempotency-Key": "archived-action"})
    assert blocked.status_code == 409 and blocked.json()["code"] == "WORLD_ARCHIVED"
    restored = edit(client, world, archived, status="active")
    assert active(client, world) == parent["id"] and restored["head"] == parent["head"]
    assert calls() == before


def test_restore_legacy_line_without_active_pointer_repairs_list_entry(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    archived = edit(client, world, line, status="archived")
    with SessionLocal() as db:
        # This is an own synthetic fixture for records produced by the old delete path.
        db.get(World, world["id"]).active_line_id = None
        db.commit()
    before = calls()
    restored = edit(client, world, archived, status="active")
    assert active(client, world) == line["id"]
    assert restored["head"] == line["head"] and calls() == before


def test_delete_parent_keeps_descendant_history_reading_and_source_frozen(client, reader, demo, profile):
    world, parent = start(client, reader, demo, profile)
    child = fork(client, world, parent)
    before = calls()
    delete(client, world, parent)
    assert active(client, world) == child["id"] and read_line(client, world, child) == child
    assert client.get(API + f"/worlds/{world['id']}/lines/{parent['id']}").status_code == 404
    history = client.get(API + f"/worlds/{world['id']}/lines/{child['id']}/history").json()["items"]
    shared = next(cp for cp in history if cp["id"] == parent["headCheckpointId"])
    assert shared["lineId"] == parent["id"] and shared["text"] == parent["head"]["text"]
    positioned = edit(client, world, child, readingPosition={"checkpointId": shared["id"], "scroll": 25})
    assert positioned["readingPosition"]["checkpointId"] == shared["id"]
    with SessionLocal() as db:
        assert db.get(Branch, demo["branchId"]).head_revision_id == demo["revisionId"]
    assert calls() == before


def test_delete_current_prefers_live_line_and_does_not_change_other_head(client, reader, demo, profile):
    world, parent = start(client, reader, demo, profile)
    child = fork(client, world, parent)
    before = calls()
    delete(client, world, child)
    assert active(client, world) == parent["id"] and read_line(client, world, parent) == parent
    assert calls() == before


def test_delete_last_line_keeps_world_prepared_and_original_reader(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    before = calls()
    delete(client, world, line)
    info = client.get(API + "/worlds/" + world["id"]).json()
    assert info["status"] == "active" and info["activeLineId"] is None and info["characters"]
    assert client.get(API + f"/worlds/{world['id']}/graph").json() == {"lines": [], "nodes": []}
    with SessionLocal() as db:
        assert db.get(Branch, demo["branchId"]).head_revision_id == demo["revisionId"]
    assert calls() == before
