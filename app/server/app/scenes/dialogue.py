"""Keep spoken words distinct from public stage directions, including old saves."""
import re
import json
from ..errors import Problem

MARKER = re.compile(r'【(旁白|台词)(?:[：:]([^】]+))?】')
QUOTES = re.compile(r'“([^”\n]*)”|「([^」\n]*)」|"([^"\n]*)"')


def structured_segments(text, people, player=None):
    """One JSON object per segment, with explicit, registry-checked speakers."""
    from .channel_json import channel_objects
    lookup = {key: p for p in people for key in (p['id'], p['name'])}
    result = []
    try:
        for part in channel_objects(text.strip()):
            if not isinstance(part, dict) or part.get('kind') not in ('narration', 'speech') or not isinstance(part.get('text'), str):raise ValueError()
            if not part['text'].strip():continue
            if part['kind'] == 'speech':
                if player and part.get('speakerId') in (player['id'],player['name']):
                    result.append({'kind':'narration','text':player['name']+'：“'+part['text'].strip()+'”'})
                    continue
                person = lookup.get(part.get('speakerId'))
                if not person:raise ValueError()
                result.append({'kind':'speech', 'speakerId':person['id'], 'speakerName':person['name'], 'text':part['text'].strip()})
            else:result.append({'kind':'narration', 'text':part['text'].strip()})
        if not result:raise ValueError()
        return result
    except (ValueError, TypeError, KeyError):
        raise Problem('WORLD_OUTPUT_INCOMPLETE', '回复分段尚未完成，已收到内容仍保留')
NARRATIVE = r'雾气|远处|水面|天色|夜色|风声|海风|没有说话|没有开口|沉默|抬头|低头|转身|看着你|望着你|你站在|你走到|你看见|你听见'
VERBS = r'正|正在|已经|缓缓|轻轻|忽然|仍|没有|并未|把|将|抬|低|转|站|坐|望|看|整理|停|沉默|说|答|问|笑|点头|摇头|收|伸|走|拿|的|手上|微微'


def actor_pattern(name):
    actor='(?:'+'|'.join(re.escape(v) for v in (name,'他','她','对方') if v)+')'
    return actor+r'(?:侧过|收钱|抬起|低下|转过|看你|看了|望向|顿了|点了|摇了|伸手|屈指|摆了|挥了|退开|放下|收起|起身|负手|把|将|没有说话|没有开口)|^(?:松风|雾气|鼎内|药汁|天色|日头|噼啪声|风声|柜台)[^。！？]{0,20}(?:掠|响|声|在|变|的|传|起)'


def separate_stage_directions(segments, partner_name=''):
    """Repair a narrow channel mistake without dropping text or changing saves."""
    result=[]
    for part in segments:
        if part['kind']!='speech':result.append(dict(part));continue
        actor='(?:'+'|'.join(re.escape(v) for v in (part.get('speakerName') or partner_name,'他','她','对方') if v)+')'
        direction=re.compile(r'(?:^|(?<=[。！？\n]))'+actor+r'(?:指了指[^，。！？\n]{0,40}[，。！？]|(?:收钱入账|伸出[^。！？\n]{0,40}(?:手指|手)|顿了顿|抬手|抬起头|看你一眼|看了你一眼|点了点头|摇了摇头)[^。！？：:\n]{0,70}[：:。！？])')
        if re.match(actor_pattern(part.get('speakerName',partner_name)),part['text']) and not re.search('[：:“”「」"]',part['text']):
            result.append({'kind':'narration','text':part['text']});continue
        cursor=0
        for match in direction.finditer(part['text']):
            if before:=part['text'][cursor:match.start()].strip():result.append({**part,'text':before})
            result.append({'kind':'narration','text':match[0].strip()});cursor=match.end()
        if tail:=part['text'][cursor:].strip():result.append({**part,'text':tail})
    return result


