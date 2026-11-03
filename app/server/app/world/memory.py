from copy import deepcopy
import re
from sqlalchemy import select
from ..domain import canonical
from .state import ancestry, retrieve_lore, public_view, public_payload, execution_state, event_audience_origins, frozen_event, decision_events
from .models import WorldMemory, WorldEvent
from .identity import actor_contract, identity_conflict
from .output import story_text


def source_recall(source, definition, action, byte_limit=5000):
    names = [e["name"] for e in definition.get("entities", []) if e["name"] in action]
    names += [alias for e in definition.get("entities", []) for alias in e.get("aliases", []) if alias in action]
    # Unknown names are not in the entity registry yet. Literal overlapping phrases
    # still retrieve old source passages, without granting access to future text.
    phrases = {action[i:i+n] for n in (2, 3, 4) for i in range(len(action)-n+1)
               if re.fullmatch(r"[\u4e00-\u9fffA-Za-z]{"+str(n)+"}", action[i:i+n])}
    scored = []
    blocks = [*source["blocks"],*definition.get("authoring",{}).get("sourceBlocks",[])]
    for i, block in enumerate(blocks):
        score = sum(10 for word in names if word in block["text"]) + sum(len(word) for word in phrases if word in block["text"])
        if score:
            scored.append((score, i, block))
    if not scored:
        scored = [(0, i, b) for i, b in enumerate(blocks[-4:])]
    result, size = [], 0
    for _, _, block in sorted(scored, key=lambda v: (-v[0], -v[1])):
        if len(canonical(block).encode()) > byte_limit:
            block = deepcopy(block)
            positions = [block["text"].find(word) for word in names+list(phrases) if word in block["text"]]
            position = min(positions) if positions else max(0,len(block["text"])-800)
            block["text"] = block["text"][max(0,position-200):position+600]
            block["excerpt"] = True
        length = len(canonical(block).encode())
        if size + length <= byte_limit:
            result.append(block)
            size += length
        if len(result) >= 8:
            break
    return result


