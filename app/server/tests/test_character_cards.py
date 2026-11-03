"""Full-book indexing never unlocks unread names, facts, or chat history."""
import json
from copy import deepcopy
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import Task, Progress, UsageRecord, KnowledgeSnapshot, UserSettings, RoleSession
from app.worker import process
from app.character_cards import filter_data, cards
from app.knowledge import visible_characters, require_role_character
from app.errors import Problem
from .helpers import BASE, read_chapter, observe
from .test_story_flow import finish


def index(client, demo, key="index", retry=False):
    response = client.post(f"{BASE}/books/{demo['bookId']}/character-index", json={"branchId": demo["branchId"], "retry": retry},
        headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    run = response.json()
    if run.get("indexTaskId") and run["indexState"] != "ready":
        finish({"id": run["indexTaskId"]})
    return run


def card_list(client, demo, anchor):
    response = client.get(f"{BASE}/books/{demo['bookId']}/character-cards", params={"branchId": demo["branchId"], "anchor": json.dumps(anchor)})
    assert response.status_code == 200, response.text
    return response


def chat(client, demo, anchor, character, key="chat"):
    response = client.post(f"{BASE}/books/{demo['bookId']}/character-chats", json={"anchor": anchor, "characterId": character["id"]},
        headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    return response.json()


def test_background_index_is_finite_and_does_not_create_progress(client, reader, demo, profile):
    run = index(client, demo)
    with SessionLocal() as db:
        parent = db.get(Task, run["indexTaskId"])
        assert parent.state == "succeeded", parent.error_code
        assert parent.payload["budget"]["maxCalls"] <= 100
        assert db.scalar(select(func.count()).select_from(Progress)) == 0
        calls = db.scalar(select(func.count()).select_from(UsageRecord))
    duplicate = index(client, demo, "index-again")
    assert duplicate["indexState"] == "ready" and duplicate["cached"] is True
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == calls


def test_unread_index_never_shows_future_names_or_quotes(client, reader, demo, profile):
    index(client, demo)
    anchor = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, anchor)
    response = card_list(client, demo, anchor)
    assert {c["name"] for c in response.json()["items"]} == {"林渡", "阿岑"}
    assert "梅舟" not in response.text and "夜渡" not in response.text and "两年前" not in response.text
    assert all(c["available"] for c in response.json()["items"])
    with SessionLocal() as db:
        progress = db.scalar(select(Progress))
        assert progress.max_observed_anchor == anchor


def test_cards_reject_unobserved_anchor_even_after_full_index(client, reader, demo, profile):
    index(client, demo)
    anchor = read_chapter(client, demo, 3)["blocks"][-1]["endAnchor"]
    response = client.get(f"{BASE}/books/{demo['bookId']}/character-cards", params={"branchId": demo["branchId"], "anchor": json.dumps(anchor)})
    assert response.status_code == 422 and response.json()["code"] == "AI_CONTEXT_NOT_READY"


def test_dead_card_has_clear_reason_and_no_time_selector(client, reader, demo, profile):
    index(client, demo)
    anchor = read_chapter(client, demo, 1)["blocks"][0]["endAnchor"]
    observe(client, reader, anchor)
    selected = next(c for c in card_list(client, demo, anchor).json()["items"] if c["name"] == "阿岑")
    assert not selected["available"] and selected["reasonCode"] == "ROLE_DECEASED"
    assert "离世" in selected["reason"]
    response = client.post(f"{BASE}/books/{demo['bookId']}/character-chats", json={"anchor": anchor, "characterId": selected["id"]},
        headers={"Idempotency-Key": "dead-chat"})
    assert response.status_code == 422 and response.json()["code"] == "ROLE_UNAVAILABLE"


def test_full_mode_requires_explicit_versioned_preference_and_rechecked_send(client, reader, demo, profile):
    index(client, demo)
    anchor = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, anchor)
    assert client.get(BASE + "/character-preferences").json() == {"mode": "progress", "version": 1}
    updated = client.put(BASE + "/character-preferences", json={"mode": "full", "expectedVersion": 1})
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert client.put(BASE + "/character-preferences", json={"mode": "progress", "expectedVersion": 1}).status_code == 409
    selected = next(c for c in card_list(client, demo, anchor).json()["items"] if c["name"] == "梅舟")
    session = chat(client, demo, anchor, selected)
    assert session["knowledgeMode"] == "full" and session["followProgress"] is True
    assert session["pinnedAnchor"] != anchor
    client.put(BASE + "/character-preferences", json={"mode": "progress", "expectedVersion": 2})
    denied = client.post(f"{BASE}/role-sessions/{session['id']}/send", json={"message": "你的身份？", "turnId": "changed-mode"},
        headers={"Idempotency-Key": "changed-mode"})
    assert denied.status_code == 409 and denied.json()["code"] == "ROLE_SCOPE_CHANGED"
    assert client.get(f"{BASE}/role-sessions/{session['id']}/messages").status_code == 409
    assert client.get(BASE + "/role-sessions").json()["items"] == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.operation == "generation")) == 0
        row = db.scalar(select(UserSettings))
        assert row.version == row.reading_goal_version == 1


