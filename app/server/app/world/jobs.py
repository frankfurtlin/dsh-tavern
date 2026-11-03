from ..execution_prompts import freeze_prompts
from ..model_budget import call_budget_exceeded, input_budget_exceeded, output_budget_exceeded
from datetime import timedelta
from sqlalchemy import select
from ..models import Task, Consent, OperationPlan, ModelProfile, uid, now
from ..db import SessionLocal
from ..domain import canonical, digest, owned
from ..errors import Problem
from ..story_flow import default_profile, stage_budget
from ..completion_policy import input_ceiling, completion_limit, completion_policy
from .models import WorldTurn, World
from .state import public_stream, scrub_phrases
from ..task_lease import lease_matches
from ..prose.policy import freeze
from .output import story_text

TERMINAL = {"succeeded", "failed", "cancelled"}


def lock_task_world(db, task_id):
    """Lock the parent before task/turn/line writes, including streamed drafts."""
    metadata = db.execute(select(Task.owner_id, Task.payload).where(Task.id == task_id)).first()
    if not metadata:
        return None
    return db.scalar(select(World).where(World.id == metadata.payload.get("worldId"),
                                         World.owner_id == metadata.owner_id).with_for_update())


def create_job(db, owner, kind, payload, stages, max_calls=None):
    editorial=None
    if kind=='world_scene_binding':
        from ..foundation.vision import selected
        profile=selected(db,owner)
    elif kind in ('world_turn','world_write','world_rewrite','world_character_chat') and (payload.get('lineId') or payload.get('chatId')):
        from ..play_controls.service import frozen
        profile,editorial=frozen(db,owner,'chat' if kind=='world_character_chat' else 'line',payload['chatId'] if kind=='world_character_chat' else payload['lineId'])
    else:profile = default_profile(db, owner)
    profile = owned(db, ModelProfile, profile.id, owner, lock=True)
    budget = stage_budget(profile, stages)
    budget["completionPolicy"] = completion_policy(profile, world=True)
    budget["outputTokens"] = sum(completion_limit(profile, stage, world=True) for stage in stages)
    budget["inputTokens"] = sum(input_ceiling(profile, completion_limit(profile, stage, world=True), world=True,
        script=kind=='world_script_breakdown',vision=kind=='world_scene_binding') for stage in stages)
    if max_calls is not None and budget["maxCalls"] > max_calls:
        raise Problem("BUDGET_NOT_AUTHORIZED", "这次世界准备超过指定调用上限，尚未开始", 403)
    if (call_budget_exceeded(profile, budget["maxCalls"]) or
            input_budget_exceeded(profile, budget["inputTokens"]) or
            output_budget_exceeded(profile, budget["outputTokens"])):
        raise Problem("BUDGET_NOT_AUTHORIZED", "模型额度不足，可在设置中调整后继续", 403)
    if db.scalar(select(Task.id).where(Task.owner_id == owner, Task.task_type.like("world_%"),
                                      Task.state.notin_(TERMINAL)).limit(5)) and len(list(db.scalars(select(Task.id).where(
            Task.owner_id == owner, Task.task_type.like("world_%"), Task.state.notin_(TERMINAL)).limit(5)))) >= 4:
        raise Problem("CONCURRENCY_LIMIT", "请先等待正在进行的世界任务", 429)
    expires = now() + timedelta(hours=2)
    from ..foundation.prompts import freeze_selection
    preferences = freeze_selection(db, owner, payload.get('worldId')) if kind in ('world_turn', 'world_write', 'world_rewrite') else []
    task = Task(id=uid(), owner_id=owner, task_type=kind, payload={**payload, "profileId": profile.id},
                checkpoint={"completionPolicy": budget["completionPolicy"], "calls": {},
                    "executionProfile": freeze(profile), "executionPrompts": freeze_prompts(), "authorPrompts": preferences}, result={"phase": "queued", **{k: v for k, v in payload.items() if k in ("worldId", "lineId", "turnId", "chapterCount")}})
    hashed = digest(canonical({"kind": kind, "payload": payload, "budget": budget, "profileVersion": profile.version}))
    plan = OperationPlan(id=uid(), owner_id=owner, profile_id=profile.id, profile_version=profile.version,
                         payload={"worldOperation": kind, **payload}, details=budget, plan_hash=hashed, expires_at=expires)
    consent = Consent(id=uid(), owner_id=owner, plan_id=plan.id, profile_id=profile.id, profile_version=profile.version,
                      plan_hash=hashed, max_calls=budget["maxCalls"], input_limit=budget["inputTokens"],
                      output_limit=budget["outputTokens"], expires_at=expires, task_id=task.id)
    task.payload = {**task.payload, "consentId": consent.id}
    from ..authoring.trials import reserve
    reserve(db,owner,payload,budget,task.id)
    if editorial is not None:
        task.checkpoint={**task.checkpoint,'playControl':editorial}
        task.payload={**task.payload,'modelName':profile.name+' · '+profile.model_id}
    db.add_all([plan, consent, task])
    db.flush()
    from ..worker import add_event
    add_event(db, task, "turn_started", {"worldId": payload.get("worldId"), "lineId": payload.get("lineId")})
    return task


