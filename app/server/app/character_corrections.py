"""Append-only recognition revisions; never mutate source facts or identities."""
from copy import deepcopy
from sqlalchemy import select, text
from .models import CharacterScopeRevision, KnowledgeSnapshot
from .domain import canonical, digest
from .errors import Problem


def revisions(db, snapshot, character_id, identity=None):
    from .character_identity import reader_identity
    character = next((c for c in snapshot.data.get("characters", []) if c.get("id") == character_id), None)
    if character is None:
        raise Problem("CHARACTER_REFERENCE_INVALID", "人物不属于当前阅读范围")
    identity = identity or reader_identity(db, snapshot, character)
    if identity.owner_id != snapshot.owner_id:
        raise Problem("CHARACTER_REFERENCE_INVALID", "人物不属于当前账号")
    rows = list(db.scalars(select(CharacterScopeRevision).where(
        CharacterScopeRevision.owner_id == snapshot.owner_id,
        CharacterScopeRevision.identity_id == identity.id,
        CharacterScopeRevision.scope_kind == "reader",
        CharacterScopeRevision.scope_id == snapshot.branch_id,
        CharacterScopeRevision.context["readerCorrection"].as_boolean().is_(True),
        CharacterScopeRevision.context["bookId"].astext == snapshot.book_id,
        CharacterScopeRevision.context["readerBranchId"].astext == snapshot.branch_id,
        CharacterScopeRevision.context["revisionId"].astext == snapshot.revision_id,
        CharacterScopeRevision.context["anchor"] == snapshot.boundary,
    ).order_by(CharacterScopeRevision.version.desc(), CharacterScopeRevision.created_at.desc())))
    versions = set()
    for row in rows:
        if type(row.version) is not int or row.version < 1 or row.version in versions:
            raise Problem("CHARACTER_CORRECTION_INVALID", "已保存的识别纠正版本无效，请重新确认当前资料")
        versions.add(row.version)
    return rows


def verified_revision_value(db, snapshot, character, row):
    """Old local IDs are display history, not identity. Never rewrite their rows.

    Earlier six-field revisions omitted frozen proofs. Only their precise old
    identity alias AND exact private snapshot may supply those proofs. They must
    still match current verified evidence; no name/global/profile fallback.
    """
    value = row.visible_data
    if not isinstance(value, dict):
        raise Problem("CHARACTER_CORRECTION_INVALID", "已保存的识别纠正格式无效")
    if "proofs" in value or value.get("id") == character["id"]:
        return verified_value(snapshot, character, value)
    from .models import CharacterAlias
    from .domain import validate_anchor, anchor_order, utf16_slice
    aliases = db.scalar(select(CharacterAlias.id).where(CharacterAlias.owner_id == snapshot.owner_id,
        CharacterAlias.identity_id == row.identity_id, CharacterAlias.scope_kind == "reader",
        CharacterAlias.scope_id == snapshot.branch_id, CharacterAlias.entity_id == value.get("id")))
    if not aliases:
        raise Problem("CHARACTER_EVIDENCE_INVALID", "这次纠正缺少原人物在精确阅读范围的证据")
    old_scopes = db.scalars(select(KnowledgeSnapshot).where(KnowledgeSnapshot.owner_id == snapshot.owner_id,
        KnowledgeSnapshot.book_id == snapshot.book_id, KnowledgeSnapshot.branch_id == snapshot.branch_id,
        KnowledgeSnapshot.revision_id == snapshot.revision_id, KnowledgeSnapshot.boundary == snapshot.boundary))
    proofs = {}
    for old in old_scopes:
        original = next((c for c in old.data.get("characters", []) if c.get("id") == value.get("id")), None)
        if original is None:
            continue
        try:
            verified = verified_value(old, original, value)
            frozen = verified["proofs"]
            for proof in frozen:
                anchor = proof["anchor"]
                if any(anchor.get(k) != snapshot.boundary[k] for k in ("bookId", "branchId", "revisionId")):
                    raise Problem("CHARACTER_EVIDENCE_INVALID", "识别证据不属于当前故事范围")
                _, revision, block = validate_anchor(db, anchor, snapshot.owner_id)
                if (anchor_order(revision, anchor) > anchor_order(revision, snapshot.boundary)
                        or utf16_slice(block.text, anchor["start"], anchor["end"]) != proof["quote"]):
                    raise Problem("CHARACTER_EVIDENCE_INVALID", "识别证据与原文不一致")
            proofs[canonical(frozen)] = frozen
        except (Problem, KeyError, TypeError):
            continue
    if len(proofs) != 1:
        raise Problem("CHARACTER_EVIDENCE_INVALID", "这次纠正缺少唯一的精确原文证据，请重新确认当前资料")
    return verified_value(snapshot, character, {**value, "proofs": next(iter(proofs.values()))})


