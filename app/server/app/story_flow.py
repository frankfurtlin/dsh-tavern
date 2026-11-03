"""Durable, finite orchestration behind the reader's single start button.

Only a child linked by an owned, immutable index parent may analyse unread text.
The parent never modifies reading progress and never exposes raw extraction data.
"""
from .execution_prompts import freeze_prompts
from .model_budget import call_budget_exceeded, input_budget_exceeded, output_budget_exceeded, remaining_calls, unlimited_usage
from copy import deepcopy
from datetime import timedelta
from sqlalchemy import select
from .db import SessionLocal
from .models import Task, ModelProfile, OperationPlan, Consent, KnowledgeSnapshot, AnalysisChunk, UserSettings, Candidate, User, uid, now
from .domain import owned, validate_anchor, prefix_blocks, canonical, digest
from .schemas import Operation
from .errors import Problem
from .knowledge import find_snapshot, missing_chunks, chunks_for, chunk_hash, snapshot_hash, merge_analysis, ANALYZER
from .completion_policy import completion_policy, planned_stages, input_ceiling
from .validation_gate import rejection_code
from .generation_review import candidate_acceptable, saved_warnings
from .prose.policy import freeze, execution_profile

TERMINAL = {"succeeded", "failed", "cancelled"}


def default_profile(db, owner):
    from .api_ai import effective_default
    rows = list(db.scalars(select(ModelProfile).where(ModelProfile.owner_id == owner).order_by(ModelProfile.created_at, ModelProfile.id)))
    preference = db.get(UserSettings, owner)
    from .foundation.vision import platform_profile_ids
    selected = effective_default(rows, preference.default_profile_id if preference else None,excluded=platform_profile_ids(db,owner))
    if not selected:
        raise Problem("NO_PROVIDER", "平台文本模型暂不可用，请稍后重试或联系管理员")
    return owned(db, ModelProfile, selected, owner)


def trusted_index_task(db, task):
    parent_id = task.payload.get("flowId")
    parent = db.get(Task, parent_id) if parent_id else None
    return bool(parent and parent.owner_id == task.owner_id and parent.task_type == "story_flow"
        and parent.payload.get("flowKind") == "character_index" and task.task_type == "analysis"
        and parent.checkpoint.get("currentTaskId") == task.id and not parent.cancel_requested
        and parent.state not in TERMINAL and task.payload.get("anchor") == parent.payload.get("operation", {}).get("anchor")
        and task.payload.get("profileId") == parent.payload.get("profileId"))


def stage_budget(profile, stages):
    policy = completion_policy(profile)
    limits = [policy["stages"][stage]["totalCompletionTokens"] for stage in stages]
    return {"maxCalls": len(stages), "inputTokens": sum(input_ceiling(profile, n) for n in limits),
            "outputTokens": sum(limits), "completionPolicy": policy}


def flow_result(task):
    value = task.result or {}
    return {"id": task.id, "state": task.state, "currentTaskId": value.get("currentTaskId"),
            "candidateId": value.get("candidateId"), "requestedChapters": value.get("requestedChapters", 1),
            "completedChapters": value.get("completedChapters", 0), "phase": value.get("phase", "preparing"),
            "errorCode": task.error_code, "scopeAnchor": task.payload.get("operation", {}).get("anchor"),
            "warningCount": len(saved_warnings(task)), "createdAt": task.created_at.isoformat()}


