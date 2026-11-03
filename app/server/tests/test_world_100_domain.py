"""Deterministic engine/archive stress, explicitly zero provider quality claim."""
from copy import deepcopy
import json
import os
from pathlib import Path
from sqlalchemy import select
from app import models as m
from app.db import SessionLocal
from app.domain import canonical, digest
from app.errors import Problem
from app.world import models as w
from app.world.memory import context, save_memory, narrator_context
from app.world.state import apply_plan, checkpoint_hash, ancestry, initial_state, public_view
from app.portable.snapshot import build_domain_snapshot
from app.portable.package import prepare_backup, write_backup
from app.portable.restore import restore_domain
from test_portable_domain import domain, migrate_owned_database

GOLDEN = json.loads((Path(__file__).parent / 'next/world-100-fixture.json').read_text('utf-8'))


def proposal(turn):
    return {"title": "第"+str(turn)+"步", "summary": "沿港口步行，记录第"+str(turn)+"步的结果。",
        "elapsedMinutes": 1, "timeLabel": "第"+str(turn)+"分钟", "location": None,
        "updates": [], "transfers": [], "resources": [], "events": [], "eventResolutions": [],
        "additions": [], "newLore": [], "learned": [], "publicOutcome": "走完这一段港口道路。",
        "options": [{"text": "沿岸查看路标", "kind": "observe"}, {"text": "向旅人询问北塔", "kind": "dialogue"},
                    {"text": "回到约定地点", "kind": "action"}], "critical": [], "resolvedUnresolved": [], "warnings": []}


def sample(state, player, npc):
    return {"minute": state["minute"], "energy": state["entities"][player]["fields"]["energy"],
        "money": state["entities"][player]["fields"]["money"],
        "npcEnergy": state["entities"][npc]["fields"]["energy"],
        "npcLampReady": state["entities"][npc]["fields"]["lampReady"], "pendingPromiseCount": len(state["queue"])}


