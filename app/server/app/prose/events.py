"""Quoted, typed narrative changes. Model proposals are never source-book truth.

This ledger keeps narration order distinct from claimed story time, so a
flashback is not rejected merely because it appears in a later chapter.
"""
from copy import deepcopy
import json
import re
from ..domain import digest, utf16_len, utf16_slice
from .presentation import bounded_json_structure, unique_object

SCHEMA = 'prose-events-v1'
KINDS = {'character', 'item', 'location', 'time', 'motivation', 'knowledge', 'relationship', 'unresolved', 'event'}
EVENT_ID = re.compile(r'c([1-9][0-9]{0,2}):e([1-9][0-9]?)$')


def scalar(value, maximum, *, nullable=False):
    if nullable and value is None:
        return True
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        return False
    try:
        value.encode('utf-8')
        return True
    except UnicodeError:
        return False


def exact(value, fields):
    return isinstance(value, dict) and set(value) == set(fields.split())


def event_id(value):
    match = EVENT_ID.fullmatch(value) if isinstance(value, str) else None
    return bool(match and int(match[1]) <= 200 and int(match[2]) <= 12)


def identity(value, subjects=None, nullable=False):
    return value is None and nullable or scalar(value, 160) and (subjects is None or value in subjects)


def references(value, known):
    return isinstance(value, list) and len(value) <= 12 and all(event_id(item) and (known is None or item in known) for item in value) and len(set(value)) == len(value)


def change_valid(kind, change, subjects=None, known=None):
    if kind == 'character':
        return (exact(change, 'name aliases') and scalar(change['name'], 160)
            and isinstance(change['aliases'], list) and len(change['aliases']) <= 8
            and all(scalar(item, 160) for item in change['aliases']) and len(set(change['aliases'])) == len(change['aliases']))
    if kind == 'item':
        return (exact(change, 'item fromHolderId toHolderId location') and scalar(change['item'], 200)
            and identity(change['fromHolderId'], subjects, True) and identity(change['toHolderId'], subjects, True)
            and scalar(change['location'], 200, nullable=True))
    if kind == 'location':
        return exact(change, 'from to') and scalar(change['from'], 200, nullable=True) and scalar(change['to'], 200)
    if kind == 'time':
        return (exact(change, 'label relation relativeTo') and scalar(change['label'], 200)
            and change['relation'] in ('before', 'after', 'same_time', 'unknown')
            and (change['relativeTo'] is None and change['relation'] == 'unknown'
                or event_id(change['relativeTo']) and change['relation'] != 'unknown' and (known is None or change['relativeTo'] in known)))
    if kind == 'motivation':
        return exact(change, 'goal state') and scalar(change['goal'], 400) and change['state'] in ('adopted', 'pursued', 'abandoned', 'uncertain')
    if kind == 'knowledge':
        return (exact(change, 'fact learnedByIds') and scalar(change['fact'], 600)
            and isinstance(change['learnedByIds'], list) and 1 <= len(change['learnedByIds']) <= 16
            and all(identity(item, subjects) for item in change['learnedByIds']) and len(set(change['learnedByIds'])) == len(change['learnedByIds']))
    if kind == 'relationship':
        return exact(change, 'otherId relation') and identity(change['otherId'], subjects) and scalar(change['relation'], 400)
    if kind == 'unresolved':
        return (exact(change, 'threadKey state detail') and scalar(change['threadKey'], 200)
            and change['state'] in ('opened', 'advanced', 'resolved') and scalar(change['detail'], 600))
    if kind == 'event':
        return exact(change, 'effect causedBy') and scalar(change['effect'], 600) and references(change['causedBy'], known)
    return False