def context(db, checkpoint, world, action, *, autoplay=False, chapter_index=1, chapter_count=1,
            action_kind="action", path=None, state=None):
    path = ancestry(db, checkpoint, checkpoint.owner_id) if path is None else path
    state = execution_state(checkpoint, path) if state is None else state
    definition = checkpoint.definition
    origins = event_audience_origins(path)
    checkpoints = {c.id: c for c in path}
    decisions = decision_events(state)
    ready_decisions = [event["id"] for event in decisions if event["dueMinute"] <= state["minute"]]
    boundaries = [max(state["minute"], event["dueMinute"]) for event in decisions
                  if event["dueMinute"] > state["minute"] or action_kind == "wait"]
    nearby = {k for k, v in state["entities"].items() if v.get("location") == state["location"]}
    mentioned = {e["id"] for e in definition["entities"] if any(n and n in action for n in [e["name"], *e.get("aliases", [])])}
    due = sorted(state["queue"], key=lambda e: e["dueMinute"])[:8]
    relevant = nearby | mentioned | {state["playerId"], state["location"]} | {e["entityId"] for e in due if e.get("entityId")}
    backgrounds = [k for k, v in state["entities"].items() if k not in relevant and v.get("goal")]
    # Rotate a bounded background set by story sequence; no real-clock simulation.
    if backgrounds:
        index = checkpoint.sequence % len(backgrounds)
        relevant.update((backgrounds[index:] + backgrounds[:index])[:2])
    mandatory = state["entities"][state["playerId"]].get("inventory", {})
    relevant.update(mandatory)
    scenes = [c for c in path if c.text and c.kind not in ("start", "fork", "correction")]
    actors = actor_contract(state, definition)
    invalid_scene_ids = {c.id for c in path if c.state.get('playerId') == state['playerId'] and identity_conflict(c.text, actors)}
    # Old model mistakes are retained in immutable receipts, but never taught
    # to subsequent generations as historical facts about the current player.
    recent = [{"checkpointId": c.id, "action": c.action, "text": story_text(c.text)} for c in scenes[-4:]
              if c.id not in invalid_scene_ids]
    summaries = [deepcopy(e) for e in checkpoint.memory.get("episodes", []) if not identity_conflict(e.get('text', ''), actors)]
    episode_bytes = 0
    bounded = []
    for episode in reversed(summaries):
        if episode_bytes + len(canonical(episode).encode()) > 3500:
            continue
        bounded.insert(0, episode); episode_bytes += len(canonical(episode).encode())
    summaries = bounded
    # Read the durable, unabridged archive too; compact summaries are navigation,
    # never the only addressable record of an old promise or conversation.
    archive = list(db.scalars(select(WorldMemory).where(WorldMemory.owner_id == checkpoint.owner_id,
        WorldMemory.world_id == world.id, WorldMemory.checkpoint_id.in_([c.id for c in path]),
        WorldMemory.level == 1).order_by(WorldMemory.created_at.desc())))
    words = {action[i:i+n] for n in (2, 3, 4) for i in range(len(action)-n+1)
             if re.fullmatch(r"[\u4e00-\u9fffA-Za-z]{"+str(n)+"}", action[i:i+n])}
    ranked = sorted(archive, key=lambda m: -sum(len(w) for w in words if w in m.text))
    recalled, recall_size = [], 0
    for m in ranked:
        if m.checkpoint_id in invalid_scene_ids:continue
        if identity_conflict(m.text, actors):continue
        if not any(w in m.text for w in words):
            continue
        record = {"firstCheckpointId": m.first_checkpoint_id, "lastCheckpointId": m.last_checkpoint_id,
                  "text": m.text, "eventIds": m.event_ids, "version": m.level}
        if len(canonical(record).encode()) > 3800:
            lines = m.text.splitlines()
            matches = [i for i, text in enumerate(lines) if any(w in text for w in words)]
            indices = sorted({j for i in matches for j in (i-1, i, i+1) if 0 <= j < len(lines)})
            selected, excerpt_size = [], 0
            for i in indices:
                text = lines[i]
                if len(text.encode()) > 1600:
                    position = next((text.index(w) for w in words if w in text), 0)
                    text = text[max(0, position-150):position+350]
                if excerpt_size + len(text.encode()) <= 2500:
                    selected.append(text); excerpt_size += len(text.encode())
            record.update(text="\n".join(selected), excerpt=True, archiveId=m.id)
            record["eventIds"] = record["eventIds"][:12]
        size = len(canonical(record).encode())
        if recall_size + size <= 4000:
            recalled.append(record); recall_size += size
        if len(recalled) == 3:
            break
    events = list(db.scalars(select(WorldEvent).where(WorldEvent.owner_id == checkpoint.owner_id,
        WorldEvent.checkpoint_id.in_([c.id for c in path]), WorldEvent.entity_id.in_(list(relevant))).order_by(WorldEvent.created_at.desc()).limit(12)))
    events = [e for e in events if e.checkpoint_id not in invalid_scene_ids and not identity_conflict(e.data.get('description', ''), actors)]
    payload = {"actors": actors, "rules": definition["rules"], "state": {k: deepcopy(v) for k, v in state.items() if k != "entities"},
        "entities": [e for e in definition["entities"] if e["id"] in relevant],
        "entityState": {k: v for k, v in state["entities"].items() if k in relevant},
        "recentScenes": recent, "episodes": summaries, "recalledEpisodes": recalled,
        "criticalFacts": [fact for fact in state["critical"] if not identity_conflict(fact, actors)],
        "lore": retrieve_lore(definition, state, action, private=True),
        "source": source_recall(world.source, definition, action), "queuedEvents": due,
        "recalledEvents": [{**frozen_event(e.data, origins.get(e.data.get("id"), checkpoints[e.checkpoint_id].state["playerId"])),
                            "checkpointId": e.checkpoint_id, "minute": e.minute}
                           for e in reversed(events)],
        "actionKind": action_kind,
        "actionRules": {"costs": deepcopy(definition["rules"].get("actionCosts", {}).get(action_kind, {})),
                        "abilityRequirements": deepcopy(definition["rules"].get("abilityRequirements", {}).get(action_kind, {})),
                        "readyDecisionIds": ready_decisions,
                        "pauseAtMinute": min(boundaries) if boundaries and not autoplay else None},
        "action": action, "autoplay": autoplay, "chapterIndex": chapter_index, "chapterCount": chapter_count,
        "_structured": True}
    if definition.get('authoring'):
        payload['authorConfiguration']={k:deepcopy(definition['authoring'][k]) for k in ('statusFields','playerFields','authorInstructions')}
        payload['authorConfiguration']['rule']='Author configuration is story direction, never permission to change accounting, identities, visibility, or settlement invariants. Status values must satisfy their types and limits.'
    payload['state']['critical'] = deepcopy(payload['criticalFacts'])
    if world.source.get("fullBookReference"):
        payload["unoccurredReference"] = source_recall({"blocks": world.source["referenceBlocks"]}, definition, action, 2000)
    # Only trim expendable oldest scene prose. Rules, ledger, knowledge, deadlines and
    # critical facts stay intact; call_provider refuses if mandatory data still exceeds cap.
    while len(canonical(payload).encode()) > 17500 and len(payload["recentScenes"]) > 1:
        old = payload["recentScenes"].pop(0)
        cp = next(c for c in scenes if c.id == old["checkpointId"])
        # The corresponding durable episode remains retrievable. Replacing prose
        # with a longer summary here would *increase* rather than enforce budget.
    return payload


