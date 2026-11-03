from ..prose.policy import execution_profile
"""Dedicated durable world executor; reader tasks never enter this queue."""
import concurrent.futures
import logging
import re
import secrets
import threading
import time
from copy import deepcopy
from datetime import timedelta
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, InterfaceError
from ..config import settings
from ..db import SessionLocal
from ..models import Task, ModelProfile, uid, now
from ..domain import owned, canonical, digest
from ..errors import Problem
from ..completion_policy import completion_limit
from ..worker import call_provider, heartbeat, add_event, assert_task_active
from ..storage import mark_synced
from .models import World, WorldLine, WorldTurn, WorldCheckpoint
from .jobs import phase, persist_stream, TERMINAL, lock_task_world
from .state import parse_json, prepared_definition, apply_plan, ancestry, public_payload, private_phrases, visible_to, execution_state, event_visible_to
from .schemas import PrepareOutput, DirectorOutput, NarrativeReview
from .memory import context, narrator_context, save_memory, restore_rewrite_records
from .service import require_line, commit_turn, checked_checkpoint
from ..task_lease import ClaimedTask, LeaseLost, assert_lease, lease_matches, leased_task, execution_token
from ..scenes.interaction import enrich_director, normalize_plan, record_interaction
from .identity import check_identity, check_plan_identity
from .output import story_text, narrative_text

logger = logging.getLogger("reader.world")


def fail(task_id, code):
    with SessionLocal() as db:
        lock_task_world(db, task_id)
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if not task or not lease_matches(task) or task.state in TERMINAL:
            return
        task.state = "cancelled" if code == "TASK_CANCELLED" else "failed"
        task.error_code = code
        task.persistence_state = "recoverable" if code == "PERSISTENCE_UNAVAILABLE" else "saved"
        task.result_outcome = "partial" if task.checkpoint.get("worldStream", {}).get("text") else "failed"
        task.provider_state = "unknown" if code == "PROVIDER_STATUS_UNKNOWN" else task.provider_state
        if task.result.get("deleted"):
            # Finish the cancelled executor without recreating events/result
            # details that the committed world deletion already removed.
            task.state, task.error_code = "cancelled", "TASK_CANCELLED"
            task.persistence_state = "saved"
            db.commit()
            return
        task.result = {**task.result, "phase": "stopped" if task.state == "cancelled" else "interrupted"}
        turn = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task.id).with_for_update())
        if turn:
            turn.status = "draft"
            line = db.get(WorldLine, turn.line_id)
            if line and line.pending_task_id == task.id and task.persistence_state != "recoverable":
                line.pending_task_id = None
        elif task.task_type in ("world_prepare", "world_script_breakdown"):
            world = db.get(World, task.payload["worldId"])
            if world and world.prepare_task_id == task.id and world.status == "preparing":
                world.status = "failed"
                if task.task_type == 'world_script_breakdown':
                    world.source={**world.source,'script':{**world.source.get('script',{}),'status':'failed','errorCode':code}}
        elif task.task_type == 'world_scene_binding':
            from ..scenes.models import SceneImage
            image=db.get(SceneImage,task.payload.get('sceneId'))
            if image and image.owner_id==task.owner_id and image.image.get('bindingTaskId')==task.id:
                image.image={**image.image,'anchorStatus':'unavailable','anchorError':code}
        elif task.task_type == 'world_character_chat':
            from ..play_controls.chat import finish_failure
            finish_failure(db,task)
        add_event(db, task, "terminal", {"state": task.state, "errorCode": code})
        add_event(db, task, "stopped" if code == "TASK_CANCELLED" else "error", {"errorCode": code})
        if turn and turn.draft_text:
            add_event(db, task, "draft_saved", {"turnId": turn.id})
        if turn and turn.warnings:
            add_event(db, task, "warning", {"warnings": turn.warnings})
        db.commit()


def preparation_world(db, task, *, lock=False):
    world = owned(db, World, task.payload["worldId"], task.owner_id, lock=lock)
    if world.prepare_task_id != task.id:
        raise Problem("TASK_CANCELLED", "这次准备已被替代")
    if world.status == "archived":
        raise Problem("WORLD_ARCHIVED", "这个世界已归档")
    if world.status not in ("preparing", "failed"):
        raise Problem("TASK_CANCELLED", "这次准备已停止")
    return world


