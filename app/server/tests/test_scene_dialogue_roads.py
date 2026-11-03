"""Dialogue channels and usable paths; production executor, isolated test DB."""
from copy import deepcopy
from unittest.mock import patch
from math import pi
import pytest
from app.scenes.dialogue import dialogue_segments
from app.scenes.interaction import normalize_plan
from app.scenes.layout import build_layout
from app.scenes.service import scene_view
from app.world.models import WorldCheckpoint
from app.world.schemas import DirectorOutput, EntityDefinition, EntityInitial
from app.world.state import checkpoint_hash
from app.world.worker import process
from app.db import SessionLocal
from .test_scenes import scene_world
from .test_scene_interaction import executable_scene_source, provider, scene, body, API


def test_named_chat_channel_and_missing_narration_boundary():
    parts=dialogue_segments('【台词：药老】材料既然齐了，就摆出来吧。\n\n他侧过身，目光在你包袱上一扫。\n\n【台词：药老】今天这第一炉，我亲自教你控火。\n\n松风掠过石台。','药老')
    assert [p['kind'] for p in parts]==['speech','narration','speech','narration']
    assert '侧过身' not in ''.join(p['text'] for p in parts if p['kind']=='speech')
    parts=dialogue_segments('【台词：药老】手别抖。\n\n噼啪声连成一片，白雾由清甜转为微苦。药老伸手掀开鼎盖。','药老')
    assert parts==[{'kind':'speech','text':'手别抖。'},{'kind':'narration','text':'噼啪声连成一片，白雾由清甜转为微苦。药老伸手掀开鼎盖。'}]


def test_silent_prose_is_narration_and_legacy_speech_is_separated():
    silent='雾气贴着水面。林渡正在整理船缆。林渡停了半拍，没有说话。'
    assert dialogue_segments(silent,'林渡')==[{'kind':'narration','text':silent}]
    assert dialogue_segments('林渡说：“你好。”他收起写着“夜渡”的木牌。','林渡')==[
        {'kind':'narration','text':'林渡说：'},{'kind':'speech','text':'你好。'},{'kind':'narration','text':'他收起写着“夜渡”的木牌。'}]


def test_received_mixed_speech_repairs_embedded_stage_directions_without_rewriting_history():
    from app.scenes.dialogue import conversation_segments
    text='小少爷点点。他收钱入账，又压低了声音：您若得空，只管来问。'
    expected=[{'kind':'speech','text':'小少爷点点。'},{'kind':'narration','text':'他收钱入账，又压低了声音：'},{'kind':'speech','text':'您若得空，只管来问。'}]
    assert dialogue_segments('【台词】'+text,'林掌柜')==expected
    entry={'partnerName':'林掌柜','reply':text,'segments':[{'kind':'speech','text':text}]}
    original=deepcopy(entry);assert conversation_segments([entry])[0]['segments']==expected;assert entry==original
    assert dialogue_segments('【台词】他的手受过伤。我告诉他：别动。','药老')==[{'kind':'speech','text':'他的手受过伤。我告诉他：别动。'}]


@pytest.mark.parametrize('reply,expected',[
    ('【旁白】陆遥抬起头。\n【台词】你好，有什么事？',[{'kind':'narration','text':'陆遥抬起头。'},{'kind':'speech','text':'你好，有什么事？'}]),
    ('【旁白】陆遥没有开口，只朝你点了点头。',[{'kind':'narration','text':'陆遥没有开口，只朝你点了点头。'}]),
])
def test_channels_commit_in_order_without_markers_in_story(client,scene_world,profile,reply,expected):
    w=scene_world;target=scene(client,w)['characters'][0]
    r=client.post(API+w['root']+'/scene/actions',json=body(w,'你好','dialogue',targetEntityId=target['id']),headers={'Idempotency-Key':'channel-chat'})
    assert r.status_code==202,r.text
    plan=DirectorOutput(title='问候',elapsedMinutes=1,timeLabel='清晨',publicOutcome='对方回应你的问候。').model_dump()
    with patch('app.world.worker.call_provider',side_effect=provider(plan,reply)):process(r.json()['taskId'])
    task=client.get(API+'/tasks/'+r.json()['taskId']).json();assert task['state']=='succeeded',task
    head=client.get(API+w['root']).json()['head'];assert '【台词】' not in head['text'] and '【旁白】' not in head['text']
    w['cp']=head['id'];assert scene(client,w)['conversations'][-1]['segments']==expected


def test_existing_conversation_is_classified_without_rewriting_the_checkpoint(client,scene_world):
    w=scene_world;silent='雾气贴着水面。林渡没有说话。'
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);cp.memory={'sceneConversation':{'playerId':cp.state['playerId'],'entries':[{'turnId':'old','partnerId':'npc','partnerName':'林渡','user':'你好','reply':silent}]}};db.commit()
        result=scene_view(cp)
        assert result['conversations'][0]['segments']==[{'kind':'narration','text':silent}]
        assert 'segments' not in cp.memory['sceneConversation']['entries'][0]


def dock(w):
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);definition=deepcopy(cp.definition)
        place=next(e for e in definition['entities'] if e['id']==cp.state['location'])
        place.update(name='雾港东岸码头',description='摆渡人的码头，林渡与阿岑在此长大')
        definition['rules']['routes']={};cp.definition=definition;cp.text='雾气贴着水面。你站在东岸码头。'
        cp.content_hash=checkpoint_hash(cp.state,definition,cp.text,None);db.commit()


