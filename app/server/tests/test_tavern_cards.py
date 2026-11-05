"""Native tavern card features: import/list/edit/export, lorebooks, presets, regex, personas."""
import base64
import json

from .test_tavern_native_chat import card_payload


def import_card(client, name="阿岩"):
    response = client.post(
        "/api/reader/v1/character-cards/imports",
        files={"file": ("card.json", json.dumps(card_payload(name)).encode(), "application/json")},
    )
    assert response.status_code == 201, response.text
    return response.json()["card"]


def test_card_import_list_edit_and_delete(client, reader):
    card = import_card(client)
    assert card["name"] == "阿岩"
    assert card["source"] == "json"
    assert card["worldId"] is None

    listing = client.get("/api/reader/v1/character-cards", params={"q": "阿"}).json()
    assert [item["id"] for item in listing["items"]] == [card["id"]]

    patched = client.patch(
        f"/api/reader/v1/character-cards/{card['id']}",
        json={"description": "改过的描述", "tags": ["测试"], "companion": True},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["card"]["description"] == "改过的描述"
    assert patched.json()["card"]["companion"] is True

    assert client.delete(f"/api/reader/v1/character-cards/{card['id']}").status_code == 204
    assert client.get(f"/api/reader/v1/character-cards/{card['id']}").status_code == 404


def test_lore_preset_regex_crud(client, reader):
    card = import_card(client)
    lore = client.post(
        f"/api/reader/v1/character-cards/{card['id']}/lore",
        json={"keys": ["酒馆"], "content": "酒馆只在夜里开门", "order": 10},
    )
    assert lore.status_code == 201, lore.text
    assert lore.json()["lore"]["keys"] == ["酒馆"]
    assert len(client.get(f"/api/reader/v1/character-cards/{card['id']}/lore").json()["items"]) == 1

    preset = client.post("/api/reader/v1/presets", json={"name": "夜谈", "systemSuffix": "语气安静"})
    assert preset.status_code == 201, preset.text
    assert client.get("/api/reader/v1/presets").json()["items"]
    assert client.delete(f"/api/reader/v1/presets/{preset.json()['preset']['id']}").status_code == 204

    script = client.post("/api/reader/v1/regex-scripts", json={
        "name": "替换", "pattern": "。", "replacement": "！"})
    assert script.status_code == 201, script.text
    assert client.patch(f"/api/reader/v1/regex-scripts/{script.json()['script']['id']}",
                        json={"enabled": False}).status_code == 200
    assert client.post("/api/reader/v1/regex-scripts", json={
        "name": "坏正则", "pattern": "(", "replacement": ""}).status_code == 422


def test_persona_crud(client, reader):
    created = client.post("/api/reader/v1/tavern-personas", json={"name": "旅人", "description": "北方的旅人"})
    assert created.status_code == 201, created.text
    persona_id = created.json()["persona"]["id"]
    listing = client.get("/api/reader/v1/tavern-personas").json()
    assert [item["id"] for item in listing["items"]] == [persona_id]
    patched = client.patch(f"/api/reader/v1/tavern-personas/{persona_id}", json={"name": "夜行者"})
    assert patched.json()["persona"]["name"] == "夜行者"
    assert client.delete(f"/api/reader/v1/tavern-personas/{persona_id}").status_code == 204
    assert client.get("/api/reader/v1/tavern-personas").json()["items"] == []


def test_card_export_png_and_json(client, reader):
    card = import_card(client)
    png = client.get(f"/api/reader/v1/character-cards/{card['id']}/export", params={"format": "png"})
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content.startswith(b"\x89PNG\r\n\x1a\n")
    text = client.get(f"/api/reader/v1/character-cards/{card['id']}/export", params={"format": "json"})
    document = text.json()
    assert document["spec"] == "chara_card_v2"
    assert document["data"]["name"] == "阿岩"
    assert client.get(f"/api/reader/v1/character-cards/{card['id']}/export",
                      params={"format": "yaml"}).status_code == 422


def test_card_endpoints_require_auth(client):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as anonymous:
        assert anonymous.get("/api/reader/v1/character-cards").status_code == 401
        assert anonymous.get("/api/reader/v1/tavern-personas").status_code == 401