def current_turn_line(db, task, current, *, lock=False):
    """Authorize new work only while this turn still owns its original head.

    Old releases could replace a failed/recoverable pending pointer. A valid
    claim token for that old task does not authorize another scene on the line.
    Temporary chapter states keep the original formal head until acceptance.
    """
    world, line = require_line(db, task.owner_id, current.world_id, current.line_id, lock=lock, active=True)
    if line.pending_task_id != task.id or line.head_checkpoint_id != current.parent_checkpoint_id:
        raise Problem("WORLD_CHECKPOINT_CONFLICT", "这轮的起点已经变化，已保存的正文仍作为草稿保留", 409)
    return world, line


def assert_current_turn(task_id):
    # Follow the same World -> Task -> Turn -> Line lock order as commit/delete.
    with SessionLocal() as db:
        lock_task_world(db, task_id)
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
        assert_task_active(db, task)
        current = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id).with_for_update())
        current_turn_line(db, task, current, lock=True)


def preparation_characters(source):
    # Local extraction IDs and stable identities can both look like UUIDs.
    # Publish the one the output contract accepts under that exact field name.
    return [{"sourceCharacterId": ref["identityId"], "name": ref["name"], "evidence": ref["evidence"]}
            for ref in source.get("characterRefs", [])]


def prepare(task_id):
    from .preparation import batches, merge
    phase(task_id, "preparing_world")
    with SessionLocal() as db:
        task=db.get(Task,task_id)
        world=preparation_world(db,task)
        profile=owned(db,ModelProfile,task.payload["profileId"],task.owner_id)
        profile = execution_profile(db, task, profile)
        groups=batches(world.source,task.payload.get("preparationBatches",1))
        limit=completion_limit(profile,"analysis",world=True)
        completed=task.checkpoint.get("preparedBatches",0)
    for index in range(completed,len(groups)):
        phase(task_id,"preparing_world",preparedBatches=index,preparationBatches=len(groups))
        with SessionLocal() as db:
            task=db.get(Task,task_id);world=preparation_world(db,task)
            data={"title":world.name,"source":groups[index],"rules":world.definition["rules"],
                  "existingEntities":[{"id":e["id"],"name":e["name"],"type":e["type"]} for e in world.definition.get("entities",[])[-40:]],
                  "sourceCharacters":preparation_characters(world.source),"_structured":True}
        result=call_provider(task_id,f"analysis:prepare:{index+1}","world_prepare",data,limit)
        if result.get("finishReason") not in (None,"stop"):
            raise Problem("WORLD_OUTPUT_INCOMPLETE","世界资料未完成，可继续准备")
        incoming=parse_json(result["text"],PrepareOutput)
        with SessionLocal() as db:
            lock_task_world(db, task_id)
            task=db.scalar(select(Task).where(Task.id==task_id).with_for_update());assert_task_active(db,task)
            world=preparation_world(db,task,lock=True)
            incoming=prepared_definition(incoming,world.source["blocks"],world.definition["rules"])
            from ..character_identity import world_reference
            for entity in incoming["entities"]:
                if entity["type"] == "character":world_reference(world, entity)
            value=merge(world.definition,incoming)
            definition=prepared_definition(value,world.source["blocks"],world.definition["rules"],world.definition,merged=True)
            ids=set(world.definition.get("preparedBlockIds",[]))|{b["blockId"] for b in groups[index]}
            world.definition={**definition,"preparedBlockIds":sorted(ids),"sourceBlockCount":len(world.source["blocks"])}
            task.checkpoint={**task.checkpoint,"preparedBatches":index+1}
            task.result={**task.result,"preparedBatches":index+1,"preparationBatches":len(groups)}
            db.commit()
    with SessionLocal() as db:
        lock_task_world(db, task_id)
        task=db.scalar(select(Task).where(Task.id==task_id).with_for_update());assert_task_active(db,task)
        world=preparation_world(db,task,lock=True)
        world.status,world.updated_at="active",now()
        task.state,task.persistence_state,task.result_outcome="succeeded","saved","complete"
        task.result={"phase":"ready","worldId":world.id,"preparedBatches":len(groups),"preparationBatches":len(groups)}
        add_event(db,task,"world_ready",{"worldId":world.id});db.commit()


