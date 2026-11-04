"""Character card / chat export helpers (v2 PNG with embedded JSON, and JSON/JSONL)."""
import base64
import io
import json
from urllib.parse import quote

from ..errors import Problem
from ..storage import private_path


def filename_header(name, extension):
    """Content-Disposition with an ASCII fallback plus RFC 5987 UTF-8 filename."""
    stem = (name or "tavern").strip() or "tavern"
    ascii_stem = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_ ") else "_" for ch in stem).strip(" _") or "tavern"
    ascii_stem = ascii_stem[:60]
    return {"Content-Disposition": f"attachment; filename={ascii_stem}{extension}; "
                                   f"filename*=UTF-8''{quote(stem + extension)}"}


def card_document(card, lore):
    entries = []
    for row in lore:
        entries.append({
            "id": row.id,
            "keys": list(row.keys or []),
            "content": row.content,
            "enabled": bool(row.enabled),
            "insertion_order": row.order,
            "name": row.name,
            "comment": row.name,
        })
    data = {
        "name": card.name,
        "description": card.description or "",
        "personality": card.personality or "",
        "scenario": card.scenario or "",
        "first_mes": card.first_message or "",
        "mes_example": "\n<START>\n".join(card.example_messages or []),
        "alternate_greetings": list(card.alternate_greetings or []),
        "tags": list(card.tags or []),
        "creator": card.creator or "",
        "character_version": card.character_version or "",
        "system_prompt": card.system_prompt or "",
        "post_history_instructions": card.post_history_instructions or "",
        "extensions": {},
        "character_book": {"entries": entries} if entries else None,
    }
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}


def _pnginfo(document):
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    raw = json.dumps(document, ensure_ascii=False).encode("utf-8")
    info.add_text("chara", base64.b64encode(raw).decode("ascii"))
    return info


def _avatar_bytes(card, owner):
    if not card.avatar_path:
        return None
    try:
        path = private_path(owner, card.avatar_path)
        if path.is_file():
            return path.read_bytes()
    except (Problem, ValueError, OSError):
        return None
    return None


def card_png(card, lore, owner):
    """Return PNG bytes carrying the v2 card document in the chara tEXt chunk."""
    from PIL import Image, ImageDraw
    info = _pnginfo(card_document(card, lore))
    avatar = _avatar_bytes(card, owner)
    image = None
    if avatar:
        try:
            image = Image.open(io.BytesIO(avatar))
            image.load()
            image = image.convert("RGBA")
        except Exception:
            image = None
    if image is None:
        image = Image.new("RGB", (400, 600), (240, 236, 228))
        draw = ImageDraw.Draw(image)
        draw.text((24, 24), card.name[:80], fill=(60, 52, 44))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


def chat_jsonl(rows):
    lines = []
    for row in rows:
        lines.append(json.dumps({
            "role": row.role,
            "text": row.text,
            "sequence": row.sequence,
            "modelName": row.model_name or "",
            "createdAt": row.created_at.isoformat(),
        }, ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")
