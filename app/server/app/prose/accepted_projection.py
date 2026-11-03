"""Exact original UTF16 coordinates for existing local/server acceptance styles."""
import re
from ..domain import utf16_len

JS_SPACE = '\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
PYTHON_SPACE = '\t\n\v\f\r\u001c-\u001f \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000'
LOCAL_TRIM = re.compile('^[' + JS_SPACE + ']+|[' + JS_SPACE + ']+$')
LOCAL_LEADING = re.compile('^[' + JS_SPACE + ']+')
EMPTY_SERVER = re.compile('^[' + PYTHON_SPACE + ']*$')
HEADING = re.compile('^[' + JS_SPACE + ']*(?:#{1,3}[' + JS_SPACE + ']*)?'
    '(?:第[0-9零〇一二三四五六七八九十百]+章|第[' + JS_SPACE + ']*[0-9]+[' + JS_SPACE + ']*章)([^\n]*)\n')


def project_accepted_text(text, style):
    if not isinstance(text, str) or style not in ('local-v1', 'server-v1'):
        raise ValueError('acceptance projection input')
    try:
        text.encode('utf-8')
        if utf16_len(text) > 6_000_000:
            raise ValueError('acceptance projection size')
    except UnicodeError as error:
        raise ValueError('acceptance projection Unicode') from error
    title = HEADING.match(text) if style == 'local-v1' else None
    base = title.end() if title and utf16_len(title[1]) <= 100 else 0
    body, segments = text[base:], []
    last_original, last_utf16 = 0, 0
    delimiter = re.compile('\n[' + JS_SPACE + ']*\n|\n') if style == 'local-v1' else re.compile('\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]')

    def add(start, end):
        nonlocal last_original, last_utf16
        raw = body[start:end]
        value = LOCAL_TRIM.sub('', raw) if style == 'local-v1' else raw
        if (not value if style == 'local-v1' else EMPTY_SERVER.fullmatch(value)):
            return
        leading = LOCAL_LEADING.match(raw) if style == 'local-v1' else None
        original = base + start + (leading.end() if leading else 0)
        first, size = last_utf16 + utf16_len(text[last_original:original]), utf16_len(value)
        last_original, last_utf16 = original, first
        segments.append({'text': value, 'sourceStart': first, 'sourceEnd': first + size,
                         'targetStart': 0, 'targetEnd': size})

    start = 0
    for match in delimiter.finditer(body):
        add(start, match.start()); start = match.end()
    add(start, len(body))
    return {'schema': 'accepted-projection-v1', 'style': style, 'segments': segments}


def validate_accepted_projection(value, text, style):
    expected = project_accepted_text(text, style)
    if (type(value) is not dict or set(value) != {'schema', 'style', 'segments'}
            or value['schema'] != expected['schema'] or value['style'] != style
            or type(value['segments']) is not list or len(value['segments']) != len(expected['segments'])):
        raise ValueError('acceptance projection mismatch')
    for part, wanted in zip(value['segments'], expected['segments']):
        if (type(part) is not dict or set(part) != set(wanted)
                or any(type(part[key]) is not type(item) or part[key] != item for key, item in wanted.items())):
            raise ValueError('acceptance projection segment mismatch')
    return expected
