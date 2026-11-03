"""Built-in starting worlds. The catalogue never exposes GM facts."""
from copy import deepcopy

from ..models import uid
from .schemas import PrepareOutput
from .state import prepared_definition
from . import douluo


def _setting(title, description, time, place, actor, identity, companion, role, item, clue, secret, goal, style):
    return {"title": title, "description": description, "timeLabel": time, "location": "place",
        "entities": [
            {"id": "place", "name": place, "type": "place", "description": description, "source": "user", "knownBy": ["actor", "companion"]},
            {"id": "actor", "name": actor, "type": "character", "description": identity, "traits": "留心他人，也坚持自己的判断", "source": "user", "knownFacts": ["clue"]},
            {"id": "companion", "name": companion, "type": "character", "description": role, "traits": "说话有分寸", "hiddenGoal": goal,
                "source": "user", "knownFacts": ["clue", "secret"]},
            {"id": "item", "name": item, "type": "item", "description": "由你随身保管的独有物品", "source": "user", "knownBy": ["actor"]}],
        "initial": {
            "actor": {"alive": True, "location": "place", "inventory": {"item": 1}, "fields": {"money": 12}, "relations": {"companion": "曾经见过，尚未熟悉"}, "known": ["clue"]},
            "companion": {"alive": True, "location": "place", "inventory": {}, "fields": {}, "relations": {"actor": "愿意听取来意"}, "goal": goal, "known": ["clue", "secret"]}},
        "lore": [
            {"id": "clue", "title": "眼前的约定", "text": clue, "triggers": [item, companion], "entityIds": ["actor", "companion", "item"], "knownBy": ["actor", "companion"], "source": "user"},
            {"id": "secret", "title": "尚未说出的事情", "text": secret, "triggers": [companion], "entityIds": ["companion"], "knownBy": ["companion"], "source": "user"}],
        "unresolved": ["你将如何回应眼前的约定？"], "narrativeStyle": style}


_TEMPLATES = {
    "original-mystery-v1": {"genre": "mystery", "definition": _setting(
        "钟楼失约", "雨夜的旧城书店即将打烊，一封便笺把两名陌生人留在灯下。", "雨夜，书店打烊前", "旧城书店", "许澄", "替人递送书信的年轻抄写员", "唐禾", "照看旧书店的店员", "蓝封便笺",
        "便笺约定今晚在书店碰面，请保管人亲手交还旧书。", "修钟匠把失约者的账册藏在书店楼梯第三级下面。", "在钟楼闭门前悄悄取回楼梯下的账册，避免牵连店主。", "克制细腻，以可观察的细节推动悬疑")},
    "original-fantasy-v1": {"genre": "fantasy", "definition": _setting(
        "雪关同行", "山道被新雪封住，关口客舍里两名旅人面对同一份通行约定。", "初雪后的清晨", "雪关客舍", "陆遥", "初次远行的年轻剑客", "闻青", "熟悉山道的采药人", "旧铜路牌",
        "旧铜路牌能证明持有人受邀前往山北，但须先找到一位同行见证人。", "闻青的师父正在北山石室疗伤，守关人并不知道此事。", "带回石室所需的药材，同时不向守关人透露师父的位置。", "清朗而有张力，行动有代价，人物保持自主判断")},
    "original-romance-v1": {"genre": "romance", "definition": _setting(
        "未寄出的合照", "旧照相馆整理最后一批底片，久别重逢的两人约好重新确认一张照片的去处。", "周末午后，照相馆关门前", "桥边照相馆", "江宁", "回乡整理旧照片的设计师", "苏晚", "负责保管底片的摄影师", "纸封合照",
        "纸封合照约定由两人一起决定是否寄给当年的朋友，未经同意不能拆封或寄出。", "苏晚已接受外地工作邀请，但尚未告诉江宁离开的日期。", "先确认江宁是否愿意坦诚谈及往事，再说明自己的离开计划。", "温和细腻，关系靠具体交流推进，不替玩家决定情感选择")},
    douluo.KEY: {"genre": "fantasy", "definition": douluo.setting()},
}


def catalogue():
    return [{"id": key, "title": value["definition"]["title"], "genre": value["genre"],
        "description": value["definition"]["description"]} for key, value in _TEMPLATES.items()]


def template_definition(template_id, rules):
    # All IDs are local to this new world. Names never merge people across worlds.
    template = _TEMPLATES[template_id]
    raw = PrepareOutput.model_validate(deepcopy(template["definition"])).model_dump()
    ids = {entry["id"]: uid() for entry in raw["entities"] + raw["lore"]}
    raw["location"] = ids[raw["location"]]
    for entity in raw["entities"]:
        entity["id"] = ids[entity["id"]]
        entity["knownFacts"] = [ids[key] for key in entity["knownFacts"]]
        entity["knownBy"] = [ids.get(key, key) for key in entity["knownBy"]]
    initial = {}
    for key, state in raw["initial"].items():
        state["location"] = ids[state["location"]] if state["location"] else None
        state["inventory"] = {ids[item]: quantity for item, quantity in state["inventory"].items()}
        state["relations"] = {ids[other]: relation for other, relation in state["relations"].items()}
        state["known"] = [ids[known] for known in state["known"]]
        initial[ids[key]] = state
    raw["initial"] = initial
    for lore in raw["lore"]:
        lore["id"] = ids[lore["id"]]
        lore["entityIds"] = [ids[key] for key in lore["entityIds"]]
        lore["knownBy"] = [ids.get(key, key) for key in lore["knownBy"]]
    definition = prepared_definition(raw, [], rules)
    return douluo.configure(definition) if template_id == douluo.KEY else definition