def dialogue_segments(text, partner_name='', *, streaming=False):
    """Explicit channels win. Unmarked old prose stays visible as narration.

    Only quotes attributed to the selected partner (or a standalone quoted
    utterance) become speech. Quoted objects and other people's lines do not.
    """
    result = []

    def add(kind, value):
        value = value.strip()
        if not value:return
        if result and result[-1]['kind'] == kind:result[-1]['text'] += '\n\n' + value
        else:result.append({'kind': kind, 'text': value})

    markers = list(MARKER.finditer(text))
    if markers:
        add('narration', text[:markers[0].start()])
        for index, marker in enumerate(markers):
            end = markers[index+1].start() if index+1 < len(markers) else len(text)
            value = text[marker.end():end]
            if streaming:value = re.sub(r'【(?:旁(?:白)?|台(?:词)?)?$', '', value)
            if marker[1] == '台词':
                while match := re.match(r'^\s*[（(]([^）)]*)[）)]\s*', value):
                    add('narration', match[1]);value=value[match.end():]
            kind='speech' if marker[1]=='台词' and (not marker[2] or not partner_name or marker[2]==partner_name) else 'narration'
            for part_index,part in enumerate(re.split(r'\n\s*\n',value)):
                # Some providers forget the next narration tag. Paragraphs that
                # explicitly describe third-person actions remain stage prose.
                stage=kind=='speech' and bool(re.match(actor_pattern(partner_name),part.strip()) or part_index>0 and partner_name and re.search(re.escape(partner_name)+r'(?:伸手|抬|收|看|把|将|顿|转)',part))
                add('narration' if stage else kind,part)
        return separate_stage_directions(result,partner_name)
    if streaming and re.fullmatch(r'【(?:旁(?:白)?|台(?:词)?)?', text.strip()):return []
    actor = '(?:' + '|'.join(re.escape(v) for v in [partner_name, '他', '她', '对方'] if v) + ')'
    attribution = re.compile(actor + r'[^。！？“”「」"\n]{0,24}(?:说|答|问|道|嘀咕|低语)[^。！？“”「」"\n]{0,8}[：:,，]?\s*$')
    narrative = re.compile(NARRATIVE + '|' + actor + r'(?:' + VERBS + ')' + r'|^[^，。：:\n“”「」]{1,12}(?:说|问|答)(?:道)?[：:]')
    direct = re.compile(r'^(?:我(?:们)?(?:[，,。!?]|会|想|要|是|在|不|没|也|和|陪|带|等)|你好|好[，。,！!]|嗯|别|请)')
    for paragraph in re.split(r'\n\s*\n', text):
        paragraph = paragraph.strip()
        if not paragraph:continue
        quotes = list(QUOTES.finditer(paragraph));cursor = 0
        for quote in quotes:
            before, after = paragraph[:quote.start()], paragraph[quote.end():]
            attributed = bool(attribution.search(before))
            standalone = not before.strip() and not after.strip()
            leading = not before.strip() and bool(re.match(actor + r'(?:说|答|问|道)', after.lstrip('，,。 ')))
            if attributed or standalone or leading:
                add('narration', paragraph[cursor:quote.start()])
                add('speech', next(v for v in quote.groups() if v is not None));cursor = quote.end()
        if cursor:add('narration', paragraph[cursor:])
        else:add('narration' if narrative.search(paragraph) and not direct.search(paragraph) else 'speech', paragraph)
    return separate_stage_directions(result,partner_name)


def dialogue_text(segments):
    return '\n\n'.join((part.get('speakerName', '') + '说：' if part.get('speakerName') else '') + '“' + part['text'].strip('“”') + '”' if part['kind'] == 'speech' else part['text'] for part in segments)


def scene_segments(text, people, partner_id=None):
    """Resolve explicit channels against the actual public NPC registry."""
    lookup={key:person for person in people for key in (person['id'],person['name'])}
    partner=lookup.get(partner_id)
    if text.lstrip().startswith('{"kind"'):return structured_segments(text, people)
    markers=list(MARKER.finditer(text))
    if not markers:
        return dialogue_segments(text,partner['name']) if partner else [{'kind':'narration','text':text}]
    result=[]
    if text[:markers[0].start()].strip():result.append({'kind':'narration','text':text[:markers[0].start()].strip()})
    for index,marker in enumerate(markers):
        value=text[marker.end():markers[index+1].start() if index+1<len(markers) else len(text)].strip()
        person=lookup.get(marker[2]) if marker[2] else partner
        if marker[1]=='旁白' or not person:
            if marker[1]=='台词' and value:value='“'+value.strip('“”')+'”'
            if value:result.append({'kind':'narration','text':value})
            continue
        parts=dialogue_segments('【台词】'+value,person['name'])
        result.extend({**part,**({'speakerId':person['id'],'speakerName':person['name']} if part['kind']=='speech' and marker[2] else {})} for part in parts)
    return result


def conversation_segments(entries):
    return [{**entry, 'segments': separate_stage_directions(entry['segments'],entry.get('partnerName','')) if entry.get('segments') else dialogue_segments(entry.get('reply', ''), entry.get('partnerName', ''))} for entry in entries]
