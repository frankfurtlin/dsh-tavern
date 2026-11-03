"""Typed world changes, visibility and immutable checkpoint ancestry.

The model proposes changes. This module owns the finite ledger constraints.
"""
from copy import deepcopy
from collections import defaultdict
import json
import re
from ..domain import canonical, digest, owned
from ..errors import Problem
from ..models import uid
from .models import WorldCheckpoint
from .schemas import PrepareOutput, DirectorOutput


def parse_json(text, schema):
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value)
    try:
        # A known provider trailer was appended after two otherwise complete
        # director objects. Accept only this exact trailer after a fully parsed
        # object; extra JSON, prose, partial objects and other suffixes still fail.
        if schema is DirectorOutput:
            complete, end = json.JSONDecoder().raw_decode(value)
            trailer = value[end:].strip()
            if trailer == 'parameter':value = value[:end]
        if schema is DirectorOutput:
            parsed=json.loads(value)
            updates=[]
            for update in parsed.get('updates',[]):
                # A received provider result contained an empty placeholder
                # with a misspelled field key. It expresses no state change;
                # never invent a target, field or value to repair it.
                if isinstance(update,dict) and update and 'entityId' in update and all(v is None for k,v in update.items() if k!='entityId'):
                    parsed.setdefault('warnings',[]).append('忽略了一条未包含字段和值的空白状态记录。')
                else:updates.append(update)
            parsed['updates']=updates
            lore=[];entities=[]
            for addition in parsed.get('additions',[]):
                if 'itemId' in addition and 'quantity' in addition and 'id' not in addition:
                    parsed.setdefault('transfers',[]).append(addition)
                elif addition.get('type')=='lore':
                    lore.append({key:val for key,val in {'id':addition.get('id'),'title':addition.get('name'),'text':addition.get('description'),'source':addition.get('source','game'),'knownBy':addition.get('knownBy',[]),'evidence':addition.get('evidence',[])}.items() if val is not None})
                else:entities.append(addition)
            parsed['additions']=entities
            if lore:parsed['newLore']=[*parsed.get('newLore',[]),*lore]
            # Lore associations occasionally leak into entity cards. They are
            # descriptive metadata, never entity state or ownership changes.
            for addition in parsed.get('additions',[]):
                addition.pop('entityIds',None)
            value=json.dumps(parsed,ensure_ascii=False)
        return schema.model_validate_json(value).model_dump()
    except Exception:
        raise Problem("WORLD_OUTPUT_INVALID", "世界结果尚未整理完成，已收到的正文会保留", 422)


def verify_evidence(entry, source_blocks):
    blocks = {b["blockId"]: b["text"] for b in source_blocks}
    evidence = entry.get("evidence", [])
    if any(e["blockId"] not in blocks or e["quote"] not in blocks[e["blockId"]] for e in evidence):
        raise Problem("WORLD_EVIDENCE_INVALID", "世界资料与来源不一致，未使用未验证资料")
    if entry.get("source") == "original" and not evidence:
        entry["source"] = "inferred"


def prepared_definition(raw, source_blocks, rules, previous=None, *, merged=False):
    # Each incoming batch is schema-validated before merge. The accumulated registry
    # can exceed the per-response entity cap without discarding earlier source facts.
    value = deepcopy(raw) if merged else PrepareOutput.model_validate(raw).model_dump()
    seen = set(); source_identities = set()
    for entity in value["entities"]:
        if entity["id"] in seen:
            raise Problem("WORLD_ENTITY_INVALID", "人物或物品标识重复")
        seen.add(entity["id"])
        if entity.get("sourceCharacterId"):
            if entity["type"] != "character" or entity["sourceCharacterId"] in source_identities:
                raise Problem("CHARACTER_REFERENCE_INVALID", "来源人物引用必须明确且唯一")
            source_identities.add(entity["sourceCharacterId"])
        verify_evidence(entity, source_blocks)
    if value["location"] not in seen or not any(e["id"] == value["location"] and e["type"] == "place" for e in value["entities"]):
        raise Problem("WORLD_LOCATION_INVALID", "起点地点尚未明确")
    for key, state in value["initial"].items():
        if key not in seen:
            raise Problem("WORLD_ENTITY_INVALID", "初始状态引用了未知人物")
        inventory = state.get("inventory", {})
        for item, count in inventory.items():
            if type(count) is not int or count < 0 or item not in seen or not any(e["id"] == item and e["type"] == "item" for e in value["entities"]):
                raise Problem("WORLD_INVENTORY_INVALID", "初始物品缺少有效出处或数量")
        if state.get("location") is not None and not any(e['id'] == state['location'] and e['type'] == 'place' for e in value['entities']):
            raise Problem("WORLD_LOCATION_INVALID", "初始位置不存在")
    lore_ids = set()
    for entity in value["entities"]:
        if any(k not in seen for k in entity["knownBy"] if k not in ("player", "all")):
            raise Problem("WORLD_ENTITY_INVALID", "人物可知范围引用了未知实体")
    for lore in value["lore"]:
        if lore["id"] in lore_ids or any(i not in seen for i in lore["entityIds"]) or any(i not in seen | {'player', 'all'} for i in lore['knownBy']):
            raise Problem("WORLD_LORE_INVALID", "资料条目关联不一致")
        lore_ids.add(lore["id"])
        verify_evidence(lore, source_blocks)
    for entity in value["entities"]:
        if any(k not in lore_ids for k in entity["knownFacts"]):
            raise Problem("WORLD_LORE_INVALID", "人物知识缺少可追溯资料")
        if entity["type"] == "item" and not entity["stackable"]:
            total = sum(s.get("inventory", {}).get(entity["id"], 0) for s in value["initial"].values())
            if total > 1:
                raise Problem("WORLD_INVENTORY_INVALID", "同一件独有物品不能同时在多个地方")
    value.update(version=(previous or {}).get("version", 0) + 1, rules=deepcopy(rules))
    from .references import reference_problems
    problems = reference_problems(value)
    if problems:
        raise Problem('WORLD_REFERENCE_INVALID', '世界资料含无效引用，请按报告确认对应条目', 422,
                      {'references': problems[:100], 'total': len(problems)})
    if not value["rules"].get("style"):
        value["rules"]["style"] = value["narrativeStyle"]
    return value


