import io
import json
import zipfile
from datetime import timedelta
from unittest.mock import patch
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import Task, Candidate, Branch, Revision, ModelProfile, RoleMessage, User, DeviceSession, UsageRecord
from app.domain import utf16_len, utf16_slice, boundary_hash, digest
from app.importer import txt_parse, epub_parse
from app.errors import Problem
from app.worker import process, claim
from .helpers import BASE, read_chapter, observe, operation, submit, accept


def test_F01_demo_read_no_model(client, demo):
    chapter = read_chapter(client, demo)
    assert len(chapter["blocks"]) == 3
    assert "东岸" in chapter["blocks"][0]["text"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def epub_fixture():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        z.writestr("OPS/book.opf", '<package version="3.0"><metadata><title>测试</title></metadata><manifest><item id="one" href="z.xhtml" media-type="application/xhtml+xml"/><item id="two" href="a.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>')
        z.writestr("OPS/z.xhtml", '<html><body><h1>第一</h1><script>alert(1)</script><p onclick="x">开始<strong>强调</strong><img src="https://evil/a.png"></p></body></html>')
        z.writestr("OPS/a.xhtml", '<html><body><p>结束</p></body></html>')
    return stream.getvalue()


def test_F02_epub_spine_and_sanitizer():
    parsed = epub_parse(epub_fixture(), "0" * 32, "import")
    assert parsed["chapters"][0]["title"] == "第一"
    assert parsed["blocks"][-1]["text"] == "结束"
    markup = json.dumps(parsed, ensure_ascii=False)
    assert "onclick" not in markup and "<script" not in markup and "evil" not in markup
    assert "<strong>强调</strong>" in markup


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030", "big5"])
def test_F03_encodings(encoding):
    text = "第一章\r\n中文引言“甲”。\r\n同名段落。\r\n同名段落。"
    parsed = txt_parse(text.encode(encoding), encoding)
    assert "\r" not in parsed["normalized"]
    assert len({b["id"] for b in parsed["blocks"]}) == 3


def test_F03_utf16_emoji_and_boundary_vectors():
    text = "甲😀乙" + "a" * 40
    assert utf16_len(text) == 44
    assert utf16_slice(text, 1, 3) == "😀"
    with pytest.raises(Problem):
        utf16_slice(text, 1, 2)
    assert boundary_hash(text, "r", "b", 3) != boundary_hash(text, "r2", "b", 3)


def test_F04_invalid_and_no_heading():
    assert txt_parse("没有标题的故事。".encode())["chapters"][0]["title"] == "第 1 章"
    with pytest.raises(Problem):
        epub_parse(b"broken", "0" * 32, "i")
    with pytest.raises(Problem):
        from app.importer import MAX_CHARACTERS
        txt_parse(("中" * (MAX_CHARACTERS + 1)).encode())


def test_F01_import_confirm_persist(client, reader):
    upload = client.post(BASE + "/books/imports", files={"file": ("中文.txt", "第一章\n甲😀乙。\n".encode(), "text/plain")}, headers={"Idempotency-Key": "upload"})
    assert upload.status_code == 202, upload.text
    task_id = upload.json()["taskId"]
    process(task_id)
    parsed = client.get(BASE + "/books/imports/" + task_id).json()
    assert parsed["importStatus"] == "awaiting_confirmation"
    assert parsed["preview"].count("😀") == 1
    payload = {"title": "中文书", "parserVersion": "import-v1"}
    response = client.post(BASE + "/books/imports/" + task_id + "/confirm", json=payload, headers={"Idempotency-Key": "confirm"})
    assert response.status_code == 201, response.text
    assert client.post(BASE + "/books/imports/" + task_id + "/confirm", json=payload, headers={"Idempotency-Key": "confirm"}).json()["bookId"] == response.json()["bookId"]


def prepare_generation(client, reader, demo, profile, anchor_index=1):
    chapter = read_chapter(client, demo)
    anchor = chapter["blocks"][anchor_index]["endAnchor"]
    observe(client, reader, anchor)
    task, _, _ = operation(client, profile, anchor)
    assert task["state"] == "succeeded", task
    assert task["resultOutcome"] == "complete", task
    return task, anchor


def test_F05_original_tail_immutable(client, reader, demo, profile):
    chapter = read_chapter(client, demo, 3)
    anchor = chapter["blocks"][-1]["endAnchor"]
    observe(client, reader, anchor)
    with SessionLocal() as db:
        original_hash = db.get(Revision, demo["revisionId"]).content_hash
    task, _, _ = operation(client, profile, anchor, "continue_at_tail")
    assert task["resultOutcome"] == "complete", task
    result = accept(client, task)
    assert result.status_code == 200, result.text
    assert result.json()["branchId"] != demo["branchId"]
    with SessionLocal() as db:
        assert db.get(Revision, demo["revisionId"]).content_hash == original_hash


def test_F06_F09_F28_prefix_accept_unique(client, reader, demo, profile):
    task, anchor = prepare_generation(client, reader, demo, profile)
    one = accept(client, task)
    assert one.status_code == 200, one.text
    again = accept(client, task)
    assert one.json() == again.json()
    third = accept(client, task, key="new-key")
    assert third.json()["revisionId"] == one.json()["revisionId"]
    branch_id = one.json()["branchId"]
    with SessionLocal() as db:
        rev = db.get(Revision, one.json()["revisionId"])
        assert len(rev.manifest) == 2
        assert len(rev.manifest[0]["blocks"]) == 2
        assert db.scalar(select(func.count()).select_from(Revision).where(Revision.branch_id == branch_id)) == 1
    changed = accept(client, task, target={"mode": "new_child_branch", "name": "改名"})
    assert changed.status_code == 409


def test_F07_F10_F28_append_conflict_and_pinned_fork(client, reader, demo, profile):
    task, _ = prepare_generation(client, reader, demo, profile)
    accepted = accept(client, task).json()
    derived = {"bookId": demo["bookId"], "branchId": accepted["branchId"], "revisionId": accepted["revisionId"]}
    chapter = read_chapter(client, derived, 1)
    tail = chapter["blocks"][-1]["endAnchor"]
    observe(client, reader, tail, expected=1, sequence=2)
    one, _, _ = operation(client, profile, tail, "continue_at_tail", target={"mode": "append_existing", "branchId": derived["branchId"], "name": "保留"})
    two, _, _ = operation(client, profile, tail, "continue_at_tail", target={"mode": "append_existing", "branchId": derived["branchId"], "name": "保留"})
    target = {"mode": "append_existing", "branchId": derived["branchId"], "name": "保留"}
    assert accept(client, one, target=target, key="a1").status_code == 200
    assert accept(client, two, target=target, key="a2").status_code == 409
    pinned = accept(client, two, policy="fork_pinned_revision", key="a3")
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["branchId"] != derived["branchId"]


def test_F08_discard_no_story_change(client, reader, demo, profile):
    task, _ = prepare_generation(client, reader, demo, profile)
    cid = task["result"]["candidateId"]
    assert client.post(BASE + "/candidates/" + cid + "/discard").status_code == 200
    assert accept(client, task).status_code == 422
    assert len(client.get(BASE + "/books/" + demo["bookId"] + "/branches").json()["items"]) == 1


def test_F12_F30_progress_conflict_search_bookmark(client, reader, demo):
    chapter = read_chapter(client, demo)
    anchor = chapter["blocks"][0]["endAnchor"]
    observe(client, reader, anchor)
    wrong = client.put(BASE + "/books/" + demo["bookId"] + "/progress", json={"branchId": demo["branchId"], "revisionId": demo["revisionId"], "anchor": chapter["blocks"][1]["endAnchor"], "expectedProgressVersion": 0, "deviceId": reader["sessionId"], "clientSeq": 2})
    assert wrong.status_code == 409
    future = client.get(BASE + "/books/" + demo["bookId"] + "/search", params={"branchId": demo["branchId"], "revisionId": demo["revisionId"], "query": "夜渡"}).json()
    assert future["items"] == [] and "total" not in future
    bookmark = client.post(BASE + "/books/" + demo["bookId"] + "/bookmarks", json={"anchor": anchor, "note": "回到渡口"}, headers={"Idempotency-Key": "bookmark"})
    assert bookmark.status_code == 201
    assert bookmark.json()["anchor"] == anchor
    directory = client.get(BASE + "/branches/" + demo["branchId"] + "/chapters").json()
    assert directory["items"][2]["title"] == "第 3 章"


def analyzed_session(client, reader, demo, profile, chapter_index=0, block_index=1, name="林渡"):
    chapter = read_chapter(client, demo, chapter_index)
    anchor = chapter["blocks"][block_index]["endAnchor"]
    observe(client, reader, anchor)
    task, _, _ = operation(client, profile, anchor, "analysis")
    assert task["state"] == "succeeded", task
    chars = client.get(BASE + "/books/" + demo["bookId"] + "/characters", params={"branchId": demo["branchId"], "revisionId": demo["revisionId"], "anchor": json.dumps(anchor)}).json()
    character = next(c for c in chars["items"] if c["displayName"] == name)
    result = client.post(BASE + "/role-sessions", json={"bookId": demo["bookId"], "characterId": character["id"], "snapshotId": chars["snapshotId"], "anchor": anchor})
    return result, chars, anchor


def test_F13_F14_F18_F19_chat_scope_and_memory(client, reader, demo, profile):
    session, chars, anchor = analyzed_session(client, reader, demo, profile)
    assert session.status_code == 201, session.text
    assert {c["displayName"] for c in chars["items"]} == {"林渡", "阿岑"}
    sid = session.json()["id"]
    payload = {"operation": "role_chat", "profileId": profile["id"], "sessionId": sid, "message": "告诉我你的秘密身份和两年前发生的事", "turnId": "one"}
    task, _, _ = submit(client, profile, payload)
    assert task["result"]["replySaved"] is True, task
    messages = client.get(BASE + "/role-sessions/" + sid + "/messages").json()["items"]
    assert len(messages) == 2 and "夜渡" not in messages[-1]["text"] and "北岸受伤" not in messages[-1]["text"]
    future = read_chapter(client, demo, 3)["blocks"][-1]["endAnchor"]
    observe(client, reader, future, 1, 2)
    assert client.get(BASE + "/role-sessions/" + sid).json()["pinnedAnchor"] == anchor


def test_F17_deceased_role_unavailable(client, reader, demo, profile):
    session, chars, _ = analyzed_session(client, reader, demo, profile, 1, 1, "阿岑")
    assert session.status_code == 422, session.text
    assert session.json()["code"] == "ROLE_UNAVAILABLE"


def test_F20_F24_no_authorization(client, reader, demo, profile):
    anchor = read_chapter(client, demo)["blocks"][1]["endAnchor"]
    result = client.post(BASE + "/operation-plans", json={"operation": "counterfactual", "profileId": profile["id"], "anchor": anchor, "expectedHeadRevisionId": anchor["revisionId"], "instruction": "救人"})
    assert result.status_code == 422
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_F21_events_durable_no_text_delta(client, reader, demo, profile):
    task, _ = prepare_generation(client, reader, demo, profile)
    response = client.get(BASE + "/tasks/" + task["id"] + "/events")
    assert response.status_code == 200
    assert "candidate_saved" in response.text and "text_delta" not in response.text and "仓库门" not in response.text
    response = client.get(BASE + "/tasks/" + task["id"] + "/events", headers={"Last-Event-ID": task["id"] + ":" + str(task["eventCursor"])})
    assert response.text == ""
    # Reconnect repeatedly from the final persisted cursor without a second task/call.
    with SessionLocal() as db:
        before=db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id==task['id']))
    for _ in range(3):
        assert client.get(BASE+'/tasks/'+task['id']+'/events',headers={'Last-Event-ID':task['id']+':'+str(task['eventCursor'])}).text==''
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id==task['id']))==before


