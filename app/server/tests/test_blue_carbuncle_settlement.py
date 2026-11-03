"""Pure ledger tests. Investigation fixtures are NOT real playthrough evidence."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.errors import Problem
from app.scripts.service import validate, blocks_for
from app.scripts.plot import advance, public_progress
from app.world.schemas import DirectorOutput, WorldRules
from app.world.state import initial_state, apply_plan, public_view, checkpoint_hash
from app.world.turn_contract import travel_capability

DIRECTORY = Path(__file__).parents[1]/'app/scripts/samples'


def sample():
    raw = json.loads((DIRECTORY/'blue-carbuncle-script.json').read_text('utf-8'))
    blocks = blocks_for((DIRECTORY/'blue-carbuncle.txt').read_text('utf-8'))
    world = SimpleNamespace(source={'blocks':blocks},definition={'rules':WorldRules(genre='mystery').model_dump()})
    value, definition = validate(raw, world, publication=True)
    definition, state = initial_state(definition, 'watson')
    return value, definition, state, blocks


def test_restored_script_and_plot_remap_all_fictional_references():
    from app.portable.world_ids import remap_world_local_ids
    ending,definition,state,_=choose('end_release')
    state['plot']['visited']={'watson':['baker_st']}
    state['plot']['transfers']=[{'fromId':'watson','toId':'countess','itemId':'carbuncle','quantity':1,'visibleTo':['player']}]
    state['plot']['transitionReceipts']=[{'transitionId':'end_release_release','authorityId':'holmes','placeId':'baker_st',
        'effects':[{'entityId':'ryder','field':'location','after':'street_london'}]}]
    original=deepcopy(definition)
    world=SimpleNamespace(definition=deepcopy(definition))
    cp=SimpleNamespace(world_id='new-world',definition=definition,state=state,changes=[],
        memory={'sceneTranscript':{'checkpointId':'cp','segments':[{'kind':'speech','speakerId':'ryder','text':'谢谢。'}]}})
    mapping={('world','old-world'):'new-world'}
    remap_world_local_ids({'world':{'new-world':world},'checkpoint':{'cp':cp}},mapping)
    entity=lambda key:mapping['world_entity','old-world:'+key]
    assert cp.definition['script']['conservedItemIds']==[entity('carbuncle')]
    assert cp.state['plot']['visited']=={entity('watson'):[entity('baker_st')]}
    assert cp.state['plot']['transfers'][0]['toId']==entity('countess')
    assert cp.state['plot']['transitionReceipts'][0]['effects'][0]['after']==entity('street_london')
    assert cp.state['plot']['transitionReceipts'][0]['authorityId']==entity('holmes')
    assert cp.memory['sceneTranscript']['segments'][0]['speakerId']==entity('ryder')
    for o in cp.definition['script']['objectives']:
        for c in o['checks']:
            if c['kind']=='knowledge':assert c['loreId'] in {l['id'] for l in cp.definition['lore']}
    release=cp.definition['script']['endings'][0]['transitions'][0]
    for t in cp.definition['script']['endings'][0]['transitions']:
        assert t['authorityId'] in cp.state['entities']
        for effect in t['effects']:
            if effect['field']=='location':assert effect['value'] in cp.state['entities']
    assert original['script']['conservedItemIds']==['carbuncle']
    assert public_progress(cp.state,cp.definition)['ending']['phase']=='selected'


def investigation_fixture():
    value, definition, state, blocks = sample()
    # Explicit unit-test precondition. Real acceptance must obtain these facts
    # through actual API actions and committed checkpoints.
    state['known'] = [l['id'] for l in definition['lore']]
    state['entities']['watson']['known'] = list(state['known'])
    state['entities']['ryder']['location'] = 'baker_st'
    state['plot'] = {'completed':[o['id'] for o in value['objectives']], 'transfers':[],
                     'ending':None,'appeared':[e['id'] for e in definition['entities']]}
    return value, definition, state, blocks


def plan(**kw):
    return DirectorOutput(title='验证行动',elapsedMinutes=5,timeLabel='圣诞案调查后',publicOutcome='本次实际行动。',**kw).model_dump()


def update(who,field,value):return {'entityId':who,'field':field,'value':value,'reason':'明确行动','visibleTo':['player']}


def transfer(sender,recipient):return {'itemId':'carbuncle','fromId':sender,'toId':recipient,'quantity':1,'reason':'当面交接','visibleTo':['player']}


def perform(state, definition, checkpoint, action='明确办理', **kw):
    minutes = kw.pop('minutes',5)
    raw = plan(**kw);raw['elapsedMinutes'] = minutes
    return apply_plan(state,definition,raw,checkpoint,action_kind='action',action=action)


def choose(ending_id):
    value,d,s,b = investigation_fixture()
    e = next(e for e in value['endings'] if e['id'] == ending_id)
    decision = next(c['value'] for c in e['checks'] if c['kind'] == 'field')
    s,d,_,_ = perform(s,d,'choice',action='我选择'+decision,updates=[update('watson','field:最终决定',decision)])
    return e,d,s,b


def test_requested_release_requires_receipt_or_explicit_blocked_result():
    e,d,s,_=choose('end_release')
    with pytest.raises(Problem,match='必须列入transitionIds'):
        perform(s,d,'missing',action='办理释放赖德')
    changed,_,changes,_=perform(s,d,'blocked',action='办理释放赖德',
        blockedTransitions=[{'transitionId':'end_release_release','reason':'尚未取得实际同意'}])
    assert not changes and changed['entities']['ryder']['fields']['处置状态']=='尚未处理'
    assert changed['plot']['resolution']['phase']=='selected'
    with pytest.raises(Problem):
        perform(s,d,'contradiction',action='办理释放赖德',transitionIds=['end_release_release'],
            blockedTransitions=[{'transitionId':'end_release_release','reason':'尚未取得实际同意'}])


def test_choice_turn_can_describe_deferred_operations_without_executing_any():
    _,definition,state,_=investigation_fixture()
    after,_,_,_=perform(state,definition,'choice-deferred',action='我只决定释放赖德，本轮不让他离开，不改变霍纳的状态。',
        updates=[update('watson','field:最终决定','释放赖德')],
        blockedTransitions=[{'transitionId':'end_release_release','reason':'本轮只登记选择，暂不办理。'},
                            {'transitionId':'end_release_dismiss','reason':'尚未提交证词，暂不办理。'}])
    assert after['plot']['ending']=='end_release' and after['plot']['resolution']['phase']=='selected'
    assert not after['plot'].get('transitionReceipts')
    assert after['entities']['ryder']==state['entities']['ryder']
    assert after['entities']['horner']==state['entities']['horner']
    with pytest.raises(Problem):
        perform(state,definition,'wrong-ending',action='我只决定释放赖德。',updates=[update('watson','field:最终决定','释放赖德')],
                blockedTransitions=[{'transitionId':'end_police_arrest','reason':'不相关的操作。'}])


def test_actual_acquisition_starts_aftermath_without_completing_return():
    _,d,s,_=choose('end_release')
    s,d,_,_=perform(s,d,'take',transfers=[transfer('peterson','watson')])
    assert s['plot']['resolution']['phase']=='resolving'
    assert s['plot']['resolution']['completedSteps']==[]
    s,d,_,_=perform(s,d,'observe')
    assert s['plot']['resolution']['phase']=='resolving'


def test_visible_registered_departure_authorizes_this_round_dialogue_only():
    from app.world.memory import narrator_context
    from app.scenes.dialogue import structured_segments
    _,d,before,_=choose('end_release')
    after,d,_,_=perform(before,d,'release',action='办理释放赖德',transitionIds=['end_release_release'])
    parent=SimpleNamespace(state=before,text='之前的故事')
    data=narrator_context(parent,after,d,{'title':'释放','publicOutcome':'赖德获准离去。'},'办理释放赖德')
    assert 'ryder' not in {p['id'] for p in data['actors']['presentNPCs']}
    assert [p['id'] for p in data['actors']['roundParticipants']]==['ryder']
    people=data['actors']['presentNPCs']+data['actors']['roundParticipants']
    assert structured_segments('{"kind":"speech","speakerId":"ryder","text":"谢谢。"}',people)[0]['speakerId']=='ryder'
    later=narrator_context(SimpleNamespace(state=after,text='释放完成'),after,d,{'title':'观察','publicOutcome':'当前处境。'},'观察')
    assert later['actors']['roundParticipants']==[]
    with pytest.raises(Problem):structured_segments('{"kind":"speech","speakerId":"horner","text":"谢谢。"}',people)


@pytest.mark.parametrize('text',['霍纳已获释。','宝石已经归还伯爵夫人。','赖德获准离去，人已离开寓所。'])
def test_unreceipted_completion_is_rejected_without_trusting_a_reviewer(text):
    from app.world.settlement import check_narrative
    _,d,s,_=choose('end_release')
    with pytest.raises(Problem) as error:check_narrative(text,s,d)
    assert error.value.code=='WORLD_NARRATIVE_CONFLICT'


@pytest.mark.parametrize('text',['霍纳尚未获释。','准备让赖德获准离去。','霍纳已获释吗？','宝石尚未归还伯爵夫人。'])
def test_questions_and_plans_are_not_completion_claims(text):
    from app.world.settlement import check_narrative
    _,d,s,_=choose('end_release')
    check_narrative(text,s,d)


@pytest.mark.parametrize('ending_id',['end_release','end_police','end_return'])
def test_choice_never_means_aftermath_or_inventory_change(ending_id):
    e,d,s,_ = choose(ending_id)
    progress = public_progress(s,d)
    assert s['entities']['peterson']['inventory']['carbuncle'] == 1
    assert s['entities']['horner']['fields']['羁押状态'] == '在押候审'
    assert progress['ending']['phase'] == 'selected'
    assert progress['resolution']['completedCount'] == 0
    assert progress['ending']['description'] != e['description']
    assert 'checks' not in progress['ending'] and 'transitions' not in json.dumps(progress)
    assert not s['ended']


@pytest.mark.parametrize('ending_id',['end_release','end_police','end_return'])
def test_three_endings_require_real_receipts_and_matching_current_state(ending_id):
    e,d,s,_ = choose(ending_id)
    before = deepcopy(s)
    s,d,_,_ = perform(s,d,'take',transfers=[transfer('peterson','watson')])
    s,d,_,_ = perform(s,d,'hotel',location='cosmopolitan')
    assert 'return' not in s['plot']['resolution']['completedSteps']
    s,d,_,_ = perform(s,d,'return',transfers=[transfer('watson','countess')])
    assert public_progress(s,d)['ending']['phase'] == 'resolving'
    if ending_id != 'end_police':
        s,d,_,_ = perform(s,d,'back',location='baker_st')
        s,d,_,events = perform(s,d,'release',action='我明确让赖德离开',transitionIds=[e['transitions'][0]['id']])
        assert events[-1]['type'] == 'script_transition'
        assert s['entities']['ryder']['location'] == 'street_london'
    s,d,_,_ = perform(s,d,'station',location='police_station',updates=[update('ryder','location','police_station')] if ending_id == 'end_police' else [])
    s,d,_,_ = perform(s,d,'evidence',updates=[update('inspector','known',['ryder_confession'])],
        events=[{'type':'knowledge_shared','entityId':'watson','description':'向接收警官当面说明供词',
                 'visibleTo':['player','entity:inspector'],'loreIds':['ryder_confession']}])
    assert s['entities']['horner']['fields']['羁押状态'] == '在押候审'
    if ending_id == 'end_police':
        s,d,_,_ = perform(s,d,'custody',action='我明确移交赖德',transitionIds=[e['transitions'][0]['id']])
        assert s['entities']['ryder']['fields']['处置状态'] == '已移交警方'
    s,d,_,_ = perform(s,d,'dismiss',action='我申请撤案并办理更正，解除羁押',minutes=30,transitionIds=[e['transitions'][1]['id']])
    assert s['plot']['resolution']['phase'] == 'resolved'
    assert s['plot']['resolution']['resolvedAtCheckpointId'] == 'dismiss'
    assert len(s['plot']['resolution']['completedSteps']) == 4
    assert s['entities']['countess']['inventory']['carbuncle'] == 1
    assert s['entities']['watson']['inventory']['carbuncle'] == 0
    assert s['entities']['horner']['fields'] == {'案件状态':'已撤销指控','羁押状态':'已解除羁押'}
    assert sum(p['inventory'].get('carbuncle',0) for p in s['entities'].values()) == 1
    assert public_progress(s,d)['ending']['description'] == e['description']
    view = public_view(s,d)
    assert next(p for p in view['entities'] if p['id'] == 'horner')['state']['fields']['羁押状态'] == '已解除羁押'
    assert public_progress(before,d)['ending']['phase'] == 'selected'
    assert s['plot']['resolution']['receiptRefs']
    restored = json.loads(json.dumps(s));assert checkpoint_hash(restored,d,'正文','parent') == checkpoint_hash(s,d,'正文','parent')
    with pytest.raises(Problem):perform(s,d,'duplicate',action='申请撤案',minutes=30,transitionIds=[e['transitions'][1]['id']])


@pytest.mark.parametrize('action',['我准备归还宝石','请安排交给警方','只向福尔摩斯说明决定','我只观察，不释放赖德',
                                  '我准备让赖德离开','我打算办理释放赖德','如果释放赖德'])
def test_plans_or_observation_cannot_execute_release(action):
    e,d,s,_ = choose('end_release')
    with pytest.raises(Problem):perform(s,d,'bad',action=action,transitionIds=[e['transitions'][0]['id']])


@pytest.mark.parametrize('ending_id',['end_release','end_police','end_return'])
def test_early_choice_cannot_skip_investigation_or_execute_settlement(ending_id):
    value,d,s,_=sample()
    ending=next(e for e in value['endings'] if e['id']==ending_id)
    decision=next(c['value'] for c in ending['checks'] if c['kind']=='field')
    after,d,_,_=perform(s,d,'premature',updates=[update('watson','field:最终决定',decision)])
    assert after['plot']['ending'] is None and public_progress(after,d)['ending'] is None
    assert 'ryder_confession' not in after['known']
    assert 'ryder_confession' not in json.dumps(public_view(after,d))
    with pytest.raises(Problem):perform(after,d,'fake',action='释放赖德或移交赖德',transitionIds=[ending['transitions'][0]['id']])
    assert after['entities']['horner']['fields']['羁押状态']=='在押候审'


def test_horner_cannot_be_dismissed_without_disposition_evidence_and_time():
    _,d,s,_=choose('end_police')
    s,d,_,_=perform(s,d,'station',location='police_station',updates=[update('ryder','location','police_station')])
    with pytest.raises(Problem):perform(s,d,'unfounded',action='申请撤案',minutes=30,transitionIds=['end_police_dismiss'])
    s,d,_,_=perform(s,d,'evidence',updates=[update('inspector','known',['ryder_confession'])],
        events=[{'type':'knowledge_shared','entityId':'watson','description':'向警官说明供词',
                 'visibleTo':['player','entity:inspector'],'loreIds':['ryder_confession']}])
    with pytest.raises(Problem):perform(s,d,'missing-custody',action='申请撤案',minutes=30,transitionIds=['end_police_dismiss'])
    s,d,_,_=perform(s,d,'custody',action='移交赖德',transitionIds=['end_police_custody'])
    with pytest.raises(Problem):perform(s,d,'too-fast',action='申请撤案',minutes=1,transitionIds=['end_police_dismiss'])
    assert s['entities']['horner']['fields']=={'案件状态':'被控盗窃','羁押状态':'在押候审'}


def test_protected_case_fields_and_ending_choice_cannot_be_forged():
    e,d,s,_ = choose('end_release')
    for correction in [False,True]:
        with pytest.raises(Problem):apply_plan(s,d,plan(updates=[update('horner','field:羁押状态','已解除羁押')]),'fake',correction=correction)
    with pytest.raises(Problem):perform(s,d,'other',updates=[update('watson','field:最终决定','交给警方')])
    assert public_progress(s,d)['ending']['phase'] == 'selected'


@pytest.mark.parametrize('bad',[transfer('watson','countess'),transfer('peterson','countess'),transfer('peterson',None)])
def test_unheld_remote_or_destroyed_gem_is_rejected_atomically(bad):
    _,d,s,_ = choose('end_return');before=deepcopy(s)
    with pytest.raises(Problem):perform(s,d,'bad',transfers=[bad])
    assert s == before


def test_same_handoff_two_branches_and_rewrite_preserve_each_ledger():
    _,d,s,_ = choose('end_return')
    s,d,_,_ = perform(s,d,'take',transfers=[transfer('peterson','watson')])
    s,d,_,_ = perform(s,d,'hotel',location='cosmopolitan')
    a,da,_,_ = perform(s,d,'branch-a',transfers=[transfer('watson','countess')])
    b,db,_,_ = perform(s,d,'branch-b',transfers=[transfer('watson','countess')])
    for state,definition,cp in [(a,da,'branch-a'),(b,db,'branch-b')]:
        assert state['entities']['watson']['inventory']['carbuncle'] == 0
        assert state['entities']['countess']['inventory']['carbuncle'] == 1
        assert state['plot']['transfers'][-1]['checkpointId'] == cp
        assert public_progress(json.loads(json.dumps(state)),definition)['resolution']['completedCount'] == 1
    rewritten = deepcopy(a)
    assert rewritten == a and rewritten['plot']['transfers'][-1]['checkpointId'] == 'branch-a'
    assert s['entities']['watson']['inventory']['carbuncle'] == 1


def test_legacy_ending_is_unverified_without_mutating_history():
    _,d,s,_ = investigation_fixture()
    for e in d['script']['endings']:e.pop('settlementSteps');e.pop('transitions')
    s['plot']['ending']='end_release';before=deepcopy(s)
    p=public_progress(s,d)
    assert p['ending']['phase'] == 'legacy_unverified'
    assert '宝石归还伯爵夫人' not in p['ending']['description']
    assert s == before


def test_navigation_cannot_execute_any_settlement_transition():
    _,d,s,_ = choose('end_release')
    result=travel_capability(plan(transitionIds=['end_release_release']),{'kind':'travel','destinationId':'street_london'},s)
    assert not result['transitionIds']


@pytest.mark.parametrize('mutation',['recipient','cycle','source','effect','conservation'])
def test_publication_rejects_invalid_aftermath(mutation):
    v,_,_,blocks=sample()
    if mutation=='recipient':v['endings'][0]['settlementSteps'][0]['checks'][0]['toId']='missing'
    if mutation=='cycle':v['endings'][0]['settlementSteps'][0]['requires']=['return']
    if mutation=='source':v['endings'][0]['settlementSteps'][0]['origin']='source'
    if mutation=='effect':v['endings'][0]['transitions'][0]['effects'][0]['field']='inventory:carbuncle'
    if mutation=='conservation':v['definition']['initial']['peterson']['inventory']['carbuncle']=0
    w=SimpleNamespace(source={'blocks':blocks},definition={'rules':WorldRules().model_dump()})
    with pytest.raises(Problem):validate(v,w,publication=True)
