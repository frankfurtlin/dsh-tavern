"""Validate persistent world ledgers, rather than per-response model limits."""
from pydantic import ValidationError

from ..world.schemas import EntityDefinition, EntityInitial, EventOutput, LoreEntry, WorldRules
from .errors import ArchiveError


def invalid():raise ArchiveError("BACKUP_WORLD_STATE_INVALID")


def strings(value, allowed=None):
    if not isinstance(value,list) or any(not isinstance(child,str) for child in value):invalid()
    if allowed is not None and any(child not in allowed for child in value):invalid()


def definition(value):
    required={"title","description","timeLabel","location","entities","initial","lore","unresolved","narrativeStyle","version","rules"}
    if not isinstance(value,dict) or not required<=set(value):invalid()
    if any(not isinstance(value[name],str) for name in ("title","description","timeLabel","location","narrativeStyle")) or type(value["version"]) is not int or value["version"]<1:invalid()
    if not isinstance(value["entities"],list) or not isinstance(value["lore"],list) or len(value["entities"])+len(value["lore"])>200000:invalid()
    try:
        entities=[EntityDefinition.model_validate(entry,strict=True).model_dump() for entry in value["entities"]]
        lore=[LoreEntry.model_validate(entry,strict=True).model_dump() for entry in value["lore"]]
        rules=WorldRules.model_validate(value["rules"],strict=True).model_dump()
    except (ValidationError,ValueError,TypeError):invalid()
    by_id={entity["id"]:entity for entity in entities};lore_ids={entry["id"] for entry in lore}
    if len(by_id)!=len(entities) or len(lore_ids)!=len(lore) or not by_id or value["location"] not in by_id or by_id[value["location"]]["type"]!="place":invalid()
    for entity in entities:
        strings(entity["knownFacts"],lore_ids);strings(entity["knownBy"],set(by_id)|{"player","all"})
    for entry in lore:
        strings(entry["entityIds"],by_id);strings(entry["knownBy"],set(by_id)|{"player","all"})
    if not isinstance(value["initial"],dict) or any(key not in by_id for key in value["initial"]):invalid()
    for key,initial in value["initial"].items():entity_state(initial,by_id,lore_ids)
    for key,targets in rules["routes"].items():
        if key not in by_id or by_id[key]["type"]!="place":invalid()
        strings(targets,{k for k,e in by_id.items() if e["type"]=="place"})
    from ..world.references import reference_problems
    if reference_problems(value):invalid()
    strings(value["unresolved"])
    return by_id,lore_ids


def entity_state(value, entities, lore_ids, *, state_minute=None):
    if not isinstance(value,dict):invalid()
    proof=value.get("relationEvidence",{})
    if "relationEvidence" in value and state_minute is None:invalid()
    try:state=EntityInitial.model_validate({k:v for k,v in value.items() if k!="relationEvidence"},strict=True).model_dump()
    except (ValidationError,ValueError,TypeError):invalid()
    if state["location"] is not None and (state["location"] not in entities or entities[state["location"]]["type"]!="place"):invalid()
    for key,count in state["inventory"].items():
        if key not in entities or entities[key]["type"]!="item" or type(count) is not int or count<0:invalid()
    if any(key not in entities for key in state["relations"]):invalid()
    strings(state["known"],lore_ids)
    if not isinstance(proof,dict) or any(key not in entities for key in proof):invalid()
    for evidence in proof.values():
        if not isinstance(evidence,dict) or set(evidence)!={"reason","checkpointId","minute"}:invalid()
        if not isinstance(evidence["reason"],str) or not evidence["reason"].strip() or len(evidence["reason"])>400:invalid()
        if not isinstance(evidence["checkpointId"],str) or len(evidence["checkpointId"])!=32:invalid()
        if type(evidence["minute"]) is not int or evidence["minute"]<0 or evidence["minute"]>state_minute:invalid()


def checkpoint(state, registry, *, event_tokens=None):
    entities,lore_ids=definition(registry)
    required={"playerId","minute","timeLabel","location","entities","known","discovered","critical","unresolved","queue","resolvedEvents","observed","ended"}
    if not isinstance(state,dict) or not required<=set(state) or type(state["minute"]) is not int or state["minute"]<0 or type(state["ended"]) is not bool or not isinstance(state["timeLabel"],str):invalid()
    if state["playerId"] not in entities or entities[state["playerId"]]["type"]!="character" or state["location"] not in entities or entities[state["location"]]["type"]!="place":invalid()
    if not isinstance(state["entities"],dict) or set(state["entities"])!=set(entities):invalid()
    for value in state["entities"].values():entity_state(value,entities,lore_ids,state_minute=state["minute"])
    for identifier,entity in entities.items():
        if entity["type"]=="item" and not entity["stackable"] and sum(value["inventory"].get(identifier,0) for value in state["entities"].values())>1:invalid()
    strings(state["known"],lore_ids);strings(state["discovered"],entities);strings(state["critical"])
    if not isinstance(state["unresolved"],list) or any(not isinstance(v,dict) or not isinstance(v.get("id"),str) or not isinstance(v.get("description"),str) for v in state["unresolved"]):invalid()
    if not isinstance(state["observed"],dict) or any(key not in entities or not isinstance(value,dict) for key,value in state["observed"].items()):invalid()
    if not isinstance(state["queue"],list):invalid()
    queue_ids=set()
    for event in state["queue"]:
        if not isinstance(event,dict) or not isinstance(event.get("id"),str) or event["id"] in queue_ids:invalid()
        queue_ids.add(event["id"])
        try:parsed=EventOutput.model_validate({key:event[key] for key in EventOutput.model_fields if key in event},strict=True).model_dump()
        except (ValidationError,ValueError,TypeError):invalid()
        if parsed["entityId"] is not None and parsed["entityId"] not in entities:invalid()
        strings(parsed["loreIds"],lore_ids);strings(parsed["visibleTo"],set(entities)|{"player","all"})
        if event_tokens is not None and event["id"] not in event_tokens:invalid()
    for name in ("resolvedEvents","reachedEvents"):
        if name in state:strings(state[name],event_tokens)
    if state["ended"] and state["entities"][state["playerId"]]["alive"] is not False:invalid()
