"""DSH model gateway adapter + worker selection + CLI setup.

The gateway is replaced by a local ThreadingHTTPServer so these tests never
touch the real DSH process on 3080.
"""
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import select

from app.config import settings
from app.context_wire import wire_json
from app.errors import Problem
from app.providers import ChatCompletionsAdapter, DshAgentAdapter, MockAdapter, decrypt_key

COMPLETE_OK = {"text": "你好", "finishReason": "stop", "usage": {"state": "known", "inputTokens": 2, "outputTokens": 3}}
STREAM_FRAMES = [
    'data: {"type":"delta","text":"你"}\n\n',
    'data: {"type":"delta","text":"好"}\n\n',
    'data: {"type":"usage","usage":{"state":"estimated","inputTokens":1,"outputTokens":2}}\n\n',
    'data: {"type":"finish","reason":"stop"}\n\n',
    "data: [DONE]\n\n",
]


class _GatewayHandler(BaseHTTPRequestHandler):
    # HTTP/1.0 closes the connection after each response, so an SSE stream that
    # ends without [DONE] is observed as a clean EOF instead of a read timeout.
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        state = self.server.gateway_state
        state["requests"].append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.path.endswith("/complete"):
            status, payload, delay = state["complete"](body)
            time.sleep(delay)
            data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass
            return
        status, frames, delay = state["stream"](body)
        time.sleep(delay)
        self.send_response(status)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for frame in frames:
            try:
                self.wfile.write(frame.encode())
                self.wfile.flush()
            except OSError:
                return
            time.sleep(state.get("frame_delay", 0))


@pytest.fixture
def gateway(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    server.daemon_threads = True
    state = {
        "requests": [],
        "frame_delay": 0.05,
        "complete": lambda body: (200, COMPLETE_OK, 0),
        "stream": lambda body: (200, list(STREAM_FRAMES), 0),
    }
    server.gateway_state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "dsh_agent_url", f"http://127.0.0.1:{server.server_port}/tavern-agent/v1")
    monkeypatch.setattr(settings, "dsh_agent_secret", "test-secret")
    monkeypatch.setattr(settings, "dsh_agent_timeout_seconds", 5)
    monkeypatch.setattr(settings, "dsh_agent_model", "")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def test_complete_posts_contract_and_normalizes(gateway):
    result = DshAgentAdapter().generate(None, "system prompt", {"message": "hi"}, 64)

    request = gateway["requests"][-1]
    assert request["path"].endswith("/complete")
    assert request["headers"]["Authorization"] == "Bearer test-secret"
    assert request["body"]["system"] == "system prompt"
    assert request["body"]["messages"] == [{"role": "user", "content": wire_json({"message": "hi"})}]
    assert request["body"]["maxTokens"] == 64
    assert "temperature" not in request["body"]
    assert result["text"] == "你好"
    assert result["finishReason"] == "stop"
    assert result["usage"] == {"state": "known", "inputTokens": 2, "outputTokens": 3}
    assert isinstance(result["latencyMs"], int)


def test_complete_structured_request_sends_temperature(gateway):
    DshAgentAdapter().generate(None, "system", {"_structured": True, "message": "hi"}, 32)
    assert gateway["requests"][-1]["body"]["temperature"] == 0.2


def test_complete_sends_configured_model_override(gateway, monkeypatch):
    monkeypatch.setattr(settings, "dsh_agent_model", "opencode-go/deepseek-v4.1-flash")
    DshAgentAdapter().generate(None, "system", {"message": "hi"}, 16)
    assert gateway["requests"][-1]["body"]["model"] == {"provider": "opencode-go", "model": "deepseek-v4.1-flash"}


def test_complete_ignores_malformed_model_override(gateway, monkeypatch):
    for bad in ("", "   ", "opencode-go", "/model", "provider/"):
        monkeypatch.setattr(settings, "dsh_agent_model", bad)
        DshAgentAdapter().generate(None, "system", {"message": "hi"}, 16)
        assert "model" not in gateway["requests"][-1]["body"]


def test_complete_empty_text_is_success(gateway):
    gateway["complete"] = lambda body: (200, {"text": "", "finishReason": "stop"}, 0)
    assert DshAgentAdapter().generate(None, "system", {}, 16)["text"] == ""


def test_complete_unauthorized(gateway):
    gateway["complete"] = lambda body: (401, {"error": {"code": "AGENT_UNAUTHORIZED"}}, 0)
    with pytest.raises(Problem) as error:
        DshAgentAdapter().generate(None, "system", {}, 16)
    assert error.value.code == "AGENT_UNAUTHORIZED"
    assert error.value.status == 502
    assert "test-secret" not in json.dumps(error.value.details)


def test_complete_upstream_failure(gateway):
    gateway["complete"] = lambda body: (500, {"error": {"code": "AGENT_UPSTREAM_FAILED"}}, 0)
    with pytest.raises(Problem) as error:
        DshAgentAdapter().generate(None, "system", {}, 16)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert error.value.status == 502
    assert error.value.details["httpStatus"] == 500


