from .prose.policy import execution_profile
from .model_budget import call_budget_exceeded, input_budget_exceeded, output_budget_exceeded
import concurrent.futures
import json
import logging
import os
import re
import shutil
import threading
import time
from contextvars import copy_context
from time import monotonic as _stream_clock
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select, func, text, delete, case
from .db import SessionLocal
from .config import settings
from .models import Task, TaskEvent, ModelProfile, Consent, UsageRecord, Candidate, KnowledgeSnapshot, AnalysisChunk, RoleSession, RoleMessage, User, Outbox, uid, now
from .domain import owned, digest, canonical, validate_anchor, prefix_blocks
from .errors import Problem
from .providers import ChatCompletionsAdapter, MockAdapter
from .context_wire import wire_json
from .completion_policy import completion_policy, completion_limit, input_ceiling
from .provider_transport import NOT_SENT_CODES
from .storage import journal, private_path, write_private, mark_synced, clean_synced_journals, clean_orphan_files, clean_deleted_world_journals
from .knowledge import find_snapshot, missing_chunks, chunk_hash, chunks_for, validate_analysis, merge_analysis, character_voice, ANALYZER, require_role_character, prior_identity_context
from .validation_gate import inspect_verdict, rejection_code
from .generation_review import REVIEW_POLICY, review_warning, reviewed_status, saved_warnings
from .book_deletion import lock_task, clean_deleted_books, task_book, usage_receipt
from .task_lease import ClaimedTask, LeaseLost, assert_lease, lease_matches, leased_task, execution_token

logger = logging.getLogger("worker")
PROMPTS = Path(__file__).parent / "prompts"
from .execution_prompts import frozen_prompt
TERMINAL = {"succeeded", "failed", "cancelled"}
ROLE_STREAM_POLICY = "role-stream-v1"


class _DeadlineStreamBuffer:
    """Publish short blocks, including the last block before an upstream pause.

    The single publisher inherits the execution lease. Its errors are observed
    by both the producer and close(); close always joins before task settlement.
    """
    def __init__(self, task_id, publish, *, window=.016, block_size=256):
        self._condition = threading.Condition()
        self._publish, self._window, self._block_size = publish, window, block_size
        self._text = ""
        self._revision = self._published = self._pending = 0
        self._deadline = None
        self._closed = False
        self._error = None
        context = copy_context()
        self._thread = threading.Thread(target=context.run, args=(self._run,),
            name="reader-stream-deadline-"+str(task_id), daemon=False)
        self._thread.start()

    @property
    def text(self):
        with self._condition:
            return self._text

    def check(self):
        with self._condition:
            if self._error is not None:
                raise self._error

    def add(self, content):
        if not content:
            self.check()
            return
        with self._condition:
            if self._error is not None:
                raise self._error
            if self._closed:
                raise RuntimeError("Stream publisher is closed")
            self._text += content
            self._revision += 1
            self._pending += len(content)
            if self._deadline is None:
                self._deadline = _stream_clock()+self._window
            revision = self._revision
            if self._pending >= self._block_size:
                self._deadline = _stream_clock()
                self._condition.notify_all()
                # Backpressure bounds pending blocks even for a rapid supplier.
                while self._published < revision and self._error is None:
                    self._condition.wait()
                if self._error is not None:
                    raise self._error
            else:
                self._condition.notify_all()

    def _run(self):
        try:
            while True:
                with self._condition:
                    while self._revision == self._published:
                        if self._closed:
                            return
                        self._condition.wait()
                    remaining = (self._deadline or _stream_clock())-_stream_clock()
                    if not self._closed and remaining > 0:
                        self._condition.wait(remaining)
                        continue
                    content, revision = self._text, self._revision
                    self._pending = 0
                    self._deadline = None
                self._publish(content)
                with self._condition:
                    self._published = revision
                    self._condition.notify_all()
        except BaseException as error:
            with self._condition:
                self._error = error
                self._closed = True
                self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._thread.join()
        self.check()


def is_stream_chat(task):
    return task.task_type == "role_chat" and task.checkpoint.get("chatStreamingPolicy") == ROLE_STREAM_POLICY


