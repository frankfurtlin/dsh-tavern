"""Durable real deltas, finite requests and recovery; no paid provider traffic."""
import json
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from app.prose.presentation import project as project_prose
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import select, func

from app.db import SessionLocal
from app.errors import Problem
from app.models import Task, RoleMessage, RoleSession, UsageRecord, TaskEvent, Consent, ModelProfile
from app.providers import ChatCompletionsAdapter, MockAdapter, encrypt_key
from app.worker import process, generation_context
from app.task_lease import LeaseLost
from .helpers import BASE, read_chapter, observe, submit
from .test_character_cards import index, card_list, chat


@pytest.fixture
def tls_stream(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "approved.test")])
    instant = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(instant-timedelta(minutes=1))
        .not_valid_after(instant+timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("approved.test")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path/"ca.pem", tmp_path/"key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    state = SimpleNamespace(requests=[], sni=[], release=threading.Event(), completed=threading.Event(), mode="normal", status=200, hold_seconds=3)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state.requests.append({"body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))), "host": self.headers["Host"]})
            if state.mode == "hold_headers":
                state.release.wait(timeout=state.hold_seconds)
                return
            self.send_response(state.status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            def frame(value):
                data = "data: "+(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))+"\r\n\r\n"
                encoded = data.encode()
                for offset in range(0, len(encoded), 7):
                    self.wfile.write(encoded[offset:offset+7])
                self.wfile.flush()
            try:
                if state.status != 200:
                    frame({"error": {"message": "PRIVATE-SUPPLIER-ERROR"}})
                    return
                frame({"choices": [{"index": 0, "delta": {"reasoning_content": "PRIVATE-REASONING"}}]})
                if state.mode == "reasoning_only":
                    state.release.wait(timeout=3)
                    return
                frame({"choices": [{"index": 0, "delta": {"content": "眼前"}}, {"index": 1, "delta": {"content": "WRONG-CHOICE"}}]})
                if state.mode == "slow_active":
                    time.sleep(.25)
                state.release.wait(timeout=3)
                if state.mode == "hold":
                    return
                if state.mode == "invalid":
                    frame("PRIVATE-INVALID-JSON")
                    return
                frame({"choices": [{"index": 0, "delta": {"content": "的灯。"}}]})
                frame({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                frame({"choices": [], "usage": {"prompt_tokens": 17, "completion_tokens": 4}})
                if state.mode != "missing_done":
                    frame("[DONE]")
                state.completed.set()
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    tls.set_servername_callback(lambda sock, hostname, context: state.sni.append(hostname))
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client_tls = ssl.create_default_context(cafile=str(cert_path))
    factory = httpx.AsyncClient
    monkeypatch.setattr("app.providers.validate_url", lambda url: ("approved.test", "127.0.0.1"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: factory(verify=client_tls, **kwargs))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    profile = SimpleNamespace(base_url=f"https://approved.test:{server.server_port}/v1", key_cipher=encrypt_key("TEST-ONLY"),
        model_id="local-stream", output_parameter="max_completion_tokens", supports_json=False)
    try:
        yield state, profile
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_real_tls_delta_arrives_before_completion_and_final_usage(tls_stream):
    state, profile = tls_stream
    deltas = []
    def receive(delta):
        assert not state.completed.is_set() or deltas
        deltas.append(delta)
        state.release.set()
    result = ChatCompletionsAdapter().generate_stream(profile, "role_chat-test", {}, 256, on_delta=receive, cancel_check=lambda: None)
    assert deltas == ["眼前", "的灯。"] and result["text"] == "眼前的灯。"
    assert result["finishReason"] == "stop" and result["usage"] == {"inputTokens": 17, "outputTokens": 4, "state": "known"}
    assert len(state.requests) == 1 and state.sni == ["approved.test"]
    body = state.requests[0]["body"]
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["max_completion_tokens"] == 256 and state.requests[0]["host"] == "approved.test"


def test_active_tls_stream_finishes_after_the_nonstream_deadline(tls_stream, monkeypatch):
    state, profile = tls_stream
    state.mode = "slow_active"
    monkeypatch.setattr("app.providers.REQUEST_TOTAL_TIMEOUT_SECONDS", .1)
    monkeypatch.setattr("app.providers.STREAM_TOTAL_TIMEOUT_SECONDS", 2)
    deltas = []
    def receive(delta):
        deltas.append(delta)
        state.release.set()
    result = ChatCompletionsAdapter().generate_stream(profile, "chapter-test", {}, 256,
        on_delta=receive, cancel_check=lambda: None)
    assert result["text"] == "眼前的灯。" and result["finishReason"] == "stop"
    assert result["latencyMs"] >= 250 and result["usage"]["state"] == "known"
    assert deltas == ["眼前", "的灯。"] and len(state.requests) == 1


def test_stalled_tls_stream_keeps_partial_text_and_times_out_without_retry(tls_stream, monkeypatch):
    state, profile = tls_stream
    state.mode = "hold"
    # Include actual local TLS startup; .3s could expire before HTTP headers on
    # a loaded host and would test connection timeout rather than saved prose.
    state.hold_seconds = 5
    monkeypatch.setattr("app.providers.STREAM_TOTAL_TIMEOUT_SECONDS", 2)
    deltas = []
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate_stream(profile, "chapter-test", {}, 256,
            on_delta=deltas.append, cancel_check=lambda: None)
    assert failed.value.code == "PROVIDER_STATUS_UNKNOWN"
    assert failed.value.details["reason"] == "timeout" and failed.value.details["httpStatus"] == 200
    assert deltas == ["眼前"] and len(state.requests) == 1


def test_local_tls_stream_propagates_execution_lease_loss(tls_stream):
    state, profile = tls_stream
    deltas = []
    def receive(delta):
        deltas.append(delta)
        state.release.set()
        raise LeaseLost()
    with pytest.raises(LeaseLost):
        ChatCompletionsAdapter().generate_stream(profile, "role_chat-test", {}, 256,
            on_delta=receive, cancel_check=lambda: None)
    assert deltas == ["眼前"] and len(state.requests) == 1


@pytest.mark.parametrize("mode", ["missing_done", "invalid"])
def test_real_tls_unknown_does_not_retry_or_expose_supplier_body(tls_stream, mode):
    state, profile = tls_stream
    state.mode = mode
    deltas = []
    def receive(delta):
        deltas.append(delta)
        state.release.set()
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate_stream(profile, "role_chat-test", {}, 256, on_delta=receive, cancel_check=lambda: None)
    assert failed.value.code == "PROVIDER_STATUS_UNKNOWN" and len(state.requests) == 1
    assert deltas[0] == "眼前"
    assert "PRIVATE" not in str(failed.value.details)


def test_real_tls_cancel_while_upstream_waits_closes_without_retry(tls_stream):
    state, profile = tls_stream
    state.mode = "hold"
    cancelled, deltas = threading.Event(), []
    def receive(delta):
        deltas.append(delta)
        cancelled.set()
    def check():
        if cancelled.is_set():
            raise Problem("TASK_CANCELLED", "stopped")
    start = time.monotonic()
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate_stream(profile, "role_chat-test", {}, 256, on_delta=receive, cancel_check=check)
    assert failed.value.code == "TASK_CANCELLED"
    assert deltas == ["眼前"] and time.monotonic()-start < 2
    assert len(state.requests) == 1


@pytest.mark.parametrize("mode", ["hold_headers", "reasoning_only"])
def test_real_tls_cancel_before_any_prose_does_not_wait_for_a_delta(tls_stream, mode):
    state, profile = tls_stream
    state.mode = mode
    deltas = []
    def check():
        if state.requests:
            raise Problem("TASK_CANCELLED", "stopped")
    start = time.monotonic()
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate_stream(profile, "role_chat-test", {}, 256,
            on_delta=deltas.append, cancel_check=check)
    assert failed.value.code == "TASK_CANCELLED"
    assert deltas == [] and time.monotonic()-start < 2
    assert len(state.requests) == 1


def prepare_chat(client, reader, demo, profile):
    index(client, demo)
    anchor = read_chapter(client, demo)["blocks"][1]["endAnchor"]
    observe(client, reader, anchor)
    person = next(c for c in card_list(client, demo, anchor).json()["items"] if c["name"] == "林渡")
    return chat(client, demo, anchor, person)


def send(client, session, message="你在想什么？", turn="stream-turn"):
    result = client.post(f"{BASE}/role-sessions/{session['id']}/send", json={"message": message, "turnId": turn},
        headers={"Idempotency-Key": "stream-send-"+turn})
    assert result.status_code == 202, result.text
    return result.json()["taskId"]


def test_queue_user_and_publish_durable_deltas_before_generation_finishes(client, reader, demo, profile, monkeypatch):
    session = prepare_chat(client, reader, demo, profile)
    task_id = send(client, session)
    queued = client.get(BASE+"/tasks/"+task_id).json()
    assert queued["replyStream"] == {"sessionId": session["id"], "turnId": "stream-turn", "userMessage": "你在想什么？", "text": "", "version": 0, "status": "waiting"}
    messages = client.get(f"{BASE}/role-sessions/{session['id']}/messages").json()["items"]
    assert len(messages) == 1 and messages[0]["role"] == "user" and messages[0]["status"] == "pending"
    snapshots = []
    original = MockAdapter.generate_stream
    def inspect(self, *args, on_delta, **kwargs):
        def receive(text):
            on_delta(text)
            deadline = time.perf_counter() + .15
            while time.perf_counter() < deadline:
                with SessionLocal() as db:
                    task = db.get(Task, task_id)
                    current = dict(task.checkpoint["replyStream"])
                    current_state = task.state
                if current["text"]:
                    snapshots.append((current_state, current)); break
                time.sleep(.002)
        return original(self, *args, on_delta=receive, **kwargs)
    monkeypatch.setattr(MockAdapter, "generate_stream", inspect)
    process(task_id)
    final = client.get(BASE+"/tasks/"+task_id).json()
    assert snapshots[0][0] == "running" and len(snapshots[0][1]["text"]) < len(final["replyStream"]["text"])
    assert final["replyStream"]["status"] == "complete" and final["result"]["replySaved"]
    assert final["replyStream"]["version"] > snapshots[0][1]["version"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RoleMessage).where(RoleMessage.task_id == task_id)) == 2
        assert all(m.status == "saved" for m in db.scalars(select(RoleMessage).where(RoleMessage.task_id == task_id)))
        events = list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.type == "stream_updated")))
        assert events and all("text" not in e.payload for e in events)
        assert len(list(db.scalars(select(UsageRecord).where(UsageRecord.task_id == task_id)))) == 2
    assert send(client, session) == task_id
    _assert_stream_delivery_releases_lifecycle(task_id, reader, demo, monkeypatch)


