"""Scheduled consequences, identity audiences and paid revival: isolated fixtures."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest
from sqlalchemy import select, func

from app.config import settings
from app.db import SessionLocal
from app.errors import Problem
from app.models import Task, UsageRecord
from app.providers import MockAdapter
from app.world.memory import context
from app.world.models import World, WorldCheckpoint, WorldEvent, WorldTurn
from app.world.schemas import EntityDefinition, PrepareOutput
from app.world.state import apply_plan, checkpoint_hash, initial_state, event_visible_to
from app.world.worker import process
from .test_world import API, definition, fast_fixture_stream, plan, post, read_line, start


def scheduled(*, private=False, decision=False):
    d, state = initial_state(definition(), "player")
    event = {"type": "knowledge_shared" if private else "promise", "entityId": "npc",
             "description": "约定的信件", "visibleTo": ["player"], "dueMinute": 60,
             "loreIds": ["secret"] if private else [], "requiresDecision": decision}
    state, d, _, _ = apply_plan(state, d, plan(events=[event]), "schedule")
    return d, state, state["queue"][0]["id"]


def test_deadline_reached_is_not_fulfilled_and_is_not_repeated():
    d, state, event_id = scheduled(private=True)
    before = deepcopy(state)
    waiting, _, _, events = apply_plan(state, d, plan(elapsedMinutes=120), "wait")
    assert waiting["minute"] == 125 and waiting["queue"] and not waiting["resolvedEvents"]
    assert waiting["known"] == [] and events[0]["type"] == "deadline_reached"
    assert events[0]["minute"] == 60 and events[0]["scheduledAtMinute"] == 5
    assert events[0]["loreIds"] == [] and waiting["reachedEvents"] == [event_id]
    later, _, _, events = apply_plan(waiting, d, plan(), "later")
    assert later["queue"] and not events and state == before


def test_literal_character_id_player_can_be_addressed_without_becoming_current_identity():
    d, state = initial_state(definition(), "npc")
    after, _, _, events = apply_plan(state, d, plan(events=[{"type": "knowledge_shared", "entityId": "npc",
        "description": "只告诉顾安", "loreIds": ["secret"], "visibleTo": ["entity:player"]}]), "literal-recipient")
    assert events[0]["audienceIds"] == ["player"] and not events[0]["audienceAll"]
    assert event_visible_to(events[0], "player") and not event_visible_to(events[0], "npc")
    assert "secret" in after["entities"]["player"]["known"]


@pytest.mark.parametrize("status", ["fulfilled", "missed", "cancelled"])
def test_explicit_resolution_has_actual_time_and_only_fulfillment_shares_knowledge(status):
    d, state, event_id = scheduled(private=True)
    final, _, _, events = apply_plan(state, d, plan(elapsedMinutes=55,
        eventResolutions=[{"eventId": event_id, "status": status, "atMinute": 60, "description": "约定的处理结果"}]), "resolve")
    assert not final["queue"] and final["resolvedEvents"] == [event_id]
    assert events[0]["minute"] == 60 and events[0]["scheduledAtMinute"] == 5
    assert events[0]["resolution"] == status
    assert ("secret" in final["known"]) == (status == "fulfilled")


@pytest.mark.parametrize("case", ["unknown", "duplicate", "early-fulfilled", "early-missed"])
def test_invalid_resolution_keeps_original_pending_state(case):
    d, state, event_id = scheduled()
    before = deepcopy(state)
    resolution = {"eventId": "unknown" if case == "unknown" else event_id,
                  "status": "missed" if case == "early-missed" else "fulfilled", "description": "处理约定"}
    with pytest.raises(Problem) as error:
        apply_plan(state, d, plan(elapsedMinutes=55 if case in ("unknown", "duplicate") else 0,
                                 eventResolutions=[resolution] * (2 if case == "duplicate" else 1)), "invalid")
    assert error.value.code == "WORLD_EVENT_INVALID" and state == before


def test_future_event_can_be_cancelled_without_delivering_its_private_message():
    d, state, event_id = scheduled(private=True)
    final, _, _, events = apply_plan(state, d, plan(elapsedMinutes=0,
        eventResolutions=[{"eventId": event_id, "status": "cancelled", "description": "取消寄信"}]), "cancel")
    assert not final["queue"] and final["known"] == [] and events[0]["minute"] == 5


def test_important_decision_bounds_ordinary_wait_but_allows_authorized_chapter_progress():
    d, state, event_id = scheduled(decision=True)
    with pytest.raises(Problem) as error:
        apply_plan(state, d, plan(elapsedMinutes=120), "skip")
    assert error.value.code == "WORLD_DECISION_REQUIRED"
    paused, _, _, events = apply_plan(state, d, plan(elapsedMinutes=55), "pause")
    assert paused["minute"] == 60 and paused["queue"] and events[0]["type"] == "deadline_reached"
    with pytest.raises(Problem):
        apply_plan(paused, d, plan(elapsedMinutes=1), "skip-overdue")
    finished, _, _, _ = apply_plan(paused, d, plan(elapsedMinutes=0,
        eventResolutions=[{"eventId": event_id, "status": "fulfilled", "description": "我作出了选择"}]), "choose")
    assert not finished["queue"]
    written, _, _, _ = apply_plan(state, d, plan(elapsedMinutes=120,
        eventResolutions=[{"eventId": event_id, "status": "fulfilled", "description": "在授权写作内处理约定"}]),
        "chapter", allow_decisions=True)
    assert written["minute"] == 125 and not written["queue"]


def test_ready_decision_can_be_completed_with_required_time_and_resource_cost():
    d, state, event_id = scheduled(decision=True)
    d["rules"]["actionCosts"] = {"action": {"minutes": 5, "stamina": 2}}
    state["entities"]["player"]["fields"]["stamina"] = 10
    paused, _, _, _ = apply_plan(state, d, plan(elapsedMinutes=55), "pause", action_kind="wait")
    value = plan(elapsedMinutes=5,
        resources=[{"entityId": "player", "field": "stamina", "operation": "spend", "amount": 2, "reason": "完成选择"}],
        eventResolutions=[{"eventId": event_id, "status": "fulfilled", "description": "完成眼前的选择"}])
    final, _, _, _ = apply_plan(paused, d, value, "paid-choice", action_kind="action")
    assert final["minute"] == 65 and final["entities"]["player"]["fields"]["stamina"] == 8
    assert not final["queue"] and paused["queue"] and paused["minute"] == 60


def test_completing_current_decision_cannot_skip_the_next_choice():
    d, state, event_id = scheduled(decision=True)
    state, d, _, _ = apply_plan(state, d, plan(elapsedMinutes=0, events=[{
        "type": "promise", "description": "下一次选择", "dueMinute": 80,
        "visibleTo": ["player"], "requiresDecision": True}]), "next-choice")
    paused, _, _, _ = apply_plan(state, d, plan(elapsedMinutes=55), "pause", action_kind="wait")
    with pytest.raises(Problem) as error:
        apply_plan(paused, d, plan(elapsedMinutes=25,
            eventResolutions=[{"eventId": event_id, "status": "fulfilled", "description": "仅完成眼前选择"}]),
            "skip-next", action_kind="action")
    assert error.value.code == "WORLD_DECISION_REQUIRED" and paused["minute"] == 60


@pytest.mark.parametrize("resolve", [False, True])
def test_autoplay_delegation_requires_an_explicit_result_for_each_crossed_choice(resolve):
    d, state, event_id = scheduled(decision=True)
    value = plan(elapsedMinutes=120, eventResolutions=[{
        "eventId": event_id, "status": "fulfilled", "description": "授权章节内实际作出选择"}] if resolve else [])
    if resolve:
        final, _, _, _ = apply_plan(state, d, value, "autoplay", allow_decisions=True)
        assert final["minute"] == 125 and final["resolvedEvents"] == [event_id] and not final["queue"]
    else:
        with pytest.raises(Problem) as error:
            apply_plan(state, d, value, "autoplay", allow_decisions=True)
        assert error.value.code == "WORLD_DECISION_REQUIRED"
    assert state["minute"] == 5 and state["queue"]


def test_ordinary_action_cannot_preselect_a_future_important_decision():
    d, state, event_id = scheduled(decision=True)
    with pytest.raises(Problem) as error:
        apply_plan(state, d, plan(elapsedMinutes=120, eventResolutions=[{
            "eventId": event_id, "status": "fulfilled", "description": "提前代替玩家选择"}]),
            "premature", action_kind="action")
    assert error.value.code == "WORLD_DECISION_REQUIRED"


def test_new_important_choice_at_the_end_of_this_scene_remains_pending():
    d, state = initial_state(definition(), "player")
    paused, _, _, events = apply_plan(state, d, plan(elapsedMinutes=60, events=[{
        "type": "promise", "description": "章末出现新的决定", "dueMinute": 60,
        "visibleTo": ["player"], "requiresDecision": True}]), "new-choice", allow_decisions=True)
    assert paused["minute"] == 60 and len(paused["queue"]) == 1 and not paused["resolvedEvents"]
    assert paused["queue"][0]["decisionActorId"] == "player" and events[0]["type"] == "deadline_reached"
    with pytest.raises(Problem) as error:
        apply_plan(paused, d, plan(elapsedMinutes=5), "skip-chapter-end", action_kind="wait")
    assert error.value.code == "WORLD_DECISION_REQUIRED"


@pytest.mark.parametrize("at_minute,allowed", [(None, True), (60, True), (61, False), (126, False)])
def test_cancelled_decision_only_removes_the_boundary_if_cancelled_in_time(at_minute, allowed):
    d, state, event_id = scheduled(decision=True)
    value = plan(elapsedMinutes=120, eventResolutions=[{
        "eventId": event_id, "status": "cancelled", "atMinute": at_minute, "description": "先取消约定再离开"}])
    if allowed:
        final, _, _, events = apply_plan(state, d, value, "cancel-then-wait", action_kind="wait")
        assert final["minute"] == 125 and not final["queue"]
        assert events[0]["minute"] == (5 if at_minute is None else at_minute)
    else:
        with pytest.raises(Problem) as error:
            apply_plan(state, d, value, "late-cancellation", action_kind="wait")
        assert error.value.code == "WORLD_DECISION_REQUIRED"


@pytest.mark.parametrize("at_minute", [4, 59, 126])
def test_resolution_timestamp_must_be_in_this_turn_and_not_before_the_deadline(at_minute):
    d, state, event_id = scheduled(private=True)
    with pytest.raises(Problem) as error:
        apply_plan(state, d, plan(elapsedMinutes=120, eventResolutions=[{
            "eventId": event_id, "status": "fulfilled", "atMinute": at_minute, "description": "无效时间的履约"}]), "bad-time")
    assert error.value.code == "WORLD_EVENT_INVALID" and state["known"] == []


@pytest.fixture
def event_provider():
    assert settings.ai_mode == "mock"
    original = MockAdapter.generate
    seen = {"director": [], "narrator": [], "review": []}

    def generate(adapter, profile, prompt, data, limit):
        result = original(adapter, profile, prompt, data, limit)
        if prompt.startswith("world-prepare"):
            d = definition()
            d["entities"].append(EntityDefinition.model_validate({"id": "other", "name": "柳影",
                "type": "character", "source": "game", "description": "渡口旅人"}).model_dump())
            d["initial"]["other"] = {"alive": True, "location": "pier"}
            d["initial"]["player"]["fields"] = {"stamina": 10}
            result["text"] = json.dumps({k: v for k, v in d.items() if k in PrepareOutput.model_fields}, ensure_ascii=False)
        elif prompt.startswith("world-director"):
            seen["director"].append(deepcopy(data))
            value = plan(elapsedMinutes=0, options=[{"text": "观察渡口", "kind": "observe"}])
            if data["action"] == "安排私信":
                value["events"] = [{"type": "knowledge_shared", "entityId": "npc", "description": "顾安约定的私信",
                                    "loreIds": ["secret"], "visibleTo": ["player"], "dueMinute": data["state"]["minute"] + 60,
                                    "requiresDecision": seen.get("decision", False)},
                                   {"type": "private_note", "entityId": "player", "description": "仅顾安见过的红蜡标记", "visibleTo": ["player"]},
                                   {"type": "street_news", "entityId": "pier", "description": "渡口铃声", "visibleTo": ["all"]}]
            elif data["action"] == "等待私信":
                value["elapsedMinutes"] = 65
            elif data["action"] == "等待选择":
                value["elapsedMinutes"] = data["actionRules"]["pauseAtMinute"] - data["state"]["minute"]
            elif data["action"] == "处理私信":
                value["eventResolutions"] = [{"eventId": data["queuedEvents"][0]["id"], "status": "fulfilled", "description": "约定的私信送达原收信人"}]
            elif data["action"] in ("检查行动规则", "完成选择"):
                value["elapsedMinutes"] = data["actionRules"]["costs"].get("minutes", 0)
                value["resources"] = [{"entityId": data["state"]["playerId"], "field": key, "operation": "spend", "amount": amount,
                                       "reason": "本轮行动的明确代价", "visibleTo": ["player"]}
                                      for key, amount in data["actionRules"]["costs"].items() if key != "minutes"]
                if data["action"] == "完成选择":
                    value["eventResolutions"] = [{"eventId": data["actionRules"]["readyDecisionIds"][0],
                        "status": "fulfilled", "description": "顾安作出选择并收到私信"}]
            elif data["autoplay"] and seen.get("chapterDecisions"):
                if data["chapterIndex"] == 1:
                    value["elapsedMinutes"] = 60
                    value["events"] = [{"type": "knowledge_shared", "entityId": "npc", "description": "章末约定的私信",
                        "loreIds": ["secret"], "visibleTo": ["player"], "dueMinute": data["state"]["minute"] + 60,
                        "requiresDecision": True}]
                else:
                    value["elapsedMinutes"] = 5
                    value["eventResolutions"] = [{"eventId": data["state"]["queue"][0]["id"],
                        "status": "fulfilled", "description": "第二章完成选择并打开私信"}]
                    value["events"] = [{"type": "promise", "description": "下一段旅途的约定", "visibleTo": ["player"],
                        "dueMinute": data["state"]["minute"] + 120, "requiresDecision": True}]
            result["text"] = json.dumps(value, ensure_ascii=False)
        elif prompt.startswith("world-narrator"):
            seen["narrator"].append(deepcopy(data))
            result["text"] = "渡口的风吹过岸边。" + "。".join(e["description"] for e in data.get("events", [])) + "你留意眼前的变化。"
            if data.get("chapter"):
                result["text"] += "\n\n" + "\n\n".join(
                    "顾安把船边散落的绳索收整齐，向老船夫道了谢。雾渐渐薄了，木板上留下浅浅的水迹。他沿岸走过一排旧屋，认出门前的铜铃，停下来敲了三声。屋里有人应声，将一盏灯推到窗前。"
                    for _ in range(8))
        elif prompt.startswith("world-review"):
            seen["review"].append(deepcopy(data))
        result["usage"]["outputTokens"] = len(result["text"])
        return result

    with patch.object(MockAdapter, "generate", generate):
        yield seen


def act(client, world, line, message, key, kind="wait"):
    job = post(client, f"/worlds/{world['id']}/lines/{line['id']}/actions",
               {"expectedCheckpointId": line["headCheckpointId"], "message": message, "kind": kind, "turnId": key}, key)
    process(job["taskId"])
    with SessionLocal() as db:
        assert db.get(Task, job["taskId"]).state == "succeeded"
    return read_line(client, world, line)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("decision", [False, True])
def test_private_history_and_queued_knowledge_keep_the_original_receiver_after_identity_change(
        client, reader, demo, profile, event_provider, legacy, decision):
    event_provider["decision"] = decision
    world, line = start(client, reader, demo, profile)
    alice = act(client, world, line, "安排私信", "schedule")
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, alice["headCheckpointId"])
        if legacy:
            state = deepcopy(cp.state)
            for event in state["queue"]:
                for key in ("audiencePlayerId", "audienceIds", "audienceAll", "decisionActorId"):
                    event.pop(key, None)
            cp.state = state
            cp.content_hash = checkpoint_hash(cp.state, cp.definition, cp.text, cp.parent_checkpoint_id)
            for event in db.scalars(select(WorldEvent).where(WorldEvent.checkpoint_id == cp.id)):
                value = deepcopy(event.data)
                for key in ("audiencePlayerId", "audienceIds", "audienceAll", "decisionActorId"):
                    value.pop(key, None)
                event.data = value
            db.commit()
        frozen = deepcopy((cp.state, cp.definition, cp.memory, cp.content_hash))
    bob = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": alice["headCheckpointId"], "mode": "identity", "characterId": "other"}, "bob")
    events_url = lambda cp: API + f"/worlds/{world['id']}/checkpoints/{cp}/events"
    assert "红蜡标记" in client.get(events_url(alice["headCheckpointId"])).text
    history = client.get(events_url(bob["headCheckpointId"])).json()["items"]
    assert "street_news" in {e["type"] for e in history} and "private_note" not in {e["type"] for e in history}
    waited = act(client, world, bob, "等待私信", "wait")
    assert event_provider["director"][-1]["actionRules"]["pauseAtMinute"] is None
    assert not event_provider["director"][-1]["actionRules"]["readyDecisionIds"]
    assert waited["head"]["state"]["lore"] == []
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, waited["headCheckpointId"])
        assert cp.state["queue"] and not cp.state["resolvedEvents"]
        event = db.scalar(select(WorldEvent).where(WorldEvent.checkpoint_id == cp.id, WorldEvent.type == "deadline_reached"))
        assert event.minute == event.data["minute"] == 60
        recalled = context(db, cp, db.get(World, world["id"]), "私信")
        assert next(e for e in recalled["recalledEvents"] if e["type"] == "deadline_reached")["minute"] == 60
    assert "约定已到期" not in waited["head"]["text"]
    completed = act(client, world, waited, "处理私信", "fulfill")
    assert completed["head"]["state"]["lore"] == []
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, completed["headCheckpointId"])
        assert "secret" in cp.state["entities"]["player"]["known"] and "secret" not in cp.state["entities"]["other"]["known"]
        assert not cp.state["queue"] and cp.state["resolvedEvents"]
        old = db.get(WorldCheckpoint, alice["headCheckpointId"])
        assert (old.state, old.definition, old.memory, old.content_hash) == frozen
    public = client.get(events_url(completed["headCheckpointId"])).json()["items"]
    assert not any(e["type"] in ("knowledge_shared", "private_note", "deadline_reached") for e in public)
    assert not event_provider["narrator"][-1]["events"]


def test_actual_worker_pauses_then_completes_the_decision_and_pays_once(
        client, reader, demo, profile, event_provider):
    event_provider["decision"] = True
    world, line = start(client, reader, demo, profile,
        {"actionCosts": {"action": {"minutes": 5, "stamina": 2}}})
    scheduled_line = act(client, world, line, "安排私信", "schedule", kind="observe")
    paused = act(client, world, scheduled_line, "等待选择", "pause", kind="wait")
    assert paused["head"]["state"]["minute"] == 60 and not paused["head"]["state"]["lore"]
    completed = act(client, world, paused, "完成选择", "choose", kind="action")
    assert completed["head"]["state"]["minute"] == 65
    own = next(e for e in completed["head"]["state"]["entities"] if e["id"] == "player")
    assert own["state"]["fields"]["stamina"] == 8 and completed["head"]["state"]["lore"]
    with SessionLocal() as db:
        turn = db.scalar(select(WorldTurn).where(WorldTurn.checkpoint_id == completed["headCheckpointId"]))
        task_id = turn.task_id
        calls = db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id))
        assert calls == 3
    process(task_id)
    assert read_line(client, world, completed)["headCheckpointId"] == completed["headCheckpointId"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id)) == calls


def test_two_chapters_carry_a_new_pending_choice_forward_and_commit_only_on_acceptance(
        client, reader, demo, profile, event_provider):
    world, line = start(client, reader, demo, profile)
    event_provider["chapterDecisions"] = True
    before = deepcopy(line)
    job = post(client, f"/worlds/{world['id']}/lines/{line['id']}/write", {
        "chapterCount": 2, "expectedCheckpointId": line["headCheckpointId"]}, "two-chapter-choices")
    process(job["taskId"])
    with SessionLocal() as db:
        task = db.get(Task, job["taskId"])
        assert task.state == "succeeded", task.error_code
        chapters = db.get(WorldTurn, job["turnId"]).result["chapters"]
        first_event = chapters[0]["state"]["queue"][0]["id"]
        assert [chapter["state"]["minute"] for chapter in chapters] == [60, 65]
        assert not chapters[0]["state"]["known"] and "secret" in chapters[1]["state"]["known"]
        assert chapters[1]["state"]["resolvedEvents"] == [first_event]
        assert chapters[1]["state"]["queue"][0]["id"] != first_event
        assert chapters[1]["state"]["queue"][0]["dueMinute"] == 180
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task.id)) == 6
    assert read_line(client, world, line) == before
    assert event_provider["director"][-1]["state"]["queue"][0]["description"] == chapters[0]["state"]["queue"][0]["description"]
    assert event_provider["director"][-1]["autoplay"] is True
    accepted = post(client, f"/worlds/{world['id']}/turns/{job['turnId']}/accept", {
        "chapterCount": 2, "expectedCheckpointId": line["headCheckpointId"]}, "accept-two-choices")
    final = read_line(client, world, line)
    assert final["headCheckpointId"] == accepted["checkpointId"] and final["head"]["state"]["minute"] == 65
    assert final["head"]["state"]["lore"][0]["id"] == "secret"
    with SessionLocal() as db:
        events = list(db.scalars(select(WorldEvent).where(WorldEvent.world_id == world["id"])))
        assert any(event.type == "deadline_reached" and event.minute == 60 for event in events)
        assert any(event.type == "knowledge_shared" and event.minute == 65 for event in events)
    process(job["taskId"])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == job["taskId"])) == 6


def test_visible_deadline_and_fulfillment_enter_narration_and_review_with_correct_minutes(
        client, reader, demo, profile, event_provider):
    world, line = start(client, reader, demo, profile)
    scheduled_line = act(client, world, line, "安排私信", "schedule")
    waited = act(client, world, scheduled_line, "等待私信", "wait")
    assert "约定已到期，尚未处理" in waited["head"]["text"] and not waited["head"]["state"]["lore"]
    event = event_provider["narrator"][-1]["events"][0]
    assert event["type"] == "deadline_reached" and event["minute"] == 60 and event["scheduledAtMinute"] == 0
    assert event_provider["review"][-1]["scene"]["events"] == event_provider["narrator"][-1]["events"]
    completed = act(client, world, waited, "处理私信", "fulfill")
    assert completed["head"]["state"]["lore"][0]["id"] == "secret"
    assert "约定的私信送达" in completed["head"]["text"]
    assert event_provider["narrator"][-1]["events"][0]["minute"] == 65


@pytest.mark.parametrize("kind,expected_minute,expected_stamina", [("action", 5, 8), ("observe", 0, 10)])
def test_identical_text_passes_the_selected_action_rules_to_the_director_and_commits_once(
        client, reader, demo, profile, event_provider, kind, expected_minute, expected_stamina):
    rules = {"actionCosts": {"action": {"minutes": 5, "stamina": 2}}, "abilityRequirements": {"action": {"stamina": 2}}}
    world, line = start(client, reader, demo, profile, rules)
    current = act(client, world, line, "检查行动规则", "kind", kind=kind)
    data = event_provider["director"][-1]
    assert data["actionKind"] == kind and data["actionRules"]["costs"] == rules["actionCosts"].get(kind, {})
    assert data["actionRules"]["abilityRequirements"] == rules["abilityRequirements"].get(kind, {})
    assert current["head"]["state"]["minute"] == expected_minute
    own = next(e for e in current["head"]["state"]["entities"] if e["id"] == "player")
    assert own["state"]["fields"]["stamina"] == expected_stamina
