"""Author edits are explicit forks, not a loophole for director ledger updates."""
from copy import deepcopy
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import UsageRecord
from app.errors import Problem
from app.world.schemas import CorrectionInput
from app.world.corrections import corrected_state
from app.world.state import initial_state, apply_plan, public_view, checkpoint_hash
from app.world.models import World, WorldCheckpoint, WorldLine
from .test_world import definition, plan, start, post, API


def change(state, d, field, value, entity="player"):
    return corrected_state(state, d, CorrectionInput(expectedCheckpointId="head", entityId=entity,
        field=field, value=value, reason="读者明确调整"), "new-head")


@pytest.mark.parametrize("policy,ended", [("end", True), ("rescue", False), ("rebirth", False)])
def test_explicit_death_policy_recomputes_end_without_resurrection(policy, ended):
    d, state = initial_state(definition(), "player")
    state["entities"]["player"]["alive"], state["ended"] = False, True
    original = deepcopy((state, d))
    updated, version, changes = change(state, d, "rule:deathPolicy", policy)
    assert updated["ended"] is ended and updated["entities"]["player"]["alive"] is False
    assert version["version"] == d["version"] + 1 and changes[0]["before"] == "end"
    assert (state, d) == original


def test_explicit_life_and_location_edit_preserve_ordinary_rules():
    d, state = initial_state(definition(), "player")
    state["entities"]["player"]["alive"], state["ended"] = False, True
    d["rules"]["routes"] = {"pier": []}
    alive, d2, changes = change(state, d, "alive", True)
    assert not alive["ended"] and changes[0]["before"] is False
    moved, _, _ = change(alive, d2, "location", "far")
    assert moved["location"] == "far"
    with pytest.raises(Problem) as denied:
        apply_plan(state, d, plan(updates=[{"entityId": "player", "field": "alive", "value": True, "reason": "无代价", "visibleTo": ["player"]}]), "turn")
    assert denied.value.code == "WORLD_DEATH_RULE"
    with pytest.raises(Problem) as denied:
        apply_plan(alive, d2, plan(updates=[{"entityId": "player", "field": "location", "value": "far", "reason": "越过路线", "visibleTo": ["player"]}]), "turn")
    assert denied.value.code == "WORLD_ROUTE_RULE"


@pytest.mark.parametrize("invalid", [True, -1, 1.5, "2", 100001, None])
def test_inventory_single_item_requires_bounded_strict_integer(invalid):
    d, state = initial_state(definition(), "player")
    with pytest.raises(Problem) as denied:
        change(state, d, "inventory:letter", invalid)
    assert denied.value.code == "WORLD_INVENTORY_INVALID"
    assert state["entities"]["player"]["inventory"] == {"letter": 1}


def test_inventory_edit_keeps_other_items_and_uniqueness_and_private_before():
    d, state = initial_state(definition(), "player")
    state2, d2, changes = change(state, d, "entity:add", {"type": "item", "name": "药丸", "stackable": True})
    pill = changes[0]["entityId"]
    state3, _, _ = change(state2, d2, "inventory:"+pill, 2)
    assert state3["entities"]["player"]["inventory"] == {"letter": 1, pill: 2}
    with pytest.raises(Problem) as duplicate:
        change(state3, d2, "inventory:letter", 1, "npc")
    assert duplicate.value.code == "WORLD_INVENTORY_INVALID"
    removed, _, changes = change(state3, d2, "inventory:letter", 0)
    assert changes[0]["before"] == 1 and "letter" not in removed["entities"]["player"]["inventory"]
    assigned, _, changes = change(removed, d2, "inventory:letter", 1, "npc")
    assert assigned["entities"]["npc"]["inventory"]["letter"] == 1 and changes[0]["before"] is None
    with pytest.raises(Problem):
        change(state, d, "inventory:pier", 1)
    with pytest.raises(Problem):
        apply_plan(state, d, plan(updates=[{"entityId": "player", "field": "inventory:letter", "value": 0, "reason": "绕过转移", "visibleTo": ["player"]}]), "turn")


