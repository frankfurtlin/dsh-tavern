"""Exact quoted views of accepted sources; no provider calls or source mutations."""
from copy import deepcopy
from ..domain import canonical, digest, utf16_len, utf16_slice
from .events import validate
from .accepted_projection import validate_accepted_projection, JS_SPACE, PYTHON_SPACE
import re


def fail():raise ValueError('accepted event source range')
def ref_key(ref):return canonical(ref)


def derive_visible_accepted_events(sources, prefix):
    if len(sources) > 1000 or len(prefix) > 10000:fail()
    visible = {}
    for position, part in enumerate(prefix):
        if (part['sourceParagraphId'] in visible or type(part['paragraphRevision']) is not int
                or part['paragraphRevision'] != 1 or digest(part['text']) != part['textHash']):fail()
        visible[part['sourceParagraphId']] = (part, position)
    origins, seen = {}, set()
    for source in sources:
        style = source['style']
        validate_accepted_projection({'schema':'accepted-projection-v1','style':style,
            'segments':[{key:part[key] for key in ['text','sourceStart','sourceEnd','targetStart','targetEnd']}
                        for part in source['segments']]}, source['text'], style)
        unique = canonical([source['runId'], source['contentId']])
        if unique in seen:fail()
        seen.add(unique)
        if (type(source['runRevision']) is not int or source['runRevision'] < 1
                or source['chapterIndex'] != source['ledger']['chapterIndex']
                or source['ledger']['sourceHash'] != digest(source['text'])):fail()
        validate(source['ledger'], source['text'])
        refs = origins.setdefault(source['runId'], {})
        for event in source['ledger']['events']:
            if event['id'] in refs:fail()
            refs[event['id']] = {'runId':source['runId'], 'contentId':source['contentId'], 'localEventId':event['id']}
        last = 0
        for part in source['segments']:
            if (part['sourceStart'] < last or utf16_slice(source['text'], part['sourceStart'], part['sourceEnd']) != part['text']
                    or part['sourceEnd'] - part['sourceStart'] != utf16_len(part['text'])
                    or part['targetStart'] != 0 or part['targetEnd'] != utf16_len(part['text'])
                    or digest(part['text']) != part['paragraphHash']):fail()
            last = part['sourceEnd']
    frames, proposed, clipped, dependency_clipped, visible_refs = {}, [], [], [], set()
    # Gaps must be omitted whitespace for the exact acceptance style; never headings.
    for source in sources:
        refs, ledger_hash = origins[source['runId']], digest(canonical(source['ledger']))
        empty_gap = re.compile('^[' + (PYTHON_SPACE if source['style']=='server-v1' else JS_SPACE) + ']*$')
        past, frame_ref = False, None
        def resolve(identifier):
            if identifier not in refs:fail()
            return refs[identifier]
        for event in source['ledger']['events']:
            ref, dependencies, current_frame = refs[event['id']], [], frame_ref
            if event['kind'] == 'time' and event['change']['relativeTo'] is not None:
                dependencies.append(resolve(event['change']['relativeTo']))
            if event['kind'] == 'event':dependencies.extend(resolve(item) for item in event['change']['causedBy'])
            if event['certainty'] == 'narrated':
                if event['kind'] == 'time' and event['change']['relation'] != 'unknown':
                    relative = event['change']['relativeTo']
                    past = event['change']['relation'] == 'before' or (relative is not None and frames.get(ref_key(resolve(relative))) is True)
                    current_frame, frame_ref = None, ref
                frames[ref_key(ref)] = past
            if current_frame is not None:dependencies.append(current_frame)
            unique_deps = list({ref_key(item):item for item in dependencies}.values())
            evidence, covered, last_position, valid = [], event['start'], -1, True
            for part in source['segments']:
                start, end = max(event['start'], part['sourceStart']), min(event['end'], part['sourceEnd'])
                if end <= start:continue
                if not empty_gap.fullmatch(utf16_slice(source['text'], covered, start)):valid = False
                visible_part = visible.get(part['paragraphId'])
                target_start, target_end = start - part['sourceStart'], end - part['sourceStart']
                if (visible_part is None or last_position >= 0 and visible_part[1] != last_position + 1
                        or target_end > utf16_len(visible_part[0]['text'])
                        or not part['text'].startswith(visible_part[0]['text'])):valid = False
                else:
                    item, last_position = visible_part
                    quote = utf16_slice(item['text'], target_start, target_end)
                    evidence.append({'paragraphId':item['paragraphId'], 'paragraphRevision':item['paragraphRevision'],
                                     'start':target_start, 'end':target_end, 'quote':quote})
                covered = end
            if not evidence or not empty_gap.fullmatch(utf16_slice(source['text'], covered, event['end'])):valid = False
            if not valid:
                clipped.append(deepcopy(ref));continue
            if any(ref_key(item) not in visible_refs for item in unique_deps):
                dependency_clipped.append(deepcopy(ref));continue
            visible_refs.add(ref_key(ref))
            proposed.append({'ref':deepcopy(ref), 'sourceRunRevision':source['runRevision'],
                'sourceHash':source['ledger']['sourceHash'], 'ledgerHash':ledger_hash,
                'entry':deepcopy(event), 'dependencies':deepcopy(unique_deps),
                'frameRef':deepcopy(current_frame), 'temporalFrame':'past' if past else 'present', 'evidence':evidence})
    return {'schema':'accepted-event-view-v1','events':proposed,'clippedRefs':clipped,'dependencyClippedRefs':dependency_clipped}