def produce_scene(task_id, index, parent, world, action, *, autoplay=False, count=1):
    phase(task_id, "directing", chapterIndex=index, chapterCount=count)
    with SessionLocal() as db:
        lock_task_world(db, task_id)
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
        assert_task_active(db, task)
        turn = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id))
        current_turn_line(db, task, turn, lock=True)
        profile = owned(db, ModelProfile, task.payload["profileId"], task.owner_id)
        profile = execution_profile(db, task, profile)
        limits = {s: completion_limit(profile, s, world=True) for s in ("analysis", "generation", "validation")}
        path = ancestry(db, parent, parent.owner_id)
        base_state = execution_state(parent, path)
        data = context(db, parent, world, action, autoplay=autoplay, chapter_index=index, chapter_count=count,
                       action_kind=turn.kind, path=path, state=base_state)
        if task.payload.get('selectedTarget'):
            data['selectedTarget']=deepcopy(task.payload['selectedTarget'])
        enrich_director(data, task, parent)
        if parent.definition.get('script'):
            from ..scripts.context import enrich_script
            enrich_script(data, parent.definition, base_state, world.source)
        if turn.result.get("chapters"):
            data["candidateScenes"] = [{"title": c["plan"]["title"], "text": c["text"], "summary": c["plan"]["summary"]} for c in turn.result["chapters"][-2:]]
        if parent.definition["rules"]["randomChecks"]:
            rolls = deepcopy(turn.result.get("rolls", {}))
            rolls.setdefault(str(index), secrets.randbelow(100) + 1)
            turn.result = {**turn.result, "rolls": rolls}
            data["randomRoll"] = rolls[str(index)]
            db.commit()
        saved = deepcopy(turn.plan.get(str(index), {}))
        if not saved:
            from .receipts import reusable_director
            saved = reusable_director(db,task,turn,parent,index)
            if saved:
                turn.plan={**turn.plan,str(index):saved}
                task.result={**task.result,'reusedDirectorTaskId':saved['reusedDirectorTaskId']}
                db.commit()
    from .director_receipt import director_proposal, missing_document_receipts
    from .work import prepare_work
    _, work = prepare_work({'transfers':[], 'additions':[], 'work':[]}, base_state, path)
    if work:base_state = {**base_state, 'work':work}
    def validate_proposal(candidate):
        from .turn_contract import travel_capability
        checked = travel_capability(normalize_plan(candidate, base_state, parent.definition), task.payload.get('sceneIntent'), base_state)
        check_plan_identity(checked, data['actors'])
        apply_plan(base_state, parent.definition, checked, 'proposal-validation', data['source'],
                   action_kind=turn.kind, allow_decisions=autoplay, action=action)
    proposal = director_proposal(task_id, index, data, parent.definition, base_state,
                                limits['analysis'], call_provider, saved, validate_proposal)
    if proposal != saved:
        saved = proposal
        with SessionLocal() as db:
            lock_task_world(db, task_id)
            task = owned(db, Task, task_id, world.owner_id, lock=True)
            assert_task_active(db, task)
            turn = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id).with_for_update())
            current_turn_line(db, task, turn, lock=True)
            turn.plan = {**turn.plan, str(index): saved}
            db.commit()
    plan, cp_id = normalize_plan(saved["plan"], base_state, parent.definition), saved["checkpointId"]
    from .turn_contract import travel_capability
    plan = travel_capability(plan, task.payload.get('sceneIntent'), base_state)
    validation_error = None
    try:
        if missing_document_receipts(plan, base_state, parent.definition):
            raise Problem('WORLD_DOCUMENT_RECEIPT_INVALID', '文书交付未登记，正文作为草稿保留')
        check_plan_identity(plan, data['actors'])
        state, definition, changes, events = apply_plan(base_state, parent.definition, plan, cp_id, data["source"], action_kind=turn.kind,
                                                       allow_decisions=autoplay, action=action)
        if parent.definition["rules"]["randomChecks"]:
            state.setdefault("checks", []).append({"checkpointId": cp_id, "roll": data["randomRoll"], "action": action})
    except Problem as error:
        # The reader still gets the model's narrative. Only the untrustworthy ledger
        # remains uncommitted; there is no fabricated fallback state.
        state, definition, changes, events = parent.state, parent.definition, [], []
        validation_error = error.code
    phase(task_id, "writing", chapterIndex=index, chapterCount=count)
    narration = narrator_context(parent, state, definition, plan, action, chapter=autoplay)
    if task.payload.get('sceneIntent',{}).get('kind') == 'travel':
        from .identity import actor_contract
        before=actor_contract(base_state,parent.definition)
        present={e['id'] for e in narration['actors']['presentNPCs']}
        narration['navigationContract']={'originId':base_state['location'],'destinationId':state['location'],
            'peopleRemainingBehind':[e for e in before['presentNPCs'] if e['id'] not in present],
            'rule':'只根据当前位置与presentNPCs描写抵达。peopleRemainingBehind没有同行，不能受旧正文影响写成一起行走。'}
    if data.get('selectedTarget'):
        narration['selectedTarget']=deepcopy(data['selectedTarget'])
    if data.get('outputMode') == 'dialogue':
        narration['outputMode'] = 'dialogue'
        narration['conversation'] = data.get('conversation', [])
    narration["changes"] = public_payload(changes, state, definition)
    narration["events"] = public_payload([event for event in events
        if event_visible_to(event, state["playerId"])], state, definition)
    save_visibility(task_id, state, definition)
    from .receipts import reusable_narrator
    with SessionLocal() as db:
        current_task=db.get(Task,task_id)
        result=reusable_narrator(db,current_task,saved.get('reusedDirectorTaskId')) if index==1 else None
        if result:
            current_task.result={**current_task.result,'reusedNarratorTaskId':saved['reusedDirectorTaskId']};db.commit()
    if not result:result = call_provider(task_id, f"generation:scene:{index}", "world_narrator", narration, limits["generation"])
    body = story_text(result.get("text", "")).strip()
    raw_body = body
    dialogue = None
    from ..scenes.dialogue import scene_segments, dialogue_text, MARKER
    if re.match(r'^\s*\{\s*"(?:kind|type)"',body):
        from ..scenes.dialogue import structured_segments
        dialogue = structured_segments(body, narration['actors'].get('presentNPCs', [])+narration['actors'].get('roundParticipants', []), narration['actors']['player'])
        body = dialogue_text(dialogue)
    elif MARKER.search(body):
        dialogue = scene_segments(body, narration['actors'].get('presentNPCs', []), data.get('selectedTarget', {}).get('entityId'))
        body = dialogue_text(dialogue)
    elif data.get('outputMode') == 'dialogue':
        from ..scenes.dialogue import dialogue_segments, dialogue_text, MARKER
        dialogue = dialogue_segments(body, data.get('selectedTarget', {}).get('name', ''))
        # Preserve old unmarked prose exactly; new channel markers belong to
        # the chat renderer, never the saved story's reading text.
        if MARKER.search(body):body = dialogue_text(dialogue)
    else:
        # A narrator can imitate the prior chat's formatting during travel.
        # These are transport tags, not part of the novel's reading text.
        body = narrative_text(body)
    persist_stream(task_id, f"generation:scene:{index}", body, final=True, segments=dialogue)
    # A quoted player utterance is narration, never a duplicate NPC bubble.
    # Keep the actual self-encounter prose checks; a protocol heading alone
    # must not turn legitimate first-person speech into an identity collision.
    player=narration['actors']['player']
    identity_body=re.sub(r'【台词[：:]\s*(?:'+ '|'.join(re.escape(v) for v in [player['id'],player['name'],*player.get('aliases',[])]) + r')\s*】','',raw_body)
    check_identity(identity_body, narration['actors'])
    if validation_error:
        raise Problem(validation_error, "状态未通过检查，正文作为草稿保留")
    if not body or result.get("finishReason") not in (None, "stop"):
        raise Problem("WORLD_OUTPUT_INCOMPLETE", "正文尚未完成，已收到内容仍可阅读")
    if autoplay:
        validate_chapter_body(body)
    from .settlement import check_narrative
    check_narrative(body,state,definition)
    review_scene(task_id, index, narration, body, limits["validation"], ledger=review_ledger(parent.state, state, definition, changes))
    scene = {"id": cp_id, "state": state, "definition": definition, "changes": changes, "events": events,
        "plan": plan, "text": body, "dialogueSegments": dialogue}
    return {**scene, "contentHash": digest(canonical(scene))}


