"""P0-B quote replies: persistence, SSE/GET echo, prompt block and edge cases."""
import json

from app.db import SessionLocal
from app.models import now, uid
from app.tavern import api as tavern_api
from app.tavern import prompt as prompt_builder
from app.tavern import service
from app.tavern.models import TavernMessage

BASE = "/api/reader/v1"
TAVERN = f"{BASE}/tavern"
QUOTE = "上一句：雨好像停了。"


def card_payload(name="阿岩", first_message="（阿岩抬头）你来了。"):
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": name,
            "description": "常年在雨夜码头守灯的人。",
            "personality": "寡言，但记性很好。",
            "scenario": "雨夜的旧码头。",
            "first_mes": first_message,
            "mes_example": "",
            "alternate_greetings": [],
            "tags": ["雨夜"],
            "system_prompt": "",
            "post_history_instructions": "",
        },
    }


def import_card(client, name="阿岩", first_message="（阿岩抬头）你来了。"):
    response = client.post(f"{TAVERN}/characters/imports", files={
        "file": ("card.json", json.dumps(card_payload(name, first_message)), "application/json")})
    assert response.status_code == 201, response.text
    return response.json()["card"]


def create_chat(client, card_id):
    response = client.post(f"{TAVERN}/characters/{card_id}/chats", json={})
    assert response.status_code == 201, response.text
    return response.json()["chat"]


def read_sse(client, url, body):
    events = []
    with client.stream("POST", url, json=body) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def send_quote(client, chat_id, text="我回来了。", quote=QUOTE):
    return read_sse(client, f"{TAVERN}/chats/{chat_id}/messages",
                    {"message": text, "quote": {"text": quote}})


def chat_messages(client, chat_id):
    detail = client.get(f"{TAVERN}/chats/{chat_id}")
    assert detail.status_code == 200, detail.text
    return detail.json()["messages"]


def user_messages(items):
    return [item for item in items if item["role"] == "user"]


def assistant_messages(items):
    return [item for item in items if item["role"] == "assistant"]


def test_quote_saved_and_echoed(client, reader, profile):
    chat = create_chat(client, import_card(client)["id"])
    events = send_quote(client, chat["id"])

    initial = events[0]
    assert initial["type"] == "message"
    assert initial["userMessage"]["quotedText"] == QUOTE
    assert initial["userMessage"]["text"] == "我回来了。"

    items = chat_messages(client, chat["id"])
    user = user_messages(items)[-1]
    assert user["quotedText"] == QUOTE
    assert all(item["quotedText"] is None for item in assistant_messages(items))


def test_quote_validation(client, reader, profile):
    chat = create_chat(client, import_card(client)["id"])
    url = f"{TAVERN}/chats/{chat['id']}/messages"

    blank = client.post(url, json={"message": "在吗", "quote": {"text": ""}})
    assert blank.status_code == 422 and blank.json()["code"] == "QUOTE_INVALID"
    spaces = client.post(url, json={"message": "在吗", "quote": {"text": "   "}})
    assert spaces.status_code == 422 and spaces.json()["code"] == "QUOTE_INVALID"
    too_long = client.post(url, json={"message": "在吗", "quote": {"text": "雨" * 2001}})
    assert too_long.status_code == 422 and too_long.json()["code"] == "QUOTE_INVALID"

    # Exactly 2000 chars is accepted (and the SSE reply still finishes).
    events = send_quote(client, chat["id"], quote="雨" * 2000)
    assert events[0]["userMessage"]["quotedText"] == "雨" * 2000
    assert any(event["type"] == "done" for event in events)


def test_quote_prompt_block_and_truncation(client, reader, profile):
    # The current user turn travels to the gateway as data["message"].
    long_quote = "旧" * 800
    chat = create_chat(client, import_card(client)["id"])
    captured = {}
    original = tavern_api._reply_context

    def capture(db, owner, chat, card, history, message, persona_id, speaker=None):
        captured["message"] = message
        return original(db, owner, chat, card, history, message, persona_id, speaker)

    tavern_api._reply_context = capture
    try:
        send_quote(client, chat["id"], text="你看这句。", quote=long_quote)
    finally:
        tavern_api._reply_context = original

    assert captured["message"] == "[引用] " + ("旧" * 500) + "\n你看这句。"

    # Previous user turns are rendered by history_lines with the same block.
    lines = prompt_builder.history_lines(None, [
        {"role": "assistant", "text": "嗯。"},
        {"role": "user", "text": "刚才那句。", "quotedText": "旧消息内容"},
        {"role": "user", "text": "没有引用。"},
    ], "阿岩", "User", 10000)
    assert "User: [引用] 旧消息内容\n刚才那句。" in lines
    assert "User: 没有引用。" in lines


def test_regenerate_keeps_quote(client, reader, profile):
    chat = create_chat(client, import_card(client)["id"])
    send_quote(client, chat["id"])

    history_seen = {}
    original = tavern_api._reply_context

    def capture(db, owner, chat, card, history, message, persona_id, speaker=None):
        history_seen["items"] = history
        return original(db, owner, chat, card, history, message, persona_id, speaker)

    tavern_api._reply_context = capture
    try:
        events = read_sse(client, f"{TAVERN}/chats/{chat['id']}/regenerate", {})
    finally:
        tavern_api._reply_context = original

    assert any(event["type"] == "done" for event in events)
    quoted_items = [item for item in history_seen["items"]
                    if item.role == "user" and item.quoted_text]
    assert quoted_items and quoted_items[0].quoted_text == QUOTE

    items = chat_messages(client, chat["id"])
    user = user_messages(items)[-1]
    assert user["quotedText"] == QUOTE  # regenerate must not touch the user row
    assert all(item["quotedText"] is None for item in assistant_messages(items))


def test_group_chat_quote(client, reader, profile):
    first = import_card(client, "甲", "甲来了。")
    second = import_card(client, "乙", "乙来了。")
    response = client.post(f"{TAVERN}/chats", json={"cardIds": [first["id"], second["id"]]})
    assert response.status_code == 201, response.text
    chat = response.json()["chat"]

    events = send_quote(client, chat["id"])
    assert events[0]["userMessage"]["quotedText"] == QUOTE
    assert events[0]["speaker"]["cardId"] in {first["id"], second["id"]}

    items = chat_messages(client, chat["id"])
    user = user_messages(items)[-1]
    assert user["quotedText"] == QUOTE


def test_legacy_messages_have_null_quote(client, reader, profile):
    chat = create_chat(client, import_card(client)["id"])
    with SessionLocal() as db:
        chat_row = service.chat_or_404(db, chat["ownerId"], chat["id"]) if "ownerId" in chat else None
        if chat_row is None:
            chat_row = db.get(service.TavernChat, chat["id"])
        db.add(TavernMessage(id=uid(), owner_id=chat_row.owner_id, chat_id=chat_row.id,
                             role="user", sequence=service.next_sequence(db, chat_row),
                             swipes=[{"content": "旧版本留下的消息", "model": "", "createdAt": None}],
                             active_swipe=0, updated_at=now()))
        db.commit()

    items = chat_messages(client, chat["id"])
    legacy = [item for item in items if item["text"] == "旧版本留下的消息"][0]
    assert legacy["quotedText"] is None
