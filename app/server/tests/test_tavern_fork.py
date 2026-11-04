"""P2-1: forking an isolated world chat into an independent branch."""
from app.world.worker import process

from .test_authoring_publications import other
from .test_play_controls_world_chat import create_chat, world


API = "/api/reader/v1/play-controls"


def send(client, session_id, message, turn_id):
    return client.post(API + "/world-chats/" + session_id + "/send",
                       json={"message": message, "turnId": turn_id},
                       headers={"Idempotency-Key": turn_id})


def fork(client, session_id, sequence, key="fork"):
    return client.post(API + "/world-chats/" + session_id + "/fork",
                       json={"sequence": sequence}, headers={"Idempotency-Key": key})


def test_fork_copies_prefix_and_keeps_original(client, reader, profile):
    world_id, line = world(client)
    session = create_chat(client, world_id, line, key="fork-chat")
    first = send(client, session["id"], "你好，今天雾很大。", "turn-1")
    assert first.status_code == 202, first.text
    process(first.json()["taskId"])
    second = send(client, session["id"], "你手里的信是什么？", "turn-2")
    assert second.status_code == 202, second.text
    process(second.json()["taskId"])
    original = client.get(API + "/world-chats/" + session["id"]).json()
    assert [m["role"] for m in original["messages"]] == ["user", "assistant", "user", "assistant"]

    response = fork(client, session["id"], 2)
    assert response.status_code == 201, response.text
    branch = response.json()
    assert branch["sessionId"] != session["id"]
    assert branch["name"] == session["name"] + "（分支）"
    detail = client.get(API + "/world-chats/" + branch["sessionId"]).json()
    assert detail["name"].endswith("（分支）")
    assert detail["characterId"] == session["characterId"]
    assert detail["worldId"] == session["worldId"]
    assert [m["text"] for m in detail["messages"]] == [m["text"] for m in original["messages"][:2]]
    assert [m["turnId"] for m in detail["messages"]] == ["turn-1", "turn-1"]
    assert detail["pendingTaskId"] is None

    unchanged = client.get(API + "/world-chats/" + session["id"]).json()
    assert unchanged["messages"] == original["messages"]

    replay = fork(client, session["id"], 2)
    assert replay.status_code == 201 and replay.json()["sessionId"] == branch["sessionId"]


def test_branch_is_independent_and_can_continue(client, reader, profile):
    world_id, line = world(client)
    session = create_chat(client, world_id, line, key="fork-live")
    first = send(client, session["id"], "先说第一句。", "turn-a")
    process(first.json()["taskId"])
    branch_id = fork(client, session["id"], 2, key="fork-live-branch").json()["sessionId"]
    followed = send(client, branch_id, "在分支里继续。", "turn-branch")
    assert followed.status_code == 202, followed.text
    process(followed.json()["taskId"])
    branch = client.get(API + "/world-chats/" + branch_id).json()
    assert len(branch["messages"]) == 4
    original = client.get(API + "/world-chats/" + session["id"]).json()
    assert len(original["messages"]) == 2
    assert original["messages"][0]["text"] == "先说第一句。"


def test_fork_validation_and_ownership(client, reader, profile):
    world_id, line = world(client)
    session = create_chat(client, world_id, line, key="fork-invalid")
    assert fork(client, session["id"], 0).status_code == 422
    missing = fork(client, session["id"], 99)
    assert missing.status_code == 422 and missing.json()["code"] == "FORK_POINT_INVALID"

    pending = send(client, session["id"], "这条还没生成完。", "turn-pending")
    assert pending.status_code == 202, pending.text
    blocked_pending = fork(client, session["id"], 2)
    assert blocked_pending.status_code == 409 and blocked_pending.json()["code"] == "CHAT_PENDING"
    process(pending.json()["taskId"])

    assert client.post(API + "/world-chats/" + session["id"] + "/fork", json={"sequence": 1},
                       headers={"Authorization": ""}).status_code == 401

    version = client.get(API + "/world-chats/" + session["id"]).json()["version"]
    archived = client.patch(API + "/world-chats/" + session["id"],
                            json={"expectedVersion": version, "status": "archived"},
                            headers={"Idempotency-Key": "archive"})
    assert archived.status_code == 200, archived.text
    blocked = fork(client, session["id"], 1)
    assert blocked.status_code == 409 and blocked.json()["code"] == "CHAT_ARCHIVED"

    other(client, "fork_owner")
    assert client.get(API + "/world-chats/" + session["id"]).status_code == 404
    assert fork(client, session["id"], 1).status_code == 404
