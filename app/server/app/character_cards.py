"""Character cards derive exclusively from verified, reader-visible evidence."""
import re
from sqlalchemy import select, text
from .models import Book, Branch, Revision, ContentBlock, KnowledgeSnapshot, AnalysisChunk, UserSettings, RoleSession, Task
from .domain import owned, validate_anchor, anchor_order, make_anchor, utf16_len, canonical, digest, prefix_blocks
from .errors import Problem
from .knowledge import safe_scope, visible_characters, require_role_character, chunks_for, chunk_hash, merge_analysis, snapshot_hash, ANALYZER


def preferences(db, owner, lock=False):
    query = select(UserSettings).where(UserSettings.owner_id == owner)
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise Problem("AUTH_REQUIRED", "请重新登录", 401)
    return row


def tail_anchor(db, book, branch, revision):
    if not revision.manifest or not revision.manifest[-1]["blocks"]:
        raise Problem("AI_CONTEXT_NOT_READY", "这本书还没有可以整理的正文")
    block = owned(db, ContentBlock, revision.manifest[-1]["blocks"][-1], book.owner_id)
    return make_anchor(book.id, branch.id, revision.id, block, utf16_len(block.text))


def in_scope(value, boundary, revision):
    try:
        return (all(value.get(k) == boundary[k] for k in ("bookId", "branchId", "revisionId"))
            and anchor_order(revision, value) <= anchor_order(revision, boundary))
    except (Problem, KeyError, TypeError, AttributeError):
        return False


def literal_fact(fact, quotes, names):
    """A whole-book extractor cannot bind an unseen value to an earlier quote."""
    value, predicate = fact.get("value"), fact.get("predicate", "")
    supported = {"alive", "speaking_style", "speech_style", "voice_style", "secret_identity", "identity", "occupation",
        "role", "personality", "trait", "relationship", "relation", "location", "revived", "resurrected"}
    if predicate not in supported:
        return False
    joined = "\n".join(quotes)
    if isinstance(value, str):
        return bool(value.strip() and value in joined)
    if predicate == "alive" and type(value) is bool:
        name = names.get(fact.get("subjectId"), "")
        if not name:
            return False
        # Readable explicit state phrases, not narrator metadata or model labels.
        pattern = re.escape(name) + (r"[^。！？\n]{0,16}(?:仍然活着|还活着|活着|生还|活了下来|复活|活过来|重获生命)" if value else r"[^。！？\n]{0,16}(?:已经死亡|死亡|死于|已经死去|已经不在|已离世|气息断绝|咽气|身亡|丧命|断气|已死|死了)")
        matches = list(re.finditer(pattern, joined))
        return any(not re.search(r"(?:没有|并未|并不|并非|不是|未曾)[^，。！？\n]{0,4}(?:活着|生还|死亡|死于|死去|不在|离世|复活|活过来|重获生命|气息断绝)", match.group())
            and not re.search(r"如果|假如|若是|也许|可能|据说|传言", joined[max(0, match.start()-8):match.end()]) for match in matches)
    if predicate in {"revived", "resurrected"} and value is True:
        name = names.get(fact.get("subjectId"), "")
        return bool(name and re.search(re.escape(name) + r"[^。！？\n]{0,16}(?:复活|活过来|重获生命)", joined))
    if type(value) is int:
        return str(value) in joined
    return False


