"""Bounded, repeatable source batches and stable registry merge."""
from copy import deepcopy
from ..domain import canonical, digest


def batches(source, limit=12):
    groups, current, size = [], [], 0
    for original in source["blocks"]:
        block = deepcopy(original)
        if len(canonical(block).encode()) > 8000:
            block["text"] = block["text"][-2000:]
        length = len(canonical(block).encode())
        if current and size + length > 8000:
            groups.append(current);current=[];size=0
        current.append(block);size+=length
    if current:
        groups.append(current)
    return groups[-limit:]


def merge(previous, incoming):
    """Later passages define the selected time; proven characters keep earlier IDs."""
    value=deepcopy(incoming)
    if not previous.get("entities"):
        return value
    entity_ids={(e["type"],e["name"]):e["id"] for e in previous["entities"]}
    for e in previous["entities"]:
        entity_ids.update({(e["type"],a):e["id"] for a in e.get("aliases",[])})
    previous_ids={e["id"] for e in previous["entities"]}
    def mapped_entity(entity):
        if entity["type"] != "character":
            return entity_ids.get((entity["type"], entity["name"]),
                "entity-" + digest(entity["type"] + ":" + entity["name"])[:24] if entity["id"] in previous_ids else entity["id"])
        # A name/alias is never enough to merge two people. Reusing an explicit
        # existing ID or a verified source UUID is different. A shared paragraph
        # may mention two same-name people and cannot itself prove continuity.
        matches = [old for old in previous["entities"] if old["type"] == "character" and (
            old["id"] == entity["id"] and entity["name"] in [old["name"], *old.get("aliases", [])]
            or entity.get("sourceCharacterId") and entity.get("sourceCharacterId") == old.get("sourceCharacterId"))]
        if len(matches) == 1:
            return matches[0]["id"]
        if entity["id"] in previous_ids:
            return "entity-" + digest(canonical(entity))[:24]
        return entity["id"]
    mapping = {e["id"]: mapped_entity(e) for e in value["entities"]}
    lore_ids={l["title"]:l["id"] for l in previous.get("lore",[])}
    previous_lore_ids={l["id"] for l in previous.get("lore",[])}
    lore_map={l["id"]:lore_ids.get(l["title"],
        "lore-"+digest(l["title"])[:24] if l["id"] in previous_lore_ids else l["id"]) for l in value["lore"]}
    for entity in value["entities"]:
        entity["id"]=mapping[entity["id"]]
        entity["knownFacts"]=[lore_map.get(k,k) for k in entity.get("knownFacts",[])]
        entity["knownBy"]=[mapping.get(k,k) for k in entity.get("knownBy",[])]
    initial={}
    for key,state in value["initial"].items():
        state["location"]=mapping.get(state.get("location"),state.get("location"))
        state["inventory"]={mapping.get(k,k):v for k,v in state["inventory"].items()}
        state["relations"]={mapping.get(k,k):v for k,v in state["relations"].items()}
        state["known"]=[lore_map.get(k,k) for k in state["known"]]
        initial[mapping.get(key,key)]=state
    for lore in value["lore"]:
        lore["id"]=lore_map[lore["id"]]
        lore["entityIds"]=[mapping.get(k,k) for k in lore["entityIds"]]
        lore["knownBy"]=[mapping.get(k,k) for k in lore["knownBy"]]
    entities={e["id"]:deepcopy(e) for e in previous["entities"]}
    for entity in value["entities"]:
        old=entities.get(entity["id"],{})
        combined=deepcopy(entity) if not old else {**old,**{k:v for k,v in entity.items() if v is not None and v != "" and v != []}}
        for key in ("aliases","knownFacts","knownBy"):
            combined[key]=list(dict.fromkeys(old.get(key,[])+entity.get(key,[])))
        evidence={digest(canonical(e)):e for e in old.get("evidence",[])+entity.get("evidence",[])}
        combined["evidence"]=list(evidence.values())
        entities[entity["id"]]=combined
    accumulated_initial=deepcopy(previous.get("initial",{}))
    for key,current in initial.items():
        old=accumulated_initial.get(key,{})
        combined={**old,**{k:v for k,v in current.items() if v is not None and v != "" and v != [] and v != {}}}
        for field in ("inventory","fields","relations"):
            combined[field]={**old.get(field,{}),**current.get(field,{})}
        combined["known"]=list(dict.fromkeys(old.get("known",[])+current.get("known",[])))
        accumulated_initial[key]=combined
    lore={e["id"]:e for e in previous.get("lore",[])}
    lore.update({e["id"]:e for e in value["lore"]})
    value.update(entities=list(entities.values()), lore=list(lore.values()),
                 initial=accumulated_initial, location=mapping.get(value["location"],value["location"]))
    for entity in value["entities"]:
        if entity["type"] == "item" and not entity.get("stackable",False):
            holders=[key for key,state in initial.items() if state["inventory"].get(entity["id"],0) == 1]
            if len(holders) == 1:
                for key,state in value["initial"].items():
                    if key != holders[0]:state["inventory"].pop(entity["id"],None)
    return value