def test_complete_invalid_payload(gateway):
    gateway["complete"] = lambda body: (200, "{not json", 0)
    with pytest.raises(Problem) as error:
        DshAgentAdapter().generate(None, "system", {}, 16)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert error.value.details["reason"] == "invalid_response"


def test_complete_timeout(monkeypatch, gateway):
    monkeypatch.setattr(settings, "dsh_agent_timeout_seconds", 0.15)
    gateway["complete"] = lambda body: (200, COMPLETE_OK, 0.8)
    with pytest.raises(Problem) as error:
        DshAgentAdapter().generate(None, "system", {}, 16)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert error.value.details["reason"] == "timeout"


def test_complete_connection_refused(monkeypatch):
    monkeypatch.setattr(settings, "dsh_agent_url", "http://127.0.0.1:1/tavern-agent/v1")
    monkeypatch.setattr(settings, "dsh_agent_secret", "test-secret")
    monkeypatch.setattr(settings, "dsh_agent_timeout_seconds", 2)
    with pytest.raises(Problem) as error:
        DshAgentAdapter().generate(None, "system", {}, 16)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert error.value.details["reason"] == "transport_error"


def test_stream_collects_deltas_and_usage(gateway):
    deltas, cancels = [], []
    result = DshAgentAdapter().generate_stream(None, "system", {"message": "hi"}, 64,
        on_delta=deltas.append, cancel_check=lambda: cancels.append(1))

    assert deltas == ["你", "好"]
    assert result["text"] == "你好"
    assert result["finishReason"] == "stop"
    assert result["usage"] == {"state": "estimated", "inputTokens": 1, "outputTokens": 2}
    assert gateway["requests"][-1]["path"].endswith("/stream")
    assert cancels, "the watchdog must poll cancellation during streaming"


def test_stream_unauthorized(gateway):
    gateway["stream"] = lambda body: (401, [], 0)
    with pytest.raises(Problem) as error:
        DshAgentAdapter().generate_stream(None, "system", {}, 16, on_delta=lambda text: None, cancel_check=lambda: None)
    assert error.value.code == "AGENT_UNAUTHORIZED"


def test_stream_error_frame(gateway):
    gateway["stream"] = lambda body: (200, ['data: {"type":"error","error":{"code":"AGENT_UPSTREAM_FAILED","message":"boom"}}\n\n'], 0)
    with pytest.raises(Problem) as error:
        DshAgentAdapter().generate_stream(None, "system", {}, 16, on_delta=lambda text: None, cancel_check=lambda: None)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert error.value.details["gatewayCode"] == "AGENT_UPSTREAM_FAILED"


def test_stream_without_done_is_failure(gateway):
    gateway["stream"] = lambda body: (200, list(STREAM_FRAMES[:-1]), 0)
    with pytest.raises(Problem) as error:
        DshAgentAdapter().generate_stream(None, "system", {}, 16, on_delta=lambda text: None, cancel_check=lambda: None)
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert error.value.details["reason"] == "incomplete_stream"


def test_worker_selects_adapter_for_mode():
    from app.worker import adapter_for

    assert isinstance(adapter_for("mock"), MockAdapter)
    assert isinstance(adapter_for("dsh"), DshAgentAdapter)
    assert isinstance(adapter_for("real"), ChatCompletionsAdapter)
    assert not isinstance(adapter_for("real"), DshAgentAdapter)


def test_configure_dsh_model_cli_is_idempotent(reader, monkeypatch, capsys):
    from app import cli
    from app.db import SessionLocal
    from app.models import ModelProfile, User, UserSettings

    argv = ["cli", "configure-dsh-model", "--username", "reader_one"]
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    first = capsys.readouterr().out.strip()
    assert re.fullmatch(r"[0-9a-f]{32}", first)

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "reader_one"))
        rows = list(db.scalars(select(ModelProfile).where(ModelProfile.owner_id == user.id, ModelProfile.base_url == "dsh://agent")))
        assert len(rows) == 1
        profile = rows[0]
        assert profile.id == first
        assert profile.name == "DSH 模型"
        assert profile.model_id == "dsh-default"
        assert profile.context_window == 128000
        assert profile.output_parameter == "max_tokens"
        assert profile.supports_json is True
        assert profile.status == "active"
        assert profile.key_tail and profile.key_tail in decrypt_key(profile.key_cipher)
        assert db.get(UserSettings, user.id).default_profile_id == profile.id

    cli.main()
    second = capsys.readouterr().out.strip()
    assert second == first
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "reader_one"))
        rows = list(db.scalars(select(ModelProfile).where(ModelProfile.owner_id == user.id, ModelProfile.base_url == "dsh://agent")))
        assert len(rows) == 1
        assert db.get(UserSettings, user.id).default_profile_id == first
