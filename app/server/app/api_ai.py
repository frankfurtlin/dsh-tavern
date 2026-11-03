import json
import time
from datetime import timedelta
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, text, or_
from pydantic import Field
from .db import get_db, SessionLocal
from .models import ModelProfile, OperationPlan, Consent, Task, TaskEvent, RoleSession, RoleMessage, UsageRecord, KnowledgeSnapshot, Branch, Book, UserSettings, uid, now
from .security import current_user
from .schemas import ProfileInput, Operation, ConsentInput, TaskInput, Strict, Anchor
from .domain import owned, validate_anchor, idempotent, digest, canonical, is_tail, anchor_order
from .errors import Problem
from .providers import encrypt_key, validate_url
from .config import settings
from .completion_policy import completion_policy, planned_stages, input_ceiling
from .knowledge import find_snapshot, missing_chunks, visible_characters, safe_scope, materialize_cached_snapshot, require_role_character, legacy_identity_uncertain
from .api_books import paginate

router = APIRouter()


def profile_result(p, default_id=None, *, source="personal"):
    from .model_budget import unlimited_usage
    return {"id": p.id,"unlimitedUsage":unlimited_usage(), "name": p.name, "baseUrl": p.base_url, "modelId": p.model_id, "adapterType": "chat_completions",
            "keyTail": p.key_tail, "hasKey": bool(p.key_cipher), "contextWindow": p.context_window, "outputParameter": p.output_parameter,
            "supportsJson": p.supports_json, "version": p.version, "status": p.status, "callBudget": p.call_budget, "inputBudget": p.input_budget,
            "outputBudget": p.output_budget, "callsReserved": p.calls_reserved, "inputReserved": p.input_reserved, "outputReserved": p.output_reserved,
            "isDefault": p.id == default_id, "source": source}


def effective_default(rows, preferred, *, excluded=()):
    active = [p for p in rows if p.status == "active" and p.key_cipher and p.id not in excluded]
    return next((p.id for p in active if p.id == preferred), None) or next(
        (p.id for p in active if p.model_id == "mimo-v2.6-flash" and p.base_url.rstrip("/") == "https://opencode.ai/zen/go/v1"),
        active[0].id if active else None)


@router.get("/model-profiles")
def profiles(user=Depends(current_user), db=Depends(get_db)):
    rows = list(db.scalars(select(ModelProfile).where(ModelProfile.owner_id == user.id).order_by(ModelProfile.created_at, ModelProfile.id)))
    from .foundation.models import ModelGrant
    from .foundation.vision import platform_profile_ids
    grant = db.get(ModelGrant, user.id)
    visual_ids=platform_profile_ids(db,user.id)
    default_id = effective_default(rows, db.get(UserSettings, user.id).default_profile_id,excluded=visual_ids)
    return {"items": [{**profile_result(p, default_id, source="platform" if grant and grant.text_profile_id == p.id or p.id in visual_ids else "personal"),
        'purpose':'vision' if p.id in visual_ids else 'text'} for p in rows], "defaultProfileId": default_id,
            "allowedHosts": [h for h in settings.provider_allowed_hosts.split(",") if h], "aiMode": settings.ai_mode}


@router.put("/model-profiles/{profile_id}/default")
def profile_default(profile_id: str, user=Depends(current_user), db=Depends(get_db)):
    preference = db.scalar(select(UserSettings).where(UserSettings.owner_id == user.id).with_for_update())
    profile = owned(db, ModelProfile, profile_id, user.id, lock=True)
    from .foundation.vision import platform_profile_ids
    if profile_id in platform_profile_ids(db,user.id):raise Problem('VISION_PROFILE_ONLY','平台画面校验通道专用于场景校验，请选择故事文本模型',403)
    if profile.status != "active" or not profile.key_cipher:
        raise Problem("NO_PROVIDER", "请选择仍可用的模型通道")
    preference.default_profile_id = profile.id
    db.commit()
    return {"defaultProfileId": profile.id}


@router.post("/model-profiles", status_code=201)
def profile_create(data: ProfileInput, user=Depends(current_user), db=Depends(get_db)):
    validate_url(data.baseUrl)
    profile = ModelProfile(owner_id=user.id, name=data.name, base_url=data.baseUrl.rstrip("/"), model_id=data.modelId,
        key_cipher=encrypt_key(data.key), key_tail=data.key[-4:], context_window=data.contextWindow, output_parameter=data.outputParameter,
        supports_json=data.supportsJson, call_budget=data.callBudget, input_budget=data.inputBudget, output_budget=data.outputBudget)
    db.add(profile)
    db.commit()
    return profile_result(profile)