def filter_data(source, boundary, revision):
    evidence = {e["id"]: e for e in source.data["evidence"] if in_scope(e.get("anchor"), boundary, revision)}
    chars = []
    for original in source.data["characters"]:
        ids = [eid for eid in original["evidenceIds"] if eid in evidence]
        if not ids or not any(original["displayName"] in evidence[eid]["quote"] for eid in ids):
            continue
        chars.append({**original, "evidenceIds": ids})
    names = {c["id"]: c["displayName"] for c in chars}
    facts = []
    for original in source.data["facts"]:
        ids = original.get("evidenceIds", [])
        if (original.get("subjectId") not in names or not ids or not set(ids).issubset(evidence)
                or not in_scope(original.get("readerRevealAnchor"), boundary, revision)):
            continue
        supported = literal_fact(original, [evidence[eid]["quote"] for eid in ids], names)
        if original.get("predicate") == "alive" and (original.get("status") != "confirmed" or type(original.get("value")) is not bool or not supported):
            # Unresolved current life state must remain unresolved. Dropping it
            # would turn the legacy no-fact default into an invented alive state.
            facts.append({**original, "status": "uncertain", "value": None, "knownBy": [], "knowledge": []})
            continue
        if not supported:
            continue
        knowledge = [k for k in original.get("knowledge", []) if k.get("characterId") in names
            and k.get("evidenceIds") and set(k["evidenceIds"]).issubset(evidence)
            and in_scope(k.get("learnedAt"), boundary, revision)]
        facts.append({**original, "knowledge": knowledge, "knownBy": [k["characterId"] for k in knowledge]})
    return {"characters": chars, "facts": facts, "evidence": list(evidence.values()),
        "cardScopeVersion": 1,
        "identityProofVersion": source.data.get("identityProofVersion", 0),
        "summary": "\n".join(e["quote"] for e in evidence.values()), "unresolved": []}


def scope_snapshot(db, owner, book_id, branch_id, anchor, profile, mode, persist=False, *, corrected=True):
    book = owned(db, Book, book_id, owner)
    branch, revision, _ = validate_anchor(db, anchor, owner)
    if branch.book_id != book.id or branch.id != branch_id:
        raise Problem("ANCHOR_INVALID", "角色卡不属于当前故事线")
    if branch.status != "active" or book.status != "active":
        raise Problem("BRANCH_ARCHIVED", "请先恢复归档内容", 409)
    boundary = tail_anchor(db, book, branch, revision) if mode == "full" else anchor
    if mode == "progress":
        safe_scope(db, boundary, revision, owner)
    rows = list(db.scalars(select(KnowledgeSnapshot).where(KnowledgeSnapshot.owner_id == owner,
        KnowledgeSnapshot.branch_id == branch.id, KnowledgeSnapshot.revision_id == revision.id,
        KnowledgeSnapshot.profile_version == f"{profile.id}:{profile.version}", KnowledgeSnapshot.completeness == "complete")))
    verified_rows = []
    for row in rows:
        if row.data.get("cardScopeVersion") == 1 or row.analyzer_version != ANALYZER:
            continue
        try:
            _, source_revision, _ = validate_anchor(db, row.boundary, owner)
            expected = snapshot_hash(row.boundary, prefix_blocks(db, revision, row.boundary, owner), profile)
            if source_revision.id == revision.id and row.scope_hash == expected:
                verified_rows.append(row)
        except Problem:
            continue
    rows = verified_rows
    tail = tail_anchor(db, book, branch, revision)
    complete = next((s for s in rows if s.boundary == tail), None)
    source = complete or (max(rows, key=lambda s: anchor_order(revision, s.boundary)) if rows and mode == "progress" else None)
    scope_ready = bool(source and in_scope(boundary, source.boundary, revision))
    if not complete:
        full_chunks = chunks_for(prefix_blocks(db, revision, tail, owner), profile)
        records = {chunk_hash(chunk, tail, profile): chunk for chunk in full_chunks}
        cached = {row.cache_hash: row for row in db.scalars(select(AnalysisChunk).where(AnalysisChunk.owner_id == owner,
            AnalysisChunk.branch_id == branch.id, AnalysisChunk.cache_hash.in_(records))) } if records else {}
        parts = [cached[h].data for h in records if h in cached]
        if parts and not scope_ready:
            source = KnowledgeSnapshot(owner_id=owner, book_id=book.id, branch_id=branch.id, revision_id=revision.id,
                boundary=tail, scope_hash=digest(canonical({"type": "partial-card-index-v1", "anchor": tail,
                    "parts": [h for h in records if h in cached]})), profile_version=f"{profile.id}:{profile.version}",
                analyzer_version=ANALYZER, data=merge_analysis(parts), completeness="partial")
            bound_order = anchor_order(revision, boundary)
            needed = []
            for h, chunk in records.items():
                for part in chunk:
                    block = owned(db, ContentBlock, part["blockId"], owner)
                    start = utf16_len(block.text[:part.get("sourceStart", 0)])
                    point = make_anchor(book.id, branch.id, revision.id, block, start)
                    if anchor_order(revision, point) < bound_order:
                        needed.append(h)
                        break
            scope_ready = all(h in cached for h in needed)
    if not source:
        return None, boundary, bool(complete)
    data = filter_data(source, boundary, revision)
    hashed = digest(canonical({"type": "character-card-scope-v1", "source": source.scope_hash, "boundary": boundary, "data": data}))
    snapshot = db.scalar(select(KnowledgeSnapshot).where(KnowledgeSnapshot.owner_id == owner, KnowledgeSnapshot.scope_hash == hashed))
    if not snapshot:
        snapshot = KnowledgeSnapshot(owner_id=owner, book_id=book.id, branch_id=branch.id, revision_id=revision.id,
            boundary=boundary, scope_hash=hashed, profile_version=source.profile_version, analyzer_version=ANALYZER,
            data=data, completeness="complete" if scope_ready else "partial")
        if persist:
            db.add(snapshot)
            db.flush()
    if corrected:
        from .character_corrections import apply_corrections
        snapshot = apply_corrections(db, snapshot, persist)
    return snapshot, boundary, bool(complete)


