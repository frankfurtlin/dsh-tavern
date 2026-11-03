"""Deterministic provider fixtures, exclusively selected by AI_MODE=mock."""
import json
from ..domain import digest


def mock_result(prompt, data):
    if prompt.startswith("world-review"):
        return json.dumps({"consistent": True, "issues": []})
    if prompt.startswith("world-prepare"):
        blocks = data["source"]
        last = blocks[-1]
        names = []
        for name in ("林渡", "梅舟", "顾安", "沈禾"):
            if any(name in b["text"] for b in blocks):
                names.append(name)
        names = names[:3] or ["旅人"]
        entities = [{"id": "harbor", "name": "故事中的渡口", "type": "place", "source": "inferred", "description": "当前故事发生的地方"}]
        initial = {}
        for i, name in enumerate(names):
            block = next((b for b in blocks if name in b["text"]), last)
            entry = {"id": "actor-" + str(i), "name": name, "type": "character", "description": "身处眼前故事的人物", "source": "original" if name in block["text"] else "inferred", "evidence": [{"blockId": block["blockId"], "quote": name}] if name in block["text"] else []}
            entities.append(entry)
            initial[entry["id"]] = {"alive": True, "location": "harbor", "goal": "照料眼前的事情"}
        value = {"title": data["title"], "description": "渡口的风吹动衣角，未说完的故事在这里等你。", "timeLabel": "黄昏", "location": "harbor", "entities": entities, "initial": initial, "lore": [], "unresolved": ["眼前的故事将怎样继续？"]}
        return json.dumps(value, ensure_ascii=False)
    if prompt.startswith("world-director"):
        player = data["state"]["playerId"]
        action = data["action"]
        others = [e for e in data["entities"] if e["type"] == "character" and e["id"] != player]
        nearby = others[0]["id"] if others else player
        minutes = 60 if "小时" in action else 5
        return json.dumps({"title": "风里的回音", "summary": "你留心眼前处境，并与在场人物有了新的交流。", "elapsedMinutes": minutes, "timeLabel": "暮色渐深", "updates": [] if nearby == player else [{"entityId": nearby, "field": "relation:" + player, "value": "愿意继续交谈", "reason": "回应这次坦诚的交流", "visibleTo": ["player"]}], "events": [{"type": "interaction", "entityId": player, "description": action[:100], "visibleTo": ["player"]}], "publicOutcome": "你尝试" + action[:100] + "。在场的人认真回应，给出眼前可确认的线索，但仍留着需要你自己决定的事情。", "options": [{"text": "留意周围的细节", "kind": "observe"}, {"text": "问清刚才提到的事情", "kind": "dialogue"}, {"text": "沿着眼前的路继续走", "kind": "action"}], "critical": []}, ensure_ascii=False)
    if prompt.startswith("world-narrator"):
        outcome = data["outcome"]
        paragraphs = ["风从水面吹来，带着尚未散尽的凉意。你停住脚步，认真看着眼前的人。" + outcome,
            "对方没有立刻答应。他把手中的东西放稳，目光在你脸上停了一会儿，才慢慢开口。声音很轻，却足够让这次试探得到一个明确的回应。你记住他说过的话，也记住他没有说出口的那一部分。",
            "远处传来脚步声，暮色把渡口的轮廓一点点收拢。你没有急着替所有人作决定，只把眼前能确认的事情整理清楚。接下来往哪里走，仍然握在你的手中。"]
        if data.get("chapter"):
            paragraphs += ["你们沿着木板搭成的小路慢慢走。每一步都带着前面那场交谈留下的分量，没有人把刚才发生的事当作没发生过。对方偶尔回头，确认你仍跟在身后，目光中的戒备比先前少了一点。",
                "转过拐角时，风忽然静下来。有人在屋檐下收起晾着的衣服，另一个人点亮了门边的灯。你从这寻常的景象里辨认出日子仍在继续，而你此刻的选择已经在其中留下痕迹。",
                "等到脚步停下，那件眼前的事情终于有了交代。对方把话说到这里，便留出了片刻安静，让你有时间想清楚。灯光映着掌心，远处的道路仍向未见的地方延伸。这一段行程暂且落定，新的问题已在路的另一头等候。"]
        return "\n\n".join(paragraphs)
    raise ValueError("Unsupported world fixture")