def initial_state(definition, character_id=None, custom_name="", identity=""):
    from .references import reference_problems
    problems = reference_problems(definition)
    if problems:
        raise Problem('WORLD_REFERENCE_INVALID', '世界资料需要引用修复，尚未开始游玩', 422,
                      {'references': problems[:100], 'total': len(problems)})
    entities = {e["id"]: {"alive": None, "location": None, "inventory": {}, "fields": {},
        "relations": {}, "goal": e.get("hiddenGoal", ""), "known": [],
        **deepcopy(definition.get("initial", {}).get(e["id"], {}))} for e in definition["entities"]}
    definition = deepcopy(definition)
    for entity in definition["entities"]:
        knowledge = set(entities[entity["id"]].get("known", [])) | set(entity.get("knownFacts", []))
        knowledge.update(l["id"] for l in definition.get("lore", []) if entity["id"] in l.get("knownBy", []) or "all" in l.get("knownBy", []))
        entities[entity["id"]]["known"] = sorted(knowledge)
        if not entities[entity["id"]].get("goal"):
            entities[entity["id"]]["goal"] = entity.get("hiddenGoal", "")
    if character_id:
        character = next((e for e in definition["entities"] if e["id"] == character_id and e["type"] == "character"), None)
        if not character:
            raise Problem("WORLD_CHARACTER_INVALID", "请选择这个世界中的人物")
        if entities[character_id].get("alive") is False:
            raise Problem("WORLD_CHARACTER_DEAD", "这个人物在当前起点已死亡，可以选择更早的起点")
        player = character_id
        known = set(character.get("knownFacts", [])) | set(entities[player].get("known", []))
        known.update(l["id"] for l in definition.get("lore", []) if player in l.get("knownBy", []))
    else:
        if not custom_name.strip() or not identity.strip():
            raise Problem("WORLD_IDENTITY_REQUIRED", "给你的人物起个名字，再写一句身份")
        player = "player-" + uid()[:20]
        definition["entities"].append({"id": player, "name": custom_name.strip(), "type": "character", "aliases": [],
            "description": identity.strip(), "traits": "", "source": "user", "evidence": [], "hiddenGoal": "", "knownFacts": []})
        entities[player] = {"alive": True, "location": definition["location"], "inventory": {}, "fields": {},
                            "relations": {}, "goal": "", "known": []}
        known = set()
        definition["version"] += 1
    location = entities[player].get("location") or definition["location"]
    entities[player]["location"] = location
    visible = [e["id"] for e in definition["entities"] if e["type"] == "place" and e["id"] == location or
               e["type"] == "character" and entities[e["id"]].get("location") == location]
    return definition, {"playerId": player, "minute": 0, "timeLabel": definition["timeLabel"], "location": location,
        "entities": entities, "known": sorted(known), "discovered": sorted(set(visible + [player, location])),
        "critical": [], "unresolved": [{"id": "initial-"+str(i), "description": text, "source": "initial"}
            for i, text in enumerate(definition.get("unresolved", []))], "queue": [], "resolvedEvents": [], "observed": {}, "ended": False}


def visible_to(audience, player):
    return "player" in audience or "all" in audience or player in audience


def event_audience_origins(path):
    """Find the creator of legacy queued events without changing frozen history."""
    origins = {}
    for checkpoint in path:
        for event in checkpoint.state.get("queue", []):
            origins.setdefault(event["id"], checkpoint.state["playerId"])
    return origins


def frozen_event(event, player):
    value = deepcopy(event)
    player = event.get("audiencePlayerId", player)
    value["audiencePlayerId"] = player
    if "audienceIds" not in value:
        value["audienceIds"] = list(dict.fromkeys(player if who == "player" else who[7:] if who.startswith("entity:") else who
                                                for who in event.get("visibleTo", []) if who != "all"))
        value["audienceAll"] = "all" in event.get("visibleTo", [])
    value["visibleTo"] = value["audienceIds"] + (["all"] if value.get("audienceAll") else [])
    if value.get("requiresDecision"):
        value.setdefault("decisionActorId", player)
    return value


