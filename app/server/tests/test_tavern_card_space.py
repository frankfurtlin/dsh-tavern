"""B1 card-space acceptance tests: card edit, lorebooks, presets, regex, preview."""
import base64
import io
import json
import re
from types import SimpleNamespace

from PIL import Image, PngImagePlugin

from app.db import SessionLocal
from app.providers import MockAdapter
from app.world.worker import process
from app import worker as worker_module

API = "/api/reader/v1"
CARDS = API + "/character-cards"
PRESETS = API + "/presets"
REGEX = API + "/regex-scripts"
HEX32 = re.compile(r"^[a-f0-9]{32}$")

V2_CARD = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "艾琳",
        "description": "雨夜旅店的主人，记得每一个客人的故事。",
        "personality": "温柔、好奇、说话简短",
        "scenario": "群山之间一间只在雨夜亮灯的旅店。",
        "first_mes": "你终于来了。把湿外套给我吧。",
        "tags": ["奇幻", "旅店"],
        "creator": "tester",
        "character_book": {
            "name": "旅店世界书",
            "entries": [
                {"keys": ["雨夜", "旅店"], "content": "雨夜旅店只在暴雨时开门，屋檐下挂着一盏蓝灯。",
                 "enabled": True, "insertion_order": 10, "name": "雨夜旅店"},
                {"keys": ["火炉"], "content": "火炉边的第三把椅子永远为客人留着。",
                 "enabled": False, "insertion_order": 20, "name": "第三把椅子"},
                {"keys": ["群山"], "content": "群山深处有旧时代的钟声。",
                 "enabled": True, "insertion_order": 5, "name": "群山"},
            ],
        },
    },
}

V3_CARD = {
    "spec": "chara_card_v3",
    "spec_version": "3.0",
    "data": {
        "name": "岚",
        "description": "在渡口等船的人。",
        "character_book": {"entries": [{"keys": ["渡口"], "content": "渡口每天黄昏发最后一班船。",
                                         "enabled": True, "insertion_order": 3, "name": "渡口"}]},
    },
}


