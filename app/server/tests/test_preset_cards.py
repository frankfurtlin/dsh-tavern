"""Bundled preset cards: lazy seeding, idempotency and deletion semantics."""
import json

import pytest

from app.cards.models import CardLore, CharacterCard
from app.db import SessionLocal

PRESET_NAMES = {"玄净", "裴聿", "奈芙尔", "沈砚", "唐团团"}
PRESET_SLUGS = {"xuanjing", "peiyu", "naifuer", "shenyan", "tangtuantuan"}
HTML_MARKERS = ("<div", "<span", "<p>", "</", "&lt;")


@pytest.fixture(autouse=True)
def _enable_preset_cards(monkeypatch):
    monkeypatch.setenv("PRESET_CARDS_ENABLED", "1")


def _library(client):
    response = client.get("/api/reader/v1/tavern/characters")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _user_card_payload(name="测试角色"):
    return json.dumps({
        "spec": "chara_card_v2", "spec_version": "2.0",
        "data": {"name": name, "description": "手动导入", "personality": "p",
                 "scenario": "s", "first_mes": "你好", "mes_example": "",
                 "alternate_greetings": [], "tags": [], "system_prompt": "",
                 "post_history_instructions": ""},
    }, ensure_ascii=False).encode("utf-8")


def test_first_list_seeds_five_preset_cards(client, reader):
    items = _library(client)
    assert {item["name"] for item in items} == PRESET_NAMES
    assert len(items) == 5
    for item in items:
        assert item["hasAvatar"] is True
        assert item["firstMessage"].strip()
        for text in (item["description"], item["personality"], item["scenario"],
                     item["firstMessage"], " ".join(item["alternateGreetings"])):
            lowered = text.lower()
            assert not any(marker in lowered for marker in HTML_MARKERS), item["name"]


def test_card_out_marks_builtin_cards(client, reader):
    response = client.get("/api/reader/v1/character-cards")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 5
    assert {item["builtinSlug"] for item in items} == PRESET_SLUGS
    assert all(item["builtin"] is True for item in items)

    # The native UI reads the same markers from the tavern list payload, so both
    # endpoints must expose them (client badge + details come from here).
    tavern = client.get("/api/reader/v1/tavern/characters")
    assert tavern.status_code == 200, tavern.text
    tavern_items = tavern.json()["items"]
    assert {item["builtinSlug"] for item in tavern_items} == PRESET_SLUGS
    assert all(item["builtin"] is True for item in tavern_items)

    imported = client.post("/api/reader/v1/character-cards/imports",
                           files={"file": ("mine.json", _user_card_payload(), "application/json")})
    assert imported.status_code == 201, imported.text
    card = imported.json()["card"]
    assert card["builtin"] is False
    assert card["builtinSlug"] is None


def test_seeding_is_idempotent(client, reader):
    assert len(_library(client)) == 5
    assert len(_library(client)) == 5
    owner = reader["user"]["id"]
    with SessionLocal() as db:
        rows = db.query(CharacterCard).filter(CharacterCard.owner_id == owner,
                                              CharacterCard.builtin_slug.is_not(None)).all()
        assert len(rows) == 5
        assert all(card.avatar_path for card in rows)


def test_deleted_preset_card_is_not_reseeded(client, reader):
    items = _library(client)
    target = next(item for item in items if item["name"] == "玄净")
    assert client.delete(f"/api/reader/v1/tavern/characters/{target['id']}").status_code == 204

    remaining = _library(client)
    assert len(remaining) == 4
    assert "玄净" not in {item["name"] for item in remaining}
    owner = reader["user"]["id"]
    with SessionLocal() as db:
        rows = db.query(CharacterCard).filter(CharacterCard.owner_id == owner,
                                              CharacterCard.builtin_slug == "xuanjing").all()
        assert len(rows) == 1
        assert rows[0].deleted_at is not None


def test_xuanjing_lore_imported(client, reader):
    items = _library(client)
    xuanjing = next(item for item in items if item["name"] == "玄净")
    owner = reader["user"]["id"]
    with SessionLocal() as db:
        entries = db.query(CardLore).filter(CardLore.owner_id == owner,
                                            CardLore.card_id == xuanjing["id"]).all()
        assert len(entries) >= 1
        assert all(entry.keys for entry in entries)
        assert any("玄净" in entry.content or "玄净" in entry.name for entry in entries)


def test_preset_avatar_is_served(client, reader):
    item = _library(client)[0]
    response = client.get(f"/api/reader/v1/tavern/characters/{item['id']}/avatar")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_seeds_are_per_owner(client, reader):
    first = {item["id"] for item in _library(client)}
    assert client.post("/api/reader/v1/auth/register",
                       json={"username": "reader_two", "password": "reader-password-456"}).status_code == 201
    login = client.post("/api/reader/v1/auth/login",
                        json={"username": "reader_two", "password": "reader-password-456",
                              "clientType": "android", "deviceName": "other-device"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["accessToken"]}
    response = client.get("/api/reader/v1/tavern/characters", headers=headers)
    assert response.status_code == 200, response.text
    second = {item["id"] for item in response.json()["items"]}
    assert len(second) == 5
    assert first.isdisjoint(second)
