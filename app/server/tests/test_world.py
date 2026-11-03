"""World state, isolation, durable calls and branch semantics; no real credentials."""
import json
from copy import deepcopy
from unittest.mock import patch
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import Task, Progress, Branch, Revision, UsageRecord, ModelProfile
from app.providers import MockAdapter
from app.errors import Problem
from app.config import settings
from app.world.models import World, WorldLine, WorldCheckpoint, WorldTurn
from app.world.worker import process
from app.world.state import prepared_definition, initial_state, apply_plan, public_view, retrieve_lore
from app.world.memory import context
from .helpers import BASE, read_chapter, observe

API = "/api/world/v1"


@pytest.fixture(autouse=True)
def fast_fixture_stream():
    def stream(adapter, profile, prompt, data, limit, *, on_delta, cancel_check, **kwargs):
        result = adapter.generate(profile, prompt, data, limit)
        for i in range(0, len(result["text"]), 20):
            cancel_check()
            on_delta(result["text"][i:i+20])
        return result
    with patch.object(MockAdapter, "generate_stream", stream):
        yield


def post(client, path, body, key):
    response = client.post(API + path, json=body, headers={"Idempotency-Key": key})
    assert response.status_code in (200, 202), response.text
    return response.json()


def start(client, reader, demo, profile, rules=None):
    at = read_chapter(client, demo)["blocks"][-1]["endAnchor"]
    observe(client, reader, at)
    created = post(client, "/worlds", {"anchor": at, "rules": rules or {}}, "world-create")
    process(created["taskId"])
    prepared = client.get(API + "/worlds/" + created["worldId"]).json()
    assert prepared["status"] == "active", client.get(API+"/tasks/"+created["taskId"]).json()
    character = next(c for c in prepared["characters"] if c["available"])
    begun = post(client, "/worlds/"+prepared["id"]+"/begin", {"characterId": character["id"]}, "world-begin")
    process(begun["taskId"])
    line = client.get(API+"/worlds/"+prepared["id"]+"/lines/"+begun["lineId"]).json()
    assert line["head"]["sequence"] == 1, client.get(API+"/tasks/"+begun["taskId"]).json()
    return prepared, line


def action(client, world, line, text="我问眼前的人，这封信将送到哪里？", key="action"):
    return post(client, f"/worlds/{world['id']}/lines/{line['id']}/actions", {"message": text, "kind": "dialogue", "expectedCheckpointId": line["headCheckpointId"], "turnId": key}, key)


def read_line(client, world, line):
    return client.get(API+f"/worlds/{world['id']}/lines/{line['id']}").json()


