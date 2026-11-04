"""P1-3: transparent per-turn injection report for card chats."""
from .test_tavern_character_cards import API, CARDS, send
from .test_tavern_card_space import V2_CARD as BOOK_CARD, import_card
from .test_authoring_publications import post

PERSONAS = API + "/tavern-personas"


def card_chat(client, card):
    return client.post(CARDS + "/" + card["id"] + "/chats").json()["sessionId"]


def injection(client, session_id, before=None):
    path = API + "/play-controls/world-chats/" + session_id + "/injection"
    return client.get(path + ("" if before is None else "?before=" + str(before)))


def test_injection_reports_lore_memories_persona_and_prompt(client, reader, profile):
    card = import_card(client, BOOK_CARD)
    preset = post(client, API + "/presets", {"name": "冷静叙述", "systemSuffix": "保持冷静克制的叙述语气。"})
    client.patch(CARDS + "/" + card["id"], json={"presetId": preset["preset"]["id"]})
    client.post(CARDS + "/" + card["id"] + "/memories",
                json={"kind": "fact", "title": "读者的猫", "content": "读者养了一只叫团子的猫。"})
    persona = client.post(PERSONAS, json={"name": "阿澈", "description": "读者的化身，说话直白。"}).json()["persona"]
    session_id = card_chat(client, card)
    version = client.get(API + "/play-controls/world-chats/" + session_id).json()["version"]
    client.patch(API + "/play-controls/world-chats/" + session_id,
                 json={"expectedVersion": version, "personaId": persona["id"]},
                 headers={"Idempotency-Key": "injection-persona-" + session_id[:8]})
    sent = send(client, session_id, "雨夜赶路到旅店，想借个火。", "injection-turn")
    assert sent.status_code == 202, sent.text
    from app.world.worker import process
    process(sent.json()["taskId"])

    payload = injection(client, session_id, before=3).json()
    assert payload["cardId"] == card["id"]
    lore_titles = [item["title"] for item in payload["lore"]]
    assert "雨夜旅店" in lore_titles
    assert [item["title"] for item in payload["memories"]] == ["读者的猫"]
    assert payload["persona"] == {"id": persona["id"], "name": "阿澈"}
    assert payload["preset"] == {"id": preset["preset"]["id"], "name": preset["preset"]["name"]}
    suffix = payload["systemSuffix"]
    for block in ("【角色卡设定】", "【世界书】", "【预设】", "【长期记忆】", "【你的对话对象】"):
        assert block in suffix, block
    assert suffix.index("【世界书】") < suffix.index("【长期记忆】") < suffix.index("【你的对话对象】")
    assert "雨夜旅店" in suffix and "读者的猫" in suffix and "阿澈" in suffix


def test_injection_without_persona_or_card(client, reader):
    card = import_card(client)
    session_id = card_chat(client, card)
    payload = injection(client, session_id).json()
    assert payload["persona"] is None
    assert "【你的对话对象】" not in payload["systemSuffix"]
    assert injection(client, session_id, before=99).status_code == 200


def test_injection_requires_auth_and_owner(client, reader):
    card = import_card(client)
    session_id = card_chat(client, card)
    client.post(API + "/auth/register", json={"username": "reader_two", "password": "reader-password-456"})
    other = client.post(API + "/auth/login", json={"username": "reader_two", "password": "reader-password-456",
                                                    "clientType": "android", "deviceName": "other"}).json()
    headers = {"Authorization": "Bearer " + other["accessToken"]}
    assert injection(client, session_id) is not None
    assert client.get(API + "/play-controls/world-chats/" + session_id + "/injection",
                      headers=headers).status_code == 404
    assert client.get(API + "/play-controls/world-chats/" + session_id + "/injection",
                      headers={"Authorization": ""}).status_code == 401