def cards(snapshot):
    if not snapshot:
        return []
    evidence = {e["id"]: e for e in snapshot.data["evidence"]}
    names = {c["id"]: c["displayName"] for c in snapshot.data["characters"]}
    reasons = {"ROLE_DECEASED": "当前剧情中已离世，暂不提供对话", "ROLE_STATE_UNCERTAIN": "当前人物状态尚不明确，暂不提供对话", "ROLE_IDENTITY_UNCERTAIN": "同名人物的身份线索尚不明确，暂不提供对话"}
    result = []
    for character in visible_characters(snapshot):
        quotes = [evidence[eid]["quote"] for eid in character["evidenceIds"] if eid in evidence]
        sentences = [s.strip() for quote in quotes for s in re.split(r"(?<=[。！？])", quote) if character["displayName"] in s]
        description = sentences[0][:180] if sentences else "已在当前故事中登场"
        traits = []
        name = re.escape(character["displayName"])
        for word in ("简短", "温和", "幽默", "谨慎", "沉默", "果断", "冷静", "勇敢", "敏锐", "口语", "正式"):
            attribute = name + r"(?:说话|言语|性格|为人|行事|一向|向来|很|十分|总是|是|显得|尤其|比较|有些){0,3}" + re.escape(word)
            modifier = re.escape(word) + "的" + name
            if any((re.search(attribute, s) or re.search(modifier, s))
                    and not re.search(r"(?:不|非|没有|从未|缺乏|很少)[^，。；！？]{0,4}" + word, s) for s in sentences):
                traits.append(word)
        relations = [s[:140] for s in sentences if any(other != character["displayName"] and other in s for other in names.values())
            and re.search("父亲|母亲|姐姐|妹妹|哥哥|弟弟|朋友|师父|师傅|妻子|丈夫|同伴|恋人", s)]
        available = character["available"] and snapshot.completeness == "complete"
        result.append({"id": character["id"], "name": character["displayName"], "description": description,
            "traits": list(dict.fromkeys(traits)), "relations": list(dict.fromkeys(relations))[:4],
            "available": available, "reason": reasons.get(character.get("reasonCode")) if character["available"] is False else ("当前阅读段落的角色资料仍在整理" if not available else None),
            "reasonCode": character.get("reasonCode") or ("AI_CONTEXT_NOT_READY" if not available else None)})
    return result


