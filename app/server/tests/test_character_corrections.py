"""NX035: exact reader-scope recognition; no model or historical mutations."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select, func

from app.db import SessionLocal
from app.models import KnowledgeSnapshot, CharacterScopeRevision, RoleSession, UsageRecord, Revision, ContentBlock
from .helpers import BASE, read_chapter, observe
from .test_character_cards import index, card_list, chat
from .test_world_security import isolated_storage_and_mock_only


@pytest.fixture
def recognized(client, reader, demo, profile):
    index(client, demo)
    anchor = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, anchor)
    card = next(c for c in card_list(client, demo, anchor).json()["items"] if c["name"] == "林渡")
    return {"demo": demo, "anchor": anchor, "card": card}


def payload(value, **changes):
    recognition = value["card"]["recognition"]
    return {"anchor": value["anchor"], "expectedVersion": recognition["version"],
        "displayName": value["card"]["name"], "aliases": [value["card"]["name"]],
        "evidenceIds": [recognition["evidence"][0]["id"]], "identityStatus": "confirmed",
        "reason": "根据已读这一段确认此卡所指的人物，不合并同名人物。", **changes}


def correct(client, value, body=None, key="recognition-correction"):
    return client.put(f"{BASE}/books/{value['demo']['bookId']}/character-cards/{value['card']['id']}/recognition",
        json=body or payload(value), headers={"Idempotency-Key": key})


def get_card(client, value):
    return next(c for c in card_list(client, value["demo"], value["anchor"]).json()["items"] if c["id"] == value["card"]["id"])


def test_recognition_shows_only_current_verified_evidence_and_no_future_alias(client, recognized):
    card = recognized["card"]
    assert "recognition" in card
    recognition = card["recognition"]
    assert recognition["version"] == 0 and recognition["evidence"]
    assert recognition["scope"]["anchor"] == recognized["anchor"]
    assert all(e["anchor"]["revisionId"] == recognized["anchor"]["revisionId"] for e in recognition["evidence"])
    assert "夜渡" not in json.dumps(recognition, ensure_ascii=False)
    assert recognition["aliases"] == ["林渡"]


def test_correction_creates_immutable_version_cas_and_idempotent_receipt_without_calls(client, recognized):
    with SessionLocal() as db:
        original = {s.id: deepcopy(s.data) for s in db.scalars(select(KnowledgeSnapshot))}
        revisions = {r.id: (deepcopy(r.manifest), r.content_hash) for r in db.scalars(select(Revision))}
        blocks = {b.id: b.text for b in db.scalars(select(ContentBlock))}
        calls = db.scalar(select(func.count()).select_from(UsageRecord))
    body = payload(recognized)
    response = correct(client, recognized, body)
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["identityId"] == recognized["card"]["identityId"]
    assert first["recognition"]["version"] == 1
    assert correct(client, recognized, body).json() == first
    conflict = correct(client, recognized, body, "other-device")
    assert conflict.status_code == 409 and conflict.json()["code"] == "CHARACTER_CORRECTION_CONFLICT"
    second_body = {**body, "expectedVersion": 1, "identityStatus": "uncertain", "reason": "该证据仍有同名歧义，暂不确认。"}
    second = correct(client, recognized, second_body, "second-correction")
    assert second.status_code == 200, second.text
    assert second.json()["recognition"]["version"] == 2
    assert [h["version"] for h in second.json()["recognition"]["history"]] == [2, 1]
    assert second.json()["recognition"]["history"][1]["reason"] == body["reason"]
    assert not second.json()["available"]
    with SessionLocal() as db:
        assert all(db.get(KnowledgeSnapshot, sid).data == data for sid, data in original.items())
        assert {r.id: (r.manifest, r.content_hash) for r in db.scalars(select(Revision))} == revisions
        assert {b.id: b.text for b in db.scalars(select(ContentBlock))} == blocks
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == calls
        rows = list(db.scalars(select(CharacterScopeRevision).where(CharacterScopeRevision.context["readerCorrection"].as_boolean().is_(True))))
        assert len(rows) == 2 and {r.version for r in rows} == {1, 2}


def test_correction_new_chat_uses_new_frozen_scope_old_chat_is_unchanged(client, recognized):
    old = chat(client, recognized["demo"], recognized["anchor"], recognized["card"], "before-correction-chat")
    with SessionLocal() as db:
        old_snapshot = db.get(RoleSession, old["id"]).snapshot_id
        old_data = deepcopy(db.get(KnowledgeSnapshot, old_snapshot).data)
    response = correct(client, recognized)
    assert response.status_code == 200, response.text
    updated = chat(client, recognized["demo"], recognized["anchor"], response.json(), "after-correction-chat")
    assert updated["id"] != old["id"]
    with SessionLocal() as db:
        prior = db.get(RoleSession, old["id"])
        current = db.get(RoleSession, updated["id"])
        assert prior.snapshot_id == old_snapshot and db.get(KnowledgeSnapshot, old_snapshot).data == old_data
        assert current.snapshot_id != old_snapshot and current.character_identity_id == prior.character_identity_id
        assert db.get(KnowledgeSnapshot, current.snapshot_id).data["readerCorrectionVersion"] == 1


@pytest.mark.parametrize("changes", [
    {"aliases": ["夜渡"]}, {"displayName": "夜渡"}, {"evidenceIds": ["unread-evidence"]},
    {"aliases": ["林渡", "另一来源人物"]}, {"reason": "   "},
])
def test_unread_or_unsupported_aliases_and_untraceable_reason_are_rejected(client, recognized, changes):
    response = correct(client, recognized, payload(recognized, **changes))
    assert response.status_code == 422, response.text
    assert get_card(client, recognized)["recognition"]["version"] == 0


def test_correction_is_exact_anchor_not_shared_with_later_or_earlier_scope(client, reader, recognized):
    assert correct(client, recognized).status_code == 200
    later = read_chapter(client, recognized["demo"])["blocks"][1]["endAnchor"]
    observe(client, reader, later, expected=1, sequence=2)
    card = next(c for c in card_list(client, recognized["demo"], later).json()["items"] if c["name"] == "林渡")
    assert card["recognition"]["version"] == 0
    assert get_card(client, recognized)["recognition"]["version"] == 1


def test_correction_cannot_unlock_dead_character_or_merge_identity(client, reader, recognized):
    later = read_chapter(client, recognized["demo"], 1)["blocks"][0]["endAnchor"]
    observe(client, reader, later, expected=1, sequence=2)
    dead = next(c for c in card_list(client, recognized["demo"], later).json()["items"] if c["name"] == "阿岑")
    value = {**recognized, "card": dead, "anchor": later}
    response = correct(client, value)
    assert response.status_code == 200, response.text
    assert not response.json()["available"] and response.json()["reasonCode"] == "ROLE_DECEASED"
    assert response.json()["identityId"] == dead["identityId"]
    denied = client.post(f"{BASE}/books/{recognized['demo']['bookId']}/character-chats", json={"anchor": later, "characterId": dead["id"]},
        headers={"Idempotency-Key": "cannot-revive-by-recognition"})
    assert denied.status_code == 422


def test_other_owner_or_wrong_book_and_unobserved_scope_are_rejected(client, recognized):
    body = payload(recognized)
    wrong = client.put(f"{BASE}/books/{'f'*32}/character-cards/{recognized['card']['id']}/recognition", json=body,
        headers={"Idempotency-Key": "wrong-book"})
    assert wrong.status_code == 404
    later = read_chapter(client, recognized["demo"], 3)["blocks"][-1]["endAnchor"]
    denied = correct(client, recognized, {**body, "anchor": later}, "unobserved")
    assert denied.status_code == 422
    client.headers.pop("Authorization", None)
    assert client.post(BASE + "/auth/register", json={"username": "correction_other", "password": "reader-password-123"}).status_code == 201
    login = client.post(BASE + "/auth/login", json={"username": "correction_other", "password": "reader-password-123", "clientType": "web", "deviceName": "other"})
    client.headers["Authorization"] = "Bearer " + login.json()["accessToken"]
    assert correct(client, recognized, body, "other-owner").status_code == 404


def test_same_name_correction_preserves_two_identities_and_only_confirms_selected_card(client, recognized):
    with SessionLocal() as db:
        source = next(s for s in db.scalars(select(KnowledgeSnapshot)) if not s.data.get("cardScopeVersion"))
        data = deepcopy(source.data)
        original = next(c for c in data["characters"] if c["displayName"] == "林渡")
        original["identityUncertain"] = True
        data["characters"].append({**deepcopy(original), "id": "different-person-same-name"})
        source.data = data
        db.commit()
    same = [c for c in card_list(client, recognized["demo"], recognized["anchor"]).json()["items"] if c["name"] == "林渡"]
    assert len(same) == 2 and len({c["identityId"] for c in same}) == 2 and all(not c["available"] for c in same)
    current = {**recognized, "card": next(c for c in same if c["id"] == recognized["card"]["id"])}
    result = correct(client, current)
    assert result.status_code == 200 and result.json()["available"], result.text
    after = [c for c in card_list(client, recognized["demo"], recognized["anchor"]).json()["items"] if c["name"] == "林渡"]
    assert len({c["identityId"] for c in after}) == 2
    assert next(c for c in after if c["id"] != current["card"]["id"])["available"] is False


def test_malformed_persisted_correction_returns_typed_error_instead_of_500(client, recognized):
    assert correct(client, recognized).status_code == 200
    with SessionLocal() as db:
        row = db.scalar(select(CharacterScopeRevision).where(CharacterScopeRevision.context["readerCorrection"].as_boolean().is_(True)))
        row.visible_data = {**row.visible_data, "aliases": 7}
        db.commit()
    response = client.get(f"{BASE}/books/{recognized['demo']['bookId']}/character-cards", params={"branchId": recognized['anchor']['branchId'], "anchor": json.dumps(recognized['anchor'])})
    assert response.status_code == 422 and response.json()["code"] == "CHARACTER_ALIAS_INVALID"


def test_profile_change_uses_exact_frozen_proof_without_losing_or_rewriting_correction(client, recognized):
    assert correct(client, recognized).status_code == 200
    added = client.post(BASE + "/model-profiles", json={"name": "second-mock", "baseUrl": "mock://deterministic", "modelId": "fixture-v1", "key": "TEST-KEY-NOT-REAL", "contextWindow": 32768, "callBudget": 100, "inputBudget": 10000000, "outputBudget": 1000000})
    assert added.status_code == 201, added.text
    second = added.json()
    from app.models import ModelProfile
    from app.knowledge import snapshot_hash
    from app.domain import prefix_blocks
    with SessionLocal() as db:
        raw = next(s for s in db.scalars(select(KnowledgeSnapshot)) if not s.data.get("cardScopeVersion"))
        data = deepcopy(raw.data)
        mapping = {e["id"]: "second-profile-" + e["id"] for e in data["evidence"]}
        for e in data["evidence"]: e["id"] = mapping[e["id"]]
        for c in data["characters"]: c["evidenceIds"] = [mapping[k] for k in c["evidenceIds"]]
        for f in data["facts"]:
            f["evidenceIds"] = [mapping[k] for k in f["evidenceIds"]]
            for claim in f.get("knowledge", []): claim["evidenceIds"] = [mapping[k] for k in claim["evidenceIds"]]
        model = db.get(ModelProfile, second["id"])
        revision = db.get(Revision, raw.revision_id)
        scoped_hash = snapshot_hash(raw.boundary, prefix_blocks(db, revision, raw.boundary, raw.owner_id), model)
        db.add(KnowledgeSnapshot(owner_id=raw.owner_id, book_id=raw.book_id, branch_id=raw.branch_id, revision_id=raw.revision_id,
            boundary=deepcopy(raw.boundary), scope_hash=scoped_hash, analyzer_version=raw.analyzer_version,
            profile_version=f"{model.id}:{model.version}", data=data, completeness="complete"))
        row = db.scalar(select(CharacterScopeRevision).where(CharacterScopeRevision.context["readerCorrection"].as_boolean().is_(True)))
        before = deepcopy(row.visible_data)
        row_id = row.id
        db.commit()
    assert client.put(BASE + f"/model-profiles/{second['id']}/default").status_code == 200
    after = get_card(client, recognized)
    assert after["recognition"]["version"] == 1 and after["identityId"] == recognized["card"]["identityId"]
    assert all(k.startswith("second-profile-") for k in after["recognition"]["selectedEvidenceIds"])
    opened = chat(client, recognized["demo"], recognized["anchor"], after, "second-profile-corrected-chat")
    assert opened["characterIdentityId"] == recognized["card"]["identityId"]
    with SessionLocal() as db:
        assert db.get(CharacterScopeRevision, row_id).visible_data == before


def changed_local_projection(client, recognized, *, altered_quote=False):
    """Hand-written projection unit fixture, separate from the actual worker gate."""
    from app.models import ModelProfile
    from app.knowledge import snapshot_hash
    from app.domain import prefix_blocks
    added = client.post(BASE + "/model-profiles", json={"name": "changed-local-id-mock", "baseUrl": "mock://deterministic",
        "modelId": "fixture-v1", "key": "TEST-KEY-NOT-REAL", "contextWindow": 32768, "callBudget": 100,
        "inputBudget": 10000000, "outputBudget": 1000000})
    assert added.status_code == 201
    model_id = added.json()["id"]
    with SessionLocal() as db:
        raw = next(s for s in db.scalars(select(KnowledgeSnapshot)) if not s.data.get("cardScopeVersion"))
        data = deepcopy(raw.data)
        char_ids = {c["id"]: "new-local-" + c["id"] for c in data["characters"]}
        proof_ids = {e["id"]: "new-proof-" + e["id"] for e in data["evidence"]}
        for e in data["evidence"]:
            e["id"] = proof_ids[e["id"]]
            if altered_quote: e["quote"] += "（不能沿用的不同引文）"
        for c in data["characters"]:
            c["id"] = char_ids[c["id"]]
            c["evidenceIds"] = [proof_ids[e] for e in c["evidenceIds"]]
        for f in data["facts"]:
            f["subjectId"] = char_ids[f["subjectId"]]
            f["evidenceIds"] = [proof_ids[e] for e in f["evidenceIds"]]
            f["knownBy"] = [char_ids[c] for c in f.get("knownBy", [])]
            for claim in f.get("knowledge", []):
                claim["characterId"] = char_ids[claim["characterId"]]
                claim["evidenceIds"] = [proof_ids[e] for e in claim["evidenceIds"]]
        model = db.get(ModelProfile, model_id); revision = db.get(Revision, raw.revision_id)
        db.add(KnowledgeSnapshot(owner_id=raw.owner_id, book_id=raw.book_id, branch_id=raw.branch_id,
            revision_id=raw.revision_id, boundary=deepcopy(raw.boundary),
            scope_hash=snapshot_hash(raw.boundary, prefix_blocks(db, revision, raw.boundary, raw.owner_id), model),
            analyzer_version=raw.analyzer_version, profile_version=f"{model.id}:{model.version}",
            data=data, completeness="complete"))
        db.commit()
    assert client.put(BASE + f"/model-profiles/{model_id}/default").status_code == 200
    return {**recognized, "card": {**recognized["card"], "id": char_ids[recognized["card"]["id"]]}}


def corrected_twice(client, recognized):
    assert correct(client, recognized).status_code == 200
    response = correct(client, recognized, payload(recognized, expectedVersion=1, identityStatus="uncertain",
        reason="第二版本保持当前原文范围的同名歧义。"), "second-owned-correction")
    assert response.status_code == 200, response.text
    return response.json()


def test_changed_local_id_keeps_same_identity_history_and_old_chat(client, recognized):
    old = chat(client, recognized["demo"], recognized["anchor"], recognized["card"], "old-local-chat")
    corrected_twice(client, recognized)
    with SessionLocal() as db:
        prior = db.get(RoleSession, old["id"])
        binding = (prior.snapshot_id, prior.character_id, prior.character_identity_id)
        before = {r.id: deepcopy(r.visible_data) for r in db.scalars(select(CharacterScopeRevision))}
    projected = changed_local_projection(client, recognized)
    card = get_card(client, projected)
    assert card["id"] != recognized["card"]["id"]
    assert card["identityId"] == recognized["card"]["identityId"]
    assert card["recognition"]["version"] == 2 and card["recognition"]["identityStatus"] == "uncertain"
    assert [r["version"] for r in card["recognition"]["history"]] == [2, 1]
    assert all(e.startswith("new-proof-") for e in card["recognition"]["selectedEvidenceIds"])
    with SessionLocal() as db:
        prior = db.get(RoleSession, old["id"])
        assert (prior.snapshot_id, prior.character_id, prior.character_identity_id) == binding
        assert all(db.get(CharacterScopeRevision, rid).visible_data == data for rid, data in before.items())


def test_changed_local_id_cannot_restart_cas_at_zero(client, recognized):
    corrected_twice(client, recognized)
    projected = changed_local_projection(client, recognized)
    projected["card"] = get_card(client, projected)
    response = correct(client, projected, payload(projected, expectedVersion=0), "stale-new-local-id")
    assert response.status_code == 409 and response.json()["code"] == "CHARACTER_CORRECTION_CONFLICT", response.text
    assert response.json()["details"]["currentVersion"] == 2
    third = correct(client, projected, payload(projected, expectedVersion=2), "explicit-third-local-id")
    assert third.status_code == 200 and third.json()["recognition"]["version"] == 3
    assert [r["version"] for r in third.json()["recognition"]["history"]] == [3, 2, 1]


def test_legacy_six_fields_translate_only_exact_old_identity_snapshot(client, recognized):
    corrected_twice(client, recognized)
    with SessionLocal() as db:
        rows = list(db.scalars(select(CharacterScopeRevision).where(CharacterScopeRevision.context["readerCorrection"].as_boolean().is_(True))))
        for row in rows: row.visible_data = {k: v for k, v in row.visible_data.items() if k != "proofs"}
        before = {r.id: deepcopy(r.visible_data) for r in rows}
        db.commit()
    projected = changed_local_projection(client, recognized)
    card = get_card(client, projected)
    assert card["recognition"]["version"] == 2 and card["identityId"] == recognized["card"]["identityId"]
    assert all(e.startswith("new-proof-") for e in card["recognition"]["selectedEvidenceIds"])
    with SessionLocal() as db:
        assert all(db.get(CharacterScopeRevision, rid).visible_data == data for rid, data in before.items())


@pytest.mark.parametrize("fault", ["different-current-quote", "legacy-missing-frozen-evidence"])
def test_changed_local_id_without_exact_supported_proof_is_typed_rejection(client, recognized, fault):
    corrected_twice(client, recognized)
    if fault == "legacy-missing-frozen-evidence":
        with SessionLocal() as db:
            rows = list(db.scalars(select(CharacterScopeRevision).where(CharacterScopeRevision.context["readerCorrection"].as_boolean().is_(True))))
            for row in rows:
                row.visible_data = {**{k: v for k, v in row.visible_data.items() if k != "proofs"}, "evidenceIds": ["missing-original-proof"]}
            db.commit()
    changed_local_projection(client, recognized, altered_quote=fault == "different-current-quote")
    response = client.get(f"{BASE}/books/{recognized['demo']['bookId']}/character-cards",
        params={"branchId": recognized["anchor"]["branchId"], "anchor": json.dumps(recognized["anchor"])})
    assert response.status_code == 422 and response.json()["code"] == "CHARACTER_EVIDENCE_INVALID", response.text


def test_same_identity_scope_duplicate_version_is_typed_rejection(client, recognized):
    assert correct(client, recognized).status_code == 200
    with SessionLocal() as db:
        original = db.scalar(select(CharacterScopeRevision).where(CharacterScopeRevision.context["readerCorrection"].as_boolean().is_(True)))
        db.add(CharacterScopeRevision(owner_id=original.owner_id, identity_id=original.identity_id,
            scope_kind=original.scope_kind, scope_id=original.scope_id, scope_key="reader-correction:duplicate-other-local-id",
            context=deepcopy(original.context), visible_data={**deepcopy(original.visible_data), "id": "duplicate-local-id"}, version=1))
        db.commit()
    response = client.get(f"{BASE}/books/{recognized['demo']['bookId']}/character-cards",
        params={"branchId": recognized["anchor"]["branchId"], "anchor": json.dumps(recognized["anchor"])})
    assert response.status_code == 422 and response.json()["code"] == "CHARACTER_CORRECTION_INVALID", response.text