def test_one_click_chat_and_send_have_no_extra_analysis_or_duplicate_charges(client, reader, demo, profile):
    index(client, demo)
    anchor = read_chapter(client, demo)["blocks"][1]["endAnchor"]
    observe(client, reader, anchor)
    selected = next(c for c in card_list(client, demo, anchor).json()["items"] if c["name"] == "林渡")
    session = chat(client, demo, anchor, selected)
    assert chat(client, demo, anchor, selected, "same-person-again")["id"] == session["id"]
    sent = client.post(f"{BASE}/role-sessions/{session['id']}/send", json={"message": "你此刻在想什么？", "turnId": "hello"},
        headers={"Idempotency-Key": "send"})
    assert sent.status_code == 202, sent.text
    task_id = sent.json()["taskId"]
    duplicate = client.post(f"{BASE}/role-sessions/{session['id']}/send", json={"message": "你此刻在想什么？", "turnId": "hello"},
        headers={"Idempotency-Key": "send-new-transport-key"})
    assert duplicate.json()["taskId"] == task_id
    process(task_id)
    result = client.get(BASE + "/tasks/" + task_id).json()
    assert result["state"] == "succeeded" and result["result"]["replySaved"], result
    changed = client.post(f"{BASE}/role-sessions/{session['id']}/send", json={"message": "另一条消息", "turnId": "hello"},
        headers={"Idempotency-Key": "changed-message"})
    assert changed.status_code == 409
    replay = client.post(f"{BASE}/role-sessions/{session['id']}/send", json={"message": "你此刻在想什么？", "turnId": "hello"},
        headers={"Idempotency-Key": "replay-saved-turn"})
    assert replay.json()["taskId"] == task_id
    with SessionLocal() as db:
        usage = list(db.scalars(select(UsageRecord).where(UsageRecord.task_id == task_id)))
        assert len(usage) == 2 and {u.operation for u in usage} == {"generation", "validation"}


def test_backward_reading_does_not_reuse_future_conversation_history(client, reader, demo, profile):
    index(client, demo)
    later = read_chapter(client, demo, 3)["blocks"][-1]["endAnchor"]
    observe(client, reader, later)
    selected = next(c for c in card_list(client, demo, later).json()["items"] if c["name"] == "林渡")
    latest = chat(client, demo, later, selected, "later-chat")
    earlier = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    earlier_card = next(c for c in card_list(client, demo, earlier).json()["items"] if c["name"] == "林渡")
    early_session = chat(client, demo, earlier, earlier_card, "earlier-chat")
    assert latest["id"] != early_session["id"]
    assert client.get(f"{BASE}/role-sessions/{early_session['id']}/messages").json()["items"] == []
    assert early_session["pinnedAnchor"] == earlier


def test_earlier_quotes_cannot_support_unread_fact_values(client, reader, demo, profile):
    index(client, demo)
    first = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, first)
    with SessionLocal() as db:
        source = db.scalar(select(KnowledgeSnapshot).where(KnowledgeSnapshot.revision_id == demo["revisionId"]))
        from app.models import Revision
        revision = db.get(Revision, demo["revisionId"])
        source.data = deepcopy(source.data)
        subject = next(c for c in source.data["characters"] if c["displayName"] == "林渡")
        early_evidence = next(e for e in source.data["evidence"] if e["anchor"]["blockId"] == first["blockId"])
        source.data["facts"].append({"id": "fake-future-value", "subjectId": subject["id"], "predicate": "secret_identity", "value": "夜渡",
            "status": "confirmed", "evidenceIds": [early_evidence["id"]], "readerRevealAnchor": early_evidence["anchor"], "knownBy": [], "knowledge": []})
        filtered = filter_data(source, first, revision)
        assert all(f["id"] != "fake-future-value" for f in filtered["facts"])
        assert "夜渡" not in json.dumps(filtered, ensure_ascii=False)