def _assert_stream_delivery_releases_lifecycle(task_id, reader, demo, monkeypatch):
    """Actual PG lock probe while the real SSE generator is suspended.

    Replacing only the response wrapper lets the consumer hold a yielded frame
    without a socket timeout. Scope checks, queries and transaction boundaries
    remain the production generator; this is not a TCP latency measurement.
    """
    from starlette.requests import Request
    from sqlalchemy import text
    from app import api_ai
    from app.domain import digest
    from app.models import DeviceSession, User

    request = Request({"type": "http", "headers": []})
    with monkeypatch.context() as response_wrapper:
        response_wrapper.setattr(api_ai, "StreamingResponse", lambda body, **kwargs: body)
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            owner_id = task.owner_id
            request.state.session = db.get(DeviceSession, reader["sessionId"])
            generator = api_ai.events(task_id, request, afterSeq=task.event_seq-1,
                                      user=db.get(User, owner_id), db=db)
        try:
            frame = next(generator)
            assert "event: terminal" in frame
            lock_id = int(digest("book-lifecycle:"+owner_id+":"+demo["bookId"])[:15], 16)
            with SessionLocal() as conn:
                # A network consumer may be slow; it must not hold the book
                # transaction and block the same authorized body's HTTP GET.
                available = conn.scalar(text("SELECT pg_try_advisory_xact_lock(:id)"), {"id": lock_id})
            assert available is True
        finally:
            generator.close()