def create_flow(db, owner, operation, *, kind="generation", call_limit=None):
    from .api_ai import resolve_operation
    profile = owned(db, ModelProfile, operation.profileId, owner, lock=True)
    resolved = operation.model_dump()
    if kind == "character_index":
        _, revision, _ = validate_anchor(db, resolved["anchor"], owner)
        blocks = prefix_blocks(db, revision, resolved["anchor"], owner)
        _, missing = missing_chunks(db, owner, resolved["anchor"], blocks, profile)
    else:
        resolved, _, missing = resolve_operation(db, operation, owner, profile)
    if profile.status != "active" or not profile.key_cipher:
        raise Problem("NO_PROVIDER", "默认模型暂时不可用")
    stages = planned_stages(operation.operation, len(missing), operation.chapterCount)
    if kind == "character_index" and stages:
        one = stage_budget(profile, ["analysis"])
        allowed = min(len(stages), remaining_calls(profile, 100),
            (100 if unlimited_usage() else (profile.input_budget-profile.input_reserved)//one["inputTokens"]),
            (100 if unlimited_usage() else (profile.output_budget-profile.output_reserved)//one["outputTokens"]))
        if allowed <= 0:
            raise Problem("BUDGET_NOT_AUTHORIZED", "当前模型额度不足，请到设置调整额度", 403)
        stages = stages[:allowed]
    budget = stage_budget(profile, stages)
    if call_limit is not None and (type(call_limit) is not int or not 1 <= call_limit <= 100 or budget["maxCalls"] > call_limit):
        raise Problem("BUDGET_NOT_AUTHORIZED", "本次任务需要的调用超过指定上限；未创建任务", 403,
            {"requiredCalls": budget["maxCalls"], "maxCalls": call_limit})
    if budget["maxCalls"] > 100 or call_budget_exceeded(profile, budget["maxCalls"]) or input_budget_exceeded(profile, budget["inputTokens"]) or output_budget_exceeded(profile, budget["outputTokens"]):
        raise Problem("BUDGET_NOT_AUTHORIZED", "当前模型额度不足，请到设置调整额度或减少章数", 403)
    active = list(db.scalars(select(Task).where(Task.owner_id == owner, Task.task_type == "story_flow", Task.state.notin_(TERMINAL))))
    if len(active) >= 3:
        raise Problem("CONCURRENCY_LIMIT", "请先等待当前写作或整理完成", 429)
    task = Task(id=uid(), owner_id=owner, task_type="story_flow", payload={"flowKind": kind, "operation": resolved,
        "profileId": profile.id, "profileVersion": profile.version, "budget": budget, "callLimit": call_limit,
        "deadline": (now()+timedelta(minutes=60)).isoformat()},
        checkpoint={"children": [], "authorizedCalls": 0, 'executionProfile': freeze(profile), 'executionPrompts': freeze_prompts()}, result={"requestedChapters": operation.chapterCount,
        "completedChapters": 0, "phase": "indexing" if kind == "character_index" else "preparing"})
    db.add(task)
    db.flush()
    return flow_result(task)


def cached_index(db, owner, anchor, revision, profile):
    blocks, hashed, snapshot = find_snapshot(db, owner, anchor, revision, profile, allow_unread=True)
    if snapshot:
        return snapshot
    chunks, missing = missing_chunks(db, owner, anchor, blocks, profile)
    if missing:
        return None
    parts = [db.scalar(select(AnalysisChunk).where(AnalysisChunk.owner_id == owner,
        AnalysisChunk.cache_hash == chunk_hash(c, anchor, profile))).data for c in chunks]
    snapshot = KnowledgeSnapshot(owner_id=owner, book_id=anchor["bookId"], branch_id=anchor["branchId"],
        revision_id=revision.id, boundary=anchor, scope_hash=hashed, profile_version=f"{profile.id}:{profile.version}", analyzer_version=ANALYZER,
        data=merge_analysis(parts), completeness="complete")
    db.add(snapshot)
    db.flush()
    return snapshot


def make_child(db, parent, profile, operation, missing_count, snapshot=None):
    analysis_batch = min(missing_count, 20)
    if parent.payload["flowKind"] == "character_index":
        analysis_batch = min(analysis_batch, parent.payload["budget"]["maxCalls"]-parent.checkpoint.get("authorizedCalls", 0))
        if analysis_batch <= 0:
            raise Problem("BUDGET_NOT_AUTHORIZED", "当前额度内的角色已整理，可以在设置中调整额度后继续", 403)
    stages = planned_stages(operation.operation, analysis_batch, operation.chapterCount)
    details = stage_budget(profile, stages)
    calls = parent.checkpoint.get("authorizedCalls", 0) + details["maxCalls"]
    if calls > parent.payload["budget"]["maxCalls"]:
        raise Problem("BUDGET_NOT_AUTHORIZED", "写作范围已变化，请重新开始", 403)
    resolved = operation.model_dump()
    details.update({"missingAnalysisBlocks": missing_count, "batchAnalysisBlocks": analysis_batch,
        "chapterCount": operation.chapterCount})
    expires = __import__("datetime").datetime.fromisoformat(parent.payload["deadline"])
    plan_hash = digest(canonical({"payload": resolved, "details": details, "profileVersion": profile.version}))
    plan = OperationPlan(id=uid(), owner_id=parent.owner_id, profile_id=profile.id, profile_version=profile.version,
        payload=resolved, details=details, plan_hash=plan_hash, expires_at=expires)
    child = Task(id=uid(), owner_id=parent.owner_id, task_type="analysis" if operation.operation == "analysis" else "generation",
        payload={**resolved, "flowId": parent.id}, checkpoint={"snapshotId": snapshot.id if snapshot else None,
        "completionPolicy": details["completionPolicy"], "analysisBatchSize": analysis_batch, "calls": {},
        'executionProfile': freeze(profile), 'executionPrompts': deepcopy(parent.checkpoint['executionPrompts']) if 'executionPrompts' in parent.checkpoint else freeze_prompts()})
    consent = Consent(id=uid(), owner_id=parent.owner_id, plan_id=plan.id, profile_id=profile.id,
        profile_version=profile.version, plan_hash=plan_hash, max_calls=details["maxCalls"],
        input_limit=details["inputTokens"], output_limit=details["outputTokens"], expires_at=expires, task_id=child.id)
    child.payload = {**child.payload, "consentId": consent.id}
    db.add_all([plan, consent, child])
    parent.checkpoint = {**parent.checkpoint, "children": parent.checkpoint.get("children", [])+[child.id],
        "authorizedCalls": calls, "currentTaskId": child.id}
    parent.result = {**parent.result, "currentTaskId": child.id, "phase": "indexing" if operation.operation == "analysis" else "writing"}
    return child


def process_flow(task_id):
    from .worker import add_event
    from .task_lease import assert_lease
    with SessionLocal() as db:
        from .book_deletion import lock_task
        parent = lock_task(db, task_id)
        if not parent or parent.state in TERMINAL:
            return
        assert_lease(parent)
        child = db.get(Task, parent.checkpoint.get("currentTaskId")) if parent.checkpoint.get("currentTaskId") else None
        account = db.get(User, parent.owner_id)
        if not account or account.status != "active":
            parent.cancel_requested = True
        if parent.cancel_requested:
            if child and (child.state not in TERMINAL or child.persistence_state == "recoverable"):
                child.cancel_requested = True
            else:
                parent.state, parent.error_code, parent.result_outcome = "cancelled", "TASK_CANCELLED", "cancelled"
        elif child and (child.state not in TERMINAL or child.persistence_state == "recoverable"):
            parent.result = {**parent.result, "completedChapters": child.result.get("completedChapters", 0),
                "phase": "recovering" if child.persistence_state == "recoverable" else parent.result.get("phase", "preparing")}
        elif child and child.state != "succeeded":
            parent.state, parent.error_code, parent.result_outcome = "failed", child.error_code or "GENERATION_FAILED", "failed"
        elif child and child.task_type == "generation":
            candidate = db.get(Candidate, child.result.get("candidateId")) if child.result.get("candidateId") else None
            complete = bool(child.owner_id == parent.owner_id and candidate_acceptable(candidate, child) and child.result_outcome == "complete"
                and child.result.get("completedChapters") == parent.result["requestedChapters"])
            parent.result = {**parent.result, **child.result, "phase": "ready" if complete else "rejected", "currentTaskId": child.id}
            parent.state, parent.persistence_state, parent.result_outcome = "succeeded" if complete else "failed", "saved", child.result_outcome
            parent.checkpoint = {**parent.checkpoint, "reviewWarnings": saved_warnings(child)}
            if complete:
                parent.error_code = None
            if not complete:
                parent.error_code = rejection_code(child.checkpoint.get("validationDecision", {})) if child.result_outcome == "blocked" else "GENERATION_INCOMPLETE"
        else:
            try:
                live_profile = owned(db, ModelProfile, parent.payload["profileId"], parent.owner_id)
                if live_profile.status != 'active':
                    raise Problem('CONSENT_REQUIRED', '模型通道授权已撤销', 403)
                profile = execution_profile(db, parent, live_profile)
                if profile.version != parent.payload["profileVersion"] or completion_policy(profile) != parent.payload["budget"]["completionPolicy"]:
                    raise Problem("CONSENT_REQUIRED", "默认模型设置已变化，请重新开始", 403)
                from datetime import datetime
                if now() > datetime.fromisoformat(parent.payload["deadline"]):
                    raise Problem("CONSENT_REQUIRED", "本次任务已超时，请重新开始", 403)
                operation = Operation.model_validate(parent.payload["operation"])
                _, revision, _ = validate_anchor(db, operation.anchor.model_dump(), parent.owner_id)
                if parent.payload["flowKind"] == "character_index":
                    blocks = prefix_blocks(db, revision, operation.anchor.model_dump(), parent.owner_id)
                    snapshot = cached_index(db, parent.owner_id, operation.anchor.model_dump(), revision, profile)
                    _, missing = missing_chunks(db, parent.owner_id, operation.anchor.model_dump(), blocks, profile)
                    if snapshot:
                        parent.result = {**parent.result, "phase": "ready", "snapshotId": snapshot.id}
                        parent.state, parent.persistence_state, parent.result_outcome = "succeeded", "saved", "complete"
                    else:
                        make_child(db, parent, profile, operation, len(missing))
                else:
                    from .api_ai import resolve_operation
                    _, snapshot, missing = resolve_operation(db, operation, parent.owner_id, profile,live_profile=live_profile)
                    child_operation = operation.model_copy(update={"operation": "analysis", "chapterCount": 1}) if len(missing) > 20 else operation
                    make_child(db, parent, profile, child_operation, len(missing), snapshot)
            except Problem as error:
                parent.state, parent.error_code, parent.result_outcome = "failed", error.code, "failed"
        if parent.state in TERMINAL:
            add_event(db, parent, "terminal", {"state": parent.state, "errorCode": parent.error_code})
        else:
            parent.state, parent.lease_expires_at = "preparing", now()+timedelta(seconds=2)
        db.commit()