def validate_chapter_body(body):
    # A game prompt inviting another input is not a completed candidate chapter.
    # Keep the received prose as a draft; do not retry or hide it.
    if re.search(r"(?:^|\n)[ \t]*(?:—+|-+)?[ \t]*(?:\*\*)?(?:你打算|轮到你|请选择|接下来[，,]?你要)[：:](?:\*\*)?[ \t]*(?:$|\n[ \t]*(?:[1-5][.、)]|[-*] ))", body[-1000:]):
        raise Problem("WORLD_OUTPUT_INCOMPLETE", "本章停在新的输入提示，已保留完整收到的正文草稿")


def save_visibility(task_id, state, definition):
    with SessionLocal() as db:
        lock_task_world(db, task_id)
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
        assert_task_active(db, task)
        current = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id).with_for_update())
        current_turn_line(db, task, current, lock=True)
        task.checkpoint = {**task.checkpoint, "worldDeniedPhrases": private_phrases(state, definition)}
        db.commit()


def review_ledger(before, after, definition, changes):
    player = after["playerId"]
    actors = {player} | {key for key,value in before["entities"].items() if value.get("location") == before["location"]}
    actors |= {key for key,value in after["entities"].items() if value.get("location") == after["location"]}
    actors |= {change.get("entityId") for change in changes}
    actors |= {change.get(key) for change in changes for key in ('fromId','toId')}
    actors |= {effect['entityId'] for ending in definition.get('script', {}).get('endings', [])
               for t in ending.get('transitions', []) for effect in t['effects']}
    people = {e["id"]:e for e in definition["entities"] if e["type"] == "character" and e["id"] in actors}
    items = {e["id"]:e["name"] for e in definition["entities"] if e["type"] == "item" and
             (not e.get("knownBy") or visible_to(e["knownBy"], player))}
    def snapshot(state):
        return [{"id":key,"name":entity["name"],"alive":state["entities"].get(key,{}).get("alive"),
                 "location":state["entities"].get(key,{}).get("location"),
                 "fields":deepcopy(state["entities"].get(key,{}).get("fields",{})),
                 "inventory":{item:state["entities"].get(key,{}).get("inventory",{}).get(item) for item in items}}
                for key,entity in people.items()]
    from ..scripts.plot import public_progress
    return {"items":items,"before":snapshot(before),"after":snapshot(after),
            "endingBefore":public_progress(before, definition),"endingAfter":public_progress(after, definition),
            "settlementReceipts":deepcopy(after.get('plot', {}).get('transitionReceipts', []))}


