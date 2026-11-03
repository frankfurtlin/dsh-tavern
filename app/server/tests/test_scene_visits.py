"""Real executor round trips and picture reservations, without paid calls."""
from copy import deepcopy
from unittest.mock import patch
from sqlalchemy import select,func
from app.db import SessionLocal
from app.scenes.models import SceneImage
from app.scenes.worker import process_one
from app.scenes.service import scene_view
from app.world.models import WorldCheckpoint,WorldLine
from app.world.schemas import DirectorOutput,EntityDefinition,EntityInitial
from app.models import uid
from app.world.worker import process
from app.world.state import checkpoint_hash
from .test_scenes import scene_world,request,generated
from .test_scene_interaction import executable_scene_source,provider,body,API
import pytest


def append_fixture_visit(db,w,location,text,player_id=None):
    parent=db.get(WorldCheckpoint,w['cp']);state=deepcopy(parent.state)
    state['location']=location;state['entities'][state['playerId']]['location']=location
    if player_id:state['playerId']=player_id;state['entities'][player_id]['location']=location
    child=WorldCheckpoint(id=uid(),owner_id=parent.owner_id,world_id=parent.world_id,line_id=parent.line_id,
        parent_checkpoint_id=parent.id,sequence=parent.sequence+1,title='往返',text=text,action='走动',state=state,
        definition=deepcopy(parent.definition),content_hash=checkpoint_hash(state,parent.definition,text,parent.id),
        kind='turn',memory=deepcopy(parent.memory or {}))
    db.add(child);db.flush();db.get(WorldLine,w['line']).head_checkpoint_id=child.id;w['cp']=child.id
    return child


def fixture_roundtrip_world(db,w):
    cp=db.get(WorldCheckpoint,w['cp']);origin=cp.state['location']
    definition=deepcopy(cp.definition);state=deepcopy(cp.state)
    definition['entities'].append(EntityDefinition(id='garden',type='place',name='庭院',description='石径通向客舍。',source='game').model_dump())
    definition['rules']['routes']={origin:['garden'],'garden':[origin]}
    state['entities']['garden']=EntityInitial().model_dump();state['discovered']=list(set(state['discovered']+['garden']))
    cp.definition=definition;cp.state=state;cp.text='屋内的木桌仍在炉边，条凳摆在窗边。'
    cp.content_hash=checkpoint_hash(state,definition,cp.text,cp.parent_checkpoint_id)
    db.flush();return origin


@pytest.mark.parametrize('text,expected',[
    ('福尔摩斯仍在寓所炉火旁，你独自站在街头。','你独自站在街头。'),
    ('你面前是石径。福尔摩斯留在壁炉旁，手边摆着木桌。你继续前行。','你面前是石径。你继续前行。'),
    ('你站在炉火旁，福尔摩斯的话仍在耳边回响。','你站在炉火旁，福尔摩斯的话仍在耳边回响。'),
    ('福尔摩斯留在寓所。你面前的木桌上放着地图。','你面前的木桌上放着地图。'),
    ('福尔摩斯仍在寓所炉火旁，而你面前的石阶上积着霜。','而你面前的石阶上积着霜。'),
    ('福尔摩斯仍在寓所炉火旁，但是你身后的木桌已经搬走。','但是你身后的木桌已经搬走。'),
])
def test_remote_scene_prose_cannot_create_local_furniture(text,expected):
    from app.scenes.mentions import local_scene_text
    assert local_scene_text(text,['福尔摩斯'])==expected
    assert local_scene_text(text,[])==text


@pytest.mark.parametrize('local',['彼得森站在街角的木桌边。','而彼得森把你面前的木桌搬走了。'])
def test_remote_scene_clause_switches_back_to_known_present_npc(local):
    from app.scenes.mentions import local_scene_text
    assert local_scene_text('福尔摩斯仍在寓所炉火旁，'+local,['福尔摩斯'],['彼得森'])==local


