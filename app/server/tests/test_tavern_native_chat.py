"""Native tavern chat: card import, chats, SSE replies, swipes, edit, prompt."""
import json

from app.providers import MockAdapter

BASE = "/api/reader/v1"
TAVERN = f"{BASE}/tavern"


def card_payload(name="阿岩", first_message="（阿岩抬头）你来了。", greetings=None):
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": name,
            "description": "常年在雨夜码头守灯的人。",
            "personality": "寡言，但记性很好。",
            "scenario": "雨夜的旧码头。",
            "first_mes": first_message,
            "mes_example": "<START>\n{{user}}: 你在等谁？\n{{char}}: 等一个会回来的人。",
            "alternate_greetings": greetings or ["雨还在下，你带伞了吗？"],
            "tags": ["雨夜", "码头"],
            "system_prompt": "",
            "post_history_instructions": "",
        },
    }


def import_card(client, payload=None):
    response = client.post(f"{TAVERN}/characters/imports", files={
        "file": ("card.json", json.dumps(payload or card_payload()), "application/json")})
    assert response.status_code == 201, response.text
    return response.json()["card"]


def create_chat(client, card_id, greeting_id=None):
    body = {} if greeting_id is None else {"greetingId": greeting_id}
    response = client.post(f"{TAVERN}/characters/{card_id}/chats", json=body)
    assert response.status_code == 201, response.text
    return response.json()["chat"]


def read_sse(client, method, url, **kwargs):
    events = []
    with client.stream(method, url, **kwargs) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def deltas(events):
    return "".join(event["text"] for event in events if event["type"] == "delta")


def test_import_list_detail_export_and_delete(client, reader):
    card = import_card(client)
    assert card["name"] == "阿岩"
    assert card["source"] == "json"

    items = client.get(f"{TAVERN}/characters").json()["items"]
    assert [item["name"] for item in items] == ["阿岩"]
    assert items[0]["chatCount"] == 0

    detail = client.get(f"{TAVERN}/characters/{card['id']}").json()
    assert detail["card"]["name"] == "阿岩"
    assert detail["worldInfoCount"] == 0
    assert detail["prompt"]["temperature"] == 0.8

    patched = client.patch(f"{TAVERN}/characters/{card['id']}", json={
        "name": "阿岩·守灯人", "tags": ["雨夜"], "alternateGreetings": ["第一夜。", "第二夜。"]})
    assert patched.status_code == 200, patched.text
    assert patched.json()["card"]["name"] == "阿岩·守灯人"
    assert patched.json()["card"]["alternateGreetings"] == ["第一夜。", "第二夜。"]

    export = client.get(f"{TAVERN}/characters/{card['id']}/export?format=json")
    assert export.status_code == 200
    document = json.loads(export.text)
    assert document["spec"] == "chara_card_v2"
    assert document["data"]["name"] == "阿岩·守灯人"
    assert document["data"]["alternate_greetings"] == ["第一夜。", "第二夜。"]

    assert client.delete(f"{TAVERN}/characters/{card['id']}").status_code == 204
    assert client.get(f"{TAVERN}/characters").json()["items"] == []


def test_chat_greeting_send_sse_and_persist(client, reader, profile):
    card = import_card(client)
    chat = create_chat(client, card["id"])
    detail = client.get(f"{TAVERN}/chats/{chat['id']}").json()
    assert detail["chat"]["card"]["id"] == card["id"]
    assert [(row["role"], row["text"]) for row in detail["messages"]] == [("assistant", "（阿岩抬头）你来了。")]

    events = read_sse(client, "POST", f"{TAVERN}/chats/{chat['id']}/messages",
                      json={"message": "我回来了。"})
    assert events[0]["type"] == "message"
    assert events[0]["userMessage"]["text"] == "我回来了。"
    assert events[-1]["type"] == "done"
    streamed = deltas(events)
    assert len(streamed) >= 12
    message = events[-1]["message"]
    assert message["role"] == "assistant"
    assert message["swipes"][0]["content"] == streamed
    assert message["activeSwipe"] == 0

    detail = client.get(f"{TAVERN}/chats/{chat['id']}").json()
    assert [row["role"] for row in detail["messages"]] == ["assistant", "user", "assistant"]
    assert detail["messages"][-1]["text"] == streamed
    assert detail["messages"][-1]["swipes"][0]["model"] == "fixture-v1"


