"""PATCH /tavern/chats/{id} with cardIds: group member management."""
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


def patch_members(client, chat_id, card_ids, **extra):
    return client.patch(f"{TAVERN}/chats/{chat_id}", json={"cardIds": card_ids, **extra})


def test_add_member_updates_snapshot_and_returns_detail_shape(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）你来了。")
    second = import_card(client, "小满", "（小满）灯亮着。")
    third = import_card(client, "老周", "（老周）账本呢？")
    chat = create_group(client, [first["id"], second["id"]])

    response = patch_members(client, chat["id"], [first["id"], second["id"], third["id"]],
                             title="三人夜谈")
    assert response.status_code == 200, response.text
    updated = response.json()["chat"]
    assert updated["title"] == "三人夜谈"
    assert updated["mode"] == "group"
    assert [member["name"] for member in updated["members"]] == ["阿岩", "小满", "老周"]
    assert [member["order"] for member in updated["members"]] == [0, 1, 2]
    assert all(member["deleted"] is False for member in updated["members"])
    assert updated["aliveMemberCount"] == 3
    assert [card["id"] for card in updated["cards"]] == [first["id"], second["id"], third["id"]]

    detail = client.get(f"{TAVERN}/chats/{chat['id']}").json()["chat"]
    assert detail["members"] == updated["members"]
    assert detail["aliveMemberCount"] == 3
    assert set(detail) == set(updated)


def test_remove_member_keeps_messages(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）到了。")
    second = import_card(client, "小满", "（小满）嗯。")
    third = import_card(client, "老周", "（老周）来了。")
    chat = create_group(client, [first["id"], second["id"], third["id"]])
    read_sse(client, f"{TAVERN}/chats/{chat['id']}/messages", {"message": "先坐。"})
    before = client.get(f"{TAVERN}/chats/{chat['id']}").json()

    response = patch_members(client, chat["id"], [first["id"], third["id"]])
    assert response.status_code == 200, response.text
    chat_view = response.json()["chat"]
    assert [member["cardId"] for member in chat_view["members"]] == [first["id"], third["id"]]
    assert [member["order"] for member in chat_view["members"]] == [0, 1]
    assert chat_view["aliveMemberCount"] == 2

    after = client.get(f"{TAVERN}/chats/{chat['id']}").json()
    assert [row["id"] for row in after["messages"]] == [row["id"] for row in before["messages"]]


def test_member_count_validation(client, reader, profile):
    first = import_card(client, "阿岩", "一。")
    second = import_card(client, "小满", "二。")
    third = import_card(client, "老周", "三。")
    fourth = import_card(client, "阿九", "四。")
    fifth = import_card(client, "阿七", "五。")
    sixth = import_card(client, "阿六", "六。")
    chat = create_group(client, [first["id"], second["id"]])

    one = patch_members(client, chat["id"], [first["id"]])
    assert one.status_code == 422, one.text
    assert one.json()["code"] == "GROUP_MEMBERS_INVALID"
    assert one.json()["message"] == "群聊需要 2 至 5 位角色"

    too_many = patch_members(client, chat["id"], [first["id"], second["id"], third["id"],
                                                  fourth["id"], fifth["id"], sixth["id"]])
    assert too_many.status_code == 422, too_many.text
    assert too_many.json()["code"] == "GROUP_MEMBERS_INVALID"

    deduped = patch_members(client, chat["id"], [first["id"], first["id"], second["id"]])
    assert deduped.status_code == 200, deduped.text
    assert [member["cardId"] for member in deduped.json()["chat"]["members"]] == [
        first["id"], second["id"]]


def test_deleted_and_foreign_cards_are_rejected(client, reader, profile):
    first = import_card(client, "阿岩", "一。")
    second = import_card(client, "小满", "二。")
    doomed = import_card(client, "阿亡", "三。")
    chat = create_group(client, [first["id"], second["id"]])
    assert client.delete(f"{TAVERN}/characters/{doomed['id']}").status_code == 204

    deleted = patch_members(client, chat["id"], [first["id"], doomed["id"]])
    assert deleted.status_code == 404, deleted.text
    assert deleted.json()["code"] == "RESOURCE_NOT_FOUND"

    assert client.post(f"{BASE}/auth/register", json={
        "username": "reader_two", "password": "reader-two-password-123"}).status_code == 201
    login = client.post(f"{BASE}/auth/login", json={
        "username": "reader_two", "password": "reader-two-password-123",
        "clientType": "android", "deviceName": "other-device"})
    assert login.status_code == 200, login.text
    client.headers["Authorization"] = "Bearer " + login.json()["accessToken"]
    foreign = import_card(client, "外来卡", "四。")

    client.headers["Authorization"] = "Bearer " + reader["accessToken"]
    foreign_response = patch_members(client, chat["id"], [first["id"], foreign["id"]])
    assert foreign_response.status_code == 404, foreign_response.text


def test_single_chat_rejects_card_ids(client, reader, profile):
    first = import_card(client, "阿岩", "一。")
    second = import_card(client, "小满", "二。")
    response = client.post(f"{TAVERN}/characters/{first['id']}/chats", json={})
    chat = response.json()["chat"]
    assert chat["mode"] == "single"

    patch = patch_members(client, chat["id"], [first["id"], second["id"]])
    assert patch.status_code == 422, patch.text
    assert patch.json()["code"] == "CHAT_MODE_INVALID"


def test_title_only_patch_keeps_members_and_other_fields(client, reader, profile):
    first = import_card(client, "阿岩", "一。")
    second = import_card(client, "小满", "二。")
    chat = create_group(client, [first["id"], second["id"]])

    response = client.patch(f"{TAVERN}/chats/{chat['id']}", json={"title": "只改标题"})
    assert response.status_code == 200, response.text
    view = response.json()["chat"]
    assert view["title"] == "只改标题"
    assert [member["cardId"] for member in view["members"]] == [first["id"], second["id"]]
    assert view["aliveMemberCount"] == 2
    assert view["sampling"] == {}

    empty = client.patch(f"{TAVERN}/chats/{chat['id']}", json={})
    assert empty.status_code == 422, empty.text
    assert empty.json()["code"] == "INVALID_REQUEST"


def test_rotation_continues_from_the_new_member_list(client, reader, profile):
    first = import_card(client, "阿岩", "（阿岩）我在。")
    second = import_card(client, "小满", "（小满）我也在。")
    third = import_card(client, "老周", "（老周）还有我。")
    chat = create_group(client, [first["id"], second["id"]])
    url = f"{TAVERN}/chats/{chat['id']}/messages"

    events = read_sse(client, url, {"message": "第一句。"})
    assert events[0]["speaker"]["cardId"] == first["id"]

    # assistant_count=1 -> alive_members[1] of the new list [second, third] is third
    assert patch_members(client, chat["id"], [second["id"], third["id"]]).status_code == 200
    events = read_sse(client, url, {"message": "第二句。"})
    assert events[0]["speaker"]["cardId"] == third["id"]
    assert events[0]["speaker"]["name"] == "老周"
    assert events[-1]["message"]["speakerCardId"] == third["id"]

    # assistant_count=2 -> index 0 of [third, second]
    assert patch_members(client, chat["id"], [third["id"], second["id"]]).status_code == 200
    events = read_sse(client, url, {"message": "第三句。"})
    assert events[0]["speaker"]["cardId"] == third["id"]