def test_named_pier_exposes_two_paths_on_panorama_and_minimap_even_without_road_word(client,scene_world):
    w=scene_world;dock(w);view=scene(client,w)
    assert [e['name'] for e in view['exits']]==['沿码头前行','沿码头返回']
    assert abs(view['exits'][1]['yaw']-view['exits'][0]['yaw'])==pi
    endpoints=[p for p in view['points'] if p.get('fixture','').startswith('path-')]
    assert [p['x'] for p in endpoints]==[20,80] and all(p['y']==50 for p in endpoints)
    for exit in view['exits']:assert any(point['id']==exit['portalId'] for point in view['points'])
    assert not any(e['name']=='道路' for e in view['points'])


def test_pier_paths_use_real_travel_and_return_connection(client,scene_world,profile):
    w=scene_world;dock(w);view=scene(client,w);exit=view['exits'][0];origin=view['descriptor']['placeId']
    r=client.post(API+w['root']+'/scene/travel',json=body(w,exit['direction'],sceneExitId=exit['id']),headers={'Idempotency-Key':'pier-travel'})
    assert r.status_code==202,r.text
    plan=DirectorOutput(title='沿岸仓库',elapsedMinutes=3,timeLabel='清晨',location='warehouse',publicOutcome='你沿码头来到仓库门前。',additions=[{'id':'warehouse','name':'沿岸仓库门前','type':'place','description':'沿岸通路连接码头和仓库大门。','source':'game'}]).model_dump()
    with patch('app.world.worker.call_provider',side_effect=provider(plan,'【旁白】你沿码头走到仓库门前。\n【台词】到了。')):process(r.json()['taskId'])
    assert client.get(API+'/tasks/'+r.json()['taskId']).json()['state']=='succeeded'
    head=client.get(API+w['root']).json()['head']
    destination=head['state']['location']
    assert destination!='warehouse' and any(e['id']==destination for e in head['state']['entities'])
    with SessionLocal() as db:
        definition=db.get(WorldCheckpoint,head['id']).definition
        assert next(e for e in definition['entities'] if e['id']==destination)['name']=='沿岸仓库门前'
    assert '【' not in head['text'] and '“到了。”' in head['text']
    w['cp']=head['id'];assert any(e['id']==origin for e in scene(client,w)['exits'])


def test_named_pier_does_not_override_explicit_route_restriction(client,scene_world):
    w=scene_world;dock(w)
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);definition=deepcopy(cp.definition);definition['rules']['routes']={cp.state['location']:[]};cp.definition=definition;db.commit()
    assert scene(client,w)['exits']==[]


def test_redeclared_existing_destination_is_reused_without_overwriting_its_identity(client,scene_world,profile):
    w=scene_world;dock(w);view=scene(client,w);exit=view['exits'][0]
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);definition=deepcopy(cp.definition)
        definition['entities'].append(EntityDefinition(id='warehouse',name='仓库',type='place',description='原有仓库资料',source='user').model_dump())
        state=deepcopy(cp.state);state['entities']['warehouse']=EntityInitial().model_dump()
        cp.definition=definition;cp.state=state;cp.content_hash=checkpoint_hash(state,definition,cp.text,None);db.commit()
    r=client.post(API+w['root']+'/scene/travel',json=body(w,exit['direction'],sceneExitId=exit['id']),headers={'Idempotency-Key':'reused-pier-destination'})
    assert r.status_code==202,r.text
    plan=DirectorOutput(title='仓库前',elapsedMinutes=3,timeLabel='清晨',publicOutcome='你走到仓库前。',updates=[{'entityId':state['playerId'],'field':'location','value':'warehouse','reason':'沿码头前行','visibleTo':['player']}],additions=[{'id':'warehouse','name':'雾港东岸仓库','type':'place','description':'模型重复登记的新资料','source':'game'}]).model_dump()
    def observed_provider(task_id,stage,prompt,data,limit):
        if prompt=='world_director':assert any(p['name']=='仓库' for p in data['placeRegistry'])
        return provider(plan,'你沿码头走到仓库门前。')(task_id,stage,prompt,data,limit)
    with patch('app.world.worker.call_provider',side_effect=observed_provider):process(r.json()['taskId'])
    task=client.get(API+'/tasks/'+r.json()['taskId']).json();assert task['state']=='succeeded',task
    head=client.get(API+w['root']).json()['head'];assert head['state']['location']=='warehouse'
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,head['id']);place=next(e for e in cp.definition['entities'] if e['id']=='warehouse')
        assert place['name']=='仓库' and place['description']=='原有仓库资料'


def test_destination_reconciliation_does_not_discard_other_collisions():
    definition={'entities':[{'id':'warehouse','type':'place','name':'仓库'}],'lore':[]}
    collision={'id':'warehouse','name':'新人物','type':'character','source':'game'}
    normalized=normalize_plan({'additions':[collision]}, {}, definition)
    assert normalized['additions']==[collision]


def test_indoor_view_of_pier_does_not_create_paths_through_a_wall(client,scene_world):
    layout=build_layout({'id':'room','name':'码头客舍','description':'窗外是码头。'},[],'你在客舍里。窗外是码头。')
    assert not any(node.get('fixture','').startswith('path') for node in layout['nodes'])
