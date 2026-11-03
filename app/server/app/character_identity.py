"""Stable UUID references; narrative facts remain immutable, authorized scopes."""
from copy import deepcopy
import json
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select, text, func
from .models import CharacterIdentity, CharacterAlias, CharacterScopeRevision, KnowledgeSnapshot, SourceDocument, Revision
from .domain import canonical, digest, owned, prefix_blocks, utf16_slice, utf16_len
from .errors import Problem


def stable_id(owner, origin):
    return uuid5(NAMESPACE_URL, "story-reader-character-v1:" + owner + ":" + origin).hex


def occurrence(block_id, quote, label, start=0):
    position = quote.find(label)
    if not label or position < 0:
        return None
    return {"blockId": block_id, "start": start + len(quote[:position].encode("utf-16-le")) // 2,
        "originalLabel": label}


def reader_proof(character, data):
    if character.get("identityUncertain") or character.get("identityProofVersion", data.get("identityProofVersion")) != 2:
        return None
    proofs = {e["id"]: e for e in data.get("evidence", [])}
    valid = [proofs[k] for k in character.get("evidenceIds", []) if k in proofs]
    if not valid:
        return None
    first = valid[0]; anchor = first.get("anchor", {})
    if not all(k in anchor for k in ("blockId", "start", "end")):
        return None
    value = occurrence(anchor["blockId"], first.get("quote", ""), character["displayName"], anchor["start"])
    if not value:
        return None
    for other in data.get("characters", []):
        if other.get("id") == character.get("id") or other.get("displayName") != character.get("displayName"):
            continue
        other_proofs = [proofs[k] for k in other.get("evidenceIds", []) if k in proofs]
        if other_proofs:
            e = other_proofs[0]; at = e.get("anchor", {})
            if occurrence(at.get("blockId"), e.get("quote", ""), other["displayName"], at.get("start", 0)) == value:
                return None
    return value


def proof_key(character, data):
    value = reader_proof(character, data)
    return "proof:" + digest(canonical(value)) if value else None


def bind(db, owner, kind, scope, entity, *, origin=None, source_ref=None, identity_id=None, ambiguous=False):
    lock = int(digest(owner + ":character-registry")[:15], 16)
    db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock})
    alias = db.scalar(select(CharacterAlias).where(CharacterAlias.owner_id == owner, CharacterAlias.scope_kind == kind,
        CharacterAlias.scope_id == scope, CharacterAlias.entity_id == entity))
    if alias:
        return db.get(CharacterIdentity, alias.identity_id)
    if identity_id:
        identity = db.scalar(select(CharacterIdentity).where(CharacterIdentity.owner_id == owner, CharacterIdentity.id == identity_id))
        if not identity:
            raise Problem("CHARACTER_REFERENCE_INVALID", "人物来源引用不存在或不属于当前账号")
    else:
        origin = origin or kind + ":" + scope + ":" + entity
        identity = db.scalar(select(CharacterIdentity).where(CharacterIdentity.owner_id == owner, CharacterIdentity.origin_key == origin))
        if not identity:
            # A downgrade/upgraded publication can preserve the UUID while its
            # unavailable original proof is represented by publication metadata.
            identity = db.get(CharacterIdentity, stable_id(owner, origin))
            if identity and identity.owner_id != owner:
                raise Problem("CHARACTER_REFERENCE_INVALID", "人物来源不属于当前账号")
        if not identity:
            identity = CharacterIdentity(id=stable_id(owner, origin), owner_id=owner, origin_key=origin,
                source_ref=source_ref or {"kind": kind, "scopeId": scope, "entityId": entity}, ambiguous=ambiguous)
            db.add(identity); db.flush()
    db.add(CharacterAlias(owner_id=owner, identity_id=identity.id, scope_kind=kind, scope_id=scope, entity_id=entity))
    db.flush()
    return identity