def test_hundred_turn_ledger_promises_archive_recall_snapshot_and_private_restore(domain, tmp_path):
    trajectory = []; proof = {}; original = {}; promise_id = None
    with SessionLocal() as db:
        old_world = db.get(w.World, domain["world"]); definition = deepcopy(old_world.definition)
        actors = [entity["id"] for entity in definition["entities"] if entity["type"] == "character"]
        player, npc = actors[:2]
        remote = m.uid()
        definition["entities"].append({"id": remote, "name": GOLDEN["background"]["location"], "type": "place",
            "description": "隔着海湾的修灯地点", "aliases": [], "traits": "", "source": "user", "evidence": [],
            "hiddenGoal": "", "knownFacts": [], "knownBy": [npc], "stackable": False})
        definition["initial"][npc]["location"] = remote
        definition["rules"]["actionCosts"] = {"action": {"minutes": 1, "energy": 1}}
        definition["initial"][player]["fields"] = {"energy": 100, "money": None}
        definition["initial"][npc]["fields"] = {"energy": 100, "lampReady": False}
        definition["initial"][npc]["goal"] = "PRIVATE_NPC_STRESS_REPAIR_GOAL"
        definition, state = initial_state(definition, player)
        world_id, line_id, root_id = m.uid(), m.uid(), m.uid()
        source = deepcopy(old_world.source)
        chapter = db.get(m.ContentBlock, domain["block"]).chapter_id
        fact = GOLDEN["sourceFact"]
        source["blocks"] = source["blocks"]+[{"blockId": m.uid(), "chapterId": chapter, "text": fact, "source": "original"},
            {"blockId": m.uid(), "chapterId": chapter, "text": "海风吹过港口，守灯人沿石阶检查每一盏灯，旅人记录脚下的潮水。"*4000, "source": "original"}]
        world = w.World(id=world_id, owner_id=domain["owner"], name="百轮确定性港口", source_title="原创压力材料",
            source_book_id=domain["book"], source=source, definition=definition, status="active", active_line_id=line_id)
        line = w.WorldLine(id=line_id, owner_id=domain["owner"], world_id=world_id, name="百轮主线", head_checkpoint_id=root_id)
        root = w.WorldCheckpoint(id=root_id, owner_id=domain["owner"], world_id=world_id, line_id=line_id, sequence=1,
            title="出发", text="守灯人开始步行。", state=state, definition=definition,
            content_hash=checkpoint_hash(state, definition, "守灯人开始步行。", None), kind="start")
        db.add(world); db.flush(); db.add(line); db.flush(); db.add(root); db.flush(); parent = root
        for turn in range(1, 101):
            frozen_parent = deepcopy((parent.state, parent.definition, parent.memory, parent.text, parent.content_hash))
            data = proposal(turn)
            data["resources"].append({"entityId": player, "field": "energy", "operation": "spend", "amount": 1,
                "reason": "走完一段路消耗一份体力", "evidence": [], "visibleTo": ["player"]})
            if turn == 2:
                data["events"].append({"type": "promise", "entityId": player, "description": GOLDEN["promise"]["description"],
                    "visibleTo": ["player"], "dueMinute": 95, "loreIds": [], "requiresDecision": False})
                data["critical"] = [GOLDEN["promise"]["description"]]
                data["summary"] = GOLDEN["promise"]["description"]
            if turn == 40:
                rejected = deepcopy(data); rejected["resources"].append({"entityId": player, "field": "money", "operation": "spend", "amount": 1,
                    "reason": "数额尚未确认的钱不能凭空扣除", "evidence": [], "visibleTo": ["player"]})
                try: apply_plan(parent.state, parent.definition, rejected, m.uid(), action_kind="action")
                except Problem as error: assert error.code == "WORLD_RESOURCE_INVALID" and parent.state["entities"][player]["fields"]["money"] is None
                else: raise AssertionError("Unknown money must remain unknown")
                data["resources"].append({"entityId": npc, "field": "energy", "operation": "spend", "amount": 5,
                    "reason": GOLDEN["background"]["description"], "evidence": [], "visibleTo": [npc]})
                data["updates"].append({"entityId": npc, "field": "field:lampReady", "value": True,
                    "reason": GOLDEN["background"]["description"], "visibleTo": [npc]})
                data["events"].append({"type": "background_work", "entityId": npc, "description": GOLDEN["background"]["description"],
                    "visibleTo": [npc], "dueMinute": None, "loreIds": [], "requiresDecision": False})
            if turn == 90:
                data["updates"].append({"entityId": player, "field": "relation:"+npc, "value": GOLDEN["relation"]["value"],
                    "reason": GOLDEN["relation"]["reason"], "visibleTo": ["player"]})
                data["events"].append({"type": "relationship", "entityId": player, "description": GOLDEN["relation"]["reason"],
                    "visibleTo": ["player"], "dueMinute": None, "loreIds": [], "requiresDecision": False})
            if turn == 95:
                data["eventResolutions"] = [{"eventId": promise_id, "status": "fulfilled", "atMinute": 95,
                    "description": "红封口信已经在约定时间交还"}]
                data["summary"] = "红封口信已经在约定时间交还"
            point_id = m.uid()
            after, definition, changes, events = apply_plan(parent.state, parent.definition, data, point_id, action_kind="action")
            assert (parent.state, parent.definition, parent.memory, parent.text, parent.content_hash) == frozen_parent
            text = data["publicOutcome"]+"第"+str(turn)+"步的体力消耗已记下。"
            point = w.WorldCheckpoint(id=point_id, owner_id=domain["owner"], world_id=world_id, line_id=line_id,
                parent_checkpoint_id=parent.id, sequence=turn+1, title=data["title"], text=text, action="走完一段路",
                state=after, definition=definition, summary=data["summary"], changes=changes,
                content_hash=checkpoint_hash(after, definition, text, parent.id))
            db.add(point); db.flush(); save_memory(db, point, parent, events)
            for event in events:
                db.add(w.WorldEvent(owner_id=domain["owner"], world_id=world_id, checkpoint_id=point.id,
                    entity_id=event.get("entityId"), type=event["type"], data=event,
                    visible_to=event["visibleTo"], minute=event["minute"]))
            db.flush(); line.head_checkpoint_id = point.id
            if turn == 2: promise_id = after["queue"][0]["id"]
            if str(turn) in GOLDEN["samples"]:
                actual = sample(after, player, npc); assert actual == GOLDEN["samples"][str(turn)]
                recalled = context(db, point, world, "红封口信与青石灯塔的第七层", path=ancestry(db, point, domain["owner"]))
                assert recalled["entityState"][npc]["location"] != after["location"]
                assert recalled["entityState"][npc]["goal"] == "PRIVATE_NPC_STRESS_REPAIR_GOAL"
                assert not any(entity["id"] == npc for entity in public_view(after, definition)["entities"])
                assert fact in json.dumps(recalled["source"], ensure_ascii=False)
                assert GOLDEN["promise"]["description"] in recalled["criticalFacts"]
                if turn in (40, 90, 100):
                    assert any("红封口信" in episode["text"] for episode in recalled["recalledEpisodes"])
                assert "PRIVATE_NPC_STRESS_REPAIR_GOAL" not in json.dumps(narrator_context(parent, after, definition, data, "步行"))
                proof[str(turn)] = {"sample": actual, "recalledSource": True, "recalledPromise": True}
            original[point.id] = deepcopy((point.state, point.definition, point.memory, point.text, point.content_hash))
            trajectory.append(point.id); parent = point
        assert parent.state["queue"] == [] and parent.state["resolvedEvents"] == [promise_id]
        assert parent.state["entities"][player]["relationEvidence"][npc]["minute"] == 90
        db.commit()
        for point_id, frozen in original.items():
            point = db.get(w.WorldCheckpoint, point_id)
            assert (point.state, point.definition, point.memory, point.text, point.content_hash) == frozen
    private = tmp_path/"hundred.srbackup"
    with SessionLocal() as db:
        with build_domain_snapshot(db, domain["owner"], "world-"+world_id, tmp_path/"snapshot") as snapshot:
            with private.open("wb") as output: write_backup(snapshot.root, snapshot.manifest, output, "synthetic-hundred-password", tmp_path/"export")
    with private.open("rb") as source, prepare_backup(source, "synthetic-hundred-password", tmp_path/"prepared") as prepared:
        with SessionLocal() as db: restored = restore_domain(db, prepared, domain["target"])
    with SessionLocal() as db:
        for old_id in trajectory:
            point = db.get(w.WorldCheckpoint, restored["idMap"][("checkpoint", old_id)])
            assert point.owner_id == domain["target"] and point.text == original[old_id][3]
            assert point.content_hash == checkpoint_hash(point.state, point.definition, point.text, point.parent_checkpoint_id)
        head = db.get(w.WorldCheckpoint, restored["idMap"][("checkpoint", trajectory[-1])])
        npc_copy = next(entity["id"] for entity in head.definition["entities"] if entity["type"] == "character" and entity["id"] != head.state["playerId"])
        assert sample(head.state, head.state["playerId"], npc_copy) == GOLDEN["samples"]["100"]
        world = db.get(w.World, restored["idMap"][("world", world_id)])
        recalled = context(db, head, world, "红封口信与青石灯塔的第七层")
        assert any("红封口信" in item["text"] for item in recalled["recalledEpisodes"])
    evidence = Path(os.environ.get('TEST_ARTIFACTS_DIR', '/evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'stress-receipt.json').write_text(json.dumps({"protocol": GOLDEN["protocol"], "engineTurnsPersisted": 100,
        "stressChainDatabaseTransactions": 1, "offscreenNpcContextAndLedger": True,
        "samples": proof, "allImmutableHashesVerified": True, "encryptedNewAccountRestore": True,
        "realProviderCalls": 0, "providerQualityClaim": False}, ensure_ascii=False, indent=2), 'utf-8')