class ProfilePatch(Strict):
    expectedVersion: int
    name: str | None = Field(default=None, min_length=1, max_length=100)
    key: str | None = Field(default=None, min_length=1, max_length=500)
    status: str | None = None
    callBudget: int | None = Field(default=None, ge=0)
    inputBudget: int | None = Field(default=None, ge=1)
    outputBudget: int | None = Field(default=None, ge=1)


@router.patch("/model-profiles/{profile_id}")
def profile_update(profile_id: str, data: ProfilePatch, user=Depends(current_user), db=Depends(get_db)):
    profile = owned(db, ModelProfile, profile_id, user.id, lock=True)
    if profile.version != data.expectedVersion:
        raise Problem("PROFILE_CONFLICT", "模型设置已更新", 409)
    from .foundation.default_models import managed_text
    if managed_text(db, profile) and any(getattr(data, field) is not None for field in ("name", "key", "callBudget", "inputBudget", "outputBudget")):
        raise Problem("PLATFORM_PROFILE_READONLY", "平台默认通道由管理员维护；如需自定义，请新建个人通道", 403)
    if data.status not in (None, "active", "revoked"):
        raise Problem("PROFILE_INVALID", "通道状态无效")
    if data.key:
        profile.key_cipher, profile.key_tail = encrypt_key(data.key), data.key[-4:]
    if data.name:
        profile.name = data.name
    for field, attribute in [("callBudget", "call_budget"), ("inputBudget", "input_budget"), ("outputBudget", "output_budget")]:
        if getattr(data, field) is not None:
            setattr(profile, attribute, getattr(data, field))
    if data.status:
        profile.status = data.status
        if data.status == "revoked":
            profile.key_cipher = ""
    profile.version += 1
    db.commit()
    return profile_result(profile)


def resolve_operation(db, operation, owner, profile,*,live_profile=None,inline_boundary=False,pinned_revision=False):
    data = operation.model_dump()
    if operation.operation in {"style_rewrite", "world_writeback"}:
        raise Problem("OPERATION_NOT_ENABLED", "此操作不属于首版")
    if operation.operation not in {"continue_at_tail", "continue_from_anchor", "counterfactual"} and operation.chapterCount != 1:
        raise Problem("CHAPTER_COUNT_INVALID", "只有续写可以指定章数")
    active_profile=live_profile or profile
    if active_profile.status != "active" or not active_profile.key_cipher:
        raise Problem("NO_PROVIDER", "请配置可用的模型通道")
    if operation.operation == "connection_test":
        return data, None, []
    if operation.operation == "role_chat":
        session = owned(db, RoleSession, operation.sessionId, owner)
        from .character_cards import ensure_session_scope
        ensure_session_scope(db, session, owner)
        data["anchor"] = session.pinned_anchor
        if not operation.message or not operation.turnId:
            raise Problem("MESSAGE_REQUIRED", "请输入消息并提供 turnId")
    if not data["anchor"]:
        raise Problem("ANCHOR_REQUIRED", "请选择明确的阅读位置")
    inline = inline_boundary and operation.operation in {'continue_at_tail','continue_from_anchor','counterfactual'}
    branch, revision, block = validate_anchor(db, data["anchor"], owner, paragraph=operation.operation != "role_chat" and not inline)
    if inline and data['anchor']['start'] != data['anchor']['end']:
        raise Problem('ANCHOR_INVALID', '请使用明确的字符起点，选区编辑另存候选')
    book = owned(db, Book, branch.book_id, owner)
    if branch.status == "archived" or book.status == "archived":
        raise Problem("BRANCH_ARCHIVED", "请先恢复归档内容", 409)
    if operation.operation != "role_chat" and operation.expectedHeadRevisionId != (branch.head_revision_id if pinned_revision else revision.id):
        raise Problem("REVISION_CONFLICT", "生成基线与阅读位置不一致", 409)
    if operation.operation != "role_chat" and not pinned_revision and branch.head_revision_id != revision.id:
        raise Problem("REVISION_CONFLICT", "当前故事线已更新，请重新选择起点", 409)
    if operation.operation == "continue_at_tail" and not is_tail(revision, data["anchor"], block):
        raise Problem("ANCHOR_INVALID", "末尾续写需要故事线末尾")
    if operation.operation == "counterfactual" and not operation.instruction.strip():
        raise Problem("INSTRUCTION_REQUIRED", "请写下希望改变的剧情")
    if operation.operation == "role_chat":
        snapshot = owned(db, KnowledgeSnapshot, session.snapshot_id, owner)
        require_role_character(snapshot, session.character_id, db)
        return data, snapshot, []
    blocks, scope_hash, snapshot = find_snapshot(db, owner, data["anchor"], revision, profile)
    chunks, missing = missing_chunks(db, owner, data["anchor"], blocks, profile)
    return data, snapshot, missing


