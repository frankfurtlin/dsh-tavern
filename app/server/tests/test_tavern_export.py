"""P1-2: character card PNG/JSON export and chat JSONL export."""
import base64
import io
import json

from PIL import Image

from app.world.worker import process

from .test_tavern_character_cards import API, CARDS, send
from .test_tavern_card_space import V2_CARD as BOOK_CARD, import_card, upload

PERSONAS = API + "/tavern-personas"


def card_chat(client, card):
    return client.post(CARDS + "/" + card["id"] + "/chats").json()["sessionId"]


def test_card_export_png_contains_v2_chara(client, reader):
    card = import_card(client, BOOK_CARD)
    response = client.get(CARDS + "/" + card["id"] + "/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    disposition = response.headers["content-disposition"]
    assert "filename*" in disposition and "attachment" in disposition
    image = Image.open(io.BytesIO(response.content))
    image.load()
    raw = image.info.get("chara")
    assert raw
    document = json.loads(base64.b64decode(raw).decode("utf-8"))
    assert document["spec"] == "chara_card_v2" and document["spec_version"] == "2.0"
    data = document["data"]
    assert data["name"] == "艾琳"
    assert data["first_mes"].startswith("你终于来了")
    assert data["tags"] == ["奇幻", "旅店"]
    book = data["character_book"]["entries"]
    assert len(book) == 3 and {entry["name"] for entry in book} >= {"雨夜旅店", "群山"}


def test_card_export_json_roundtrips_import(client, reader):
    card = import_card(client, BOOK_CARD)
    response = client.get(CARDS + "/" + card["id"] + "/export?format=json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    document = response.json()
    assert document["spec"] == "chara_card_v2"
    assert document["data"]["name"] == "艾琳"
    assert document["data"]["character_book"]["entries"]
    reimported = upload(client, json.dumps(document).encode("utf-8"), "exported.json", "application/json")
    assert reimported.status_code == 201, reimported.text
    clone = reimported.json()["card"]
    assert clone["name"] == document["data"]["name"]
    assert clone["chatTarget"]
    assert client.get(CARDS + "/" + card["id"] + "/export?format=bogus").status_code == 422
    assert client.get(CARDS + "/missing/export").status_code == 404


def test_chat_export_jsonl(client, reader, profile):
    card = import_card(client)
    session_id = card_chat(client, card)
    sent = send(client, session_id, "你好，旅店还有房间吗？", "export-turn")
    assert sent.status_code == 202, sent.text
    process(sent.json()["taskId"])
    response = client.get(API + "/play-controls/world-chats/" + session_id + "/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "filename*" in response.headers["content-disposition"]
    lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert [line["role"] for line in lines] == ["user", "assistant"]
    assert lines[0]["text"] == "你好，旅店还有房间吗？"
    assert all(set(line) == {"role", "text", "sequence", "modelName", "createdAt"} for line in lines)
    assert [line["sequence"] for line in lines] == [1, 2]
    json_response = client.get(API + "/play-controls/world-chats/" + session_id + "/export?format=json")
    assert json_response.status_code == 200
    assert len(json_response.json()) == 2
    assert client.get(API + "/play-controls/world-chats/" + session_id + "/export?format=bogus").status_code == 422


def test_export_requires_auth(client, reader):
    card = import_card(client)
    headers = {"Authorization": ""}
    assert client.get(CARDS + "/" + card["id"] + "/export", headers=headers).status_code == 401
    assert client.get(API + "/play-controls/world-chats/missing/export", headers=headers).status_code == 401
