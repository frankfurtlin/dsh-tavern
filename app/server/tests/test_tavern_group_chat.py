"""Group chat acceptance: room creation, send/idempotency, rotation, mock
generation with regex substitution, and deletion. Generation runs through the
real ``card_group_chat`` worker path with the deterministic mock adapter."""
import json

from fastapi.testclient import TestClient

from app.main import app
from app.providers import MockAdapter
from app.worker import process

API = "/api/reader/v1"


def import_card(client, name, key=None):
    payload = {"spec": "chara_card_v2", "data": {
        "name": name, "description": f"{name}的描述", "personality": "安静、谨慎",
        "scenario": "小镇酒馆的常客", "first_mes": "你好"}}
    response = client.post(API + "/character-cards/imports",
                           files={"file": (f"{name}.json", json.dumps(payload, ensure_ascii=False).encode(),
                                           "application/json")})
    assert response.status_code == 201, response.text
    return response.json()["card"]


def other_user(client, name="other_one"):
    def register():
        assert client.post(API + "/auth/register", json={"username": name,
            "password": "reader-password-123"}).status_code == 201
        login = client.post(API + "/auth/login", json={"username": name,
            "password": "reader-password-123", "clientType": "android", "deviceName": "other"})
        assert login.status_code == 200, login.text
        return "Bearer " + login.json()["accessToken"]
    original = client.headers.get("Authorization")
    client.headers["Authorization"] = register()
    return original


