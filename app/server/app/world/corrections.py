"""Explicit, versioned author corrections; ordinary turns retain world-rule gates."""
from copy import deepcopy
from pydantic import ValidationError
from ..errors import Problem
from ..models import uid
from .schemas import EntityDefinition, WorldRules
from .state import apply_plan, public_view


def visible_before(view, entity_id, field):
    if field.startswith("route:"):
        return deepcopy(view["rules"].get("routes", {}).get(field[6:], []))
    if field.startswith("rule:"):
        return deepcopy(view["rules"].get(field[5:]))
    entity = next((e for e in view["entities"] if e["id"] == entity_id), None)
    if not entity:
        return None
    if field in ("name", "description", "traits"):
        return entity.get(field) or None
    state = entity.get("state") or {}
    if field.startswith("field:"):
        return state.get("fields", {}).get(field[6:])
    if field.startswith("relation:"):
        return state.get("relations", {}).get(field[9:])
    if field.startswith("inventory:"):
        return state.get("inventory", {}).get(field[10:], 0) if entity_id == view["playerId"] else None
    if field in ("inventory", "goal") and entity_id != view["playerId"]:
        return None
    return deepcopy(state.get(field))


def corrected_state(base, original, data, checkpoint_id):
    definition, state = deepcopy(original), deepcopy(base)
    before_view = public_view(base, original)
    field, entity_id = data.field, data.entityId
    ledger_reason = data.reason[:400]
    plan = {"title": "世界资料调整", "elapsedMinutes": 0, "timeLabel": state["timeLabel"],
            "publicOutcome": data.reason, "updates": []}
    try:
        if field.startswith("rule:"):
            key = field[5:]
            if key not in WorldRules.model_fields:
                raise Problem("WORLD_CORRECTION_INVALID", "请选择有效的世界规则")
            definition["rules"] = WorldRules.model_validate({**definition["rules"], key: data.value}).model_dump()
        elif field.startswith("route:"):
            entity_id = field[6:]
            places = {e["id"] for e in before_view["entities"] if e["type"] == "place"}
            if entity_id not in places or not isinstance(data.value, list) or any(type(v) is not str or v not in places for v in data.value):
                raise Problem("WORLD_LOCATION_INVALID", "请选择已知地点之间的连接")
            routes = definition["rules"].setdefault("routes", {})
            # Editing the map of known places does not silently discard hidden geography.
            routes[entity_id] = list(dict.fromkeys([v for v in routes.get(entity_id, []) if v not in places] + data.value))
        elif field == "entity:add":
            if not isinstance(data.value, dict) or set(data.value) - {"name", "type", "description", "traits", "stackable"}:
                raise Problem("WORLD_CORRECTION_INVALID", "请填写有效的新增资料")
            entity = EntityDefinition.model_validate({**data.value, "id": "user-" + uid()[:20], "source": "user"}).model_dump()
            entity_id = entity["id"]
            plan["additions"] = [entity]
            if entity["type"] == "character":
                plan["updates"] = [{"entityId": entity_id, "field": "location", "value": state["location"],
                                    "reason": ledger_reason, "visibleTo": ["player"]}]
            state, definition, _, _ = apply_plan(state, definition, plan, checkpoint_id, correction=True)
            state["discovered"] = sorted(set(state["discovered"] + [entity_id]))
        elif field in ("name", "description", "traits"):
            entity = next((e for e in definition["entities"] if e["id"] == entity_id), None)
            if not entity:
                raise Problem("WORLD_CORRECTION_INVALID", "请选择有效的人物或实体")
            updated = EntityDefinition.model_validate({**entity, field: data.value, "source": "user", "evidence": []}).model_dump()
            if field in ("description", "traits") and updated["knownBy"]:
                updated["knownBy"] = list(dict.fromkeys(updated["knownBy"] + [state["playerId"]]))
            entity.update(updated)
        else:
            plan["updates"] = [{"entityId": entity_id, "field": field, "value": data.value,
                                "reason": ledger_reason, "visibleTo": ["player"]}]
            state, definition, _, _ = apply_plan(state, definition, plan, checkpoint_id, correction=True)
            if field.startswith("relation:"):
                related = field[9:]
                state["entities"][entity_id]["relationEvidence"][related]["reason"] = data.reason
                state["observed"][entity_id]["relationEvidence:" + related]["reason"] = data.reason
    except ValidationError:
        raise Problem("WORLD_CORRECTION_INVALID", "调整内容的格式或长度不符合要求")
    state["ended"] = state["entities"][state["playerId"]].get("alive") is False and definition["rules"]["deathPolicy"] == "end"
    definition["version"] = original["version"] + 1
    # Do not serialize an NPC's hidden goal, inventory, or unobserved attribute as the old value.
    change = {"entityId": entity_id, "field": field, "before": visible_before(before_view, entity_id, field),
              "after": data.value, "reason": data.reason, "source": "user"}
    return state, definition, [change]
