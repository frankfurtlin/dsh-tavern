"""Deterministic reading projection; original provider content stays separate.

Parsing cannot call a model, invent prose, or mark a chapter complete. All
metadata can be checked against the preserved bytes during portable restore.
"""
from copy import deepcopy
import json
import re
from ..domain import digest

BODY_KEYS = {'text', 'body', 'content', '正文'}
TITLE_KEYS = {'title', '标题'}
TAG = re.compile(r'<\s*(/?)\s*(think|analysis)\s*>', re.I)
MAX_RAW = 1_000_000
MAX_JSON_DEPTH = 64


def bounded_json_structure(raw):
    """Reject excess nesting before entering the native JSON decoder."""
    depth, quoted, escaped = 0, False, False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '{[':
            depth += 1
            if depth > MAX_JSON_DEPTH:
                return False
        elif char in '}]':
            depth -= 1
    return True


def visible(raw):
    pieces, stack, cursor, omitted, pending = [], [], 0, False, False
    while cursor < len(raw):
        if raw[cursor] != '<':
            if not stack:
                pieces.append(raw[cursor])
            cursor += 1
            continue
        tag = TAG.match(raw, cursor)
        if tag:
            closing, name = bool(tag[1]), tag[2].lower()
            omitted = True
            if not closing:
                stack.append(name)
            elif stack and stack[-1] == name:
                stack.pop()
            elif stack:
                # A mismatched close cannot reveal an inner reasoning block.
                pending = True
            cursor = tag.end()
            continue
        rest = raw[cursor:]
        if re.fullmatch(r'<\s*/?\s*[a-z]*\s*', rest, re.I):
            name = re.sub(r'^<\s*/?\s*', '', rest).strip().lower()
            if any(candidate.startswith(name) for candidate in ('think', 'analysis')):
                pending = True
                break
        if not stack:
            pieces.append('<')
        cursor += 1
    return ''.join(pieces), omitted, pending or bool(stack)


