"""Progress comes from committed state and receipts, never narrative claims."""
from copy import deepcopy


def check_condition(condition, state, plot):
    who = state['playerId'] if condition['entityId'] == 'player' else condition['entityId']
    entity = state['entities'].get(who, {})
    kind = condition['kind']
    if kind == 'visit':return condition['placeId'] in (plot.get('visited', {}).get(who, []) if plot.get('version') == 2 else state['discovered'])
    if kind == 'location':return entity.get('location') == condition['placeId']
    if kind == 'inventory':return entity.get('inventory', {}).get(condition['itemId'], 0) >= condition['quantity']
    if kind == 'acquired':return plot.get('acquired', {}).get(who, {}).get(condition['itemId'], 0) >= condition['quantity']
    if kind == 'knowledge':return condition['loreId'] in entity.get('known', [])
    if kind == 'field':return entity.get('fields', {}).get(condition['field']) == condition['value']
    if kind == 'transition':return any(r['transitionId'] == condition['value'] for r in plot.get('transitionReceipts', []))
    if kind == 'transfer':return any(t['fromId'] == who and t['toId'] == condition['toId'] and t['itemId'] == condition['itemId'] and t['quantity'] >= condition['quantity'] for t in plot.get('transfers', []))
    return False


def advance(state, definition, changes, checkpoint_id=None):
    script = definition.get('script')
    if not script:return
    plot = deepcopy(state.get('plot', {'completed':[], 'transfers':[], 'ending':None}))
    for key, default in (('completed',[]),('transfers',[]),('ending',None)):
        plot.setdefault(key, default)
    player = state['playerId']
    if any(e.get('settlementSteps') for e in script['endings']):
        plot['version'] = 2
        visited = plot.setdefault('visited', {})
        for who, entity in state['entities'].items():
            if entity.get('location'):
                visited[who] = sorted(set(visited.get(who, [])) | {entity['location']})
    # Acquisition is historical, while inventory means currently held. Keep
    # actual committed quantities so handing over a unique item cannot erase
    # evidence that it was obtained earlier.
    acquired=plot.setdefault('acquired', {})
    for who,entity in state['entities'].items():
        held=acquired.setdefault(who,{})
        for item,quantity in entity.get('inventory',{}).items():
            held[item]=max(held.get(item,0),quantity)
    for change in changes:
        if change.get('field') == 'inventory' and change.get('toId'):
            receipt = {k:change.get(k) for k in ('itemId','fromId','toId','quantity','visibleTo')}
            receipt.update(checkpointId=checkpoint_id, changeIndex=changes.index(change))
            plot['transfers'].append(receipt)
            who=change.get('toId')
            held=acquired.setdefault(who,{})
            held[change['itemId']]=max(held.get(change['itemId'],0),change['quantity'])
    def check(condition):
        return check_condition(condition, state, plot)
    # A pass is deliberately bounded by the objective DAG, including objectives
    # whose inventory evidence was acquired earlier and still exists now.
    for _ in range(len(script['objectives'])):
        changed = False
        for objective in script['objectives']:
            if objective['id'] in plot['completed'] or not set(objective['requires']) <= set(plot['completed']):continue
            if all(check(c) for c in objective['checks']):
                plot['completed'].append(objective['id']); changed = True
        if not changed:break
    appeared=plot.setdefault('appeared',[])
    for entry in script.get('castSchedule',[]):
        who=entry['entityId']
        if who not in appeared and set(entry['requires']) <= set(plot['completed']):
            before=state['entities'][who].get('location')
            state['entities'][who]['location']=entry['placeId'];appeared.append(who)
            if state['location']==entry['placeId']:
                state['discovered']=sorted(set(state['discovered'])|{who})
                changes.append({'entityId':who,'field':'location','before':before,'after':entry['placeId'],
                    'reason':'调查推进后，此人已抵达当前地点；请在本轮正文呈现其到场，不能仍写成等待未来到来。','visibleTo':[player]})
    if not plot['ending']:
        ending = next((e for e in script['endings'] if set(e['requires']) <= set(plot['completed']) and all(check(c) for c in e.get('checks',[]))), None)
        if ending:plot['ending'] = ending['id']
    ending = next((e for e in script['endings'] if e['id'] == plot['ending']), None)
    if ending and ending.get('settlementSteps'):
        resolution = plot.setdefault('resolution', {'endingId':ending['id'],'phase':'selected',
            'selectedAtCheckpointId':checkpoint_id,'completedSteps':[],'receiptRefs':[],'resolvedAtCheckpointId':None})
        if resolution['phase'] != 'resolved':
            for _ in ending['settlementSteps']:
                for step in ending['settlementSteps']:
                    if (step['id'] not in resolution['completedSteps']
                            and set(step['requires']) <= set(resolution['completedSteps']) and all(check(c) for c in step['checks'])):
                        resolution['completedSteps'].append(step['id'])
            resolution['receiptRefs'] = deepcopy(plot.get('transitionReceipts', []) + plot.get('transfers', []))
            relevant_items={c['itemId'] for step in ending['settlementSteps'] for c in step['checks']
                            if c['kind'] in ('inventory','acquired','transfer')}
            started=resolution['phase']=='resolving' or any(c.get('field')=='inventory' and c.get('itemId') in relevant_items
                for c in changes) and resolution['selectedAtCheckpointId']!=checkpoint_id
            resolution['phase'] = 'resolving' if resolution['completedSteps'] or started else 'selected'
            if len(resolution['completedSteps']) == len(ending['settlementSteps']):
                resolution.update(phase='resolved',resolvedAtCheckpointId=checkpoint_id)
    state['plot'] = plot