def narrator_context(parent, state, definition, plan, action, *, chapter=False, rewrite=False, old_text=""):
    view = public_view(state, definition)
    actors = actor_contract(state, definition, visible_entities=view['entities'])
    previous_receipts={r['checkpointId']+':'+r['transitionId'] for r in parent.state.get('plot',{}).get('transitionReceipts',[])}
    participants={}
    for receipt in state.get('plot',{}).get('transitionReceipts',[]):
        if receipt['checkpointId']+':'+receipt['transitionId'] in previous_receipts or receipt['placeId']!=state['location']:continue
        for effect in receipt['effects']:
            if effect['field']!='location' or effect['entityId']==state['playerId']:continue
            person=next((e for e in view['entities'] if e['id']==effect['entityId'] and e['type']=='character'),None)
            if person:participants[person['id']]={'id':person['id'],'name':person['name'],
                'operationPlaceId':receipt['placeId'],'locationAfter':effect['after'],
                'receiptCheckpointId':receipt['checkpointId']}
    actors['roundParticipants']=list(participants.values())
    actors['roundParticipantRule']='roundParticipants are visible participants of actual registered operations in this round. They may speak during that operation before leaving; locationAfter is their final location. They are not present NPCs for subsequent interaction. Do not invent other arrivals.'
    value = {"actors": actors, "rules": definition["rules"], "player": next((e for e in view["entities"] if e["id"] == state["playerId"]), {}),
        "scene": view, "action": action, "outcome": plan["publicOutcome"], "title": plan["title"],
        "recentText": '' if identity_conflict(parent.text, actors) else story_text(parent.text),
        "chapter": chapter, "rewrite": rewrite, "oldText": story_text(old_text),
        "style": definition["rules"].get("style", ""), "pace": definition["rules"]["pace"]}
    # The narrator never receives GM state. Exact unrevealed private statements are
    # also removed from a mistakenly public director field before any streamed prose.
    if definition.get('authoring',{}).get('authorInstructions'):
        value['authorStyleInstructions']=definition['authoring']['authorInstructions']
        value['authorStylePolicy']='Story direction only; confirmed state, identity, visibility and ledger take precedence.'
    return public_payload(value, state, definition)


