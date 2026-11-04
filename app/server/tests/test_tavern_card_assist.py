"""P2-3: AI card drafts and greeting suggestions (never persisted)."""
from sqlalchemy import select

from app.cards.models import CharacterCard
from app.db import SessionLocal

from .test_authoring_publications import other
from .test_tavern_character_cards import CARDS, import_card


DRAFT = CARDS + "/draft"


def draft(client, brief):
    return client.post(DRAFT, json={"brief": brief})


def test_draft_returns_complete_card_without_saving(client, reader, profile):
    before = client.get(CARDS).json()["items"]
    response = draft(client, "一个在雾港送信的神秘信使")
    assert response.status_code == 200, response.text
    body = response.json()["draft"]
    assert set(body) == {"name", "description", "personality", "scenario", "firstMessage", "tags"}
    assert body["name"] == "雾港信使"
    assert body["tags"] == ["悬疑", "信使", "雾港"]
    assert body["description"] and body["personality"]
    assert body["scenario"] and body["firstMessage"]
    after = client.get(CARDS).json()["items"]
    assert [item["id"] for item in after] == [item["id"] for item in before]


def test_draft_validation_and_auth(client, reader, profile):
    assert draft(client, "   ").status_code == 422
    assert client.post(DRAFT, json={"brief": "x" * 2001}).status_code == 422
    assert client.post(DRAFT, json={"brief": "一个普通角色"},
                       headers={"Authorization": ""}).status_code == 401
    assert draft(client, "一个普通角色").status_code == 200


def test_greetings_generate_candidates_without_saving(client, reader, profile):
    card = import_card(client)
    with SessionLocal() as db:
        saved_greetings = list(db.scalar(
            select(CharacterCard).where(CharacterCard.id == card["id"])).alternate_greetings or [])
    response = client.post(CARDS + "/" + card["id"] + "/greetings", json={"count": 3})
    assert response.status_code == 200, response.text
    greetings = response.json()["greetings"]
    assert len(greetings) == 3
    assert all(isinstance(item, str) and item.strip() for item in greetings)
    assert len(set(greetings)) == 3
    with SessionLocal() as db:
        row = db.scalar(select(CharacterCard).where(CharacterCard.id == card["id"]))
        assert list(row.alternate_greetings or []) == saved_greetings
        assert row.first_message == card["firstMessage"]

    default = client.post(CARDS + "/" + card["id"] + "/greetings", json={})
    assert default.status_code == 200 and len(default.json()["greetings"]) == 3


def test_greetings_validation_and_ownership(client, reader, profile):
    card = import_card(client)
    assert client.post(CARDS + "/" + card["id"] + "/greetings", json={"count": 0}).status_code == 422
    assert client.post(CARDS + "/" + card["id"] + "/greetings", json={"count": 6}).status_code == 422
    assert client.post(CARDS + "/" + card["id"] + "/greetings", json={"count": 3},
                       headers={"Authorization": ""}).status_code == 401
    assert client.post(CARDS + "/" + "f" * 32 + "/greetings", json={"count": 3}).status_code == 404
    other(client, "greetings_other")
    assert client.post(CARDS + "/" + card["id"] + "/greetings", json={"count": 3}).status_code == 404
