"""Ordinary death/recovery commits and identity-aware restart; isolated provider only."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import Task, UsageRecord, uid
from app.providers import MockAdapter
from app.world.memory import context
from app.world.models import World, WorldCheckpoint, WorldEvent, WorldMemory, WorldTurn
from app.world.state import checkpoint_hash, initial_state
from app.world.worker import process
from .test_world import API, definition, fast_fixture_stream, post, read_line, start

DEATH = "我尝试穿过急流"
RECOVER = "等待救援"


@pytest.fixture
def lifecycle_provider():
    assert settings.ai_mode == "mock"
    original = MockAdapter.generate
    controls = {"event": None, "cost": 2, "calls": []}

    def generate(adapter, profile, prompt, data, limit):
        result = original(adapter, profile, prompt, data, limit)
        if prompt.startswith("world-prepare"):
            value = json.loads(result["text"])
            player = next(e["id"] for e in value["entities"] if e["type"] == "character")
            value["initial"][player]["fields"] = {"money": 12}
            value["entities"].append({"id": "recovery-token", "name": "护身符", "type": "item", "source": "game"})
            value["initial"][player]["inventory"] = {"recovery-token": 1}
            result["text"] = json.dumps(value, ensure_ascii=False)
        elif prompt.startswith("world-director") and data["action"] in (DEATH, RECOVER):
            player = data["state"]["playerId"]
            controls["calls"].append({"action": data["action"], "alive": data["entityState"][player]["alive"]})
            dying = data["action"] == DEATH
            event = "death" if dying else controls["event"] or data["rules"]["deathPolicy"]
            outcome = "你不幸在急流中身亡。" if dying else "你恢复了生命，并付出了两枚银钱的代价。"
            value = {"title": "急流" if dying else "再次醒来", "summary": outcome,
                     "elapsedMinutes": 10 if dying else 60, "timeLabel": "入夜",
                     "updates": [{"entityId": player, "field": "alive", "value": not dying,
                                  "reason": outcome, "visibleTo": ["player"]}],
                     "events": [] if event == "missing" else [{"type": event, "entityId": player,
                                                                 "description": outcome, "visibleTo": ["player"]}],
                     "resources": [] if dying else [{"entityId": player, "field": "money",
                                                      "operation": "spend", "amount": controls["cost"],
                                                      "reason": "恢复生命的代价", "visibleTo": ["player"]}],
                     "publicOutcome": outcome, "critical": [outcome],
                     "options": [{"text": "观察岸边", "kind": "observe"}]}
            if not dying and controls.get("costKind") in ("item", "item-return"):
                value["resources"] = []
                destination = data["state"]["location"]
                value["transfers"] = [{"itemId": "recovery-token", "fromId": player, "toId": destination,
                                       "quantity": 1, "reason": "恢复生命的物品代价", "visibleTo": ["player"]}]
                if controls["costKind"] == "item-return":
                    value["transfers"].append({"itemId": "recovery-token", "fromId": destination, "toId": player,
                                               "quantity": 1, "reason": "原物返还", "visibleTo": ["player"]})
                value["publicOutcome"] = "你恢复了生命，并交出了护身符。"
            elif not dying and controls.get("costKind") == "refund":
                value["resources"].append({"entityId": player, "field": "money", "operation": "earn", "amount": 2,
                                           "reason": "退还救援押金", "visibleTo": ["player"]})
                value["events"].append({"type": "resource_reward", "entityId": player,
                                        "description": "退还救援押金", "visibleTo": ["player"]})
            result["text"] = json.dumps(value, ensure_ascii=False)
        elif prompt.startswith("world-narrator") and data["action"] in (DEATH, RECOVER):
            result["text"] = "水声渐渐远去，岸边的灯火映着渡口。" + data["outcome"] + "这次经历留在了你的故事中。"
        result["usage"]["outputTokens"] = len(result["text"])
        return result

    with patch.object(MockAdapter, "generate", generate):
        yield controls


def ordinary(client, world, line, message, key):
    return post(client, f"/worlds/{world['id']}/lines/{line['id']}/actions",
                {"expectedCheckpointId": line["headCheckpointId"], "message": message,
                 "kind": "wait" if message == RECOVER else "action", "turnId": key}, key)


def frozen(checkpoint):
    return deepcopy((checkpoint.state, checkpoint.definition, checkpoint.memory,
                     checkpoint.text, checkpoint.options, checkpoint.changes, checkpoint.content_hash))


def add_known_character(client, world, line):
    line = post(client, f"/worlds/{world['id']}/corrections", {
        "expectedCheckpointId": line["headCheckpointId"], "entityId": "", "field": "entity:add",
        "value": {"name": "渡口旅人", "type": "character", "description": "在渡口结识的旅人"},
        "reason": "为身份重开测试建立一个明确已知的独立人物"}, "known-character")
    player = line["head"]["state"]["playerId"]
    other = next(e["id"] for e in line["head"]["state"]["entities"]
                 if e["type"] == "character" and e["id"] != player)
    return line, other


def counts():
    with SessionLocal() as db:
        return tuple(db.scalar(select(func.count()).select_from(model))
                     for model in (WorldCheckpoint, WorldEvent, UsageRecord))


@pytest.mark.parametrize("policy", ["rescue", "rebirth"])
def test_ordinary_death_and_paid_recovery_commit_once(client, reader, demo, profile, lifecycle_provider, policy):
    world, line = start(client, reader, demo, profile, {"deathPolicy": policy})
    death_job = ordinary(client, world, line, DEATH, "death")
    process(death_job["taskId"])
    dead = read_line(client, world, line)
    player = dead["head"]["state"]["playerId"]
    own = next(e for e in dead["head"]["state"]["entities"] if e["id"] == player)
    assert own["state"]["alive"] is False and own["state"]["fields"]["money"] == 12
    assert not dead["head"]["state"]["ended"] and "身亡" in dead["head"]["text"]
    with SessionLocal() as db:
        parent = db.get(WorldCheckpoint, dead["headCheckpointId"])
        snapshot, before_minute = frozen(parent), parent.state["minute"]
    before = counts()
    job = ordinary(client, world, dead, RECOVER, "recover")
    process(job["taskId"])
    recovered = read_line(client, world, line)
    own = next(e for e in recovered["head"]["state"]["entities"] if e["id"] == player)
    assert own["state"]["alive"] is True and own["state"]["fields"]["money"] == 10
    assert recovered["head"]["state"]["minute"] == before_minute + 60
    assert recovered["headCheckpointId"] != dead["headCheckpointId"] and not recovered["pendingTaskId"]
    assert "恢复了生命" in recovered["head"]["text"]
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, recovered["headCheckpointId"])
        assert cp.kind == "turn" and cp.parent_checkpoint_id == dead["headCheckpointId"]
        assert frozen(db.get(WorldCheckpoint, dead["headCheckpointId"])) == snapshot
        assert db.get(Task, job["taskId"]).state == "succeeded"
        assert db.scalar(select(WorldTurn).where(WorldTurn.task_id == job["taskId"])).status == "committed"
        event = db.scalar(select(WorldEvent).where(WorldEvent.checkpoint_id == cp.id, WorldEvent.type == policy))
        assert event and event.entity_id == player and event.minute == cp.state["minute"]
        assert any(p["checkpointId"] == cp.id and event.data["id"] in p["eventIds"] for p in cp.memory["pending"])
    after = counts()
    assert tuple(a - b for a, b in zip(after, before)) == (1, 1, 3)
    assert ordinary(client, world, dead, RECOVER, "recover") == job
    process(job["taskId"])
    assert counts() == after and read_line(client, world, line) == recovered
    following = ordinary(client, world, recovered, "我留意岸边的灯火", "after-recovery")
    process(following["taskId"])
    assert read_line(client, world, line)["head"]["sequence"] == recovered["head"]["sequence"] + 1
    assert lifecycle_provider["calls"] == [{"action": DEATH, "alive": True}, {"action": RECOVER, "alive": False}]


@pytest.mark.parametrize("event,cost", [("rebirth", 2), ("missing", 2), ("rescue", 0)])
def test_invalid_ordinary_recovery_keeps_body_without_committing_or_charging_twice(
        client, reader, demo, profile, lifecycle_provider, event, cost):
    world, line = start(client, reader, demo, profile, {"deathPolicy": "rescue"})
    death = ordinary(client, world, line, DEATH, "death")
    process(death["taskId"])
    dead = read_line(client, world, line)
    lifecycle_provider.update(event=event, cost=cost)
    before = counts()
    job = ordinary(client, world, dead, RECOVER, "invalid-recovery")
    process(job["taskId"])
    assert read_line(client, world, line)["headCheckpointId"] == dead["headCheckpointId"]
    with SessionLocal() as db:
        task = db.get(Task, job["taskId"])
        turn = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task.id))
        assert task.state == "failed" and task.error_code == "WORLD_DEATH_RULE"
        assert turn.status == "draft" and "恢复了生命" in turn.draft_text
        assert task.checkpoint["worldStream"]["text"] == turn.draft_text
    after = counts()
    assert tuple(a - b for a, b in zip(after, before)) == (0, 0, 2)
    process(job["taskId"])
    assert ordinary(client, world, dead, RECOVER, "invalid-recovery") == job
    assert counts() == after


def test_ordinary_terminal_death_blocks_paid_action_and_restores_alive_checkpoint(client, reader, demo, profile, lifecycle_provider):
    world, line = start(client, reader, demo, profile)
    death = ordinary(client, world, line, DEATH, "death")
    process(death["taskId"])
    dead = read_line(client, world, line)
    assert dead["head"]["state"]["ended"] and dead["head"]["options"] == [] and "身亡" in dead["head"]["text"]
    before = counts()
    denied = client.post(API + f"/worlds/{world['id']}/lines/{line['id']}/actions", json={
        "expectedCheckpointId": dead["headCheckpointId"], "message": RECOVER, "kind": "wait", "turnId": "denied"
    }, headers={"Idempotency-Key": "denied"})
    assert denied.status_code == 409 and "WORLD_ENDED" in denied.text and counts() == before
    rechoose = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": dead["headCheckpointId"], "mode": "rechoose"}, "rechoose")
    assert rechoose["head"]["state"] == line["head"]["state"]
    restart = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": dead["headCheckpointId"], "mode": "restart"}, "restart")
    with SessionLocal() as db:
        root = db.get(WorldCheckpoint, restart["forkCheckpointId"])
        assert root.kind == "start" and root.sequence == 0
        assert root.state["entities"][root.state["playerId"]]["alive"] is True
    assert read_line(client, world, line) == dead and counts()[2] == before[2]


@pytest.mark.parametrize("descendant", ["continue", "rechoose", "correction", "rewrite", "death"])
def test_restart_inherits_identity_origin_and_excludes_abandoned_memory(client, reader, demo, profile, lifecycle_provider, descendant):
    world, line = start(client, reader, demo, profile)
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, line["headCheckpointId"])
        d, state = initial_state(definition(), "player")
        state["entities"]["npc"].update(location="far", known=["secret"])
        state["queue"] = [{"id": "promise", "type": "promise", "entityId": "npc", "description": "明天回信", "visibleTo": ["npc"], "dueMinute": 100}]
        cp.state, cp.definition = state, d
        cp.memory = {"pending": [], "episodes": [], "layers": []}
        cp.content_hash = checkpoint_hash(state, d, cp.text, cp.parent_checkpoint_id)
        db.commit()
    identity = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": line["headCheckpointId"], "mode": "identity", "characterId": "npc"}, "bob")
    assert identity["head"]["kind"] == "identity"
    with SessionLocal() as db:
        origin = db.get(WorldCheckpoint, identity["headCheckpointId"])
        snapshot = frozen(origin)
    job = ordinary(client, world, identity, DEATH if descendant == "death" else "我观察远城", "later")
    process(job["taskId"])
    later = read_line(client, world, identity)
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, later["headCheckpointId"])
        db.add(WorldMemory(id=uid(), owner_id=cp.owner_id, world_id=cp.world_id, line_id=cp.line_id,
                           checkpoint_id=cp.id, first_checkpoint_id=cp.id, last_checkpoint_id=cp.id,
                           text="废弃支线暗号：青铜扇", event_ids=[], level=1))
        db.commit()
    if descendant == "death":
        assert later["head"]["state"]["ended"] and "身亡" in later["head"]["text"]
        selected = later
    elif descendant == "correction":
        selected = post(client, f"/worlds/{world['id']}/corrections", {"expectedCheckpointId": later["headCheckpointId"], "entityId": "npc", "field": "field:money", "value": 7, "reason": "明确设定"}, "adjust")
    elif descendant == "rewrite":
        job = post(client, f"/worlds/{world['id']}/rewrite", {"checkpointId": later["headCheckpointId"]}, "rewrite")
        process(job["taskId"])
        selected = read_line(client, world, {"id": job["lineId"]})
    else:
        selected = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": later["headCheckpointId"], "mode": descendant}, "descendant")
    before = counts()
    restarted = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": selected["headCheckpointId"], "mode": "restart"}, "restart")
    with SessionLocal() as db:
        origin = db.get(WorldCheckpoint, identity["headCheckpointId"])
        restored = db.get(WorldCheckpoint, restarted["headCheckpointId"])
        assert restarted["forkCheckpointId"] == origin.id and frozen(origin) == snapshot
        assert (restored.state, restored.definition, restored.memory) == snapshot[:3]
        assert restored.state["playerId"] == "npc" and restored.state["known"] == ["secret"]
        assert restored.state["location"] == "far" and restored.state["queue"] == origin.state["queue"]
        data = context(db, restored, db.get(World, world["id"]), "青铜扇")
        assert "废弃支线暗号" not in json.dumps(data, ensure_ascii=False)
    assert counts()[2] == before[2] and read_line(client, world, identity) == later


@pytest.mark.parametrize("same_character", [False, True])
def test_restart_uses_most_recent_identity_even_when_same_character_reenters(client, reader, demo, profile, same_character):
    world, line = start(client, reader, demo, profile)
    player = line["head"]["state"]["playerId"]
    line, other = add_known_character(client, world, line)
    first = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": line["headCheckpointId"], "mode": "identity", "characterId": player if same_character else other}, "identity-one")
    job = ordinary(client, world, first, "我留意渡口", "advance")
    process(job["taskId"])
    advanced = read_line(client, world, first)
    latest = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": advanced["headCheckpointId"], "mode": "identity", "characterId": player}, "identity-two")
    restarted = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": latest["headCheckpointId"], "mode": "restart"}, "restart")
    assert restarted["forkCheckpointId"] == latest["headCheckpointId"]
    assert restarted["head"]["state"] == latest["head"]["state"]


def test_restart_recognizes_legacy_identity_but_not_copied_continuation_summary(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    line, other = add_known_character(client, world, line)
    identity = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": line["headCheckpointId"], "mode": "identity", "characterId": other}, "identity")
    with SessionLocal() as db:
        db.get(WorldCheckpoint, identity["headCheckpointId"]).kind = "fork"
        db.commit()
    continuation = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": identity["headCheckpointId"], "mode": "continue"}, "continue")
    restarted = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": continuation["headCheckpointId"], "mode": "restart"}, "restart")
    assert restarted["forkCheckpointId"] == identity["headCheckpointId"]


@pytest.mark.parametrize("policy", ["rescue", "rebirth"])
def test_ordinary_item_bearing_recovery_commits_real_cost_and_replays_without_duplication(
        client, reader, demo, profile, lifecycle_provider, policy):
    world, line = start(client, reader, demo, profile, {"deathPolicy": policy})
    job = ordinary(client, world, line, DEATH, "death");process(job["taskId"])
    dead = read_line(client, world, line)
    lifecycle_provider["costKind"] = "item"
    before = counts()
    job = ordinary(client, world, dead, RECOVER, "item-recovery");process(job["taskId"])
    recovered = read_line(client, world, line)
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, recovered["headCheckpointId"])
        own = cp.state["entities"][cp.state["playerId"]]
        assert own["alive"] is True and own["inventory"].get("recovery-token", 0) == 0 and own["fields"]["money"] == 12
        assert cp.state["entities"][cp.state["location"]]["inventory"]["recovery-token"] == 1
        assert db.get(Task, job["taskId"]).state == "succeeded"
        assert "护身符" in cp.text
    after = counts();assert tuple(a-b for a,b in zip(after,before)) == (1, 1, 3)
    process(job["taskId"]);assert ordinary(client, world, dead, RECOVER, "item-recovery") == job
    assert counts() == after and read_line(client, world, line) == recovered


@pytest.mark.parametrize("cost_kind", ["refund", "item-return"])
def test_returning_the_whole_recovery_price_is_rejected_with_body_retained(
        client, reader, demo, profile, lifecycle_provider, cost_kind):
    world, line = start(client, reader, demo, profile, {"deathPolicy": "rescue"})
    job = ordinary(client, world, line, DEATH, "death");process(job["taskId"])
    dead = read_line(client, world, line)
    lifecycle_provider["costKind"] = cost_kind
    before = counts()
    job = ordinary(client, world, dead, RECOVER, "returned-price");process(job["taskId"])
    assert read_line(client, world, line)["headCheckpointId"] == dead["headCheckpointId"]
    with SessionLocal() as db:
        task = db.get(Task, job["taskId"])
        turn = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task.id))
        assert task.state == "failed" and task.error_code == "WORLD_DEATH_RULE" and turn.draft_text
    after = counts();assert tuple(a-b for a,b in zip(after,before)) == (0, 0, 2)
    process(job["taskId"]);assert counts() == after
