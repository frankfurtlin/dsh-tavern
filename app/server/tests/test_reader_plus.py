from datetime import timedelta
from sqlalchemy import select
from app.db import SessionLocal
from app.models import ReadingActivity, UserSettings
from .helpers import BASE, read_chapter


def test_default_model_is_owner_scoped_and_does_not_change_authorizations(client, reader, profile):
    old_settings = client.get(BASE + "/settings").json()
    first = client.get(BASE + "/model-profiles").json()
    assert first["defaultProfileId"] == profile["id"]
    assert first["items"][0]["isDefault"] is True
    second = client.post(BASE + "/model-profiles", json={"name": "other", "baseUrl": "mock://deterministic", "modelId": "other", "key": "TEST-NOT-REAL", "contextWindow": 32768}).json()
    assert client.put(BASE + f"/model-profiles/{second['id']}/default").status_code == 200
    listed = client.get(BASE + "/model-profiles").json()
    assert listed["defaultProfileId"] == second["id"]
    assert sum(p["isDefault"] for p in listed["items"]) == 1
    assert client.get(BASE + "/settings").json() == old_settings
    assert next(p for p in listed["items"] if p["id"] == second["id"])["version"] == 1
    assert client.patch(BASE + f"/model-profiles/{second['id']}", json={"expectedVersion": 1, "status": "revoked"}).status_code == 200
    assert client.get(BASE + "/model-profiles").json()["defaultProfileId"] == profile["id"]
    assert client.put(BASE + f"/model-profiles/{second['id']}/default").status_code == 422
    other_owner(client)
    assert client.put(BASE + f"/model-profiles/{profile['id']}/default").status_code == 404
    assert client.get(BASE + "/model-profiles").json()["items"] == []


def other_owner(client):
    assert client.post(BASE + "/auth/register", json={"username": "reader_plus_other", "password": "reader-other-password-123"}).status_code == 201
    auth = client.post(BASE + "/auth/login", json={"username": "reader_plus_other", "password": "reader-other-password-123", "clientType": "android"}).json()
    client.headers["Authorization"] = "Bearer " + auth["accessToken"]


def test_bookshelf_favorites_tags_version_and_owner(client, reader, demo):
    path = BASE + "/books/" + demo["bookId"]
    book = client.get(path).json()
    assert book["favorite"] is False and book["tags"] == []
    edit = client.patch(path, json={"expectedVersion": book["version"], "favorite": True, "tags": [" 悬疑 ", "重读", "悬疑"]})
    assert edit.status_code == 200, edit.text
    assert edit.json()["tags"] == ["悬疑", "重读"] and edit.json()["favorite"] is True
    assert client.patch(path, json={"expectedVersion": book["version"], "favorite": False}).status_code == 409
    assert client.patch(path, json={"expectedVersion": edit.json()["version"], "favorite": "false"}).status_code == 422
    assert client.patch(path, json={"expectedVersion": edit.json()["version"], "tags": ["x" * 21]}).status_code == 422
    assert client.get(BASE + "/books").json()["items"][0]["favorite"] is True
    other_owner(client)
    assert client.patch(path, json={"expectedVersion": 2, "favorite": False}).status_code == 404


def test_notes_preserve_anchor_edit_conflict_and_private_notebook(client, reader, demo):
    anchor = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    path = BASE + "/books/" + demo["bookId"] + "/bookmarks"
    note = "我的想法：这段伏笔值得重读。\n" * 40
    first = client.post(path, json={"anchor": anchor, "note": note}, headers={"Idempotency-Key": "note-test"})
    assert first.status_code == 201, first.text
    replay = client.post(path, json={"anchor": anchor, "note": note}, headers={"Idempotency-Key": "note-test"})
    assert first.json() == replay.json()
    item = first.json()
    all_notes = client.get(BASE + "/notebook").json()["items"]
    assert len(all_notes) == 1 and all_notes[0]["anchor"] == anchor and all_notes[0]["note"] == note
    assert all_notes[0]["bookTitle"] and all_notes[0]["location"]
    target = path + "/" + item["id"]
    assert client.get(target).json()["note"] == note
    edited = client.patch(target, json={"expectedVersion": 1, "note": "修改后的想法"})
    assert edited.status_code == 200 and edited.json()["version"] == 2
    assert edited.json()["anchor"] == anchor
    assert client.patch(target, json={"expectedVersion": 1, "note": "迟到的覆盖"}).status_code == 409
    assert client.patch(target, json={"expectedVersion": 2, "note": "x" * 2001}).status_code == 422
    other_owner(client)
    assert client.get(BASE + "/notebook").json()["items"] == []
    assert client.patch(target, json={"expectedVersion": 2, "note": "越权"}).status_code == 404
    assert client.get(target).status_code == 404
    assert client.delete(target).status_code == 404


