"""Lossless repair of unescaped quotation marks in a complete text channel."""
import json
import re


def channel_objects(text):
    """Accept literal newline separators only BETWEEN complete JSON objects.

    JSON's own string decoder consumes quoted escapes first. Never globally
    replace backslash-n: that would alter a character's actual words.
    """
    decoder=json.JSONDecoder();cursor=0
    while cursor<len(text):
        while cursor<len(text):
            if text[cursor].isspace():cursor+=1
            elif text.startswith(('\\n','\\r'),cursor):cursor+=2
            else:break
        if cursor==len(text):return
        try:part,end=decoder.raw_decode(text,cursor)
        except ValueError:
            # Preserve the existing narrow repair for an unescaped quotation
            # in one complete channel; do not guess across malformed objects.
            end=text.find('\n',cursor)
            if end<0:end=len(text)
            part=channel_object(text[cursor:end])
        if end<len(text) and not text[end].isspace() and not text.startswith(('\\n','\\r'),end):raise ValueError('Invalid channel separator')
        yield part
        cursor=end


def channel_object(line):
    try:return json.loads(line)
    except ValueError:
        match=re.fullmatch(r'\s*\{\s*"kind"\s*:\s*"(narration|speech)"\s*,\s*(?:"speakerId"\s*:\s*"([^"\\]*)"\s*,\s*)?"text"\s*:\s*"(.*)"\s*}\s*',line)
        if not match:raise
        kind,speaker,raw=match.groups()
        # Additional fields/objects are not inner quotation marks. Do not
        # reinterpret malformed protocol fields as a character's words.
        if re.search(r'"\s*,\s*"[^"\n]+"\s*:',raw):raise ValueError()
        escaped=False;characters=[]
        for char in raw:
            characters.append('\\"' if char=='"' and not escaped else char)
            escaped=not escaped if char=='\\' else False
        value=json.loads('"'+''.join(characters)+'"')
        return {'kind':kind,'text':value,**({'speakerId':speaker} if speaker is not None else {})}