def event_visible_to(event, player):
    if "audienceIds" in event:
        return event.get("audienceAll", False) or player in event["audienceIds"]
    audience = event.get("visibleTo", [])
    origin = event.get("audiencePlayerId", player)
    return "all" in audience or player in {origin if who == "player" else who for who in audience}


def decision_events(state):
    return [event for event in state.get("queue", []) if event.get("requiresDecision")
            and event.get("decisionActorId", event.get("audiencePlayerId", state["playerId"])) == state["playerId"]
            and event.get("dueMinute") is not None]


def execution_state(checkpoint, path):
    state = deepcopy(checkpoint.state)
    origins = event_audience_origins(path)
    state["queue"] = [frozen_event(event, origins.get(event["id"], state["playerId"]))
                      for event in state.get("queue", [])]
    return state


def private_phrases(state, definition):
    player, known = state["playerId"], set(state["known"])
    # Explicit knowledge, rather than token length, determines visibility. The
    # bounded legacy patterns cover identity statements in existing definitions;
    # new lore records carry exact privateTerms for names, codes and other short
    # secrets. This is a literal projection, not a semantic secrecy classifier.
    def terms(text):
        if not isinstance(text, str) or not text.strip():
            return set()
        result = {text}
        for pattern in (
            r"(?:是|为)([\u4e00-\u9fff·]{2,12})的(?:化名|假名|别名)",
            r"(?:真名|本名|原名|代号|口令|暗号|密码)(?:是|为|叫做|叫作|叫|[:：])\s*[“‘\"']?([\u4e00-\u9fffA-Za-z0-9·_-]{1,40})(?=[。！？，,；;：:\s“”‘’\"']|$)",
        ):
            result.update(re.findall(pattern, text))
        return result
    allowed = set()
    for entry in definition.get("lore", []):
        if entry["id"] in known:
            allowed.update(terms(entry["text"]))
            allowed.update(entry.get("privateTerms", []))
    denied = [e.get("hiddenGoal", "") for e in definition["entities"] if e["id"] != player]
    denied += [state["entities"].get(e["id"], {}).get("goal", "") for e in definition["entities"] if e["id"] != player]
    denied += [l["text"] for l in definition.get("lore", []) if l["id"] not in known]
    denied += [e.get(k, "") for e in definition["entities"] for k in ("description", "traits")
               if e.get("knownBy") and not visible_to(e["knownBy"], player) and e["id"] != player]
    protected = {term for value in denied for term in terms(value)}
    protected.update(term for entry in definition.get("lore", []) if entry["id"] not in known
                     for term in entry.get("privateTerms", []))
    return sorted(protected - allowed, key=lambda term: (len(term), term), reverse=True)


def scrub_phrases(value, phrases):
    if isinstance(value, str):
        for phrase in phrases:
            value = value.replace(phrase, "尚未明确的事情")
        return value
    if isinstance(value, list):
        return [scrub_phrases(v, phrases) for v in value]
    if isinstance(value, dict):
        return {k: scrub_phrases(v, phrases) for k, v in value.items()}
    return value


def public_payload(value, state, definition):
    """One projection for narration, metadata and event APIs, including nested values."""
    return scrub_phrases(value, private_phrases(state, definition))


def public_stream(content, phrases, final=False):
    # Hold only a suffix that could still become a private phrase, rather than
    # exposing its first tokens before a later chunk completes the phrase.
    end = len(content)
    if not final:
        for phrase in phrases:
            for length in range(min(len(phrase)-1, len(content)), 0, -1):
                if content.endswith(phrase[:length]):
                    end = min(end, len(content)-length)
                    break
    return scrub_phrases(content[:end], phrases)


def base_definition_lore(definition, plan):
    added = {l["id"] for l in plan["newLore"]}
    return [l for l in definition["lore"] if l["id"] not in added]