def reader_identity(db, snapshot, character):
    # A published proof binding is created by the publisher from visible entities
    # and a precise paragraph span, never from a global surname/name lookup.
    source = db.scalar(select(SourceDocument).where(SourceDocument.owner_id == snapshot.owner_id,
        SourceDocument.book_id == snapshot.book_id))
    published = (source.source_map.get("characterBindings", []) if source else [])
    proof = reader_proof(character, snapshot.data)
    if proof and getattr(snapshot, 'revision_id', None):
        from .prose.accepted_basis import database_roots
        revision = owned(db, Revision, snapshot.revision_id, snapshot.owner_id)
        try:
            roots = database_roots(db, snapshot.owner_id, revision, snapshot.book_id)
            root = roots.get(proof['blockId'], proof['blockId'])
            if root != proof['blockId']:
                if revision.branch_id != snapshot.branch_id:
                    raise ValueError('character prefix revision scope')
                visible = next((part for part in prefix_blocks(db, revision, snapshot.boundary, snapshot.owner_id)
                    if roots.get(part['blockId'], part['blockId']) == root), None)
                end = proof['start'] + utf16_len(proof['originalLabel'])
                if (visible is None or end > utf16_len(visible['text'])
                        or utf16_slice(visible['text'], proof['start'], end) != proof['originalLabel']):
                    raise ValueError('character prefix literal occurrence')
                proof = {**proof, 'blockId':root}
        except (ValueError, KeyError, TypeError) as error:
            raise Problem('CHARACTER_REFERENCE_INVALID', '人物前文来源不一致，原记录仍保留') from error
    matches = {ref["identityId"] for ref in published if proof and
        ref.get("name") == proof["originalLabel"] and ref.get("blockId") == proof["blockId"] and
        ref.get("start") == proof["start"]}
    identity_id = next(iter(matches)) if len(matches) == 1 else None
    source_ref = {"kind": "reader-proof", "bookId": snapshot.book_id, "branchId": snapshot.branch_id, **proof} if proof else {
        "kind": "reader", "bookId": snapshot.book_id, "branchId": snapshot.branch_id, "entityId": character["id"]}
    return bind(db, snapshot.owner_id, "reader", snapshot.branch_id, character["id"], identity_id=identity_id,
        origin="proof:"+digest(canonical(proof)) if proof else None,
        source_ref=source_ref, ambiguous=bool(character.get("identityUncertain")))


def scoped_revision(db, identity, kind, scope, key, context, visible_data):
    scoped_key = kind + ":" + key
    row = db.scalar(select(CharacterScopeRevision).where(CharacterScopeRevision.owner_id == identity.owner_id,
        CharacterScopeRevision.identity_id == identity.id, CharacterScopeRevision.scope_key == scoped_key))
    if not row:
        version = db.scalar(select(func.count()).select_from(CharacterScopeRevision).where(
            CharacterScopeRevision.owner_id == identity.owner_id, CharacterScopeRevision.identity_id == identity.id,
            CharacterScopeRevision.scope_kind == kind, CharacterScopeRevision.scope_id == scope)) + 1
        row = CharacterScopeRevision(owner_id=identity.owner_id, identity_id=identity.id, scope_key=scoped_key,
            scope_kind=kind, scope_id=scope, context=deepcopy(context), visible_data=deepcopy(visible_data), version=version)
        db.add(row); db.flush()
    return row


def reader_fields(db, snapshot, character, card=None):
    identity = reader_identity(db, snapshot, character)
    context = {"bookId": snapshot.book_id, "readerBranchId": snapshot.branch_id, "revisionId": snapshot.revision_id,
        "anchor": snapshot.boundary}
    visible = card if card is not None else {k: deepcopy(v) for k, v in character.items()
        if k in {"id", "displayName", "evidenceIds", "available", "reasonCode", "identityUncertain"}}
    row = scoped_revision(db, identity, "reader", snapshot.branch_id, snapshot.scope_hash, context, visible)
    return {"identityId": identity.id, "scopeVersion": row.version, "scopeRevisionId": row.id,
        "sourceRef": deepcopy(identity.source_ref), "aliases": [character.get("displayName", "")]}


def resolve_reader_character(db, snapshot, value):
    # Only identities that are present in this exact snapshot are resolvable.
    local = next((c for c in snapshot.data.get("characters", []) if c["id"] == value), None)
    if local:
        return local["id"]
    matches = [c["id"] for c in snapshot.data.get("characters", []) if reader_identity(db, snapshot, c).id == value]
    if len(matches) != 1:
        raise Problem("ROLE_UNAVAILABLE", "此人物不在当前授权时间点或身份仍不明确")
    return matches[0]


def source_references(db, owner, source):
    anchor = source["anchor"]
    from .character_cards import filter_data
    from .models import Revision
    revision = db.get(Revision, anchor["revisionId"])
    rows = db.scalars(select(KnowledgeSnapshot).where(KnowledgeSnapshot.owner_id == owner,
        KnowledgeSnapshot.branch_id == anchor["branchId"], KnowledgeSnapshot.revision_id == anchor["revisionId"],
        KnowledgeSnapshot.completeness == "complete").order_by(KnowledgeSnapshot.created_at))
    refs = {}
    for row in rows:
        data = filter_data(row, anchor, revision)
        for character in data["characters"]:
            if character.get("identityUncertain"):
                continue
            proof = reader_proof(character, data)
            if not proof:
                continue  # An ambiguous broad citation cannot prove shared identity.
            identity = reader_identity(db, row, character)
            proofs = {e["id"]: e for e in data["evidence"]}
            evidence = [{"blockId": proofs[k]["anchor"]["blockId"], "quote": proofs[k]["quote"]}
                for k in character["evidenceIds"] if k in proofs]
            points = []
            for key in character["evidenceIds"]:
                if key not in proofs:
                    continue
                e = proofs[key]; at = e["anchor"]
                point = occurrence(at["blockId"], e["quote"], character["displayName"], at["start"])
                ambiguous = any(other["id"] != character["id"] and
                    any(occurrence(proofs[k]["anchor"]["blockId"], proofs[k]["quote"], other["displayName"], proofs[k]["anchor"]["start"]) == point
                        for k in other.get("evidenceIds", []) if k in proofs)
                    for other in data["characters"])
                if point and not ambiguous and point not in points:
                    points.append(point)
            refs[identity.id] = {"identityId": identity.id, "entityId": character["id"], "name": character["displayName"],
                "evidence": evidence, "proof": proof, "proofs": points}
    return list(refs.values())


