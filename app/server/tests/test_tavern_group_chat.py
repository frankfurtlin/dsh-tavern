"""P3-e group chats: multi-card chats, speaker rotation, regenerate and export."""
import json

BASE = "/api/reader/v1"
TAVERN = f"{BASE}/tavern"


def card_payload(name, greeting):
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": name,
            "description": f"{name}的设定描述。",
            "personality": "沉着，偶尔毒舌。",
            "scenario": "深夜的旧书店。",
            "first_mes": greeting,
            "mes_example": "",
            "alternate_greetings": [],
            "tags": ["群聊"],
            "system_prompt": "",
            "post_history_instructions": "",
        },
    }


def import_card(client, name, greeting):
    response = client.post(f"{TAVERN}/characters/imports", files={
        "file": ("card.json", json.dumps(card_payload(name, greeting)), "application/json")})
    assert response.status_code == 201, response.text
    return response.json()["card"]


def read_sse(client, url, body):
    events = []
    with client.stream("POST", url, json=body) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def create_group(client, card_ids, **extra):
    response = client.post(f"{TAVERN}/chats", json={"cardIds": card_ids, **extra})
    return response


def test_create_group_chat_and_history_listing(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩放下手里的书）你来了。")
    second = import_card(client, "小满", "小满从柜台后探出头。")

    response = create_group(client, [first["id"], second["id"]])
    assert response.status_code == 201, response.text
    chat = response.json()["chat"]
    assert chat["mode"] == "group"
    assert chat["cardId"] is None
    assert [member["cardId"] for member in chat["members"]] == [first["id"], second["id"]]
    assert [member["order"] for member in chat["members"]] == [0, 1]
    assert [member["name"] for member in chat["members"]] == ["阿岩", "小满"]
    assert "阿岩" in chat["title"] and "小满" in chat["title"]

    detail = client.get(f"{TAVERN}/chats/{chat['id']}").json()
    assert detail["chat"]["mode"] == "group"
    assert [card["id"] for card in detail["chat"]["cards"]] == [first["id"], second["id"]]
    assert detail["messages"] == []

    listing = client.get(f"{TAVERN}/chats").json()
    item = next(entry for entry in listing["items"] if entry["id"] == chat["id"])
    assert item["mode"] == "group"
    assert len(item["members"]) == 2
    assert "lastMessage" in item and "lastMessageAt" in item


def test_group_members_are_validated_and_deduped(client, reader, profile):
    first = import_card(client, "阿岩", "开场一。")
    second = import_card(client, "小满", "开场二。")

    assert create_group(client, [first["id"]]).status_code == 422
    duplicated = create_group(client, [first["id"], first["id"], second["id"]])
    assert duplicated.status_code == 201, duplicated.text
    assert [member["cardId"] for member in duplicated.json()["chat"]["members"]] == [
        first["id"], second["id"]]
    assert create_group(client, [first["id"], first["id"]]).status_code == 422
    assert create_group(client, []).status_code == 422
    assert create_group(client, ["not-a-card", second["id"]]).status_code == 404


def test_group_member_must_belong_to_the_same_owner(client, reader, profile):
    card = import_card(client, "阿岩", "开场。")
    assert client.post(f"{BASE}/auth/register", json={
        "username": "reader_two", "password": "reader-two-password-123"}).status_code == 201
    login = client.post(f"{BASE}/auth/login", json={
        "username": "reader_two", "password": "reader-two-password-123",
        "clientType": "android", "deviceName": "other-device"})
    assert login.status_code == 200, login.text
    client.headers["Authorization"] = "Bearer " + login.json()["accessToken"]

    response = create_group(client, [card["id"], card["id"]])
    # duplicate dedupes to a single member -> validation error before ownership lookup
    assert response.status_code == 422
    response = client.post(f"{TAVERN}/chats", json={"cardIds": [card["id"]]})
    assert response.status_code == 422
    # a second (non-owned) id forces the ownership check
    own = import_card(client, "临时卡", "开场。")
    response = create_group(client, [own["id"], card["id"]])
    assert response.status_code == 404


def test_group_speakers_rotate_across_turns(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）灯还亮着。")
    second = import_card(client, "小满", "（小满）我把门锁了。")
    chat = create_group(client, [first["id"], second["id"]]).json()["chat"]
    url = f"{TAVERN}/chats/{chat['id']}/messages"

    events = read_sse(client, url, {"message": "我先说一句。"})
    assert events[0]["type"] == "message"
    assert events[0]["speaker"]["cardId"] == first["id"]
    assert events[0]["speaker"]["name"] == "阿岩"
    done = events[-1]
    assert done["type"] == "done"
    assert done["message"]["role"] == "assistant"
    assert done["message"]["speakerCardId"] == first["id"]
    assert done["message"]["speakerName"] == "阿岩"

    events = read_sse(client, url, {"message": "换个人说。"})
    assert events[0]["speaker"]["cardId"] == second["id"]
    assert events[-1]["message"]["speakerCardId"] == second["id"]

    messages = client.get(f"{TAVERN}/chats/{chat['id']}").json()["messages"]
    assistant_ids = [row["speakerCardId"] for row in messages if row["role"] == "assistant"]
    assert assistant_ids == [first["id"], second["id"]]
    assert [row["speakerName"] for row in messages if row["role"] == "assistant"] == ["阿岩", "小满"]


def test_regenerate_keeps_the_speaker(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）我听着。")
    second = import_card(client, "小满", "（小满）说吧。")
    chat = create_group(client, [first["id"], second["id"]]).json()["chat"]
    read_sse(client, f"{TAVERN}/chats/{chat['id']}/messages", {"message": "开始。"})

    events = read_sse(client, f"{TAVERN}/chats/{chat['id']}/regenerate", {})
    assert events[0]["type"] == "message"
    assert events[0]["speaker"]["cardId"] == first["id"]
    done = events[-1]
    assert done["message"]["speakerCardId"] == first["id"]
    assert len(done["message"]["swipes"]) == 2
    assert done["message"]["activeSwipe"] == 1


def test_group_export_includes_speaker_name(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）到了。")
    second = import_card(client, "小满", "（小满）嗯。")
    chat = create_group(client, [first["id"], second["id"]]).json()["chat"]
    read_sse(client, f"{TAVERN}/chats/{chat['id']}/messages", {"message": "出门吧。"})

    response = client.get(f"{TAVERN}/chats/{chat['id']}/export", params={"format": "jsonl"})
    assert response.status_code == 200, response.text
    lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assistant_lines = [line for line in lines if not line["is_user"]]
    assert assistant_lines
    assert assistant_lines[-1]["speaker_name"] == "阿岩"
    assert assistant_lines[-1]["character_name"] == "阿岩"


def test_single_chat_regression_has_no_speaker(client, reader, profile):
    card = import_card(client, "阿岩", "（阿岩）你来了。")
    response = client.post(f"{TAVERN}/characters/{card['id']}/chats", json={})
    assert response.status_code == 201, response.text
    chat = response.json()["chat"]
    assert chat["mode"] == "single"
    assert chat["members"] == []

    events = read_sse(client, f"{TAVERN}/chats/{chat['id']}/messages", {"message": "你好。"})
    assert "speaker" not in events[0]
    assert "speakerCardId" not in events[-1]["message"]
    again = client.get(f"{TAVERN}/chats/{chat['id']}").json()
    assert "speakerCardId" not in again["messages"][-1]

    response = client.get(f"{TAVERN}/chats/{chat['id']}/export", params={"format": "jsonl"})
    lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert all("speaker_name" not in line for line in lines)