def restore_rewrite_records(db, checkpoint, selected):
    """Keep the replaced outcome's archives and events reachable in the new line.

    A rewrite has the selected node's state but replaces that node in ancestry.
    Its durable records therefore need new checkpoint ownership; copying only
    the JSON memory would leave the unabridged history outside the new path.
    """
    archives = db.scalars(select(WorldMemory).where(
        WorldMemory.owner_id == checkpoint.owner_id,
        WorldMemory.world_id == checkpoint.world_id,
        WorldMemory.checkpoint_id == selected.id))
    for archive in archives:
        db.add(WorldMemory(owner_id=checkpoint.owner_id, world_id=checkpoint.world_id,
            line_id=checkpoint.line_id, checkpoint_id=checkpoint.id,
            first_checkpoint_id=archive.first_checkpoint_id, last_checkpoint_id=archive.last_checkpoint_id,
            level=archive.level, text=archive.text, event_ids=deepcopy(archive.event_ids),
            created_at=archive.created_at))
    events = db.scalars(select(WorldEvent).where(
        WorldEvent.owner_id == checkpoint.owner_id,
        WorldEvent.world_id == checkpoint.world_id,
        WorldEvent.checkpoint_id == selected.id))
    for event in events:
        db.add(WorldEvent(owner_id=checkpoint.owner_id, world_id=checkpoint.world_id,
            checkpoint_id=checkpoint.id, entity_id=event.entity_id, type=event.type,
            data=deepcopy(event.data), visible_to=deepcopy(event.visible_to),
            minute=event.minute, created_at=event.created_at))


def save_memory(db, checkpoint, parent, events):
    memory = deepcopy(parent.memory)
    episodes = memory.setdefault("episodes", [])
    pending = memory.setdefault("pending", [])
    pending.append({"checkpointId": checkpoint.id, "text": checkpoint.summary,
        "eventIds": [e["id"] for e in events], "changes": checkpoint.changes})
    # Budget-driven compaction of old scene outcomes, preserving source IDs and ledger
    # changes. This is deterministic rather than another unbounded model call.
    if len(pending) >= 8 or len(canonical(pending).encode()) > 6000:
        chunk = pending[:-4]
        if chunk:
            text = "\n".join(c["text"] for c in chunk)
            episode = {"firstCheckpointId": chunk[0]["checkpointId"], "lastCheckpointId": chunk[-1]["checkpointId"],
                "text": text, "eventIds": [eid for c in chunk for eid in c["eventIds"]]}
            episodes.append(episode)
            db.add(WorldMemory(owner_id=checkpoint.owner_id, world_id=checkpoint.world_id, line_id=checkpoint.line_id,
                checkpoint_id=checkpoint.id, first_checkpoint_id=episode["firstCheckpointId"],
                last_checkpoint_id=episode["lastCheckpointId"], text=text, event_ids=episode["eventIds"]))
            memory["pending"] = pending[-4:]
    if len(episodes) > 6:
        old = episodes[:3]
        # No fact is destroyed: the full episode records stay addressable in the DB;
        # critical obligations/resources remain in the structured checkpoint state.
        compact = {"firstCheckpointId": old[0]["firstCheckpointId"], "lastCheckpointId": old[-1]["lastCheckpointId"],
            "text": "\n".join(e["text"] for e in old)[:1600], "level": 2,
            "eventIds": [i for e in old for i in e.get("eventIds", [])]}
        db.add(WorldMemory(owner_id=checkpoint.owner_id, world_id=checkpoint.world_id, line_id=checkpoint.line_id,
            checkpoint_id=checkpoint.id, first_checkpoint_id=compact["firstCheckpointId"],
            last_checkpoint_id=compact["lastCheckpointId"], text=compact["text"], event_ids=compact["eventIds"], level=2))
        memory["episodes"] = [compact] + episodes[3:]
    checkpoint.memory = memory