def test_uncertain_and_unusual_death_quotes_do_not_default_to_alive(client, reader, demo, profile):
    index(client, demo)
    from app.models import Revision
    with SessionLocal() as db:
        source = db.scalar(select(KnowledgeSnapshot).where(KnowledgeSnapshot.revision_id == demo["revisionId"]))
        revision = db.get(Revision, demo["revisionId"])
        character = next(c for c in source.data["characters"] if c["displayName"] == "林渡")
        evidence = next(e for e in source.data["evidence"] if e["id"] in character["evidenceIds"])
        for value, status, quote in [(None, "uncertain", "林渡的生死尚不明确。"), (False, "confirmed", "林渡气息断绝。")]:
            altered = deepcopy(source.data)
            altered["evidence"] = [{**evidence, "quote": quote}]
            altered["characters"] = [{**character, "evidenceIds": [evidence["id"]]}]
            altered["facts"] = [{"id": "life", "subjectId": character["id"], "predicate": "alive", "value": value, "status": status,
                "temporalRelation": "at_scope", "evidenceIds": [evidence["id"]], "readerRevealAnchor": evidence["anchor"], "knownBy": [], "knowledge": []}]
            synthetic = KnowledgeSnapshot(data=altered)
            filtered = KnowledgeSnapshot(data=filter_data(synthetic, source.boundary, revision), completeness="complete")
            assert not visible_characters(filtered)[0]["available"]
            with pytest.raises(Problem):
                require_role_character(filtered, character["id"])


def test_explicit_revival_remains_available_and_future_scope_is_not_imported(client, reader, demo, profile):
    index(client, demo)
    from app.models import Revision
    with SessionLocal() as db:
        source = db.scalar(select(KnowledgeSnapshot).where(KnowledgeSnapshot.revision_id == demo["revisionId"]))
        revision = db.get(Revision, demo["revisionId"])
        character = next(c for c in source.data["characters"] if c["displayName"] == "林渡")
        evidence = next(e for e in source.data["evidence"] if e["id"] in character["evidenceIds"])
        altered = deepcopy(source.data)
        altered["evidence"] = [{**evidence, "quote": "林渡死了，随后明确复活，林渡又活过来。"}]
        altered["characters"] = [{**character, "evidenceIds": [evidence["id"]]}]
        base = {"subjectId": character["id"], "status": "confirmed", "temporalRelation": "at_scope", "evidenceIds": [evidence["id"]],
            "readerRevealAnchor": evidence["anchor"], "knownBy": [], "knowledge": []}
        altered["facts"] = [{**base, "id": "death", "predicate": "alive", "value": False},
            {**base, "id": "live", "predicate": "alive", "value": True}, {**base, "id": "revival", "predicate": "revived", "value": True}]
        filtered = KnowledgeSnapshot(data=filter_data(KnowledgeSnapshot(data=altered), source.boundary, revision), completeness="complete")
        assert visible_characters(filtered)[0]["available"]
        first = read_chapter(client, demo)["blocks"][0]["endAnchor"]
        future = {**base, "id": "future-claim", "predicate": "alive", "value": False, "readerRevealAnchor": source.boundary}
        altered["facts"] = [future]
        assert filter_data(KnowledgeSnapshot(data=altered), first, revision)["facts"] == []
        altered["facts"] = [{**future, "readerRevealAnchor": {**evidence["anchor"], "branchId": "different-branch"}}]
        assert filter_data(KnowledgeSnapshot(data=altered), source.boundary, revision)["facts"] == []


def test_character_endpoints_are_owned_and_no_provider_does_not_leak_another_book(client, reader, demo, profile):
    index(client, demo)
    anchor = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, anchor)
    selected = card_list(client, demo, anchor).json()["items"][0]
    session = chat(client, demo, anchor, selected)
    client.post(BASE + "/auth/register", json={"username": "another_card_reader", "password": "reader-password-123"})
    login = client.post(BASE + "/auth/login", json={"username": "another_card_reader", "password": "reader-password-123"}).json()
    client.headers["Authorization"] = "Bearer " + login["accessToken"]
    assert client.get(f"{BASE}/books/{demo['bookId']}/character-cards", params={"branchId": demo["branchId"], "anchor": json.dumps(anchor)}).status_code == 404
    assert client.post(f"{BASE}/books/{demo['bookId']}/character-index", json={"branchId": demo["branchId"]}, headers={"Idempotency-Key": "other-index"}).status_code == 404
    assert client.post(f"{BASE}/books/{demo['bookId']}/character-chats", json={"anchor": anchor, "characterId": selected["id"]}, headers={"Idempotency-Key": "other-chat"}).status_code == 404
    assert client.post(f"{BASE}/role-sessions/{session['id']}/send", json={"message": "你好", "turnId": "other-send"}, headers={"Idempotency-Key": "other-send"}).status_code == 404


