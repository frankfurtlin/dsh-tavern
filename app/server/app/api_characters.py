import json
from .model_budget import call_budget_exceeded, input_budget_exceeded, output_budget_exceeded
from typing import Literal
from datetime import timedelta
from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictBool
from sqlalchemy import select
from .db import get_db
from .security import current_user
from .schemas import Strict, Anchor, Operation, TaskInput
from .models import RoleSession, Task, ModelProfile, OperationPlan, Consent, KnowledgeSnapshot, uid, now
from .domain import owned, validate_anchor, anchor_order, canonical, digest, idempotent
from .errors import Problem
from .completion_policy import planned_stages, completion_policy
from .knowledge import require_role_character, ANALYZER
from .story_flow import default_profile, stage_budget
from .character_cards import preferences, scope_snapshot, cards, ensure_session_scope, start_character_index, tail_anchor

router = APIRouter()


class PreferenceInput(Strict):
    mode: Literal["progress", "full"]
    expectedVersion: int = Field(ge=1, strict=True)


@router.get("/character-preferences")
def preference_read(user=Depends(current_user), db=Depends(get_db)):
    row = preferences(db, user.id)
    return {"mode": row.character_mode, "version": row.character_mode_version}


@router.put("/character-preferences")
def preference_write(data: PreferenceInput, user=Depends(current_user), db=Depends(get_db)):
    row = preferences(db, user.id, lock=True)
    if row.character_mode_version != data.expectedVersion:
        raise Problem("CHARACTER_PREFERENCE_CONFLICT", "角色阅读模式已在其他设备更新", 409,
            {"currentVersion": row.character_mode_version})
    row.character_mode, row.character_mode_version = data.mode, row.character_mode_version + 1
    db.commit()
    return {"mode": row.character_mode, "version": row.character_mode_version}


@router.get("/books/{book_id}/character-cards")
def character_cards(book_id: str, branchId: str, anchor: str, user=Depends(current_user), db=Depends(get_db)):
    from .models import Book
    try:
        boundary = Anchor.model_validate(json.loads(anchor)).model_dump()
    except Exception:
        raise Problem("ANCHOR_INVALID", "角色阅读位置无效")
    # Verify requested ownership before returning NO_PROVIDER/deferred status.
    owned(db, Book, book_id, user.id)
    branch, revision, _ = validate_anchor(db, boundary, user.id)
    if boundary["bookId"] != book_id or branch.id != branchId:
        raise Problem("ANCHOR_INVALID", "角色卡不属于当前故事线")
    pref = preferences(db, user.id)
    try:
        profile = default_profile(db, user.id)
    except Problem as error:
        if error.code != "NO_PROVIDER":
            raise
        return {"items": [], "mode": pref.character_mode, "modeVersion": pref.character_mode_version,
            "indexState": "deferred", "indexTaskId": None, "reason": "配置默认模型后会整理角色卡"}
    snapshot, _, complete = scope_snapshot(db, user.id, book_id, branchId, boundary, profile, pref.character_mode)
    index_scope = digest(canonical({"bookId": book_id, "branchId": branchId, "revisionId": revision.id,
        "profileId": profile.id, "profileVersion": profile.version, "analyzerVersion": ANALYZER,
        "completionPolicy": completion_policy(profile)}))
    state, index_task = "ready" if complete else "missing", None
    if not complete:
        from .models import Book
        book = owned(db, Book, book_id, user.id)
        tail = tail_anchor(db, book, branch, revision)
        rows = db.scalars(select(Task).where(Task.owner_id == user.id, Task.task_type == "story_flow").order_by(Task.created_at.desc()))
        existing = next((t for t in rows if t.payload.get("flowKind") == "character_index"
            and t.payload.get("profileId") == profile.id and t.payload.get("profileVersion") == profile.version
            and t.payload.get("operation", {}).get("anchor") == tail), None)
        if existing and existing.state != "succeeded":
            state = "failed" if existing.state in {"failed", "cancelled"} else "indexing"
            index_task = existing.id
    from .character_identity import reader_fields
    items = cards(snapshot)
    for card in items:
        character = next(c for c in snapshot.data["characters"] if c["id"] == card["id"])
        card.update(reader_fields(db, snapshot, character, card))
        from .character_corrections import recognition
        card["recognition"] = recognition(db, snapshot, character)
    db.commit()
    return {"items": items, "mode": pref.character_mode, "modeVersion": pref.character_mode_version,
        "indexState": state, "indexTaskId": index_task, "indexScope": index_scope}