def validate(value, text=None, *, subjects=None, prior_ids=None):
    if (not exact(value, 'schema sourceHash chapterIndex status events') or value['schema'] != SCHEMA
        or not isinstance(value['sourceHash'], str) or not re.fullmatch('[a-f0-9]{64}', value['sourceHash'])
        or type(value['chapterIndex']) is not int or not 1 <= value['chapterIndex'] <= 200
        or value['status'] not in ('model-proposed', 'unavailable') or not isinstance(value['events'], list)
        or len(value['events']) > 12 or value['status'] == 'unavailable' and value['events']):
        raise ValueError('event ledger format')
    if text is not None and digest(text) != value['sourceHash']:
        raise ValueError('event source hash')
    known = set(prior_ids) if prior_ids is not None else None
    for ordinal, entry in enumerate(value['events'], 1):
        if (not exact(entry, 'id kind subjectId certainty confidence statement quote start end offsetUnit change')
            or entry['id'] != f'c{value["chapterIndex"]}:e{ordinal}' or not isinstance(entry['kind'], str) or entry['kind'] not in KINDS
            or not identity(entry['subjectId'], subjects, True) or entry['certainty'] not in ('narrated', 'inferred')
            or type(entry['confidence']) is not int or not 0 <= entry['confidence'] <= 1000
            or not scalar(entry['statement'], 600) or not scalar(entry['quote'], 1200)
            or entry['offsetUnit'] != 'utf16' or type(entry['start']) is not int or type(entry['end']) is not int
            or not 0 <= entry['start'] < entry['end'] <= 6_000_000 or entry['end'] - entry['start'] != utf16_len(entry['quote'])
            or not change_valid(entry['kind'], entry['change'], subjects, known)):
            raise ValueError('event entry format')
        if text is not None and utf16_slice(text, entry['start'], entry['end']) != entry['quote']:
            raise ValueError('event quote source')
        # Every reference is backwards in narration, never self/forward or cyclic.
        refs = entry['change'].get('causedBy', []) + ([entry['change']['relativeTo']] if entry['kind'] == 'time' and entry['change']['relativeTo'] is not None else [])
        current = (value['chapterIndex'], ordinal)
        if any((int(EVENT_ID.fullmatch(ref)[1]), int(EVENT_ID.fullmatch(ref)[2])) >= current for ref in refs):
            raise ValueError('event reference order')
        if known is not None:
            known.add(entry['id'])
    return deepcopy(value)


def from_review(result, text, chapter, subjects=None, prior_ids=()):
    empty = {'schema':SCHEMA, 'sourceHash':digest(text), 'chapterIndex':chapter, 'status':'unavailable', 'events':[]}
    try:
        if not result or result.get('finishReason') not in ('stop', 'completed'):
            return empty
        raw = result['text']
        if not isinstance(raw, str) or len(raw) > 100_000 or not bounded_json_structure(raw):
            return empty
        data = json.loads(raw, object_pairs_hook=unique_object)
        proposals = data.get('events')
        if not isinstance(proposals, list) or len(proposals) > 12:
            return empty
        entries = []
        for ordinal, proposal in enumerate(proposals, 1):
            if not exact(proposal, 'kind subjectId certainty confidence statement quote change'):
                raise ValueError('event proposal')
            quote = proposal['quote']
            start = text.find(quote) if isinstance(quote, str) else -1
            if start < 0 or text.find(quote, start + 1) >= 0:
                raise ValueError('event ambiguous quote')
            offset = utf16_len(text[:start])
            entries.append({**proposal, 'id':f'c{chapter}:e{ordinal}', 'start':offset, 'end':offset + utf16_len(quote), 'offsetUnit':'utf16'})
        return validate({**empty, 'status':'model-proposed', 'events':entries}, text, subjects=subjects, prior_ids=prior_ids)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return empty


def validate_ledger(value):
    if not isinstance(value, dict) or len(value) > 10000:
        raise ValueError('event ledger versions')
    output = {}
    for version, chapters in value.items():
        if (not isinstance(version, str) or not version.isascii() or not version.isdecimal()
                or str(int(version)) != version or not 1 <= int(version) <= 10000
                or not isinstance(chapters, dict) or len(chapters) > 200):
            raise ValueError('event ledger version')
        for index in chapters:
            if (not isinstance(index, str) or not index.isascii() or not index.isdecimal()
                    or str(int(index)) != index or not 1 <= int(index) <= 200):
                raise ValueError('event ledger chapter')
        prior, checked = set(), {}
        for index in sorted(chapters, key=int):
            chapter = validate(chapters[index], prior_ids=prior)
            if chapter['chapterIndex'] != int(index):
                raise ValueError('event ledger chapter binding')
            checked[index] = chapter
            prior.update(entry['id'] for entry in chapter['events'])
        output[version] = checked
    return output


def fold(ledgers):
    return _fold_groups([(None, ledgers)])