def verified_value(snapshot, character, value):
    if not isinstance(value, dict) or not isinstance(value.get("displayName"), str) or not isinstance(value.get("reason"), str) or value.get("identityStatus") not in {"confirmed", "uncertain"}:
        raise Problem("CHARACTER_CORRECTION_INVALID", "已保存的识别纠正格式无效，请重新确认当前资料")
    allowed = set(character["evidenceIds"])
    ids = value.get("evidenceIds", [])
    evidence = {e["id"]: e for e in snapshot.data["evidence"]}
    if not isinstance(ids, list) or not 1 <= len(ids) <= 20 or any(not isinstance(k, str) for k in ids) or len(ids) != len(set(ids)):
        raise Problem("CHARACTER_EVIDENCE_INVALID", "请选择这张角色卡在当前阅读范围内的识别证据")
    proofs = value.get("proofs")
    if proofs is not None:
        if not isinstance(proofs, list) or len(proofs) != len(ids) or any(not isinstance(p, dict) or set(p) != {"quote", "anchor"} or not isinstance(p["quote"], str) or not p["quote"] or not isinstance(p["anchor"], dict) for p in proofs):
            raise Problem("CHARACTER_EVIDENCE_INVALID", "已保存的识别证据格式无效")
        # Analysis IDs may change with the selected model. Only the same local
        # character's exact frozen quote AND full anchor can carry a correction.
        mapped = []
        for proof in proofs:
            matching = sorted(k for k in allowed & evidence.keys() if evidence[k]["quote"] == proof["quote"] and evidence[k]["anchor"] == proof["anchor"])
            if not matching:
                raise Problem("CHARACTER_EVIDENCE_INVALID", "当前角色资料尚未包含这次纠正的精确已读证据")
            mapped.append(matching[0])
        ids = list(dict.fromkeys(mapped))
    elif not set(ids).issubset(allowed & evidence.keys()):
        raise Problem("CHARACTER_EVIDENCE_INVALID", "请选择这张角色卡在当前阅读范围内的识别证据")
    name = value["displayName"].strip()
    aliases = value.get("aliases", [])
    if not name or len(name) > 100 or not isinstance(aliases, list) or len(aliases) > 20 or any(not isinstance(a, str) or not a.strip() or len(a) > 100 for a in aliases):
        raise Problem("CHARACTER_ALIAS_INVALID", "人物名称和别名不能为空或超过100字")
    aliases = list(dict.fromkeys([name, *[a.strip() for a in aliases]]))
    if len(aliases) > 20:
        raise Problem("CHARACTER_ALIAS_INVALID", "名称和别名合计最多20项")
    quotes = [evidence[k]["quote"] for k in ids]
    if any(not any(label in quote for quote in quotes) for label in aliases):
        raise Problem("CHARACTER_ALIAS_INVALID", "名称和别名需要所选已读原文的字面证据，不能引入未来身份")
    if not value["reason"].strip() or len(value["reason"]) > 600:
        raise Problem("CHARACTER_CORRECTION_REASON_REQUIRED", "请说明这次纠正的原因")
    return {"id": character["id"], "displayName": name, "aliases": aliases,
        "evidenceIds": ids, "proofs": deepcopy(proofs) if proofs is not None else [{"quote": evidence[k]["quote"], "anchor": deepcopy(evidence[k]["anchor"])} for k in ids],
        "identityStatus": value["identityStatus"], "reason": value["reason"].strip()}