def test_short_reply_fragment_is_durable_before_the_provider_can_pause(client, reader, demo, profile, monkeypatch):
    session = prepare_chat(client, reader, demo, profile)
    task_id = send(client, session, turn="short-burst-before-pause")
    original = MockAdapter.generate_stream
    seen = []
    observations = []
    def inspect(self, *args, on_delta, **kwargs):
        def receive(text):
            # A deterministic burst/pause control, not a latency sample. Only
            # the cadence decision is frozen; scope checks, PostgreSQL writes
            # and the authorized HTTP read still execute normally.
            with monkeypatch.context() as clock:
                clock.setattr("app.worker.time.monotonic", lambda: 100.0)
                on_delta(text)
            seen.append(text)
            deadline = time.perf_counter() + .15
            while True:
                visible = client.get(BASE + "/tasks/" + task_id)
                assert visible.status_code == 200, visible.text
                if visible.json()["replyStream"]["text"] == "".join(seen) or time.perf_counter() >= deadline:
                    break
                time.sleep(.002)
            observations.append({"expected": "".join(seen), "visible": visible.json()["replyStream"]["text"],
                                 "state": visible.json()["state"]})
        return original(self, *args, on_delta=receive, **kwargs)
    monkeypatch.setattr(MockAdapter, "generate_stream", inspect)
    process(task_id)
    assert len(seen) > 1
    final = client.get(BASE + "/tasks/" + task_id).json()
    assert final["state"] == "succeeded" and final["result"]["replySaved"]
    assert final["replyStream"]["text"] == "".join(seen)
    for observation in observations:
        assert observation["state"] == "running"
        assert observation["visible"] == observation["expected"]


