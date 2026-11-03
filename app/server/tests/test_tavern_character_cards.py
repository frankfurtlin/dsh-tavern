"""Character card import + card chat API acceptance tests.

The legacy book character-card regression suite lives in
``test_character_cards.py`` and is intentionally untouched.
"""
import base64
import io
import json
import re
from copy import deepcopy

from PIL import Image, PngImagePlugin
from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.play_controls.models import WorldChatSession
from app.world.models import World, WorldCheckpoint, WorldLine, WorldTurn
from app.world.worker import process

API = "/api/reader/v1"
CARDS = API + "/character-cards"
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
        "mes_example": "<START>\n{{user}}: 这里还有空房吗？\n{{char}}: 只有一间，一直给你留着。",
        "alternate_greetings": ["又见面了，旅人。"],
        "tags": ["奇幻", "旅店"],
        "creator": "tester",
        "character_version": "1.0",
    },
}


def card_png(payload):
    image = Image.new("RGB", (8, 8), (30, 60, 90))
    info = PngImagePlugin.PngInfo()
    info.add_text("chara", base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii"))
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def upload(client, data, name="card.png", content_type="image/png"):
    return client.post(CARDS + "/imports", files={"file": (name, data, content_type)})


def upload_json(client, payload, name="card.json"):
    return upload(client, json.dumps(payload).encode("utf-8"), name, "application/json")


def import_card(client):
    response = upload(client, card_png(V2_CARD))
    assert response.status_code == 201, response.text
    return response.json()["card"]


def send(client, session_id, message, turn_id="message-1"):
    return client.post(API + "/play-controls/world-chats/" + session_id + "/send",
                       json={"message": message, "turnId": turn_id},
                       headers={"Idempotency-Key": turn_id})


def world_checkpoints(world_id):
    with SessionLocal() as db:
        return [(c.id, c.content_hash, c.kind) for c in db.scalars(
            select(WorldCheckpoint).where(WorldCheckpoint.world_id == world_id).order_by(WorldCheckpoint.sequence))]


def test_v2_png_import_creates_active_world_line_and_card_target(client, reader):
    card = import_card(client)
    assert HEX32.match(card["id"])
    assert card["name"] == "艾琳"
    assert card["source"] == "png"
    assert card["hasAvatar"] is True
    assert card["tags"] == ["奇幻", "旅店"]
    assert card["firstMessage"].startswith("你终于来了")
    target = card["chatTarget"]
    assert set(target) == {"worldId", "lineId", "checkpointId", "characterId"}
    assert target["characterId"] == "card-" + card["id"]
    with SessionLocal() as db:
        world = db.get(World, target["worldId"])
        assert world.status == "active" and world.owner_id == reader["user"]["id"]
        assert world.source["kind"] == "character-card"
        line = db.get(WorldLine, target["lineId"])
        assert line.head_checkpoint_id == target["checkpointId"]
        root = db.get(WorldCheckpoint, target["checkpointId"])
        assert root.kind == "start" and root.sequence == 0 and root.parent_checkpoint_id is None
        assert db.scalar(select(WorldTurn).where(WorldTurn.world_id == world.id)) is None
        character = next(e for e in root.definition["entities"] if e["id"] == target["characterId"])
        assert character["name"] == "艾琳" and character["type"] == "character"


def test_v2_json_and_v1_flat_imports_are_supported(client, reader):
    v2 = upload_json(client, V2_CARD)
    assert v2.status_code == 201, v2.text
    assert v2.json()["card"]["hasAvatar"] is False
    assert v2.json()["card"]["source"] == "json"
    flat = {"name": "老船长", "description": "在雾里航行了四十年。", "personality": "沉默",
            "scenario": "一艘旧渡船。", "first_mes": "上船吧。", "mes_example": "{{char}}: 坐稳。"}
    v1 = upload_json(client, flat)
    assert v1.status_code == 201, v1.text
    card = v1.json()["card"]
    assert card["name"] == "老船长" and card["firstMessage"] == "上船吧。"
    assert card["chatTarget"]["characterId"] == "card-" + card["id"]


def test_v3_and_minimal_description_fallbacks(client, reader):
    v3 = {"spec": "chara_card_v3", "data": {"name": "守夜人", "personality": "冷静", "first_mes": "灯还亮着。"}}
    response = upload_json(client, v3)
    assert response.status_code == 201, response.text
    card = response.json()["card"]
    assert card["name"] == "守夜人"
    assert card["description"] == "冷静"
    assert card["firstMessage"] == "灯还亮着。"


def test_invalid_cards_return_structured_422(client, reader):
    for data, name in ((b"not a card", "card.json"),
                       (b'{"spec":"chara_card_v2","data":{}}', "card.json"),
                       (io.BytesIO().getvalue(), "card.png")):
        response = upload(client, data, name)
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["code"] == "CARD_INVALID" and body["message"]
    plain = Image.new("RGB", (4, 4), (1, 2, 3))
    buffer = io.BytesIO()
    plain.save(buffer, "PNG")
    response = upload(client, buffer.getvalue(), "plain.png")
    assert response.status_code == 422
    assert response.json()["code"] == "CARD_INVALID"


def test_import_requires_login(client):
    response = upload(client, card_png(V2_CARD))
    assert response.status_code == 401


def test_avatar_serves_png_and_404s_without_one(client, reader):
    card = import_card(client)
    avatar = client.get(CARDS + "/" + card["id"] + "/avatar")
    assert avatar.status_code == 200 and avatar.headers["content-type"] == "image/png"
    assert avatar.content.startswith(b"\x89PNG")
    json_card = upload_json(client, V2_CARD).json()["card"]
    missing = client.get(CARDS + "/" + json_card["id"] + "/avatar")
    assert missing.status_code == 404


def test_list_search_and_read_single_card(client, reader):
    first = import_card(client)
    upload_json(client, {"name": "另一个人", "description": "无关"})
    listing = client.get(CARDS)
    assert listing.status_code == 200
    assert {item["id"] for item in listing.json()["items"]} >= {first["id"]}
    filtered = client.get(CARDS, params={"q": "艾琳"})
    assert [item["name"] for item in filtered.json()["items"]] == ["艾琳"]
    single = client.get(CARDS + "/" + first["id"])
    assert single.status_code == 200 and single.json()["id"] == first["id"]


def test_card_chat_creates_once_and_reuses_session(client, reader):
    card = import_card(client)
    first = client.post(CARDS + "/" + card["id"] + "/chats")
    assert first.status_code == 201, first.text
    assert first.json()["cardId"] == card["id"]
    assert HEX32.match(first.json()["sessionId"])
    second = client.post(CARDS + "/" + card["id"] + "/chats")
    assert second.status_code == 201
    assert second.json()["sessionId"] == first.json()["sessionId"]
    with SessionLocal() as db:
        sessions = list(db.scalars(select(WorldChatSession).where(
            WorldChatSession.owner_id == reader["user"]["id"], WorldChatSession.character_id == card["chatTarget"]["characterId"])))
        assert len(sessions) == 1 and sessions[0].status == "active"


def test_card_chat_reply_runs_without_writing_world_ledger(client, reader, profile):
    card = import_card(client)
    session_id = client.post(CARDS + "/" + card["id"] + "/chats").json()["sessionId"]
    before = world_checkpoints(card["chatTarget"]["worldId"])
    response = send(client, session_id, "把湿外套给我吧，也说说你的故事。")
    assert response.status_code == 202, response.text
    process(response.json()["taskId"])
    current = client.get(API + "/play-controls/world-chats/" + session_id).json()
    assert [message["role"] for message in current["messages"]] == ["user", "assistant"]
    assert current["messages"][1]["text"]
    assert world_checkpoints(card["chatTarget"]["worldId"]) == before


def test_soft_delete_hides_card_but_keeps_chat(client, reader):
    card = import_card(client)
    session_id = client.post(CARDS + "/" + card["id"] + "/chats").json()["sessionId"]
    deleted = client.delete(CARDS + "/" + card["id"])
    assert deleted.status_code == 204
    assert client.get(CARDS + "/" + card["id"]).status_code == 404
    assert card["id"] not in {item["id"] for item in client.get(CARDS).json()["items"]}
    with SessionLocal() as db:
        row = db.get(World, card["chatTarget"]["worldId"])
        assert row is not None and row.status == "active"
        assert db.get(WorldChatSession, session_id) is not None
    kept = client.get(API + "/play-controls/world-chats/" + session_id)
    assert kept.status_code == 200


def test_other_user_cannot_read_or_delete_card(client, reader):
    card = import_card(client)
    assert client.post(API + "/auth/register", json={"username": "reader_two", "password": "reader-password-456"}).status_code == 201
    other = client.post(API + "/auth/login", json={"username": "reader_two", "password": "reader-password-456",
                                                    "clientType": "android", "deviceName": "other"}).json()
    headers = {"Authorization": "Bearer " + other["accessToken"]}
    assert client.get(CARDS + "/" + card["id"], headers=headers).status_code == 404
    assert client.delete(CARDS + "/" + card["id"], headers=headers).status_code == 404
    assert client.post(CARDS + "/" + card["id"] + "/chats", headers=headers).status_code == 404
    with SessionLocal() as db:
        assert db.get(User, other["user"]["id"]) is not None
