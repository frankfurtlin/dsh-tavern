"""Real world executor/ledger on an isolated DB; provider calls mocked only."""
import json
from copy import deepcopy
from unittest.mock import patch
import pytest
from app.db import SessionLocal
from app.models import Task
from app.world.models import WorldCheckpoint,WorldLine,World
from app.world.schemas import DirectorOutput
from app.world.state import checkpoint_hash
from app.world.worker import process
from app.scenes.service import scene_view
from app.scenes.interaction import normalize_plan, public_presence
from .test_scenes import scene_world

API='/api/world/v1'

@pytest.fixture(autouse=True)
def executable_scene_source(scene_world):
    with SessionLocal() as db:
        world=db.get(World,scene_world['world']);world.source={**world.source,'blocks':[],'chapters':[],'title':'独立测试','characterRefs':[]};db.commit()

def scene(client,w):
    with SessionLocal() as db:return scene_view(db.get(WorldCheckpoint,w['cp']))

def body(w,message,kind='action',**extra):
    return {'expectedCheckpointId':w['cp'],'message':message,'kind':kind,'turnId':message,**extra}

def provider(plan,reply):
    def call(task,stage,prompt,data,limit):
        if prompt=='world_director':return {'text':json.dumps(plan),'finishReason':'stop'}
        if prompt=='world_narrator':return {'text':reply,'finishReason':'stop'}
        return {'text':'{"consistent":true,"issues":[]}','finishReason':'stop'}
    return call

def test_absent_dead_and_narratively_departed_people_are_not_clickable(client,scene_world,profile):
    w=scene_world;target=scene(client,w)['characters'][0]
    for mode in ['remote','dead','departed']:
        with SessionLocal() as db:
            cp=db.get(WorldCheckpoint,w['cp']);state=deepcopy(cp.state)
            state['entities'][target['id']]['location']='elsewhere' if mode=='remote' else state['location']
            state['entities'][target['id']]['alive']=mode!='dead'
            cp.state=state;cp.text=target['name']+'已经离开了客舍。' if mode=='departed' else '你坐在客舍里。'
            cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None);db.commit()
        assert not scene(client,w)['characters']
        r=client.post(API+w['root']+'/scene/actions',json=body(w,'你好','dialogue',targetEntityId=target['id']),headers={'Idempotency-Key':'absent-'+mode})
        assert r.status_code==409 and r.json()['code']=='SCENE_TARGET_UNAVAILABLE'

def test_present_return_and_past_frames_do_not_hide_people(client,scene_world):
    e={'name':'陆遥'}
    assert not public_presence(e,'陆遥已经离开。')
    assert public_presence(e,'陆遥已经离开。陆遥回到客舍。')
    assert public_presence(e,'昨天陆遥已经离开。')
    assert not public_presence(e,'陆遥早已悄悄告辞，把帽子带走了。')
    assert public_presence(e,'陆遥准备告辞。')
    from app.scenes.layout import build_layout
    layout=build_layout({'id':'room','name':'雪关客舍','description':'窗外是山道。'},[],'窗边能看见山道。')
    assert not any(point['name']=='道路' for point in layout['nodes'])