def apply_plan(base, definition, raw, checkpoint_id, source_blocks=(), *, action_kind=None, correction=False, allow_decisions=False, action=''):
    plan = DirectorOutput.model_validate(raw).model_dump()
    from .knowledge import normalize_knowledge
    plan = normalize_knowledge(plan, base, definition)
    from .work import prepare_work
    plan, work = prepare_work(plan, base)
    state, definition = deepcopy(base), deepcopy(definition)
    if work:state['work'] = work
    entities = state["entities"]
    player = state["playerId"]
    end_minute = state["minute"] + plan["elapsedMinutes"]
    resolutions = {resolution["eventId"]: resolution for resolution in plan["eventResolutions"]}
    decisions = decision_events({**state, "queue": state["queue"] + [frozen_event(event, player) for event in plan["events"]]})
    for event in decisions:
        boundary = max(state["minute"], event["dueMinute"])
        if end_minute <= boundary:
            continue
        resolution = resolutions.get(event.get("id"))
        cancelled_at = resolution.get("atMinute") if resolution else None
        if resolution and resolution["status"] == "cancelled" and (cancelled_at if cancelled_at is not None else state["minute"]) <= boundary:
            continue
        if resolution and (allow_decisions or event["dueMinute"] <= state["minute"] and action_kind != "wait"):
            continue
        raise Problem("WORLD_DECISION_REQUIRED", "故事需要先停在重要选择处，已收到的正文会保留")
    from .routes import journey_path
    changes, events = [], []
    traversed_places = set()
    revived = set()
    knowledge_updates = []
    definitions = {e["id"]: e for e in definition["entities"]}
    numeric_resources = {"money", "currency", "health", "stamina"}
    numeric_resources.update(k for costs in definition["rules"].get("actionCosts", {}).values() for k in costs if k != "minutes")
    for key, requirement in definition["rules"].get("abilityRequirements", {}).get(action_kind, {}).items():
        actual = entities[player]["fields"].get(key)
        if type(requirement) in (int, float):
            allowed = type(actual) in (int, float) and actual >= requirement
        else:
            allowed = actual == requirement
        if not allowed:
            raise Problem("WORLD_ABILITY_RULE", "当前能力不满足已明确的行动规则，正文会作为草稿保留")
    for addition in plan["additions"]:
        if addition["type"] != "character" and any(addition["name"] == existing["name"] or addition["name"] in existing.get("aliases", []) for existing in definitions.values()):
            raise Problem("WORLD_ENTITY_INVALID", "同一个人物或物品需要沿用原标识")
        if addition["source"] == "original":
            verify_evidence(addition, source_blocks)
        if addition["id"] in definitions or addition["source"] not in ("game", "user", "original", "inferred"):
            raise Problem("WORLD_ENTITY_INVALID", "新实体与已存在的世界资料冲突")
        definitions[addition["id"]] = addition
        definition["entities"].append(addition)
        entities[addition["id"]] = {"alive": None, "location": None, "inventory": {}, "fields": {}, "relations": {},
            "goal": addition.get("hiddenGoal", ""), "known": []}
    for lore in plan["newLore"]:
        if lore["source"] == "original":
            verify_evidence(lore, source_blocks)
        if any(i not in definitions for i in lore["entityIds"] + lore["knownBy"] if i != "player") or lore["id"] in {x["id"] for x in definition["lore"]}:
            raise Problem("WORLD_LORE_INVALID", "新增资料没有明确的世界归属")
        definition["lore"].append(lore)
    if plan["additions"] or plan["newLore"]:
        definition["version"] += 1
    from .settlement import guard_updates
    guard_updates(base, definition, plan)
    for update in plan["updates"]:
        entity_id, field, value = update["entityId"], update["field"], update["value"]
        if entity_id not in entities:
            raise Problem("WORLD_ENTITY_INVALID", "状态变化引用了不存在的人物")
        target = entities[entity_id]
        before = None
        if field == "alive":
            if type(value) is not bool:
                raise Problem("WORLD_STATE_INVALID", "生命状态格式无效")
            before = target.get("alive")
            if before is False and value is True and not correction:
                policy = definition["rules"]["deathPolicy"]
                if policy == "end" or not any(e["type"] == policy and e["entityId"] == entity_id for e in plan["events"]):
                    raise Problem("WORLD_DEATH_RULE", "恢复生命需要世界允许的救援或重生事件")
                revived.add(entity_id)
            target[field] = value
        elif field == "location":
            if not isinstance(value, str) or value not in definitions or definitions[value]["type"] != "place":
                raise Problem("WORLD_LOCATION_INVALID", "移动目标不是一个已知地点")
            before = target.get(field)
            path = journey_path(definition, before, value, correction=correction)
            target[field] = value
            if entity_id == player:
                state["location"] = value
                traversed_places.update(path)
        elif field == "inventory" or field.startswith("inventory:"):
            quantities = value if field == "inventory" else {field[10:]: value}
            if not correction or not isinstance(quantities, dict) or any(
                item not in definitions or definitions[item]["type"] != "item"
                or type(count) is not int or not 0 <= count <= 100000
                for item, count in quantities.items()
            ):
                raise Problem("WORLD_INVENTORY_INVALID", "库存调整需要已知物品和有效的整数数量")
            if field == "inventory":
                before = deepcopy(target.get("inventory", {}))
                target["inventory"] = {item: count for item, count in quantities.items() if count > 0}
                value = deepcopy(target["inventory"])
            else:
                item = field[10:]
                before = target["inventory"].get(item, 0)
                if value > 0:
                    target["inventory"][item] = value
                else:
                    target["inventory"].pop(item, None)
        elif field == "goal":
            if not isinstance(value, str) or len(value) > 500:
                raise Problem("WORLD_STATE_INVALID", "人物目标格式无效")
            before, target[field] = target.get(field), value
        elif field == "known":
            if not isinstance(value, list) or any(not isinstance(k, str) or k not in {l["id"] for l in definition["lore"]} for k in value):
                raise Problem("WORLD_LORE_INVALID", "人物获知的资料不存在")
            before = list(target.get("known", []))
            target[field] = sorted(set(before + value))
            if not correction:
                knowledge_updates.append((entity_id, set(value)-set(before)))
        elif field.startswith("relation:"):
            related = field.split(":", 1)[1]
            if related not in definitions or not isinstance(value, str) or len(value) > 300:
                raise Problem("WORLD_RELATION_INVALID", "关系变化格式无效")
            before = target["relations"].get(related)
            target["relations"][related] = value
            evidence = {"reason": update["reason"], "checkpointId": checkpoint_id, "minute": state["minute"] + plan["elapsedMinutes"]}
            target.setdefault("relationEvidence", {})[related] = evidence
            if visible_to(update["visibleTo"], player):
                state.setdefault("observed", {}).setdefault(entity_id, {})["relationEvidence:" + related] = deepcopy(evidence)
        elif field.startswith("field:"):
            key = field.split(":", 1)[1]
            list_value=isinstance(value,list) and len(value)<=20 and all(isinstance(v,str) and v.strip() and len(v)<=80 for v in value) and len(value)==len(set(value))
            if not key or len(key) > 80 or (type(value) not in (str, int, float, bool, type(None)) and not list_value):
                raise Problem("WORLD_STATE_INVALID", "扩展状态格式无效")
            if key in ("inventory", "known", "alive", "location", "playerId"):
                raise Problem("WORLD_STATE_INVALID", "这个状态需要使用对应的世界操作")
            before = target["fields"].get(key)
            if key in numeric_resources and not correction:
                if value != before:
                    raise Problem("WORLD_RESOURCE_INVALID", "资源变化需要明确的收支记录；未知资源不能凭空赋值")
                continue
            if key in ("money", "currency", "health", "stamina", "affection") and value is not None and (
                    type(value) not in (int, float) or value < 0 or key == "affection" and value > 100):
                raise Problem("WORLD_RESOURCE_INVALID", "已启用的资源值不符合规则")
            if key == "affection" and not definition["rules"]["relationshipNumbers"]:
                raise Problem("WORLD_RELATION_INVALID", "这个世界使用文字关系，不使用数值好感")
            target["fields"][key] = value
        else:
            raise Problem("WORLD_STATE_INVALID", "未定义的状态更新")
        change = {"entityId": entity_id, "field": field, "before": before, "after": value,
                  "reason": update["reason"], "visibleTo": update["visibleTo"]}
        if field == "location":
            change["path"] = path
        changes.append(change)
        if visible_to(update["visibleTo"], player):
            state.setdefault("observed", {}).setdefault(entity_id, {})[field] = deepcopy(value)
    for operation in plan["resources"]:
        entity_id, key, amount = operation["entityId"], operation["field"], operation["amount"]
        if entity_id not in entities or key not in numeric_resources:
            raise Problem("WORLD_RESOURCE_INVALID", "收支必须关联已启用的资源")
        target, kind = entities[entity_id], operation["operation"]
        before = target["fields"].get(key)
        if kind == "observe":
            if before is not None:
                raise Problem("WORLD_RESOURCE_INVALID", "已知资源只能通过收支更新，不能重读起点余额")
            verify_evidence({"evidence": operation["evidence"]}, source_blocks)
            number = str(int(amount) if amount.is_integer() else amount)
            if not operation["evidence"] or not any(re.search(r"(?<![\d.])"+re.escape(number)+r"(?![\d.])", e["quote"]) for e in operation["evidence"]):
                raise Problem("WORLD_RESOURCE_INVALID", "确认未知资源需要包含数量的原文证据")
            after = amount
        else:
            if type(before) not in (int, float):
                raise Problem("WORLD_RESOURCE_INVALID", "资源数量未知，不能当作零或无限使用")
            if kind == "earn" and not any(e["type"] == "resource_reward" and e["entityId"] == entity_id and e["description"] == operation["reason"] for e in plan["events"]):
                raise Problem("WORLD_RESOURCE_INVALID", "收入需要可追溯的获得事件")
            after = before + amount if kind == "earn" else before - amount
        if after < 0:
            raise Problem("WORLD_RESOURCE_INVALID", "没有足够资源支付这次消耗")
        target["fields"][key] = after
        if kind == "transfer":
            recipient = operation["counterpartyId"]
            if recipient not in entities or recipient == entity_id or type(entities[recipient]["fields"].get(key)) not in (int, float):
                raise Problem("WORLD_RESOURCE_INVALID", "付款的接收方和余额必须明确")
            entities[recipient]["fields"][key] += amount
        changes.append({"entityId": entity_id, "field": "field:"+key, "before": before, "after": after,
                        "reason": operation["reason"], "visibleTo": operation["visibleTo"]})
        if visible_to(operation["visibleTo"], player):
            state.setdefault("observed", {}).setdefault(entity_id, {})["field:"+key] = after
    costs = definition["rules"].get("actionCosts", {}).get(action_kind, {})
    if plan["elapsedMinutes"] < costs.get("minutes", 0):
        raise Problem("WORLD_RESOURCE_INVALID", "故事时间没有满足这次行动的规则代价")
    for key, amount in costs.items():
        if key == "minutes" or amount == 0:
            continue
        before, after = base["entities"][player]["fields"].get(key), entities[player]["fields"].get(key)
        if type(before) not in (int, float) or type(after) not in (int, float) or before - after < amount:
            raise Problem("WORLD_RESOURCE_INVALID", "这次行动的资源代价尚未支付")
    for transfer in plan["transfers"]:
        item, sender, recipient, quantity = transfer["itemId"], transfer["fromId"], transfer["toId"], transfer["quantity"]
        newly_created = item in {e["id"] for e in plan["additions"]} and definitions[item]["source"] == "game"
        if item not in definitions or definitions[item]["type"] != "item" or sender not in entities and not (sender is None and newly_created):
            raise Problem("WORLD_INVENTORY_INVALID", "物品转移缺少合法来源")
        if recipient is not None and recipient not in entities or sender == recipient:
            raise Problem("WORLD_INVENTORY_INVALID", "物品接收方无效")
        available = entities[sender]["inventory"].get(item, 0) if sender else quantity
        if type(available) is not int or available < quantity:
            raise Problem("WORLD_INVENTORY_INVALID", "没有足够的这件物品；正文会保留为草稿")
        if sender:
            entities[sender]["inventory"][item] = available - quantity
        if recipient:
            entities[recipient]["inventory"][item] = entities[recipient]["inventory"].get(item, 0) + quantity
        changes.append({"entityId": recipient or sender, "field": "inventory", "itemId": item, "fromId": sender,
            "toId": recipient, "quantity": quantity, "reason": transfer["reason"], "visibleTo": transfer["visibleTo"]})
    for entity_id in revived:
        before, after = base["entities"][entity_id], entities[entity_id]
        resource_loss = any(type(amount) in (int, float) and type(after["fields"].get(key)) in (int, float)
                            and after["fields"][key] < amount for key, amount in before.get("fields", {}).items())
        item_loss = any(after["inventory"].get(item, 0) < quantity
                        for item, quantity in before.get("inventory", {}).items())
        if not resource_loss and not item_loss:
            raise Problem("WORLD_DEATH_RULE", "恢复生命需要实际付出代价，临时支付后返还不算消耗")
    for entity in definition["entities"]:
        if entity["type"] == "item" and not entity.get("stackable", False):
            total = sum(s.get("inventory", {}).get(entity["id"], 0) for s in entities.values())
            if total > 1:
                raise Problem("WORLD_INVENTORY_INVALID", "同一件独有物品不能重复出现")
    state["minute"] += plan["elapsedMinutes"]
    state["timeLabel"] = plan["timeLabel"] or state["timeLabel"]
    if plan["location"]:
        if plan["location"] not in definitions or definitions[plan["location"]]["type"] != "place":
            raise Problem("WORLD_LOCATION_INVALID", "当前地点无效")
        before = state["location"]
        path = journey_path(definition, before, plan["location"], correction=correction)
        traversed_places.update(path)
        if before != plan["location"]:
            changes.append({"entityId": player, "field": "location", "before": before,
                            "after": plan["location"], "path": path, "reason": "沿已有路线前往目的地", "visibleTo": ["player"]})
        state["location"] = plan["location"]
        entities[player]["location"] = plan["location"]
    for index, event in enumerate(plan["events"]):
        if event["entityId"] is not None and event["entityId"] not in entities:
            raise Problem("WORLD_ENTITY_INVALID", "世界事件引用无效")
        value = {**frozen_event(event, player), "id": digest(checkpoint_id + ":" + str(index))[:24], "minute": state["minute"]}
        if event["dueMinute"] is not None and (event["dueMinute"] > state["minute"] or event["requiresDecision"]):
            value["scheduledAtMinute"] = state["minute"]
            state["queue"].append(value)
        else:
            events.append(value)
    queued = {event["id"]: event for event in state["queue"]}
    resolved = set()
    for resolution in plan["eventResolutions"]:
        event_id, status = resolution["eventId"], resolution["status"]
        scheduled = queued.get(event_id)
        resolved_minute = resolution["atMinute"] if resolution["atMinute"] is not None else base["minute"] if status == "cancelled" else state["minute"]
        if (not scheduled or event_id in resolved or not base["minute"] <= resolved_minute <= state["minute"]
                or status != "cancelled" and scheduled["dueMinute"] > resolved_minute):
            raise Problem("WORLD_EVENT_INVALID", "处理约定需要有效的待发生事件，履约或失约不能早于期限")
        events.append({**scheduled, "description": resolution["description"], "resolution": status,
                       "type": scheduled["type"] if status == "fulfilled" else "event_" + status,
                       "loreIds": scheduled.get("loreIds", []) if status == "fulfilled" else [],
                       "scheduledAtMinute": scheduled.get("scheduledAtMinute", scheduled["minute"]),
                       "minute": resolved_minute})
        resolved.add(event_id)
    reached = set(state.get("reachedEvents", []))
    for event_id, scheduled in queued.items():
        if event_id not in resolved and event_id not in reached and scheduled["dueMinute"] <= state["minute"]:
            events.append({**scheduled, "id": digest(event_id + ":reached")[:24], "type": "deadline_reached",
                           "description": "约定已到期，尚未处理：" + scheduled["description"], "loreIds": [],
                           "scheduledAtMinute": scheduled.get("scheduledAtMinute", scheduled["minute"]),
                           "minute": scheduled["dueMinute"]})
            reached.add(event_id)
    state["queue"] = [event for event in state["queue"] if event["id"] not in resolved]
    state["resolvedEvents"] = list(dict.fromkeys(state["resolvedEvents"] + sorted(resolved)))
    if reached:
        state["reachedEvents"] = sorted(reached)
    lore_by_id = {l["id"]:l for l in definition["lore"]}
    previous_lore_ids = {l["id"] for l in base_definition_lore(definition, plan)}
    knowledge = {key:set(base["entities"].get(key,{}).get("known",[])) for key in entities}
    for lore in definition["lore"]:
        if lore["id"] in previous_lore_ids:
            for key in entities:
                if key in lore.get("knownBy",[]) or "all" in lore.get("knownBy",[]) or key == player and "player" in lore.get("knownBy",[]):
                    knowledge[key].add(lore["id"])
    channels = {key:set() for key in entities}
    for event in events:
        if not event.get("loreIds"):
            continue
        if event["type"] not in ("knowledge_shared", "knowledge_observed") or any(k not in lore_by_id for k in event["loreIds"]):
            raise Problem("WORLD_KNOWLEDGE_INVALID", "获知资料需要明确的见闻或交流事件")
        source = event["entityId"]
        if source not in entities:
            raise Problem("WORLD_KNOWLEDGE_INVALID", "见闻事件需要明确的来源实体")
        recipients = [key for key in entities if definitions[key]["type"] == "character" and event_visible_to(event, key)]
        if not recipients:
            raise Problem("WORLD_KNOWLEDGE_INVALID", "见闻事件需要明确的接收人物")
        for recipient in recipients:
            location = entities[recipient].get("location")
            source_location = entities[source].get("location")
            before_location = base["entities"].get(recipient,{}).get("location")
            before_source = base["entities"].get(source,{}).get("location")
            colocated = location is not None and (location == source_location or source == location)
            contacted_before = before_location is not None and (before_location == before_source or source == before_location)
            held = entities[recipient].get("inventory",{}).get(source,0) > 0 or base["entities"].get(recipient,{}).get("inventory",{}).get(source,0) > 0
            if not colocated and not contacted_before and not held:
                raise Problem("WORLD_KNOWLEDGE_INVALID", "异地人物不能凭空获得没有接触渠道的资料")
            for lore_id in event["loreIds"]:
                if event["type"] == "knowledge_shared" and lore_id not in knowledge[source]:
                    raise Problem("WORLD_KNOWLEDGE_INVALID", "人物不能传播自己尚不知道的资料")
                channels[recipient].add(lore_id)
                knowledge[recipient].add(lore_id)
    for recipient, values in knowledge_updates:
        if any(key not in channels[recipient] for key in values):
            raise Problem("WORLD_KNOWLEDGE_INVALID", "新增人物知识缺少对应的见闻事件")
    for learned in plan["learned"]:
        if learned not in lore_by_id or learned not in knowledge[player] and learned not in channels[player]:
            raise Problem("WORLD_KNOWLEDGE_INVALID", "新获知资料缺少合理的见闻渠道")
    for key, values in knowledge.items():
        entities[key]["known"] = sorted(set(entities[key].get("known",[])) | values)
    known = set(base["known"]) | set(plan["learned"]) | knowledge[player] | set(entities[player].get("known",[]))
    state["known"] = sorted(known)
    entities[player]["known"] = state["known"]
    state["critical"] = list(dict.fromkeys(state["critical"] + plan["critical"]))
    unresolved_ids = {u["id"] for u in state.get("unresolved", [])}
    if any(key not in unresolved_ids for key in plan["resolvedUnresolved"]):
        raise Problem("WORLD_EVENT_INVALID", "解决的事项必须引用当前未解决事项")
    state["unresolved"] = [u for u in state.get("unresolved", []) if u["id"] not in plan["resolvedUnresolved"]]
    state["resolvedUnresolved"] = list(dict.fromkeys(state.get("resolvedUnresolved", []) + plan["resolvedUnresolved"]))
    current = {e["id"] for e in definition["entities"] if e["type"] == "character" and entities[e["id"]].get("location") == state["location"]}
    state["discovered"] = sorted(set(state["discovered"]) | current | {state["location"]} | traversed_places)
    from .settlement import apply_settlement
    apply_settlement(base, state, definition, plan, checkpoint_id, changes, events,
                     action=action, action_kind=action_kind, autoplay=allow_decisions)
    for item_id in definition.get('script', {}).get('conservedItemIds', []):
        if sum(e.get('inventory', {}).get(item_id, 0) for e in entities.values()) != 1:
            raise Problem('WORLD_SETTLEMENT_INVALID', '登记的唯一物证不能销毁或复制')
        for transfer in plan['transfers']:
            if transfer['itemId'] != item_id:continue
            sender, recipient = transfer['fromId'], transfer['toId']
            if not sender or not recipient or not any(
                    snapshot['entities'][sender].get('location') is not None
                    and snapshot['entities'][sender].get('location') == snapshot['entities'][recipient].get('location')
                    for snapshot in (base, state)):
                raise Problem('WORLD_SETTLEMENT_INVALID', '唯一物证交接需要实际接触，异地委托不等于签收')
    state["ended"] = entities[player].get("alive") is False and definition["rules"]["deathPolicy"] == "end"
    from ..scripts.plot import advance
    advance(state, definition, changes, checkpoint_id)
    public_changes = [{k: v for k, v in c.items() if k != "visibleTo"} for c in changes if visible_to(c["visibleTo"], player)]
    from ..authoring.service import validate_fields
    validate_fields(definition,state)
    return state, definition, public_changes, events