def test_activity_idempotence_wall_time_bounds_and_goals(client, reader, demo):
    event = {"eventId": "reader-plus-event-0001", "bookId": demo["bookId"], "branchId": demo["branchId"], "revisionId": demo["revisionId"], "seconds": 30}
    first = client.post(BASE + "/reading-activity", json=event)
    assert first.status_code == 201 and first.json()["recordedSeconds"] == 30
    assert client.post(BASE + "/reading-activity", json=event).json() == first.json()
    assert client.post(BASE + "/reading-activity", json={**event, "seconds": 31}).status_code == 409
    assert client.post(BASE + "/reading-activity", json={**event, "eventId": "reader-plus-event-0002", "seconds": 61}).status_code == 422
    assert client.post(BASE + "/reading-activity", json={**event, "eventId": "reader-plus-event-0002", "seconds": True}).status_code == 422
    second = client.post(BASE + "/reading-activity", json={**event, "eventId": "reader-plus-event-0002"})
    assert second.status_code == 201 and second.json()["recordedSeconds"] < 30
    with SessionLocal() as db:
        for row in db.scalars(select(ReadingActivity)):
            row.created_at -= timedelta(seconds=61)
        db.commit()
    third = client.post(BASE + "/reading-activity", json={**event, "eventId": "reader-plus-event-0003"})
    assert third.json()["recordedSeconds"] == 30
    stats = client.get(BASE + "/reading-stats?offsetMinutes=480").json()
    assert stats["totalSeconds"] == first.json()["recordedSeconds"] + second.json()["recordedSeconds"] + 30
    assert stats["streakDays"] == 1 and len(stats["week"]) == 7 and stats["bookCount"] == 1
    goal = client.put(BASE + "/reading-goal", json={"expectedVersion": 1, "minutes": 30})
    assert goal.status_code == 200 and goal.json()["goalMinutes"] == 30
    assert client.put(BASE + "/reading-goal", json={"expectedVersion": 1, "minutes": 60}).status_code == 409
    assert client.put(BASE + "/reading-goal", json={"expectedVersion": 2, "minutes": 0}).status_code == 422
    assert client.get(BASE + "/reading-stats").json()["goalMinutes"] == 30
    other_owner(client)
    assert client.get(BASE + "/reading-stats").json()["totalSeconds"] == 0
    assert client.post(BASE + "/reading-activity", json={**event, "eventId": "reader-plus-event-0004"}).status_code == 404


def test_stats_bucket_uses_local_day_without_other_owner_data(client, reader, demo):
    event = {"eventId": "reader-plus-zone-0001", "bookId": demo["bookId"], "branchId": demo["branchId"], "revisionId": demo["revisionId"], "seconds": 60}
    assert client.post(BASE + "/reading-activity", json=event).status_code == 201
    from app.models import now
    with SessionLocal() as db:
        row = db.scalar(select(ReadingActivity).where(ReadingActivity.event_id == event["eventId"]))
        # An interval at yesterday's UTC 20:00 belongs to today's Shanghai day.
        today_shanghai = (now() + timedelta(hours=8)).date()
        from datetime import datetime, timezone
        row.created_at = datetime.combine(today_shanghai, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=4)
        db.commit()
    assert client.get(BASE + "/reading-stats?offsetMinutes=480").json()["todaySeconds"] == 60
    assert client.get(BASE + "/reading-stats?offsetMinutes=2000").status_code == 422