def test_arriving_reveals_stationary_destination_npc_and_repairs_legacy_roster_on_read(client,scene_world):
    from types import SimpleNamespace
    from app.scenes.interaction import record_interaction
    from app.models import uid
    w=scene_world
    with SessionLocal() as db:
        parent=db.get(WorldCheckpoint,w['cp']);definition=deepcopy(parent.definition);base=deepcopy(parent.state)
        place={**deepcopy(next(e for e in definition['entities'] if e['type']=='place')),'id':'market','name':'坊市'}
        npc={**deepcopy(next(e for e in definition['entities'] if e['type']=='character')),'id':'merchant','name':'林掌柜'}
        definition['entities'] += [place,npc]
        base['entities']['market']={'alive':None,'location':None,'fields':{},'inventory':{},'relations':{},'known':[]}
        base['entities']['merchant']={**deepcopy(base['entities'][base['playerId']]),'location':'market','inventory':{}}
        parent.definition=definition;parent.state=base
        state=deepcopy(base);state['location']='market';state['entities'][state['playerId']]['location']='market'
        state['discovered']=list(set(state['discovered'])|{'market','merchant'})
        child=WorldCheckpoint(id=uid(),owner_id=parent.owner_id,world_id=parent.world_id,line_id=parent.line_id,parent_checkpoint_id=parent.id,
            sequence=parent.sequence+1,title='抵达坊市',text='林掌柜拨了两下算盘，报出药材价格。',action='前往坊市',state=state,definition=definition,
            options=[],changes=[],summary='',memory={'scenePresence':{'location':'market','playerId':state['playerId'],'absentCharacterIds':['merchant']}},
            content_hash=checkpoint_hash(state,definition,'',parent.id),kind='turn')
        db.add(child);db.flush()
        assert 'merchant' in {e['id'] for e in scene_view(child)['characters']}
        fork=WorldCheckpoint(id=uid(),owner_id=child.owner_id,world_id=child.world_id,line_id=child.line_id,parent_checkpoint_id=child.id,
            sequence=child.sequence,title='另一条故事线',text='',action='',state=deepcopy(child.state),definition=deepcopy(child.definition),
            options=[],changes=[],summary='',memory=deepcopy(child.memory),content_hash=checkpoint_hash(child.state,child.definition,'',child.id),kind='fork')
        db.add(fork);db.flush()
        assert 'merchant' in {e['id'] for e in scene_view(fork)['characters']}
        assert child.memory['scenePresence']['absentCharacterIds']==['merchant']
        record_interaction(child,parent,SimpleNamespace(payload={}),None)
        assert 'merchant' not in child.memory['scenePresence']['absentCharacterIds']
        child.text='林掌柜已经离开了。'
        assert 'merchant' not in {e['id'] for e in scene_view(child)['characters']}
        assert 'merchant' not in {e['id'] for e in scene_view(fork)['characters']}


def test_director_receives_explicit_item_and_resource_contract(client,scene_world):
    from types import SimpleNamespace
    from app.scenes.interaction import enrich_director
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,scene_world['cp']);data=enrich_director({},SimpleNamespace(payload={}),cp)
        assert 'quantity' in data['outputContract']['transfers']['required']
        assert 'amount' not in data['outputContract']['transfers']['required']
        assert 'counterpartyId' in data['outputContract']['resources']['transfer']
        assert 'numericResourceAssignments' in data['outputContract']['updates']


def test_registered_custom_attribute_alias_preserves_resource_guards():
    state={'playerId':'p','entities':{'p':{'fields':{'任务进度':'待办','金币':300}}}}
    definition={'rules':{'actionCosts':{'action':{'金币':0}}},'entities':[],'lore':[]}
    updates=[{'entityId':'p','field':field,'value':value,'reason':'当面交易','visibleTo':['player']}
             for field,value in [('任务进度','备齐材料'),('金币',60),('未知属性','凭空新增')]]
    normalized=normalize_plan({'updates':updates},state,definition)
    assert [u['field'] for u in normalized['updates']]==['field:任务进度','金币','未知属性']
    assert normalized['updates'][0]['value']=='备齐材料'
    assert updates[0]['field']=='任务进度'


def test_visual_channel_alias_has_a_finite_character_only_contract():
    definition={'rules':{},'entities':[{'id':'spirit','type':'character'},{'id':'ring','type':'item'}]}
    base={'playerId':'p','entities':{'spirit':{'fields':{}},'ring':{'fields':{}}}}
    raw=[{'entityId':who,'field':'presentation','value':value,'reason':'传音'} for who,value in [('spirit','voice'),('spirit','visible'),('spirit','invalid'),('ring','voice')]]
    result=normalize_plan({'updates':raw},base,definition)
    assert [u['field'] for u in result['updates']]==['field:presentation','field:presentation','presentation','presentation']


