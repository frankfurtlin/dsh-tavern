"""Group chats must survive soft-deleted member cards (no 404, no 500)."""
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


def create_group(client, card_ids):
    response = client.post(f"{TAVERN}/chats", json={"cardIds": card_ids})
    assert response.status_code == 201, response.text
    return response.json()["chat"]


def read_sse(client, url, body):
    events = []
    with client.stream("POST", url, json=body) as response:
        assert response.status_code == 200, response.read()
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def delete_card(client, card_id):
    response = client.delete(f"{TAVERN}/characters/{card_id}")
    assert response.status_code == 204, response.text


def test_detail_flags_deleted_member_and_keeps_history(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）你来了。")
    second = import_card(client, "小满", "（小满）灯亮着。")
    chat = create_group(client, [first["id"], second["id"]])
    read_sse(client, f"{TAVERN}/chats/{chat['id']}/messages", {"message": "晚上好。"})

    delete_card(client, first["id"])

    response = client.get(f"{TAVERN}/chats/{chat['id']}")
    assert response.status_code == 200, response.text
    detail = response.json()
    members = detail["chat"]["members"]
    assert [member["cardId"] for member in members] == [first["id"], second["id"]]
    assert members[0]["deleted"] is True
    assert members[0]["name"] == "阿岩"  # snapshot name survives the deletion
    assert members[1]["deleted"] is False
    assert detail["chat"]["aliveMemberCount"] == 1
    assert [card["id"] for card in detail["chat"]["cards"]] == [second["id"]]
    assert detail["chat"]["card"]["id"] == second["id"]
    assistant = [row for row in detail["messages"] if row["role"] == "assistant"][-1]
    assert assistant["speakerCardId"] == first["id"]
    assert assistant["speakerName"] == "阿岩"


def test_chats_list_survives_deleted_members(client, reader, profile):
    first = import_card(client, "阿岩", "开场一。")
    second = import_card(client, "小满", "开场二。")
    chat = create_group(client, [first["id"], second["id"]])
    read_sse(client, f"{TAVERN}/chats/{chat['id']}/messages", {"message": "先说一句。"})
    delete_card(client, first["id"])

    response = client.get(f"{TAVERN}/chats")
    assert response.status_code == 200, response.text
    item = next(entry for entry in response.json()["items"] if entry["id"] == chat["id"])
    assert item["mode"] == "group"
    assert [member["name"] for member in item["members"]] == ["阿岩", "小满"]
    assert item["lastMessage"]["speakerName"] == "阿岩"


def test_speaker_rotation_skips_deleted_member(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）我在。")
    second = import_card(client, "小满", "（小满）我也在。")
    chat = create_group(client, [first["id"], second["id"]])
    url = f"{TAVERN}/chats/{chat['id']}/messages"
    events = read_sse(client, url, {"message": "第一句。"})
    assert events[0]["speaker"]["cardId"] == first["id"]

    delete_card(client, first["id"])  # only the second member is alive now
    events = read_sse(client, url, {"message": "第二句。"})
    assert events[0]["speaker"]["cardId"] == second["id"]
    assert events[0]["speaker"]["name"] == "小满"
    assert events[-1]["message"]["speakerCardId"] == second["id"]


def test_all_members_deleted_detail_reads_and_exports_ok(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）到了。")
    second = import_card(client, "小满", "（小满）嗯。")
    chat = create_group(client, [first["id"], second["id"]])
    read_sse(client, f"{TAVERN}/chats/{chat['id']}/messages", {"message": "出门吧。"})
    delete_card(client, first["id"])
    delete_card(client, second["id"])

    detail = client.get(f"{TAVERN}/chats/{chat['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["chat"]["aliveMemberCount"] == 0
    assert body["chat"]["cards"] == []
    assert body["chat"]["card"] is None
    assert all(member["deleted"] is True for member in body["chat"]["members"])
    assert [row["speakerName"] for row in body["messages"] if row["role"] == "assistant"] == ["阿岩"]

    assert client.get(f"{TAVERN}/chats").status_code == 200

    jsonl = client.get(f"{TAVERN}/chats/{chat['id']}/export", params={"format": "jsonl"})
    assert jsonl.status_code == 200, jsonl.text
    lines = [json.loads(line) for line in jsonl.text.splitlines() if line.strip()]
    assistant = [line for line in lines if not line["is_user"]][-1]
    assert assistant["speaker_name"] == "阿岩"
    assert assistant["character_name"] == "阿岩"

    document = client.get(f"{TAVERN}/chats/{chat['id']}/export", params={"format": "json"})
    assert document.status_code == 200, document.text
    payload = document.json()
    assert payload["cards"] == []
    assert payload["card"] is None
    assert payload["chat"]["aliveMemberCount"] == 0


def test_send_and_regenerate_report_409_when_all_members_deleted(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）在。")
    second = import_card(client, "小满", "（小满）在。")
    chat = create_group(client, [first["id"], second["id"]])
    read_sse(client, f"{TAVERN}/chats/{chat['id']}/messages", {"message": "开始。"})
    delete_card(client, first["id"])
    delete_card(client, second["id"])

    response = client.post(f"{TAVERN}/chats/{chat['id']}/messages", json={"message": "还能说话吗？"})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "CHAT_NO_MEMBERS"
    assert "无法继续发言" in response.json()["message"]

    response = client.post(f"{TAVERN}/chats/{chat['id']}/regenerate", json={})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "CHAT_NO_MEMBERS"

    # reads stay available after the 409s
    assert client.get(f"{TAVERN}/chats/{chat['id']}").status_code == 200


def test_regenerate_after_speaker_deleted_falls_back_to_alive(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）我听着。")
    second = import_card(client, "小满", "（小满）说吧。")
    chat = create_group(client, [first["id"], second["id"]])
    read_sse(client, f"{TAVERN}/chats/{chat['id']}/messages", {"message": "开始。"})
    delete_card(client, first["id"])  # the first speaker is gone

    events = read_sse(client, f"{TAVERN}/chats/{chat['id']}/regenerate", {})
    assert events[0]["type"] == "message"
    assert events[0]["speaker"]["cardId"] == second["id"]
    done = events[-1]
    assert done["type"] == "done"
    assert done["message"]["speakerCardId"] == second["id"]
    assert done["message"]["speakerName"] == "小满"


def test_export_after_partial_delete_uses_snapshot_names(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）到了。")
    second = import_card(client, "小满", "（小满）嗯。")
    chat = create_group(client, [first["id"], second["id"]])
    read_sse(client, f"{TAVERN}/chats/{chat['id']}/messages", {"message": "出门吧。"})
    delete_card(client, first["id"])

    response = client.get(f"{TAVERN}/chats/{chat['id']}/export", params={"format": "jsonl"})
    assert response.status_code == 200, response.text
    lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assistant = [line for line in lines if not line["is_user"]][-1]
    assert assistant["speaker_name"] == "阿岩"
    assert assistant["character_name"] == "阿岩"

    document = client.get(f"{TAVERN}/chats/{chat['id']}/export", params={"format": "json"})
    payload = document.json()
    assert [card["id"] for card in payload["cards"]] == [second["id"]]
    assert payload["chat"]["aliveMemberCount"] == 1
