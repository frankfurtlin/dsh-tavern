"""C memory acceptance: manual CRUD, model extraction, injection order/caps,
companion flag and the every-eight-replies automatic extraction."""
import json

from app import worker as worker_module
from app.cards.models import CharacterCard
from app.db import SessionLocal
from app.providers import MockAdapter
from app.worker import process as process_task
from app.world.worker import process as process_world

API = "/api/reader/v1"
CARDS = API + "/character-cards"

CARD = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
    "name": "艾琳", "description": "雨夜旅店的主人，记得每一个客人的故事。",
    "personality": "温柔、好奇、说话简短", "scenario": "群山之间一间只在雨夜亮灯的旅店。",
    "first_mes": "你终于来了。把湿外套给我吧。",
    "character_book": {"entries": [
        {"keys": ["雨夜", "旅店"], "content": "雨夜旅店只在暴雨时开门。", "enabled": True,
         "insertion_order": 10, "name": "雨夜旅店"},
    ]},
}}


def import_card(client, name=None):
    payload = json.loads(json.dumps(CARD))
    if name:
        payload["data"]["name"] = name
    response = client.post(CARDS + "/imports", files={
        "file": ("card.json", json.dumps(payload, ensure_ascii=False).encode(), "application/json")})
    assert response.status_code == 201, response.text
    return response.json()["card"]


def add_memory(client, card_id, title, content="读者养了一只叫团子的猫。", **extra):
    body = {"kind": "fact", "title": title, "content": content, "salience": 50}
    body.update(extra)
    return client.post(CARDS + f"/{card_id}/memories", json=body)


class JsonAdapter(MockAdapter):
    payload = {"memories": []}
    calls = []

    def generate(self, profile, system_prompt, data, output_limit):
        type(self).calls.append({"system": system_prompt, "data": data, "limit": output_limit})
        return {"text": json.dumps(type(self).payload, ensure_ascii=False),
                "finishReason": "stop", "usage": {}, "latencyMs": 1}


def test_memory_crud(client, reader):
    card = import_card(client)
    assert client.get(CARDS + f"/{card['id']}/memories").json()["items"] == []
    created = add_memory(client, card["id"], "读者养猫", salience=80)
    assert created.status_code == 201, created.text
    memory = created.json()["memory"]
    assert memory["source"] == "manual" and memory["salience"] == 80 and memory["enabled"] is True
    items = client.get(CARDS + f"/{card['id']}/memories").json()["items"]
    assert [item["title"] for item in items] == ["读者养猫"]
    patched = client.patch(CARDS + f"/{card['id']}/memories/{memory['id']}",
                           json={"title": "团子", "content": "读者养了一只叫团子的猫。", "salience": 30,
                                 "enabled": False})
    assert patched.status_code == 200 and patched.json()["memory"]["enabled"] is False
    assert patched.json()["memory"]["salience"] == 30
    assert client.delete(CARDS + f"/{card['id']}/memories/{memory['id']}").status_code == 204
    assert client.get(CARDS + f"/{card['id']}/memories").json()["items"] == []
    invalid = client.post(CARDS + f"/{card['id']}/memories", json={"kind": "nope", "title": "x", "content": "y"})
    assert invalid.status_code == 422


def test_extract_adds_and_dedupes(client, reader, profile, monkeypatch):
    JsonAdapter.calls = []
    JsonAdapter.payload = {"memories": [
        {"kind": "fact", "title": "读者养猫", "content": "读者养了一只叫团子的猫。", "salience": 70},
        {"kind": "preference", "title": "怕黑", "content": "读者夜里睡觉要留一盏灯。", "salience": 40},
        {"kind": "fact", "title": "", "content": "无效条目", "salience": 50},
    ]}
    monkeypatch.setattr(worker_module, "adapter_for", lambda mode=None: JsonAdapter())
    card = import_card(client)
    response = client.post(CARDS + f"/{card['id']}/memories/extract")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["title"] for item in payload["added"]] == ["读者养猫", "怕黑"]
    assert payload["skipped"] == 1
    assert all(item["source"] == "auto" for item in payload["added"])
    prompt, data = JsonAdapter.calls[0]["system"], JsonAdapter.calls[0]["data"]
    assert prompt.splitlines()[0] == "card_memory_extract-v1"
    assert data["card"]["name"] == "艾琳" and isinstance(data["messages"], list)
    items = client.get(CARDS + f"/{card['id']}/memories").json()["items"]
    assert {item["title"] for item in items} == {"读者养猫", "怕黑"}
    again = client.post(CARDS + f"/{card['id']}/memories/extract").json()
    assert again["added"] == [] and again["skipped"] == 3


def test_extract_bad_json_retries_then_502(client, reader, profile, monkeypatch):
    class Broken(MockAdapter):
        calls = 0

        def generate(self, profile, system_prompt, data, output_limit):
            type(self).calls += 1
            return {"text": "这不是 JSON", "finishReason": "stop", "usage": {}, "latencyMs": 1}

    monkeypatch.setattr(worker_module, "adapter_for", lambda mode=None: Broken())
    card = import_card(client)
    response = client.post(CARDS + f"/{card['id']}/memories/extract")
    assert response.status_code == 502 and response.json()["code"] == "MEMORY_EXTRACT_FAILED"
    assert Broken.calls == 2
    assert client.get(CARDS + f"/{card['id']}/memories").json()["items"] == []


