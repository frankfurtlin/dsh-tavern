"""P1-1: reader personas, chat binding and prompt injection."""
from app.providers import MockAdapter
from app.world.worker import process
from app import worker as worker_module

from .test_tavern_character_cards import API, CARDS, import_card, send

PERSONAS = API + "/tavern-personas"


def create_persona(client, name="读者·阿澈", description="刚从城里来的旅人，谨慎但好奇。"):
    response = client.post(PERSONAS, json={"name": name, "description": description})
    assert response.status_code == 201, response.text
    return response.json()["persona"]


def open_chat(client, card):
    response = client.post(CARDS + "/" + card["id"] + "/chats")
    assert response.status_code == 201, response.text
    return response.json()["sessionId"]


def bind(client, session_id, persona_id, version=1):
    return client.patch(API + "/play-controls/world-chats/" + session_id,
                        json={"expectedVersion": version, "personaId": persona_id},
                        headers={"Idempotency-Key": "persona-bind-%s-%s" % (session_id[:8], version)})


def test_persona_crud_and_validation(client, reader):
    assert client.get(PERSONAS).json()["items"] == []
    persona = create_persona(client)
    assert persona["name"] == "读者·阿澈" and persona["description"].startswith("刚从城里")
    assert [item["id"] for item in client.get(PERSONAS).json()["items"]] == [persona["id"]]
    patched = client.patch(PERSONAS + "/" + persona["id"],
                           json={"name": "阿澈", "description": "换了身衣服。"})
    assert patched.status_code == 200
    assert patched.json()["persona"]["name"] == "阿澈"
    assert patched.json()["persona"]["description"] == "换了身衣服。"
    assert client.post(PERSONAS, json={"name": "  ", "description": ""}).status_code == 422
    assert client.patch(PERSONAS + "/" + persona["id"], json={"name": ""}).status_code == 422
    assert client.delete(PERSONAS + "/" + persona["id"]).status_code == 204
    assert client.get(PERSONAS).json()["items"] == []
    assert client.get(PERSONAS + "/does-not-exist").status_code == 405


def test_bind_persona_to_chat_and_clear(client, reader):
    card = import_card(client)
    session_id = open_chat(client, card)
    persona = create_persona(client)
    current = client.get(API + "/play-controls/world-chats/" + session_id).json()
    assert current["personaId"] is None
    bound = bind(client, session_id, persona["id"], current["version"])
    assert bound.status_code == 200, bound.text
    assert bound.json()["personaId"] == persona["id"]
    refreshed = client.get(API + "/play-controls/world-chats/" + session_id).json()
    assert refreshed["personaId"] == persona["id"]
    cleared = bind(client, session_id, None, refreshed["version"])
    assert cleared.json()["personaId"] is None
    # Deleting a persona detaches it from existing chats.
    updated = client.get(API + "/play-controls/world-chats/" + session_id).json()
    bind(client, session_id, persona["id"], updated["version"])
    assert client.delete(PERSONAS + "/" + persona["id"]).status_code == 204
    assert client.get(API + "/play-controls/world-chats/" + session_id).json()["personaId"] is None


def test_persona_is_injected_after_memory(client, reader, profile, monkeypatch):
    captured = []

    class Capture(MockAdapter):
        def generate(self, profile, system_prompt, data, output_limit):
            captured.append(system_prompt)
            return super().generate(profile, system_prompt, data, output_limit)

    monkeypatch.setattr(worker_module, "adapter_for", lambda mode=None: Capture())
    card = import_card(client)
    client.post(CARDS + "/" + card["id"] + "/memories",
                json={"kind": "fact", "title": "读者的猫", "content": "读者养了一只叫团子的猫。"})
    persona = create_persona(client, name="阿澈", description="读者的化身，说话直白。")
    session_id = open_chat(client, card)
    version = client.get(API + "/play-controls/world-chats/" + session_id).json()["version"]
    assert bind(client, session_id, persona["id"], version).status_code == 200
    sent = send(client, session_id, "我带着团子来借宿。", "persona-turn")
    assert sent.status_code == 202, sent.text
    process(sent.json()["taskId"])
    prompt = next(text for text in captured if "【角色卡设定】" in text)
    assert "【你的对话对象】" in prompt and "阿澈" in prompt and "读者的化身" in prompt
    assert prompt.index("【长期记忆】") < prompt.index("【你的对话对象】")


def test_context_preview_returns_persona(client, reader):
    card = import_card(client)
    persona = create_persona(client, name="阿澈", description="读者的化身，说话直白。")
    payload = client.post(CARDS + "/" + card["id"] + "/context-preview",
                          json={"message": "随便聊聊", "personaId": persona["id"]}).json()
    assert payload["persona"] == {"id": persona["id"], "name": "阿澈"}
    assert "【你的对话对象】" in payload["personaSection"] and "阿澈" in payload["personaSection"]
    without = client.post(CARDS + "/" + card["id"] + "/context-preview",
                          json={"message": "随便聊聊"}).json()
    assert without["persona"] is None and without["personaSection"] == ""


def test_persona_requires_auth_and_owner(client, reader):
    persona = create_persona(client)
    client.post(API + "/auth/register", json={"username": "reader_two", "password": "reader-password-456"})
    other = client.post(API + "/auth/login", json={"username": "reader_two", "password": "reader-password-456",
                                                    "clientType": "android", "deviceName": "other"}).json()
    headers = {"Authorization": "Bearer " + other["accessToken"]}
    assert client.get(PERSONAS, headers=headers).json()["items"] == []
    assert client.patch(PERSONAS + "/" + persona["id"], json={"name": "偷改"}, headers=headers).status_code == 404
    assert client.delete(PERSONAS + "/" + persona["id"], headers=headers).status_code == 404
    assert client.get(PERSONAS, headers={"Authorization": ""}).status_code == 401
