"""Short secrets follow frozen knowledge through metadata, prose and real HTTP."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from app.db import SessionLocal
from app.world.models import WorldCheckpoint
from app.world.schemas import LoreEntry
from app.world.state import initial_state, private_phrases, public_payload, public_stream, checkpoint_hash
from app.world.memory import narrator_context
from app.world.worker import save_visibility
from app.world.jobs import persist_stream
from .test_world import definition, start, action, API


def private_world(text, private_terms=()):
    d = definition()
    d["lore"][0].update(text=text, privateTerms=list(private_terms))
    return initial_state(d, "player")


@pytest.mark.parametrize("secret", ["玄", "青雀", "白玉堂"])
def test_short_literal_secret_removed_from_nested_metadata_and_narrator_memory(secret):
    d, state = private_world(secret)
    original = deepcopy((d, state))
    payload = public_payload({"options": [{"text": secret}], "sourceRef": {"label": secret}}, state, d)
    narration = narrator_context(SimpleNamespace(text=secret,state=deepcopy(state)), state, d,
        {"title": secret, "publicOutcome": secret}, "观察", rewrite=True, old_text=secret)
    assert secret not in json.dumps([payload, narration], ensure_ascii=False)
    assert (d, state) == original


def test_legacy_identity_sentence_protects_fragment_without_private_description():
    d, state = private_world("金生是白玉堂的化名。")
    assert all(e.get("description", "") != "白玉堂" for e in d["entities"])
    assert "白玉堂" not in public_payload({"text": "来者是白玉堂。"}, state, d)["text"]


@pytest.mark.parametrize("text,term", [("真名是白玉堂。", "白玉堂"), ("口令是x7。", "x7")])
def test_legacy_explicit_identity_and_code_statements(text, term):
    d, state = private_world(text)
    assert term in private_phrases(state, d)


def test_explicit_terms_protect_arbitrary_long_sentence_and_are_released_by_knowledge():
    d, state = private_world("柜台背面的旧记号对应青雀。", ["青雀"])
    assert "青雀" not in public_payload({"text": "说出青雀。"}, state, d)["text"]
    state["known"] = ["secret"]
    state["entities"]["player"]["known"] = ["secret"]
    assert public_payload({"text": "说出青雀。"}, state, d)["text"] == "说出青雀。"


def test_known_identity_overrides_duplicate_private_description():
    d, state = private_world("白玉堂")
    next(e for e in d["entities"] if e["id"] == "npc").update(description="白玉堂", knownBy=["npc"])
    state["known"] = ["secret"]
    assert public_payload({"text": "白玉堂"}, state, d)["text"] == "白玉堂"


def test_discovering_public_character_does_not_reveal_explicit_private_alias():
    d, state = private_world("梅舟还有一个未公开的名字。", ["白玉堂"])
    next(e for e in d["entities"] if e["id"] == "npc")["aliases"] = ["白玉堂"]
    assert "npc" in state["discovered"]
    assert "白玉堂" not in public_payload({"text": "白玉堂走来。"}, state, d)["text"]
    state["known"] = ["secret"]
    assert public_payload({"text": "白玉堂走来。"}, state, d)["text"] == "白玉堂走来。"


def test_unknown_fact_does_not_hide_already_public_name_or_unrelated_short_name():
    d, state = private_world("梅舟计划在明日离开。")
    assert public_payload({"text": "梅舟与张小明说话。"}, state, d)["text"] == "梅舟与张小明说话。"


def test_explicit_private_place_and_alias_are_not_released_by_discovery():
    d, state = private_world("青城又叫云台，这是尚未获知的地点。", ["青城", "云台"])
    far = next(e for e in d["entities"] if e["id"] == "far")
    far.update(name="青城", aliases=["云台"])
    assert public_payload({"text": "青城又称云台。"}, state, d)["text"] == "尚未明确的事情又称尚未明确的事情。"
    state["discovered"].append("far")
    assert public_payload({"text": "青城又称云台。"}, state, d)["text"] == "尚未明确的事情又称尚未明确的事情。"
    state["known"] = ["secret"]
    assert public_payload({"text": "青城又称云台。"}, state, d)["text"] == "青城又称云台。"


def test_split_stream_holds_short_secret_prefix_then_redacts_exact_term():
    d, state = private_world("白玉堂")
    phrases = private_phrases(state, d)
    assert public_stream("来者是白", phrases) == "来者是"
    assert public_stream("来者是白玉", phrases) == "来者是"
    assert public_stream("来者是白玉堂。", phrases, final=True) == "来者是尚未明确的事情。"


@pytest.mark.parametrize("terms", [[""], [" "], ["秘" * 101], [str(i) for i in range(21)]])
def test_private_terms_are_bounded_and_nonblank(terms):
    with pytest.raises(ValidationError):
        LoreEntry(id="private", title="秘密", text="测试资料", privateTerms=terms)


def test_old_lore_schema_is_compatible_and_duplicate_terms_are_deduplicated():
    assert LoreEntry(id="old", title="旧资料", text="旧事实").privateTerms == []
    assert LoreEntry(id="new", title="新资料", text="新事实", privateTerms=["青雀", "青雀"]).privateTerms == ["青雀"]


def test_actual_persisted_split_stream_and_http_turn_metadata_protect_short_term(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    job = action(client, world, line)
    with SessionLocal() as db:
        parent = db.get(WorldCheckpoint, line["headCheckpointId"])
        d, state = deepcopy(parent.definition), deepcopy(parent.state)
        d["lore"].append({"id": "private-short", "title": "秘密口令", "text": "柜台后的旧符号对应青雀。",
                          "privateTerms": ["青雀"], "knownBy": [], "entityIds": []})
        parent.definition = d
        parent.content_hash = checkpoint_hash(state, d, parent.text, parent.parent_checkpoint_id)
        db.commit()
    save_visibility(job["taskId"], state, d)
    persist_stream(job["taskId"], "generation:scene:1", "公开正文。青")
    assert client.get(API + "/tasks/" + job["taskId"]).json()["stream"]["text"] == "公开正文。"
    persist_stream(job["taskId"], "generation:scene:1", "公开正文。青雀。", final=True)
    for path in ("/tasks/" + job["taskId"], f"/worlds/{world['id']}/turns/{job['turnId']}"):
        response = client.get(API + path)
        assert response.status_code == 200 and "青雀" not in response.text
    assert client.post(API + "/tasks/" + job["taskId"] + "/cancel").status_code == 200