def check_stream_active(task_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert_task_active(db, task)
        from .character_cards import ensure_task_session_scope
        ensure_task_session_scope(db, task, owned(db, RoleSession, task.payload["sessionId"], task.owner_id))


def persist_reply_stream(task_id, content, *, status="streaming", warnings=None, check_scope=True):
    from . import durable_stream
    from sqlalchemy.exc import SQLAlchemyError
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        if not task or task.result.get("deleted") or not is_stream_chat(task) or not lease_matches(task):
            return
        if check_scope:
            assert_task_active(db, task)
            from .character_cards import ensure_task_session_scope
            ensure_task_session_scope(db, task, owned(db, RoleSession, task.payload["sessionId"], task.owner_id))
        previous = task.checkpoint.get("replyStream", {})
        extra = {"sessionId": task.payload["sessionId"], "turnId": task.payload["turnId"], "userMessage": task.payload["message"]}
        if warnings:
            extra["reviewWarnings"] = warnings
        elif warnings is None and previous.get("reviewWarnings"):
            extra["reviewWarnings"] = previous["reviewWarnings"]
        stream = durable_stream.persist(db, task, "replyStream", content, status, extra=extra)
        if stream is previous:
            return
        add_event(db, task, "stream_updated", {"streamVersion": stream["version"], "status": status})
        try:
            db.commit()
        except SQLAlchemyError:
            # Atomic/fsynced public bytes are already recoverable. Do not lose
            # a provider receipt because the optional event mirror failed.
            db.rollback()


def save_stream_messages(db, task, content, status):
    session = owned(db, RoleSession, task.payload["sessionId"], task.owner_id, lock=True)
    rows = {row.role: row for row in db.scalars(select(RoleMessage).where(RoleMessage.session_id == session.id,
        RoleMessage.turn_id == task.payload["turnId"]))}
    sequence = db.scalar(select(func.coalesce(func.max(RoleMessage.sequence), 0)).where(RoleMessage.session_id == session.id))
    for role, body in (("user", task.payload["message"]), ("assistant", content)):
        if role == "assistant" and not body:
            continue
        if role in rows:
            rows[role].text, rows[role].status = body, status
        else:
            sequence += 1
            db.add(RoleMessage(owner_id=task.owner_id, session_id=session.id, turn_id=task.payload["turnId"],
                sequence=sequence, role=role, text=body, task_id=task.id, status=status))
    if session.pending_task_id == task.id:
        session.pending_task_id = None
    task.result = {**task.result, "sessionId": session.id, "turnId": task.payload["turnId"], "replySaved": bool(content)}
    if content and is_stream_chat(task):
        from .durable_stream import saved
        saved(db, task, "replyStream", content)


def add_event(db, task, event_type, payload):
    from .prose import is_prose
    if is_prose(task):
        from .models import GenerationBatch
        batch = db.scalar(select(GenerationBatch).where(GenerationBatch.task_id == task.id))
        event_type = {'state': 'phase', 'usage_updated': 'usage'}.get(event_type, event_type)
        payload = {**payload, 'candidateVersion': batch.candidate_version if batch else 1}
        if event_type == 'terminal':
            payload = {**payload, 'outcome': task.result_outcome, 'providerState': task.provider_state,
                'persistenceState': task.persistence_state, 'acceptedPrefix': batch.accepted_prefix if batch else 0,
                'fee': {'certainty': 'unknown'}}
    if is_stream_chat(task) or task.task_type.startswith("world_"):
        # Preserve existing notification event names, and add the canonical
        # phase/usage event when their old names are used.
        canonical_type = {"state": "phase", "phase_changed": "phase", "usage_updated": "usage"}.get(event_type)
        if canonical_type:
            add_event(db, task, canonical_type, payload)
        if event_type == "terminal":
            payload = {**payload, "outcome": task.result_outcome, "providerState": task.provider_state,
                "persistenceState": task.persistence_state, "acceptedPrefix": 0, "fee": {"certainty": "unknown"}}
    task.event_seq += 1
    db.add(TaskEvent(task_id=task.id, seq=task.event_seq, type=event_type, payload=payload))


def transition(task_id, state, **fields):
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        if not task:
            return
        assert_lease(task)
        if task.state in TERMINAL and task.persistence_state == "saved":
            raise LeaseLost()
        task.state = state
        for key, value in fields.items():
            setattr(task, key, value)
        add_event(db, task, "terminal" if state in TERMINAL else "state", {"state": state, "errorCode": task.error_code, "resultOutcome": task.result_outcome})
        db.commit()


def assert_task_active(db, task, *, received_only=False):
    assert_lease(task)
    if task.result.get("deleted"):
        raise Problem("TASK_CANCELLED", "内容已删除")
    if task.state in TERMINAL and task.persistence_state == "saved" and not received_only:
        raise LeaseLost()
    user = db.get(User, task.owner_id) if task else None
    if not task or task.cancel_requested or not user or user.status != "active":
        raise Problem("TASK_CANCELLED", "任务已停止")
    if not received_only:
        from .prose import is_prose
        if is_prose(task):
            from .prose.selection_generation import guard_task
            guard_task(db, task)
    if task.payload.get("flowId"):
        parent = db.get(Task, task.payload["flowId"])
        if (not parent or parent.owner_id != task.owner_id or parent.task_type != "story_flow"
                or parent.cancel_requested or parent.state in TERMINAL
                or parent.checkpoint.get("currentTaskId") != task.id):
            raise Problem("TASK_CANCELLED", "任务已停止")


def check_cancel(task_id, *, received_only=False):
    with SessionLocal() as db:
        assert_task_active(db, db.get(Task, task_id), received_only=received_only)


def settle_usage(task_id, call_id, result, *, accounting_only=False):
    """Idempotent settlement also runs when replaying a received-result journal."""
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        usage = db.scalar(select(UsageRecord).where(UsageRecord.provider_call_id == call_id).with_for_update())
        if not task or not usage or usage.status in {"completed", "not_sent"}:
            return
        consent = db.scalar(select(Consent).where(Consent.id == usage.consent_id).with_for_update())
        profile = db.scalar(select(ModelProfile).where(ModelProfile.id == usage.profile_id).with_for_update())
        normalized = result["usage"]
        usage.input_tokens, usage.output_tokens, usage.usage_state = normalized["inputTokens"], normalized["outputTokens"], normalized["state"]
        from .usage_reporting import normalized_billing
        usage.billing_metadata = {**(usage.billing_metadata or {}), 'billing': normalized_billing(normalized)}
        usage.status, usage.latency_ms = "not_sent" if result.get("notSent") else "completed", result.get("latencyMs")
        if result.get("notSent"):
            consent.calls_reserved -= 1
            profile.calls_reserved -= 1
        if normalized["state"] == "known":
            input_release = usage.reserved_input-normalized["inputTokens"]
            output_release = usage.reserved_output-normalized["outputTokens"]
            consent.input_reserved -= input_release
            consent.output_reserved -= output_release
            profile.input_reserved -= input_release
            profile.output_reserved -= output_release
        if not accounting_only and lease_matches(task) and not task.result.get("deleted"):
            task.provider_state, task.usage_state = usage.status, normalized["state"]
            add_event(db, task, "usage_updated", {"usageState": normalized["state"]})
        from .foundation.wallet import settle_usage_credits
        from .foundation.events import emit
        settle_usage_credits(db, usage)
        emit(db, usage.owner_id, "provider-completed:" + call_id, "fact.provider.completed",
             data={"taskId": task_id, "operation": usage.operation, "status": usage.status,
                   "usageState": usage.usage_state, "inputTokens": usage.input_tokens, "outputTokens": usage.output_tokens})
        db.commit()


def record_received_result(owner, task_id, call_id, result, *, book_receipt=False):
    """Keep durable receipts first; deletion still permits accounting alone."""
    try:
        if book_receipt:
            try:
                usage_receipt(owner,task_id,call_id,result)
            except OSError:
                # The optional accounting backup must not suppress the original
                # full-result journal when only this separate file write fails.
                logger.warning("usage backup unavailable task=%s",task_id)
        journal(owner, task_id, call_id, result)
    except Problem as error:
        if error.code == "TASK_CANCELLED":
            # A committed deletion forbids retaining the provider's body. Its
            # already received usage can still release the unused reservation,
            # without restoring the deleted task's events or calling again.
            settle_usage(task_id, call_id, result)
        raise
    settle_usage(task_id, call_id, result)


def call_provider(task_id, stage, prompt_name, data, output_limit):
    check_cancel(task_id, received_only=True)
    with SessionLocal() as db:
        metadata=db.get(Task,task_id)
        if metadata and metadata.task_type=='world_character_chat':
            from .world.jobs import lock_task_world
            lock_task_world(db,task_id)
        task = lock_task(db, task_id)
        # Cancellation linearizes against the durable provider-call intent. A
        # cancellation committed before this row lock cannot authorize a call.
        assert_task_active(db, task, received_only=True)
        if task.task_type == "role_chat":
            from .character_cards import ensure_task_session_scope
            ensure_task_session_scope(db, task, owned(db, RoleSession, task.payload["sessionId"], task.owner_id))
        if task.task_type=='world_character_chat':
            from .play_controls.chat import active
            active(db,task)
        if task.task_type.startswith('world_') and prompt_name in ('world_director','world_narrator','world_character_chat'):
            from .play_controls.service import decorate
            data=decorate(task,data)
        budget_profile = owned(db, ModelProfile, task.payload["profileId"], task.owner_id, lock=True)
        from .prose import is_prose
        from .prose.policy import policy as prose_policy
        prose_run = is_prose(task)
        profile = execution_profile(db, task, budget_profile)
        if task.task_type=='world_scene_binding' and task.checkpoint.get('bindingInspection'):
            from .scenes.recovery import assert_execution
            assert_execution(task,budget_profile)
        consent = owned(db, Consent, task.payload["consentId"], task.owner_id, lock=True)
        previous = task.checkpoint.get("calls", {}).get(stage)
        if previous:
            path = private_path(task.owner_id, f"journal-{task.id}-{previous['callId']}.json")
            if path.exists():
                saved = json.loads(path.read_text("utf-8"))
                if saved["hash"] != digest(json.dumps(saved["result"], ensure_ascii=False, sort_keys=True)):
                    raise Problem("JOURNAL_INVALID", "持久检查点校验失败")
                # Release locks before an independent settlement transaction.
                db.commit()
                settle_usage(task_id, previous["callId"], saved["result"])
                if saved["result"].get("notSent"):
                    raise Problem(saved["result"]["notSent"], "供应商请求确认未发送，预算已释放")
                check_cancel(task_id, received_only=True)
                return saved["result"]
            raise Problem("PROVIDER_STATUS_UNKNOWN", "先前调用状态不确定，未自动重复请求")
        # A terminal task may expose an existing received journal; it cannot
        # reserve a new stage or send another provider request.
        assert_task_active(db, task)
        if budget_profile.status != "active" or profile.version != consent.profile_version or consent.task_id != task.id or consent.expires_at < now():
            raise Problem("CONSENT_REQUIRED", "通道或调用授权已失效", 403)
        if prose_run:
            from .models import GenerationBatch
            batch = db.scalar(select(GenerationBatch).where(GenerationBatch.task_id == task.id))
            policy = prose_policy(profile, batch.request['chapterLength'])
        else:
            policy = completion_policy(profile, world=task.task_type.startswith("world_"))
        if task.checkpoint.get("completionPolicy") != policy:
            raise Problem("CONSENT_REQUIRED", "模型执行策略已变化，请重新确认调用计划", 403)
        policy_stage = "analysis" if stage.startswith("analysis:") else "role_chat" if stage == "generation" and task.task_type == "role_chat" else stage.split(":", 1)[0]
        if policy_stage not in policy["stages"] or output_limit != policy["stages"][policy_stage]["totalCompletionTokens"]:
            raise Problem("BUDGET_NOT_AUTHORIZED", "调用参数不对应已授权阶段", 403)
        thinking_mode = policy["stages"][policy_stage]["thinking"]
        from .world.output import narrator_prompt_name
        system_prompt = frozen_prompt(task,narrator_prompt_name(prompt_name, data),PROMPTS)
        from .foundation.prompts import narrator_preferences
        data = narrator_preferences(task, data, prompt_name)
        # UTF-8 byte count is a conservative token upper estimate for byte-based tokenizers.
        if task.task_type.startswith("world_") and task.task_type!='world_script_breakdown':
            from .world.context_budget import fit_context
            cap = input_ceiling(profile, output_limit, world=True,vision=task.task_type=='world_scene_binding')
            data = fit_context(data, min(30000, cap - len(system_prompt.encode()) - 512))
        # Base64 is transport encoding, not tokenized image text. Reserve a
        # conservative vision allowance plus the actual textual context.
        image_count = len(data.get('_sceneImages', [])) if isinstance(data.get('_sceneImages'), list) else (1 if data.get('_sceneImage') else 0)
        input_estimate = len(wire_json({k:v for k,v in data.items() if k not in ('_sceneImage','_sceneImages')}).encode()) + len(system_prompt.encode()) + 256 + 8192 * image_count
        if input_estimate > input_ceiling(profile, output_limit, world=task.task_type.startswith("world_") or prose_run,
                script=task.task_type=='world_script_breakdown',vision=task.task_type=='world_scene_binding'):
            logger.warning('context_budget_exceeded stage=%s estimated=%s window=%s promptBytes=%s fieldBytes=%s',
                stage,input_estimate,profile.context_window,len(system_prompt.encode()),
                {k:len(wire_json({'value':v}).encode()) for k,v in data.items() if k not in ('_sceneImage','_sceneImages')})
            raise Problem("CONTEXT_BUDGET_EXCEEDED", "必要上下文超过已配置窗口；未发送请求")
        if consent.calls_reserved + 1 > consent.max_calls or consent.input_reserved + input_estimate > consent.input_limit or consent.output_reserved + output_limit > consent.output_limit:
            raise Problem("BUDGET_NOT_AUTHORIZED", "本次授权不足，未发送请求", 403)
        if call_budget_exceeded(budget_profile, 1) or input_budget_exceeded(budget_profile, input_estimate) or output_budget_exceeded(budget_profile, output_limit):
            raise Problem("BUDGET_NOT_AUTHORIZED", "通道预算不足，未发送请求", 403)
        consent.calls_reserved += 1
        consent.input_reserved += input_estimate
        consent.output_reserved += output_limit
        budget_profile.calls_reserved += 1
        budget_profile.input_reserved += input_estimate
        budget_profile.output_reserved += output_limit
        call_id = uid()
        usage = UsageRecord(owner_id=task.owner_id, task_id=task.id, provider_call_id=call_id, profile_id=profile.id, consent_id=consent.id,
            operation=stage, reserved_input=input_estimate, reserved_output=output_limit, usage_state="unknown", status="intent")
        from .usage_reporting import frozen_labels
        usage.billing_metadata = frozen_labels(db, task, profile)
        db.add(usage)
        from .foundation.wallet import reserve_usage
        reserve_usage(db, usage)
        task.checkpoint = {**task.checkpoint, "providerConfig": {"provider":profile.name,"modelId":profile.model_id,"profileVersion":profile.version,"outputParameter":profile.output_parameter,"contextWindow":profile.context_window}, "calls": {**task.checkpoint.get("calls", {}), stage: {"callId": call_id,"outputLimit":output_limit,"thinking":thinking_mode,"completionPolicyVersion":policy["version"],"reservedInput":input_estimate}}}
        task.provider_state = "running"
        stream_prose = prose_run and prompt_name == 'prose_chapter'
        if stream_prose:
            from .prose.stream import context as prose_stream_context
            prose_context = prose_stream_context(db, task, stage)
        db.commit()
        owner = task.owner_id
        book_receipt = task_book(db, task) is not None
        provider_conversation = task.payload.get("sessionId") or task.id
        stream_chat = is_stream_chat(task) and stage == "generation"
        stream_world = task.task_type.startswith("world_") and prompt_name in ("world_narrator","world_character_chat")
        if task.task_type=='world_character_chat':
            from .play_controls.attribution import requests_participation
            if requests_participation(task.payload.get('message','')):stream_world=False
        stream_script = task.task_type == "world_script_breakdown"
    adapter = MockAdapter() if settings.ai_mode == "mock" else ChatCompletionsAdapter()
    def publish(content):
        if stream_world or stream_prose or stream_script:
            check_cancel(task_id)
        else:
            check_stream_active(task_id)
        # Every timed publication rechecks cancellation, authorization and lease.
        if stream_script:
            persist_script_stream(task_id, stage, content)
        elif stream_prose:
            from .prose.stream import persist_raw
            persist_raw(prose_context, content)
        elif stream_world:
            from .world.jobs import persist_stream
            persist_stream(task_id, stage, content)
        else:
            persist_reply_stream(task_id, content)
    buffer = None
    def active():
        buffer.check()
        check_cancel(task_id) if stream_world or stream_prose or stream_script else check_stream_active(task_id)
    received_result = None
    try:
        if stream_chat or stream_world or stream_prose or stream_script:
            buffer = _DeadlineStreamBuffer(task_id, publish)
            try:
                result = adapter.generate_stream(profile, system_prompt, data, output_limit, on_delta=buffer.add,
                    cancel_check=active, conversation_id=provider_conversation, thinking_mode=thinking_mode)
                received_result = result
                # The complete supplier receipt precedes all optional final
                # stream mirrors. A concurrent deletion may reject the mirror
                # even when the deadline buffer has already flushed cleanly.
                record_received_result(owner, task_id, call_id, result,
                    book_receipt=book_receipt)
            finally:
                try:
                    buffer.close()
                except BaseException:
                    if received_result is not None:
                        record_received_result(owner, task_id, call_id, received_result,
                            book_receipt=book_receipt)
                    raise
                finally:
                    partial = buffer.text
                    if partial or stream_prose and received_result is not None:
                        if stream_script:
                            persist_script_stream(task_id, stage, partial)
                        elif stream_prose:
                            from .prose.stream import persist_raw
                            try:
                                persist_raw(prose_context, received_result['text'] if received_result is not None else partial,
                                    finish_reason=received_result.get('finishReason') if received_result is not None else None)
                            except (Problem, LeaseLost, OSError):
                                if received_result is not None:
                                    record_received_result(owner, task_id, call_id, received_result,
                                        book_receipt=book_receipt)
                                raise
                        elif stream_world:
                            from .world.jobs import persist_stream
                            persist_stream(task_id, stage, partial, final=True)
                        else:
                            persist_reply_stream(task_id, partial, check_scope=False)
        else:
            result = adapter.generate(profile, system_prompt, data, output_limit, conversation_id=provider_conversation, thinking_mode=thinking_mode) if settings.ai_mode == "real" else adapter.generate(profile, system_prompt, data, output_limit)
    except Problem as error:
        # Keep only diagnostic metadata. Exception messages can contain headers,
        # prompts or a supplier response and must never be logged or persisted.
        diagnostic = {"stage": stage, "errorCode": error.code}
        from .provider_rejection import CATEGORIES,CODES
        if error.details.get('rejectionCategory') in CATEGORIES:
            diagnostic['rejectionCategory']=error.details['rejectionCategory']
        if error.details.get('rejectionCode') in CODES:
            diagnostic['rejectionCode']=error.details['rejectionCode']
        if error.details.get('responseReceived') is True:
            diagnostic['responseReceived']=True
        if error.details.get('httpStatus')==429:
            from .scenes.cooldown import failure_metadata
            cooldown_metadata=failure_metadata(error,profile)
            diagnostic.update(cooldown_metadata);error.details.update(cooldown_metadata)
        request_id=error.details.get('upstreamRequestId')
        if isinstance(request_id,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',request_id):
            diagnostic['upstreamRequestId']=request_id
        if error.details.get("reason") in {"timeout", "transport_error", "invalid_response", "client_error", "http_error", "connection_failed"}:
            diagnostic["reason"] = error.details["reason"]
        exception_type = error.details.get("exceptionType")
        if isinstance(exception_type, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", exception_type):
            diagnostic["exceptionType"] = exception_type
        for key in ("latencyMs", "httpStatus", "connectionAttempts"):
            if type(error.details.get(key)) is int and error.details[key] >= 0:
                diagnostic[key] = error.details[key]
        if error.details.get("requestSent") is False:
            diagnostic["requestSent"] = False
        raw_stream=error.details.get('streamDiagnostic')
        if isinstance(raw_stream,dict):
            safe_stream={k:v for k,v in raw_stream.items() if k in ('frames','contentCharacters','reasoningCharacters','lines','lastFrameLatencyMs','firstContentLatencyMs') and type(v) is int and v>=0}
            if raw_stream.get('responseType') in ('text/event-stream','application/json','text/html','text/plain','other'):safe_stream['responseType']=raw_stream['responseType']
            if type(raw_stream.get('doneSeen')) is bool:safe_stream['doneSeen']=raw_stream['doneSeen']
            if safe_stream:diagnostic['streamDiagnostic']=safe_stream
        logger.warning("provider_failure task=%s stage=%s code=%s reason=%s exception_type=%s latency_ms=%s http_status=%s",
            task_id, stage, error.code, diagnostic.get("reason", "unspecified"),
            diagnostic.get("exceptionType", "unspecified"), diagnostic.get("latencyMs"), diagnostic.get("httpStatus"))
        try:
            with SessionLocal() as diagnostic_db:
                failed = lock_task(diagnostic_db, task_id)
                if failed and lease_matches(failed) and failed.state not in TERMINAL and not failed.result.get("deleted"):
                    from .scenes.cooldown import merge_receipt
                    failed.checkpoint = {**failed.checkpoint, "providerFailure": diagnostic, **(merge_receipt(failed.checkpoint, diagnostic) if diagnostic.get("httpStatus")==429 else {})}
                    failed.provider_state, failed.usage_state = ("not_sent", "known") if error.code in NOT_SENT_CODES else ("unknown", "unknown")
                    if error.code not in NOT_SENT_CODES:
                        from .foundation.wallet import settle
                        settle(diagnostic_db, owner, "provider:" + call_id, 0, unknown=True)
                    diagnostic_db.commit()
        except Exception:
            logger.warning("provider_failure_metadata_unavailable task=%s", task_id)
        if error.code in NOT_SENT_CODES:
            result = {"notSent": error.code, "usage": {"state": "known", "inputTokens": 0, "outputTokens": 0},
                **{key: diagnostic[key] for key in ("latencyMs", "connectionAttempts") if key in diagnostic}}
            record_received_result(owner, task_id, call_id, result, book_receipt=book_receipt)
        raise
    # If the DB dies here, recovery reads this durable result, never sends the request again.
    record_received_result(owner, task_id, call_id, result, book_receipt=book_receipt)
    check_cancel(task_id)
    return result


def ensure_snapshot(task_id):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        anchor = task.payload["anchor"]
        _, revision, _ = validate_anchor(db, anchor, task.owner_id)
        profile = owned(db, ModelProfile, task.payload["profileId"], task.owner_id)
        profile = execution_profile(db, task, profile)
        from .story_flow import trusted_index_task
        allow_unread = trusted_index_task(db, task)
        blocks, scope_hash, snapshot = find_snapshot(db, task.owner_id, anchor, revision, profile, allow_unread=allow_unread)
        if snapshot:
            return snapshot.id
        chunks, missing = missing_chunks(db, task.owner_id, anchor, blocks, profile)
        count = len(missing)
        batch_size = task.checkpoint.get("analysisBatchSize", 20) if allow_unread else 20
        if type(batch_size) is not int or not 1 <= batch_size <= 20:
            raise Problem("BUDGET_NOT_AUTHORIZED", "分析批次不对应内部授权", 403)
        to_process = missing[:batch_size]
    for index, (cache_hash, chunk) in enumerate(to_process):
        check_cancel(task_id)
        # Earlier chunks only: never borrow a character from later material.
        with SessionLocal() as db:
            earlier = []
            for preceding in chunks:
                if chunk_hash(preceding, anchor, profile) == cache_hash:
                    break
                cached = db.scalar(select(AnalysisChunk).where(AnalysisChunk.owner_id == task.owner_id, AnalysisChunk.cache_hash == chunk_hash(preceding, anchor, profile)))
                if cached:
                    earlier.append(cached.data)
            merged = merge_analysis(earlier)
            joined = "\n".join(b["text"] for b in chunk)
            identity_context = prior_identity_context(merged, joined, revision)
            prior_chars = identity_context['priorCharacters']
        # Classification is relative to this immutable chunk endpoint, never the
        # eventual requested snapshot. This dependency is derivable from chunk_hash.
        result = call_provider(task_id, "analysis:" + cache_hash[:16], "analysis", {"blocks": chunk, "scopeHash": cache_hash, "analysisPoint": {"blockId": chunk[-1]["blockId"], "sourceEnd": chunk[-1]["sourceStart"] + len(chunk[-1]["text"]), "offsetUnit": "unicode_codepoint", "timeBasis": "last explicit scene in this chunk; unknown if unresolved"}, **identity_context, "_structured": True}, completion_limit(profile, "analysis"))
        if result.get("finishReason") not in ("stop", "completed"):
            raise Problem("ANALYSIS_OUTPUT_INCOMPLETE", "模型分析没有完整结束，未建立事实快照")
        with SessionLocal() as db:
            task = lock_task(db, task_id)
            assert_task_active(db, task)
            analyzed = validate_analysis(result["text"], chunk, cache_hash, db, task.owner_id, anchor, prior_chars)
            db.add(AnalysisChunk(owner_id=task.owner_id, book_id=anchor["bookId"], branch_id=anchor["branchId"], cache_hash=cache_hash, data=analyzed))
            task.result = {**task.result, "analysisCompletedBlocks": index + 1, "analysisBatchBlocks": len(to_process), "analysisRemainingBlocks": count - index - 1}
            add_event(db, task, "state", {"state": "preparing", "completedBlocks": index + 1, "batchBlocks": len(to_process)})
            db.commit()
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        assert_task_active(db, task)
        parts = []
        for chunk in chunks:
            cache_hash = chunk_hash(chunk, anchor, profile)
            cached = db.scalar(select(AnalysisChunk).where(AnalysisChunk.owner_id == task.owner_id, AnalysisChunk.cache_hash == cache_hash))
            if cached:
                parts.append(cached.data)
        complete = len(parts) == len(chunks)
        snapshot = KnowledgeSnapshot(owner_id=task.owner_id, book_id=anchor["bookId"], branch_id=anchor["branchId"], revision_id=anchor["revisionId"], boundary=anchor,
                 scope_hash=scope_hash, profile_version=f"{profile.id}:{profile.version}", analyzer_version=ANALYZER,
                 data=merge_analysis(parts), completeness="complete" if complete else "partial")
        db.add(snapshot)
        db.flush()
        task.checkpoint = {**task.checkpoint, "snapshotId": snapshot.id, "contextHash": scope_hash, "promptVersion": ANALYZER}
        task.result = {**task.result, "snapshotId": snapshot.id, "aiStatus": "ai_ready" if complete else "ai_partial"}
        db.commit()
        return snapshot.id


def local_outcome(output, prefix, task_type, finish_reason):
    if finish_reason not in ("stop", "completed"):
        return "partial"
    if not output.strip():
        return "empty"
    if output.lstrip().lower().startswith(("<!doctype", "<html")) or "```" in output:
        return "invalid_format"
    # The presentation contract is prose, including when a provider emits an
    # invalid object containing an unquoted expression. Never unwrap its text.
    if re.match(r'''^[\[{]\s*(?:\{\s*)?["'][^"'\r\n]{1,80}["']\s*:''', output.lstrip()):
        return "invalid_format"
    if task_type == "role_chat" and re.search(
        r"\bdata\.(?:character(?:Facts|Names)?|confirmedFacts|contextEncoding|scope|prefix|sourcePositions|history|evidence|priorCharacters|priorIdentityEvidence)\b",
        output,
    ):
        return "invalid_format"
    if output in "\n".join(b["text"] for b in prefix):
        return "input_copy"
    if len(output) < (80 if task_type == "role_chat" else 600):
        return "too_short"
    if len(output) > (300 if task_type == "role_chat" else 1200):
        return "too_long"
    paragraphs = [p.strip() for p in output.splitlines() if p.strip()]
    if len(paragraphs) > 1 and len(set(paragraphs)) / len(paragraphs) < .7:
        return "repetitive"
    return "complete"


def outside_reader_physical_directive(output, identity_mode, story_names=()):
    """Bounded Chinese directive guard, in addition to semantic validation."""
    if identity_mode != "reader_out_of_story":
        return False
    body = output.strip()
    # Only an explicit recipient drawn from allowed story characters permits
    # an attributed quotation. Unattributed quotes must not hide a directive.
    names = [re.escape(name) for name in story_names if isinstance(name, str) and name and name not in {"你", "您", "读者", "玩家", "用户"}]
    if names:
        attribution = r"我(?:曾|当时)?(?:(?:对|向)|(?:告诉|提醒))(?:" + "|".join(names) + r''')[^。！？；“”‘’"\n]{0,10}'''
        for left, right in (("“", "”"), ("‘", "’"), ('"', '"')):
            quoted = attribution + re.escape(left) + "[^" + re.escape(left + right) + "]*" + re.escape(right)
            body = re.sub(quoted, " ", body)
    body = body.translate(str.maketrans({char: " " for char in '“”‘’"'}))
    for clause in re.split(r"[。！？；，,:：\n]", body):
        if re.match(r"^\s*你(?:(?:也|就|可以|现在|此刻|先|只|暂时)){0,3}在(?:旁边|我身边|我旁边)\s*(?:看着|站着|待着|等着)", clause):
            return True
        # An imperative may omit its second-person subject. Require a concrete
        # physical object and action; reading a word or first-person narration
        # must remain permitted. Explicit story-recipient quotes were masked.
        if re.match(r"^\s*(?:你|您)?(?:(?:先|请|赶紧)){1,2}(?:把|将)\s*(?:(?:手头|眼前|手里|手中)的?)?\s*(?:(?:这|那)(?:根|条)?)?\s*绳(?:子|索)?\s*(?:收好|握紧|抓牢|系好|拉紧)", clause):
            return True
        match = re.match(r"^\s*你(?:(?:也|先|要|得|去|来|别|不要|赶紧|现在|此刻|就|还|最好|可以)){0,3}(?:空手)?(?:站|拿|握|收|抓|扶|抬|走|跑|跳|救|拉|推|递|送|搬|看|上船|下船|靠近|帮)", clause)
        if not match:
            continue
        if not re.search(r"空手|手上|手里|手中|手边|我的手|绳|船|码头|仓库|木板|窗|门|箱|岸|水|阿岑", clause):
            continue
        if "看" in match.group() and re.search(r"本章|文字|这段|段落|书里|小说|描述|阅读", clause):
            continue
        return True
    return False


def model_anchor_position(anchor):
    # Scope ownership/revision/hash are checked locally and supplied once.
    return {key: anchor[key] for key in ("chapterId", "blockId", "start", "end") if key in anchor}


def generation_context(db, task, snapshot):
    anchor = task.payload["anchor"]
    _, revision, _ = validate_anchor(db, anchor, task.owner_id)
    prefix = prefix_blocks(db, revision, anchor, task.owner_id)
    facts = [f for f in snapshot.data["facts"] if f["status"] == "confirmed"]
    profile = owned(db, ModelProfile, task.payload["profileId"], task.owner_id)
    profile = execution_profile(db, task, profile)
    from .prose import is_prose
    data = {"prefix": [], "confirmedFacts": [], "instruction": task.payload.get("instruction"), "scope": anchor}
    output_limit, prompt = completion_limit(profile, "generation"), "counterfactual" if task.payload["operation"] == "counterfactual" else "continuation"
    if is_prose(task):
        output_limit, prompt = task.checkpoint['completionPolicy']['stages']['generation']['totalCompletionTokens'], 'prose_chapter'
    if task.task_type == "role_chat":
        output_limit, prompt = completion_limit(profile, "role_chat"), "role_chat"
        session = owned(db, RoleSession, task.payload["sessionId"], task.owner_id)
        from .character_cards import ensure_task_session_scope
        ensure_task_session_scope(db, task, session)
        character = require_role_character(snapshot, session.character_id, db)
        messages = list(db.scalars(select(RoleMessage).where(RoleMessage.session_id == session.id, RoleMessage.status == "saved").order_by(RoleMessage.sequence.desc()).limit(24)))
        facts = [f for f in facts if session.character_id in f["knownBy"] and any(k["characterId"] == session.character_id and k.get("validity") == "at_scope" for k in f.get("knowledge", []))]
        data.update({"character": {"id": character["id"], "displayName": character["displayName"], "voice": character_voice(snapshot, session.character_id)}, "characterFacts": [],
             "message": task.payload["message"], "history": [{"role": m.role, "text": m.text} for m in reversed(messages)], "identityMode": session.identity_mode})
        data.pop("confirmedFacts")
        # A whole narrative block can contain an omniscient secret beside an ordinary action.
        # Supply only exact quotations attached to explicitly established character knowledge.
        prefix = []
    names = {c["id"]: c["displayName"] for c in snapshot.data["characters"]}
    data["characterNames"] = {cid: name for cid, name in names.items() if task.task_type != "role_chat" or cid == session.character_id or any(fact["subjectId"] == cid for fact in facts)}
    positions = {}
    def position_ref(anchor):
        position = model_anchor_position(anchor)
        reference = "p" + digest(canonical(position))[:16]
        positions[reference] = position
        return reference
    compact_facts = [{"id": f["id"], "subjectId": f["subjectId"], "subject": names.get(f["subjectId"], f["subjectId"]), "predicate": f["predicate"], "value": f["value"], "evidenceIds": f["evidenceIds"], "knownBy": [cid for cid in f["knownBy"] if task.task_type != "role_chat" or cid == session.character_id], "occurredOrder": f.get("occurredOrder"), "temporalRelation": f.get("temporalRelation", "unknown"), "readerRevealAnchor": position_ref(f.get("readerRevealAnchor") or {}), "knowledgeAt": [{"characterId": k["characterId"], "evidenceIds": k.get("evidenceIds", []), "learnedAt": position_ref(k["learnedAt"]), "learnedOrder": k["learnedOrder"], "validity": k.get("validity", "unknown")} for k in f.get("knowledge", []) if task.task_type != "role_chat" or k["characterId"] == session.character_id]} for f in facts]
    data["sourcePositions"] = positions
    data["characterFacts" if task.task_type == "role_chat" else "confirmedFacts"] = compact_facts
    evidence_ids = {eid for f in facts for eid in f["evidenceIds"]}
    evidence_ids.update(eid for fact in compact_facts for knowledge in fact["knowledgeAt"] for eid in knowledge["evidenceIds"])
    if task.task_type == "role_chat":
        evidence_ids.update(data['character']['voice']['evidenceIds'])
    data["evidence"] = [{"id": e["id"], "quote": e["quote"]} for e in snapshot.data["evidence"] if e["id"] in evidence_ids]
    if task.task_type == "role_chat":
        # The supporting quote may contain other, omniscient propositions. Role generation
        # receives only validated known claims and evidence IDs; full quotes stay in the gate.
        data["evidence"] = [{"id": e["id"]} for e in data["evidence"]]
    budget = input_ceiling(profile, output_limit)
    overhead = len(frozen_prompt(task,prompt,PROMPTS).encode()) + 256
    # Constraints and known facts are mandatory. Reject rather than silently truncate them.
    if len(wire_json(data).encode()) + overhead > budget:
        raise Problem("CONTEXT_BUDGET_EXCEEDED", "必要事实、角色知识或对话历史超过窗口；未省略约束")
    scene = []
    for block in reversed(prefix):
        item = {"blockId": block["blockId"], "text": block["text"]}
        if len(wire_json({**data, "prefix": [item]+scene}).encode()) + overhead > budget:
            if not scene:
                # Long paragraph: retain a legal Unicode suffix nearest the selected anchor.
                text = block["text"]
                while text and len(wire_json({**data, "prefix": [{**item, "text": text}]}).encode())+overhead > budget:
                    text = text[len(text)//4+1:]
                if text:
                    scene = [{**item, "text": text, "excerpt": True}]
            break
        scene.insert(0, item)
        if sum(len(b["text"]) for b in scene) >= 1800:
            break
    data["prefix"] = scene
    return data


def hidden_evidence(db, task, output):
    import re
    from .models import Book, Revision, ContentBlock
    anchor = task.payload["anchor"]
    book = owned(db, Book, anchor["bookId"], task.owner_id)
    original = owned(db, Revision, book.original_revision_id, task.owner_id)
    _, current, _ = validate_anchor(db, anchor, task.owner_id)
    kept = {b["blockId"]: b["text"] for b in prefix_blocks(db, current, anchor, task.owner_id)}
    kept_text = "\n".join(kept.values())
    # General lexical screening supplements the semantic gate; it is not a fixture-only
    # list of secrets. Matching future material is supplied only to the isolated validator.
    phrases = {output[i:i+8] for i in range(0, max(0,len(output)-7), 3) if output[i:i+8].strip() and output[i:i+8] not in kept_text}
    pattern = re.compile("|".join(re.escape(p) for p in sorted(phrases))) if phrases else None
    hidden = []
    for chapter in original.manifest:
        for block_id in chapter["blocks"]:
            block = owned(db, ContentBlock, block_id, task.owner_id)
            unread = block.text[len(kept.get(block_id, "")):]
            match = pattern.search(unread) if pattern else None
            suspicious = next((term for term in ("身份", "代号", "秘密", "两年前", "死亡", "死于", "旧信", "夜渡", "第一次") if term in unread and term in output), None)
            if match or suspicious:
                start = match.start() if match else unread.index(suspicious)
                hidden.append({"id": block.id, "text": unread[max(0,start-120):start+240]})
            if sum(len(b["text"]) for b in hidden) >= 1600:
                return hidden
    return hidden


def chapter_context(base, chapters, index, count, profile, prompt_name, chapter_number=None, prompt_text=None):
    """Carry accepted preceding prose forward, within the frozen input ceiling.

    Original confirmed facts are mandatory. Older generated chapters retain their
    titles and endings; the immediately preceding chapter remains complete.
    """
    if count == 1:
        return base
    data = {**base, "chapter": {"index": index, "count": count, "number": chapter_number or index},
            "previousChapters": [{"index": c["index"], "title": c["title"], "ending": c["text"][-120:]} for c in chapters[:-1]]}
    if chapters:
        data["prefix"] = [{"blockId": "generated:" + str(chapters[-1]["index"]), "text": chapters[-1]["text"]}]
    overhead = len((prompt_text if prompt_text is not None else (PROMPTS / (prompt_name + ".txt")).read_text("utf-8")).encode()) + 256
    if len(wire_json(data).encode()) + overhead > input_ceiling(profile, completion_limit(profile, "generation")):
        raise Problem("CONTEXT_BUDGET_EXCEEDED", "必要事实和前一章超过窗口；已完成章节已保留，未追加调用")
    return data


def chapter_output(output, index, count):
    text = output["text"]
    if count == 1:
        return "续写 · 第 1 章", text
    first, separator, body = text.strip().partition("\n")
    if not separator or not first.startswith("标题："):
        return None, text
    title = first[3:].strip()
    if not title or len(title) > 100 or any(c in title for c in "<>`{}[]"):
        return None, text
    return title, body.strip()


def record_validation(task_id, stage, diagnostic):
    """Retain enum/count diagnostics; never store response prose or evidence IDs here."""
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        assert_task_active(db, task)
        task.checkpoint = {**task.checkpoint, "validationDecision": {"stage": stage, **diagnostic}}
        if diagnostic.get("reason") == "provider_unavailable":
            call = task.checkpoint.get("calls", {}).get(stage)
            usage = db.scalar(select(UsageRecord).where(UsageRecord.provider_call_id == call["callId"])) if call else None
            if usage and usage.status == "intent":
                task.provider_state, task.usage_state = "unknown", "unknown"
        if diagnostic["status"] != "passed":
            task.error_code = rejection_code(diagnostic)
        db.commit()
    logger.info("validation_decision task=%s stage=%s status=%s reason=%s reason_code=%s",
                task_id, stage, diagnostic["status"], diagnostic["reason"], diagnostic.get("reasonCode", "none"))


def validate_chapter(task_id, snapshot_id, data, body, stage):
    transition(task_id, "validating")
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        hidden = hidden_evidence(db, task, body)
        snapshot = owned(db, KnowledgeSnapshot, snapshot_id, task.owner_id)
        used_ids = {e["id"] for e in data["evidence"]}
        evidence = [{"id": e["id"], "quote": e["quote"]} for e in snapshot.data["evidence"] if e["id"] in used_ids]
        profile = owned(db, ModelProfile, task.payload["profileId"], task.owner_id)
        profile = execution_profile(db, task, profile)
        output_limit = completion_limit(profile, "validation")
        cap = input_ceiling(profile, output_limit)
        validation_data = {"output": body, "prefix": data["prefix"], "previousChapters": data.get("previousChapters", []),
            "evidence": evidence, "facts": data["confirmedFacts"], "character": None, "identityMode": None,
            "characterNames": data["characterNames"], "scope": data["scope"], "sourcePositions": data["sourcePositions"],
            "hiddenOriginal": hidden, "operation": task.payload["operation"], "instruction": task.payload.get("instruction"), "_structured": True}
    overhead = len(frozen_prompt(task,"validation",PROMPTS).encode()) + 256
    while validation_data["prefix"] and len(wire_json(validation_data).encode()) + overhead > cap:
        validation_data["prefix"] = validation_data["prefix"][1:]
    verdict_result = call_provider(task_id, stage, "validation", validation_data, output_limit)
    diagnostic = inspect_verdict(verdict_result, used_ids, {e["id"] for e in hidden})
    record_validation(task_id, stage, diagnostic)
    return diagnostic["status"]


def process_chapters(task_id, snapshot_id, base_data, prompt_name):
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        assert_task_active(db, task)
        owner, count = task.owner_id, task.payload.get("chapterCount", 1)
        _, revision, _ = validate_anchor(db, task.payload["anchor"], owner)
        source_chapters = len({b["chapterId"] for b in prefix_blocks(db, revision, task.payload["anchor"], owner)})
        chapters = list(task.checkpoint.get("chapters", []))
        review_warnings = saved_warnings(task)
        interrupted = bool(task.checkpoint.get("reviewInterrupted") or task.checkpoint.get("generationInterrupted"))
        profile = owned(db, ModelProfile, task.payload["profileId"], owner)
        profile = execution_profile(db, task, profile)
        generation_limit = completion_limit(profile, "generation")
        task.result = {**task.result, "requestedChapters": count, "completedChapters": len(chapters)}
        db.commit()
    prompt_name = prompt_name + "_chapter" if count > 1 else prompt_name
    outcome, validation_status, rejected_body = "complete", reviewed_status(review_warnings), None
    if interrupted and len(chapters) < count:
        outcome = "partial"
    for index in (() if interrupted else range(len(chapters) + 1, count + 1)):
        check_cancel(task_id)
        data = chapter_context(base_data, chapters, index, count, profile, prompt_name, source_chapters + index, frozen_prompt(task,prompt_name,PROMPTS))
        with SessionLocal() as db:
            task = lock_task(db, task_id)
            assert_task_active(db, task)
            context_hash = digest(wire_json(data))
            task.checkpoint = {**task.checkpoint, "contextHash": context_hash,
                "semanticContextHash": digest(canonical(data)),
                "promptVersion": frozen_prompt(task,prompt_name,PROMPTS).splitlines()[0],
                "chapterContexts": {**task.checkpoint.get("chapterContexts", {}), str(index): context_hash}}
            db.commit()
        transition(task_id, "running")
        generation_stage = "generation" if index == 1 else "generation:" + str(index)
        validation_stage = "validation" if index == 1 else "validation:" + str(index)
        try:
            output = call_provider(task_id, generation_stage, prompt_name, data, generation_limit)
        except Problem as error:
            if not chapters or not error.code.startswith("PROVIDER_"):
                raise
            warning = {"code": "WRITING_INTERRUPTED", "chapterIndex": index}
            review_warnings.append(warning)
            with SessionLocal() as db:
                task = lock_task(db, task_id)
                assert_task_active(db, task)
                task.checkpoint = {**task.checkpoint, "generationInterrupted": True, "reviewWarnings": list(review_warnings)}
                task.error_code = error.code
                task.provider_state, task.usage_state = "unknown", "unknown"
                db.commit()
            outcome, validation_status = "partial", reviewed_status(review_warnings)
            break
        title, body = chapter_output(output, index, count)
        if count == 1:
            title = "续写 · 第 " + str(source_chapters + index) + " 章"
        outcome = local_outcome(body, data["prefix"], "generation", output["finishReason"])
        if title is None and outcome == "complete":
            outcome = "invalid_format"
        review_unavailable = False
        if outcome == "complete":
            validation_text = title + "\n\n" + body if count > 1 else body
            try:
                validate_chapter(task_id, snapshot_id, data, validation_text, validation_stage)
                with SessionLocal() as db:
                    diagnostic = db.get(Task, task_id).checkpoint["validationDecision"]
                warning = review_warning(diagnostic, index)
            except Problem as error:
                if not error.code.startswith("PROVIDER_"):
                    raise
                # A failed advisory request does not erase prose already received.
                # Stop subsequent stages after an uncertain call; never resend it.
                review_unavailable = True
                record_validation(task_id, validation_stage, {"status": "unknown", "reason": "provider_unavailable"})
                warning = {"code": "CHECK_UNAVAILABLE", "chapterIndex": index}
            if warning:
                review_warnings.append(warning)
            validation_status = reviewed_status(review_warnings)
        check_cancel(task_id)
        if outcome != "complete":
            rejected_body = body
            break
        chapter = {"index": index, "title": title, "text": body, "contentHash": digest(body)}
        with SessionLocal() as db:
            task = lock_task(db, task_id)
            assert_task_active(db, task)
            # One durable write per complete chapter, including advisory notes. A resumed lease never
            # regenerates its completed prefix, and provider journals cover the
            # narrower interval between receipt and this checkpoint commit.
            chapters.append(chapter)
            task.checkpoint = {**task.checkpoint, "chapters": list(chapters),
                "generationReviewPolicy": REVIEW_POLICY, "reviewWarnings": list(review_warnings),
                "reviewInterrupted": review_unavailable}
            task.result = {**task.result, "requestedChapters": count, "completedChapters": len(chapters)}
            add_event(db, task, "chapter_saved", {"completedChapters": len(chapters), "requestedChapters": count})
            db.commit()
        if review_unavailable and index < count:
            outcome = "partial"
            break
    check_cancel(task_id)
    transition(task_id, "persisting")
    if count == 1:
        combined = chapters[0]["text"] if chapters else (rejected_body or "")
    else:
        combined = "\n\n".join(c["title"] + "\n\n" + c["text"] for c in chapters)
        if rejected_body is not None:
            combined += "\n\n" + rejected_body
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        assert_task_active(db, task)
        candidate = db.scalar(select(Candidate).where(Candidate.task_id == task.id))
        if not candidate:
            candidate = Candidate(owner_id=owner, task_id=task.id, text=combined, content_hash=digest(combined),
                partial=outcome == "partial", outcome=outcome, validation_status=validation_status)
            db.add(candidate)
            db.flush()
        task.result = {**task.result, "candidateId": candidate.id, "requestedChapters": count, "completedChapters": len(chapters)}
        add_event(db, task, "candidate_saved", {"candidateId": candidate.id, "persistenceState": "saved"})
        task.result_outcome, task.persistence_state, task.state = outcome, "saved", "succeeded"
        if outcome == "complete":
            task.error_code = None
        add_event(db, task, "terminal", {"state": "succeeded", "resultOutcome": outcome})
        db.commit()


def process_role_stream(task_id, snapshot_id, data, output_limit):
    output = call_provider(task_id, "generation", "role_chat", data, output_limit)
    persist_reply_stream(task_id, output["text"])
    outcome = local_outcome(output["text"], data["prefix"], "role_chat", output["finishReason"])
    warnings = []
    local_codes = {"partial": "WRITING_INTERRUPTED", "empty": "REPLY_EMPTY", "invalid_format": "REPLY_FORMAT",
        "input_copy": "REPLY_INPUT_COPY", "too_short": "REPLY_TOO_SHORT", "too_long": "REPLY_TOO_LONG", "repetitive": "REPLY_REPETITIVE"}
    if outcome != "complete":
        warnings.append(local_codes.get(outcome, "UNCERTAIN"))
    if outside_reader_physical_directive(output["text"], data.get("identityMode"), data["characterNames"].values()):
        warnings.append("CHARACTER_KNOWLEDGE")
    if output["text"].strip() and outcome != "partial":
        transition(task_id, "validating")
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            snapshot = owned(db, KnowledgeSnapshot, snapshot_id, task.owner_id)
            profile = owned(db, ModelProfile, task.payload["profileId"], task.owner_id)
            profile = execution_profile(db, task, profile)
            hidden = hidden_evidence(db, task, output["text"])
            used_ids = {e["id"] for e in data["evidence"]}
            validation_evidence = [{"id": e["id"], "quote": e["quote"]} for e in snapshot.data["evidence"] if e["id"] in used_ids]
            validation_limit = completion_limit(profile, "validation")
            cap = input_ceiling(profile, validation_limit)
            validation_data = {"output": output["text"], "prefix": list(data["prefix"]), "evidence": validation_evidence,
                "facts": data.get("characterFacts", []), "character": data.get("character"), "identityMode": data.get("identityMode"),
                "characterNames": data["characterNames"], "scope": data["scope"], "sourcePositions": data["sourcePositions"],
                "hiddenOriginal": hidden, "operation": "role_chat", "_structured": True}
        overhead = len(frozen_prompt(task,"validation",PROMPTS).encode())+256
        while validation_data["prefix"] and len(wire_json(validation_data).encode())+overhead > cap:
            validation_data["prefix"] = validation_data["prefix"][1:]
        try:
            verdict = call_provider(task_id, "validation", "validation", validation_data, validation_limit)
            diagnostic = inspect_verdict(verdict, used_ids, {e["id"] for e in hidden})
            record_validation(task_id, "validation", diagnostic)
            if diagnostic["status"] != "passed":
                warning = review_warning(diagnostic, 1)
                warnings.append(warning["code"] if warning else "UNCERTAIN")
        except Problem as error:
            if error.code in {"TASK_CANCELLED", "ROLE_SCOPE_CHANGED", "ROLE_UNAVAILABLE", "ANCHOR_INVALID"}:
                raise
            warnings.append("CHECK_UNAVAILABLE")
    check_stream_active(task_id)
    status = "interrupted" if outcome == "partial" else "complete"
    persist_reply_stream(task_id, output["text"], status=status, warnings=list(dict.fromkeys(warnings)))
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        assert_task_active(db, task)
        from .character_cards import ensure_task_session_scope
        ensure_task_session_scope(db, task, owned(db, RoleSession, task.payload["sessionId"], task.owner_id))
        message_status = "interrupted" if outcome == "partial" else "review_warning" if warnings else "saved"
        save_stream_messages(db, task, output["text"], message_status)
        task.state, task.persistence_state = "succeeded", "saved"
        task.result_outcome = "partial" if outcome == "partial" else "complete"
        task.result = {**task.result, "reviewWarnings": list(dict.fromkeys(warnings))}
        add_event(db, task, "terminal", {"state": task.state, "resultOutcome": task.result_outcome})
        db.commit()


@leased_task
def process(task_id):
    try:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            assert_lease(task)
            if task.state in TERMINAL and task.persistence_state == "saved":
                return
            kind, owner = task.task_type, task.owner_id
        if kind == "story_flow":
            from .prose import is_prose
            if is_prose(task):
                from .prose.worker import process as process_prose
                process_prose(task_id)
            else:
                from .story_flow import process_flow
                process_flow(task_id)
            return
        if kind == "delete_account":
            clean_user(task_id, owner)
            return
        check_cancel(task_id)
        transition(task_id, "preparing")
        if kind == "import":
            from .importer import parse
            with SessionLocal() as db:
                task = db.get(Task, task_id)
                payload = task.payload
            parsed = parse(private_path(owner, payload["fileRef"]).read_bytes(), payload["filename"], owner, task_id, payload.get("encoding"))
            with SessionLocal() as db:
                task = lock_task(db, task_id)
                assert_task_active(db, task)
                ref = write_private(owner, f"parsed-{task_id}.json", json.dumps(parsed, ensure_ascii=False).encode())
                task.checkpoint = {"parsedRef": ref}
                task.result = {**task.result, "importStatus": "awaiting_confirmation", "parserVersion": "import-v1", "title": parsed.get("title") or Path(payload["filename"]).stem,
                   "encoding": parsed["encoding"], "warnings": parsed["warnings"], "chapters": [{"id": c["id"], "title": c["title"], "blockCount": len(c["blocks"])} for c in parsed["chapters"]], "preview": parsed["normalized"][:1000]}
                db.commit()
            transition(task_id, "succeeded", persistence_state="saved")
            return
        if kind == "connection_test":
            connected = call_provider(task_id, "connection_test", "connection", {"message": "连接测试"}, 32)
            if connected.get("finishReason") not in ("stop", "completed") or not connected.get("text", "").strip():
                raise Problem("PROVIDER_CONNECTION_INCOMPLETE", "模型连接测试未收到完整非空回复")
            transition(task_id, "succeeded", persistence_state="saved", result={"connected": True}, result_outcome="complete")
            return
        if kind == "role_chat":
            with SessionLocal() as db:
                role_task = db.get(Task, task_id)
                session = owned(db, RoleSession, role_task.payload["sessionId"], owner)
                from .character_cards import ensure_task_session_scope
                ensure_task_session_scope(db, role_task, session)
                snapshot_id = session.snapshot_id
        else:
            snapshot_id = ensure_snapshot(task_id)
        with SessionLocal() as db:
            snapshot = owned(db, KnowledgeSnapshot, snapshot_id, owner)
            if kind == "analysis":
                transition(task_id, "succeeded", persistence_state="saved", result_outcome="complete" if snapshot.completeness == "complete" else "partial")
                return
            if snapshot.completeness != "complete":
                raise Problem("AI_CONTEXT_NOT_READY", "分析尚未完成，请确认下一批调用计划")
            task = lock_task(db, task_id)
            assert_task_active(db, task)
            data = generation_context(db, task, snapshot)
            wire_context = wire_json(data)
            prompt_name = "role_chat" if kind == "role_chat" else "counterfactual" if task.payload["operation"] == "counterfactual" else "continuation"
            task.checkpoint = {**task.checkpoint, "snapshotId": snapshot.id, "contextHash": digest(wire_context), "semanticContextHash": digest(canonical(data)), "contextEncoding": json.loads(wire_context).get("contextEncoding", "plain-json"), "evidenceIds": [e["id"] for e in snapshot.data["evidence"]], "promptVersion": frozen_prompt(task,prompt_name,PROMPTS).splitlines()[0], "validationPromptVersion": frozen_prompt(task,"validation",PROMPTS).splitlines()[0]}
            db.commit()
        transition(task_id, "running")
        prompt = "role_chat" if kind == "role_chat" else "counterfactual" if task.payload["operation"] == "counterfactual" else "continuation"
        if kind == "generation":
            process_chapters(task_id, snapshot_id, data, prompt)
            return
        with SessionLocal() as db:
            profile = owned(db, ModelProfile, task.payload["profileId"], owner)
            profile = execution_profile(db, task, profile)
            generation_limit = completion_limit(profile, "role_chat" if kind == "role_chat" else "generation")
            stream_chat = is_stream_chat(task)
        if stream_chat:
            process_role_stream(task_id, snapshot_id, data, generation_limit)
            return
        output = call_provider(task_id, "generation", prompt, data, generation_limit)
        outcome = local_outcome(output["text"], data["prefix"], kind, output["finishReason"])
        if outcome == "complete" and kind == "role_chat" and outside_reader_physical_directive(output["text"], data.get("identityMode"), data["characterNames"].values()):
            outcome = "blocked"
        validation_status = "blocked"
        if outcome == "complete":
            transition(task_id, "validating")
            with SessionLocal() as db:
                task = db.get(Task, task_id)
                hidden = hidden_evidence(db, task, output["text"])
            with SessionLocal() as db:
                saved_snapshot = owned(db, KnowledgeSnapshot, snapshot_id, owner)
                used_ids = {e["id"] for e in data["evidence"]}
                validation_evidence = [{"id": e["id"], "quote": e["quote"]} for e in saved_snapshot.data["evidence"] if e["id"] in used_ids]
            validation_data = {"output": output["text"], "prefix": data["prefix"], "evidence": validation_evidence, "facts": data.get("confirmedFacts", data.get("characterFacts", [])),
                "character": data.get("character"), "identityMode": data.get("identityMode"), "characterNames": data["characterNames"], "scope": data["scope"], "sourcePositions": data["sourcePositions"], "hiddenOriginal": hidden, "operation": task.payload["operation"], "instruction": task.payload.get("instruction"), "_structured": True}
            with SessionLocal() as db:
                profile = owned(db, ModelProfile, task.payload["profileId"], owner)
                profile = execution_profile(db, task, profile)
                validation_limit = completion_limit(profile, "validation")
                cap = input_ceiling(profile, validation_limit)
            validation_overhead = len(frozen_prompt(task,"validation",PROMPTS).encode())+256
            while validation_data["prefix"] and len(wire_json(validation_data).encode())+validation_overhead > cap:
                validation_data["prefix"] = validation_data["prefix"][1:]
            verdict_result = call_provider(task_id, "validation", "validation", validation_data, validation_limit)
            diagnostic = inspect_verdict(verdict_result, used_ids, {e["id"] for e in hidden})
            record_validation(task_id, "validation", diagnostic)
            validation_status = diagnostic["status"]
            if validation_status != "passed":
                outcome = "blocked"
        check_cancel(task_id)
        transition(task_id, "persisting")
        with SessionLocal() as db:
            task = lock_task(db, task_id)
            assert_task_active(db, task)
            if kind == "generation":
                candidate = db.scalar(select(Candidate).where(Candidate.task_id == task.id))
                if not candidate:
                    candidate = Candidate(owner_id=owner, task_id=task.id, text=output["text"], content_hash=digest(output["text"]), partial=outcome == "partial", outcome=outcome, validation_status=validation_status)
                    db.add(candidate)
                    db.flush()
                task.result = {**task.result, "candidateId": candidate.id}
                add_event(db, task, "candidate_saved", {"candidateId": candidate.id, "persistenceState": "saved"})
            else:
                session = owned(db, RoleSession, task.payload["sessionId"], owner, lock=True)
                if validation_status == "passed" and outcome == "complete":
                    exists = db.scalar(select(RoleMessage).where(RoleMessage.session_id == session.id, RoleMessage.turn_id == task.payload["turnId"]))
                    if not exists:
                        sequence = db.scalar(select(func.coalesce(func.max(RoleMessage.sequence), 0)).where(RoleMessage.session_id == session.id))
                        for i, (role, content) in enumerate([("user", task.payload["message"]), ("assistant", output["text"])], 1):
                            db.add(RoleMessage(owner_id=owner, session_id=session.id, turn_id=task.payload["turnId"], sequence=sequence + i, role=role, text=content, task_id=task.id))
                session.pending_task_id = None
                task.result = {"sessionId": session.id, "turnId": task.payload["turnId"], "replySaved": outcome == "complete" and validation_status == "passed"}
            task.result_outcome, task.persistence_state, task.state = outcome, "saved", "succeeded"
            add_event(db, task, "terminal", {"state": "succeeded", "resultOutcome": outcome})
            db.commit()
    except LeaseLost:
        raise
    except Problem as error:
        fail_task(task_id, error.code)
    except Exception as error:
        logger.error("task=%s exception=%s", task_id, type(error).__name__)
        # Leave a recoverable lease and journal when database persistence is unavailable.
        try:
            fail_task(task_id, "PERSISTENCE_UNAVAILABLE")
        except Exception:
            pass


def fail_task(task_id, code):
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        if not task or not lease_matches(task) or (task.state in TERMINAL and task.persistence_state == "saved"):
            return
        task.error_code, task.state = code, "cancelled" if code == "TASK_CANCELLED" else "failed"
        task.provider_state = "unknown" if code == "PROVIDER_STATUS_UNKNOWN" else task.provider_state
        task.usage_state = "unknown" if task.provider_state in ("unknown", "running") else task.usage_state
        task.persistence_state = "recoverable" if code == "PERSISTENCE_UNAVAILABLE" or task.task_type == "delete_account" else "saved"
        task.result_outcome = "cancelled" if task.state == "cancelled" else "failed"
        if task.task_type == "role_chat" and task.persistence_state != "recoverable":
            if is_stream_chat(task):
                from .durable_stream import overlay
                stream = overlay(task, "replyStream")
                task.checkpoint = {**task.checkpoint, "replyStream": stream}
                content = stream.get("text", "")
                status = "stopped" if task.state == "cancelled" else "interrupted"
                from .durable_stream import persist
                extra = {"sessionId": task.payload["sessionId"], "turnId": task.payload["turnId"], "userMessage": task.payload["message"]}
                if "reviewWarnings" in stream:
                    extra["reviewWarnings"] = stream["reviewWarnings"]
                persist(db, task, "replyStream", content, status, extra=extra)
                save_stream_messages(db, task, content, status)
                add_event(db, task, "stream_updated", {"streamVersion": task.checkpoint["replyStream"]["version"], "status": status})
            session = db.get(RoleSession, task.payload["sessionId"])
            if session and session.pending_task_id == task.id:
                session.pending_task_id = None
        if task.task_type == "import":
            task.result = {**task.result, "importStatus": "cancelled" if task.state == "cancelled" else "failed"}
        if task.task_type == "delete_account":
            from .models import CleanupReceipt
            receipt = db.get(CleanupReceipt, task.payload.get("receiptId"))
            if receipt:
                receipt.state = "retrying"
        add_event(db, task, "terminal", {"state": task.state, "errorCode": code})
        db.commit()


def clean_user(task_id, owner):
    root = settings.storage_path.resolve()
    target = (root / owner).resolve()
    if target.parent != root or len(owner) != 32:
        raise Problem("CLEANUP_INVALID", "清理目标无效")
    with SessionLocal() as db:
        job = lock_task(db, task_id)
        assert_lease(job)
        if job.state in TERMINAL and job.persistence_state == "saved":
            return
        in_flight=list(db.scalars(select(Task).where(Task.owner_id==owner,Task.id!=task_id,Task.state.notin_(list(TERMINAL)),Task.state!="queued",Task.lease_expires_at>now())))
        if in_flight:
            job.state="queued"
            job.lease_expires_at=None
            db.commit()
            return
        tombstones = root / ".deleted"
        tombstones.mkdir(parents=True, exist_ok=True, mode=0o700)
        (tombstones / owner).touch(mode=0o600)
        if target.exists():
            shutil.rmtree(target)
        from .portable.service import purge_owner_files
        purge_owner_files(owner)
        from .foundation.storage import remove_account_objects
        remove_account_objects(db, owner)
        if db.scalar(text("SELECT to_regclass('image_assets')")) is not None:
            from .assets.service import purge_owner
            purge_owner(db, owner)
        user = db.get(User, owner)
        from .models import CleanupReceipt
        receipt = db.get(CleanupReceipt, job.payload.get("receiptId")) if job else None
        if receipt:
            receipt.state, receipt.completed_at = "completed", now()
        if user:
            db.delete(user)
        db.commit()


def renew_lease(task_id, lease_token):
    with SessionLocal() as db:
        task = lock_task(db, task_id)
        if not task or task.state in TERMINAL or task.lease_owner != lease_token:
            return False
        task.lease_expires_at = now() + timedelta(seconds=30)
        db.commit()
        return True


def heartbeat(task_id, stop, lease_token):
    while not stop.wait(10):
        try:
            if not renew_lease(task_id, lease_token):
                return
        except Exception:
            logger.warning("heartbeat task=%s unavailable", task_id)


@leased_task
def run_claimed(task_id):
    stop = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(task_id, stop, execution_token()), daemon=True)
    thread.start()
    try:
        process(task_id)
        with SessionLocal() as db:
            task=db.get(Task,task_id)
            if task and lease_matches(task) and task.state in TERMINAL and task.persistence_state=="saved":
                try:
                    mark_synced(task.owner_id,task.id,task.checkpoint.get("calls",{}))
                except OSError:
                    logger.warning("journal sync marker unavailable task=%s",task_id)
    finally:
        stop.set()


def claim(worker_id):
    with SessionLocal() as db:
        # Serialize the global concurrency and per-user limits across worker replicas.
        db.execute(text("SELECT pg_advisory_xact_lock(82370213)"))
        active = list(db.scalars(select(Task).where(Task.task_type.not_like("world_%"), Task.state.notin_(list(TERMINAL) + ["queued"]), Task.lease_expires_at > now())))
        if len(active) >= 4:
            return None
        candidates = list(db.scalars(select(Task).where(Task.task_type.not_like("world_%"), (Task.state == "queued") | ((Task.state.notin_(list(TERMINAL))) & (Task.lease_expires_at < now())) | ((Task.state == "failed") & (Task.persistence_state == "recoverable"))).order_by(case((Task.task_type == "story_flow", 1), else_=0), Task.created_at).with_for_update(skip_locked=True).limit(20)))
        for task in candidates:
            if sum(t.owner_id == task.owner_id for t in active) >= 2:
                continue
            token = uid()
            task.state, task.lease_owner, task.lease_expires_at = "preparing", token, now() + timedelta(seconds=30)
            db.commit()
            return ClaimedTask(task.id, token)
    return None


def run():
    from .logging_config import setup_logging
    setup_logging()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker_id = uid()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending = set()
        maintenance_at=0
        while True:
            pending = {f for f in pending if not f.done()}
            try:
                if len(pending) < 4:
                    task_id = claim(worker_id)
                    if task_id:
                        pending.add(pool.submit(run_claimed, task_id))
                with SessionLocal() as db:
                    for event in db.scalars(select(Outbox).where(Outbox.status == "pending").with_for_update(skip_locked=True).limit(20)):
                        # Old pinned snapshots remain valid; a new revision cannot hit their cache keys.
                        event.status = "processed"
                    db.commit()
                if time.monotonic()-maintenance_at>60:
                    if not pending:
                        from .portable.service import maintenance as portable_maintenance
                        portable_maintenance()
                        with SessionLocal() as db:
                            if db.scalar(text("SELECT to_regclass('image_assets')")) is not None:
                                from .assets.service import expired_uploads
                                expired_uploads(db)
                                db.commit()
                    clean_deleted_books()
                    clean_deleted_world_journals()
                    from .durable_stream import clean_deleted
                    clean_deleted()
                    clean_synced_journals()
                    clean_orphan_files()
                    maintenance_at=time.monotonic()
            except Exception as error:
                logger.warning("queue unavailable error=%s", type(error).__name__)
            time.sleep(1)


if __name__ == "__main__":
    run()


def persist_script_stream(task_id, stage, content):
    from .world.jobs import lock_task_world
    with SessionLocal() as db:
        world=lock_task_world(db,task_id)
        task=db.scalar(select(Task).where(Task.id==task_id).with_for_update())
        assert_task_active(db,task)
        task.checkpoint={**task.checkpoint,'scriptStream':{'stage':stage,'content':content,'receivedCharacters':len(content)}}
        if world and world.prepare_task_id==task.id:
            world.source={**world.source,'script':{**world.source['script'],'receivedCharacters':len(content)}}
        db.commit()
