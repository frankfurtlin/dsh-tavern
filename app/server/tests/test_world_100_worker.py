"""100 actual HTTP actions and durable worker commits with an authored mock.

This tests transactions and memory, never model prose quality or real billing.
The independent golden numbers and the provider fixture are frozen first.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re

from sqlalchemy import func, select
from app import models as m
from app.config import settings
from app.db import SessionLocal
from app.domain import canonical, digest
from app.world import models as w
from app.world import fixtures
from app.world.memory import context
from app.world.state import initial_state, checkpoint_hash, public_view
from app.world.worker import process
from app.portable.snapshot import build_domain_snapshot
from app.portable.package import write_backup, prepare_backup
from app.portable.restore import restore_domain
from test_portable_domain import domain, migrate_owned_database, http_client
from test_world_100_domain import GOLDEN, proposal, sample


def add_profile(http):
    result = http.post('/api/reader/v1/model-profiles', json={"name": "Owned hundred-turn mock",
        "baseUrl": "mock://deterministic", "modelId": "hundred-transaction-fixture", "key": "SYNTHETIC-ONLY-NOT-A-REAL-KEY",
        "contextWindow": 32768, "callBudget": 400, "inputBudget": 20000000, "outputBudget": 5000000})
    assert result.status_code == 201, result.text
    identifier = result.json()["id"]
    chosen = http.put('/api/reader/v1/model-profiles/'+identifier+'/default')
    assert chosen.status_code == 200, chosen.text
    return identifier


def test_hundred_http_worker_commits_then_private_restore_and_new_owned_action(domain, tmp_path, monkeypatch):
    assert settings.ai_mode == "mock"
    with SessionLocal() as db:
        source_world = db.get(w.World, domain["world"])
        definition = deepcopy(source_world.definition)
        player, npc = [entity["id"] for entity in definition["entities"] if entity["type"] == "character"][:2]
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
        source = deepcopy(source_world.source)
        chapter = db.get(m.ContentBlock, domain["block"]).chapter_id
        source["blocks"] += [{"blockId": m.uid(), "chapterId": chapter, "text": GOLDEN["sourceFact"], "source": "original"},
            {"blockId": m.uid(), "chapterId": chapter, "text": "海风吹过港口，守灯人沿石阶检查每一盏灯，旅人记录脚下的潮水。"*4000, "source": "original"}]
        world_id, line_id, first_id = m.uid(), m.uid(), m.uid()
        world = w.World(id=world_id, owner_id=domain["owner"], name="百轮事务港口", source_title="原创压力材料",
            source_book_id=domain["book"], source=source, definition=definition, status="active", active_line_id=line_id)
        line = w.WorldLine(id=line_id, owner_id=domain["owner"], world_id=world_id, name="百轮主线", head_checkpoint_id=first_id)
        first = w.WorldCheckpoint(id=first_id, owner_id=domain["owner"], world_id=world_id, line_id=line_id, sequence=1,
            title="出发", text="守灯人开始步行。", state=state, definition=definition, kind="start",
            content_hash=checkpoint_hash(state, definition, "守灯人开始步行。", None))
        db.add(world); db.flush(); db.add(line); db.flush(); db.add(first); db.commit()
    calls = []; sampled = {}; checkpoints = {}; tasks = []; current_step = [0]; promise_id = [None]
    original_mock = fixtures.mock_result

    def authored_mock(prompt, data):
        calls.append({"step": current_step[0], "prompt": prompt.split('\n', 1)[0]})
        if prompt.startswith("world-director"):
            step = int(re.search(r"第([0-9]+)步", data["action"])[1]); current_step[0] = step
            actor = data["state"]["playerId"]
            other = next(entry["id"] for entry in data["entities"] if entry["type"] == "character" and entry["id"] != actor)
            plan = proposal(step)
            if step <= 100:
                plan["resources"] = [{"entityId": actor, "field": "energy", "operation": "spend", "amount": 1,
                    "reason": "走完一段路消耗一份体力", "evidence": [], "visibleTo": ["player"]}]
            if step == 2:
                plan["events"] = [{"type": "promise", "entityId": actor, "description": GOLDEN["promise"]["description"],
                    "visibleTo": ["player"], "dueMinute": 95, "loreIds": [], "requiresDecision": False}]
                plan["critical"] = [GOLDEN["promise"]["description"]]; plan["summary"] = GOLDEN["promise"]["description"]
            if step == 40:
                plan["resources"].append({"entityId": other, "field": "energy", "operation": "spend", "amount": 5,
                    "reason": GOLDEN["background"]["description"], "evidence": [], "visibleTo": [other]})
                plan["updates"] = [{"entityId": other, "field": "field:lampReady", "value": True,
                    "reason": GOLDEN["background"]["description"], "visibleTo": [other]}]
                plan["events"] = [{"type": "background_work", "entityId": other, "description": GOLDEN["background"]["description"],
                    "visibleTo": [other], "dueMinute": None, "loreIds": [], "requiresDecision": False}]
            if step == 90:
                plan["updates"] = [{"entityId": actor, "field": "relation:"+other, "value": GOLDEN["relation"]["value"],
                    "reason": GOLDEN["relation"]["reason"], "visibleTo": ["player"]}]
                plan["events"] = [{"type": "relationship", "entityId": actor, "description": GOLDEN["relation"]["reason"],
                    "visibleTo": ["player"], "dueMinute": None, "loreIds": [], "requiresDecision": False}]
            if step == 95:
                plan["eventResolutions"] = [{"eventId": promise_id[0], "status": "fulfilled", "atMinute": 95,
                    "description": "红封口信已经在约定时间交还"}]
            if str(step) in GOLDEN["samples"]:
                assert data["entityState"][other]["goal"] == "PRIVATE_NPC_STRESS_REPAIR_GOAL"
                assert data["entityState"][other]["location"] != data["state"]["location"]
                assert GOLDEN["sourceFact"] in json.dumps(data["source"], ensure_ascii=False)
            return json.dumps(plan, ensure_ascii=False)
        if prompt.startswith("world-narrator"):
            assert "PRIVATE_NPC_STRESS_REPAIR_GOAL" not in canonical(data)
            amount = data["player"]["state"]["fields"]["energy"]
            return "你沿着港口走完这一段。剩余体力"+str(amount)+"，钱的数额仍未知。"
        return original_mock(prompt, data)

    monkeypatch.setattr(fixtures, "mock_result", authored_mock)
    with http_client(domain["owner"], diagnostic=True) as http:
        profile_id = add_profile(http); parent = first_id
        for step in range(1, 101):
            current_step[0] = step
            request = {"message": "第"+str(step)+"步，记住红封口信和青石灯塔第七层", "kind": "action",
                "expectedCheckpointId": parent, "turnId": "owned-hundred-"+str(step)}
            url = f'/api/reader/v1/worlds/{world_id}/lines/{line_id}/actions'
            headers = {"Idempotency-Key": "owned-hundred-"+str(step)}
            response = http.post(url, json=request, headers=headers)
            assert response.status_code == 202, response.text
            task_id = response.json()["taskId"]; tasks.append(task_id); process(task_id)
            with SessionLocal() as db:
                task = db.get(m.Task, task_id)
                assert task.state == "succeeded" and task.persistence_state == "saved", (step, task.error_code, task.result)
                head = db.get(w.WorldLine, line_id).head_checkpoint_id; point = db.get(w.WorldCheckpoint, head)
                assert point.parent_checkpoint_id == parent and point.sequence == step+1
                assert point.content_hash == checkpoint_hash(point.state, point.definition, point.text, parent)
                checkpoints[head] = deepcopy((point.state, point.definition, point.memory, point.text, point.content_hash))
                if step == 2: promise_id[0] = point.state["queue"][0]["id"]
                if str(step) in GOLDEN["samples"]:
                    actual = sample(point.state, player, npc); assert actual == GOLDEN["samples"][str(step)]
                    sampled[str(step)] = actual
                    assert not any(entity["id"] == npc for entity in public_view(point.state, point.definition)["entities"])
                if step % 25 == 0:
                    prior_calls = len(calls)
                    duplicate = http.post(url, json=request, headers=headers)
                    assert duplicate.status_code == 202 and duplicate.json() == response.json()
                    process(task_id); assert len(calls) == prior_calls
                parent = head
    assert len(calls) == 300
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(w.WorldTurn).where(w.WorldTurn.world_id == world_id)) == 100
        for identifier, old in checkpoints.items():
            point = db.get(w.WorldCheckpoint, identifier)
            assert (point.state, point.definition, point.memory, point.text, point.content_hash) == old
        head = db.get(w.WorldCheckpoint, parent)
        assert head.state["queue"] == [] and head.state["resolvedEvents"] == [promise_id[0]]
        assert head.state["entities"][player]["relationEvidence"][npc]["minute"] == 90
        recalls = context(db, head, db.get(w.World, world_id), "红封口信和青石灯塔第七层")
        assert any("红封口信" in item["text"] for item in recalls["recalledEpisodes"])
        usage = list(db.scalars(select(m.UsageRecord).where(m.UsageRecord.task_id.in_(tasks))))
        assert len(usage) == 300 and all(row.profile_id == profile_id for row in usage)
    backup = tmp_path/"hundred-worker.srbackup"
    with SessionLocal() as db:
        with build_domain_snapshot(db, domain["owner"], "world-"+world_id, tmp_path/"snapshot") as snapshot:
            with backup.open("wb") as output: write_backup(snapshot.root, snapshot.manifest, output, "synthetic-hundred-password", tmp_path/"export")
    with backup.open("rb") as source, prepare_backup(source, "synthetic-hundred-password", tmp_path/"prepare") as prepared:
        with SessionLocal() as db: receipt = restore_domain(db, prepared, domain["target"])
    ids = receipt["idMap"]; restored_world = ids[("world", world_id)]; restored_line = ids[("world_line", line_id)]
    with SessionLocal() as db:
        for identifier, old in checkpoints.items():
            point = db.get(w.WorldCheckpoint, ids[("checkpoint", identifier)])
            assert point.text == old[3] and point.content_hash == checkpoint_hash(point.state, point.definition, point.text, point.parent_checkpoint_id)
        historical = {row.id: deepcopy((row.state, row.checkpoint)) for row in db.scalars(select(m.Task).where(m.Task.owner_id == domain["target"]))}
        assert len(historical) == 100 and all(value[0] == "succeeded" for value in historical.values())
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id == domain["target"])) == 0
    with http_client(domain["target"], diagnostic=True) as http:
        target_profile = add_profile(http)
        request = {"message": "第101步，观察红封口信和青石灯塔第七层", "kind": "observe",
            "expectedCheckpointId": ids[("checkpoint", parent)], "turnId": "owned-restored-hundred"}
        result = http.post(f'/api/reader/v1/worlds/{restored_world}/lines/{restored_line}/actions', json=request,
            headers={"Idempotency-Key": "owned-restored-hundred"})
        assert result.status_code == 202, result.text
        process(result.json()["taskId"])
    with SessionLocal() as db:
        task = db.get(m.Task, result.json()["taskId"])
        assert task.state == "succeeded" and task.payload["profileId"] == target_profile
        head = db.get(w.WorldCheckpoint, db.get(w.WorldLine, restored_line).head_checkpoint_id)
        assert head.parent_checkpoint_id == ids[("checkpoint", parent)]
        assert head.state["entities"][head.state["playerId"]]["fields"]["energy"] == 0
        assert all((db.get(m.Task, identifier).state, db.get(m.Task, identifier).checkpoint) == old for identifier, old in historical.items())
    evidence = Path(os.environ.get('TEST_ARTIFACTS_DIR', '/evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'worker-stress-receipt.json').write_text(json.dumps({"actualHttpActions": 100,
        "actualWorkerCompletedTasks": 100, "distinctCommittedTurnTransactions": 100, "mockAdapterCalls": len(calls),
        "realProviderCalls": 0, "providerQualityClaim": False, "samples": sampled,
        "allOldCheckpointsImmutable": True, "encryptedNewOwnerRestore": True, "restoredNewOwnedAction": True}, indent=2), 'utf-8')