def test_stop_preserves_visible_text_and_excludes_partial_turn_from_history(client, reader, demo, profile, monkeypatch):
    session = prepare_chat(client, reader, demo, profile)
    task_id = send(client, session)
    def stopped(self, *args, on_delta, cancel_check, **kwargs):
        on_delta("已收到的文字。")
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.cancel_requested = True
            db.commit()
        cancel_check()
    monkeypatch.setattr(MockAdapter, "generate_stream", stopped)
    process(task_id)
    final = client.get(BASE+"/tasks/"+task_id).json()
    assert final["state"] == "cancelled" and final["replyStream"]["status"] == "stopped"
    assert final["replyStream"]["text"] == "已收到的文字。" and final["result"]["replySaved"]
    with SessionLocal() as db:
        assert db.get(RoleSession, session["id"]).pending_task_id is None
        rows = list(db.scalars(select(RoleMessage).where(RoleMessage.task_id == task_id)))
        assert len(rows) == 2 and all(m.status == "stopped" for m in rows)
        usage = list(db.scalars(select(UsageRecord).where(UsageRecord.task_id == task_id)))
        assert len(usage) == 1 and usage[0].usage_state == "unknown"
        consent = db.get(Consent, usage[0].consent_id)
        assert consent.calls_reserved == 1 and consent.output_reserved > 0
    assert send(client, session) == task_id
    next_id = send(client, session, "下一条消息", "next-turn")
    with SessionLocal() as db:
        from app.models import KnowledgeSnapshot
        task = db.get(Task, next_id)
        context = generation_context(db, task, db.get(KnowledgeSnapshot, task.checkpoint["snapshotId"]))
        assert context["history"] == []


