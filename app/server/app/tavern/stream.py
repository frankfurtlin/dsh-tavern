"""Direct SSE streaming for native tavern chats.

Unlike the legacy world/play-controls flow this does not create a task: the
request thread inserts the user message and assembles the prompt, then a
worker thread runs the configured adapter (mock / DSH gateway / chat
completions) and pushes deltas through a queue. Only the final, completed text
is persisted - partial chunk data never reaches the database (see design §5).
"""
import json
import logging
import queue
import threading
from types import SimpleNamespace

from ..completion_policy import completion_limit
from ..db import SessionLocal
from ..errors import Problem
from ..models import now, uid
from ..worker import adapter_for
from . import service
from .models import TavernMessage

logger = logging.getLogger("reader")
PROFILE_FIELDS = ("id", "base_url", "model_id", "key_cipher", "output_parameter",
                  "supports_json", "context_window")


def sse(payload):
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def profile_view(profile):
    return {field: getattr(profile, field, None) for field in PROFILE_FIELDS}


def output_limit_for(profile, settings):
    limit = completion_limit(profile, "generation")
    requested = int((settings or {}).get("maxTokens") or 0)
    return max(16, min(limit, requested)) if requested else limit


def _persist(*, owner, chat_id, mode, text, model, target_id):
    with SessionLocal() as db:
        chat = db.get(service.TavernChat, chat_id)
        if chat is None or chat.owner_id != owner:
            return None
        swipe = {"content": text, "model": model or "", "createdAt": now().isoformat()}
        if mode == "regenerate":
            row = db.get(TavernMessage, target_id)
            if row is None or row.chat_id != chat.id or row.role != "assistant":
                return None
            swipes = list(row.swipes or [])
            swipes.append(swipe)
            row.swipes = swipes
            row.active_swipe = len(swipes) - 1
            row.updated_at = now()
        else:
            row = TavernMessage(id=uid(), owner_id=owner, chat_id=chat.id, role="assistant",
                                sequence=service.next_sequence(db, chat), swipes=[swipe],
                                active_swipe=0, updated_at=now())
            db.add(row)
        service.touch_chat(db, chat, model)
        db.commit()
        return service.message_out(row)


def reply_events(*, owner, chat_id, mode, system_prompt, data, output_limit,
                 profile, model_name, target_id=None, initial=None):
    """Return a generator of SSE frames for one assistant reply."""
    def stream():
        cancel = threading.Event()
        frames = queue.Queue()
        settings = {"systemPrompt": system_prompt}

        def on_delta(text):
            frames.put(("delta", text))

        def cancel_check():
            if cancel.is_set():
                raise Problem("GENERATION_CANCELLED", "生成已取消", 499)

        def work():
            try:
                adapter = adapter_for()
                plain_profile = SimpleNamespace(**profile)
                result = adapter.generate_stream(plain_profile, system_prompt, data, output_limit,
                                                 on_delta=on_delta, cancel_check=cancel_check)
                frames.put(("result", result))
            except BaseException as error:  # surfaced to the client as an error frame
                frames.put(("error", error))
            finally:
                frames.put(("end", None))

        if initial:
            yield sse(initial)
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        parts, result, error = [], None, None
        try:
            while True:
                kind, payload = frames.get()
                if kind == "delta":
                    parts.append(payload)
                    yield sse({"type": "delta", "text": payload})
                elif kind == "result":
                    result = payload
                elif kind == "error":
                    error = payload
                elif kind == "end":
                    break
        finally:
            cancel.set()
        if isinstance(error, Problem):
            yield sse({"type": "error", "code": error.code, "message": error.message})
            return
        if error is not None:
            logger.warning("tavern stream failed chat=%s error=%s", chat_id, type(error).__name__)
            yield sse({"type": "error", "code": "PROVIDER_UNAVAILABLE",
                       "message": "模型生成失败，请稍后重试"})
            return
        text = "".join(parts).strip()
        if not text and isinstance(result, dict):
            text = str(result.get("text") or "").strip()
        if not text:
            yield sse({"type": "error", "code": "REPLY_EMPTY", "message": "模型没有返回内容"})
            return
        try:
            row = _persist(owner=owner, chat_id=chat_id, mode=mode, text=text,
                           model=model_name, target_id=target_id)
        except Exception as failure:  # persistence is reported, stream stays well-formed
            logger.error("tavern persist failed chat=%s error=%s", chat_id, type(failure).__name__)
            yield sse({"type": "error", "code": "PERSISTENCE_UNAVAILABLE",
                       "message": "回复已生成但保存失败，请重试"})
            return
        if row is None:
            yield sse({"type": "error", "code": "RESOURCE_NOT_FOUND", "message": "对话或消息已被删除"})
            return
        yield sse({"type": "done", "message": row})

    return stream()


SSE_MEDIA_TYPE = "text/event-stream"
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