def fold_scoped(groups):
    """Fold validated batches with explicit event namespaces; never merge names."""
    if not isinstance(groups, list) or len(groups) > 1000:
        raise ValueError('scoped ledger groups')
    scopes = set()
    checked = []
    for group in groups:
        if (not isinstance(group, dict) or set(group) != {'scope', 'ledgers'}
                or not scalar(group['scope'], 300)
                or group['scope'] in scopes or not isinstance(group['ledgers'], list)):
            raise ValueError('scoped ledger namespace')
        scopes.add(group['scope']); checked.append((group['scope'], group['ledgers']))
    return _fold_groups(checked)


def _fold_groups(groups):
    """Finite proposed state and soft continuity observations, never a gate."""
    items, locations, motives, threads, knowledge, people, temporal, hints, order = {}, {}, {}, {}, {}, {}, [], [], []
    historical, frames, relations, causal = [], {}, {}, []
    for scope, ledgers in groups:
        prior, previous_chapter = set(), 0
        def key_for(identifier):
            return identifier if scope is None else scope + "/" + identifier
        for ledger in ledgers:
            value = validate(ledger, prior_ids=prior)
            if value['chapterIndex'] <= previous_chapter:
                raise ValueError('ledger chapter order')
            previous_chapter = value['chapterIndex']
            earlier_frame = False
            for entry in value['events']:
                prior.add(entry['id']); order.append(key_for(entry['id']))
                if entry['certainty'] != 'narrated':
                    continue
                change, subject, key = entry['change'], entry['subjectId'], key_for(entry['id'])
                if entry['kind'] == 'time':
                    temporal.append({'eventId':key, **change, 'relativeTo':key_for(change['relativeTo']) if change['relativeTo'] is not None else None})
                    if change['relation'] != 'unknown':
                        earlier_frame = change['relation'] == 'before' or frames.get(key_for(change['relativeTo']), False)
                    frames[key] = earlier_frame
                    continue
                frames[key] = earlier_frame
                if entry['kind'] == 'event':
                    causal.append({'eventId':key, 'effect':change['effect'], 'causedBy':[key_for(ref) for ref in change['causedBy']], 'temporalFrame':'past' if earlier_frame else 'present'})
                if earlier_frame:
                    # Quoted flashback changes stay in narration history. They do
                    # not silently move a present-day object/actor into the past.
                    historical.append(key)
                    continue
                def hint(code, predecessor):
                    hints.append({'code':code, 'eventId':key, 'previousEventId':predecessor, 'severity':'soft'})
                if entry['kind'] == 'item':
                    previous = items.get(change['item'])
                    if previous and previous['holderId'] is not None and change['fromHolderId'] is not None and previous['holderId'] != change['fromHolderId']:
                        hint('ITEM_TRANSFER_GAP', previous['eventId'])
                    items[change['item']] = {'holderId':change['toHolderId'], 'location':change['location'], 'eventId':key}
                elif entry['kind'] == 'location' and subject is not None:
                    previous = locations.get(subject)
                    if previous and change['from'] is not None and previous['place'] != change['from']:
                        hint('LOCATION_TRANSITION_GAP', previous['eventId'])
                    locations[subject] = {'place':change['to'], 'eventId':key}
                elif entry['kind'] == 'motivation' and subject is not None:
                    motives[subject] = {**change, 'eventId':key}
                elif entry['kind'] == 'knowledge':
                    for actor in change['learnedByIds']:
                        knowledge.setdefault(actor, []).append({'fact':change['fact'], 'eventId':key})
                elif entry['kind'] == 'unresolved':
                    previous = threads.get(change['threadKey'])
                    if previous and previous['state'] == 'resolved' and change['state'] == 'advanced':
                        hint('THREAD_REOPENING_UNCLEAR', previous['eventId'])
                    threads[change['threadKey']] = {'state':change['state'], 'detail':change['detail'], 'eventId':key}
                elif entry['kind'] == 'relationship' and subject is not None:
                    relations.setdefault(subject, {})[change['otherId']] = {'relation':change['relation'], 'eventId':key}
                elif entry['kind'] == 'character':
                    # A name is display data. Only stable IDs can update identity.
                    people[key if subject is None else subject] = {**change, 'subjectId':subject, 'eventId':key}
    return {'schema':'prose-event-state-v2', 'eventOrder':order, 'items':items, 'locations':locations,
        'motivations':motives, 'threads':threads, 'knowledge':knowledge, 'characters':people, 'timeRelations':temporal, 'historicalEvents':historical, 'relationships':relations, 'causalEvents':causal, 'hints':hints}