def public_view(state, definition):
    player = state["playerId"]
    discovered, known = set(state["discovered"]), set(state["known"])
    output = {"playerId": player, "minute": state["minute"], "timeLabel": state["timeLabel"], "location": state["location"],
              "ended": state["ended"], "entities": [], "lore": [], "rules": deepcopy(definition["rules"]), "definitionVersion": definition["version"], "checks": state.get("checks", [])}
    output["rules"]["routes"] = {key: [v for v in values if v in discovered] for key, values in definition["rules"].get("routes", {}).items() if key in discovered}
    output['fixedActions']=deepcopy(definition.get('authoring',{}).get('fixedActions',[]))
    config=definition.get('authoring',{})
    output['statusFields']=[{**{k:f[k] for k in ('id','label','type','width','group','minHeight') if k in f},
        'panelTheme':config.get('statusTheme','paper'),'panelTitle':config.get('statusTitle','')} for f in [*config.get('statusFields',[]),*config.get('playerFields',[])]]
    for entity in definition["entities"]:
        if entity["id"] not in discovered and entity["id"] != player:
            continue
        current = state["entities"][entity["id"]]
        own = entity["id"] == player
        observed = state.get("observed", {}).get(entity["id"], {})
        attributes_known = own or not entity.get("knownBy") or visible_to(entity["knownBy"], player)
        output["entities"].append({"id": entity["id"], "name": entity["name"], "type": entity["type"],
            "description": entity["description"] if attributes_known else "", "traits": entity["traits"] if attributes_known else "", "source": entity["source"],
            "evidence": entity["evidence"] if attributes_known else [], "nearby": own or current.get("location") == state["location"],
            "state": {"alive": current.get("alive") if own else observed.get("alive"), "location": current.get("location") if own or current.get("location") == state["location"] else observed.get("location"),
                "inventory": current.get("inventory", {}) if own else {}, "fields": current.get("fields", {}) if own else {k[6:]: v for k, v in observed.items() if k.startswith("field:")},
                "relations": current.get("relations", {}) if own else {k[9:]: v for k, v in observed.items() if k.startswith("relation:")},
                "relationEvidence": {k[17:]: v for k, v in observed.items() if k.startswith("relationEvidence:") and "relation:" + k[17:] in observed},
                "goal": current.get("goal", "") if own else ""}})
    for entry in definition.get("lore", []):
        if entry["id"] in known:
            output["lore"].append({k: v for k, v in entry.items() if k not in ("knownBy",)})
    own_items = state["entities"][player]["inventory"]
    represented = {e["id"] for e in output["entities"]}
    for entity in definition["entities"]:
        if entity["id"] in own_items and own_items[entity["id"]] > 0 and entity["id"] not in represented:
            output["entities"].append({k: v for k, v in entity.items() if k not in ("hiddenGoal", "knownFacts", "knownBy")})
    from ..scripts.plot import public_progress
    progress = public_progress(state, definition)
    if progress:output['plot'] = progress
    return public_payload(output, state, definition)