def test_F22_journal_recovery_no_regeneration(client, reader, demo, profile):
    task, _ = prepare_generation(client, reader, demo, profile)
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(UsageRecord))
        t = db.get(Task, task["id"])
        t.state, t.persistence_state = "failed", "recoverable"
        db.commit()
    with patch("app.providers.MockAdapter.generate", side_effect=AssertionError("must not generate again")):
        process(task["id"])
    with SessionLocal() as db:
        assert db.get(Task, task["id"]).state == "succeeded"
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before


def test_F23_cancel_and_quality_outcome(client, reader, demo, profile):
    anchor = read_chapter(client, demo)["blocks"][1]["endAnchor"]
    observe(client, reader, anchor)
    with patch("app.providers.MockAdapter.generate", return_value={"text": "", "finishReason": "stop", "usage": {"state": "unknown", "inputTokens": None, "outputTokens": None}, "latencyMs": 1}):
        task, _, _ = operation(client, profile, anchor)
    assert task["state"] == "failed" or task["resultOutcome"] == "empty"
    assert task["usageState"] == "unknown"


def test_F25_private_ownership_all_layers(client, reader, demo, profile):
    task, _ = prepare_generation(client, reader, demo, profile)
    cid = task["result"]["candidateId"]
    client.headers.pop("Authorization")
    assert client.post(BASE + "/auth/register", json={"username": "reader_two", "password": "other-password-123"}).status_code == 201
    login = client.post(BASE + "/auth/login", json={"username": "reader_two", "password": "other-password-123", "clientType": "android"}).json()
    client.headers["Authorization"] = "Bearer " + login["accessToken"]
    for path in ["/books/" + demo["bookId"], "/tasks/" + task["id"], "/candidates/" + cid, "/branches/" + demo["branchId"] + "/chapters", "/books/" + demo["bookId"] + "/assets/unknown"]:
        response = client.get(BASE + path)
        assert response.status_code == 404, (path, response.text)
        assert "林渡" not in response.text