def test_local_walk_does_not_resurrect_a_departed_npc(client,scene_world,profile):
    w=scene_world;target=scene(client,w)['characters'][0]
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);cp.text='条凳旁有火炉。'+target['name']+'已经离开了客舍。';cp.memory={'scenePresence':{'location':cp.state['location'],'playerId':cp.state['playerId'],'absentCharacterIds':[]}};cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None);db.commit()
    assert not scene(client,w)['characters']
    response=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'point','targetId':w['bench']},headers={'Idempotency-Key':'walk-after-departure'})
    assert response.status_code==200,response.text
    w['cp']=response.json()['checkpointId'];assert not scene(client,w)['characters']
    r=client.post(API+w['root']+'/scene/actions',json=body(w,'你好','dialogue',targetEntityId=target['id']),headers={'Idempotency-Key':'no-resurrection'})
    assert r.status_code==409

def test_direct_chat_commits_and_survives_refresh_with_partner_scope(client,scene_world,profile):
    w=scene_world;target=scene(client,w)['characters'][0]
    r=client.post(API+w['root']+'/scene/actions',json=body(w,'我们一起走，好吗？','dialogue',targetEntityId=target['id']),headers={'Idempotency-Key':'chat'})
    assert r.status_code==202,r.text
    plan=DirectorOutput(title='结伴',elapsedMinutes=2,timeLabel='清晨',publicOutcome='对方答应同行。').model_dump()
    with patch('app.world.worker.call_provider',side_effect=provider(plan,'好，我们一起走。')) as calls:
        process(r.json()['taskId'])
    task=client.get(API+'/tasks/'+r.json()['taskId']).json();assert task['state']=='succeeded',task
    assert task['result']['interactionMode']=='dialogue'
    assert calls.call_args_list[0].args[3]['outputMode']=='dialogue'
    assert calls.call_args_list[1].args[3]['outputMode']=='dialogue'
    w['cp']=client.get(API+w['root']).json()['headCheckpointId']
    conversation=scene(client,w)['conversations'];assert len(conversation)==1
    assert conversation[0]['user']=='我们一起走，好吗？' and conversation[0]['reply']=='好，我们一起走。'
    assert conversation[0]['partnerId']==target['id']
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);state=deepcopy(cp.state);state['playerId']=target['id'];cp.state=state;db.commit()
    assert not scene(client,w)['conversations']

def test_door_exploration_is_idempotent_world_turn_with_return_route(client,scene_world,profile):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);cp.text+='你看见通往门外的房门。';cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None);db.commit()
    exit=next(e for e in scene(client,w)['exits'] if e['kind']=='exit' and e['name']=='探索门外')
    data=body(w,exit['direction'],sceneExitId=exit['id'])
    r=client.post(API+w['root']+'/scene/travel',json=data,headers={'Idempotency-Key':'exit'})
    assert r.status_code==202,r.text
    assert client.post(API+w['root']+'/scene/travel',json=data,headers={'Idempotency-Key':'exit'}).json()==r.json()
    origin=scene(client,w)['descriptor']['placeId']
    plan=DirectorOutput(title='门外',elapsedMinutes=3,timeLabel='清晨',publicOutcome='你穿过房门来到庭院。',location='new-garden',additions=[{'id':'new-garden','name':'雪中庭院','type':'place','description':'门旁的小路通向关口。','source':'game'}]).model_dump()
    with patch('app.world.worker.call_provider',side_effect=provider(plan,'你穿过房门来到雪中庭院。')) as calls:process(r.json()['taskId'])
    task=client.get(API+'/tasks/'+r.json()['taskId']).json();assert task['state']=='succeeded',task
    sent=calls.call_args_list[0].args[3]
    assert sent['sceneIntent']['origin']==sent['state']['location']
    assert sent['sceneIntent']['origin'] in {e['id'] for e in sent['registry'] if e['type']=='place'}
    head=client.get(API+w['root']).json()['head'];assert head['state']['minute']==3
    assert next(e for e in head['state']['entities'] if e['id']==head['state']['location'])['name']=='雪中庭院'
    w['cp']=head['id'];view=scene(client,w)
    assert not view['characters'];assert any(e['id']==origin for e in view['exits'])
    assert any(e['name']=='沿路继续探索' for e in view['exits'])
    assert not any(e['id']==exit['id'] for e in view['exits'])