def test_regenerate_adds_swipe_and_select(client, reader, profile):
    card = import_card(client)
    chat = create_chat(client, card["id"])
    read_sse(client, "POST", f"{TAVERN}/chats/{chat['id']}/messages", json={"message": "说下去。"})

    events = read_sse(client, "POST", f"{TAVERN}/chats/{chat['id']}/regenerate", json={})
    assert events[-1]["type"] == "done"
    regenerated = events[-1]["message"]
    assert len(regenerated["swipes"]) == 2
    assert regenerated["activeSwipe"] == 1
    assert deltas(events) == regenerated["swipes"][1]["content"]

    detail = client.get(f"{TAVERN}/chats/{chat['id']}").json()
    assert len(detail["messages"]) == 3  # regenerate did not add a message
    last = detail["messages"][-1]
    assert len(last["swipes"]) == 2 and last["activeSwipe"] == 1

    selected = client.post(f"{TAVERN}/chats/{chat['id']}/messages/{last['id']}/swipes/0/select")
    assert selected.status_code == 200, selected.text
    assert selected.json()["message"]["activeSwipe"] == 0
    assert client.get(f"{TAVERN}/chats/{chat['id']}").json()["messages"][-1]["text"] == last["swipes"][0]["content"]

    bad = client.post(f"{TAVERN}/chats/{chat['id']}/messages/{last['id']}/swipes/9/select")
    assert bad.status_code == 422