@router.post("/operation-plans", status_code=201)
def operation_plan(data: Operation, user=Depends(current_user), db=Depends(get_db)):
    profile = owned(db, ModelProfile, data.profileId, user.id)
    resolved, snapshot, missing = resolve_operation(db, data, user.id, profile)
    count = len(missing)
    if count > 20 and data.operation not in {"analysis"}:
        raise Problem("AI_CONTEXT_NOT_READY", "上下文需要分批准备；请先确认分析计划", details={"missingAnalysisBlocks": count, "batchSize": 20})
    max_analysis = min(count, 20)
    stages = planned_stages(data.operation, max_analysis, data.chapterCount)
    calls = len(stages)
    if calls == 0:
        if snapshot is None:
            _, revision, _ = validate_anchor(db, resolved['anchor'], user.id)
            snapshot = materialize_cached_snapshot(db, user.id, resolved['anchor'], revision, profile)
            if snapshot is None:
                raise Problem('AI_CONTEXT_NOT_READY', '缓存范围发生变化，请重新查看调用计划', 409)
            db.commit()
        return {"ready": True, "snapshotId": snapshot.id if snapshot else None, "maxCalls": 0}
    policy = completion_policy(profile)
    stage_limits = [policy["stages"][stage]["totalCompletionTokens"] for stage in stages]
    output = sum(stage_limits)
    input_limits = [input_ceiling(profile, limit) for limit in stage_limits]
    if any(limit <= 0 for limit in input_limits):
        raise Problem("CONTEXT_BUDGET_EXCEEDED", "模型上下文窗口不足")
    details = {"missingAnalysisBlocks": count, "batchAnalysisBlocks": max_analysis, "maxCalls": calls,
               "inputTokens": sum(input_limits), "outputTokens": output, "completionPolicy": policy, "usageEstimate": "estimated", "cost": "unknown", "operation": data.operation,
               "providerName": profile.name, "modelId": profile.model_id, "range": resolved.get("anchor"), "needsNextBatch": count > 20,
               "chapterCount": data.chapterCount}
    plan_hash = digest(canonical({"payload": resolved, "details": details, "profileVersion": profile.version}))
    plan = OperationPlan(owner_id=user.id, profile_id=profile.id, profile_version=profile.version, payload=resolved,
           plan_hash=plan_hash, details=details, expires_at=now() + timedelta(minutes=10))
    db.add(plan)
    db.commit()
    return {"planId": plan.id, "planHash": plan.plan_hash, "profileVersion": profile.version, **details}


@router.post("/consents", status_code=201)
def consent(data: ConsentInput, user=Depends(current_user), db=Depends(get_db)):
    plan = owned(db, OperationPlan, data.planId, user.id, lock=True)
    profile = owned(db, ModelProfile, plan.profile_id, user.id, lock=True)
    if plan.expires_at < now() or data.planHash != plan.plan_hash or data.profileId != plan.profile_id or profile.version != plan.profile_version or plan.details.get("completionPolicy") != completion_policy(profile):
        raise Problem("CONSENT_REQUIRED", "调用计划已变化，请重新确认", 403)
    if data.maxCalls != plan.details["maxCalls"] or data.inputTokens != plan.details["inputTokens"] or data.outputTokens != plan.details["outputTokens"]:
        raise Problem("BUDGET_NOT_AUTHORIZED", "授权上限须对应当前计划", 403)
    existing = db.scalar(select(Consent).where(Consent.plan_id == plan.id))
    if existing:
        return {"consentId": existing.id}
    row = Consent(owner_id=user.id, plan_id=plan.id, profile_id=profile.id, profile_version=profile.version,
           plan_hash=plan.plan_hash, max_calls=data.maxCalls, input_limit=data.inputTokens, output_limit=data.outputTokens, expires_at=plan.expires_at)
    db.add(row)
    db.commit()
    return {"consentId": row.id, "expiresAt": row.expires_at.isoformat()}