from .character_correction_schemas import CharacterRecognitionInput


@router.put("/books/{book_id}/character-cards/{character_id}/recognition")
def correct_recognition(book_id: str, character_id: str, data: CharacterRecognitionInput, request: Request,
                        user=Depends(current_user), db=Depends(get_db)):
    from .models import Book
    from .character_identity import reader_identity, reader_fields, resolve_reader_character
    from .character_corrections import save_correction, apply_corrections, recognition
    # Lock lifecycle before registry locks: DELETE uses Book -> dependent rows.
    owned(db, Book, book_id, user.id, lock=True)
    boundary = data.anchor.model_dump()
    branch, _, _ = validate_anchor(db, boundary, user.id)
    if boundary["bookId"] != book_id:
        raise Problem("ANCHOR_INVALID", "角色纠正不属于当前小说")
    def save():
        pref = preferences(db, user.id)
        profile = default_profile(db, user.id)
        base, _, _ = scope_snapshot(db, user.id, book_id, branch.id, boundary, profile, pref.character_mode, corrected=False)
        if not base:
            raise Problem("AI_CONTEXT_NOT_READY", "当前阅读范围还没有可纠正的角色证据")
        local = resolve_reader_character(db, base, character_id)
        character = next(c for c in base.data["characters"] if c["id"] == local)
        identity = reader_identity(db, base, character)
        save_correction(db, base, character, identity, data.model_dump())
        updated = apply_corrections(db, base, persist=True)
        card = next(c for c in cards(updated) if c["id"] == local)
        updated_character = next(c for c in updated.data["characters"] if c["id"] == local)
        card.update(reader_fields(db, updated, updated_character, card))
        card["recognition"] = recognition(db, updated, updated_character)
        return card
    return idempotent(db, user.id, "character-recognition:" + book_id + ":" + character_id,
                      request.headers.get("idempotency-key"), data.model_dump(), save)


class IndexInput(Strict):
    branchId: str
    retry: StrictBool = False


