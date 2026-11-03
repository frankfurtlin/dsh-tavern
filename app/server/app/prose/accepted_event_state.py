"""Replay proven events across origins without guessing identities by names."""
from copy import deepcopy
from ..domain import canonical
from .events import fold_scoped


def fold_visible_accepted_events(events, bindings):
    identities = {}
    for binding in bindings:
        key = canonical([binding['runId'], binding['subjectId']])
        if key in identities:raise ValueError('duplicate accepted identity')
        identities[key] = binding['canonicalId']
    state = fold_scoped([])
    state['schema'] = 'accepted-event-state-v1'
    known = set()
    for view in events:
        identifier, event = canonical(view['ref']), view['entry']
        if identifier in known or any(canonical(ref) not in known for ref in view['dependencies']):
            raise ValueError('accepted event dependency closure')
        known.add(identifier);state['eventOrder'].append(identifier)
        if event['certainty'] != 'narrated':continue
        def actor(value):
            return None if value is None else identities.get(canonical([view['ref']['runId'], value]), canonical([view['ref']['runId'], value]))
        def source(local):
            refs = [ref for ref in view['dependencies'] if ref['runId'] == view['ref']['runId'] and ref['localEventId'] == local]
            if not refs:raise ValueError('accepted event source reference')
            return canonical(refs[0])
        def hint(code, previous):
            state['hints'].append({'code':code,'eventId':identifier,'previousEventId':previous,'severity':'soft'})
        change, kind, subject = event['change'], event['kind'], actor(event['subjectId'])
        if kind == 'time':
            state['timeRelations'].append(dict(deepcopy(change), eventId=identifier, relativeTo=None if change['relativeTo'] is None else source(change['relativeTo'])))
            continue
        if kind == 'event':state['causalEvents'].append({'eventId':identifier,'effect':change['effect'],'causedBy':[source(ref) for ref in change['causedBy']], 'temporalFrame':view['temporalFrame']})
        if view['temporalFrame'] == 'past':state['historicalEvents'].append(identifier);continue
        if kind == 'item':
            old = state['items'].get(change['item']);holder = actor(change['fromHolderId'])
            if old and old['holderId'] is not None and holder is not None and old['holderId'] != holder:hint('ITEM_TRANSFER_GAP',old['eventId'])
            state['items'][change['item']] = {'holderId':actor(change['toHolderId']),'location':change['location'],'eventId':identifier}
        elif kind == 'location' and subject is not None:
            old = state['locations'].get(subject)
            if old and change['from'] is not None and old['place'] != change['from']:hint('LOCATION_TRANSITION_GAP',old['eventId'])
            state['locations'][subject] = {'place':change['to'],'eventId':identifier}
        elif kind == 'motivation' and subject is not None:state['motivations'][subject] = dict(deepcopy(change),eventId=identifier)
        elif kind == 'knowledge':
            for value in change['learnedByIds']:state['knowledge'].setdefault(actor(value),[]).append({'fact':change['fact'],'eventId':identifier})
        elif kind == 'unresolved':
            old = state['threads'].get(change['threadKey'])
            if old and old['state']=='resolved' and change['state']=='advanced':hint('THREAD_REOPENING_UNCLEAR',old['eventId'])
            state['threads'][change['threadKey']] = {'state':change['state'],'detail':change['detail'],'eventId':identifier}
        elif kind == 'relationship' and subject is not None:state['relationships'].setdefault(subject,{})[actor(change['otherId'])] = {'relation':change['relation'],'eventId':identifier}
        elif kind == 'character':state['characters'][subject if subject is not None else identifier] = dict(deepcopy(change),subjectId=subject,eventId=identifier)
    return state


def candidate_views(scope, earlier):
    refs = {entry['id']:{'runId':scope,'contentId':row['contentId'],'localEventId':entry['id']} for row in earlier for entry in (row.get('eventLedger') or {}).get('events',[])}
    frames, views = {}, []
    for row in earlier:
        past, frame = False, None
        for entry in (row.get('eventLedger') or {}).get('events',[]):
            ref, current, deps = refs[entry['id']], frame, []
            if entry['kind']=='time' and entry['change']['relativeTo'] is not None:deps.append(refs[entry['change']['relativeTo']])
            if entry['kind']=='event':deps.extend(refs[value] for value in entry['change']['causedBy'])
            if entry['certainty']=='narrated':
                if entry['kind']=='time' and entry['change']['relation']!='unknown':
                    relative=entry['change']['relativeTo'];past=entry['change']['relation']=='before' or relative is not None and frames.get(canonical(refs[relative])) is True
                    frame,current=ref,None
                frames[canonical(ref)] = past
            if current is not None:deps.append(current)
            views.append({'ref':ref,'entry':entry,'dependencies':list({canonical(dep):dep for dep in deps}.values()),'frameRef':current,'temporalFrame':'past' if past else 'present'})
    return views


def combined_state(basis, earlier, bindings, scope):
    return fold_visible_accepted_events(basis['events'] + candidate_views(scope, earlier), basis['subjects'] + bindings)