def world_reference(world, entity):
    refs = world.source.get("characterRefs", [])
    explicit = entity.get("sourceCharacterId")
    actual = world_proof(world, entity)
    def accepted_point(ref):
        if not actual:
            return False
        # An explicit UUID already ties all of these verified occurrences to
        # this person. Non-explicit inference remains limited to the first one.
        if explicit:
            points = ref.get("proofs", [ref.get("proof")])
            return actual in points
        return not ref.get("proof") or ref["proof"] == actual
    matches = [r for r in refs if (not explicit or r["identityId"] == explicit) and
        (entity["name"] == r["name"] or r["name"] in entity.get("aliases", [])) and
        accepted_point(r) and
        any(p["blockId"] == e["blockId"] and r["name"] in e["quote"] and
            (p["quote"] in e["quote"] or e["quote"] in p["quote"]) and
            any(b["blockId"] == e["blockId"] and e["quote"] in b["text"] for b in world.source.get("blocks", []))
            for p in r["evidence"] for e in entity.get("evidence", []))
        and entity.get("source") == "original"]
    if explicit and len(matches) != 1:
        raise Problem("CHARACTER_REFERENCE_INVALID", "人物来源需要当前世界冻结范围内的精确证据")
    return matches[0]["identityId"] if len(matches) == 1 else None


def world_proof(world, entity):
    if entity.get("source") != "original":
        return None
    blocks = {b["blockId"]: b["text"] for b in world.source.get("blocks", [])}
    def first_proof(person):
        for evidence in person.get("evidence", []):
            quote = evidence.get("quote", ""); block = blocks.get(evidence.get("blockId"), "")
            position = block.find(quote)
            if not quote or position < 0 or block.find(quote, position + 1) >= 0:
                continue  # A repeated short quote has no unique source position.
            labels = [person["name"]] if person["name"] in quote else [a for a in person.get("aliases", []) if a in quote]
            if len(labels) != 1:
                continue
            return occurrence(evidence["blockId"], quote, labels[0], len(block[:position].encode("utf-16-le")) // 2)
    proof = first_proof(entity)
    if proof and any(other["id"] != entity["id"] and other["type"] == "character" and first_proof(other) == proof
                     for other in world.definition.get("entities", [])):
        return None
    return proof


def world_identity(db, world, entity):
    identity = world_reference(world, entity)
    proof = world_proof(world, entity)
    anchor = world.source.get("anchor") or {}
    source_ref = {"kind": "reader-proof", "bookId": anchor.get("bookId"), "branchId": anchor.get("branchId"), **proof} if proof else {
        "kind": "world", "worldId": world.id, "entityId": entity["id"]}
    return bind(db, world.owner_id, "world", world.id, entity["id"], identity_id=identity,
        origin="proof:" + digest(canonical(proof)) if proof else None, source_ref=source_ref)


def world_fields(db, world, entities, checkpoint=None, *, definition=None):
    result = deepcopy(entities)
    for entity in result:
        if entity.get("type") != "character":
            continue
        original = next(e for e in (checkpoint.definition if checkpoint else definition if definition is not None else world.definition)["entities"] if e["id"] == entity["id"])
        identity = world_identity(db, world, original)
        entity["identityId"] = identity.id
        entity["sourceRef"] = deepcopy(identity.source_ref)
        if checkpoint:
            from .world.state import public_payload
            visible = public_payload(entity, checkpoint.state, checkpoint.definition)
            entity.clear(); entity.update(visible)
            row = scoped_revision(db, identity, "world", world.id, digest(checkpoint.id + checkpoint.content_hash),
                {"worldId": world.id, "worldLineId": checkpoint.line_id, "checkpointId": checkpoint.id}, entity)
            entity.update(scopeVersion=row.version, scopeRevisionId=row.id)
    return result


def resolve_world_character(db, world, definition, value):
    if any(e["id"] == value and e["type"] == "character" for e in definition["entities"]):
        return value
    matches = [e["id"] for e in definition["entities"] if e["type"] == "character" and world_identity(db, world, e).id == value]
    if len(matches) != 1:
        raise Problem("WORLD_CHARACTER_INVALID", "此人物不属于当前世界与时间点")
    return matches[0]
