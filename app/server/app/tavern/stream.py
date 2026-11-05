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

from ..cards import space as card_space
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
    """Encode one SSE frame.

    The client dispatches on the SSE ``event:`` field (message/delta/done/error),
    so mirror ``payload["type"]`` there; otherwise every frame would be parsed as
    a plain ``message`` event and incremental text would never render.
    """
    event = payload.get("type") if isinstance(payload, dict) else None
    prefix = "event: " + event + "\n" if event else ""
    return prefix + "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def profile_view(profile):
    return {field: getattr(profile, field, None) for field in PROFILE_FIELDS}


def output_limit_for(profile, settings, preset=None, chat_sampling=None):
    limit = completion_limit(profile, "generation")
    requested = int((chat_sampling or {}).get("maxTokens") or 0)
    if not requested:
        requested = int(getattr(preset, "max_tokens", None) or (settings or {}).get("maxTokens") or 0)
    return max(16, min(limit, requested)) if requested else limit


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sampling_for(settings, preset, chat_sampling=None):
    """Chat sampling > preset sampling > per-card settings; None = adapter default."""
    settings = settings or {}
    chat_sampling = chat_sampling or {}
    temperature = chat_sampling.get("temperature")
    if temperature is None:
        temperature = getattr(preset, "temperature", None) if preset is not None else None
    if temperature is None:
        temperature = _number(settings.get("temperature"))
    top_p = chat_sampling.get("topP")
    if top_p is None:
        top_p = getattr(preset, "top_p", None) if preset is not None else None
    if top_p is None:
        top_p = _number(settings.get("topP"))
    stop = [str(item) for item in (chat_sampling.get("stop") or [])]
    if not stop:
        stop = [str(item) for item in (getattr(preset, "stop", None) or [])] if preset is not None else []
    if not stop:
        stop = [str(item) for item in (settings.get("stop") or [])]
    return {
        "temperature": _number(temperature),
        "topP": _number(top_p),
        "stop": [item[:200] for item in stop][:4] or None,
    }


def _persist(*, owner, chat_id, mode, text, model, target_id, speaker_card_id=None):
    with SessionLocal() as db:
        chat = db.get(service.TavernChat, chat_id)
        if chat is None or chat.owner_id != owner:
            return None
        # Account-level output substitutions apply to the text that is saved;
        # streamed deltas stay untouched (same contract as the legacy story flow).
        text = card_space.apply_regex(db, owner, text)
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
                                speaker_card_id=speaker_card_id,
                                sequence=service.next_sequence(db, chat), swipes=[swipe],
                                active_swipe=0, updated_at=now())
            db.add(row)
        service.touch_chat(db, chat, model)
        db.commit()
        return service.message_out(row)


def reply_events(*, owner, chat_id, mode, system_prompt, data, output_limit,
                 profile, model_name, target_id=None, initial=None, sampling=None,
                 speaker_card_id=None, speaker_names=None):
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
                                                 on_delta=on_delta, cancel_check=cancel_check,
                                                 sampling=sampling)
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
                           model=model_name, target_id=target_id,
                           speaker_card_id=speaker_card_id)
        except Exception as failure:  # persistence is reported, stream stays well-formed
            logger.error("tavern persist failed chat=%s error=%s", chat_id, type(failure).__name__)
            yield sse({"type": "error", "code": "PERSISTENCE_UNAVAILABLE",
                       "message": "回复已生成但保存失败，请重试"})
            return
        if row is None:
            yield sse({"type": "error", "code": "RESOURCE_NOT_FOUND", "message": "对话或消息已被删除"})
            return
        message = dict(row) if isinstance(row, dict) else service.message_out(row, speaker_names)
        resolved_speaker_id = message.get("speakerCardId")
        if resolved_speaker_id and speaker_names:
            message["speakerName"] = speaker_names.get(resolved_speaker_id, "")
        yield sse({"type": "done", "message": message})

    return stream()


SSE_MEDIA_TYPE = "text/event-stream"
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
