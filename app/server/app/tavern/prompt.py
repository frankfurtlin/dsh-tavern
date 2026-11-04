"""Prompt assembly for native tavern chats, in SillyTavern order.

Order: system prompt -> main prompt -> world info (before_char) -> character
(name/description/personality/scenario) -> world info (before_example) ->
examples -> world info (after_char) -> user persona -> history (with at_depth
world info inside) -> post-history instructions -> final reply instruction.

Trimming strategy
-----------------
The model context is bounded by ``context_window`` (default 128k, stored per
profile). We approximate tokens with chars (~3 chars/token) and keep the newest
messages until the budget is exhausted; world-info entries are never partially
cut (an entry either fits before the budget is spent or is dropped). A single
oversized message is cut from its head so the newest tail - the part the reply
must continue - always survives. The system prompt itself is not counted
against the history budget: it is bounded by the card fields and world-info
caps at import/edit time.
"""
import hashlib

DEFAULT_MAIN_PROMPT = (
    "Write {{char}}'s next reply in a fictional roleplay chat between {{char}} and {{user}}. "
    "Stay fully in character, write only {{char}}'s reply, never speak or act for {{user}}, "
    "use plain prose (actions in *asterisks* are allowed), and keep the reply between 2 and 8 sentences."
)
POSITION_LABELS = {
    "before_char": "[世界书 · 角色设定之前]",
    "after_char": "[世界书 · 角色设定之后]",
    "before_example": "[世界书 · 示例对话之前]",
    "at_depth": "[世界书 · 注入深度]",
}
DEFAULT_REPLY_INSTRUCTION = "Only write {char}'s next reply now. Do not write {user}'s lines or any name prefix."


def replace_macros(text, char_name, user_name):
    if not text:
        return ""
    for marker in ("{{char}}", "<BOT>", "[char]"):
        text = text.replace(marker, char_name)
    for marker in ("{{user}}", "<USER>", "[user]"):
        text = text.replace(marker, user_name)
    return text


def scan_text(message, history):
    """Text used for keyword matching: the new message plus recent user lines."""
    user_lines = [item.get("text", "") for item in history if item.get("role") == "user"][-4:]
    return "\n".join([message, *user_lines])


def match_world_info(entries, text):
    """Enabled entries that should be injected for this turn (order preserved)."""
    matched, haystack = [], (text or "").lower()
    for row in entries:
        if not row.enabled:
            continue
        if row.constant:
            matched.append(row)
            continue
        keys = [str(key).strip().lower() for key in (row.keys or []) if str(key).strip()]
        if not keys:
            continue
        if not any(key in haystack for key in keys):
            continue
        if int(row.probability or 100) < 100:
            roll = int(hashlib.sha256((row.id + "|" + haystack).encode("utf-8")).hexdigest()[:8], 16) % 100
            if roll >= int(row.probability):
                continue
        matched.append(row)
    return matched


def _section(title, body):
    body = (body or "").strip()
    return f"{title}\n{body}" if body else ""


def character_section(card, char_name):
    lines = [f"姓名：{char_name}"]
    if card.description:
        lines.append(f"描述：{card.description}")
    if card.personality:
        lines.append(f"性格：{card.personality}")
    if card.scenario:
        lines.append(f"场景：{card.scenario}")
    return "[角色设定]\n" + "\n".join(lines)


def example_section(card, char_name, user_name):
    examples = [str(item).strip() for item in (card.example_messages or []) if str(item).strip()]
    if not examples:
        return ""
    body = "\n<START>\n".join(replace_macros(item, char_name, user_name) for item in examples)
    return "[示例对话]\n" + body


def persona_section(persona, user_name):
    if not persona:
        return ""
    lines = [f"姓名：{user_name}"]
    if persona.get("description"):
        lines.append(f"描述：{persona['description']}")
    return "[你的角色]\n" + "\n".join(lines)


def world_section(entries):
    if not entries:
        return ""
    parts = []
    for row in entries:
        keys = "、".join(str(key) for key in (row.keys or []))
        head = f"- {row.content}" if not keys else f"- （关键词：{keys}）{row.content}"
        parts.append(head)
    return "[世界书]\n" + "\n".join(parts)


def history_lines(card, history, char_name, user_name, budget_chars):
    """Newest-first trimming then chronological formatting."""
    picked, used = [], 0
    for item in reversed(history):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        speaker = user_name if item.get("role") == "user" else char_name
        line = f"{speaker}: {text}"
        if used + len(line) > budget_chars:
            remaining = budget_chars - used
            if remaining <= 0:
                break
            # Keep the newest tail of an oversized line (reply continuity).
            line = line[-remaining:]
        picked.append(line)
        used += len(line)
    picked.reverse()
    return picked


def build(card, chat, *, history, message, user_name="User", persona=None,
          settings=None, profile=None, output_limit=512, world_info=None):
    settings = settings or {}
    world_info = world_info or []
    char_name = card.name
    main_prompt = (settings.get("mainPrompt") or "").strip() or DEFAULT_MAIN_PROMPT
    blocks = []
    if card.system_prompt and card.system_prompt.strip():
        blocks.append("[系统提示词]\n" + replace_macros(card.system_prompt.strip(), char_name, user_name))
    blocks.append("[主提示词]\n" + replace_macros(main_prompt, char_name, user_name))

    matched = match_world_info(world_info, scan_text(message, history))
    before_char = [row for row in matched if row.position == "before_char"]
    before_example = [row for row in matched if row.position == "before_example"]
    after_char = [row for row in matched if row.position == "after_char"]
    at_depth = [row for row in matched if row.position == "at_depth"]

    if before_char:
        blocks.append(world_section(before_char))
    blocks.append(character_section(card, char_name))
    if before_example:
        blocks.append(world_section(before_example))
    examples = example_section(card, char_name, user_name)
    if examples:
        blocks.append(examples)
    if after_char:
        blocks.append(world_section(after_char))
    persona_block = persona_section(persona, user_name)
    if persona_block:
        blocks.append(persona_block)

    context_window = int(getattr(profile, "context_window", 32768) or 32768)
    budget_chars = max(1200, min(60000, (context_window - output_limit - 1024) * 3))
    lines = history_lines(card, history, char_name, user_name, budget_chars)
    if at_depth:
        # Entries with different depths are inserted from the requested depth.
        groups = {}
        for row in at_depth:
            groups.setdefault(max(0, int(row.depth or 4)), []).append(row)
        for depth in sorted(groups):
            text = world_section(groups[depth])
            index = max(0, len(lines) - depth)
            lines.insert(index, text)
    blocks.append("[对话历史]\n" + ("\n".join(lines) if lines else "（暂无历史）"))
    if card.post_history_instructions and card.post_history_instructions.strip():
        blocks.append("[后置指令]\n" + replace_macros(card.post_history_instructions.strip(), char_name, user_name))
    blocks.append("[本轮要求]\n" + DEFAULT_REPLY_INSTRUCTION.format(char=char_name, user=user_name))

    system_prompt = "tavern-v1\n\n" + "\n\n".join(block for block in blocks if block)
    data = {
        "message": message,
        "character": {"id": card.id, "name": char_name},
        "chatId": chat.id,
        "mode": "reply",
    }
    return {"systemPrompt": system_prompt, "data": data}