def task_view(task):
    from sqlalchemy.orm import object_session
    from .queue import progress
    from ..durable_stream import overlay
    stored = task.checkpoint.get("worldStream", {})
    historical = task.payload.get("importedHistory") is True or stored.get("historical") is True
    stream = stored if historical else overlay(task, "worldStream")
    if task.result.get("deleted"):
        stream = {}
    return {"id": task.id, "type": task.task_type, "state": task.state, "phase": task.result.get("phase", task.state),
            "errorCode": task.error_code, "result": scrub_phrases(task.result, task.checkpoint.get("worldDeniedPhrases", [])), "eventSeq": task.event_seq,
            "providerState": task.provider_state, "persistenceState": task.persistence_state,
            "stream": stream, "createdAt": task.created_at.isoformat(),
            "progress": progress(object_session(task), task) if object_session(task) else None}


def persist_stream(task_id, stage, content, final=False, segments=None):
    from .. import durable_stream
    from sqlalchemy.exc import SQLAlchemyError
    from .models import WorldLine
    with SessionLocal() as db:
        world = lock_task_world(db, task_id)
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if not task or not task.task_type.startswith("world_") or not lease_matches(task):
            return
        durable_stream.guard(db, task)
        if not world or world.status != "active":
            raise Problem("WORLD_ARCHIVED", "世界已归档")
        if task.task_type=='world_character_chat':
            from ..play_controls.chat import active
            active(db,task)
        turn = db.scalar(select(WorldTurn).where(WorldTurn.task_id == task_id).with_for_update())
        if turn:
            line = db.scalar(select(WorldLine).where(WorldLine.id == turn.line_id,
                WorldLine.world_id == world.id, WorldLine.owner_id == task.owner_id).with_for_update())
            if not line or line.status != "active" or line.pending_task_id != task.id or line.head_checkpoint_id != turn.parent_checkpoint_id:
                raise Problem("WORLD_CHECKPOINT_CONFLICT", "行动入口已改变", 409)
        content = story_text(content, streaming=not final)
        content = public_stream(content, task.checkpoint.get("worldDeniedPhrases", []), final)
        stream = durable_stream.persist(db, task, "worldStream", content,
            "complete" if final else "streaming", stage=stage)
        if turn:
            turn.draft_text = stream["text"]
            if final and segments is not None:
                # Keep the provider's explicit channels even if the later ledger
                # or prose review rejects this turn. Bind them to these bytes so
                # a subsequent stream can never display stale dialogue.
                turn.result = {**turn.result, "draftSegments": segments,
                    "draftTextHash": digest(stream["text"])}
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()


def phase(task_id, value, **result):
    from ..worker import add_event, check_cancel, assert_task_active
    check_cancel(task_id)
    with SessionLocal() as db:
        task = owned(db, Task, task_id, db.get(Task, task_id).owner_id, lock=True)
        assert_task_active(db, task)
        task.result = {**task.result, "phase": value, **result}
        add_event(db, task, "phase_changed", {"phase": value})
        db.commit()
