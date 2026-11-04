"""Model-assisted card authoring: draft a new card or greet variations.

Both helpers only return previews; nothing is written to storage.
"""
import json
import logging
from pathlib import Path

from ..completion_policy import completion_limit
from ..errors import Problem
from ..story_flow import default_profile

logger = logging.getLogger("reader")

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
DRAFT_PROMPT = PROMPT_DIR / "card_draft.txt"
GREETINGS_PROMPT = PROMPT_DIR / "card_greetings.txt"
TEXT_LIMIT = 20000


def _prompt(path):
    return path.read_text(encoding="utf-8").strip()


def _parse(text):
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").strip()
        if "\n" in body:
            body = body.split("\n", 1)[1]
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        raise ValueError("response is not JSON")
    payload = json.loads(body[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("response is not a JSON object")
    return payload


def _generate(profile, prompt, data):
    from ..worker import adapter_for
    adapter = adapter_for()
    result = adapter.generate(profile, prompt, data, completion_limit(profile, "generation"))
    return result.get("text") or ""


def _attempt(profile, prompt, data):
    last = None
    for attempt in range(2):
        try:
            return _parse(_generate(profile, prompt, data))
        except Exception as error:  # noqa: BLE001 - model and parse failures share one error
            last = error
            logger.warning("card assist attempt %s failed error=%s", attempt + 1, error)
    raise Problem("CARD_ASSIST_FAILED", "AI 创作助手暂不可用，请稍后重试", 502, retryable=True)


def _text(value, limit=TEXT_LIMIT):
    return ("" if value is None else str(value)).strip()[:limit]


def _tags(value):
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:40] for item in value if str(item).strip()][:20]


def draft(db, owner, brief):
    text = (brief or "").strip()
    if not text:
        raise Problem("CARD_INVALID", "请先描述想要的角色", 422)
    profile = default_profile(db, owner)
    payload = _attempt(profile, _prompt(DRAFT_PROMPT), {"brief": text[:2000]})
    name = _text(payload.get("name"), 160)
    if not name:
        raise Problem("CARD_ASSIST_FAILED", "AI 创作助手返回内容不完整，请重试", 502, retryable=True)
    return {
        "name": name,
        "description": _text(payload.get("description")),
        "personality": _text(payload.get("personality")),
        "scenario": _text(payload.get("scenario")),
        "firstMessage": _text(payload.get("first_message") or payload.get("firstMessage")),
        "tags": _tags(payload.get("tags")),
    }


def _card_payload(card):
    return {"name": card.name, "description": card.description[:1200],
            "personality": card.personality[:1200], "scenario": card.scenario[:1200],
            "firstMessage": card.first_message[:1200], "tags": list(card.tags or [])[:20]}


def greetings(db, owner, card, count=3):
    count = max(1, min(5, int(count or 3)))
    profile = default_profile(db, owner)
    payload = _attempt(profile, _prompt(GREETINGS_PROMPT), {"count": count, "card": _card_payload(card)})
    values = payload.get("greetings")
    if not isinstance(values, list):
        values = []
    lines = []
    for value in values:
        text = _text(value)
        if text:
            lines.append(text)
        if len(lines) >= count:
            break
    if not lines:
        raise Problem("CARD_ASSIST_FAILED", "AI 创作助手返回内容不完整，请重试", 502, retryable=True)
    return lines
