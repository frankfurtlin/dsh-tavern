"""Character card parsing: PNG embedded card data and standalone JSON cards.

Supports the common Tavern/SillyTavern formats: V2/V3 ``{"spec": ..., "data": {...}}``
and the flat V1 form. PNG cards are read from ``tEXt``/``zTXt``/``iTXt`` chunks whose
keyword is ``chara`` (or the newer ``ccv3``)."""
import base64
import json
import struct
import zlib

from ..errors import Problem

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
CARD_KEYWORDS = {b"chara", b"ccv3"}
MAX_FILE = 8 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
LIMITS = {
    "name": 160,
    "description": 20000,
    "personality": 20000,
    "scenario": 20000,
    "first_message": 20000,
    "creator": 20000,
    "character_version": 20000,
    "system_prompt": 20000,
    "post_history_instructions": 20000,
}
LIST_LIMIT = 200
LORE_LIMIT = 200
LORE_KEYS_LIMIT = 20


def _bad(message="角色卡无法识别"):
    raise Problem("CARD_INVALID", message, 422)


def _png_chunks(data):
    if not data.startswith(PNG_MAGIC):
        _bad("PNG 文件头无效")
    position = len(PNG_MAGIC)
    size = len(data)
    while position + 12 <= size:
        length = struct.unpack(">I", data[position:position + 4])[0]
        kind = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + length]
        position += 12 + length
        if kind == b"IEND":
            break
        if kind == b"tEXt":
            keyword, separator, value = body.partition(b"\x00")
            if separator and keyword.lower() in CARD_KEYWORDS:
                yield value
        elif kind == b"zTXt":
            keyword, separator, rest = body.partition(b"\x00")
            if separator and keyword.lower() in CARD_KEYWORDS and len(rest) > 1 and rest[0] == 0:
                try:
                    yield zlib.decompress(rest[1:])
                except zlib.error:
                    _bad("PNG 里的角色卡数据已损坏")
        elif kind == b"iTXt":
            keyword, separator, rest = body.partition(b"\x00")
            if not separator or keyword.lower() not in CARD_KEYWORDS:
                continue
            if len(rest) < 2:
                _bad("PNG 里的角色卡数据已损坏")
            compressed, method = rest[0], rest[1]
            remainder = rest[2:]
            language, separator, remainder = remainder.partition(b"\x00")
            translated, separator, value = remainder.partition(b"\x00")
            if not separator:
                _bad("PNG 里的角色卡数据已损坏")
            if compressed:
                if method != 0:
                    _bad("PNG 里的角色卡数据压缩方式不支持")
                try:
                    value = zlib.decompress(value)
                except zlib.error:
                    _bad("PNG 里的角色卡数据已损坏")
            yield value


def _decode_chunk(value):
    value = value.strip()
    if not value:
        return None
    if value.startswith(b"{"):
        try:
            return json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            _bad("PNG 里的角色卡数据不是有效 JSON")
    try:
        decoded = base64.b64decode(value, validate=False)
        return json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _bad("PNG 里的角色卡数据不是有效 JSON")


def _text(value, limit=20000):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()[:limit]


def _string_list(value):
    if not isinstance(value, list):
        return []
    items = []
    for entry in value:
        text = _text(entry, 500)
        if text:
            items.append(text)
        if len(items) >= LIST_LIMIT:
            break
    return items


def _lore_entries(payload, data):
    """Normalize SillyTavern v2/v3 ``character_book.entries`` into flat rows."""
    book = data.get("character_book") if isinstance(data, dict) else None
    if not isinstance(book, dict):
        book = payload.get("character_book") if isinstance(payload, dict) else None
    entries = book.get("entries") if isinstance(book, dict) else None
    if not isinstance(entries, list):
        return []
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        keys = []
        for key in entry.get("keys") if isinstance(entry.get("keys"), list) else []:
            text = _text(key, 200)
            if text and text not in keys:
                keys.append(text)
            if len(keys) >= LORE_KEYS_LIMIT:
                break
        content = _text(entry.get("content"), 8000)
        if not keys or not content:
            continue
        name = _text(entry.get("name") or entry.get("comment"), 160) or keys[0][:160]
        order = entry.get("insertion_order", entry.get("order", 100))
        try:
            order = int(order)
        except (TypeError, ValueError):
            order = 100
        rows.append({"name": name, "keys": keys, "content": content,
                     "enabled": bool(entry.get("enabled", True)), "order": order})
        if len(rows) >= LORE_LIMIT:
            break
    return rows


def _examples(value):
    text = _text(value, 20000)
    if not text:
        return []
    parts = [part.strip() for part in text.split("<START>")]
    return [part for part in parts if part][:LIST_LIMIT]


def _card_body(payload, filename):
    if not isinstance(payload, dict):
        _bad("角色卡 JSON 结构无效")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    name = _text(data.get("name") or payload.get("name"), LIMITS["name"])
    if not name:
        _bad("角色卡缺少人物名称")
    personality = _text(data.get("personality"), LIMITS["personality"])
    description = _text(data.get("description"), LIMITS["description"]) or personality
    first_message = _text(data.get("first_mes") or data.get("first_message"), LIMITS["first_message"])
    tags = _string_list(data.get("tags"))
    return {
        "name": name,
        "description": description,
        "personality": personality,
        "scenario": _text(data.get("scenario"), LIMITS["scenario"]),
        "first_message": first_message,
        "example_messages": _examples(data.get("mes_example")),
        "alternate_greetings": _string_list(data.get("alternate_greetings")),
        "tags": tags,
        "creator": _text(data.get("creator"), LIMITS["creator"]),
        "character_version": _text(data.get("character_version"), LIMITS["character_version"]),
        "system_prompt": _text(data.get("system_prompt"), LIMITS["system_prompt"]),
        "post_history_instructions": _text(data.get("post_history_instructions"), LIMITS["post_history_instructions"]),
        "lore": _lore_entries(payload, data),
        "raw": payload,
        "source": "png" if filename.lower().endswith(".png") else "json",
    }


def parse_card(data: bytes, filename: str = "") -> dict:
    """Return normalized card fields plus the original JSON and source type."""
    if not data or len(data) > MAX_FILE:
        _bad("角色卡文件为空或超过 8 MiB")
    filename = filename or ""
    if data.startswith(PNG_MAGIC):
        if len(data) > MAX_FILE:
            _bad("角色卡文件超过 8 MiB")
        payload = None
        for value in _png_chunks(data):
            payload = _decode_chunk(value)
            if payload is not None:
                break
        if payload is None:
            _bad("PNG 里没有角色卡数据")
        fields = _card_body(payload, filename)
        fields["source"] = "png"
        return fields
    if len(data) > MAX_JSON:
        _bad("角色卡 JSON 超过 2 MiB")
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        _bad("角色卡不是有效的 JSON 或 PNG")
    fields = _card_body(payload, filename)
    fields["source"] = "json"
    return fields
