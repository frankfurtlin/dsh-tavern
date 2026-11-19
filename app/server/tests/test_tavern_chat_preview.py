"""A1: chat list ``lastMessage`` is a plain-text preview, never a message object."""
import json

from app.db import SessionLocal
from app.models import now, uid
from app.tavern import service
from app.tavern.models import TavernMessage

BASE = "/api/reader/v1"
TAVERN = f"{BASE}/tavern"


def _card_payload(name="阿岩"):
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": name,
            "description": "守灯的人。",
            "personality": "寡言。",
            "scenario": "旧码头。",
            "first_mes": "（抬头）你来了。",
            "mes_example": "",
            "alternate_greetings": [],
            "tags": [],
            "system_prompt": "",
            "post_history_instructions": "",
        },
    }


def _import_card(client):
    response = client.post(f"{TAVERN}/characters/imports", files={
        "file": ("card.json", json.dumps(_card_payload()), "application/json")})
    assert response.status_code == 201, response.text
    return response.json()["card"]


def _create_chat(client, card_id, title="雨夜"):
    response = client.post(f"{TAVERN}/characters/{card_id}/chats", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["chat"]


def _seed(owner, chat_id, contents, active=0):
    with SessionLocal() as db:
        chat = db.get(service.TavernChat, chat_id)
        db.add(TavernMessage(
            id=uid(), owner_id=owner, chat_id=chat_id, role="assistant",
            sequence=service.next_sequence(db, chat),
            swipes=[{"content": text, "model": "", "createdAt": None} for text in contents],
            active_swipe=active, updated_at=now()))
        db.commit()


def _item(client, chat_id):
    response = client.get(f"{TAVERN}/chats")
    assert response.status_code == 200, response.text
    return next(item for item in response.json()["items"] if item["id"] == chat_id)


def test_last_message_is_plain_text(client, reader):
    owner = reader["user"]["id"]
    chat = _create_chat(client, _import_card(client)["id"])
    _seed(owner, chat["id"], ["雨停了。\n\n你带伞了吗？"])

    item = _item(client, chat["id"])
    assert item["lastMessage"] == "雨停了。 你带伞了吗？"
    assert "{" not in item["lastMessage"] and '"id"' not in item["lastMessage"]
    assert item["lastMessageAt"] is not None
    assert item["messageCount"] == 2  # greeting + seeded message


def test_last_message_uses_active_swipe_and_truncates_80(client, reader):
    owner = reader["user"]["id"]
    chat = _create_chat(client, _import_card(client)["id"])
    _seed(owner, chat["id"], ["旧版本不该出现", "雨" * 100], active=1)

    item = _item(client, chat["id"])
    assert item["lastMessage"] == "雨" * 80
    assert len(item["lastMessage"]) == 80
    assert "旧版本" not in item["lastMessage"]


def test_last_message_folds_whitespace_and_empty_is_null(client, reader):
    owner = reader["user"]["id"]
    card = _import_card(client)
    blank = _create_chat(client, card["id"], "空消息")
    _seed(owner, blank["id"], ["   \n\t  "])

    assert _item(client, blank["id"])["lastMessage"] is None

    spaced = _create_chat(client, card["id"], "折叠空白")
    _seed(owner, spaced["id"], ["  第一行  \n\n 第二行   "])
    assert _item(client, spaced["id"])["lastMessage"] == "第一行 第二行"


def test_last_message_absent_when_chat_has_only_greeting(client, reader):
    # A fresh chat has the greeting from the card; the preview must still be a
    # string (never the raw message object).
    chat = _create_chat(client, _import_card(client)["id"])
    item = _item(client, chat["id"])
    assert isinstance(item["lastMessage"], str)
    assert item["lastMessage"] == "（抬头）你来了。"