def public_progress(state, definition):
    script = definition.get('script')
    if not script:return None
    progress = state.get('plot', {})
    completed = set(progress.get('completed', []))
    available = [o for o in script['objectives'] if o['id'] not in completed and set(o['requires']) <= completed]
    ending = next((e for e in script['endings'] if e['id'] == progress.get('ending')), None)
    result = {'completedCount':len(completed), 'totalCount':len(script['objectives']),
            'objectives':[{'id':o['id'], 'title':o['title'], 'description':o['description'], 'completed':o['id'] in completed} for o in script['objectives'] if o['id'] in completed or o in available],
            'ending':None,'resolution':None}
    if ending:
        resolution = progress.get('resolution', {}) if ending.get('settlementSteps') else {}
        phase = resolution.get('phase', 'legacy_unverified')
        result['ending'] = {'id':ending['id'],'title':ending['title'],'phase':phase,
            'description':ending['description'] if phase == 'resolved' else ending.get('selectionDescription') or '已选择这个结局；后果尚未核验。'}
        steps = set(resolution.get('completedSteps', []))
        result['resolution'] = {'phase':phase,'completedCount':len(steps),'totalCount':len(ending.get('settlementSteps', [])),
            'resolvedAtCheckpointId':resolution.get('resolvedAtCheckpointId'),
            'steps':[{'id':s['id'],'title':s['title'],'description':s['description'],'origin':s.get('origin','adaptation'),
                      'completed':s['id'] in steps,'available':set(s['requires']) <= steps} for s in ending.get('settlementSteps', [])]}
    result['currentSummary'] = ('调查已完成。' if len(completed) == len(script['objectives']) else '调查正在进行。') + (
        ('已选择：' + ending['title'] + ('；善后已完成。' if result['ending']['phase'] == 'resolved' else '；请继续完成善后。')) if ending else '尚未选择结局。')
    registry = {e['id']:e for e in definition.get('entities', [])}
    result['confirmedHoldings'] = []
    for item in script.get('conservedItemIds', []):
        visible = [t for t in progress.get('transfers', []) if t['itemId'] == item
                   and set(t.get('visibleTo') or []) & {'player','all',state['playerId']}]
        if visible:
            t = visible[-1]
            result['confirmedHoldings'].append({'itemName':registry[item]['name'],
                'holderName':registry[t['toId']]['name'],'checkpointId':t.get('checkpointId'),
                'description':'最近确认交接至'+registry[t['toId']]['name']+'，后续未知变化不作推断。'})
    return result