def review_scene(task_id, index, narration, body, limit, *, ledger=None):
    assert_current_turn(task_id)
    phase(task_id, "checking")
    result = call_provider(task_id, f"validation:world:{index}", "world_review", {"scene": narration, "ledger": ledger or {}, "text": body, "_structured": True}, limit)
    review = parse_json(result["text"], NarrativeReview)
    if result.get("finishReason") not in (None, "stop"):
        raise Problem("WORLD_OUTPUT_INCOMPLETE", "正文核对尚未完成，正文草稿仍可阅读")
    if not review["consistent"]:
        with SessionLocal() as db:
            lock_task_world(db, task_id)
            task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
            assert_task_active(db, task)
            current = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id).with_for_update())
            current_turn_line(db, task, current, lock=True)
            current.result = {**current.result, "reviewIssues": review["issues"]}
            # The reviewer sees a limited internal inventory comparison. Its free
            # explanations remain private; the reader receives a visible warning.
            current.warnings = ["本次正文与行动结果存在差异，已保留收到的文字草稿"]
            db.commit()
        raise Problem("WORLD_NARRATIVE_CONFLICT", "正文与本次结果有差异，已保留为草稿")


def turn(task_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        current = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id))
        world, line = current_turn_line(db, task, current)
        parent = checked_checkpoint(db, task.owner_id, world.id, current.parent_checkpoint_id)
        action = current.action
    scene = produce_scene(task_id, 1, parent, world, action)
    phase(task_id, "saving")
    with SessionLocal() as db:
        lock_task_world(db, task_id)
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
        current = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id).with_for_update())
        checkpoint = commit_turn(db, task, current, parent, scene["state"], scene["definition"], scene["plan"],
            scene["text"], scene["changes"], scene["events"], scene["id"])
        save_memory(db, checkpoint, parent, scene["events"])
        record_interaction(checkpoint, parent, task, current, scene.get('dialogueSegments'))
        current.warnings = scene["plan"]["warnings"]
        from ..durable_stream import saved
        saved(db, task, "worldStream", scene["text"], stage="generation:scene:1")
        add_event(db, task, "terminal", {"state": task.state, "resultOutcome": task.result_outcome})
        db.commit()


