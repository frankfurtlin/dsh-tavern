"""Author-registered consequences; an ending choice never executes them."""
from copy import deepcopy
import re
from ..errors import Problem


def active_ending(state, definition):
    chosen = state.get('plot', {}).get('ending')
    return next((e for e in definition.get('script', {}).get('endings', []) if e['id'] == chosen), None)


def protected_fields(definition):
    return {(effect['entityId'], effect['field'])
            for ending in definition.get('script', {}).get('endings', [])
            for transition in ending.get('transitions', []) for effect in transition['effects']
            if effect['field'].startswith('field:')}


def guard_updates(base, definition, plan):
    protected = protected_fields(definition)
    selected = active_ending(base, definition)
    if not selected:
        selected = next((e for e in definition.get('script', {}).get('endings', [])
            if any(c['kind'] == 'field' and any(u['entityId'] == (base['playerId'] if c['entityId'] == 'player' else c['entityId'])
                and u['field'] == 'field:' + c['field'] and u['value'] == c['value'] for u in plan['updates'])
                for c in e['checks'])), None)
    choices = {(base['playerId'] if c['entityId'] == 'player' else c['entityId'], 'field:' + c['field'])
               for c in (selected or {}).get('checks', []) if c['kind'] == 'field'}
    for update in plan['updates']:
        pair = update['entityId'], update['field']
        if pair in protected:
            raise Problem('WORLD_SETTLEMENT_INVALID', '受保护字段“'+pair[1]+'”只能由登记办理改变；请删除该字段的普通更新，调查时只记录实际移动与知识渠道')
        if pair in choices and base['entities'][pair[0]]['fields'].get(pair[1][6:]) != update['value']:
            if base.get('plot', {}).get('ending'):
                raise Problem('WORLD_ENDING_LOCKED', '这条线已作出最终决定，请从历史节点另选')
        if any(effect['entityId'] == pair[0] and effect['field'] == pair[1] == 'location'
               and effect['value'] == update['value'] for t in (selected or {}).get('transitions', []) for effect in t['effects']):
            raise Problem('WORLD_SETTLEMENT_INVALID', '登记的离场需要实际办理回执，不能提前执行')