@router.post("/books/{book_id}/character-index", status_code=202)
def character_index(book_id: str, data: IndexInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    return idempotent(db, user.id, "character-index:" + book_id, request.headers.get("idempotency-key"),
        data.model_dump(), lambda: start_character_index(db, user.id, book_id, data.branchId, data.retry))


class ChatInput(Strict):
    anchor: Anchor
    characterId: str = Field(min_length=1, max_length=100)


@router.post("/books/{book_id}/character-chats", status_code=201)
def character_chat(book_id: str, data: ChatInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    from .api_ai import session_result
    def create():
        from .models import Book
        owned(db, Book, book_id, user.id)
        boundary = data.anchor.model_dump()
        branch, _, _ = validate_anchor(db, boundary, user.id)
        if branch.book_id != book_id:
            raise Problem("ANCHOR_INVALID", "角色卡不属于这本书")
        pref = preferences(db, user.id, lock=True)
        profile = default_profile(db, user.id)
        snapshot, scope, _ = scope_snapshot(db, user.id, book_id, boundary["branchId"], boundary, profile, pref.character_mode, persist=True)
        if not snapshot or snapshot.completeness != "complete":
            raise Problem("AI_CONTEXT_NOT_READY", "角色卡仍在整理，请稍后再打开")
        from .character_identity import resolve_reader_character, reader_identity
        local_id = resolve_reader_character(db, snapshot, data.characterId)
        character = require_role_character(snapshot, local_id, db)
        identity = reader_identity(db, snapshot, character)
        _, revision, _ = validate_anchor(db, scope, user.id)
        existing = db.scalars(select(RoleSession).where(RoleSession.owner_id == user.id,
            RoleSession.book_id == book_id, RoleSession.branch_id == scope["branchId"], RoleSession.card_id == local_id,
            RoleSession.follow_progress.is_(True), RoleSession.knowledge_mode == pref.character_mode,
            RoleSession.pinned_revision_id == scope["revisionId"], RoleSession.status == "active").order_by(RoleSession.created_at.desc()).with_for_update())
        session = next((s for s in existing if anchor_order(revision, s.pinned_anchor) <= anchor_order(revision, scope)
            and (not snapshot.data.get("readerCorrectionApplied") or s.snapshot_id == snapshot.id)), None)
        if session and session.pending_task_id:
            raise Problem("TURN_IN_PROGRESS", "请等待当前回复保存", 409)
        if not session:
            session = RoleSession(id=uid(), owner_id=user.id, book_id=book_id, branch_id=scope["branchId"],
                pinned_revision_id=scope["revisionId"], pinned_anchor=scope, character_id=character["id"],
                character_name=character["displayName"], snapshot_id=snapshot.id, follow_progress=True,
                knowledge_mode=pref.character_mode, card_id=local_id, character_identity_id=identity.id)
            db.add(session)
        else:
            session.pinned_anchor, session.snapshot_id = scope, snapshot.id
            session.character_identity_id = identity.id
        db.flush()
        return session_result(db, session)
    return idempotent(db, user.id, "character-chat:" + book_id, request.headers.get("idempotency-key"), data.model_dump(), create)


class SendInput(Strict):
    message: str = Field(min_length=1, max_length=4000)
    turnId: str = Field(min_length=1, max_length=100)
    profileId: str | None = None


@router.post("/role-sessions/{session_id}/send", status_code=202)
def character_send(session_id: str, data: SendInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    from .api_ai import resolve_operation, create_task
    from .models import RoleMessage
    session = owned(db, RoleSession, session_id, user.id, lock=True)
    ensure_session_scope(db, session, user.id)
    if not data.message.strip():
        raise Problem("MESSAGE_REQUIRED", "请输入消息")
    # A turn identity stays stable even when the transport key changes. This
    # lookup happens before creating authorization or reserving any calls.
    previous = db.scalar(select(RoleMessage).where(RoleMessage.session_id == session.id, RoleMessage.turn_id == data.turnId))
    if previous:
        previous_task = owned(db, Task, previous.task_id, user.id)
        if previous_task.payload.get("message") != data.message:
            raise Problem("IDEMPOTENCY_CONFLICT", "同一条消息的内容发生变化", 409)
        return {"taskId": previous.task_id, "reused": True}
    tasks = db.scalars(select(Task).where(Task.owner_id == user.id, Task.task_type == "role_chat").order_by(Task.created_at.desc()))
    existing = next((t for t in tasks if t.payload.get("sessionId") == session.id and t.payload.get("turnId") == data.turnId), None)
    if existing:
        if existing.payload.get("message") != data.message:
            raise Problem("IDEMPOTENCY_CONFLICT", "同一条消息的内容发生变化", 409)
        return {"taskId": existing.id, "reused": True}
    if session.pending_task_id:
        raise Problem("TURN_IN_PROGRESS", "请等待当前回复保存", 409)
    profile = owned(db, ModelProfile, data.profileId, user.id) if data.profileId else default_profile(db, user.id)
    operation = Operation(operation="role_chat", profileId=profile.id, sessionId=session.id, message=data.message, turnId=data.turnId)
    resolved, _, _ = resolve_operation(db, operation, user.id, profile)
    details = stage_budget(profile, planned_stages("role_chat", 0))
    if (call_budget_exceeded(profile, details["maxCalls"]) or input_budget_exceeded(profile, details["inputTokens"])
            or output_budget_exceeded(profile, details["outputTokens"])):
        raise Problem("BUDGET_NOT_AUTHORIZED", "当前模型额度不足，请到设置调整额度", 403)
    expires = now() + timedelta(minutes=10)
    plan_hash = digest(canonical({"payload": resolved, "details": details, "profileVersion": profile.version}))
    plan = OperationPlan(id=uid(), owner_id=user.id, profile_id=profile.id, profile_version=profile.version,
        payload=resolved, details=details, plan_hash=plan_hash, expires_at=expires)
    authorization = Consent(id=uid(), owner_id=user.id, plan_id=plan.id, profile_id=profile.id,
        profile_version=profile.version, plan_hash=plan_hash, max_calls=details["maxCalls"],
        input_limit=details["inputTokens"], output_limit=details["outputTokens"], expires_at=expires)
    db.add_all([plan, authorization])
    db.flush()
    return create_task(db, user, request, TaskInput(**operation.model_dump(), consentId=authorization.id), "role_chat", chat_streaming=True)