def create_task(db, user, request, data, task_type, *, chat_streaming=False):
    payload = data.model_dump(exclude={"consentId"})

    def create():
        profile = owned(db, ModelProfile, data.profileId, user.id, lock=True)
        resolved, snapshot, missing = resolve_operation(db, Operation.model_validate(payload), user.id, profile)
        authorization = owned(db, Consent, data.consentId, user.id, lock=True)
        plan = owned(db, OperationPlan, authorization.plan_id, user.id)
        if authorization.task_id or authorization.expires_at < now() or profile.version != authorization.profile_version or canonical(resolved) != canonical(plan.payload) or plan.details.get("completionPolicy") != completion_policy(profile):
            raise Problem("CONSENT_REQUIRED", "授权已使用、过期或操作内容已变化", 403)
        db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": int(digest(user.id)[:15], 16)})
        running = db.scalar(select(func.count()).select_from(Task).where(Task.owner_id == user.id, Task.state.in_(["preparing", "running", "validating", "persisting"])))
        queued = db.scalar(select(func.count()).select_from(Task).where(Task.owner_id == user.id, Task.state == "queued"))
        if running >= 2 and queued >= 3 or queued >= 3:
            raise Problem("CONCURRENCY_LIMIT", "等待任务已达上限", 429, {"retryAfter": 2}, True)
        session = None
        if task_type == "role_chat":
            session = owned(db, RoleSession, data.sessionId, user.id, lock=True)
            if session.pending_task_id:
                raise Problem("TURN_IN_PROGRESS", "请等待当前回复保存", 409)
            previous = db.scalar(select(RoleMessage).where(RoleMessage.session_id == session.id, RoleMessage.turn_id == data.turnId))
            if previous:
                return {"taskId": previous.task_id, "reused": True}
        from .execution_prompts import freeze_prompts
        from .prose.policy import freeze
        task = Task(id=uid(), owner_id=user.id, task_type=task_type, payload={**resolved, "consentId": authorization.id}, checkpoint={"snapshotId": snapshot.id if snapshot else None, "completionPolicy": plan.details["completionPolicy"], "calls": {}, 'executionProfile': freeze(profile), 'executionPrompts': freeze_prompts()})
        db.add(task)
        authorization.task_id = task.id
        if session:
            session.pending_task_id = task.id
            if chat_streaming:
                task.checkpoint = {**task.checkpoint, "chatStreamingPolicy": "role-stream-v1", "replyStream": {
                    "sessionId": session.id, "turnId": data.turnId, "userMessage": data.message,
                    "text": "", "version": 0, "status": "waiting"}}
                sequence = db.scalar(select(func.coalesce(func.max(RoleMessage.sequence), 0)).where(RoleMessage.session_id == session.id))
                db.add(RoleMessage(owner_id=user.id, session_id=session.id, turn_id=data.turnId, sequence=sequence+1,
                    role="user", text=data.message, task_id=task.id, status="pending"))
        return {"taskId": task.id, "baseRevisionId": resolved["anchor"]["revisionId"] if resolved.get("anchor") else None, "state": "queued", "aiMode": settings.ai_mode}
    return idempotent(db, user.id, "task:" + task_type, request.headers.get("idempotency-key"), data.model_dump(), create)