def test_unknown_stream_recovery_reuses_task_and_never_calls_provider_twice(client, reader, demo, profile, monkeypatch):
    session = prepare_chat(client, reader, demo, profile)
    task_id = send(client, session)
    calls = []
    def interrupted(self, *args, on_delta, **kwargs):
        calls.append(1)
        on_delta("断线前已保存。")
        raise Problem("PROVIDER_STATUS_UNKNOWN", "PRIVATE-BODY", 502, {"reason": "transport_error"})
    monkeypatch.setattr(MockAdapter, "generate_stream", interrupted)
    process(task_id)
    first = client.get(BASE+"/tasks/"+task_id).json()
    assert first["state"] == "failed" and first["replyStream"]["status"] == "interrupted"
    assert first["replyStream"]["text"] == "断线前已保存。" and first["result"]["replySaved"]
    assert "PRIVATE" not in json.dumps(first)
    assert send(client, session) == task_id
    process(task_id)
    assert calls == [1]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == task_id)) == 1
        assert db.scalar(select(func.count()).select_from(RoleMessage).where(RoleMessage.task_id == task_id)) == 2


def test_soft_verdict_keeps_text_but_warned_turn_is_not_history(client, reader, demo, profile, monkeypatch):
    session = prepare_chat(client, reader, demo, profile)
    task_id = send(client, session)
    original = MockAdapter.generate
    def blocked(self, p, prompt, data, limit):
        if prompt.startswith("validation-"):
            return {"text": json.dumps({"verdict": "blocked", "reasonCode": "CHARACTER_KNOWLEDGE", "evidenceIds": [], "severity": "P0"}),
                "finishReason": "stop", "usage": {"state": "estimated", "inputTokens": 1, "outputTokens": 1}, "latencyMs": 1}
        return original(self, p, prompt, data, limit)
    monkeypatch.setattr(MockAdapter, "generate", blocked)
    process(task_id)
    final = client.get(BASE+"/tasks/"+task_id).json()
    assert final["state"] == "succeeded" and final["replyStream"]["status"] == "complete"
    assert final["result"]["replySaved"] and final["replyStream"]["text"]
    assert final["replyStream"]["reviewWarnings"] == ["CHARACTER_KNOWLEDGE"]
    with SessionLocal() as db:
        assert all(m.status == "review_warning" for m in db.scalars(select(RoleMessage).where(RoleMessage.task_id == task_id)))
    next_id = send(client, session, "下一条消息", "after-warning")
    with SessionLocal() as db:
        from app.models import KnowledgeSnapshot
        task = db.get(Task, next_id)
        assert generation_context(db, task, db.get(KnowledgeSnapshot, task.checkpoint["snapshotId"]))["history"] == []


def test_validator_transport_failure_retains_complete_prose_with_a_truthful_warning(client, reader, demo, profile, monkeypatch):
    session = prepare_chat(client, reader, demo, profile)
    task_id = send(client, session)
    original = MockAdapter.generate
    calls = []
    def failed_validation(self, p, prompt, data, limit):
        calls.append(prompt.split('-')[0])
        if prompt.startswith("validation-"):
            raise Problem("PROVIDER_STATUS_UNKNOWN", "PRIVATE-SUPPLIER-ERROR", 502, {"reason": "transport_error"})
        return original(self, p, prompt, data, limit)
    monkeypatch.setattr(MockAdapter, "generate", failed_validation)
    process(task_id)
    final = client.get(BASE+"/tasks/"+task_id).json()
    assert final["state"] == "succeeded" and final["replyStream"]["status"] == "complete"
    assert final["replyStream"]["text"] and final["result"]["replySaved"]
    assert final["replyStream"]["reviewWarnings"] == ["CHECK_UNAVAILABLE"]
    assert "PRIVATE" not in json.dumps(final)
    assert calls == ["role_chat", "validation"]
    process(task_id)
    assert calls == ["role_chat", "validation"]
    with SessionLocal() as db:
        rows = list(db.scalars(select(RoleMessage).where(RoleMessage.task_id == task_id)))
        assert len(rows) == 2 and all(row.status == "review_warning" for row in rows)
        usage = list(db.scalars(select(UsageRecord).where(UsageRecord.task_id == task_id)))
        assert len(usage) == 2 and any(row.usage_state == "unknown" for row in usage)