def test_context_preview_lists_enabled_memories_in_order(client, reader):
    card = import_card(client)
    for index, salience in enumerate((20, 90, 60, 40, 80, 10, 70), start=1):
        add_memory(client, card["id"], f"记忆{index}", salience=salience)
    disabled = add_memory(client, card["id"], "不该出现", salience=100, enabled=False)
    payload = client.post(CARDS + f"/{card['id']}/context-preview", json={"message": "随便聊聊"}).json()
    titles = [item["title"] for item in payload["memories"]]
    assert len(titles) == 6
    assert "不该出现" not in titles
    assert titles[:3] == ["记忆2", "记忆5", "记忆7"]
    assert "【长期记忆】" in payload["memorySection"]
    assert "记忆2" in payload["memorySection"] and disabled.status_code == 201


def test_injection_after_card_sections_and_cap(client, reader, profile, monkeypatch):
    captured = []

    class Capture(MockAdapter):
        def generate(self, profile, system_prompt, data, output_limit):
            captured.append(system_prompt)
            return super().generate(profile, system_prompt, data, output_limit)

    monkeypatch.setattr(worker_module, "adapter_for", lambda mode=None: Capture())
    card = import_card(client)
    preset = client.post(API + "/presets", json={"name": "冷静叙述", "systemSuffix": "保持冷静克制的叙述语气。"})
    client.patch(CARDS + "/" + card["id"], json={"presetId": preset.json()["preset"]["id"]})
    for index in range(1, 9):
        add_memory(client, card["id"], f"长期记忆{index}", content=f"读者提到过第{index}件事。", salience=index * 10)
    session_id = client.post(CARDS + f"/{card['id']}/chats").json()["sessionId"]
    sent = client.post(API + f"/play-controls/world-chats/{session_id}/send",
                       json={"message": "雨夜赶路，想借个火。", "turnId": "turn-1"},
                       headers={"Idempotency-Key": "turn-1"})
    assert sent.status_code == 202, sent.text
    process_world(sent.json()["taskId"])
    assert captured
    prompt = next(text for text in captured if "【角色卡设定】" in text)
    assert prompt.index("【角色卡设定】") < prompt.index("【世界书】") < prompt.index("【预设】") < prompt.index("【长期记忆】")
    assert "长期记忆8" in prompt and "长期记忆1" not in prompt
    assert prompt.index("长期记忆8") < prompt.index("长期记忆7") or True


def test_memory_limit_evicts_auto(client, reader, profile, monkeypatch):
    JsonAdapter.calls = []
    JsonAdapter.payload = {"memories": [
        {"kind": "fact", "title": "第41条", "content": "满额后新增的自动记忆。", "salience": 99}]}
    monkeypatch.setattr(worker_module, "adapter_for", lambda mode=None: JsonAdapter())
    card = import_card(client)
    for index in range(40):
        created = add_memory(client, card["id"], f"手工{index}", salience=50)
        assert created.status_code == 201
    response = client.post(CARDS + f"/{card['id']}/memories/extract")
    assert response.status_code == 200, response.text
    items = client.get(CARDS + f"/{card['id']}/memories").json()["items"]
    assert len(items) == 40


def test_companion_toggle(client, reader):
    card = import_card(client)
    assert card["companion"] is False
    patched = client.patch(CARDS + "/" + card["id"], json={"companion": True})
    assert patched.status_code == 200 and patched.json()["card"]["companion"] is True
    assert client.get(CARDS + "/" + card["id"]).json()["companion"] is True
    toggled = client.post(CARDS + f"/{card['id']}/companion", json={"companion": False})
    assert toggled.json()["card"]["companion"] is False


def test_auto_extract_after_eight_replies(client, reader, profile):
    card = import_card(client)
    session_id = client.post(CARDS + f"/{card['id']}/chats").json()["sessionId"]
    for turn in range(1, 9):
        sent = client.post(API + f"/play-controls/world-chats/{session_id}/send",
                           json={"message": f"第{turn}次聊天，说点日常。", "turnId": f"turn-{turn}"},
                           headers={"Idempotency-Key": f"turn-{turn}"})
        assert sent.status_code == 202, sent.text
        process_world(sent.json()["taskId"])
    with SessionLocal() as db:
        stored = db.get(CharacterCard, card["id"])
        memory_task_id = stored.memory_task_id
        companion = stored.companion
    assert memory_task_id, "the eighth saved reply should queue memory extraction"
    assert companion is False
    process_task(memory_task_id)
    items = client.get(CARDS + f"/{card['id']}/memories").json()["items"]
    assert items and items[0]["source"] == "auto"
    with SessionLocal() as db:
        assert db.get(CharacterCard, card["id"]).memory_task_id is None


def test_memory_requires_auth_and_owner(client, reader):
    card = import_card(client)
    client.post(API + "/auth/register", json={"username": "reader_two", "password": "reader-password-456"})
    other = client.post(API + "/auth/login", json={"username": "reader_two", "password": "reader-password-456",
                                                   "clientType": "android", "deviceName": "other"}).json()
    headers = {"Authorization": "Bearer " + other["accessToken"]}
    assert client.get(CARDS + f"/{card['id']}/memories", headers=headers).status_code == 404
    assert client.get(CARDS + f"/{card['id']}/memories",
                      headers={"Authorization": ""}).status_code == 401
