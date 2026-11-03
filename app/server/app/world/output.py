"""Remove only protocol/planning prefaces, never legitimate English prose."""
import re

# These are model instructions quoted as prose, not a language detector. Keep
# English dialogue, item inscriptions, and sentences inside quotation marks.
META = re.compile(r'^(?:outputMode\s+is\b|(?:The\s+)?outputMode\s*[=:]|'
                  r'(?:Since|Because)\b[^\n]*\boutputMode\b|'
                  r'(?:I\s+(?:will|should|need to)|I[\'’]ll)\s+(?:use|follow|output|write)\b'
                  r'[^\n]*(?:dialogue format|narrative format|Chinese prose)|'
                  r'输出模式(?:为|是|[=:：])|(?:根据|按照)\s*outputMode)', re.I)
PREFIXES = ('outputMode is', 'The outputMode =', 'Since', 'Because', "I'll", 'I’ll', 'I will',
            'I should', 'I need to', '输出模式', '根据 outputMode', '按照 outputMode')


def story_text(text, *, streaming=False):
    value = text.lstrip()
    while value:
        line, sep, rest = value.partition('\n')
        if META.search(line):
            if not sep and streaming:return ''
            value = rest.lstrip();continue
        # Hold only the possible first protocol line until enough tokens arrive
        # to classify it. The rest of the novel still streams normally.
        if streaming and not sep and any(prefix.lower().startswith(line.lower()) for prefix in PREFIXES):
            return ''
        break
    return value


def narrator_prompt_name(name, data):
    # This decision belongs to the server; the model never chooses its channel.
    return 'world_dialogue' if name == 'world_narrator' and data.get('outputMode') == 'dialogue' else name


def narrative_text(text):
    """Accidental chat channels in a story keep their actual speaker names."""
    from ..scenes.dialogue import MARKER
    markers = list(MARKER.finditer(text))
    if not markers:return text
    result = [text[:markers[0].start()].strip()]
    for index, marker in enumerate(markers):
        value = text[marker.end():markers[index+1].start() if index+1 < len(markers) else len(text)].strip()
        if marker[1] == '台词' and value:
            first, *rest = re.split(r'\n\s*\n', value)
            value = (marker[2] + '说：' if marker[2] else '') + '“' + first.strip('“”') + '”'
            if rest:value += '\n\n' + '\n\n'.join(rest)
        result.append(value)
    return '\n\n'.join(filter(None, result))