def test_stream_projection_owned_and_old_turn_remains_strict(client, reader, demo, profile):
    session = prepare_chat(client, reader, demo, profile)
    task, _, _ = submit(client, profile, {"operation": "role_chat", "profileId": profile["id"], "sessionId": session["id"],
        "message": "眼前有什么？", "turnId": "legacy"})
    assert "replyStream" not in task
    task_id = send(client, session)
    client.post(BASE+"/auth/register", json={"username": "other_stream", "password": "other-password-123"})
    login = client.post(BASE+"/auth/login", json={"username": "other_stream", "password": "other-password-123", "clientType": "web", "deviceName": "isolated"}).json()
    client.headers["Authorization"] = "Bearer "+login["accessToken"]
    assert client.get(BASE+"/tasks/"+task_id).status_code == 404
    assert client.get(f"{BASE}/role-sessions/{session['id']}/messages").status_code == 404
    assert client.post(BASE+"/tasks/"+task_id+"/cancel").status_code == 404


def test_full_book_stream_projection_is_hidden_after_preference_changes(client, reader, demo, profile):
    index(client, demo)
    anchor = read_chapter(client, demo)["blocks"][0]["endAnchor"]
    observe(client, reader, anchor)
    assert client.put(BASE+"/character-preferences", json={"mode": "full", "expectedVersion": 1}).status_code == 200
    person = next(c for c in card_list(client, demo, anchor).json()["items"] if c["name"] == "梅舟")
    session = chat(client, demo, anchor, person)
    task_id = send(client, session, "林渡的身份？")
    process(task_id)
    assert "replyStream" in client.get(BASE+"/tasks/"+task_id).json()
    assert client.put(BASE+"/character-preferences", json={"mode": "progress", "expectedVersion": 2}).status_code == 200
    assert client.get(BASE+"/tasks/"+task_id).status_code == 409
    response = client.get(BASE+"/tasks/"+task_id+"/events")
    assert response.status_code == 409 and response.json()["code"] == "ROLE_SCOPE_CHANGED"
    listed = client.get(BASE+"/tasks?taskType=role_chat").json()
    item = next(row for row in listed["items"] if row["id"] == task_id)
    assert "replyStream" not in item and "夜渡" not in json.dumps(item, ensure_ascii=False)