def test_correction_before_is_public_and_entity_add_is_explicit_versioned():
    d, state = initial_state(definition(), "player")
    state["entities"]["npc"]["fields"]["money"] = 999
    for field, value in [("goal", "新的隐秘目标"), ("field:money", 2)]:
        _, _, changes = change(state, d, field, value, "npc")
        assert changes[0]["before"] is None
    state["observed"] = {"npc": {"field:money": 10}}
    _, _, changes = change(state, d, "field:money", 2, "npc")
    assert changes[0]["before"] == 10
    state["location"] = "far"
    added, new, changes = change(state, d, "entity:add", {"type": "character", "name": "向导", "description": "读者设定"})
    guide = changes[0]["entityId"]
    assert added["entities"][guide]["location"] == "far" and guide in added["discovered"]
    assert new["version"] == d["version"] + 1 and new["initial"] == d["initial"]
    assert next(e for e in public_view(added, new)["entities"] if e["id"] == guide)["source"] == "user"
    for value in [{"type": "item", "name": "唯一的信"}, {"type": "item", "name": "新物", "hiddenGoal": "秘密"}]:
        with pytest.raises(Problem):
            change(state, d, "entity:add", value)


def test_relation_reason_survives_later_turns_and_hidden_changes_remain_hidden():
    d, state = initial_state(definition(), "player")
    related, updated, _ = change(state, d, "relation:npc", "互相信任")
    later, updated, _, _ = apply_plan(related, updated, plan(), "later-turn")
    own = next(e for e in public_view(later, updated)["entities"] if e["id"] == "player")
    assert own["state"]["relationEvidence"]["npc"] == {"reason": "读者明确调整", "checkpointId": "new-head", "minute": 0}
    hidden, updated, _, _ = apply_plan(later, updated, plan(updates=[{"entityId": "npc", "field": "relation:player", "value": "暗中不信任", "reason": "秘密动机", "visibleTo": []}]), "hidden-turn")
    npc = next(e for e in public_view(hidden, updated)["entities"] if e["id"] == "npc")
    assert not npc["state"]["relationEvidence"] and "秘密动机" not in str(npc)


def test_hidden_own_relationship_reason_is_not_public_or_a_replacement_for_known_reason():
    d, state = initial_state(definition(), "player")
    public, d, _ = change(state, d, "relation:npc", "互相信任")
    hidden_plan = plan(updates=[{"entityId": "player", "field": "relation:npc", "value": "互相信任", "reason": "NPC暗中受某人指使", "visibleTo": []}])
    hidden, d, _, _ = apply_plan(public, d, hidden_plan, "secret-turn")
    assert hidden["entities"]["player"]["relationEvidence"]["npc"]["reason"] == "NPC暗中受某人指使"
    own = next(e for e in public_view(hidden, d)["entities"] if e["id"] == "player")
    assert own["state"]["relationEvidence"]["npc"]["reason"] == "读者明确调整"
    assert "NPC暗中受某人指使" not in str(public_view(hidden, d))
    unknown, d, _, _ = apply_plan(state, d, hidden_plan, "secret-first")
    own = next(e for e in public_view(unknown, d)["entities"] if e["id"] == "player")
    assert not own["state"]["relationEvidence"]


def test_known_route_editor_preserves_hidden_edges_and_rejects_unknown_choices():
    d, state = initial_state(definition(), "player")
    d["rules"]["routes"] = {"pier": ["far"]}
    corrected, updated, changes = change(state, d, "route:pier", [])
    assert updated["rules"]["routes"]["pier"] == ["far"] and changes[0]["before"] == []
    with pytest.raises(Problem):
        change(state, d, "route:pier", ["far"])
    state["discovered"].append("far")
    _, updated, changes = change(state, d, "route:pier", [])
    assert updated["rules"]["routes"]["pier"] == [] and changes[0]["before"] == ["far"]


def test_long_author_reason_is_preserved_and_does_not_use_director_reason_limit():
    d, state = initial_state(definition(), "player")
    reason = "调整设定" * 200
    updated, _, changes = corrected_state(state, d, CorrectionInput(expectedCheckpointId="head", entityId="player", field="field:勇气", value=7, reason=reason), "edit")
    assert updated["entities"]["player"]["fields"]["勇气"] == 7 and changes[0]["reason"] == reason
    updated, _, _ = corrected_state(state, d, CorrectionInput(expectedCheckpointId="head", entityId="player", field="relation:npc", value="信任", reason=reason), "edit")
    assert updated["entities"]["player"]["relationEvidence"]["npc"]["reason"] == reason