def json_string(raw, start):
    """Project only complete code points; hold unfinished escaped characters."""
    pieces, index = [], start + 1
    escapes = {'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}
    while index < len(raw):
        char = raw[index]
        if char == '"':
            return ''.join(pieces), index + 1, True
        if char == '\\':
            if index + 1 >= len(raw):
                break
            escaped = raw[index + 1]
            if escaped == 'u':
                code = raw[index + 2:index + 6]
                if len(code) < 4:
                    break
                if not re.fullmatch(r'[0-9a-f]{4}', code, re.I):
                    raise ValueError('invalid escape')
                point = int(code, 16)
                if 0xD800 <= point <= 0xDBFF:
                    pair = raw[index + 6:index + 12]
                    if len(pair) < 6:
                        break
                    if not re.fullmatch(r'\\u[dD][c-fC-F][0-9a-f]{2}', pair):
                        raise ValueError('invalid pair')
                    pieces.append(chr(0x10000 + ((point - 0xD800) << 10) + int(pair[2:], 16) - 0xDC00))
                    index += 12
                    continue
                if 0xDC00 <= point <= 0xDFFF:
                    raise ValueError('invalid pair')
                pieces.append(chr(point))
                index += 6
                continue
            if escaped not in escapes:
                raise ValueError('invalid escape')
            pieces.append(escapes[escaped])
            index += 2
            continue
        if ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF:
            raise ValueError('invalid code point')
        pieces.append(char)
        index += 1
    return ''.join(pieces), index, False


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate key')
        value[key] = item
    return value


def partial_object(raw):
    index, text, title, seen = 1, '', None, set()
    decoder = json.JSONDecoder(object_pairs_hook=unique_object)
    while index < len(raw):
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index == len(raw):
            break
        if raw[index] != '"':
            if raw[index] == '}':
                return text, title
            raise ValueError('invalid key')
        key, index, closed = json_string(raw, index)
        if not closed:
            break
        if key in seen:
            raise ValueError('duplicate key')
        seen.add(key)
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index == len(raw):
            break
        if raw[index] != ':':
            raise ValueError('invalid separator')
        index += 1
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index == len(raw):
            break
        if key in BODY_KEYS | TITLE_KEYS:
            if raw[index] != '"':
                raise ValueError('body and title must be strings')
            value, index, closed = json_string(raw, index)
            if key in BODY_KEYS:
                if sum(item in BODY_KEYS for item in seen) != 1:
                    raise ValueError('ambiguous body')
                text = value
            else:
                title = value
            if not closed:
                break
        else:
            try:
                _, index = decoder.raw_decode(raw, index)
            except json.JSONDecodeError:
                break
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index == len(raw) or raw[index] == '}':
            break
        if raw[index] != ',':
            raise ValueError('invalid separator')
        index += 1
    return text, title


def project(raw, index):
    if not isinstance(raw, str) or len(raw) > MAX_RAW:
        raise ValueError('response size')
    raw.encode('utf-8')  # Reject invalid scalar values before any durable write.
    clean, omitted, pending = visible(raw)
    leading = clean.lstrip()
    title, kind, body = f'第 {index} 章', 'prose', clean
    if leading in ('`', '``'):
        return {'text': '', 'title': title, 'format': 'unrecognized', 'thoughtOmitted': omitted, 'pending': True}
    if leading.startswith('```'):
        fence = re.match(r'^```(?:json)?[ \t]*\r?\n', leading, re.I)
        if not fence:
            return {'text': '', 'title': title, 'format': 'unrecognized', 'thoughtOmitted': omitted, 'pending': True}
        body = leading[fence.end():]
        closing = re.search(r'\r?\n```\s*$', body)
        if closing:
            body = body[:closing.start()]
        else:
            pending = True
        if not body.lstrip().startswith('{'):
            return {'text': '', 'title': title, 'format': 'unrecognized', 'thoughtOmitted': omitted, 'pending': True}
    structured = body.lstrip()
    if structured.startswith(('{', '[')):
        if not bounded_json_structure(structured):
            return {'text': '', 'title': title, 'format': 'unrecognized', 'thoughtOmitted': omitted, 'pending': True}
        kind = 'json'
        try:
            value = json.loads(structured, object_pairs_hook=unique_object)
            if not isinstance(value, dict) or len(BODY_KEYS & value.keys()) != 1:
                raise ValueError('ambiguous body')
            body = value[next(iter(BODY_KEYS & value.keys()))]
            if not isinstance(body, str):
                raise ValueError('non-string body')
            body.encode('utf-8')
            titles = TITLE_KEYS & value.keys()
            if len(titles) == 1 and isinstance(value[next(iter(titles))], str):
                title = value[next(iter(titles))]
                try:
                    title.encode('utf-8')
                except UnicodeError:
                    title = f'第 {index} 章'
        except json.JSONDecodeError:
            pending = True
            try:
                if not structured.startswith('{'):
                    raise ValueError('array response')
                body, supplied_title = partial_object(structured)
                title = supplied_title if supplied_title is not None else title
            except (ValueError, RecursionError):
                body, kind = '', 'unrecognized'
        except (ValueError, UnicodeError, RecursionError):
            body, kind = '', 'unrecognized'
        body, inner_omitted, inner_pending = visible(body)
        omitted, pending = omitted or inner_omitted, pending or inner_pending
    else:
        first, separator, rest = body.lstrip().partition('\n')
        if not separator and (first in ('标', '标题', '#', '##', '###', '第')
                or re.fullmatch(r'第\s*[\d零〇一二三四五六七八九十百]+\s*', first)):
            return {'text': '', 'title': title, 'format': kind, 'thoughtOmitted': omitted, 'pending': True}
        if re.fullmatch(r'(?:标题\s*[:：]\s*|#{1,3}\s*|第\s*[\d零〇一二三四五六七八九十百]+\s*章).*', first) and len(first) <= 120:
            if separator:
                title = re.sub(r'^(?:标题\s*[:：]\s*|#{1,3}\s*)', '', first).strip()
                body = rest.lstrip('\r\n')
            else:
                body, pending = '', True
    title, title_omitted, title_pending = visible(title)
    if '\x00' in title:
        title = f'第 {index} 章'
    if '\x00' in body:
        body, kind = '', 'unrecognized'
    return {'text': body, 'title': title.strip()[:100] or f'第 {index} 章', 'format': kind,
            'thoughtOmitted': omitted or title_omitted, 'pending': pending or title_pending}


def record(raw, index, *, kind='original', finish_reason=None, previous=None):
    attempts = deepcopy(previous['attempts']) if previous else []
    candidate = {'kind': kind, 'raw': raw, 'rawHash': digest(raw), 'finishReason': finish_reason}
    for position, attempt in enumerate(attempts):
        if attempt['kind'] == kind:
            attempts[position] = candidate
            break
    else:
        attempts.append(candidate)
    presentation = project(raw, index)
    return {'schema': 'prose-presentation-v1', 'chapterIndex': index, 'selected': kind,
            'attempts': attempts, 'presentation': presentation}


def validate_record(value, text=None, title=None):
    if (not isinstance(value, dict) or set(value) != {'schema', 'chapterIndex', 'selected', 'attempts', 'presentation'}
            or value['schema'] != 'prose-presentation-v1' or type(value['chapterIndex']) is not int
            or not 1 <= value['chapterIndex'] <= 200 or value['selected'] not in ('original', 'repair')
            or not isinstance(value['attempts'], list) or not 1 <= len(value['attempts']) <= 2):
        raise ValueError('presentation format')
    kinds = set()
    selected = None
    for attempt in value['attempts']:
        if (not isinstance(attempt, dict) or set(attempt) != {'kind', 'raw', 'rawHash', 'finishReason'}
                or not isinstance(attempt['kind'], str) or attempt['kind'] not in ('original', 'repair') or attempt['kind'] in kinds
                or not isinstance(attempt['raw'], str) or len(attempt['raw']) > MAX_RAW
                or attempt['finishReason'] is not None and (not isinstance(attempt['finishReason'], str) or len(attempt['finishReason']) > 80)
                or attempt['rawHash'] != digest(attempt['raw'])):
            raise ValueError('presentation attempt')
        kinds.add(attempt['kind'])
        if attempt['kind'] == value['selected']:
            selected = attempt
    if selected is None or value['presentation'] != project(selected['raw'], value['chapterIndex']):
        raise ValueError('presentation source')
    if text is not None and value['presentation']['text'] != text or title is not None and value['presentation']['title'] != title:
        raise ValueError('presentation body')
    return deepcopy(value)


def validate_ledger(value):
    if not isinstance(value, dict) or len(value) > 10000:
        raise ValueError('presentation versions')
    for version, chapters in value.items():
        if (not isinstance(version, str) or not version.isdecimal() or str(int(version)) != version
                or not 1 <= int(version) <= 10000 or not isinstance(chapters, dict) or len(chapters) > 200):
            raise ValueError('presentation version')
        for index, item in chapters.items():
            if not isinstance(index, str) or not index.isdecimal() or str(int(index)) != index or not 1 <= int(index) <= 200:
                raise ValueError('presentation chapter')
            validate_record(item)
            if item['chapterIndex'] != int(index):
                raise ValueError('presentation index')
    return deepcopy(value)