def test_queued_turn_blocks_scope_upgrade_and_changed_task_snapshot_stops_before_call(client, reader, demo, profile):
    index(client, demo)
    earlier = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, earlier)
    selected = next(c for c in card_list(client, demo, earlier).json()["items"] if c["name"] == "林渡")
    session = chat(client, demo, earlier, selected)
    sent = client.post(f"{BASE}/role-sessions/{session['id']}/send", json={"message": "你好", "turnId": "queued"},
        headers={"Idempotency-Key": "queued"})
    assert sent.status_code == 202, sent.text
    task_id = sent.json()["taskId"]
    later = read_chapter(client, demo, 3)["blocks"][-1]["endAnchor"]
    observe(client, reader, later, expected=1, sequence=2)
    denied = client.post(f"{BASE}/books/{demo['bookId']}/character-chats", json={"anchor": later, "characterId": selected["id"]},
        headers={"Idempotency-Key": "upgrade-with-pending-turn"})
    assert denied.status_code == 409 and denied.json()["code"] == "TURN_IN_PROGRESS"
    with SessionLocal() as db:
        row = db.get(Task, task_id)
        row.checkpoint = {**row.checkpoint, "snapshotId": "different-immutable-snapshot"}
        db.commit()
    process(task_id)
    result = client.get(BASE + "/tasks/" + task_id).json()
    assert result["state"] == "failed" and result["errorCode"] == "ROLE_SCOPE_CHANGED"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id)) == 0
        assert db.get(RoleSession, session["id"]).pinned_anchor == earlier


def test_partial_index_budget_stops_but_verified_early_cards_remain_readable(client, reader, demo, profile):
    from app.models import ModelProfile
    from app.api_books import create_book
    from app.importer import txt_parse
    from app.storage import write_private
    from app.domain import digest
    from pathlib import Path
    content = (Path("app/fixtures/fog-harbor.txt").read_text("utf-8") + "\n第五章 山路\n" + "\n\n".join(
        f"林渡走过第{i}段山路。他看着山下的雾慢慢散开，沿着早已选定的方向继续前行。每一盏灯都映照着不同的石阶，风把远处的钟声送到路边。" for i in range(24))).encode()
    with SessionLocal() as db:
        owner = reader["user"]["id"]
        ref = write_private(owner, "long-card-fixture.txt", content)
        demo = create_book(db, owner, "渐进角色索引", txt_parse(content), digest(content), ref, "txt")
        row = db.get(ModelProfile, profile["id"])
        row.call_budget = 1
        db.commit()
    run = index(client, demo)
    with SessionLocal() as db:
        parent = db.get(Task, run["indexTaskId"])
        assert parent.state == "failed" and parent.error_code == "BUDGET_NOT_AUTHORIZED"
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 1
    earlier = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, earlier)
    listing = card_list(client, demo, earlier).json()
    assert listing["indexState"] == "failed"
    assert {c["name"] for c in listing["items"]} == {"林渡", "阿岑"}
    assert all(c["available"] for c in listing["items"])
    assert "夜渡" not in json.dumps(listing, ensure_ascii=False)
    repeated = index(client, demo, "do-not-auto-recharge")
    assert repeated["indexTaskId"] == run["indexTaskId"] and repeated["indexState"] == "failed"
    later = read_chapter(client, demo, 4)["blocks"][-1]["endAnchor"]
    observe(client, reader, later, expected=1, sequence=2)
    later_cards = card_list(client, demo, later).json()["items"]
    assert later_cards and not any(c["available"] for c in later_cards)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 1


def test_preference_change_after_queue_blocks_provider_before_request(client, reader, demo, profile):
    index(client, demo)
    anchor = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, anchor)
    selected = next(c for c in card_list(client, demo, anchor).json()["items"] if c["name"] == "林渡")
    session = chat(client, demo, anchor, selected)
    sent = client.post(f"{BASE}/role-sessions/{session['id']}/send", json={"message": "你好", "turnId": "mode-race"},
        headers={"Idempotency-Key": "mode-race"})
    assert sent.status_code == 202, sent.text
    client.put(BASE + "/character-preferences", json={"mode": "full", "expectedVersion": 1})
    process(sent.json()["taskId"])
    result = client.get(BASE + "/tasks/" + sent.json()["taskId"])
    assert result.status_code == 409 and result.json()["code"] == "ROLE_SCOPE_CHANGED"
    with SessionLocal() as db:
        assert db.get(Task, sent.json()["taskId"]).error_code == "ROLE_SCOPE_CHANGED"
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == sent.json()["taskId"])) == 0