def test_remote_origin_fire_is_not_in_destination_or_its_return_history(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        origin=fixture_roundtrip_world(db,w);cp=db.get(WorldCheckpoint,w['cp'])
        definition=deepcopy(cp.definition);state=deepcopy(cp.state)
        definition['entities'].append(EntityDefinition(id='remote-holmes',type='character',name='福尔摩斯',description='留在客舍',source='game').model_dump())
        state['entities']['remote-holmes']=EntityInitial(location=origin).model_dump()
        state['discovered'].append('remote-holmes');cp.state=state;cp.definition=definition
        child=append_fixture_visit(db,w,'garden','你站在庭院里。福尔摩斯仍在客舍炉火旁，木桌上的地图未动。你面前石径延伸。')
        assert not any(p.get('fixture') in ('hearth','table') for p in scene_view(child)['points'])
        assert '客舍炉火' in child.text
        returned=append_fixture_visit(db,w,origin,'你回到客舍。')
        assert any(p.get('fixture')=='hearth' for p in scene_view(returned)['points'])
        again=append_fixture_visit(db,w,'garden','你回到庭院。')
        assert not any(p.get('fixture') in ('hearth','table') for p in scene_view(again)['points'])


def test_multiple_return_visits_do_not_redraw_furniture_omitted_from_new_prose(client,scene_world):
    w=scene_world
    with SessionLocal() as db:origin=fixture_roundtrip_world(db,w);db.commit()
    first=request(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier:
        assert process_one();picture=client.get(API+'/scenes/'+first['id']+'/image').content
        for index in range(3):
            with SessionLocal() as db:
                append_fixture_visit(db,w,'garden','你来到庭院。')
                child=append_fixture_visit(db,w,origin,'你沿原路回到客舍，烤了烤手。')
                assert any(p.get('fixture')=='table' for p in scene_view(child)['points'])
                db.commit()
            returned=request(client,w,'unchanged-return-'+str(index))
            assert returned['id']==first['id']
            assert client.get(API+'/scenes/'+returned['id']+'/image').content==picture
            assert not process_one()
        assert supplier.call_count==1


def test_explicit_pose_persists_through_dialogue_and_return_but_not_hidden_fields(client,scene_world):
    from app.scenes.cache import appearance
    with SessionLocal() as db:
        w=scene_world;origin=fixture_roundtrip_world(db,w);cp=db.get(WorldCheckpoint,w['cp'])
        npc=next(entity for entity in scene_view(cp)['characters'])
        cp.text+=npc['name']+'坐在窗边的椅子上。'
        first=scene_view(cp);assert first['descriptor']['characterPoses'][npc['id']]=='seated'
        after=append_fixture_visit(db,w,origin,npc['name']+'说：“今天我们先看信。”')
        assert appearance(scene_view(after))==appearance(first)
        append_fixture_visit(db,w,'garden','你来到庭院。')
        returned=append_fixture_visit(db,w,origin,'你沿原路回到客舍。')
        assert appearance(scene_view(returned))==appearance(first)
        stood=append_fixture_visit(db,w,origin,npc['name']+'站起身。')
        current=scene_view(stood);assert current['descriptor']['characterPoses'][npc['id']]=='standing'
        assert appearance(current)!=appearance(first)
        # A secret ledger field is not a publicly observed body posture.
        state=deepcopy(stood.state);state['entities'][npc['id']]['fields']['posture']='kneeling';stood.state=state
        assert scene_view(stood)['descriptor']['characterPoses'][npc['id']]=='standing'


def test_explicit_fixture_removal_survives_leaving_and_returning(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        origin=fixture_roundtrip_world(db,w)
        append_fixture_visit(db,w,origin,'屋内的木桌已经搬走。')
        append_fixture_visit(db,w,'garden','你来到庭院。')
        child=append_fixture_visit(db,w,origin,'你沿原路回到客舍。')
        assert not any(p.get('fixture')=='table' for p in scene_view(child)['points'])
        child=append_fixture_visit(db,w,origin,'一张木桌重新搬回屋内。')
        assert any(p.get('fixture')=='table' for p in scene_view(child)['points'])


def test_reappeared_fixture_before_departure_overrides_inherited_removal_snapshot(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        origin=fixture_roundtrip_world(db,w)
        removed=append_fixture_visit(db,w,origin,'木桌已经搬走。')
        removed.kind='scene_move';removed.memory={'sceneSpatialContext':{
            'location':origin,'playerId':removed.state['playerId'],'text':'木桌已经搬走。','removedFixtures':['table']}}
        restored=append_fixture_visit(db,w,origin,'一张木桌重新搬回屋内。')
        assert restored.memory['sceneSpatialContext']['removedFixtures']==['table']
        append_fixture_visit(db,w,'garden','你来到庭院。')
        returned=append_fixture_visit(db,w,origin,'你沿原路回到客舍。')
        assert any(p.get('fixture')=='table' for p in scene_view(returned)['points'])


@pytest.mark.parametrize('change',['已经拆除','已拆除'])
def test_demolished_fixture_is_not_reintroduced_by_background_description(change):
    from app.scenes.layout import build_layout
    place={'id':'a','name':'客舍','description':'屋内摆着木桌。'}
    layout=build_layout(place,[],'木桌'+change+'。',current_text='木桌'+change+'。')
    assert 'table' in layout['removedFixtures']
    assert not any(p.get('fixture')=='table' for p in layout['nodes'])


@pytest.mark.parametrize('change',['并没有被搬走','没有被搬走','并未被拆除','未曾被移走','还没被拆走','没有被烧毁'])
def test_denied_removal_preserves_current_fixture(change):
    from app.scenes.layout import build_layout
    place={'id':'a','name':'客舍','description':'屋内摆着木桌。'}
    layout=build_layout(place,[],'木桌'+change+'。',current_text='木桌'+change+'。')
    assert 'table' not in layout['removedFixtures']
    assert any(p.get('fixture')=='table' for p in layout['nodes'])


def test_previous_visitor_does_not_reappear_when_only_its_furniture_is_retained(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        origin=fixture_roundtrip_world(db,w);parent=db.get(WorldCheckpoint,w['cp'])
        person=next(e for e in scene_view(parent)['characters']);parent.text+=' '+person['name']+'站在木桌旁，手里拿着旧钥匙。'
        left=append_fixture_visit(db,w,'garden','你来到庭院。');state=deepcopy(left.state)
        state['entities'][person['id']]['location']='garden';left.state=state
        child=append_fixture_visit(db,w,origin,'你沿原路回到客舍，屋里安静。')
        view=scene_view(child)
        assert any(p.get('fixture')=='table' for p in view['points'])
        assert person['id'] not in {p['id'] for p in view['characters']}
        quotes='\n'.join(p.get('source',{}).get('quote','') for p in view['points'])
        assert person['name'] not in quotes and '旧钥匙' not in quotes


def commit(client,w,plan,text,key,**extra):
    result=client.post(API+w['root']+'/actions',json=body(w,key,**extra),headers={'Idempotency-Key':key})
    assert result.status_code==202,result.text
    with patch('app.world.worker.call_provider',side_effect=provider(plan.model_dump(),text)):
        process(result.json()['taskId'])
    finished=client.get(API+'/tasks/'+result.json()['taskId']).json()
    assert finished['state']=='succeeded',finished
    w['cp']=client.get(API+w['root']).json()['head']['id']
    with SessionLocal() as db:return scene_view(db.get(WorldCheckpoint,w['cp']))


def test_new_route_and_changed_light_require_new_picture_then_unchanged_visit_reuses(client,scene_world,profile):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);definition=deepcopy(cp.definition);definition['rules']['routes']={};cp.definition=definition;db.commit()
        origin=cp.state['location']
    first=request(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()) as image_provider:
        assert process_one()
        view=commit(client,w,DirectorOutput(title='庭院',elapsedMinutes=3,timeLabel='午后',location='garden',publicOutcome='你走出客舍来到庭院。',additions=[{'id':'garden','type':'place','name':'雪中庭院','description':'石径连着客舍的大门。','source':'game'}]),'你走出客舍来到庭院。','typed-to-garden')
        garden=view['descriptor']['placeId']
        assert origin in {e['id'] for e in view['exits']}
        second=request(client,w,'garden-image');assert process_one()
        # Old records lacked sceneConnections: derive the reverse exit on read.
        with SessionLocal() as db:
            cp=db.get(WorldCheckpoint,w['cp']);cp.memory={k:v for k,v in cp.memory.items() if k!='sceneConnections'};db.commit()
            assert origin in {e['id'] for e in scene_view(cp)['exits']}
        commit(client,w,DirectorOutput(title='重回客舍',elapsedMinutes=3,timeLabel='午后',location=origin,publicOutcome='你沿原路回到客舍。'),'你沿原路回到客舍，轻声放下包袱。','typed-return')
        returned=request(client,w,'return-image')
        assert returned['id']!=first['id'] and returned['checkpointId']==w['cp']
        assert process_one()
        assert client.get(API+'/scenes/'+first['id']).status_code==409
        assert client.get(API+'/scenes/'+first['id']+'/image').status_code==200
        commit(client,w,DirectorOutput(title='再到庭院',elapsedMinutes=3,timeLabel='午后',location=garden,publicOutcome='你再次来到庭院。'),'你再次来到庭院。','typed-to-garden-again')
        assert request(client,w,'garden-again')['id']==second['id']
        assert not process_one() and image_provider.call_count==3


def test_cast_change_uses_same_pose_image_reference_and_other_visual_changes_invalidate(client,scene_world):
    w=scene_world;first=request(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);state=deepcopy(cp.state)
        target=scene_view(cp)['characters'][0]['id'];state['entities'][target]['location']='elsewhere';cp.state=state
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,cp.parent_checkpoint_id);db.commit()
    changed=request(client,w,'departed-character')
    assert changed['id']!=first['id']
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier:
        assert process_one()
        assert supplier.call_args.args[2]==generated()[0]
        assert supplier.call_args.args[0]['referenceSceneId']==first['id']
        assert supplier.call_args.args[0]['referenceMode']=='edit-cast-and-state'
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);state=deepcopy(cp.state);state['entities'][state['location']]['fields']['天气']='大雨';cp.state=state
        state.setdefault('observed',{}).setdefault(state['location'],{})['field:天气']='大雨'
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,cp.parent_checkpoint_id);db.commit()
    assert request(client,w,'weather-change')['id']!=changed['id']


def test_legacy_picture_can_be_reused_without_rewriting_its_hash(client,scene_world):
    w=scene_world;first=request(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        row=db.get(SceneImage,first['id']);descriptor=deepcopy(row.descriptor);descriptor.pop('appearance');row.descriptor=descriptor;row.scene_hash='f'*64;db.commit()
    assert request(client,w,'legacy-cache')['id']==first['id']
    assert client.get(API+'/scenes/'+first['id']).status_code==200
    with SessionLocal() as db:assert db.get(SceneImage,first['id']).scene_hash=='f'*64


def test_fork_reuses_received_bytes_and_survives_original_branch_deletion(client,scene_world):
    w=scene_world;original=request(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier:
        assert process_one() and supplier.call_count==1
        picture=client.get(API+'/scenes/'+original['id']+'/image').content
        fork=client.post(API+'/worlds/'+w['world']+'/fork',json={'checkpointId':w['cp'],'mode':'continue','name':'保留图像的分支'},headers={'Idempotency-Key':'fork-image'})
        assert fork.status_code==200,fork.text
        line=fork.json();branch={**w,'line':line['id'],'cp':line['headCheckpointId'],'root':f"/worlds/{w['world']}/lines/{line['id']}"}
        reused=request(client,branch,'fork-image-reuse')
        assert reused['status']=='ready' and reused['id']!=original['id']
        with SessionLocal() as db:assert db.get(SceneImage,reused['id']).descriptor['reusedFromSceneId']==original['id']
        assert supplier.call_count==1
        assert client.get(API+'/scenes/'+reused['id']+'?lineId='+branch['line']).status_code==200
        deleted=client.delete(API+w['root'])
        assert deleted.status_code==200,deleted.text
        assert client.get(API+'/scenes/'+reused['id']+'/image').content==picture
        assert client.get(API+'/scenes/'+reused['id']+'?lineId='+branch['line']).status_code==200


@pytest.mark.parametrize('failure',['failed','unknown'])
def test_fork_copies_parent_picture_after_failed_repair_and_survives_delete(client,scene_world,failure):
    w=scene_world;original=request(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier:
        assert process_one()
        with SessionLocal() as db:
            parent=db.get(SceneImage,original['id'])
            child=SceneImage(id=uid(),owner_id=parent.owner_id,world_id=parent.world_id,line_id=parent.line_id,
                checkpoint_id=parent.checkpoint_id,profile_id=parent.profile_id,profile_version=parent.profile_version,
                scene_hash=parent.scene_hash,generation=parent.generation+1,status=failure,
                descriptor={**parent.descriptor,'repairOfSceneId':parent.id,'visualRepairAttempt':1},image={},error_code='SCENE_STATUS_UNKNOWN')
            db.add(child);parent.image={**parent.image,'anchorStatus':'repairing','repairSceneId':child.id};db.commit()
        picture=client.get(API+'/scenes/'+original['id']+'/image').content
        fork=client.post(API+'/worlds/'+w['world']+'/fork',json={'checkpointId':w['cp'],'mode':'continue','name':'保留失败补图前的画面'},headers={'Idempotency-Key':'fork-failed-repair'})
        assert fork.status_code==200,fork.text
        line=fork.json();branch={**w,'line':line['id'],'cp':line['headCheckpointId'],'root':f"/worlds/{w['world']}/lines/{line['id']}"}
        reused=request(client,branch,'reuse-after-failed-repair')
        assert reused['id']!=original['id'] and reused['status']=='ready'
        assert reused['image']['anchorStatus']=='incomplete' and reused['image']['repairError']=='SCENE_STATUS_UNKNOWN'
        assert supplier.call_count==1
        assert client.delete(API+w['root']).status_code==200
        assert client.get(API+'/scenes/'+reused['id']+'/image').content==picture
        assert client.get(API+'/scenes/'+reused['id']+'?lineId='+branch['line']).status_code==200


@pytest.mark.parametrize('inspection',['queued','unknown','not_sent'])
def test_fork_waits_for_existing_inspection_instead_of_copying_a_permanent_spinner(client,scene_world,profile,inspection):
    from app.scenes.binding import queue_binding
    from app.models import Task
    w=scene_world;original=request(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        row=db.get(SceneImage,original['id'])
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
        prior=db.get(Task,row.image['bindingTaskId'])
        if inspection!='queued':
            prior.state='failed';prior.provider_state=inspection;prior.error_code='PROVIDER_CONNECTION_FAILED'
            row.image={**row.image,'anchorStatus':'unavailable'}
        db.commit()
    fork=client.post(API+'/worlds/'+w['world']+'/fork',json={'checkpointId':w['cp'],'mode':'continue','name':'等待校验'},headers={'Idempotency-Key':'fork-pending'})
    assert fork.status_code==200,fork.text
    line=fork.json();branch={**w,'line':line['id'],'cp':line['headCheckpointId'],'root':f"/worlds/{w['world']}/lines/{line['id']}"}
    with patch('app.scenes.binding.settings.ai_mode','live'):
        reused=request(client,branch,'reuse-pending');again=request(client,branch,'reuse-still-pending')
    assert reused['id']==again['id']==original['id']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SceneImage))==1
        assert db.scalar(select(func.count()).select_from(Task).where(Task.task_type=='world_scene_binding'))==1
        row=db.get(SceneImage,original['id']);task=db.get(Task,row.image['bindingTaskId'])
        task.state='succeeded';task.provider_state='completed';row.image={**row.image,'anchorStatus':'complete'};db.commit()
    ready=request(client,branch,'reuse-complete')
    assert ready['id']!=original['id'] and ready['image']['anchorStatus']=='complete'


def test_legacy_empty_background_is_updated_with_the_visible_cast_using_its_reference(client,scene_world):
    w=scene_world;first=request(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        row=db.get(SceneImage,first['id']);descriptor=deepcopy(row.descriptor)
        descriptor.pop('appearance');descriptor['sceneCast']=[];row.descriptor=descriptor;row.scene_hash='e'*64;db.commit()
    updated=request(client,w,'add-cast-to-legacy-background')
    assert updated['id']!=first['id']
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier:
        assert process_one()
        assert supplier.call_args.args[2]==generated()[0]
        assert supplier.call_args.args[0]['sceneCast']
        assert supplier.call_args.args[0]['referenceSceneId']==first['id']


def test_multiple_exits_do_not_share_identical_marker_position(client,scene_world):
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,scene_world['cp']);definition=deepcopy(cp.definition)
        places=['garden','road'];state=deepcopy(cp.state)
        for key in places:
            definition['entities'].append(EntityDefinition(id=key,type='place',name=key,description='客舍外的通路。',source='game').model_dump())
            state['entities'][key]=EntityInitial().model_dump()
        state['discovered']=list(set(state['discovered']+places));cp.state=state
        definition['rules']['routes']={cp.state['location']:places};cp.definition=definition;db.commit()
        exits=scene_view(cp)['exits'];assert len(exits)>=2
        assert len({(e['yaw'],e['pitch']) for e in exits})==len(exits)


def test_multi_stop_arrival_offers_whole_return_trip_but_does_not_bypass_blocked_route(client,scene_world):
    with SessionLocal() as db:
        parent=db.get(WorldCheckpoint,scene_world['cp']);definition=deepcopy(parent.definition);state=deepcopy(parent.state);origin=state['location']
        for key in ['road','market']:
            definition['entities'].append(EntityDefinition(id=key,type='place',name=key,description='通路相连。',source='game').model_dump())
            state['entities'][key]=EntityInitial().model_dump()
        definition['rules']['routes']={origin:['road'],'road':[origin,'market'],'market':['road']}
        state['discovered']=list(set(state['discovered']+['road','market']));state['location']='market';state['entities'][state['playerId']]['location']='market'
        child=WorldCheckpoint(id=uid(),owner_id=parent.owner_id,world_id=parent.world_id,line_id=parent.line_id,parent_checkpoint_id=parent.id,
            sequence=parent.sequence+1,title='坊市',text='你穿过街道抵达坊市。',action='去坊市',state=state,definition=definition,
            content_hash=checkpoint_hash(state,definition,'你穿过街道抵达坊市。',parent.id),kind='turn',memory={})
        db.add(child);db.flush()
        assert any(e['id']==origin and e['name'].startswith('返回') for e in scene_view(child)['exits'])
        definition=deepcopy(child.definition);definition['rules']['routes']['market']=[];child.definition=definition
        assert not scene_view(child)['exits']
