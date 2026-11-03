"""Author clues are available to the director, never granted to the player."""
from copy import deepcopy


def enrich_script(data, definition, state, source):
    script = definition.get('script')
    if not script:return
    completed = set(state.get('plot', {}).get('completed', []))
    available = [o for o in script['objectives'] if o['id'] not in completed and set(o['requires']) <= completed]
    present = {k for k, v in state['entities'].items() if v.get('location') == state['location']}
    required = {c['loreId'] for o in available for c in o['checks'] if c['kind'] == 'knowledge'}
    unlocked = required | set(state.get('known', [])) | {
        c['loreId'] for o in script['objectives'] if o['id'] in completed
        for c in o['checks'] if c['kind'] == 'knowledge'}
    quest_clues = {c['loreId'] for o in script['objectives'] for c in o['checks'] if c['kind'] == 'knowledge'}
    local = [l for l in definition.get('lore', []) if l['id'] in required or
             (set(l.get('knownBy', [])) & present and (l['id'] not in quest_clues or l['id'] in unlocked))]
    # General context may already contain a nearby NPC's future secrets.
    data['lore'] = [l for l in data.get('lore', []) if l['id'] not in quest_clues or l['id'] in unlocked]
    data['lore'] = list({l['id']:deepcopy(l) for l in data.get('lore', []) + local}.values())
    data['scriptGuide'] = {
        'completed':sorted(completed), 'objectives':deepcopy(available),
        'endings':deepcopy([e for e in script['endings'] if set(e['requires']) <= completed]),
        'localClues':[{'loreId':l['id'], 'title':l['title'], 'text':l['text'],
                      'availableSources':sorted(set(l.get('knownBy', [])) & present),
                      'alreadyKnown':l['id'] in state.get('known', []),
                      'receiptWhenActuallyDisclosed':{'learned':[l['id']],
                          'events':[{'type':'knowledge_shared','entityId':who,'loreIds':[l['id']],
                              'description':l['text'],'visibleTo':['player'],'dueMinute':None,'requiresDecision':False}
                              for who in sorted(set(l.get('knownBy', [])) & present)]}} for l in local],
        'knowledgeProtocol':'When an actual present source explains a clue, record knowledge_shared with that source entityId, loreIds and visibleTo:[player], plus learned. Mere travel or asking an unanswered question grants nothing. Use the existing clue ID rather than creating a duplicate.',
    }
    from ..world.settlement import active_ending
    from ..world.settlement import protected_fields
    data['scriptGuide']['protectedFields']=[{'entityId':who,'field':field}
        for who,field in sorted(protected_fields(definition))]
    data['scriptGuide']['protectedFieldProtocol']='These fields may NEVER appear in updates, even before ending selection. Use ordinary location moves and actual knowledge receipts for investigation. Registered transitions alone own these fields. Do not invent intermediate statuses for them.'
    ending = active_ending(state, definition)
    if ending and ending.get('settlementSteps'):
        data['scriptGuide'].update(selectedEndingId=ending['id'],
            settlementSteps=deepcopy(ending['settlementSteps']),
            completedSteps=deepcopy(state['plot'].get('resolution', {}).get('completedSteps', [])),
            transitions=deepcopy(ending.get('transitions', [])),
            settlementProtocol='A choice only selects this ending. Perform only explicitly requested actions. For a requested registered operation include its exact ID in transitionIds, or include blockedTransitions:[{transitionId:ID,reason:actual obstacle}]. Omitting both is INVALID, even if prose says success. Inventory uses transfers; registered case/character consequences use transitionIds, never direct updates to protected fields or registered departures. Sharing a clue with an NPC requires knowledge_shared with the actual informed source and visibleTo:[player,entity:RECIPIENT_ID], loreIds plus recipient known update. Do not create new duplicate lore. All prerequisites, actual contact and elapsed time must hold. Plans/arrival are not handoff, custody or release. Never promise completed aftermath without receipts.')
    # Chinese actions cannot lexically retrieve an English original. Use the
    # verified evidence of the current scene/clue instead of the novel's ending.
    ids = {e['blockId'] for l in local for e in l.get('evidence', [])}
    ids.update(i for o in available for i in o.get('sourceBlockIds', []))
    data['source'] = [deepcopy(b) for b in source.get('blocks', []) if b['blockId'] in ids][:18]