def write(task_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        current = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id))
        world, line = current_turn_line(db, task, current)
        parent = checked_checkpoint(db, task.owner_id, world.id, current.parent_checkpoint_id)
        original = parent
        chapters = deepcopy(current.result.get("chapters", []))
        count, action = task.payload["chapterCount"], current.action
    # Temporary candidate state advances in sequence. Nothing writes the formal head.
    for index in range(len(chapters) + 1, count + 1):
        if chapters:
            last = chapters[-1]
            parent = WorldCheckpoint(id=original.id, owner_id=original.owner_id, world_id=original.world_id,
                line_id=original.line_id, parent_checkpoint_id=original.parent_checkpoint_id,
                sequence=original.sequence + len(chapters), state=last["state"], definition=last["definition"],
                text=last["text"], memory=original.memory)
        scene = produce_scene(task_id, index, parent, world, action, autoplay=True, count=count)
        chapters.append(scene)
        with SessionLocal() as db:
            lock_task_world(db, task_id)
            task = owned(db, Task, task_id, world.owner_id, lock=True)
            assert_task_active(db, task)
            current = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id).with_for_update())
            current_turn_line(db, task, current, lock=True)
            current.result = {**current.result, "chapters": chapters}
            from ..durable_stream import saved
            saved(db, task, "worldStream", scene["text"], stage=f"generation:scene:{index}")
            add_event(db, task, "chapter_ready", {"chapterIndex": index, "title": public_payload(scene["plan"]["title"], scene["state"], scene["definition"])})
            db.commit()
    with SessionLocal() as db:
        lock_task_world(db, task_id)
        task = owned(db, Task, task_id, world.owner_id, lock=True)
        assert_task_active(db, task)
        current = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id).with_for_update())
        _, line = current_turn_line(db, task, current, lock=True)
        line.pending_task_id = None
        current.status = "candidate"
        task.state, task.persistence_state, task.result_outcome = "succeeded", "saved", "complete"
        task.result = {"phase": "candidate", "worldId": world.id, "lineId": line.id, "turnId": current.id, "chapterCount": len(chapters)}
        add_event(db, task, "terminal", {"state": "succeeded", "resultOutcome": "complete"})
        db.commit()


def rewrite(task_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        current = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id))
        world, _ = current_turn_line(db, task, current)
        selected = checked_checkpoint(db, task.owner_id, world.id, task.payload["rewriteCheckpointId"])
        parent = checked_checkpoint(db, task.owner_id, world.id, selected.parent_checkpoint_id)
        profile = owned(db, ModelProfile, task.payload["profileId"], task.owner_id)
        profile = execution_profile(db, task, profile)
        limit = completion_limit(profile, "generation",world=True)
        plan = {"title": selected.title, "summary": selected.summary, "publicOutcome": selected.summary,
                "options": selected.options}
        data = narrator_context(parent, selected.state, selected.definition, plan, selected.action,
            rewrite=True, old_text=selected.text)
        data["changes"] = public_payload(selected.changes, selected.state, selected.definition)
        data["rewriteInstruction"] = current.action
    phase(task_id, "writing")
    save_visibility(task_id, selected.state, selected.definition)
    result = call_provider(task_id, "generation:rewrite", "world_narrator", data, limit)
    body = story_text(result.get("text", "")).strip()
    segments = None
    from ..scenes.dialogue import structured_segments, dialogue_text, scene_segments, MARKER
    participants = data['actors'].get('presentNPCs', []) + data['actors'].get('roundParticipants', [])
    if re.match(r'^\s*\{\s*"(?:kind|type)"', body):
        segments = structured_segments(body, participants, data['actors']['player'])
        body = dialogue_text(segments)
    elif MARKER.search(body):
        segments = scene_segments(body, participants)
        body = dialogue_text(segments)
    else:
        body = narrative_text(body)
    persist_stream(task_id, "generation:rewrite", body, final=True, segments=segments)
    check_identity(body, data['actors'])
    if not body or result.get("finishReason") not in (None, "stop"):
        raise Problem("WORLD_OUTPUT_INCOMPLETE", "改写正文尚未完整返回")
    from .settlement import check_narrative
    check_narrative(body,selected.state,selected.definition)
    review_scene(task_id, 1, data, body, completion_limit(profile, "validation",world=True), ledger=review_ledger(parent.state, selected.state, selected.definition, selected.changes))
    with SessionLocal() as db:
        lock_task_world(db, task_id)
        task = owned(db, Task, task_id, world.owner_id, lock=True)
        current = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id).with_for_update())
        checkpoint = commit_turn(db, task, current, parent, deepcopy(selected.state), deepcopy(selected.definition), plan,
            body, deepcopy(selected.changes), [], uid(), kind="rewrite")
        checkpoint.action, checkpoint.memory = selected.action, deepcopy(selected.memory)
        if segments is not None:
            checkpoint.memory['sceneTranscript'] = {'checkpointId':checkpoint.id,'segments':deepcopy(segments)}
        restore_rewrite_records(db, checkpoint, selected)
        from ..durable_stream import saved
        saved(db, task, "worldStream", body, stage="generation:rewrite")
        add_event(db, task, "terminal", {"state": task.state, "resultOutcome": task.result_outcome})
        db.commit()


