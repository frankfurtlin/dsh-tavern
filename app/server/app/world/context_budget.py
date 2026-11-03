"""Bound expendable model context using the exact serialized request size.

The immutable checkpoint/archive is never shortened. Rules, inventory, current
attributes, knowledge, deadlines and the requested action are never discarded.
"""
from copy import deepcopy
from ..context_wire import wire_json


def fit_context(data, byte_budget):
    value = deepcopy(data)
    value['historyPolicy'] = 'Only state/ledger/events confirm occurrence. Omitted prose remains in the archive; do not invent prior actions, purchases or successes.'
    if 'state' in value:
        # These archival indexes are executor bookkeeping, not new decisions.
        for key in ('resolvedEvents', 'reachedEvents', 'checks', 'observed'):
            value['state'].pop(key, None)
        if value.get('criticalFacts') == value['state'].get('critical'):
            value['state'].pop('critical', None)
    # The director system prompt already contains these static JSON examples.
    # Keep dynamic attributes, identity and transaction constraints in full.
    contract = value.get('outputContract', {})
    for key in ('updates', 'transfers', 'resources'):
        section = contract.get(key)
        if not isinstance(section, dict):continue
        for repeated in ('required','fieldSyntax','operation','quantity','currencyId','fields'):
            section.pop(repeated, None)
    def size():return len(wire_json(value).encode())
    # Old prose and retrieval are optional. Their original records remain
    # addressable; current authoritative state always takes precedence.
    for key in ('candidateScenes', 'recentScenes', 'recalledEpisodes', 'episodes',
                'recalledEvents', 'conversation', 'unoccurredReference', 'source'):
        records = value.get(key)
        if not isinstance(records, list):continue
        while records and size() > byte_budget:
            records.pop(0)
    for key in ('recentText', 'oldText'):
        if size() > byte_budget and value.get(key):value[key] = ''
    # Consecutive chapter execution also carries this copy of the prior prose.
    # Its immutable original and summary remain available; it cannot displace
    # the current ledger, a pending decision or the requested action.
    last=value.get('lastCommittedTurn')
    if size() > byte_budget and isinstance(last,dict) and isinstance(last.get('text'),str):
        last['text']=''
    # Review calls wrap narrator context. Apply the same prose policy inside
    # that wrapper; otherwise an old conversation escapes the top-level bound.
    if size() > byte_budget and isinstance(value.get('scene'), dict):
        scene = value['scene']
        if any(key in scene for key in ('recentText','conversation','oldText')):
            scene['recentText']='';scene['oldText']='';scene['conversation']=[]
    return value
