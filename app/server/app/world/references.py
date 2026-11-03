"""Pure reference closure shared by preparation, startup and portable archives.

Diagnostics identify invalid references; they never delete, guess or repair them.
"""
from collections import Counter


def reference_problems(value):
    entities = {entry['id']: entry for entry in value['entities']}
    lore = {entry['id'] for entry in value.get('lore', [])}
    places = {key for key, entry in entities.items() if entry['type'] == 'place'}
    items = {key for key, entry in entities.items() if entry['type'] == 'item'}
    audiences = set(entities) | {'player', 'all'}
    problems = []

    def pointer(identifier):
        return identifier.replace('~', '~0').replace('/', '~1')

    def check(identifier, allowed, path, expected):
        if identifier not in allowed:
            problems.append({'path': path, 'identifier': identifier, 'expectedType': expected})

    check(value['location'], places, '/location', 'place')
    for index, entry in enumerate(value['entities']):
        for n, identifier in enumerate(entry.get('knownFacts', [])):
            check(identifier, lore, f'/entities/{index}/knownFacts/{n}', 'lore')
        for n, identifier in enumerate(entry.get('knownBy', [])):
            check(identifier, audiences, f'/entities/{index}/knownBy/{n}', 'entity-or-audience')
    for index, entry in enumerate(value.get('lore', [])):
        for n, identifier in enumerate(entry.get('entityIds', [])):
            check(identifier, entities, f'/lore/{index}/entityIds/{n}', 'entity')
        for n, identifier in enumerate(entry.get('knownBy', [])):
            check(identifier, audiences, f'/lore/{index}/knownBy/{n}', 'entity-or-audience')
    totals = Counter()
    for identifier, state in value.get('initial', {}).items():
        path = '/initial/' + pointer(identifier)
        check(identifier, entities, path, 'entity')
        if not isinstance(state, dict):
            problems.append({'path': path, 'identifier': identifier, 'expectedType': 'entity-state-object'})
            continue
        if state.get('location') is not None:
            check(state['location'], places, path + '/location', 'place')
        for n, known in enumerate(state.get('known', [])):
            check(known, lore, path + f'/known/{n}', 'lore')
        for related in state.get('relations', {}):
            check(related, entities, path + '/relations/' + pointer(related), 'entity')
        for item, count in state.get('inventory', {}).items():
            check(item, items, path + '/inventory/' + pointer(item), 'item')
            if type(count) is not int or count < 0:
                problems.append({'path': path + '/inventory/' + pointer(item), 'identifier': item, 'expectedType': 'nonnegative-integer'})
            else:
                totals[item] += count
    for item, total in totals.items():
        if item in items and not entities[item].get('stackable', False) and total > 1:
            problems.append({'path': '/initial', 'identifier': item, 'expectedType': 'unique-item-single-holder'})
    for origin, destinations in value.get('rules', {}).get('routes', {}).items():
        check(origin, places, '/rules/routes/' + pointer(origin), 'place')
        for n, destination in enumerate(destinations):
            check(destination, places, '/rules/routes/' + pointer(origin) + f'/{n}', 'place')
    return problems