def apply_settlement(base, state, definition, plan, checkpoint_id, changes, events, *, action='', action_kind=None, autoplay=False):
    from ..scripts.plot import check_condition
    ids = plan.get('transitionIds', [])
    ending = active_ending(base, definition)
    blocked=plan.get('blockedTransitions',[])
    if not ids and not blocked and not ending:return
    # On the choice turn the base checkpoint deliberately has no ending yet.
    # A director may truthfully list the chosen ending's deferred operations.
    # Accept those explanations only after ALL choice conditions/prerequisites
    # match; they create no receipt/effect. Actual operations still require an
    # ending already selected at the preceding checkpoint below.
    deferred_ending=ending
    if not ending and blocked and not ids:
        eligible=[e for e in definition.get('script',{}).get('endings',[])
                  if set(e['requires'])<=set(base.get('plot',{}).get('completed',[]))
                  and all(check_condition(c,state,state.get('plot',{})) for c in e['checks'])]
        if len(eligible)==1:deferred_ending=eligible[0]
    transitions = {t['id']:t for t in (deferred_ending or {}).get('transitions', [])}
    blocked_ids=[entry['transitionId'] for entry in blocked]
    if len(blocked_ids)!=len(set(blocked_ids)) or set(blocked_ids)&set(ids) or not set(blocked_ids)<=transitions.keys():
        raise Problem('WORLD_SETTLEMENT_INVALID','受阻操作必须引用本结局的实际办理ID，不能同时宣称已办理')
    def explicit(t):
        return any(term in action and not re.search(r'(?:不|未|别|禁止|不要|没有|准备|打算|计划|如果|将来|未来)[^，。；\n]{0,12}' + re.escape(term), action)
                   for term in t['actionTerms'])
    if not autoplay and action_kind in ('action','dialogue'):
        existing_ids={r['transitionId'] for r in base.get('plot',{}).get('transitionReceipts',[])}
        for tid,t in transitions.items():
            if explicit(t) and tid not in existing_ids and tid not in ids and tid not in blocked_ids:
                raise Problem('WORLD_SETTLEMENT_INVALID','明确请求的登记办理 '+tid+' 必须列入transitionIds；若尚未办理，列入blockedTransitions并说明实际受阻原因，不能仅在正文说已完成')
    if not ids:return
    if not ending or not ending.get('settlementSteps') or len(ids) != len(set(ids)):
        raise Problem('WORLD_SETTLEMENT_INVALID', '善后需要已选择的结局和有效操作')
    player = state['playerId']
    completed = set(base.get('plot', {}).get('resolution', {}).get('completedSteps', []))
    existing = base.get('plot', {}).get('transitionReceipts', [])
    for tid in ids:
        t = transitions.get(tid)
        if not t or any(r['transitionId'] == tid for r in existing):
            raise Problem('WORLD_SETTLEMENT_INVALID', '办理操作不存在或已经完成')
        # Explicitly registered action scopes, plus real contact and prerequisites.
        # This gate supplements the director; it never extracts facts from prose.
        if (not autoplay and (action_kind not in ('action','dialogue') or not explicit(t))
                or not set(t['requires']) <= completed or plan['elapsedMinutes'] < t['minMinutes']
                or state['location'] != t['placeId']
                or state['entities'][t['authorityId']].get('location') != t['placeId']
                or not all(check_condition(c, state, state.get('plot', {})) for c in t['checks'])):
            raise Problem('WORLD_SETTLEMENT_INVALID', '办理尚缺明确行动、接收人、时间或前置证据')
        effects = []
        for effect in t['effects']:
            who, field, value = effect['entityId'], effect['field'], effect['value']
            target = state['entities'][who]
            if field == 'location':
                before = target.get('location');target['location'] = value
                state.setdefault('observed', {}).setdefault(who, {})['location'] = value
                if who == player:state['location'] = value
            else:
                key = field[6:];before = target['fields'].get(key);target['fields'][key] = value
                state.setdefault('observed', {}).setdefault(who, {})[field] = value
            change = {'entityId':who,'field':field,'before':before,'after':value,
                      'reason':t['title'],'visibleTo':[player]}
            changes.append(change);effects.append(deepcopy(change))
            state['discovered'] = sorted(set(state['discovered']) | {who})
        receipt = {'transitionId':tid,'checkpointId':checkpoint_id,'authorityId':t['authorityId'],
                   'placeId':t['placeId'],'origin':t.get('origin','adaptation'),'effects':effects}
        state.setdefault('plot', {}).setdefault('transitionReceipts', []).append(receipt)
        events.append({'id':checkpoint_id + ':transition:' + tid,'type':'script_transition',
                       'entityId':t['authorityId'],'description':t['title'],'visibleTo':[player],
                       'minute':state['minute'],'loreIds':[],'receipt':receipt})


def check_narrative(body,state,definition):
    """Author-declared completion phrases supplement semantic review.

    Never turns a sentence into a fact. A false completion stays a draft even
    when a semantic reviewer mistakenly returns consistent=true.
    """
    ending=active_ending(state,definition)
    if not ending:return
    plot=state.get('plot',{})
    for step in ending.get('settlementSteps',[]):
        if step['id'] in plot.get('resolution',{}).get('completedSteps',[]):continue
        for claim in step.get('completionClaims',[]):
            for match in re.finditer(re.escape(claim),body):
                prefix=re.split(r'[。！？；\n]',body[:match.start()])[-1][-16:]
                suffix=body[match.end():match.end()+6]
                if not re.search(r'不|未|尚|没有|准备|打算|将|等待|如果|假如|能否|是否|计划|未来|预计|才能|应该',prefix) and not re.match(r'[^。！？\n]{0,3}(?:吗|么|[?？])',suffix):
                    raise Problem('WORLD_NARRATIVE_CONFLICT','正文宣称的善后缺少实际完成回执，保留为草稿')
