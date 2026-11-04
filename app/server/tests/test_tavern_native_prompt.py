"""Native tavern P2: preset CRUD, ST prompt order, world-info depth, sampling."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.tavern import service as tavern_service
from app.tavern.models import TavernPreset

from app.providers import MockAdapter

BASE = "/api/reader/v1"
TAVERN = f"{BASE}/tavern"


def card_payload(name="阿岩"):
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": name,
            "description": "常年在雨夜码头守灯的人。",
            "personality": "寡言，但记性很好。",
            "scenario": "雨夜的旧码头。",
            "first_mes": "（阿岩抬头）你来了。",
            "mes_example": "<START>\n{{user}}: 你在等谁？\n{{char}}: 等一个会回来的人。",
            "alternate_greetings": [],
            "tags": ["雨夜"],
            "system_prompt": "你是一位擅长海港氛围的叙述者。",
            "post_history_instructions": "回复里保留潮气。",
        },
    }


def import_card(client, payload=None):
    response = client.post(f"{TAVERN}/characters/imports", files={
        "file": ("card.json", json.dumps(payload or card_payload()), "application/json")})
    assert response.status_code == 201, response.text
    return response.json()["card"]


def create_chat(client, card_id, body=None):
    response = client.post(f"{TAVERN}/characters/{card_id}/chats", json=body or {})
    assert response.status_code == 201, response.text
    return response.json()["chat"]


def world_info(client, card_id, **fields):
    body = {"keys": ["码头"], "content": "码头尽头的灯塔在雨里忽明忽暗。"}
    body.update(fields)
    response = client.post(f"{TAVERN}/characters/{card_id}/world-info", json=body)
    assert response.status_code == 201, response.text
    return response.json()["item"]


def send_message(client, chat_id, message):
    with client.stream("POST", f"{TAVERN}/chats/{chat_id}/messages",
                       json={"message": message}) as response:
        assert response.status_code == 200, response.read()
        for _ in response.iter_lines():
            pass


def test_prompt_sections_follow_st_order(client, reader):
    card = import_card(client)
    chat = create_chat(client, card["id"])
    response = client.get(f"{TAVERN}/characters/{card['id']}/prompt",
                          params={"chatId": chat["id"]})
    assert response.status_code == 200, response.text
    data = response.json()
    titles = [section["title"] for section in data["sections"]]
    assert titles == ["[系统提示词]", "[主提示词]", "[角色设定]", "[示例对话]",
                      "[对话历史]", "[后置指令]", "[本轮要求]"]
    assert data["presetName"] == "默认"
    prompt = data["systemPrompt"]
    assert prompt.startswith("tavern-v1")
    assert "你是一位擅长海港氛围的叙述者。" in prompt
    assert "{{char}}" not in prompt and "{{user}}" not in prompt
    assert "阿岩" in prompt and "潮气" in prompt
    # Empty chat still renders the reply instruction last.
    assert prompt.rstrip().endswith("Do not write User's lines or any name prefix.")


def test_world_info_positions_and_depth(client, reader, profile):
    card = import_card(client)
    chat = create_chat(client, card["id"])
    world_info(client, card["id"], keys=["码头"], content="灯塔在雨里忽明忽暗。",
               position="before_char", order=1)
    world_info(client, card["id"], keys=["雨夜"], content="水位正在上涨。",
               position="after_char", order=2)
    world_info(client, card["id"], keys=["码头"], content="守灯人记得每一个归人。",
               position="at_depth", depth=2, order=3, constant=True)
    send_message(client, chat["id"], "雨夜的码头上还有人吗？")

    data = client.get(f"{TAVERN}/characters/{card['id']}/prompt",
                      params={"chatId": chat["id"]}).json()
    prompt = data["systemPrompt"]
    index_before = prompt.index("灯塔在雨里忽明忽暗。")
    index_character = prompt.index("[角色设定]")
    index_after = prompt.index("水位正在上涨。")
    index_depth = prompt.index("守灯人记得每一个归人。")
    index_history = prompt.index("[对话历史]")
    assert index_before < index_character < index_after < index_history < index_depth
    assert {item["position"] for item in data["matched"]} == {"before_char", "after_char", "at_depth"}
    assert "潮气" in prompt


def test_world_info_probability_disabled_and_constant(client, reader):
    card = import_card(client)
    world_info(client, card["id"], keys=["码头"], content="不可能命中。", probability=0)
    world_info(client, card["id"], keys=["码头"], content="禁用条目。", enabled=False)
    world_info(client, card["id"], keys=["码头"], content="必定命中。", probability=100)
    world_info(client, card["id"], keys=["不存在"], content="常驻条目。", constant=True)
    preview = client.post(f"{TAVERN}/characters/{card['id']}/world-info/preview",
                          json={"message": "我走上码头。"})
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    contents = {item["content"] for item in payload["matched"]}
    assert contents == {"必定命中。", "常驻条目。"}
    assert "不可能命中" not in payload["injection"]
    assert "禁用条目" not in payload["injection"]
    assert "必定命中" in payload["injection"]
    reasons = {item["content"]: item["reason"] for item in payload["matched"]}
    assert reasons["必定命中。"] == "keyword:码头"
    assert reasons["常驻条目。"] == "constant"
    assert {item["reason"] for item in payload["reasons"]}.issuperset({"constant", "keyword:码头"})


def test_preset_crud_and_default_immutable(client, reader):
    items = client.get(f"{TAVERN}/presets").json()["items"]
    assert [item["name"] for item in items] == ["默认"]
    default_id = items[0]["id"]

    response = client.post(f"{TAVERN}/presets", json={
        "name": "冷雨", "main": "保持冷硬。", "system": "你是冷雨叙述者。",
        "jailbreak": "越狱文本", "postHistory": "后置提醒",
        "temperature": 0.4, "topP": 0.7, "maxTokens": 512, "stop": ["\nUser:"]})
    assert response.status_code == 201, response.text
    preset = response.json()["preset"]
    assert preset["isDefault"] is False
    assert preset["temperature"] == 0.4 and preset["stop"] == ["\nUser:"]

    patched = client.patch(f"{TAVERN}/presets/{preset['id']}", json={"temperature": 0.6})
    assert patched.status_code == 200 and patched.json()["preset"]["temperature"] == 0.6

    assert client.delete(f"{TAVERN}/presets/{default_id}").status_code == 409
    assert client.delete(f"{TAVERN}/presets/{preset['id']}").status_code == 204
    assert [item["name"] for item in client.get(f"{TAVERN}/presets").json()["items"]] == ["默认"]

    assert client.post(f"{TAVERN}/presets", json={"name": ""}).status_code == 422
    assert client.post(f"{TAVERN}/presets", json={
        "name": "坏参数", "temperature": 3}).status_code == 422
    assert client.post(f"{TAVERN}/presets", json={
        "name": "坏停止串", "stop": ["x" * 300]}).status_code == 422


def test_chat_preset_overrides_and_sampling_reaches_adapter(client, reader, profile,
                                                           monkeypatch):
    preset = client.post(f"{TAVERN}/presets", json={
        "name": "冷雨", "main": "预设主提示。", "system": "预设系统提示。",
        "temperature": 0.35, "topP": 0.65, "maxTokens": 256, "stop": ["END"]}).json()["preset"]
    card = import_card(client)
    chat = create_chat(client, card["id"], {"presetId": preset["id"]})
    assert chat["presetId"] == preset["id"]

    prompt = client.get(f"{TAVERN}/characters/{card['id']}/prompt",
                        params={"chatId": chat["id"]}).json()
    assert "预设系统提示。" in prompt["systemPrompt"]
    assert "预设主提示。" in prompt["systemPrompt"]

    captured = {}

    class Capture(MockAdapter):
        def generate_stream(self, profile, system_prompt, data, output_limit,
                            *, on_delta, cancel_check, sampling=None, **kwargs):
            captured.update({"sampling": sampling, "limit": output_limit,
                             "system": system_prompt})
            return super().generate_stream(profile, system_prompt, data, output_limit,
                                           on_delta=on_delta, cancel_check=cancel_check,
                                           sampling=sampling)

    monkeypatch.setattr("app.tavern.stream.adapter_for", lambda mode=None: Capture())
    send_message(client, chat["id"], "今晚还出海吗？")
    assert captured["limit"] == 256
    assert captured["sampling"] == {"temperature": 0.35, "topP": 0.65, "stop": ["END"]}
    assert "[系统提示词]" in captured["system"]


def test_chat_unknown_prompt_context_404(client, reader):
    card = import_card(client)
    other = import_card(client, card_payload("白露"))
    chat = create_chat(client, other["id"])
    assert client.get(f"{TAVERN}/characters/{card['id']}/prompt",
                      params={"chatId": chat["id"]}).status_code == 404
    assert client.get(f"{TAVERN}/characters/{card['id']}/prompt",
                      params={"chatId": "0" * 32}).status_code == 404


def test_chat_sampling_patch_and_get(client, reader, profile, monkeypatch):
    card = import_card(client)
    chat = create_chat(client, card["id"])
    assert chat["sampling"] == {}
    patched = client.patch(f"{TAVERN}/chats/{chat['id']}", json={
        "sampling": {"temperature": 0.5, "top_p": 0.8, "max_tokens": 512, "stop": ["END"]}})
    assert patched.status_code == 200, patched.text
    assert patched.json()["chat"]["sampling"] == {
        "temperature": 0.5, "topP": 0.8, "maxTokens": 512, "stop": ["END"]}
    detail = client.get(f"{TAVERN}/chats/{chat['id']}").json()
    assert detail["chat"]["sampling"]["maxTokens"] == 512
    assert client.patch(f"{TAVERN}/chats/{chat['id']}",
                        json={"sampling": {"temperature": 9}}).status_code == 422
    assert client.patch(f"{TAVERN}/chats/{chat['id']}",
                        json={"sampling": {"stop": ["x" * 300]}}).status_code == 422
    assert client.patch(f"{TAVERN}/chats/{chat['id']}",
                        json={"sampling": None}).json()["chat"]["sampling"] == {}

    captured = {}

    class Capture(MockAdapter):
        def generate_stream(self, profile, system_prompt, data, output_limit,
                            *, on_delta, cancel_check, sampling=None, **kwargs):
            captured.update({"sampling": sampling, "limit": output_limit})
            return super().generate_stream(profile, system_prompt, data, output_limit,
                                           on_delta=on_delta, cancel_check=cancel_check,
                                           sampling=sampling)

    client.patch(f"{TAVERN}/chats/{chat['id']}", json={
        "sampling": {"temperature": 0.5, "topP": 0.8, "maxTokens": 512, "stop": ["END"]}})
    monkeypatch.setattr("app.tavern.stream.adapter_for", lambda mode=None: Capture())
    send_message(client, chat["id"], "再讲一段。")
    assert captured["limit"] == 512
    assert captured["sampling"] == {"temperature": 0.5, "topP": 0.8, "stop": ["END"]}


def test_prompt_contract_fields_roundtrip(client, reader):
    card = import_card(client)
    response = client.patch(f"{TAVERN}/characters/{card['id']}/prompt", json={
        "system": "系统设定A", "main": "主提示A", "jailbreakPrompt": "越狱A",
        "postHistory": "后置A", "worldInfoPosition": "after_char"})
    assert response.status_code == 200, response.text
    prompt = client.get(f"{TAVERN}/characters/{card['id']}/prompt").json()
    assert prompt["system"] == "系统设定A"
    assert prompt["main"] == "主提示A"
    assert prompt["jailbreakPrompt"] == "越狱A"
    assert prompt["postHistory"] == "后置A"
    assert prompt["worldInfoPosition"] == "after_char"
    assert isinstance(prompt["segments"], list)
    assert "系统设定A" in prompt["systemPrompt"] and "越狱A" in prompt["systemPrompt"]

    snake = client.patch(f"{TAVERN}/characters/{card['id']}/prompt",
                         json={"main_prompt": "主提示B"})
    assert snake.status_code == 200, snake.text
    assert client.get(f"{TAVERN}/characters/{card['id']}/prompt").json()["main"] == "主提示B"


def test_preset_default_switch(client, reader):
    client.get(f"{TAVERN}/presets")  # lazily creates the built-in default preset
    first = client.post(f"{TAVERN}/presets", json={"name": "甲"}).json()["preset"]
    second = client.post(f"{TAVERN}/presets", json={"name": "乙"}).json()["preset"]
    response = client.patch(f"{TAVERN}/presets/{second['id']}", json={"is_default": True})
    assert response.status_code == 200, response.text
    items = client.get(f"{TAVERN}/presets").json()["items"]
    by_name = {item["name"]: item for item in items}
    assert by_name["乙"]["isDefault"] is True
    assert by_name["默认"]["isDefault"] is False
    assert items[0]["name"] == "乙"
    switched = client.patch(f"{TAVERN}/presets/{first['id']}", json={"isDefault": True})
    assert switched.status_code == 200
    assert [item["name"] for item in client.get(f"{TAVERN}/presets").json()["items"]][0] == "甲"


def _preset_rows(owner_id):
    with SessionLocal() as db:
        return list(db.scalars(select(TavernPreset).where(TavernPreset.owner_id == owner_id)))


def test_ensure_default_is_idempotent_across_ten_calls(client, reader):
    for _ in range(10):
        response = client.get(f"{TAVERN}/presets")
        assert response.status_code == 200, response.text
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.username == "reader_one")).id
    rows = _preset_rows(owner)
    default_rows = [row for row in rows if row.name == "默认"]
    assert len(default_rows) == 1
    assert default_rows[0].builtin is True
    assert default_rows[0].is_default is True
    # still exactly one default and one builtin row for the owner
    assert sum(1 for row in rows if row.is_default) == 1
    assert sum(1 for row in rows if row.builtin) == 1


def test_concurrent_default_creation_respects_unique_index(reader):
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.username == "reader_one")).id

    def create_default(_):
        with SessionLocal() as db:
            row = tavern_service.default_preset(db, owner)
            return row.id

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(create_default, range(12)))
    assert len(set(ids)) == 1
    rows = _preset_rows(owner)
    assert len([row for row in rows if row.builtin]) == 1
    assert len([row for row in rows if row.is_default]) == 1
    assert len(rows) == 1  # only the built-in default exists; no duplicates created