@router.post("/generation-tasks", status_code=202)
def generation(data: TaskInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    if data.operation not in ("continue_at_tail", "continue_from_anchor", "counterfactual"):
        raise Problem("OPERATION_NOT_ENABLED", "生成操作不属于首版")
    return create_task(db, user, request, data, "generation")


@router.post("/books/{book_id}/analysis-tasks", status_code=202)
def analysis(book_id: str, data: TaskInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    if data.operation != "analysis" or not data.anchor or data.anchor.bookId != book_id:
        raise Problem("ANCHOR_INVALID", "分析范围无效")
    return create_task(db, user, request, data, "analysis")


@router.post("/model-profiles/{profile_id}/test", status_code=202)
def connection_test(profile_id: str, data: TaskInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    if data.operation != "connection_test" or data.profileId != profile_id:
        raise Problem("CONSENT_REQUIRED", "需要连接测试专用授权", 403)
    return create_task(db, user, request, data, "connection_test")


def task_result(task, db=None, *, strict_stream=False):
    # Explicit streaming chats publish only their reply projection. Legacy task
    # checkpoints, reasoning, prompts and provider diagnostic bodies stay private.
    result = {"id": task.id, "taskId": task.id, "taskType": task.task_type, "state": task.state, "providerState": task.provider_state,
            "persistenceState": task.persistence_state, "resultOutcome": task.result_outcome, "usageState": task.usage_state,
            "cancelRequested": task.cancel_requested, "errorCode": task.error_code, "eventCursor": task.event_seq, "result": task.result, "scopeAnchor": task.payload.get("anchor"), "createdAt": task.created_at.isoformat(), "aiMode": settings.ai_mode}
    stream = task.checkpoint.get("replyStream")
    historical = (task.payload.get("importedHistory") is True and isinstance(stream, dict)
                  and stream.get("historical") is True and stream.get("durability") == "unconfirmed")
    if (task.task_type == "role_chat" and (task.checkpoint.get("chatStreamingPolicy") == "role-stream-v1" or historical)
            and isinstance(stream, dict) and stream.get("sessionId") == task.payload.get("sessionId")
            and stream.get("turnId") == task.payload.get("turnId")):
        allowed = False
        if db is not None:
            from .character_cards import ensure_session_scope
            try:
                session = owned(db, RoleSession, task.payload["sessionId"], task.owner_id)
                if historical:
                    anchor = task.payload.get("anchor")
                    if (task.payload.get("bookId", session.book_id) != session.book_id or isinstance(anchor, dict) and any(
                            anchor.get(key) != expected for key, expected in (("bookId", session.book_id),
                                ("branchId", session.branch_id), ("revisionId", session.pinned_revision_id)))):
                        raise Problem("ROLE_SCOPE_CHANGED", "这段回复的阅读范围不一致", 409)
                    if anchor:
                        _, revision, _ = validate_anchor(db, anchor, task.owner_id)
                        if anchor_order(revision, anchor) > anchor_order(revision, session.pinned_anchor):
                            raise Problem("ROLE_SCOPE_CHANGED", "这段回复超出了会话的阅读时点", 409)
                if not historical or session.status != "needs_refresh":
                    ensure_session_scope(db, session, task.owner_id)
                allowed = True
            except Problem:
                if strict_stream:
                    raise
        if allowed:
            from .durable_stream import overlay
            if not historical:
                stream = overlay(task, "replyStream")
            result["replyStream"] = {key: stream[key] for key in ("sessionId", "turnId", "userMessage", "text", "version", "status", "reviewWarnings", "contentId", "chapterIndex", "streamVersion", "snapshotVersion", "contentHash", "durability", "historical") if key in stream}
    return result


def reader_task(db, task_id, owner, lock=False):
    task = owned(db, Task, task_id, owner, lock=lock)
    if task.task_type.startswith("world_") or task.result.get("deleted"):
        raise Problem("RESOURCE_NOT_FOUND", "这不是一个阅读任务", 404)
    return task


@router.get("/tasks")
def tasks(branchId: str | None = None, taskType: str | None = None, cursor: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    filters = [Task.task_type.notlike("world_%")]
    if branchId:
        owned(db, Branch, branchId, user.id)
        filters.append(Task.payload["anchor"]["branchId"].astext == branchId)
    if taskType:
        filters.append(Task.task_type == taskType)
    rows, next_cursor = paginate(db, Task, user.id, cursor, filters=filters)
    return {"items": [task_result(t, db) for t in rows], "nextCursor": next_cursor}


@router.get("/tasks/{task_id}/diagnostics")
def task_diagnostics(task_id: str, user=Depends(current_user), db=Depends(get_db)):
    task=reader_task(db,task_id,user.id)
    events=list(db.scalars(select(TaskEvent).where(TaskEvent.task_id==task.id).order_by(TaskEvent.seq)))
    durations={state:0 for state in ["queued","preparing","running","validating","persisting"]}
    state,previous="queued",task.created_at
    for event in events:
        next_state=event.payload.get("state") if event.type in {"state","terminal"} else None
        if next_state:
            if state in durations:durations[state]+=max(0,(event.created_at-previous).total_seconds()*1000)
            state,previous=next_state,event.created_at
    if state in durations:durations[state]+=max(0,(now()-previous).total_seconds()*1000)
    usage=list(db.scalars(select(UsageRecord).where(UsageRecord.task_id==task.id).order_by(UsageRecord.created_at)))
    consent=db.get(Consent,task.payload.get("consentId"))
    configured=task.checkpoint.get("providerConfig",{})
    snapshot_id=task.checkpoint.get("snapshotId")
    snapshot=db.get(KnowledgeSnapshot,snapshot_id) if snapshot_id else None
    return {"taskId":task.id,"aiMode":settings.ai_mode,"provider":configured.get("provider"),"modelId":configured.get("modelId"),"profileVersion":configured.get("profileVersion",consent.profile_version if consent else None),"promptVersion":task.checkpoint.get("promptVersion"),"validationPromptVersion":task.checkpoint.get("validationPromptVersion","validation-v1"),"snapshotHash":snapshot.scope_hash if snapshot else None,"contextHash":task.checkpoint.get("contextHash"),"semanticContextHash":task.checkpoint.get("semanticContextHash"),"contextEncoding":task.checkpoint.get("contextEncoding"),"phaseMs":durations,"parameters":{"completionPolicy":task.checkpoint.get("completionPolicy"),"outputParameter":configured.get("outputParameter"),"contextWindow":configured.get("contextWindow"),"calls":[{"operation":stage,"outputLimit":call.get("outputLimit"),"thinking":call.get("thinking"),"completionPolicyVersion":call.get("completionPolicyVersion"),"reservedInput":call.get("reservedInput")} for stage,call in task.checkpoint.get("calls",{}).items()]},"usage":[{"operation":u.operation,"inputTokens":u.input_tokens,"outputTokens":u.output_tokens,"usageState":u.usage_state,"status":u.status,"latencyMs":u.latency_ms} for u in usage]}


@router.get("/tasks/{task_id}")
def task_read(task_id: str, user=Depends(current_user), db=Depends(get_db)):
    return task_result(reader_task(db, task_id, user.id), db, strict_stream=True)


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: str, user=Depends(current_user), db=Depends(get_db)):
    task = reader_task(db, task_id, user.id, lock=True)
    if task.state not in ("succeeded", "failed", "cancelled"):
        task.cancel_requested = True
    db.commit()
    return {"state": task.state if task.state in ("succeeded", "failed", "cancelled") else "cancel_requested", "providerCancellation": "unsupported", "usageMayBeUnknown": True}


@router.get("/tasks/{task_id}/events")
def events(task_id: str, request: Request, afterSeq: int = 0, user=Depends(current_user), db=Depends(get_db)):
    initial = reader_task(db, task_id, user.id)
    streaming_chat = initial.task_type == "role_chat" and initial.checkpoint.get("chatStreamingPolicy") == "role-stream-v1"
    from .prose import is_prose
    streaming_prose = is_prose(initial)
    if streaming_chat:
        from .character_cards import ensure_session_scope
        ensure_session_scope(db, owned(db, RoleSession, initial.payload["sessionId"], user.id), user.id)
    last = request.headers.get("last-event-id", "")
    if last.startswith(task_id + ":"):
        try:
            afterSeq = max(afterSeq, int(last.split(":")[-1]))
        except ValueError:
            pass
    owner, sid = user.id, request.state.session.id

    def stream():
        from .models import DeviceSession, User
        cursor = afterSeq
        # Keep the original two-minute reconnect window independent of the
        # faster body polling cadence.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            frames = []
            with SessionLocal() as conn:
                session = conn.get(DeviceSession, sid)
                account = conn.get(User, owner)
                if not session or session.revoked_at or session.expires_at < now() or not account or account.status != "active":
                    return
                task = reader_task(conn, task_id, owner)
                if streaming_chat:
                    try:
                        ensure_session_scope(conn, owned(conn, RoleSession, task.payload["sessionId"], owner), owner)
                    except Problem:
                        return
                for event in conn.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.seq > cursor).order_by(TaskEvent.seq)):
                    cursor = event.seq
                    value = {'taskId': task_id, 'seq': event.seq, 'type': event.type}
                    if streaming_prose or streaming_chat:
                        if streaming_chat:
                            from .durable_stream import event_payload
                            value['payload'] = event_payload(event.type,event.payload)
                        else:
                            value['payload'] = event.payload
                        if streaming_chat:
                            value.update(at=event.created_at.isoformat(), **value['payload'])
                    else:
                        value.update(at=event.created_at.isoformat(), **event.payload)
                    frames.append(f"id: {task_id}:{event.seq}\nevent: {event.type}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n")
                terminal = task.state in ("succeeded", "failed", "cancelled")
            # Authorization and projection finish inside the transaction.
            # Release its book lifecycle lock before network backpressure:
            # role notifications cause an independently authorized body GET,
            # and neither that GET nor the worker should wait on this consumer.
            for frame in frames:
                yield frame
            if terminal:
                return
            yield ": keepalive\n\n"
            time.sleep(.01 if streaming_chat else .025 if streaming_prose else 1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/usage")
def usage(cursor: str | None = None, limit: int = 20, taskId: str | None = None, storyId: str | None = None,
          profileId: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    from .usage_reporting import query, items, summary
    statement=query(db,user.id,task_id=taskId,story_id=storyId,profile_id=profileId)
    rows,next_cursor=paginate(db,UsageRecord,user.id,cursor,limit,filters=(statement.whereclause,))
    return {'items':items(db,rows),'nextCursor':next_cursor,'summary':summary(db,statement)}


@router.get('/usage/filters')
def usage_filters(user=Depends(current_user),db=Depends(get_db)):
    from .usage_reporting import options
    return options(db,user.id)


@router.get("/books/{book_id}/characters")
def characters(book_id: str, branchId: str, revisionId: str, anchor: str, user=Depends(current_user), db=Depends(get_db)):
    try:
        boundary = Anchor.model_validate(json.loads(anchor)).model_dump()
    except Exception:
        raise Problem("ANCHOR_INVALID", "角色范围无效")
    branch, revision, _ = validate_anchor(db, boundary, user.id)
    if branch.book_id != book_id or branch.id != branchId or revision.id != revisionId:
        raise Problem("ANCHOR_INVALID", "角色范围关联无效")
    safe_scope(db, boundary, revision, user.id)
    snapshots = list(db.scalars(select(KnowledgeSnapshot).where(KnowledgeSnapshot.owner_id == user.id, KnowledgeSnapshot.branch_id == branchId, KnowledgeSnapshot.revision_id == revisionId, KnowledgeSnapshot.boundary == boundary, KnowledgeSnapshot.completeness == "complete").order_by(KnowledgeSnapshot.created_at.desc()).limit(1)))
    characters = visible_characters(snapshots[0]) if snapshots else []
    for character in characters:
        if character['available'] and legacy_identity_uncertain(db, snapshots[0], character):
            character.update(available=False, reasonCode='ROLE_IDENTITY_UNCERTAIN')
        from .character_identity import reader_fields
        character.update(reader_fields(db, snapshots[0], character))
    db.commit()
    return {"items": characters, "aiStatus": "ai_ready" if snapshots else "ai_unprepared", "snapshotId": snapshots[0].id if snapshots else None}


class SessionInput(Strict):
    bookId: str
    characterId: str
    snapshotId: str
    anchor: Anchor
    identityMode: str = "reader_out_of_story"


@router.post("/role-sessions", status_code=201)
def session_create(data: SessionInput, user=Depends(current_user), db=Depends(get_db)):
    anchor = data.anchor.model_dump()
    branch, revision, _ = validate_anchor(db, anchor, user.id)
    snapshot = owned(db, KnowledgeSnapshot, data.snapshotId, user.id)
    if data.identityMode != "reader_out_of_story" or data.bookId != branch.book_id or snapshot.branch_id != branch.id or snapshot.revision_id != revision.id or snapshot.boundary != anchor or snapshot.completeness != "complete":
        raise Problem("AI_CONTEXT_NOT_READY", "角色上下文未准备或范围不一致")
    safe_scope(db, anchor, revision, user.id)
    from .character_identity import resolve_reader_character, reader_identity
    local_id = resolve_reader_character(db, snapshot, data.characterId)
    character = require_role_character(snapshot, local_id, db)
    identity = reader_identity(db, snapshot, character)
    session = RoleSession(owner_id=user.id, book_id=data.bookId, branch_id=branch.id, pinned_revision_id=revision.id,
        pinned_anchor=anchor, character_id=local_id, character_name=character["displayName"], snapshot_id=snapshot.id, character_identity_id=identity.id)
    db.add(session)
    db.commit()
    return session_result(db, session)


def session_result(db, s):
    from .domain import anchor_label
    branch = owned(db, Branch, s.branch_id, s.owner_id)
    book = owned(db, Book, s.book_id, s.owner_id)
    historical = db.scalar(select(Task.id).where(*received_reply_filters(s.id, s.owner_id)).limit(1))
    return {"id": s.id, "bookId": s.book_id, "bookTitle": book.title, "branchId": s.branch_id, "branchName": branch.name,
       "pinnedRevisionId": s.pinned_revision_id, "pinnedAnchor": s.pinned_anchor, "characterId": s.character_id,
       "characterName": s.character_name, "pinnedLocation": anchor_label(db, s.pinned_anchor, s.owner_id), "identityMode": s.identity_mode, "pendingTaskId": s.pending_task_id, "status": s.status,
       "followProgress": s.follow_progress, "knowledgeMode": s.knowledge_mode, "cardId": s.card_id,
       "characterIdentityId": s.character_identity_id, **({"receivedHistory": True} if historical else {})}


@router.get("/role-sessions")
def role_sessions(bookId: str | None = None, branchId: str | None = None, cursor: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    filters = []
    if bookId:
        filters.append(RoleSession.book_id == bookId)
    if branchId:
        filters.append(RoleSession.branch_id == branchId)
    rows, next_cursor = paginate(db, RoleSession, user.id, cursor, filters=filters)
    preference = db.get(UserSettings, user.id)
    rows = [s for s in rows if not s.follow_progress or preference and preference.character_mode == s.knowledge_mode]
    return {"items": [session_result(db, s) for s in rows], "nextCursor": next_cursor}


@router.get("/role-sessions/{session_id}")
def role_session_read(session_id: str, user=Depends(current_user), db=Depends(get_db)):
    session = owned(db, RoleSession, session_id, user.id)
    from .character_cards import ensure_session_scope
    if session.status != "needs_refresh":
        ensure_session_scope(db, session, user.id)
    return session_result(db, session)


@router.get("/role-sessions/{session_id}/messages")
def messages(session_id: str, cursor: str | None = None, limit: int = 20, user=Depends(current_user), db=Depends(get_db)):
    session = owned(db, RoleSession, session_id, user.id)
    from .character_cards import ensure_session_scope
    if session.status != "needs_refresh":
        ensure_session_scope(db, session, user.id)
    rows, next_cursor = paginate(db, RoleMessage, user.id, cursor, limit, filters=(RoleMessage.session_id == session.id,), ascending=True)
    return {"items": [{"id": m.id, "turnId": m.turn_id, "sequence": m.sequence, "role": m.role, "text": m.text, "taskId": m.task_id, "status": m.status} for m in rows], "nextCursor": next_cursor, "pendingTaskId": session.pending_task_id}


def received_reply_filters(session_id, owner):
    return (Task.owner_id == owner, Task.task_type == "role_chat",
            Task.payload["sessionId"].astext == session_id,
            Task.payload["importedHistory"].astext == "true",
            Task.checkpoint["replyStream"]["historical"].astext == "true",
            Task.checkpoint["replyStream"]["durability"].astext == "unconfirmed")


@router.get("/role-sessions/{session_id}/received-replies")
def received_replies(session_id: str, cursor: str | None = None, limit: int = 20, user=Depends(current_user), db=Depends(get_db)):
    """Read-only restored receipts; never create or rewrite conversation rows."""
    session = owned(db, RoleSession, session_id, user.id)
    from .character_cards import ensure_session_scope
    if session.status != "needs_refresh":
        ensure_session_scope(db, session, user.id)
    if cursor:
        point = db.scalar(select(Task).where(Task.id == cursor, *received_reply_filters(session.id, user.id)))
        if point is None:
            raise Problem("RESOURCE_NOT_FOUND", "这段回复记录不存在", 404)
    rows, next_cursor = paginate(db, Task, user.id, cursor, limit,
        filters=received_reply_filters(session.id, user.id), ascending=True)
    items = []
    saved = list(db.scalars(select(RoleMessage).where(RoleMessage.owner_id == user.id,
        RoleMessage.session_id == session.id, RoleMessage.role == "assistant", or_(
            RoleMessage.task_id.in_([t.id for t in rows]), RoleMessage.turn_id.in_([t.payload.get("turnId") for t in rows])))))
    for task in rows:
        if task.result.get("deleted"):
            continue
        value = task_result(task, db).get("replyStream")
        if not value or not value.get("text") or any((m.task_id == task.id or m.turn_id == value["turnId"]) and
            (m.status == "saved" or m.text == value["text"]) for m in saved):
            continue
        items.append({"id": task.id, "taskId": task.id, **value, "createdAt": task.created_at.isoformat()})
    return {"items": items, "nextCursor": next_cursor}


@router.post("/role-sessions/{session_id}/turns", status_code=202)
def turn(session_id: str, data: TaskInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    if data.sessionId != session_id or data.operation != "role_chat":
        raise Problem("SESSION_INVALID", "会话范围不一致")
    return create_task(db, user, request, data, "role_chat")