@leased_task
def process(task_id):
    try:
        with SessionLocal() as db:
            task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
            assert_lease(task)
            if task and task.state in TERMINAL and task.persistence_state == "saved":
                return
            if task and task.state == "failed" and task.persistence_state == "recoverable":
                task.state = "preparing"
                db.commit()
            assert_task_active(db, task)
            kind = task.task_type
        from ..scenes.binding import bind_scene
        from ..scripts.worker import process as breakdown
        from ..play_controls.chat import process as character_chat
        {"world_character_chat":character_chat,"world_script_breakdown":breakdown,"world_scene_binding":bind_scene,"world_prepare": prepare, "world_turn": turn, "world_write": write, "world_rewrite": rewrite}[kind](task_id)
    except LeaseLost:
        raise
    except Problem as error:
        fail(task_id, error.code)
    except (OperationalError, InterfaceError) as error:
        logger.warning("world execution task=%s error=%s", task_id, type(error).__name__)
        fail(task_id, "PERSISTENCE_UNAVAILABLE")
    except Exception as error:
        logger.warning("world execution task=%s error=%s", task_id, type(error).__name__)
        fail(task_id, "WORLD_EXECUTION_FAILED")


def claim(worker_id):
    with SessionLocal() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(82370219)"))
        active = list(db.scalars(select(Task).where(Task.task_type.like("world_%"),
            Task.state.notin_(list(TERMINAL) + ["queued"]), Task.lease_expires_at > now())))
        from .queue import capacity
        if len(active) >= capacity():
            return None
        query = select(Task).where(Task.task_type.like("world_%"), (Task.state == "queued") |
            (Task.state.notin_(list(TERMINAL)) & (Task.lease_expires_at < now())) |
            ((Task.state == "failed") & (Task.persistence_state == "recoverable")))
        if active:
            # Filter before the finite scan. Twenty older tasks belonging to
            # one busy user must not hide another user's eligible task.
            query=query.where(Task.owner_id.not_in({t.owner_id for t in active}))
        for task in db.scalars(query.order_by(Task.created_at).with_for_update(skip_locked=True).limit(20)):
            if any(t.owner_id == task.owner_id for t in active):
                continue
            token = uid()
            task.state, task.lease_owner, task.lease_expires_at = "preparing", token, now() + timedelta(seconds=30)
            db.commit()
            return ClaimedTask(task.id, token)
    return None


@leased_task
def run_claimed(task_id):
    stop = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(task_id, stop, execution_token()), daemon=True)
    thread.start()
    try:
        process(task_id)
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task and lease_matches(task) and task.state in TERMINAL and task.persistence_state == "saved":
                mark_synced(task.owner_id, task.id, task.checkpoint.get("calls", {}))
    finally:
        stop.set()


def run():
    from ..logging_config import setup_logging
    setup_logging()
    worker_id = uid()
    from .queue import capacity
    with concurrent.futures.ThreadPoolExecutor(max_workers=capacity()) as pool:
        pending = set()
        while True:
            pending = {f for f in pending if not f.done()}
            if settings.interactive_world_enabled and len(pending) < capacity():
                try:
                    task_id = claim(worker_id)
                    if task_id:
                        pending.add(pool.submit(run_claimed, task_id))
                except Exception as error:
                    logger.warning("world queue unavailable error=%s", type(error).__name__)
            time.sleep(1)


if __name__ == "__main__":
    run()