def ensure_session_scope(db, session, owner):
    if session.status == "needs_refresh":
        raise Problem("ROLE_NEEDS_REFRESH", "来源已移除，请显式重新整理角色后开始新会话", 409)
    if not session.follow_progress:
        return
    pref = preferences(db, owner)
    if not pref or pref.character_mode != session.knowledge_mode:
        raise Problem("ROLE_SCOPE_CHANGED", "角色阅读模式已变化，请重新打开角色卡", 409)
    _, revision, _ = validate_anchor(db, session.pinned_anchor, owner)
    if session.knowledge_mode == "progress":
        safe_scope(db, session.pinned_anchor, revision, owner)
    snapshot = owned(db, KnowledgeSnapshot, session.snapshot_id, owner)
    if snapshot.boundary != session.pinned_anchor or snapshot.branch_id != session.branch_id or snapshot.revision_id != session.pinned_revision_id:
        raise Problem("ROLE_SCOPE_CHANGED", "角色阅读范围已变化，请重新打开角色卡", 409)
    if snapshot.completeness != "complete":
        raise Problem("AI_CONTEXT_NOT_READY", "当前阅读段落的角色资料仍在整理")
    require_role_character(snapshot, session.character_id, db)


def ensure_task_session_scope(db, task, session):
    ensure_session_scope(db, session, task.owner_id)
    if session.follow_progress and (task.payload.get("anchor") != session.pinned_anchor
            or task.checkpoint.get("snapshotId") != session.snapshot_id):
        raise Problem("ROLE_SCOPE_CHANGED", "这条消息的角色范围已变化，请重新打开角色卡", 409)


def start_character_index(db, owner, book_id, branch_id, retry=False):
    from .story_flow import default_profile, create_flow, flow_result
    from .schemas import Operation
    book = owned(db, Book, book_id, owner)
    branch = owned(db, Branch, branch_id, owner)
    if branch.book_id != book.id or branch.status != "active" or book.status != "active":
        raise Problem("ANCHOR_INVALID", "请在有效故事线中整理角色")
    revision = owned(db, Revision, branch.head_revision_id, owner)
    profile = default_profile(db, owner)
    anchor = tail_anchor(db, book, branch, revision)
    db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": int(digest(owner + ":character-index:" + canonical(anchor))[:15], 16)})
    cached = db.scalar(select(KnowledgeSnapshot).where(KnowledgeSnapshot.owner_id == owner,
        KnowledgeSnapshot.branch_id == branch.id, KnowledgeSnapshot.revision_id == revision.id,
        KnowledgeSnapshot.boundary == anchor, KnowledgeSnapshot.profile_version == f"{profile.id}:{profile.version}",
        KnowledgeSnapshot.completeness == "complete", KnowledgeSnapshot.analyzer_version == ANALYZER,
        KnowledgeSnapshot.scope_hash == snapshot_hash(anchor, prefix_blocks(db, revision, anchor, owner), profile)))
    rows = db.scalars(select(Task).where(Task.owner_id == owner, Task.task_type == "story_flow").order_by(Task.created_at.desc()))
    existing = next((t for t in rows if t.payload.get("flowKind") == "character_index"
        and t.payload.get("profileId") == profile.id and t.payload.get("profileVersion") == profile.version
        and t.payload.get("operation", {}).get("anchor") == anchor), None)
    if cached and existing and existing.state not in {"failed", "cancelled"}:
        return {**flow_result(existing), "indexState": "ready", "indexTaskId": existing.id, "cached": True}
    if cached:
        return {"id": None, "state": "succeeded", "indexState": "ready", "indexTaskId": None, "cached": True}
    if existing and existing.state not in {"succeeded", "failed", "cancelled"}:
        return {**flow_result(existing), "indexState": "indexing", "indexTaskId": existing.id, "cached": True}
    if existing and existing.state in {"failed", "cancelled"} and (not retry or existing.error_code == "PROVIDER_STATUS_UNKNOWN"):
        return {**flow_result(existing), "indexState": "failed", "indexTaskId": existing.id, "cached": True, "errorCode": existing.error_code}
    operation = Operation(operation="analysis", profileId=profile.id, anchor=anchor, expectedHeadRevisionId=revision.id)
    created = create_flow(db, owner, operation, kind="character_index")
    return {**created, "indexState": "indexing", "indexTaskId": created["id"], "cached": False}