def recognition(db, snapshot, character):
    rows = revisions(db, snapshot, character["id"])
    for row in rows:
        if type(row.version) is not int or row.version < 1:
            raise Problem("CHARACTER_CORRECTION_INVALID", "已保存的识别纠正版本无效")
        verified_revision_value(db, snapshot, character, row)
    latest = rows[0] if rows else None
    values = verified_revision_value(db, snapshot, character, latest) if latest else None
    evidence = {e["id"]: e for e in snapshot.data["evidence"]}
    # Only this already filtered snapshot supplies quotes/aliases, including full
    # mode's explicitly chosen scope. Never query an unfiltered global card.
    aliases = [character["displayName"]]
    for fact in snapshot.data["facts"]:
        if fact.get("subjectId") == character["id"] and fact.get("status") == "confirmed" and fact.get("predicate") in {"identity", "secret_identity", "alias"}:
            label = fact.get("value")
            if isinstance(label, str) and label and any(label in evidence[k]["quote"] for k in fact.get("evidenceIds", []) if k in evidence):
                aliases.append(label)
    if values:
        aliases = values["aliases"]
    status = values["identityStatus"] if values else ("uncertain" if character.get("identityUncertain") or
        sum(c["displayName"] == character["displayName"] for c in snapshot.data["characters"]) > 1 else "confirmed")
    ids = character["evidenceIds"]
    return {"version": latest.version if latest else 0, "aliases": list(dict.fromkeys(aliases)), "identityStatus": status,
        "evidence": [deepcopy(evidence[k]) for k in ids if k in evidence],
        "selectedEvidenceIds": deepcopy(values["evidenceIds"] if values else ids), "reason": values["reason"] if values else None,
        "scope": {"bookId": snapshot.book_id, "readerBranchId": snapshot.branch_id,
                  "revisionId": snapshot.revision_id, "anchor": deepcopy(snapshot.boundary)},
        "history": [{"version": r.version, "displayName": r.visible_data["displayName"],
                     "aliases": deepcopy(r.visible_data["aliases"]), "identityStatus": r.visible_data["identityStatus"],
                     "reason": r.visible_data["reason"], "createdAt": r.created_at.isoformat()} for r in rows]}


def apply_corrections(db, snapshot, persist=False):
    if not snapshot or snapshot.data.get("readerCorrectionApplied"):
        return snapshot
    data = deepcopy(snapshot.data)
    versions = []
    from .character_identity import reader_identity
    for character in data["characters"]:
        rows = revisions(db, snapshot, character["id"])
        if not rows:
            continue
        value = verified_revision_value(db, snapshot, character, rows[0])
        # Establish the stable, uncorrected alias before changing a local label.
        # Recognition never rebinds an existing UUID to another same-name person.
        reader_identity(db, snapshot, character)
        character.update(displayName=value["displayName"],
            readerAliases=value["aliases"], identityUncertain=value["identityStatus"] == "uncertain",
            readerIdentityConfirmed=value["identityStatus"] == "confirmed", readerCorrectionVersion=rows[0].version)
        versions.append(rows[0].version)
    if not versions:
        return snapshot
    data.update(readerCorrectionApplied=True, readerCorrectionVersion=max(versions))
    hashed = digest(canonical({"type": "reader-recognition-scope-v1", "base": snapshot.scope_hash, "data": data}))
    derived = db.scalar(select(KnowledgeSnapshot).where(KnowledgeSnapshot.owner_id == snapshot.owner_id, KnowledgeSnapshot.scope_hash == hashed))
    if not derived:
        derived = KnowledgeSnapshot(owner_id=snapshot.owner_id, book_id=snapshot.book_id, branch_id=snapshot.branch_id,
            revision_id=snapshot.revision_id, boundary=deepcopy(snapshot.boundary), scope_hash=hashed,
            profile_version=snapshot.profile_version, analyzer_version=snapshot.analyzer_version,
            data=data, completeness=snapshot.completeness)
        if persist:
            db.add(derived)
            db.flush()
    return derived


def save_correction(db, snapshot, character, identity, value):
    if snapshot.completeness != "complete":
        raise Problem("AI_CONTEXT_NOT_READY", "当前阅读范围的证据还在整理，请稍后纠正")
    lock = int(digest(snapshot.owner_id + ":reader-recognition:" + canonical(snapshot.boundary) + ":" + identity.id)[:15], 16)
    db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock})
    rows = revisions(db, snapshot, character["id"], identity)
    version = rows[0].version if rows else 0
    if version != value["expectedVersion"]:
        raise Problem("CHARACTER_CORRECTION_CONFLICT", "这张角色卡已在其他设备纠正，请载入最新资料再确认", 409,
                      {"currentVersion": version})
    verified = verified_value(snapshot, character, value)
    row = CharacterScopeRevision(owner_id=snapshot.owner_id, identity_id=identity.id, scope_kind="reader",
        scope_id=snapshot.branch_id, scope_key="reader-correction:" + digest(canonical([snapshot.boundary, identity.id, version + 1])),
        context={"readerCorrection": True, "bookId": snapshot.book_id, "readerBranchId": snapshot.branch_id,
                 "revisionId": snapshot.revision_id, "anchor": deepcopy(snapshot.boundary)},
        visible_data=verified, version=version + 1)
    db.add(row)
    db.flush()
    return row