def test_edit_user_message_truncates_and_delete(client, reader, profile):
    card = import_card(client)
    chat = create_chat(client, card["id"])
    read_sse(client, "POST", f"{TAVERN}/chats/{chat['id']}/messages", json={"message": "第一次问。"})
    messages = client.get(f"{TAVERN}/chats/{chat['id']}").json()["messages"]
    user_row = next(row for row in messages if row["role"] == "user")

    edited = client.patch(f"{TAVERN}/chats/{chat['id']}/messages/{user_row['id']}", json={"text": "改过的问法。"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["deleted"] == 1
    assert edited.json()["message"]["text"] == "改过的问法。"
    remaining = client.get(f"{TAVERN}/chats/{chat['id']}").json()["messages"]
    assert [row["role"] for row in remaining] == ["assistant", "user"]

    assistant = next(row for row in messages if row["role"] == "assistant")
    not_editable = client.patch(f"{TAVERN}/chats/{chat['id']}/messages/{assistant['id']}", json={"text": "x"})
    assert not_editable.status_code == 422

    assert client.delete(f"{TAVERN}/chats/{chat['id']}/messages/{user_row['id']}").status_code == 204
    assert len(client.get(f"{TAVERN}/chats/{chat['id']}").json()["messages"]) == 1


def test_world_info_crud_and_preview(client, reader):
    card = import_card(client)
    entry = client.post(f"{TAVERN}/characters/{card['id']}/world-info", json={
        "keys": ["雨夜", "灯塔"], "content": "码头尽头有一座常年不灭的灯塔。",
        "position": "before_char", "order": 10})
    assert entry.status_code == 201, entry.text
    entry_id = entry.json()["item"]["id"]
    items = client.get(f"{TAVERN}/characters/{card['id']}/world-info").json()["items"]
    assert [item["id"] for item in items] == [entry_id]

    preview = client.post(f"{TAVERN}/characters/{card['id']}/world-info/preview", json={"message": "灯塔还亮着吗？"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["matched"][0]["id"] == entry_id
    assert "灯塔" in preview.json()["injection"]

    missed = client.post(f"{TAVERN}/characters/{card['id']}/world-info/preview", json={"message": "今天天气不错。"})
    assert missed.json()["matched"] == []

    disabled = client.patch(f"{TAVERN}/characters/{card['id']}/world-info/{entry_id}", json={"enabled": False})
    assert disabled.json()["item"]["enabled"] is False
    again = client.post(f"{TAVERN}/characters/{card['id']}/world-info/preview", json={"message": "灯塔还亮着吗？"})
    assert again.json()["matched"] == []

    assert client.delete(f"{TAVERN}/characters/{card['id']}/world-info/{entry_id}").status_code == 204
    assert client.get(f"{TAVERN}/characters/{card['id']}/world-info").json()["items"] == []


def test_prompt_settings_roundtrip(client, reader):
    card = import_card(client)
    default = client.get(f"{TAVERN}/characters/{card['id']}/prompt").json()["prompt"]
    assert default["mainPrompt"] == "" and default["temperature"] == 0.8

    patched = client.patch(f"{TAVERN}/characters/{card['id']}/prompt", json={
        "systemPrompt": "保持阿岩的口吻。", "mainPrompt": "简短回复。",
        "temperature": 0.5, "topP": 0.9, "maxTokens": 256, "stop": ["\n\n"]})
    assert patched.status_code == 200, patched.text
    prompt = patched.json()["prompt"]
    assert prompt["systemPrompt"] == "保持阿岩的口吻。"
    assert prompt["mainPrompt"] == "简短回复。"
    assert prompt["temperature"] == 0.5 and prompt["maxTokens"] == 256
    assert prompt["stop"] == ["\n\n"]
    assert client.get(f"{TAVERN}/characters/{card['id']}/prompt").json()["prompt"] == prompt


def test_prompt_assembly_and_injection_order(client, reader, profile, monkeypatch):
    card = import_card(client)
    client.patch(f"{TAVERN}/characters/{card['id']}", json={"systemPrompt": "你是{{char}}，对{{user}}保持戒心。", "postHistoryInstructions": "保持简短。"})
    client.post(f"{TAVERN}/characters/{card['id']}/world-info", json={
        "keys": ["雨夜"], "content": "雨夜时码头不开灯。", "position": "before_char", "order": 1})
    client.post(f"{TAVERN}/characters/{card['id']}/world-info", json={
        "keys": ["灯塔"], "content": "灯塔的守灯人叫阿岩。", "position": "after_char", "order": 2})
    chat = create_chat(client, card["id"])

    seen = {}

    class Capture(MockAdapter):
        def generate_stream(self, profile, system_prompt, data, output_limit, **kwargs):
            seen["system"] = system_prompt
            seen["data"] = data
            seen["limit"] = output_limit
            return super().generate_stream(profile, system_prompt, data, output_limit, **kwargs)

    monkeypatch.setattr("app.tavern.stream.adapter_for", lambda mode=None: Capture())
    events = read_sse(client, "POST", f"{TAVERN}/chats/{chat['id']}/messages",
                      json={"message": "雨夜里灯塔还亮着吗？"})
    assert events[-1]["type"] == "done"

    system = seen["system"]
    assert system.splitlines()[0] == "tavern-v1"
    assert "{{char}}" not in system and "{{user}}" not in system
    assert "[角色设定]" in system and "阿岩" in system
    order = [system.index(marker) for marker in (
        "[系统提示词]", "[主提示词]", "[世界书]", "[角色设定]", "[对话历史]", "[后置指令]", "[本轮要求]")]
    assert order == sorted(order)
    assert system.count("[世界书]") == 2
    assert seen["data"]["character"]["name"] == "阿岩"
    assert seen["data"]["chatId"] == chat["id"]
    assert seen["limit"] == 2048


def test_requires_auth():
    from fastapi.testclient import TestClient

    from app.main import app

    anonymous = TestClient(app, raise_server_exceptions=False)
    assert anonymous.get(f"{TAVERN}/characters").status_code == 401
