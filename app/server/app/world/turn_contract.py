"""Round-local typed references and capabilities, independent of model spelling."""
from copy import deepcopy
from ..models import uid


def reference_wire(data, definition):
    ids = {e['id'] for e in definition['entities']}
    ids.update(l['id'] for l in definition.get('lore', []))
    ids.update(e['id'] for e in data.get('queuedEvents', []) if e.get('id'))
    mapping = {key: f'E{index:03}' for index, key in enumerate(sorted(ids), 1)}
    value = replace_references(data, mapping)
    value['referenceProtocol'] = 'Use the exact E### references. New entities need a distinct NEW_name reference. Never invent or copy opaque IDs. itemId must refer to an item.'
    value['registry'] = [{'id': mapping[e['id']], 'type': e['type'], 'name': e['name']}
                         for e in definition['entities']]
    return value, {v: k for k, v in mapping.items()}


def replace_references(value, mapping):
    if isinstance(value, dict):
        return {mapping.get(k, k): replace_references(v, mapping) for k, v in value.items()}
    if isinstance(value, list):return [replace_references(v, mapping) for v in value]
    if isinstance(value, str):
        if value in mapping:return mapping[value]
        if value.startswith(('relation:', 'inventory:', 'entity:')):
            prefix, key = value.split(':', 1)
            return prefix + ':' + mapping.get(key, key)
    return value


def resolve_plan(plan, references):
    # New IDs are local too. Resolve before persisting so later rounds never
    # inherit an ambiguous NEW_item or a reference to another round's E001.
    references = dict(references)
    for entity in plan.get('additions', []):
        if entity['id'].startswith('NEW_') or entity['id'] not in references and entity['id'] not in references.values():references[entity['id']] = uid()
    for lore in plan.get('newLore', []):
        if lore['id'].startswith('NEW_') or lore['id'] not in references and lore['id'] not in references.values():references[lore['id']] = uid()
    return replace_references(plan, references)


def travel_capability(plan, intent, base):
    """A navigation click authorizes walking, never trading or quest decisions."""
    if not intent or intent.get('kind') != 'travel':return plan
    result = deepcopy(plan)
    # Locations, actual obstacles and required walking costs still pass through
    # the normal route, deadline and finite-resource validators.
    rejected = bool(result.get('transfers') or result.get('resolvedUnresolved') or result.get('work') or result.get('transitionIds'))
    result['transitionIds'] = []
    result['blockedTransitions'] = []
    allowed_updates = [u for u in result.get('updates', []) if u['field'] == 'location']
    rejected |= len(allowed_updates) != len(result.get('updates', []))
    result['updates'] = allowed_updates
    result['transfers'], result['resolvedUnresolved'], result['work'] = [], [], []
    result['additions'] = [e for e in result.get('additions', []) if e['type'] == 'place' and e['id'] == result.get('location')]
    result['eventResolutions'] = []
    result['resources'] = [r for r in result.get('resources', []) if r['operation'] == 'spend' and r['entityId'] == base['playerId'] and r['field'] in ('stamina', 'health')]
    result['newLore'], result['learned'], result['critical'] = [], [], []
    result['events'] = [e for e in result.get('events', []) if e['type'] in ('arrival', 'obstacle', 'travel')]
    destination = intent.get('destinationId')
    if destination and not any(e['type'] == 'obstacle' for e in result['events']):
        # A declared exit is an explicit destination. A narrative hook is not
        # a physical obstruction. The normal route validator still checks
        # locks, adjacency, deadlines and walking costs atomically.
        if result.get('location') != destination:
            result['location'] = destination
            result['updates'] = [u for u in result['updates'] if u.get('entityId') != base['playerId']]
            result['title'] = '沿路前行'
            rejected = True
    if rejected:
        destination = result.get('location')
        result['publicOutcome'] = ('玩家抵达目的地。只描写当地环境与在场人物注意到来客的反应；没有交易、交付证据、接受委托或完成任务。' if destination and destination != base['location'] else '玩家尝试沿出口移动。根据已登记的阻碍说明停留的原因，没有交易、交物或完成任务。')
        result['summary'] = '沿路线抵达目的地' if destination and destination != base['location'] else '尝试沿路线移动'
        result['options'] = []
    return result