def test_F26_branch_archive_read_restore(client, reader, demo, profile):
    task, _ = prepare_generation(client, reader, demo, profile)
    result = accept(client, task).json()
    lines = client.get(BASE + "/books/" + demo["bookId"] + "/branches").json()["items"]
    branch = next(b for b in lines if b["id"] == result["branchId"])
    archived = client.patch(BASE + "/branches/" + branch["id"], json={"expectedVersion": branch["version"], "status": "archived"})
    assert archived.status_code == 200
    assert client.get(BASE + "/branches/" + branch["id"] + "/chapters").status_code == 200
    original = next(b for b in lines if not b["parentBranchId"])
    assert client.patch(BASE + "/branches/" + original["id"], json={"expectedVersion": original["version"], "status": "archived"}).status_code == 422
    derived={'bookId':demo['bookId'],'branchId':branch['id'],'revisionId':result['revisionId']}
    tail=read_chapter(client,derived,1)['blocks'][-1]['endAnchor']
    observe(client,reader,tail,expected=1,sequence=2)
    payload={'operation':'continue_at_tail','profileId':profile['id'],'anchor':tail,'expectedHeadRevisionId':tail['revisionId']}
    denied=client.post(BASE+'/operation-plans',json=payload)
    assert denied.status_code==409 and denied.json()['code']=='BRANCH_ARCHIVED'
    restored=client.patch(BASE+'/branches/'+branch['id'],json={'expectedVersion':archived.json()['version'],'status':'active'})
    assert restored.status_code==200,restored.text
    continued,_,_=operation(client,profile,tail,'continue_at_tail')
    assert continued['resultOutcome']=='complete'