def make_chat(client, card_ids, title=None, key="chat-create"):
    body = {"cardIds": card_ids}
    if title is not None:
        body["title"] = title
    response = client.post(API + "/card-group-chats", json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    return response.json()["chat"]


def send(client, chat_id, message, turn, key):
    return client.post(API + f"/card-group-chats/{chat_id}/messages",
                       json={"message": message, "turnId": turn}, headers={"Idempotency-Key": key})


def detail(client, chat_id):
    response = client.get(API + f"/card-group-chats/{chat_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_create_validates_card_selection(client, reader):
    a, b, c = import_card(client, "阿岩"), import_card(client, "白露"), import_card(client, "陈渡")
    too_few = client.post(API + "/card-group-chats", json={"cardIds": [a["id"]]}, headers={"Idempotency-Key": "few"})
    assert too_few.status_code == 422 and too_few.json()["code"] == "CARD_SELECTION_INVALID"
    duplicate = client.post(API + "/card-group-chats", json={"cardIds": [a["id"], a["id"]]},
                            headers={"Idempotency-Key": "dup"})
    assert duplicate.status_code == 422 and duplicate.json()["code"] == "CARD_SELECTION_INVALID"
    missing = client.post(API + "/card-group-chats",
                          json={"cardIds": [a["id"], "0" * 32]}, headers={"Idempotency-Key": "missing"})
    assert missing.status_code == 404
    original = other_user(client)
    try:
        foreign = import_card(client, "外人")
    finally:
        if original:
            client.headers["Authorization"] = original
    stolen = client.post(API + "/card-group-chats", json={"cardIds": [a["id"], foreign["id"]]},
                         headers={"Idempotency-Key": "stolen"})
    assert stolen.status_code == 404
    chat = make_chat(client, [a["id"], b["id"]], title="  夜间酒馆  ")
    assert chat["title"] == "夜间酒馆"
    assert [card["name"] for card in chat["cards"]] == ["阿岩", "白露"]
    default_title = make_chat(client, [a["id"], b["id"], c["id"]], key="chat-create-2")
    assert default_title["title"] == "阿岩、白露、陈渡 的群聊"


def test_list_and_detail(client, reader):
    a, b = import_card(client, "阿岩"), import_card(client, "白露")
    chat = make_chat(client, [a["id"], b["id"]])
    items = client.get(API + "/card-group-chats").json()["items"]
    assert [item["id"] for item in items] == [chat["id"]]
    assert items[0]["lastText"] == ""
    page = detail(client, chat["id"])
    assert page["chat"]["id"] == chat["id"]
    assert [card["name"] for card in page["cards"]] == ["阿岩", "白露"]
    assert page["messages"] == []


def test_send_turn_identity_and_pending(client, reader, profile):
    a, b = import_card(client, "阿岩"), import_card(client, "白露")
    chat = make_chat(client, [a["id"], b["id"]])
    first = send(client, chat["id"], "今天谁在守夜？", "turn-1", "msg-1")
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["chatId"] == chat["id"] and body["speakerCardId"] == a["id"]
    assert body["speakerName"] == "阿岩" and body["taskId"]
    replay = send(client, chat["id"], "今天谁在守夜？", "turn-1", "msg-1")
    assert replay.status_code == 202 and replay.json()["taskId"] == body["taskId"]
    same_turn = send(client, chat["id"], "今天谁在守夜？", "turn-1", "msg-other-key")
    assert same_turn.status_code == 202 and same_turn.json()["taskId"] == body["taskId"]
    pending = send(client, chat["id"], "换个问法", "turn-2", "msg-2")
    assert pending.status_code == 409 and pending.json()["code"] == "CHAT_PENDING"
    conflict = send(client, chat["id"], "内容变了", "turn-1", "msg-3")
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    messages = detail(client, chat["id"])["messages"]
    assert [(row["role"], row["status"]) for row in messages] == [("user", "saved"), ("assistant", "pending")]


def test_mock_generation_rotation_and_history(client, reader, profile):
    a, b = import_card(client, "阿岩"), import_card(client, "白露")
    chat = make_chat(client, [a["id"], b["id"]])
    first = send(client, chat["id"], "今晚喝什么？", "turn-1", "msg-1").json()
    process(first["taskId"])
    page = detail(client, chat["id"])
    assert [row["role"] for row in page["messages"]] == ["user", "assistant"]
    reply = page["messages"][1]
    assert reply["status"] == "saved" and reply["speakerCardId"] == a["id"]
    assert reply["speakerName"] == "阿岩" and "阿岩" in reply["text"]
    second = send(client, chat["id"], "白露你呢？", "turn-2", "msg-2").json()
    assert second["speakerCardId"] == b["id"] and second["speakerName"] == "白露"
    process(second["taskId"])
    page = detail(client, chat["id"])
    assert [row["status"] for row in page["messages"]] == ["saved"] * 4
    assert [row["speakerName"] for row in page["messages"]] == ["", "阿岩", "", "白露"]
    assert page["messages"][-1]["speakerName"] == "白露"
    assert page["chat"]["lastText"] == page["messages"][-1]["text"]


def test_generation_prompt_contract(client, reader, profile, monkeypatch):
    calls = []

    class Capture(MockAdapter):
        def generate(self, profile, system_prompt, data, output_limit, **kwargs):
            calls.append({"system": system_prompt, "data": data, "limit": output_limit})
            return {"text": "捕获到的群聊回复", "finishReason": "stop",
                    "usage": {"state": "known", "inputTokens": 10, "outputTokens": 5}, "latencyMs": 1}

    monkeypatch.setattr("app.worker.adapter_for", lambda mode=None: Capture())
    a, b = import_card(client, "阿岩"), import_card(client, "白露")
    chat = make_chat(client, [a["id"], b["id"]])
    sent = send(client, chat["id"], "先说说各自的打算。", "turn-1", "msg-1").json()
    process(sent["taskId"])
    assert len(calls) == 1
    call = calls[0]
    assert call["system"].splitlines()[0] == "card_group_chat-v1"
    assert call["data"]["speaker"] == {"id": a["id"], "name": "阿岩"}
    assert [card["name"] for card in call["data"]["cards"]] == ["阿岩", "白露"]
    assert call["data"]["message"] == "先说说各自的打算。"
    assert call["data"]["conversation"] == []
    assert call["limit"] == 2048
    reply = detail(client, chat["id"])["messages"][1]
    assert reply["text"] == "捕获到的群聊回复" and reply["status"] == "saved"


def test_regex_applied_to_saved_reply(client, reader, profile):
    script = client.post(API + "/regex-scripts", json={"name": "统一替换",
        "pattern": "(?s)^.*$", "replacement": "群聊正则替换后的回复"})
    assert script.status_code == 201, script.text
    a, b = import_card(client, "阿岩"), import_card(client, "白露")
    chat = make_chat(client, [a["id"], b["id"]])
    sent = send(client, chat["id"], "说点什么", "turn-1", "msg-1").json()
    process(sent["taskId"])
    reply = detail(client, chat["id"])["messages"][1]
    assert reply["text"] == "群聊正则替换后的回复"


def test_failure_marks_pending_reply_failed(client, reader, profile, monkeypatch):
    from app.errors import Problem

    class Failing(MockAdapter):
        def generate(self, profile, system_prompt, data, output_limit, **kwargs):
            raise Problem("PROVIDER_UNAVAILABLE", "DSH 模型网关不可用，未收到回复", 502)

    monkeypatch.setattr("app.worker.adapter_for", lambda mode=None: Failing())
    a, b = import_card(client, "阿岩"), import_card(client, "白露")
    chat = make_chat(client, [a["id"], b["id"]])
    sent = send(client, chat["id"], "会失败的请求", "turn-1", "msg-1").json()
    process(sent["taskId"])
    page = detail(client, chat["id"])
    assert page["messages"][1]["status"] == "failed"
    task = client.get(API + f"/tasks/{sent['taskId']}").json()
    assert task["errorCode"] == "PROVIDER_UNAVAILABLE"
    retried = send(client, chat["id"], "重试一次", "turn-2", "msg-2")
    assert retried.status_code == 202 and retried.json()["speakerCardId"] == a["id"]


def test_delete_and_ownership(client, reader):
    a, b = import_card(client, "阿岩"), import_card(client, "白露")
    chat = make_chat(client, [a["id"], b["id"]])
    original = other_user(client)
    try:
        assert client.get(API + f"/card-group-chats/{chat['id']}").status_code == 404
        assert client.delete(API + f"/card-group-chats/{chat['id']}").status_code == 404
    finally:
        if original:
            client.headers["Authorization"] = original
    assert client.delete(API + f"/card-group-chats/{chat['id']}").status_code == 204
    assert client.get(API + f"/card-group-chats/{chat['id']}").status_code == 404
    assert client.get(API + "/card-group-chats").json()["items"] == []


def test_requires_auth():
    with TestClient(app) as fresh:
        assert fresh.get(API + "/card-group-chats").status_code == 401