def test_old_analyzer_snapshot_is_not_current_full_index_and_scope_changes_with_profile(client, reader, demo, profile):
    from app.models import ModelProfile, AnalysisChunk
    run = index(client, demo)
    anchor = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, anchor)
    initial = card_list(client, demo, anchor).json()
    assert initial["indexState"] == "ready" and initial["indexScope"]
    with SessionLocal() as db:
        for row in db.scalars(select(KnowledgeSnapshot)):
            row.analyzer_version = "analysis-old"
        db.query(AnalysisChunk).delete()
        db.commit()
    current = card_list(client, demo, anchor).json()
    assert current["indexState"] == "missing" and current["items"] == []
    restarted = index(client, demo, "current-analyzer-index")
    assert restarted["indexTaskId"] != run["indexTaskId"]
    assert card_list(client, demo, anchor).json()["indexState"] == "ready"
    with SessionLocal() as db:
        row = db.get(ModelProfile, profile["id"])
        row.version += 1
        db.commit()
    changed = card_list(client, demo, anchor).json()
    assert changed["indexScope"] != initial["indexScope"]
    assert changed["indexState"] == "missing"


def test_concurrent_send_and_scope_upgrade_keep_the_queued_knowledge_boundary(client, reader, demo, profile):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from unittest.mock import patch
    from sqlalchemy import event
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import engine
    from app import api_ai
    index(client, demo)
    earlier = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, earlier)
    selected = next(c for c in card_list(client, demo, earlier).json()["items"] if c["name"] == "林渡")
    session = chat(client, demo, earlier, selected)
    later = read_chapter(client, demo, 3)["blocks"][-1]["endAnchor"]
    observe(client, reader, later, expected=1, sequence=2)
    send_locked, upgrade_query, release_send = Event(), Event(), Event()
    original_create = api_ai.create_task
    def paused_create(*args, **kwargs):
        send_locked.set()
        assert release_send.wait(5), "test release signal did not arrive"
        return original_create(*args, **kwargs)
    def before_query(connection, cursor, statement, params, context, multiple):
        if (("FROM role_sessions" in statement and "ORDER BY role_sessions.created_at DESC" in statement) or
                ("SELECT pg_advisory_xact_lock" in statement and send_locked.is_set() and not release_send.is_set())):
            upgrade_query.set()
    auth = {"Authorization": client.headers["Authorization"]}
    def send():
        with TestClient(app) as device:
            return device.post(f"{BASE}/role-sessions/{session['id']}/send", json={"message": "你眼前在想什么？", "turnId": "race-turn"},
                headers={**auth, "Idempotency-Key": "race-send"})
    def upgrade():
        with TestClient(app) as device:
            return device.post(f"{BASE}/books/{demo['bookId']}/character-chats", json={"anchor": later, "characterId": selected["id"]},
                headers={**auth, "Idempotency-Key": "race-upgrade"})
    event.listen(engine, "before_cursor_execute", before_query)
    try:
        with patch.object(api_ai, "create_task", paused_create), ThreadPoolExecutor(max_workers=2) as pool:
            sent = pool.submit(send)
            assert send_locked.wait(5)
            advanced = pool.submit(upgrade)
            assert upgrade_query.wait(5)
            release_send.set()
            sent_response, advanced_response = sent.result(timeout=5), advanced.result(timeout=5)
        assert sent_response.status_code == 202, sent_response.text
        assert advanced_response.status_code == 409 and advanced_response.json()["code"] == "TURN_IN_PROGRESS"
        task_id = sent_response.json()["taskId"]
        with SessionLocal() as db:
            assert db.get(Task, task_id).payload["anchor"] == earlier
            assert db.get(RoleSession, session["id"]).pinned_anchor == earlier
        process(task_id)
        assert client.get(BASE + "/tasks/" + task_id).json()["result"]["replySaved"]
    finally:
        release_send.set()
        event.remove(engine, "before_cursor_execute", before_query)


def test_traits_are_attributed_to_named_subject_not_another_person_in_same_quote():
    evidence = {"id": "appearance", "quote": "林渡看向温和的阿岑。林渡说话简短。"}
    characters = [{"id": "lindu", "displayName": "林渡", "evidenceIds": [evidence["id"]], "identityProofVersion": 2},
        {"id": "acen", "displayName": "阿岑", "evidenceIds": [evidence["id"]], "identityProofVersion": 2}]
    snapshot = KnowledgeSnapshot(data={"characters": characters, "evidence": [evidence], "facts": []}, completeness="complete")
    found = {card["name"]: card for card in cards(snapshot)}
    assert found["林渡"]["traits"] == ["简短"]
    assert found["阿岑"]["traits"] == ["温和"]