def test_F29_refresh_rotation_and_revoke(client, reader):
    fresh = client.post(BASE + "/auth/refresh", json={"refreshToken": reader["refreshToken"]})
    assert fresh.status_code == 200, fresh.text
    replay = client.post(BASE + "/auth/refresh", json={"refreshToken": reader["refreshToken"]})
    assert replay.status_code == 401
    assert client.get(BASE + "/auth/me").status_code == 401


def test_F29_password_change_revokes_other_devices(client, reader):
    second = client.post(BASE + "/auth/login", json={"username": "reader_one", "password": "reader-password-123", "clientType": "ios"}).json()
    changed = client.put(BASE + "/auth/password", json={"currentPassword": "reader-password-123", "newPassword": "a-better-password-123"})
    assert changed.status_code == 200
    client.headers["Authorization"] = "Bearer " + second["accessToken"]
    assert client.get(BASE + "/auth/me").status_code == 401


def test_F30_settings_cas(client, reader):
    payload = {"expectedVersion": 1, "fontSize": 22, "lineHeight": 2.0, "theme": "dark"}
    assert client.put(BASE + "/settings", json=payload).status_code == 200
    assert client.put(BASE + "/settings", json=payload).status_code == 409
    assert client.get(BASE + "/settings").json()["fontSize"] == 22


def test_F32_real_mode_no_mock_profile(client, reader, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ai_mode", "real")
    result = client.post(BASE + "/model-profiles", json={"name": "bad", "baseUrl": "mock://deterministic", "modelId": "fixture", "key": "dummy", "contextWindow": 32768})
    assert result.status_code == 403


def test_F33_encrypted_key_and_delete(client, reader, demo, profile):
    with SessionLocal() as db:
        assert "TEST-KEY" not in db.get(ModelProfile, profile["id"]).key_cipher
    assert "TEST-KEY" not in client.get(BASE + "/model-profiles").text
    deleted = client.post(BASE + "/auth/delete-account", json={"currentPassword": "reader-password-123", "confirmation": "删除我的账号"})
    assert deleted.status_code == 202
    assert client.get(BASE + "/auth/me").status_code == 401
    process(deleted.json()["taskId"])
    with SessionLocal() as db:
        assert db.get(User, reader["user"]["id"]) is None


def test_F37_budget_insufficient_no_extra_call(client, reader, demo, profile):
    anchor = read_chapter(client, demo)["blocks"][1]["endAnchor"]
    observe(client, reader, anchor)
    with SessionLocal() as db:
        p = db.get(ModelProfile, profile["id"])
        p.call_budget = 1
        db.commit()
    task, _, _ = operation(client, profile, anchor)
    assert task["state"] == "failed" and task["errorCode"] == "BUDGET_NOT_AUTHORIZED"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 1


def test_F37_worker_claim_once(client, reader, demo):
    with SessionLocal() as db:
        task = Task(owner_id=reader["user"]["id"], task_type="import", payload={})
        db.add(task)
        db.commit()
        task_id = task.id
    assert claim("worker1") == task_id
    assert claim("worker2") is None