def test_death_policy_and_inventory_api_forks_are_idempotent_and_source_frozen(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    path = f"/worlds/{world['id']}/corrections"
    with SessionLocal() as db:
        origin = db.get(WorldCheckpoint, line["headCheckpointId"])
        snapshot = deepcopy((origin.state, origin.definition, origin.content_hash))
        calls = db.scalar(select(func.count()).select_from(UsageRecord))
    dead = post(client, path, {"expectedCheckpointId": line["headCheckpointId"], "entityId": line["head"]["state"]["playerId"], "field": "alive", "value": False, "reason": "测试死亡"}, "dead-edit")
    assert dead["head"]["state"]["ended"]
    body = {"expectedCheckpointId": dead["headCheckpointId"], "field": "rule:deathPolicy", "value": "rescue", "reason": "明确允许救援"}
    rescue = post(client, path, body, "rescue-edit")
    assert post(client, path, body, "rescue-edit") == rescue and not rescue["head"]["state"]["ended"]
    queued = post(client, f"/worlds/{world['id']}/lines/{rescue['id']}/actions", {"expectedCheckpointId": rescue["headCheckpointId"], "message": "等待救援", "kind": "wait", "turnId": "rescue-wait"}, "rescue-wait")
    assert queued["taskId"]
    with SessionLocal() as db:
        origin = db.get(WorldCheckpoint, line["headCheckpointId"])
        assert (origin.state, origin.definition, origin.content_hash) == snapshot
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == calls


def test_identity_seed_uses_current_location_without_changing_frozen_definition(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, line["headCheckpointId"])
        d, state = initial_state(definition(), "player")
        state["location"] = "far";state["entities"]["player"]["location"] = "far"
        state["entities"]["npc"].update(location="far", known=["secret"])
        cp.definition, cp.state = d, state
        cp.content_hash = checkpoint_hash(state, d, cp.text, cp.parent_checkpoint_id)
        db.commit()
    chosen = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": line["headCheckpointId"], "mode": "identity", "characterId": "npc"}, "identity-current")
    custom = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": line["headCheckpointId"], "mode": "identity", "customName": "旅人", "customIdentity": "正在寻找旧友"}, "identity-new")
    with SessionLocal() as db:
        origin = db.get(WorldCheckpoint, line["headCheckpointId"])
        existing = db.get(WorldCheckpoint, chosen["headCheckpointId"])
        fresh = db.get(WorldCheckpoint, custom["headCheckpointId"])
        assert origin.definition == d == existing.definition
        assert existing.state["known"] == ["secret"] and existing.state["location"] == "far"
        assert fresh.state["location"] == "far" and fresh.definition["location"] == "pier"
        assert fresh.definition["initial"] == d["initial"] and fresh.definition["version"] == d["version"] + 1
        assert fresh.state["entities"]["npc"] == state["entities"]["npc"]


@pytest.mark.parametrize("field,value", [("inventory:not-an-item", 1), ("inventory:letter", 2), ("rule:unknown", True)])
def test_invalid_correction_rolls_back_fork_and_active_line(client, reader, demo, profile, field, value):
    world, line = start(client, reader, demo, profile)
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, line["headCheckpointId"])
        d, state = initial_state(definition(), "player")
        cp.definition, cp.state = d, state
        cp.content_hash = checkpoint_hash(state, d, cp.text, cp.parent_checkpoint_id)
        db.commit()
        counts = (db.scalar(select(func.count()).select_from(WorldLine)), db.scalar(select(func.count()).select_from(WorldCheckpoint)))
    for _ in range(2):
        failed = client.post(API+f"/worlds/{world['id']}/corrections", json={"expectedCheckpointId": line["headCheckpointId"], "entityId": "player", "field": field, "value": value, "reason": "故意无效的调整"}, headers={"Idempotency-Key": "invalid-edit"})
        assert failed.status_code == 422
        with SessionLocal() as db:
            assert db.get(World, world["id"]).active_line_id == line["id"]
            assert counts == (db.scalar(select(func.count()).select_from(WorldLine)), db.scalar(select(func.count()).select_from(WorldCheckpoint)))