@pytest.mark.parametrize("stream_kind", ["prose", "world"])
def test_prose_and_world_second_body_fragment_is_durable_during_provider_pause(
        client, reader, demo, profile, monkeypatch, stream_kind):
    """Cadence regression through real pipelines, not a 150 ms latency sample.

    The original synthetic generator determines the valid final answer/usage.
    Only the timing of its first two body fragments is controlled. HTTP
    observations do not assert during the provider callback: the task reaches
    its natural terminal state before the durability assertion can fail.
    """
    from app.config import settings
    from app.world.state import public_stream
    from .test_prose_generations import start as start_prose, get as get_prose
    from .test_world import API as WORLD_API, start as start_world, action as world_action
    from app.world.worker import process as process_world

    assert settings.ai_mode == "mock"
    assert SessionLocal.kw["bind"].url.database.endswith("_test")
    if stream_kind == "prose":
        receipt, _ = start_prose(client, reader, demo, profile,
                                 key="prose-second-body-fragment-pause")
        task_id = receipt["taskId"]

        def observe():
            value = get_prose(client, receipt)
            return {"text": value["chapters"][0]["text"], "state": value["state"],
                    "phase": value["phase"], "providerState": value["providerState"],
                    "canAccept": value["chapters"][0]["canAccept"]}

        runner = process
        target_prompt = "prose-chapter-"
    else:
        world, line = start_world(client, reader, demo, profile)
        receipt = world_action(client, world, line, key="world-second-body-fragment-pause")
        task_id = receipt["taskId"]

        def observe():
            response = client.get(WORLD_API + "/tasks/" + task_id)
            value = response.json()
            return {"text": value.get("stream", {}).get("text", ""), "state": value.get("state"),
                    "httpStatus": response.status_code, "phase": value.get("phase"),
                    "providerState": value.get("providerState")}

        runner = process_world
        target_prompt = "world-narrator-"
    observations = []
    expected_prefixes = []
    actual_pause_ms = []

    def controlled(self, p, prompt, data, limit, *, on_delta, cancel_check, **kwargs):
        result = self.generate(p, prompt, data, limit)
        text = result["text"]
        if not prompt.startswith(target_prompt):
            for offset in range(0, len(text), 256):
                cancel_check()
                on_delta(text[offset:offset+256])
            return result
        with SessionLocal() as db:
            denied = list(db.get(Task, task_id).checkpoint.get("worldDeniedPhrases", []))
        for index, (start, end) in enumerate(((0, 20), (20, 40))):
            cancel_check()
            with monkeypatch.context() as clock:
                clock.setattr("app.worker.time.monotonic", lambda: 100.0 + index * .04)
                on_delta(text[start:end])
            expected_prefixes.append(public_stream(text[:end], denied)
                                     if stream_kind == "world" else project_prose(text[:end],1)["text"])
            deadline = time.perf_counter() + .15
            while True:
                observation = observe()
                if observation["text"] == expected_prefixes[-1] or time.perf_counter() >= deadline:
                    break
                time.sleep(.002)
            observations.append(observation)
        pause_start = time.monotonic()
        time.sleep(.18)
        actual_pause_ms.append((time.monotonic() - pause_start) * 1000)
        observations.append(observe())  # still no third fragment or provider return
        for offset in range(40, len(text), 256):
            cancel_check()
            on_delta(text[offset:offset+256])
        return result

    monkeypatch.setattr(MockAdapter, "generate_stream", controlled)
    runner(task_id)
    with SessionLocal() as db:
        natural = db.get(Task, task_id)
        terminal = (natural.state, natural.persistence_state)
        intents = db.scalar(select(func.count()).select_from(UsageRecord).where(
            UsageRecord.task_id == task_id, UsageRecord.status == "intent"))
    import os
    from pathlib import Path
    from app.worker import TERMINAL
    observation_root = os.environ.get("PROSE_TEST_OBSERVATIONS")
    if observation_root:
        output = Path(observation_root); output.mkdir(parents=True, exist_ok=True)
        (output / ("pause-" + stream_kind + ".json")).write_text(json.dumps({
            "streamKind": stream_kind, "taskId": task_id, "naturalTerminal": terminal,
            "usageIntents": intents, "actualPauseMs": actual_pause_ms,
            "expectedPrefixes": expected_prefixes, "observations": observations,
            "scope": "finite pipeline cadence; not HTTP latency acceptance"},
            ensure_ascii=False, indent=2), encoding="utf-8")
    assert terminal == ("succeeded", "saved")
    assert intents == 0
    assert len(expected_prefixes) == 2 and expected_prefixes[0] != expected_prefixes[1]
    assert len(actual_pause_ms) == 1 and actual_pause_ms[0] >= 150
    assert len(observations) == 3
    assert all(isinstance(row["state"], str) and row["state"] not in TERMINAL
               and row["providerState"] == "running" and row["phase"] == "writing"
               for row in observations)
    if stream_kind == "prose":
        assert all(row["canAccept"] is False for row in observations)
    assert [row["text"] for row in observations] == [
        expected_prefixes[0], expected_prefixes[1], expected_prefixes[1]]
