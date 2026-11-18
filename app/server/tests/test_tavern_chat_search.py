"""Global chat search: GET /tavern/chats?q= (title + visible message text)."""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import now, uid
from app.tavern.models import TavernChat, TavernMessage

TAVERN = "/api/reader/v1/tavern"


def _import_card(client, name="搜索角色"):
    payload = {"spec": "chara_card_v2", "spec_version": "2.0",
               "data": {"name": name, "description": "", "personality": "",
                        "scenario": "", "first_mes": "你好", "mes_example": "",
                        "alternate_greetings": [], "tags": [], "system_prompt": "",
                        "post_history_instructions": ""}}
    response = client.post(f"{TAVERN}/characters/imports", files={
        "file": ("search.json", json.dumps(payload), "application/json")})
    assert response.status_code == 201, response.text
    return response.json()["card"]


def _create_chat(client, card_id, title=None):
    body = {"cardId": card_id}
    if title:
        body["title"] = title
    response = client.post(f"{TAVERN}/chats", json=body)
    assert response.status_code == 201, response.text
    return response.json()["chat"]


def _seed_message(owner, chat_id, text, when=None):
    """Append one assistant message and bump the chat's lastMessageAt."""
    with SessionLocal() as db:
        highest = db.scalar(select(func.max(TavernMessage.sequence)).where(
            TavernMessage.chat_id == chat_id)) or 0
        db.add(TavernMessage(id=uid(), owner_id=owner, chat_id=chat_id, role="assistant",
                             sequence=highest + 1,
                             swipes=[{"content": text, "model": "", "createdAt": None}],
                             active_swipe=0, updated_at=now()))
        chat = db.get(TavernChat, chat_id)
        chat.last_message_at = when or now()
        db.commit()


def _search(client, **params):
    response = client.get(f"{TAVERN}/chats", params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _titles(items):
    return [item["title"] for item in items]


def test_search_matches_title(client, reader):
    card = _import_card(client)
    _create_chat(client, card["id"], "雨夜灯笼")
    _create_chat(client, card["id"], "晴天散步")
    _create_chat(client, card["id"], "雨夜之后")

    assert _titles(_search(client, q="灯笼")) == ["雨夜灯笼"]
    assert {item["title"] for item in _search(client, q="雨夜")} == {"雨夜灯笼", "雨夜之后"}
    assert _search(client, q="不存在的词") == []
    # surrounding whitespace is trimmed
    assert _titles(_search(client, q="  灯笼  ")) == ["雨夜灯笼"]


def test_search_matches_message_content(client, reader):
    card = _import_card(client)
    owner = reader["user"]["id"]
    lit = _create_chat(client, card["id"], "甲")
    dark = _create_chat(client, card["id"], "乙")
    _seed_message(owner, lit["id"], "我们在雨夜里点亮了一盏灯笼")
    _seed_message(owner, dark["id"], "今晚什么也没发生")

    hits = _search(client, q="点亮")
    assert [item["id"] for item in hits] == [lit["id"]]
    assert hits[0]["lastMessage"]["text"] == "我们在雨夜里点亮了一盏灯笼"
    assert _search(client, q="什么也没")[0]["id"] == dark["id"]


def test_search_is_owner_scoped(client, reader):
    card = _import_card(client)
    owner = reader["user"]["id"]
    mine = _create_chat(client, card["id"], "密室")
    _seed_message(owner, mine["id"], "只有我知道的暗号")

    assert client.post("/api/reader/v1/auth/register",
                       json={"username": "searcher_two", "password": "reader-password-456"}).status_code == 201
    login = client.post("/api/reader/v1/auth/login",
                        json={"username": "searcher_two", "password": "reader-password-456",
                              "clientType": "android", "deviceName": "other"})
    assert login.status_code == 200, login.text
    other = {"Authorization": "Bearer " + login.json()["accessToken"]}

    response = client.get(f"{TAVERN}/chats", params={"q": "暗号"}, headers=other)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert [item["id"] for item in _search(client, q="暗号")] == [mine["id"]]


def test_deleted_chat_is_not_searchable(client, reader):
    card = _import_card(client)
    chat = _create_chat(client, card["id"], "将要删除")
    assert [item["id"] for item in _search(client, q="删除")] == [chat["id"]]
    assert client.delete(f"{TAVERN}/chats/{chat['id']}").status_code == 204
    assert _search(client, q="删除") == []


def test_blank_q_equals_no_q(client, reader):
    card = _import_card(client)
    _create_chat(client, card["id"], "甲")
    _create_chat(client, card["id"], "乙")
    baseline = [item["id"] for item in _search(client)]
    assert [item["id"] for item in _search(client, q="")] == baseline
    assert [item["id"] for item in _search(client, q="   ")] == baseline


def test_search_respects_limit_offset_and_order(client, reader):
    card = _import_card(client)
    owner = reader["user"]["id"]
    base = datetime.now(timezone.utc)
    chats = [_create_chat(client, card["id"], f"测试 {index}") for index in range(3)]
    for index, chat in enumerate(chats):
        _seed_message(owner, chat["id"], f"第 {index} 条", when=base + timedelta(minutes=index))

    first = _search(client, q="测试", limit=2, offset=0)
    second = _search(client, q="测试", limit=2, offset=2)
    assert [item["id"] for item in first] == [chats[2]["id"], chats[1]["id"]]
    assert [item["id"] for item in second] == [chats[0]["id"]]
    # every chat starts with its greeting message, then the seeded one
    assert second[0]["messageCount"] == 2


def test_like_wildcards_are_escaped(client, reader):
    card = _import_card(client)
    owner = reader["user"]["id"]
    percent = _create_chat(client, card["id"], "进度 100%")
    plain = _create_chat(client, card["id"], "进度 100x")
    underscore = _create_chat(client, card["id"], "文件 a_b")
    other = _create_chat(client, card["id"], "文件 axb")
    _seed_message(owner, percent["id"], "完成 50% 了")
    _seed_message(owner, plain["id"], "完成五十")
    _seed_message(owner, underscore["id"], "名字是 a_b")
    _seed_message(owner, other["id"], "名字是 axb")

    assert [item["id"] for item in _search(client, q="100%")] == [percent["id"]]
    # a bare % must not behave as a wildcard matching every chat
    assert [item["id"] for item in _search(client, q="%")] == [percent["id"]]
    assert [item["id"] for item in _search(client, q="a_b")] == [underscore["id"]]
    assert [item["id"] for item in _search(client, q="_")] == [underscore["id"]]


def test_overlong_q_is_rejected(client, reader):
    response = client.get(f"{TAVERN}/chats", params={"q": "长" * 101})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "CHAT_SEARCH_INVALID"
    # exactly 100 characters is still accepted
    ok = client.get(f"{TAVERN}/chats", params={"q": "长" * 100})
    assert ok.status_code == 200, ok.text
    assert ok.json()["items"] == []