def ancestry(db, checkpoint, owner, limit=10000):
    result, seen = [], set()
    current = checkpoint
    while current:
        if current.id in seen or len(result) >= limit:
            raise Problem("WORLD_HISTORY_INVALID", "世界历史无法完整恢复")
        seen.add(current.id)
        result.append(current)
        current = owned(db, WorldCheckpoint, current.parent_checkpoint_id, owner) if current.parent_checkpoint_id else None
        if current and current.world_id != checkpoint.world_id:
            raise Problem("WORLD_HISTORY_INVALID", "世界历史归属不一致")
    return list(reversed(result))


def checkpoint_hash(state, definition, text, parent):
    return digest(canonical({"state": state, "definition": definition, "text": text, "parent": parent}))


def retrieve_lore(definition, state, message, *, private=False):
    nearby = {k for k, v in state["entities"].items() if v.get("location") == state["location"]} | {state["playerId"], state["location"]}
    known = set(state["known"])
    mentioned = {e["id"] for e in definition["entities"] if any(n and n in message for n in [e["name"], *e.get("aliases", [])])}
    scored = []
    for entry in definition.get("lore", []):
        if not private and entry["id"] not in known:
            continue
        score = 4 * len(set(entry.get("entityIds", [])) & nearby) + 10 * len(set(entry.get("entityIds", [])) & mentioned) + 8 * sum(1 for word in entry.get("triggers", []) if word and word in message)
        if score or entry["id"] in known and state["playerId"] in entry.get("entityIds", []):
            scored.append((score, entry))
    return [deepcopy(entry) for _, entry in sorted(scored, key=lambda x: (-x[0], x[1]["id"]))[:8]]