def test_locked_door_remains_blocked_and_has_actionable_options(client,scene_world,profile):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);cp.text+='房门锁着。';cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None);db.commit()
    exit=next(e for e in scene(client,w)['exits'] if e['name']=='探索门外')
    r=client.post(API+w['root']+'/scene/travel',json=body(w,'试着推开房门',sceneExitId=exit['id']),headers={'Idempotency-Key':'locked'})
    origin=scene(client,w)['descriptor']['placeId']
    plan=DirectorOutput(title='门锁',elapsedMinutes=1,timeLabel='清晨',publicOutcome='门锁着，暂时无法通过。',options=[{'text':'询问掌柜钥匙在哪','kind':'dialogue'}]).model_dump()
    with patch('app.world.worker.call_provider',side_effect=provider(plan,'房门锁着，你需要先找到钥匙。')):process(r.json()['taskId'])
    assert client.get(API+'/tasks/'+r.json()['taskId']).json()['state']=='succeeded'
    head=client.get(API+w['root']).json()['head'];assert head['state']['location']==origin
    assert head['options'][0]['text']=='询问掌柜钥匙在哪'
    with SessionLocal() as db:assert not db.get(WorldCheckpoint,head['id']).memory.get('sceneConnections')

def test_misfiled_obligation_is_not_granted_as_knowledge_or_silently_resolved(client,scene_world):
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,scene_world['cp']);state=deepcopy(cp.state);state['unresolved']=[{'id':'initial-0','text':'同行约定'}]
        result=normalize_plan({'learned':['initial-0','unknown-secret'],'resolvedUnresolved':[]},state,cp.definition)
        assert result['learned']==['unknown-secret'];assert result['resolvedUnresolved']==[]
        assert state['unresolved'][0]['id']=='initial-0'

def test_real_mimo_viewpoint_category_error_does_not_block_companion_travel(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);state=cp.state;npc=scene_view(cp)['characters'][0]['id'];player=state['playerId']
        plan={'learned':[],'updates':[{'entityId':npc,'field':'location','value':'garden','reason':'同行'},
            {'entityId':npc,'field':'场景观察点','value':'entry','reason':'同行'},
            {'entityId':player,'field':'场景地点','value':'garden','reason':'出门'},
            {'entityId':npc,'field':'unsupported-secret','value':True,'reason':'无效'}]}
        normalized=normalize_plan(plan,state,cp.definition)
        assert normalized['updates']==[plan['updates'][0],{**plan['updates'][2],'field':'field:场景地点'},plan['updates'][3]]
        assert len(plan['updates'])==4

def test_stale_forged_and_explicitly_restricted_exits_cannot_queue(client,scene_world,profile):
    w=scene_world
    r=client.post(API+w['root']+'/scene/travel',json=body(w,'出去',sceneExitId='exit:forged'),headers={'Idempotency-Key':'forged'})
    assert r.status_code==409
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);definition=deepcopy(cp.definition);definition['rules']['routes']={cp.state['location']:[]};cp.definition=definition;cp.text+='房门敞开。';cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None);db.commit()
    assert not scene(client,w)['exits']
    assert client.get(API+w['root']).json()['pendingTaskId'] is None
