"""Prompt assembly for native tavern chats, in SillyTavern order.

Order (P2): system prompt -> main prompt -> world info (before_char) ->
character description/personality/scenario -> user persona -> examples ->
world info (after_char, before_example) -> history (with at_depth entries
injected near the tail) -> post-history instructions -> jailbreak -> final
reply instruction.

A chat may bind a preset; non-empty preset fields override the card/chat
settings (main/system/post_history/jailbreak, sampling is resolved by the API
layer). The default preset is applied when a chat has no binding.

World-info semantics
--------------------
``position`` chooses the slot (before_char / after_char / before_example /
at_depth), ``order`` sorts entries inside a slot, ``constant`` entries always
match, otherwise enabled entries match when one of ``keys`` is a case-insensitive
substring of the scan text (new message + recent user lines). ``probability``
< 100 makes the match deterministic-per-turn via a hash of the entry id and the
scan text, so a client can preview exactly what the next generation injects.
``depth`` (at_depth only) inserts the entry before the Nth message from the
tail.

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


def _entry_order(row):
    return (int(getattr(row, "order", 100) or 0), str(getattr(row, "id", "")))


def match_world_info_with_reason(entries, text):
    """Return (entry, reason) pairs for this turn, sorted by order.

    ``reason`` is ``constant`` for always-on entries or ``keyword:<词>`` for
    keyword entries (the exact keyword that hit), so previews/UI can explain
    the injection and verifiers can assert on it.
    """
    matched, haystack = [], (text or "").lower()
    for row in sorted(entries, key=_entry_order):
        if not getattr(row, "enabled", True):
            continue
        if getattr(row, "constant", False):
            matched.append((row, "constant"))
            continue
        keys = [(str(key).strip(), str(key).strip().lower())
                for key in (getattr(row, "keys", None) or []) if str(key).strip()]
        if not keys:
            continue
        hit = next((original for original, lowered in keys if lowered in haystack), None)
        if hit is None:
            continue
        raw_probability = getattr(row, "probability", 100)
        probability = 100 if raw_probability is None else int(raw_probability)
        if probability < 100:
            roll = int(hashlib.sha256((str(row.id) + "|" + haystack).encode("utf-8")).hexdigest()[:8], 16) % 100
            if roll >= probability:
                continue
        matched.append((row, f"keyword:{hit}"))
    return matched


def match_world_info(entries, text):
    """Enabled entries that should be injected for this turn, sorted by order."""
    return [row for row, _reason in match_world_info_with_reason(entries, text)]


def _section(title, body):
    body = (body or "").strip()
    return f"{title}\n{body}" if body else ""


def _preset_field(preset, name):
    if preset is None:
        return ""
    value = preset.get(name) if isinstance(preset, dict) else getattr(preset, name, "")
    return value.strip() if isinstance(value, str) else (value or "")


def effective_prompts(card, settings=None, preset=None):
    """Resolve main/system/post-history/jailbreak with preset-over-card precedence."""
    settings = settings or {}
    main = _preset_field(preset, "main") or (settings.get("mainPrompt") or "").strip() or DEFAULT_MAIN_PROMPT
    return {
        "main": main,
        "system": (_preset_field(preset, "system") or (settings.get("system") or "").strip()
                   or (card.system_prompt or "").strip()),
        "postHistory": (_preset_field(preset, "post_history")
                        or (settings.get("postHistory") or "").strip()
                        or (card.post_history_instructions or "").strip()),
        "jailbreak": _preset_field(preset, "jailbreak") or (settings.get("jailbreakPrompt") or "").strip(),
    }


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


def world_section(entries, title="[世界书]"):
    if not entries:
        return ""
    parts = []
    for row in entries:
        keys = "、".join(str(key) for key in (getattr(row, "keys", None) or []))
        head = f"- {row.content}" if not keys else f"- （关键词：{keys}）{row.content}"
        parts.append(head)
    return title + "\n" + "\n".join(parts)


def history_lines(card, history, char_name, user_name, budget_chars):
    """Newest-first trimming then chronological formatting."""
    picked, used = [], 0
    for item in reversed(history):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if item.get("role") == "user":
            speaker = user_name
        else:
            speaker = str(item.get("speaker") or char_name)
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


def _matched_view(pairs):
    return [{"id": row.id, "keys": list(getattr(row, "keys", None) or []),
             "position": getattr(row, "position", "before_char"),
             "depth": int(getattr(row, "depth", 4) or 0),
             "order": int(getattr(row, "order", 100) or 0),
             "reason": reason, "content": row.content} for row, reason in pairs]


def build(card, chat, *, history, message, user_name="User", persona=None,
          settings=None, profile=None, output_limit=512, world_info=None,
          preset=None):
    settings = settings or {}
    world_info = world_info or []
    char_name = card.name
    resolved = effective_prompts(card, settings, preset)

    matched_pairs = match_world_info_with_reason(world_info, scan_text(message, history))
    matched = [row for row, _reason in matched_pairs]
    before_char = [row for row in matched if getattr(row, "position", "") == "before_char"]
    before_example = [row for row in matched if getattr(row, "position", "") == "before_example"]
    after_char = [row for row in matched if getattr(row, "position", "") == "after_char"]
    at_depth = [row for row in matched if getattr(row, "position", "") == "at_depth"]

    blocks = []
    world_parts = []

    def add_world(title, rows):
        text = world_section(rows, "")
        body = text.lstrip("\n")
        blocks.append((title, body))
        world_parts.append(title + "\n" + body)

    if resolved["system"]:
        blocks.append(("[系统提示词]", replace_macros(resolved["system"], char_name, user_name)))
    blocks.append(("[主提示词]", replace_macros(resolved["main"], char_name, user_name)))
    if before_char:
        add_world(POSITION_LABELS["before_char"], before_char)
    blocks.append(("[角色设定]", character_section(card, char_name).split("\n", 1)[1]))
    persona_block = persona_section(persona, user_name)
    if persona_block:
        blocks.append(("[你的角色]", persona_block.split("\n", 1)[1]))
    examples = example_section(card, char_name, user_name)
    if examples:
        blocks.append(("[示例对话]", examples.split("\n", 1)[1]))
    if before_example:
        add_world(POSITION_LABELS["before_example"], before_example)
    if after_char:
        add_world(POSITION_LABELS["after_char"], after_char)

    context_window = int(getattr(profile, "context_window", 32768) or 32768)
    budget_chars = max(1200, min(60000, (context_window - output_limit - 1024) * 3))
    lines = history_lines(card, history, char_name, user_name, budget_chars)
    depth_blocks = []
    if at_depth:
        groups = {}
        for row in at_depth:
            groups.setdefault(max(0, int(getattr(row, "depth", 4) or 0)), []).append(row)
        for depth in sorted(groups):
            depth_blocks.append((depth, world_section(groups[depth], POSITION_LABELS["at_depth"])))
            world_parts.append(POSITION_LABELS["at_depth"] + "\n" + groups[depth][0].content)
        for depth, text in depth_blocks:
            index = max(0, len(lines) - depth)
            lines.insert(index, text)
    blocks.append(("[对话历史]", "\n".join(lines) if lines else "（暂无历史）"))
    if resolved["postHistory"]:
        blocks.append(("[后置指令]", replace_macros(resolved["postHistory"], char_name, user_name)))
    if resolved["jailbreak"]:
        blocks.append(("[越狱提示]", replace_macros(resolved["jailbreak"], char_name, user_name)))
    blocks.append(("[本轮要求]", DEFAULT_REPLY_INSTRUCTION.format(char=char_name, user=user_name)))

    rendered = [{"title": title, "chars": len(body)} for title, body in blocks]
    system_prompt = "tavern-v1\n\n" + "\n\n".join(_section(title, body) for title, body in blocks)
    data = {
        "message": message,
        "character": {"id": card.id, "name": char_name},
        "chatId": chat.id,
        "mode": "reply",
    }
    return {"systemPrompt": system_prompt, "data": data, "sections": rendered,
            "matched": _matched_view(matched_pairs), "worldInjection": "\n\n".join(world_parts),
            "prompts": resolved}


GROUP_MEMBER_HEADER = "[群聊角色]"
GROUP_SPEAKER_LIMITS = {"description": 500, "personality": 300, "scenario": 500}


def group_member_section(cards):
    lines = []
    for card in cards:
        lines.append(f"- {card.name}")
        for attribute, limit in GROUP_SPEAKER_LIMITS.items():
            value = (getattr(card, attribute, "") or "").strip()
            if not value:
                continue
            label = {"description": "描述", "personality": "性格", "scenario": "场景"}[attribute]
            lines.append(f"  {label}：{value[:limit]}")
    return GROUP_MEMBER_HEADER + "\n" + "\n".join(lines)


def build_group(cards, chat, *, speaker, history, message, user_name="User", persona=None,
                settings=None, profile=None, output_limit=512, world_info=None,
                preset=None):
    """Assemble a group-chat prompt where only ``speaker`` may talk this turn."""
    settings = settings or {}
    world_info = world_info or []
    speaker_name = speaker.name
    resolved = effective_prompts(speaker, settings, preset)

    matched_pairs = match_world_info_with_reason(world_info, scan_text(message, history))
    matched = [row for row, _reason in matched_pairs]
    before_char = [row for row in matched if getattr(row, "position", "") == "before_char"]
    before_example = [row for row in matched if getattr(row, "position", "") == "before_example"]
    after_char = [row for row in matched if getattr(row, "position", "") == "after_char"]
    at_depth = [row for row in matched if getattr(row, "position", "") == "at_depth"]

    blocks = []
    world_parts = []

    def add_world(title, rows):
        body = world_section(rows, "").lstrip("\n")
        blocks.append((title, body))
        world_parts.append(title + "\n" + body)

    if resolved["system"]:
        blocks.append(("[系统提示词]", replace_macros(resolved["system"], speaker_name, user_name)))
    blocks.append(("[主提示词]", replace_macros(resolved["main"], speaker_name, user_name)))
    if before_char:
        add_world(POSITION_LABELS["before_char"], before_char)
    blocks.append((GROUP_MEMBER_HEADER, group_member_section(cards).split("\n", 1)[1]))
    persona_block = persona_section(persona, user_name)
    if persona_block:
        blocks.append(("[你的角色]", persona_block.split("\n", 1)[1]))
    examples = example_section(speaker, speaker_name, user_name)
    if examples:
        blocks.append(("[示例对话]", examples.split("\n", 1)[1]))
    if before_example:
        add_world(POSITION_LABELS["before_example"], before_example)
    if after_char:
        add_world(POSITION_LABELS["after_char"], after_char)

    context_window = int(getattr(profile, "context_window", 32768) or 32768)
    budget_chars = max(1200, min(60000, (context_window - output_limit - 1024) * 3))
    lines = history_lines(speaker, history, speaker_name, user_name, budget_chars)
    if at_depth:
        groups = {}
        for row in at_depth:
            groups.setdefault(max(0, int(getattr(row, "depth", 4) or 0)), []).append(row)
        for depth in sorted(groups):
            text = world_section(groups[depth], POSITION_LABELS["at_depth"])
            world_parts.append(POSITION_LABELS["at_depth"] + "\n" + groups[depth][0].content)
            lines.insert(max(0, len(lines) - depth), text)
    blocks.append(("[对话历史]", "\n".join(lines) if lines else "（暂无历史）"))
    if resolved["postHistory"]:
        blocks.append(("[后置指令]", replace_macros(resolved["postHistory"], speaker_name, user_name)))
    if resolved["jailbreak"]:
        blocks.append(("[越狱提示]", replace_macros(resolved["jailbreak"], speaker_name, user_name)))
    blocks.append(("[本轮要求]",
                   f"本轮只扮演 {speaker_name}，其他人作为场景中的角色存在；"
                   f"不要替其他角色或读者说话，也不要写名字前缀。"
                   + DEFAULT_REPLY_INSTRUCTION.format(char=speaker_name, user=user_name)))

    rendered = [{"title": title, "chars": len(body)} for title, body in blocks]
    system_prompt = "tavern-v1\n\n" + "\n\n".join(_section(title, body) for title, body in blocks)
    data = {
        "message": message,
        "character": {"id": speaker.id, "name": speaker_name},
        "members": [{"id": card.id, "name": card.name} for card in cards],
        "chatId": chat.id,
        "mode": "group",
        "speaker": {"id": speaker.id, "name": speaker_name},
    }
    return {"systemPrompt": system_prompt, "data": data, "sections": rendered,
            "matched": _matched_view(matched_pairs), "worldInjection": "\n\n".join(world_parts),
            "prompts": resolved}
