"""P2-2: exporting an interactive-world character as a character card."""
from .test_authoring_publications import other
from .test_play_controls_world_chat import world


WORLD = "/api/world/v1"
CARDS = "/api/reader/v1/character-cards"


def export(client, world_id, character_id, key="world-card"):
    return client.post(WORLD + "/worlds/" + world_id + "/character-cards",
                       json={"characterId": character_id}, headers={"Idempotency-Key": key})


def test_export_world_character_creates_card_with_source_refs(client, reader, profile):
    world_id, line = world(client)
    entity = next(e for e in line["head"]["state"]["entities"] if e["type"] == "character")
    response = export(client, world_id, entity["id"])
    assert response.status_code == 201, response.text
    card_id = response.json()["cardId"]

    card = client.get(CARDS + "/" + card_id).json()
    assert card["source"] == "world-export"
    assert card["worldId"] == world_id
    assert card["lineId"] == line["id"]
    assert card["checkpointId"]
    assert card["characterId"] == entity["id"]
    assert card["name"] == entity["name"]
    assert card["scenario"] == "来自互动世界《" + line["worldName"] + "》" if "worldName" in line else card["scenario"].startswith("来自互动世界")
    assert card["tags"] == ["世界导出"]
    assert card["chatTarget"]["worldId"] == world_id
    assert card["chatTarget"]["characterId"] == entity["id"]

    listed = client.get(CARDS).json()["items"]
    assert card_id in {item["id"] for item in listed}

    again = export(client, world_id, entity["id"], key="world-card-again")
    assert again.status_code == 201 and again.json()["cardId"] == card_id

    opened = client.post(CARDS + "/" + card_id + "/chats")
    assert opened.status_code == 201, opened.text
    assert opened.json()["cardId"] == card_id


def test_export_world_character_validation(client, reader, profile):
    world_id, line = world(client)
    entity = next(e for e in line["head"]["state"]["entities"] if e["type"] == "character")
    missing = export(client, world_id, "f" * 32)
    assert missing.status_code == 404 and missing.json()["code"] == "CARD_CHARACTER_UNAVAILABLE"
    assert export(client, "0" * 32, entity["id"]).status_code == 404
    assert client.post(WORLD + "/worlds/" + world_id + "/character-cards",
                       json={"characterId": entity["id"]},
                       headers={"Authorization": ""}).status_code == 401

    owner_card = export(client, world_id, entity["id"]).json()["cardId"]
    other(client, "world_export_other")
    assert client.get(CARDS + "/" + owner_card).status_code == 404
    assert export(client, world_id, entity["id"]).status_code == 404