def test_source_scope_preparation_begin_and_reader_unchanged(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    assert line["head"]["text"] and len(line["head"]["options"]) == 3
    assert line["head"]["state"]["playerId"]
    with SessionLocal() as db:
        assert db.get(Branch, demo["branchId"]).head_revision_id == demo["revisionId"]
        assert db.scalar(select(func.count()).select_from(Branch)) == 1
        assert db.scalar(select(func.count()).select_from(Progress)) == 1
        assert "夜渡" not in json.dumps(db.get(World, world["id"]).source["blocks"], ensure_ascii=False)
    sources = client.get(API+"/sources").json()
    assert sources["items"][0]["lines"]


def test_preparation_source_identity_matches_the_validated_output_contract():
    from types import SimpleNamespace
    from app.world.worker import preparation_characters
    from app.character_identity import world_reference
    proof = {"blockId": "block-a", "start": 0, "originalLabel": "林渡"}
    evidence = {"blockId": "block-a", "quote": "林渡是摆渡人"}
    ref = {"name": "林渡", "identityId": "1" * 32, "entityId": "2" * 32,
           "proof": proof, "proofs": [proof], "evidence": [evidence]}
    source = {"characterRefs": [ref], "blocks": [{"blockId": "block-a", "text": "林渡是摆渡人。"}]}
    supplied = preparation_characters(source)[0]
    world = SimpleNamespace(source=source, definition={})
    entity = {"id": "lin", "type": "character", "name": "林渡", "source": "original",
              "sourceCharacterId": supplied["sourceCharacterId"], "evidence": supplied["evidence"]}
    assert world_reference(world, entity) == ref["identityId"]
    assert "entityId" not in supplied and "identityId" not in supplied
    with pytest.raises(Problem) as rejected:
        world_reference(world, {**entity, "sourceCharacterId": ref["entityId"]})
    assert rejected.value.code == "CHARACTER_REFERENCE_INVALID"


def test_action_idempotency_head_competition_and_atomic_commit(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    job = action(client, world, line)
    assert action(client, world, line) == job
    other = client.post(API+f"/worlds/{world['id']}/lines/{line['id']}/actions", json={"message": "换个动作", "kind": "action", "expectedCheckpointId": line["headCheckpointId"], "turnId": "other"}, headers={"Idempotency-Key": "other"})
    assert other.status_code == 409
    process(job["taskId"])
    saved = read_line(client, world, line)
    assert saved["head"]["sequence"] == 2 and not saved["pendingTaskId"]
    assert saved["head"]["state"]["minute"] > line["head"]["state"]["minute"]
    assert action(client, world, line) == job
    with SessionLocal() as db:
        calls = db.scalar(select(func.count()).select_from(UsageRecord))
    process(job["taskId"])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == calls


def test_empty_input_never_calls_and_cancel_retains_checkpoint(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    response = client.post(API+f"/worlds/{world['id']}/lines/{line['id']}/actions", json={"message": "  ", "expectedCheckpointId": line["headCheckpointId"], "turnId": "blank"}, headers={"Idempotency-Key": "blank"})
    assert response.status_code == 422
    job = action(client, world, line)
    client.post(API+"/tasks/"+job["taskId"]+"/cancel")
    process(job["taskId"])
    assert read_line(client, world, line)["headCheckpointId"] == line["headCheckpointId"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == job["taskId"])) == 0


def test_ledger_conflict_and_length_keep_prose_without_advancing(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    original = MockAdapter.generate
    def invalid(adapter, p, prompt, data, limit):
        result = original(adapter, p, prompt, data, limit)
        if prompt.startswith("world-director"):
            value = json.loads(result["text"])
            value["transfers"] = [{"itemId": "not-an-item", "fromId": data["state"]["playerId"], "toId": None, "quantity": 1, "reason": "不存在物品", "visibleTo": ["player"]}]
            result["text"] = json.dumps(value)
        return result
    job = action(client, world, line)
    with patch.object(MockAdapter, "generate", invalid):
        process(job["taskId"])
    task = client.get(API+"/tasks/"+job["taskId"]).json()
    assert task["state"] == "failed" and task["stream"]["text"]
    assert read_line(client, world, line)["headCheckpointId"] == line["headCheckpointId"]
    assert client.get(API+f"/worlds/{world['id']}/turns/{job['turnId']}").json()["draftText"]


def test_stream_interruption_preserves_partial_no_automatic_resend(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    job = action(client, world, line)
    def interrupted(adapter, p, prompt, data, limit, *, on_delta, **kwargs):
        on_delta("你听见有人在渡口轻声呼唤。")
        raise Problem("PROVIDER_STATUS_UNKNOWN", "模拟中断")
    with patch.object(MockAdapter, "generate_stream", interrupted):
        process(job["taskId"])
    task = client.get(API+"/tasks/"+job["taskId"]).json()
    assert task["errorCode"] == "PROVIDER_STATUS_UNKNOWN" and task["stream"]["text"]
    assert read_line(client, world, line)["headCheckpointId"] == line["headCheckpointId"]
    with patch.object(MockAdapter, "generate", side_effect=AssertionError("no automatic retry")):
        process(job["taskId"])


def test_fork_before_after_and_rules_correction_are_independent(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    before = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": line["headCheckpointId"], "mode": "rechoose"}, "before")
    after = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": line["headCheckpointId"], "mode": "continue"}, "after")
    assert before["head"]["state"]["minute"] == 0
    assert after["head"]["state"] == line["head"]["state"]
    corrected = post(client, f"/worlds/{world['id']}/corrections", {"expectedCheckpointId": line["headCheckpointId"], "field": "rule:pace", "value": "detailed", "reason": "想读细腻一点"}, "correction")
    assert corrected["head"]["state"]["rules"]["pace"] == "detailed"
    assert read_line(client, world, line)["head"]["state"]["rules"]["pace"] == "standard"
    graph = client.get(API+f"/worlds/{world['id']}/graph").json()
    assert len({n["id"] for n in graph["nodes"]}) == len(graph["nodes"])


def test_three_chapters_use_temporary_state_then_accept_prefix(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    job = post(client, f"/worlds/{world['id']}/lines/{line['id']}/write", {"chapterCount": 3, "expectedCheckpointId": line["headCheckpointId"]}, "write3")
    process(job["taskId"])
    task = client.get(API+"/tasks/"+job["taskId"]).json()
    assert task["state"] == "succeeded", task
    with SessionLocal() as db:
        candidate = db.get(WorldTurn, job["turnId"])
        chapters = candidate.result["chapters"]
        assert len(chapters) == 3
        assert [c["state"]["minute"] for c in chapters] == [10, 15, 20]
    assert read_line(client, world, line)["headCheckpointId"] == line["headCheckpointId"]
    accepted = post(client, f"/worlds/{world['id']}/turns/{job['turnId']}/accept", {"chapterCount": 2, "expectedCheckpointId": line["headCheckpointId"]}, "accept2")
    saved = read_line(client, world, line)
    assert saved["headCheckpointId"] == accepted["checkpointId"] and saved["head"]["state"]["minute"] == 15
    assert saved["head"]["sequence"] == 3
    assert post(client, f"/worlds/{world['id']}/turns/{job['turnId']}/accept", {"chapterCount": 2, "expectedCheckpointId": line["headCheckpointId"]}, "accept2") == accepted


def test_candidate_outdated_cannot_overwrite_next_action(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    written = post(client, f"/worlds/{world['id']}/lines/{line['id']}/write", {"chapterCount": 1, "expectedCheckpointId": line["headCheckpointId"]}, "write")
    process(written["taskId"])
    next_turn = action(client, world, line)
    process(next_turn["taskId"])
    response = client.post(API+f"/worlds/{world['id']}/turns/{written['turnId']}/accept", json={"chapterCount": 1, "expectedCheckpointId": line["headCheckpointId"]}, headers={"Idempotency-Key": "stale"})
    assert response.status_code == 409
    assert client.get(API+f"/worlds/{world['id']}/turns/{written['turnId']}").json()["chapters"]


def test_rewrite_preserves_same_outcome_and_replaces_text_in_ancestry(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    job = post(client, f"/worlds/{world['id']}/rewrite", {"checkpointId": line["headCheckpointId"], "instruction": "更细腻"}, "rewrite")
    process(job["taskId"])
    new_line = {"id": job["lineId"]}
    rewritten = read_line(client, world, new_line)
    assert rewritten["head"]["state"] == line["head"]["state"]
    history = client.get(API+f"/worlds/{world['id']}/lines/{job['lineId']}/history").json()["items"]
    assert line["headCheckpointId"] not in {c["id"] for c in history}
    assert rewritten["head"]["kind"] == "rewrite"


def test_publication_is_explicit_once_and_origin_is_unchanged(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    path = f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish"
    publication = post(client, path, {}, "publish")
    assert post(client, path, {}, "publish2") == publication
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Branch)) == 2
        assert db.get(Branch, demo["branchId"]).head_revision_id == demo["revisionId"]
        published = db.get(Revision, publication["revisionId"])
        assert published.provenance["operation"] == "world_publication"
    published_head = publication["revisionId"]
    new_turn = action(client, world, line)
    process(new_turn["taskId"])
    with SessionLocal() as db:
        assert db.get(Branch, publication["branchId"]).head_revision_id == published_head


def test_source_deletion_retains_world_and_publishes_snapshot(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    # Same deletion cascade as the book service, without rewriting reader contracts.
    from app.models import Book
    with SessionLocal() as db:
        db.delete(db.get(Book, demo["bookId"]))
        db.commit()
    assert client.get(API+"/worlds/"+world["id"]).json()["sourceRemoved"]
    publication = post(client, f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish", {}, "after-source-delete")
    assert publication["bookId"] != demo["bookId"]
    assert client.get(BASE+"/books/"+publication["bookId"]).status_code == 200


def test_other_owner_never_sees_world_line_checkpoint_or_task(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    client.post(BASE+"/auth/register", json={"username": "other_world_user", "password": "reader-password-123"})
    login = client.post(BASE+"/auth/login", json={"username": "other_world_user", "password": "reader-password-123"}).json()
    client.headers["Authorization"] = "Bearer "+login["accessToken"]
    for path in ("/worlds/"+world["id"], f"/worlds/{world['id']}/lines/{line['id']}", f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}", "/tasks/"+world["prepareTaskId"]):
        assert client.get(API+path).status_code == 404
    assert client.get(API+"/worlds").json()["items"] == []


def test_world_disabled_leaves_reader_health_and_books_available(client, reader, demo):
    with patch.object(settings, "interactive_world_enabled", False):
        assert client.get(API+"/config").json()["enabled"] is False
        assert client.get(API+"/worlds").status_code == 503
        assert client.get(BASE+"/books/"+demo["bookId"]).status_code == 200
        assert client.get("/health/ready").status_code == 200


def definition():
    return prepared_definition({"title": "测试世界", "description": "渡口", "timeLabel": "上午", "location": "pier",
        "entities": [{"id": "pier", "name": "渡口", "type": "place", "source": "game"}, {"id": "far", "name": "远城", "type": "place", "source": "game"},
            {"id": "player", "name": "顾安", "type": "character", "source": "game"}, {"id": "npc", "name": "梅舟", "aliases": ["舟姐"], "type": "character", "hiddenGoal": "秘密等待一位失散的亲人", "source": "game"},
            {"id": "letter", "name": "唯一的信", "type": "item", "source": "game"}],
        "initial": {"player": {"alive": True, "location": "pier", "inventory": {"letter": 1}}, "npc": {"alive": True, "location": "pier", "goal": "等信"}},
        "lore": [{"id": "secret", "title": "隐秘消息", "text": "远城将在明天封闭东门", "triggers": ["东门"], "entityIds": ["npc"], "knownBy": ["npc"], "source": "game"}]}, [], {"genre": "mystery", "deathPolicy": "end", "relationshipNumbers": False, "randomChecks": False, "pace": "standard", "style": "", "customRules": ""})


def plan(**values):
    return {"title": "场景", "elapsedMinutes": 5, "timeLabel": "午后", "publicOutcome": "观察到了眼前的事情", **values}


def test_hundred_turn_replay_persistent_ownership_relationships_goals_and_knowledge():
    d, state = initial_state(definition(), "player")
    root = deepcopy(state)
    for i in range(100):
        updates, transfers = [], []
        if i == 0:
            transfers = [{"itemId": "letter", "fromId": "player", "toId": "npc", "quantity": 1, "reason": "亲手交信", "visibleTo": ["player"]}]
            updates = [{"entityId": "npc", "field": "relation:player", "value": "愿意信任", "reason": "收到了信", "visibleTo": ["player"]}, {"entityId": "npc", "field": "location", "value": "far", "reason": "送信", "visibleTo": []}]
        if i == 30:
            updates = [{"entityId": "npc", "field": "location", "value": "pier", "reason": "返程", "visibleTo": ["player"]}]
        if i == 40:
            updates = [{"entityId": "player", "field": "known", "value": ["secret"], "reason": "梅舟亲口告知", "visibleTo": ["player"]}]
        knowledge_events=[{"type":"knowledge_shared","entityId":"npc","loreIds":["secret"],"description":"梅舟亲口告诉顾安消息","visibleTo":["player"]}] if i == 40 else []
        state, d, _, _ = apply_plan(state, d, plan(updates=updates, transfers=transfers, events=knowledge_events, critical=["我答应梅舟归还这封信"] if i == 0 else []), str(i))
    assert state["minute"] == 500
    assert state["entities"]["npc"]["inventory"]["letter"] == 1 and state["entities"]["player"]["inventory"]["letter"] == 0
    assert state["entities"]["npc"]["relations"]["player"] == "愿意信任"
    assert state["entities"]["npc"]["goal"] == "等信"
    assert state["critical"] == ["我答应梅舟归还这封信"]
    assert state["entities"]["player"]["known"] == ["secret"]
    assert root["entities"]["player"]["inventory"]["letter"] == 1


def test_private_projection_alias_recall_and_no_free_resurrection_or_item():
    d, state = initial_state(definition(), "player")
    public = public_view(state, d)
    assert "秘密等待" not in json.dumps(public, ensure_ascii=False) and public["lore"] == []
    assert retrieve_lore(d, state, "问舟姐", private=True)[0]["id"] == "secret"
    assert retrieve_lore(d, state, "问舟姐") == []
    with pytest.raises(Problem):
        apply_plan(state, d, plan(transfers=[{"itemId": "letter", "fromId": "npc", "toId": "player", "quantity": 1, "reason": "凭空取得"}]), "bad-item")
    dead, d, _, _ = apply_plan(state, d, plan(updates=[{"entityId": "player", "field": "alive", "value": False, "reason": "受伤死亡", "visibleTo": ["player"]}]), "death")
    assert dead["ended"]
    with pytest.raises(Problem):
        apply_plan(dead, d, plan(updates=[{"entityId": "player", "field": "alive", "value": True, "reason": "凭空复活"}]), "bad-rebirth")


def test_story_deadlines_only_advance_with_turn_and_npc_knowledge_separate():
    d, state = initial_state(definition(), "player")
    state, d, _, events = apply_plan(state, d, plan(events=[{"type": "promise", "entityId": "npc", "description": "一个小时后交接信件", "visibleTo": [], "dueMinute": 60}]), "promise")
    assert state["queue"] and not events
    earlier = deepcopy(state)
    state, d, _, events = apply_plan(state, d, plan(elapsedMinutes=60), "wait")
    assert state["queue"] and not state["resolvedEvents"] and events[0]["type"] == "deadline_reached"
    state, d, _, events = apply_plan(state, d, plan(elapsedMinutes=0, eventResolutions=[{
        "eventId": state["queue"][0]["id"], "status": "fulfilled", "description": "实际处理约定"}]), "fulfill")
    assert not state["queue"] and events[0]["type"] == "promise"
    assert earlier["minute"] == 5 and earlier["queue"]
    assert "secret" not in state["known"]


def test_rebirth_requires_event_and_cost():
    d = definition();d["rules"]["deathPolicy"] = "rebirth"
    d, state = initial_state(d, "player")
    state, d, _, _ = apply_plan(state, d, plan(updates=[{"entityId": "player", "field": "alive", "value": False, "reason": "死亡"}]), "death")
    reborn, _, _, _ = apply_plan(state, d, plan(updates=[{"entityId": "player", "field": "alive", "value": True, "reason": "付出代价重生"}], transfers=[{"itemId": "letter", "fromId": "player", "toId": None, "quantity": 1, "reason": "信作为代价消失"}], events=[{"type": "rebirth", "entityId": "player", "description": "重生", "visibleTo": ["player"]}]), "reborn")
    assert reborn["entities"]["player"]["alive"] and not reborn["ended"]


def test_metadata_projection_and_entity_knowledge_guard():
    from app.world.state import public_payload
    from app.world.memory import narrator_context
    from types import SimpleNamespace
    d, state = initial_state(definition(), "player")
    npc = next(e for e in d["entities"] if e["id"] == "npc")
    npc.update(description="私密身份只有梅舟知道", traits="隐藏特征只有梅舟知道", knownBy=["npc"])
    hidden = d["lore"][0]["text"]
    payload = public_payload({"title": hidden, "options": [{"text": hidden}], "summary": hidden}, state, d)
    assert hidden not in json.dumps(payload, ensure_ascii=False)
    view = public_view(state, d)
    assert next(e for e in view["entities"] if e["id"] == "npc")["description"] == ""
    narr = narrator_context(SimpleNamespace(text=hidden,state=deepcopy(state)), state, d, {"title": hidden, "publicOutcome": hidden}, "观察")
    assert hidden not in json.dumps(narr, ensure_ascii=False)


def test_resource_payments_costs_capability_and_routes():
    d, state = initial_state(definition(), "player")
    state["entities"]["player"]["fields"] = {"money": 5, "stamina": 4, "ability": "游泳"}
    state["entities"]["npc"]["fields"]["money"] = 2
    d["rules"]["actionCosts"] = {"action": {"stamina": 2, "minutes": 5}}
    d["rules"]["abilityRequirements"] = {"action": {"ability": "游泳"}}
    op = {"entityId": "player", "field": "stamina", "operation": "spend", "amount": 2, "reason": "划船"}
    money = {"entityId": "player", "field": "money", "operation": "transfer", "counterpartyId": "npc", "amount": 3, "reason": "付款"}
    after, _, _, _ = apply_plan(state, d, plan(resources=[op, money]), "paid", action_kind="action")
    assert after["entities"]["player"]["fields"]["money"] == 2
    assert after["entities"]["npc"]["fields"]["money"] == 5
    assert after["entities"]["player"]["fields"]["stamina"] == 2
    for bad in (plan(), plan(resources=[{**op, "amount": 20}]), plan(resources=[op], elapsedMinutes=1)):
        with pytest.raises(Problem): apply_plan(state, d, bad, "bad", action_kind="action")
    d["rules"]["abilityRequirements"]["action"]["ability"] = "飞行"
    with pytest.raises(Problem): apply_plan(state, d, plan(resources=[op]), "fly", action_kind="action")
    d["rules"]["routes"] = {"pier": []}
    with pytest.raises(Problem): apply_plan(state, d, plan(location="far"), "teleport")
    d["rules"]["routes"] = {"pier": ["far"]}
    assert apply_plan(state, d, plan(location="far"), "walk")[0]["location"] == "far"


def test_unknown_resources_not_invented_or_reset_by_old_evidence():
    d, state = initial_state(definition(), "player")
    source = [{"blockId": "number", "text": "顾安身上有1000文钱。"}]
    op = {"entityId": "player", "field": "money", "operation": "observe", "amount": 100, "reason": "原文余额", "evidence": [{"blockId": "number", "quote": source[0]["text"]}]}
    with pytest.raises(Problem): apply_plan(state, d, plan(resources=[op]), "wrong-number", source)
    after = apply_plan(state, d, plan(resources=[{**op, "amount": 1000}]), "observe", source)[0]
    with pytest.raises(Problem): apply_plan(after, d, plan(resources=[{**op, "amount": 1000}]), "reset", source)
    with pytest.raises(Problem): apply_plan(state, d, plan(updates=[{"entityId": "player", "field": "field:money", "value": 999999, "reason": "凭空赋值"}]), "magic")
    d["rules"]["actionCosts"] = {"observe": {"stamina": 0}}
    assert apply_plan(state, d, plan(), "free", action_kind="observe")[0]["minute"] == 5


def test_death_policy_mismatch_and_fake_cost_rejected():
    d, state = initial_state(definition(), "player")
    d["rules"]["deathPolicy"] = "rescue"
    state["entities"]["player"]["alive"] = False
    state["entities"]["player"]["fields"]["money"] = 3
    update = {"entityId": "player", "field": "alive", "value": True, "reason": "救援"}
    for event, amount in (("rebirth", 1), ("rescue", 0)):
        with pytest.raises(Problem): apply_plan(state, d, plan(updates=[update], resources=[{"entityId": "player", "field": "money", "operation": "spend", "amount": amount, "reason": "代价"}], events=[{"type": event, "entityId": "player", "description": "恢复生命"}]), "invalid")


def test_bad_location_is_saved_failure_and_does_not_auto_reclaim(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    original = MockAdapter.generate
    def bad(adapter, p, prompt, data, limit):
        result = original(adapter, p, prompt, data, limit)
        if prompt.startswith("world-director"):
            value = json.loads(result["text"]);value["updates"] = [{"entityId": data["state"]["playerId"], "field": "location", "value": ["harbor"], "reason": "错误类型"}];result["text"] = json.dumps(value)
        return result
    job = action(client, world, line)
    with patch.object(MockAdapter, "generate", bad): process(job["taskId"])
    saved = client.get(API+"/tasks/"+job["taskId"]).json()
    assert saved["state"] == "failed" and saved["persistenceState"] == "saved" and saved["stream"]["text"]
    assert not read_line(client, world, line)["pendingTaskId"]


def test_review_contradiction_keeps_body_and_original_state(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    original = MockAdapter.generate
    def contradiction(adapter, p, prompt, data, limit):
        result = original(adapter, p, prompt, data, limit)
        if prompt.startswith("world-review"):
            assert data["ledger"]["before"] and data["ledger"]["after"]
            result["text"] = json.dumps({"consistent": False, "issues": ["正文物品去向与行动不一致"]})
        return result
    job = action(client, world, line)
    with patch.object(MockAdapter, "generate", contradiction): process(job["taskId"])
    assert read_line(client, world, line)["headCheckpointId"] == line["headCheckpointId"]
    draft = client.get(API+f"/worlds/{world['id']}/turns/{job['turnId']}").json()
    assert draft["draftText"] and draft["warnings"]


def test_world_deleted_private_tasks_cannot_return_via_reader_api(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    receipt = post(client, f"/worlds/{world['id']}/lines/{line['id']}/actions", {"message": "观察", "expectedCheckpointId": line["headCheckpointId"], "turnId": "receipt"}, "receipt")
    assert client.get(API+f"/worlds/{world['id']}/lines/{line['id']}/actions/receipt").json()["taskId"] == receipt["taskId"]
    process(receipt["taskId"])
    assert client.get(BASE+"/tasks/"+receipt["taskId"]).status_code == 404
    assert all(not t["taskType"].startswith("world_") for t in client.get(BASE+"/tasks").json()["items"])
    assert client.delete(API+"/worlds/"+world["id"]).status_code == 200
    from app.models import TaskEvent
    with SessionLocal() as db:
        assert not db.get(Task, receipt["taskId"]).checkpoint.get("worldStream")
        assert db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == receipt["taskId"])) == 0
        assert db.get(Branch, demo["branchId"]).head_revision_id == demo["revisionId"]


def test_checkpoint_and_candidate_integrity_guards(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    job = post(client, f"/worlds/{world['id']}/lines/{line['id']}/write", {"chapterCount": 1, "expectedCheckpointId": line["headCheckpointId"]}, "tamper-write")
    process(job["taskId"])
    with SessionLocal() as db:
        turn = db.get(WorldTurn, job["turnId"]);value=deepcopy(turn.result);value["chapters"][0]["text"]="被篡改的正文";turn.result=value;db.commit()
    response = client.post(API+f"/worlds/{world['id']}/turns/{job['turnId']}/accept", json={"chapterCount": 1, "expectedCheckpointId": line["headCheckpointId"]}, headers={"Idempotency-Key": "tampered"})
    assert response.status_code == 409
    with SessionLocal() as db:
        checkpoint=db.get(WorldCheckpoint,line["headCheckpointId"]);checkpoint.text="被篡改的存档";db.commit()
    assert client.get(API+f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}").status_code == 409


def test_hundred_checkpoints_recall_large_old_archive_within_budget(client, reader, demo, profile):
    from app.world.memory import save_memory
    from app.world.state import checkpoint_hash
    from app.domain import canonical
    from app.models import uid
    world, line = start(client, reader, demo, profile)
    with SessionLocal() as db:
        instance=db.get(World,world["id"]);parent=db.get(WorldCheckpoint,line["headCheckpointId"])
        for i in range(100):
            state=deepcopy(parent.state);state["minute"]+=5
            summary=("梅舟答应归还黑檀匣。" if i == 0 else f"第{i}轮的普通经历。")+"沿着渡口继续观察，记下当天见闻。"*26
            text="风吹过树梢，人们在岸边说起今日的事情。"*15
            cp=WorldCheckpoint(id=uid(),owner_id=parent.owner_id,world_id=parent.world_id,line_id=parent.line_id,parent_checkpoint_id=parent.id,sequence=parent.sequence+1,state=state,definition=deepcopy(parent.definition),text=text,title="回合",action="观察",summary=summary,options=[],changes=[],memory=deepcopy(parent.memory),kind="turn",content_hash=checkpoint_hash(state,parent.definition,text,parent.id))
            db.add(cp);db.flush();save_memory(db,cp,parent,[]);parent=cp
        db.commit()
        payload=context(db,parent,instance,"黑檀匣的归还承诺怎样了？")
        assert len(canonical(payload).encode())+4000 < 24000
        assert len(payload["recentScenes"]) >= 1
        assert "黑檀匣" in json.dumps(payload["recalledEpisodes"],ensure_ascii=False)
        assert payload["recalledEpisodes"][0]["firstCheckpointId"]


def test_received_journals_resume_persistence_failure_without_new_calls(client, reader, demo, profile):
    from sqlalchemy.exc import OperationalError
    world,line=start(client,reader,demo,profile,rules={"randomChecks":True})
    job=action(client,world,line)
    with patch("app.world.worker.commit_turn",side_effect=OperationalError("commit",{},Exception("database unavailable"))): process(job["taskId"])
    with SessionLocal() as db:
        assert db.get(Task,job["taskId"]).persistence_state == "recoverable"
        calls=db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == job["taskId"]))
        roll=db.get(WorldTurn,job["turnId"]).result["rolls"]["1"]
    assert read_line(client,world,line)["headCheckpointId"] == line["headCheckpointId"]
    with patch.object(MockAdapter,"generate",side_effect=AssertionError("received results must be replayed, never resent")): process(job["taskId"])
    saved=read_line(client,world,line)
    assert saved["headCheckpointId"] != line["headCheckpointId"]
    assert saved["head"]["state"]["checks"][-1]["roll"] == roll
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == job["taskId"])) == calls


def test_initial_unresolved_lifecycle_and_unknown_reference_rejection():
    d=definition();d["unresolved"]=["归还信件的承诺"]
    d,state=initial_state(d,"player")
    assert state["unresolved"][0]["id"] == "initial-0"
    state,d,_,_=apply_plan(state,d,plan(resolvedUnresolved=["initial-0"]),"resolved")
    assert state["unresolved"] == [] and state["resolvedUnresolved"] == ["initial-0"]
    with pytest.raises(Problem):apply_plan(state,d,plan(resolvedUnresolved=["initial-0"]),"again")


def test_bounded_incremental_preparation_stable_ids_and_checkpoint_resume(client, reader, demo, profile):
    from app.models import ContentBlock
    from app.domain import digest
    with SessionLocal() as db:
        revision=db.get(Revision,demo["revisionId"])
        for i,block_id in enumerate(revision.manifest[0]["blocks"]):
            block=db.get(ContentBlock,block_id);block.text="记录渡口的普通见闻。"*400+("林渡来到渡口。" if i==0 else "梅舟在渡口等人。")
            block.content_hash=digest(block.text)
        db.commit()
    at=read_chapter(client,demo)["blocks"][-1]["endAnchor"];observe(client,reader,at)
    job=post(client,"/worlds",{"anchor":at,"maxCalls":2},"large-source")
    original=MockAdapter.generate
    seen=0
    def pause_after_first(adapter,p,prompt,data,limit):
        nonlocal seen
        if prompt.startswith("world-prepare"):
            seen+=1
            if seen==2:
                from sqlalchemy.exc import OperationalError
                raise OperationalError("read",{},Exception("temporary database disconnect"))
        return original(adapter,p,prompt,data,limit)
    with patch.object(MockAdapter,"generate",pause_after_first):process(job["taskId"])
    with SessionLocal() as db:
        task=db.get(Task,job["taskId"])
        assert task.checkpoint["preparedBatches"] == 1
        assert task.persistence_state == "recoverable"
        # This injection occurs before a provider response; the interrupted intent
        # is deliberately UNKNOWN and must never automatically be sent again.
    process(job["taskId"])
    result=client.get(API+"/tasks/"+job["taskId"]).json()
    assert result["state"] == "failed" and result["errorCode"] == "PROVIDER_STATUS_UNKNOWN"
    # A separate finite preparation request is an explicit retry, not recovery.
    retried=post(client,f"/worlds/{job['worldId']}/prepare",{},"large-explicit-retry")
    process(retried["taskId"])
    with SessionLocal() as db:
        world=db.get(World,job["worldId"])
        assert world.status == "active"
        assert {e["name"] for e in world.definition["entities"]} >= {"林渡","梅舟"}
        assert len({e["id"] for e in world.definition["entities"]}) == len(world.definition["entities"])


def test_preparation_preserves_omitted_state_and_distinct_lore_references():
    from app.world.preparation import merge
    before=definition();incoming=deepcopy(before)
    incoming["lore"][0].update(title="另一则消息",text="明天会下雨")
    incoming["initial"]["player"].update(alive=None,inventory={},fields={},relations={})
    merged=merge(before,incoming)
    assert len(merged["lore"]) == 2
    assert merged["lore"][0]["text"] == before["lore"][0]["text"]
    assert merged["lore"][1]["id"] != "secret"
    assert merged["initial"]["player"]["alive"] is True
    assert merged["initial"]["player"]["inventory"] == {"letter":1}


def test_knowledge_requires_a_present_informed_source_and_updates_only_receivers():
    d,state=initial_state(definition(),"player")
    with pytest.raises(Problem):apply_plan(state,d,plan(learned=["secret"]),"no-channel")
    with pytest.raises(Problem):apply_plan(state,d,plan(updates=[{"entityId":"player","field":"known","value":["secret"],"reason":"无来源"}]),"no-channel-update")
    event={"type":"knowledge_shared","entityId":"npc","loreIds":["secret"],"description":"梅舟告知东门的消息","visibleTo":["player"]}
    changed,d,_,events=apply_plan(state,d,plan(events=[event],learned=["secret"]),"heard")
    assert changed["known"] == ["secret"] and state["known"] == []
    assert changed["entities"]["far"]["known"] == []
    assert events[0]["loreIds"] == ["secret"]
    left,_,_,_=apply_plan(state,d,plan(events=[event],learned=["secret"],updates=[{"entityId":"npc","field":"location","value":"far","reason":"告知后离开"}]),"heard-then-left")
    assert left["known"] == ["secret"] and left["entities"]["npc"]["location"] == "far"
    far=deepcopy(state);far["entities"]["npc"]["location"]="far"
    with pytest.raises(Problem):apply_plan(far,d,plan(events=[event],learned=["secret"]),"remote")
    uninformed=deepcopy(state);uninformed["entities"]["npc"]["known"]=[]
    d2=deepcopy(d);d2["lore"][0]["knownBy"]=[]
    with pytest.raises(Problem):apply_plan(uninformed,d2,plan(events=[event],learned=["secret"]),"uninformed")


def test_private_phrase_filter_covers_drafts_warnings_and_split_stream(client,reader,demo,profile):
    from app.world.state import public_stream
    from app.world.jobs import persist_stream
    from app.world.worker import save_visibility
    secret="只有梅舟知道的完整秘密"
    world,line=start(client,reader,demo,profile)
    job=action(client,world,line)
    with SessionLocal() as db:
        parent=db.get(WorldCheckpoint,line["headCheckpointId"])
        d=deepcopy(parent.definition);state=deepcopy(parent.state)
        d["lore"].append({"id":"private-new","text":secret,"title":"秘密","knownBy":[]})
        parent.definition=d
        from app.world.state import checkpoint_hash
        parent.content_hash=checkpoint_hash(state,d,parent.text,parent.parent_checkpoint_id)
        t=db.get(WorldTurn,job["turnId"]);t.warnings=[secret]
        db.commit()
    save_visibility(job["taskId"],state,d)
    persist_stream(job["taskId"],"generation:scene:1","公开文字。只有梅舟知道的完")
    partial=client.get(API+"/tasks/"+job["taskId"]).json()["stream"]["text"]
    assert partial == "公开文字。"
    persist_stream(job["taskId"],"generation:scene:1","公开文字。"+secret,final=True)
    for path in ("/tasks/"+job["taskId"],f"/worlds/{world['id']}/turns/{job['turnId']}"):
        assert secret not in json.dumps(client.get(API+path).json(),ensure_ascii=False)
    assert public_stream("故事结尾",[secret]) == "故事结尾"


def test_publication_cannot_bypass_player_visibility_in_title_or_body(client,reader,demo,profile):
    from app.models import ContentBlock
    from app.world.state import checkpoint_hash
    world,line=start(client,reader,demo,profile)
    secret="未知身份隐藏在远城的完整秘密"
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,line["headCheckpointId"])
        cp.definition={**cp.definition,"lore":cp.definition.get("lore",[])+[{"id":"private-extra","title":"秘密","text":secret,"knownBy":[]}]}
        cp.title=secret;cp.text="公开故事。"+secret
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,cp.parent_checkpoint_id)
        db.commit()
    publication=post(client,f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",{},"filtered-publication")
    with SessionLocal() as db:
        revision=db.get(Revision,publication["revisionId"])
        assert secret not in json.dumps(revision.manifest,ensure_ascii=False)
        texts=[db.get(ContentBlock,key).text for chapter in revision.manifest for key in chapter["blocks"]]
        assert secret not in "".join(texts) and "公开故事。" in "".join(texts)


def test_chapter_that_ends_in_player_input_prompt_is_draft_not_complete(client,reader,demo,profile):
    from app.world.worker import validate_chapter_body
    validate_chapter_body('她问：“你打算怎么做？”\n\n他已把信送到，转身迎向下一段旅途。')
    with pytest.raises(Problem):validate_chapter_body('这一段故事的文字。\n\n——轮到你：')
    world,line=start(client,reader,demo,profile)
    job=post(client,f"/worlds/{world['id']}/lines/{line['id']}/write",{"expectedCheckpointId":line["headCheckpointId"],"chapterCount":1},"unfinished-menu-chapter")
    original=MockAdapter.generate
    def menu(adapter,p,prompt,data,limit):
        value=original(adapter,p,prompt,data,limit)
        if prompt.startswith('world-narrator'):
            value["text"]+='\n\n——轮到你：'
        return value
    with patch.object(MockAdapter,'generate',menu):process(job['taskId'])
    task=client.get(API+'/tasks/'+job['taskId']).json()
    draft=client.get(API+f"/worlds/{world['id']}/turns/{job['turnId']}").json()
    assert task['state']=='failed' and task['errorCode']=='WORLD_OUTPUT_INCOMPLETE'
    assert draft['status']=='draft' and draft['draftText'].endswith('——轮到你：') and not draft['chapters']
    assert read_line(client,world,line)['headCheckpointId']==line['headCheckpointId']


def test_narrative_review_compares_item_ownership_before_and_after_without_private_goals():
    from app.world.worker import review_ledger
    d,before=initial_state(definition(),"player")
    after,d,changes,_=apply_plan(before,d,plan(transfers=[{"itemId":"letter","fromId":"player","toId":"npc","quantity":1,"reason":"亲手交信","visibleTo":["player"]}]),"given")
    ledger=review_ledger(before,after,d,changes)
    old={e['id']:e for e in ledger['before']};new={e['id']:e for e in ledger['after']}
    assert old['player']['inventory']['letter']==1 and new['player']['inventory']['letter']==0
    assert old['npc']['inventory']['letter'] is None and new['npc']['inventory']['letter']==1
    assert ledger['items']['letter']=='唯一的信'
    assert '秘密等待' not in json.dumps(ledger,ensure_ascii=False)
    assert all('known' not in e and 'goal' not in e for e in ledger['after'])


def test_archived_failed_preparation_restores_as_unready_then_explicit_retry(client,reader,demo,profile):
    at=read_chapter(client,demo)["blocks"][-1]["endAnchor"];observe(client,reader,at)
    job=post(client,"/worlds",{"anchor":at,"maxCalls":1},"failed-before-archive")
    with patch.object(MockAdapter,'generate',return_value={"text":"{}","finishReason":"stop","usage":{"state":"known","inputTokens":10,"outputTokens":2}}):process(job['taskId'])
    world=client.get(API+'/worlds/'+job['worldId']).json()
    assert world['status']=='failed'
    archived=client.patch(API+'/worlds/'+world['id'],json={"expectedVersion":world['version'],"status":"archived"}).json()
    blocked=client.post(API+f"/worlds/{world['id']}/prepare",json={},headers={"Idempotency-Key":"archived-retry"})
    assert blocked.status_code==409 and blocked.json()['code']=='WORLD_ARCHIVED'
    restored=client.patch(API+'/worlds/'+world['id'],json={"expectedVersion":archived['version'],"status":"active"}).json()
    assert restored['status']=='failed' and not restored['activeLineId']
    begun=client.post(API+f"/worlds/{world['id']}/begin",json={"customName":"小夏","customIdentity":"借书的读者"},headers={"Idempotency-Key":"unready-begin"})
    assert begun.status_code==409
    retried=post(client,f"/worlds/{world['id']}/prepare",{},"restored-explicit-retry")
    process(retried['taskId'])
    assert client.get(API+'/worlds/'+world['id']).json()['status']=='active'
