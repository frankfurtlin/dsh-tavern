"""Publication checks for reachable and auditable ending obligations."""
from ..errors import Problem


def validate_settlement(result, *, people, places, items, lore, blocks):
    def invalid(message):raise Problem('SCRIPT_SETTLEMENT_INVALID', message, 422)
    all_ids = set()
    for ending in result['endings']:
        steps, transitions = ending['settlementSteps'], ending['transitions']
        if not steps:
            if transitions:invalid('办理操作需要善后步骤')
            continue
        if not ending['selectionDescription']:invalid('请补充结局选择说明，不能提前显示完成文案')
        step_ids = {s['id'] for s in steps}
        ids = {t['id'] for t in transitions}
        if len(step_ids) != len(steps) or len(ids) != len(transitions) or ids & all_ids:invalid('善后步骤或办理 ID 重复')
        all_ids |= ids
        def check(c):
            if c['entityId'] not in people | {'player'}:invalid('善后条件引用了不存在的人物')
            if c['kind'] in ('visit','location') and c['placeId'] not in places:invalid('善后地点不存在')
            if c['kind'] in ('inventory','acquired','transfer') and c['itemId'] not in items:invalid('善后物品不存在')
            if c['kind'] == 'transfer' and c['toId'] not in people:invalid('善后接收人不存在')
            if c['kind'] == 'knowledge' and c['loreId'] not in lore:invalid('善后知识不存在')
            if c['kind'] == 'field' and (not c['field'] or c['value'] is None):invalid('善后状态条件不完整')
            if c['kind'] == 'transition' and c['value'] not in ids:invalid('善后办理回执不存在')
        finished = set()
        for _ in steps:
            for step in steps:
                if not set(step['requires']) <= step_ids:invalid('善后前置不存在')
                if set(step['requires']) <= finished:finished.add(step['id'])
        if finished != step_ids:invalid('善后步骤存在循环依赖')
        dependencies = {'step:'+s['id']:{'step:'+r for r in s['requires']} |
                        {'transition:'+c['value'] for c in s['checks'] if c['kind'] == 'transition'} for s in steps}
        dependencies.update({'transition:'+t['id']:{'step:'+r for r in t['requires']} |
                             {'transition:'+c['value'] for c in t['checks'] if c['kind'] == 'transition'} for t in transitions})
        satisfied = set()
        for _ in dependencies:
            satisfied |= {key for key, deps in dependencies.items() if deps <= satisfied}
        if len(satisfied) != len(dependencies):invalid('办理操作与善后条件存在循环依赖')
        for entry in steps + transitions:
            if not set(entry['sourceBlockIds']) <= blocks or entry['origin'] == 'source' and not entry['sourceBlockIds']:
                invalid('原作善后需有来源，新增过程请标记改编')
            for c in entry['checks']:check(c)
            for c in entry['checks']:
                if c['kind'] == 'inventory' and any(t['kind'] == 'transfer' and t['entityId'] == c['entityId'] and t['itemId'] == c['itemId'] for t in entry['checks']):
                    invalid('善后不能同时要求持有并交出，请使用曾获得与真实交付')
        for t in transitions:
            if t['authorityId'] not in people or t['placeId'] not in places or not set(t['requires']) <= step_ids:
                invalid('办理主体、地点或前置不存在')
            if not any(c['kind'] == 'transition' and c['value'] == t['id'] for s in steps for c in s['checks']):
                invalid('每个办理操作必须有对应的善后回执条件')
            for effect in t['effects']:
                if effect['entityId'] not in people:invalid('办理结果人物不存在')
                if effect['field'] == 'location':
                    if effect['value'] not in places:invalid('办理结果地点不存在')
                elif not effect['field'].startswith('field:') or not effect['field'][6:] or effect['field'][6:] in ('inventory','known','alive','location','playerId'):
                    invalid('办理只能改变已登记状态或地点，物品与知识使用原有账本')
    if len(result['conservedItemIds']) != len(set(result['conservedItemIds'])) or not set(result['conservedItemIds']) <= items:
        invalid('守恒物证不存在或重复')
    initial = result['definition']['initial']
    for item in result['conservedItemIds']:
        if sum(e.get('inventory', {}).get(item, 0) for e in initial.values()) != 1:
            invalid('唯一物证必须有一个初始持有人')