def card_png(payload):
    image = Image.new("RGB", (8, 8), (30, 60, 90))
    info = PngImagePlugin.PngInfo()
    info.add_text("chara", base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii"))
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def upload(client, data, name, content_type):
    return client.post(CARDS + "/imports", files={"file": (name, data, content_type)})


def import_card(client, payload=V2_CARD, name="card.json"):
    response = upload(client, json.dumps(payload).encode("utf-8"), name, "application/json")
    assert response.status_code == 201, response.text
    return response.json()["card"]


def create_preset(client, name="冷静叙述", suffix="保持冷静克制的叙述语气。"):
    response = client.post(PRESETS, json={"name": name, "systemSuffix": suffix})
    assert response.status_code == 201, response.text
    return response.json()["preset"]


def send(client, session_id, message, turn_id="message-1"):
    return client.post(API + "/play-controls/world-chats/" + session_id + "/send",
                       json={"message": message, "turnId": turn_id},
                       headers={"Idempotency-Key": turn_id})


def test_patch_card_updates_fields_and_preset(client, reader):
    card = import_card(client)
    preset = create_preset(client)
    response = client.patch(CARDS + "/" + card["id"], json={
        "name": "艾琳·改", "description": "新的描述", "personality": "安静",
        "systemPrompt": "用第二人称叙述。", "tags": ["奇幻"], "presetId": preset["id"]})
    assert response.status_code == 200, response.text
    updated = response.json()["card"]
    assert updated["name"] == "艾琳·改"
    assert updated["description"] == "新的描述"
    assert updated["tags"] == ["奇幻"]
    assert updated["presetId"] == preset["id"]
    assert client.get(CARDS + "/" + card["id"]).json()["name"] == "艾琳·改"
    cleared = client.patch(CARDS + "/" + card["id"], json={"presetId": None})
    assert cleared.json()["card"]["presetId"] is None


def test_import_parses_v2_and_v3_character_book(client, reader):
    card = import_card(client)
    items = client.get(CARDS + "/" + card["id"] + "/lore").json()["items"]
    assert [item["name"] for item in items] == ["群山", "雨夜旅店", "第三把椅子"]
    assert all(item["source"] == "import" for item in items)
    assert items[2]["enabled"] is False
    assert items[0]["order"] == 5
    third = import_card(client, V3_CARD, "v3.json")
    lore = client.get(CARDS + "/" + third["id"] + "/lore").json()["items"]
    assert [item["keys"] for item in lore] == [["渡口"]]


def test_manual_lore_crud(client, reader):
    card = import_card(client)
    created = client.post(CARDS + "/" + card["id"] + "/lore",
                          json={"keys": ["雨", "雨声"], "content": "雨声会让人想起远方。"})
    assert created.status_code == 201, created.text
    lore = created.json()["lore"]
    assert HEX32.match(lore["id"])
    assert lore["source"] == "manual"
    assert lore["order"] == 100
    patched = client.patch(CARDS + "/" + card["id"] + "/lore/" + lore["id"],
                           json={"content": "雨声让人安心。", "order": 7, "enabled": False})
    assert patched.status_code == 200, patched.text
    assert patched.json()["lore"]["order"] == 7
    items = client.get(CARDS + "/" + card["id"] + "/lore").json()["items"]
    manual = next(item for item in items if item["id"] == lore["id"])
    assert manual["enabled"] is False and manual["content"] == "雨声让人安心。"
    assert client.delete(CARDS + "/" + card["id"] + "/lore/" + lore["id"]).status_code == 204
    remaining = client.get(CARDS + "/" + card["id"] + "/lore").json()["items"]
    assert lore["id"] not in {item["id"] for item in remaining}


def test_lore_requires_keys_and_content(client, reader):
    card = import_card(client)
    assert client.post(CARDS + "/" + card["id"] + "/lore", json={"keys": [], "content": "x"}).status_code == 422
    assert client.post(CARDS + "/" + card["id"] + "/lore", json={"keys": ["x"], "content": ""}).status_code == 422


def test_preset_crud_binding_and_delete_clears_card(client, reader):
    card = import_card(client)
    preset = create_preset(client)
    assert client.get(PRESETS).json()["items"][0]["id"] == preset["id"]
    patched = client.patch(PRESETS + "/" + preset["id"], json={"systemSuffix": "更克制的语气。", "enabled": False})
    assert patched.status_code == 200, patched.text
    assert patched.json()["preset"]["systemSuffix"] == "更克制的语气。"
    assert patched.json()["preset"]["enabled"] is False
    client.patch(CARDS + "/" + card["id"], json={"presetId": preset["id"]})
    assert client.delete(PRESETS + "/" + preset["id"]).status_code == 204
    assert client.get(CARDS + "/" + card["id"]).json()["presetId"] is None
    assert client.get(PRESETS).json()["items"] == []


def test_regex_crud_and_invalid_pattern(client, reader):
    created = client.post(REGEX, json={"name": "句号变感叹", "pattern": "。", "replacement": "！", "order": 3})
    assert created.status_code == 201, created.text
    script = created.json()["script"]
    assert script["order"] == 3 and script["enabled"] is True
    invalid = client.post(REGEX, json={"name": "坏规则", "pattern": "(", "replacement": "x"})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "REGEX_INVALID"
    bad_patch = client.patch(REGEX + "/" + script["id"], json={"pattern": "[", "replacement": ""})
    assert bad_patch.status_code == 422
    assert bad_patch.json()["code"] == "REGEX_INVALID"
    good = client.patch(REGEX + "/" + script["id"], json={"enabled": False, "replacement": "!"})
    assert good.status_code == 200, good.text
    assert good.json()["script"]["replacement"] == "!"
    assert client.delete(REGEX + "/" + script["id"]).status_code == 204


def test_context_preview_matches_lore_and_preset(client, reader):
    card = import_card(client)
    preset = create_preset(client)
    client.patch(CARDS + "/" + card["id"], json={"presetId": preset["id"]})
    matched = client.post(CARDS + "/" + card["id"] + "/context-preview", json={"message": "雨夜赶路真冷"})
    assert matched.status_code == 200, matched.text
    payload = matched.json()
    assert [item["name"] for item in payload["loreMatched"]] == ["雨夜旅店"]
    assert payload["preset"] == {"id": preset["id"], "name": "冷静叙述"}
    assert payload["systemSuffix"] == "保持冷静克制的叙述语气。"
    unmatched = client.post(CARDS + "/" + card["id"] + "/context-preview", json={"message": "今天阳光很好"}).json()
    assert unmatched["loreMatched"] == []


def test_context_preview_ignores_disabled_lore(client, reader):
    card = import_card(client)
    items = client.get(CARDS + "/" + card["id"] + "/lore").json()["items"]
    target = next(item for item in items if item["name"] == "雨夜旅店")
    client.patch(CARDS + "/" + card["id"] + "/lore/" + target["id"], json={"enabled": False})
    payload = client.post(CARDS + "/" + card["id"] + "/context-preview", json={"message": "雨夜"}).json()
    assert all(item["name"] != "雨夜旅店" for item in payload["loreMatched"])
    assert payload["preset"] is None


def _task_for(reader, card, message, recent=()):
    user_id = reader["user"]["id"]
    return SimpleNamespace(owner_id=user_id, payload={
        "worldId": card["chatTarget"]["worldId"], "message": message,
        "messages": [{"role": "user", "text": text} for text in recent],
    })


def test_injection_order_hit_and_cap(client, reader):
    from app.cards import space
    card = import_card(client)
    preset = create_preset(client, "预设甲", "预设的追加提示。")
    client.patch(CARDS + "/" + card["id"], json={"presetId": preset["id"]})
    task = _task_for(reader, card, "雨夜赶路真冷", recent=["群山里有钟声吗？"])
    with SessionLocal() as db:
        text = space.chat_injection(db, task)
        empty = space.chat_injection(db, SimpleNamespace(owner_id=reader["user"]["id"], payload={"worldId": "0" * 32}))
    assert empty == ""
    assert text.startswith("以下内容仅作扮演补充")
    assert "【角色卡设定】" in text and "【世界书】" in text and "【预设】" in text
    assert text.index("【角色卡设定】") < text.index("【世界书】") < text.index("【预设】")
    assert "雨夜旅店" in text and "群山" in text and "第三把椅子" not in text
    assert "预设的追加提示。" in text
    # Matching also sees the recent user message keys, not just the current one.
    lore_only = _task_for(reader, card, "无关的话", recent=["火炉边有人吗？"])
    with SessionLocal() as db:
        matched = space.chat_injection(db, lore_only)
    assert "第三把椅子" not in matched  # disabled entries never inject


def test_injection_uses_recent_user_messages(client, reader):
    from app.cards import space
    card = import_card(client)
    task = _task_for(reader, card, "嗯。", recent=["群山里的钟声你听过吗？"])
    with SessionLocal() as db:
        text = space.chat_injection(db, task)
    assert "群山深处有旧时代的钟声。" in text


class CaptureAdapter(MockAdapter):
    captured = []

    def generate(self, profile, system_prompt, data, output_limit):
        CaptureAdapter.captured.append(system_prompt)
        return super().generate(profile, system_prompt, data, output_limit)


def test_chat_generation_prompt_gets_card_sections(client, reader, profile, monkeypatch):
    CaptureAdapter.captured = []
    card = import_card(client)
    preset = create_preset(client)
    client.patch(CARDS + "/" + card["id"], json={"presetId": preset["id"]})
    monkeypatch.setattr(worker_module, "adapter_for", lambda: CaptureAdapter())
    session_id = client.post(CARDS + "/" + card["id"] + "/chats").json()["sessionId"]
    response = send(client, session_id, "雨夜赶路，想借个火。")
    assert response.status_code == 202, response.text
    process(response.json()["taskId"])
    assert CaptureAdapter.captured, "generation call was not captured"
    prompt = CaptureAdapter.captured[0]
    assert prompt.index("【角色卡设定】") < prompt.index("【世界书】") < prompt.index("【预设】")
    assert "雨夜旅店只在暴雨时开门" in prompt


def test_regex_replacement_is_saved_before_streaming(client, reader, profile):
    card = import_card(client)
    target = "正则替换后的回复"
    created = client.post(REGEX, json={"name": "整体替换", "pattern": "(?s)^.*$", "replacement": target, "order": 1})
    assert created.status_code == 201, created.text
    client.post(REGEX, json={"name": "不该执行", "pattern": "(?s)^.*$", "replacement": "不应出现", "enabled": False, "order": 2})
    session_id = client.post(CARDS + "/" + card["id"] + "/chats").json()["sessionId"]
    response = send(client, session_id, "把湿外套给我吧。")
    assert response.status_code == 202, response.text
    process(response.json()["taskId"])
    current = client.get(API + "/play-controls/world-chats/" + session_id).json()
    assert current["messages"][-1]["role"] == "assistant"
    assert current["messages"][-1]["text"] == target


def test_card_space_requires_auth(client):
    assert client.get(CARDS + "/" + "0" * 32 + "/lore").status_code == 401
    assert client.get(PRESETS).status_code == 401
    assert client.get(REGEX).status_code == 401


def test_card_space_is_owner_scoped(client, reader):
    card = import_card(client)
    client.post(API + "/auth/register", json={"username": "reader_two", "password": "reader-password-456"})
    other = client.post(API + "/auth/login", json={"username": "reader_two", "password": "reader-password-456",
                                                   "clientType": "android", "deviceName": "other"}).json()
    headers = {"Authorization": "Bearer " + other["accessToken"]}
    assert client.get(CARDS + "/" + card["id"] + "/lore", headers=headers).status_code == 404
    assert client.post(CARDS + "/" + card["id"] + "/lore", headers=headers,
                       json={"keys": ["x"], "content": "y"}).status_code == 404
    assert client.patch(CARDS + "/" + card["id"], headers=headers, json={"name": "偷改"}).status_code == 404
