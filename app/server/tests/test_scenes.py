"""Scene persistence/authorization/movement tests, isolated DB, zero paid calls."""
from copy import deepcopy
import io
import json
from unittest.mock import patch
import pytest
from PIL import Image
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import User,uid
from app.errors import Problem
from app.world.models import World,WorldLine,WorldCheckpoint
from app.world.templates import template_definition, catalogue
from app.world.state import initial_state,checkpoint_hash
from app.world.schemas import WorldRules
from app.scenes.models import SceneImage,SceneProfile
from app.scenes.migrate import migrate
from app.scenes.service import scene_view,image_bytes
from app.scenes.provider import encode_panorama, prompt_for
from app.scenes.layout import stable_id, build_layout
from app.scenes.worker import process_one
from app.storage import private_path

API='/api/world/v1'

@pytest.fixture
def scene_world(client,reader,profile):
    # Visual inspection now has an explicit independently selected channel.
    # Synthetic scene tests use this mock profile even when they temporarily
    # enable the real inspection scheduling path.
    selected=client.put('/api/reader/v1/product/vision-model',json={'expectedVersion':0,'profileId':profile['id']})
    assert selected.status_code==200,selected.text
    migrate()
    with SessionLocal() as db:
        owner=db.scalar(select(User.id))
        definition=template_definition(catalogue()[0]['id'],WorldRules().model_dump())
        player=next(e['id'] for e in definition['entities'] if e['type']=='character')
        definition,state=initial_state(definition,player,None,None)
        world=World(owner_id=owner,name='测试客舍',source_title='独立测试',source={'kind':'standalone'},definition=definition,status='active')
        db.add(world);db.flush()
        line=WorldLine(owner_id=owner,world_id=world.id,name='测试线',head_checkpoint_id=uid(),status='active');db.add(line);db.flush()
        cp=WorldCheckpoint(id=line.head_checkpoint_id,owner_id=owner,world_id=world.id,line_id=line.id,sequence=1,title='雪关',text='你站在客舍里。条凳旁的火炉燃着，窗边能看见山道。',state=state,definition=definition,content_hash=checkpoint_hash(state,definition,'你站在客舍里。条凳旁的火炉燃着，窗边能看见山道。',None),kind='start')
        db.add(cp);world.active_line_id=line.id;db.commit()
        result={'world':world.id,'line':line.id,'cp':cp.id,'owner':owner,'root':f'/worlds/{world.id}/lines/{line.id}','bench':stable_id(state['location'],'bench'),'hearth':stable_id(state['location'],'hearth'),'window':stable_id(state['location'],'window')}
    response=client.put(API+'/scene-profile',json={'key':'SCENE-TEST-CREDENTIAL-NEVER-REAL','dailyLimit':50});assert response.status_code==200,response.text
    return result

def request(client,w,key='first',regenerate=False):
    response=client.post(API+w['root']+'/scene',headers={'Idempotency-Key':key},json={'expectedCheckpointId':w['cp'],'regenerate':regenerate})
    assert response.status_code==202,response.text
    return response.json()

def generated():
    image=Image.new('RGB',(800,400),'#a99065');output=io.BytesIO();image.save(output,format='PNG')
    value,metadata=encode_panorama(output.getvalue());return value,metadata,'fixture-request'

def test_refresh_and_two_tabs_share_one_reservation(client,scene_world):
    w=scene_world;a=request(client,w);b=request(client,w,'tab-two');c=request(client,w)
    assert a['id']==b['id']==c['id']
    assert a['characters'] and a['items']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SceneImage))==1
        profile=db.scalar(select(SceneProfile));assert 'SCENE-TEST' not in profile.key_cipher
    profile=client.get(API+'/scene-profile').json();assert profile['hasKey'] and 'key' not in profile

def test_worker_saves_encrypted_image_and_never_repeats(client,scene_world):
    row=request(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier:
        assert process_one();assert not process_one();assert supplier.call_count==1
    response=client.get(API+'/scenes/'+row['id']);assert response.status_code==200
    result=response.json();assert result['status']=='ready';assert 'file' not in result['image']
    data=client.get(API+f"/scenes/{row['id']}/image");assert data.status_code==200;assert data.headers['content-type']=='image/webp'
    with SessionLocal() as db:
        record=db.get(SceneImage,row['id']);assert private_path(record.owner_id,record.image['file']).read_bytes()!=data.content;assert image_bytes(record)==data.content
    client.headers.pop('Authorization');assert client.get(API+f"/scenes/{row['id']}/image").status_code==401


def test_aged_scene_and_crop_source_survive_orphan_cleanup(client,scene_world):
    import os
    import time
    from app.storage import clean_orphan_files,write_private
    from app.scenes.characters import source_name
    row=request(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        saved=db.get(SceneImage,row['id'])
        display=private_path(saved.owner_id,saved.image['file'])
        source=private_path(saved.owner_id,write_private(saved.owner_id,source_name(saved),b'encrypted-source-fixture'))
        orphan=private_path(saved.owner_id,write_private(saved.owner_id,'unreferenced-scene-fixture.enc',b'orphan'))
    original=display.read_bytes()
    for path in (display,source,orphan):os.utime(path,(time.time()-172800,)*2)
    clean_orphan_files()
    assert display.read_bytes()==original and source.exists()
    assert not orphan.exists()
    assert client.get(API+f"/scenes/{row['id']}/image").status_code==200
    with SessionLocal() as db:db.delete(db.get(SceneImage,row['id']));db.commit()
    clean_orphan_files()
    assert not display.exists() and not source.exists()


def test_pending_scene_receipt_without_committed_image_metadata_is_retained(client,scene_world):
    import os
    import time
    from app.storage import clean_orphan_files,write_private
    row=request(client,scene_world)
    name='scene-'+scene_world['world']+'-'+row['id']+'.enc'
    path=private_path(scene_world['owner'],write_private(scene_world['owner'],name,b'durable-receipt-fixture'))
    os.utime(path,(time.time()-172800,)*2)
    clean_orphan_files()
    assert path.read_bytes()==b'durable-receipt-fixture'
    with SessionLocal() as db:
        assert db.get(SceneImage,row['id']).image=={}

def test_unknown_response_is_preserved_without_automatic_retry(client,scene_world):
    row=request(client,scene_world)
    with patch('app.scenes.worker.generate',side_effect=Problem('SCENE_STATUS_UNKNOWN','未知')) as supplier:
        assert process_one();assert not process_one();assert supplier.call_count==1
    assert client.get(API+'/scenes/'+row['id']).json()['status']=='unknown'
    assert request(client,scene_world,'refresh')['id']==row['id']
    assert request(client,scene_world,'explicit-regenerate',True)['id']!=row['id']

def test_move_is_versioned_idempotent_and_retains_original_checkpoint(client,scene_world):
    w=scene_world;body={'expectedCheckpointId':w['cp'],'targetKind':'point','targetId':w['bench']}
    endpoint=API+w['root']+'/scene/move'
    a=client.post(endpoint,json=body,headers={'Idempotency-Key':'move-left'});assert a.status_code==200,a.text
    b=client.post(endpoint,json=body,headers={'Idempotency-Key':'move-left'});assert b.json()==a.json()
    result=client.get(API+w['root']).json();assert result['headCheckpointId']!=w['cp'];assert result['head']['kind']=='scene_move'
    assert result['head']['state']['location']
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,result['headCheckpointId']);assert scene_view(cp)['pointId']==w['bench'];assert db.get(WorldCheckpoint,w['cp']).text=='你站在客舍里。条凳旁的火炉燃着，窗边能看见山道。'
        assert db.scalar(select(func.count()).select_from(WorldCheckpoint))==2
    response=client.post(endpoint,json=body,headers={'Idempotency-Key':'stale-move'});assert response.status_code==409

def test_unknown_exit_cannot_teleport(client,scene_world):
    w=scene_world;r=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'place','targetId':'undiscovered-place'},headers={'Idempotency-Key':'teleport'})
    assert r.status_code==409
    assert client.get(API+w['root']).json()['headCheckpointId']==w['cp']

def test_queue_does_not_publish_for_changed_profile(client,scene_world):
    row=request(client,scene_world)
    client.put(API+'/scene-profile',json={'key':'DIFFERENT-TEST-CREDENTIAL-NEVER-REAL'})
    with patch('app.scenes.worker.generate') as supplier:assert process_one();supplier.assert_not_called()
    assert client.get(API+'/scenes/'+row['id']).json()['errorCode']=='SCENE_CONTEXT_CHANGED'

def test_public_scene_has_no_hidden_character_or_secret(client,scene_world):
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,scene_world['cp']);copy=deepcopy(cp.definition)
        copy['entities'].append({'id':'secret-person','type':'character','name':'隐藏真凶','description':'秘密身份','traits':'秘密','source':'game','evidence':[],'knownBy':[]})
        state=deepcopy(cp.state);state['entities']['secret-person']={'alive':True,'location':state['location'],'fields':{},'inventory':{},'relations':{},'goal':'绝密目标'}
        cp.definition=copy;cp.state=state
        value=scene_view(cp);assert '隐藏真凶' not in json.dumps(value,ensure_ascii=False);assert '绝密目标' not in json.dumps(value,ensure_ascii=False)

def test_deleted_world_removes_image_file_and_pending_result(client,scene_world):
    row=request(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        record=db.get(SceneImage,row['id']);file=private_path(record.owner_id,record.image['file']);assert file.exists()
        db.delete(db.get(World,scene_world['world']));db.commit()
    assert not file.exists();assert client.get(API+'/scenes/'+row['id']+'/image').status_code==404

def test_known_route_requires_real_world_turn_instead_of_local_teleport(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);definition=deepcopy(cp.definition);state=deepcopy(cp.state);player=state['playerId']
        definition['rules']['actionCosts']={'action':{'money':2,'minutes':3}}
        definition['entities'].append({'id':'garden','name':'庭院','type':'place','description':'已看见的庭院','traits':'','source':'user','evidence':[],'knownBy':[player],'knownFacts':[],'aliases':[]})
        state['entities']['garden']={'alive':None,'location':None,'fields':{},'inventory':{},'relations':{},'goal':'','known':[]}
        state['discovered'].append('garden');definition['rules']['routes']={state['location']:['garden']}
        cp.definition=definition;cp.state=state;cp.content_hash=checkpoint_hash(state,definition,cp.text,None);db.commit()
    response=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'place','targetId':'garden'},headers={'Idempotency-Key':'move-garden'})
    assert response.status_code==409,response.text
    assert response.json()['code']=='SCENE_TRAVEL_REQUIRED'
    state=client.get(API+w['root']).json()['head']['state'];assert state['location']!='garden';assert state['minute']==0

def test_foreign_account_cannot_read_scene_or_mutate_line(client,scene_world):
    w=scene_world
    row=request(client,scene_world)
    client.post('/api/reader/v1/auth/register',json={'username':'scene_other','password':'scene-other-password'})
    credentials=client.post('/api/reader/v1/auth/login',json={'username':'scene_other','password':'scene-other-password','clientType':'android','deviceName':'test'}).json()
    client.headers['Authorization']='Bearer '+credentials['accessToken']
    assert client.get(API+'/scenes/'+row['id']).status_code==404
    assert client.get(API+'/scenes/'+row['id']+'/image').status_code==404
    result=client.post(API+scene_world['root']+'/scene/move',json={'expectedCheckpointId':scene_world['cp'],'targetKind':'point','targetId':w['hearth']},headers={'Idempotency-Key':'foreign-move'})
    assert result.status_code==404

def test_move_blocked_during_pending_action(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        line=db.get(WorldLine,w['line']);line.pending_task_id=uid();db.commit()
    result=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'point','targetId':w['hearth']},headers={'Idempotency-Key':'busy-move'})
    assert result.status_code==409
    assert client.get(API+w['root']).json()['headCheckpointId']==w['cp']

def test_late_image_cannot_resurrect_deleted_world(client,scene_world):
    row=request(client,scene_world)
    def late(*args,**kwargs):
        with SessionLocal() as db:db.delete(db.get(World,scene_world['world']));db.commit()
        return generated()
    with patch('app.scenes.worker.generate',side_effect=late):assert process_one()
    with SessionLocal() as db:assert db.get(SceneImage,row['id']) is None
    assert client.get(API+'/scenes/'+row['id']).status_code==404

def test_known_image_save_failure_recovers_without_provider_retry(client,scene_world):
    from app.scenes.service import save_image
    row=request(client,scene_world)
    def failed_save(*args,**kwargs):save_image(*args,**kwargs);raise OSError('isolated save failure')
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier,patch('app.scenes.worker.save_image',side_effect=failed_save):
        assert process_one();assert supplier.call_count==1
    assert client.get(API+'/scenes/'+row['id']).json()['status']=='saving'
    with patch('app.scenes.worker.generate') as supplier:assert process_one();supplier.assert_not_called()
    value=client.get(API+'/scenes/'+row['id']).json();assert value['status']=='ready';assert value['image']['providerRequestId']=='fixture-request'

def test_old_image_cannot_be_labelled_as_new_position(client,scene_world):
    row=request(client,scene_world);w=scene_world
    result=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'point','targetId':w['hearth']},headers={'Idempotency-Key':'other-tab-move'});assert result.status_code==200
    assert client.get(API+'/scenes/'+row['id']).status_code==409

def test_disable_blocks_queue_and_send(client,scene_world):
    row=request(client,scene_world)
    result=client.patch(API+'/scene-profile',json={'enabled':False});assert result.status_code==200
    with patch('app.scenes.worker.generate') as supplier:assert not process_one();supplier.assert_not_called()
    assert client.get(API+'/scenes/'+row['id']).json()['errorCode']=='SCENE_DISABLED'
    result=client.post(API+scene_world['root']+'/scene',json={'expectedCheckpointId':scene_world['cp']},headers={'Idempotency-Key':'disabled-scene'})
    assert result.status_code==409

def test_corrupt_save_receipt_does_not_starve_next_request(client,scene_world):
    row=request(client,scene_world)
    with SessionLocal() as db:r=db.get(SceneImage,row['id']);r.status='saving';db.commit()
    with patch('app.scenes.worker.generate') as supplier:assert process_one();supplier.assert_not_called()
    with patch('app.scenes.worker.generate') as supplier:assert process_one();supplier.assert_not_called()
    assert client.get(API+'/scenes/'+row['id']).json()['errorCode']=='SCENE_RECEIPT_INVALID'
    request(client,scene_world,'regenerate-next',True)
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier:assert process_one();assert supplier.call_count==1

def test_targeted_action_freezes_selected_entity_id(client,scene_world,profile):
    from app.models import Task
    w=scene_world;scene=request(client,w);target=scene['characters'][0]['id']
    body={'expectedCheckpointId':w['cp'],'kind':'dialogue','message':'你好，接下来怎么办？','turnId':'targeted','targetEntityId':target}
    result=client.post(API+w['root']+'/scene/actions',json=body,headers={'Idempotency-Key':'targeted'});assert result.status_code==202,result.text
    with SessionLocal() as db:
        task=db.get(Task,result.json()['taskId']);assert task.payload['selectedTarget']['entityId']==target
    assert client.post(API+w['root']+'/scene/actions',json=body,headers={'Idempotency-Key':'targeted'}).json()==result.json()

def test_narrative_object_is_an_observation_not_invented_inventory(client,scene_world,profile):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);cp.text='水壶在炉上轻响，你把铜路牌放在条凳上。';cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None);db.commit()
    value=request(client,w);target=next(e for e in value['items'] if e['name']=='铜路牌');assert target['type']=='observation'
    result=client.post(API+w['root']+'/scene/actions',json={'expectedCheckpointId':w['cp'],'kind':'observe','message':'我仔细观察铜路牌。','turnId':'observe-prop','targetEntityId':target['id']},headers={'Idempotency-Key':'observe-prop'});assert result.status_code==202,result.text
    from app.models import Task
    with SessionLocal() as db:
        selected=db.get(Task,result.json()['taskId']).payload['selectedTarget'];assert selected['type']=='observedObject';assert 'entityId' not in selected
        assert db.get(WorldCheckpoint,w['cp']).state['entities'] # Frozen state was retained.

def test_observed_objects_remain_visible_after_walking_within_room(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);cp.text='条凳旁的桌上，水壶轻响。';cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None);db.commit()
    result=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'point','targetId':w['bench']},headers={'Idempotency-Key':'walk-past-kettle'});assert result.status_code==200
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,result.json()['checkpointId']);assert any(e['name']=='水壶' for e in scene_view(cp)['items'])


def test_observed_objects_exclude_absent_and_past_mentions(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp'])
        cp.text='这里没有钥匙。昨天在桥头见过水壶。木箱不见了。陆遥没有立刻回答，眼前的烛台微微发亮。'
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None);db.commit()
    value=request(client,w)
    names={e['name'] for e in value['items'] if e['type']=='observation'}
    assert names=={'烛台'}


def test_map_contains_named_grounded_areas_not_compass_slots(client,scene_world):
    value=request(client,scene_world)
    assert {'炉边','条凳旁','窗边'} <= {p['name'] for p in value['points']}
    assert not {'中央','前方','左侧','右侧','后方'} & {p['name'] for p in value['points']}
    assert all(p['source'].get('quote') or p['source'].get('entityId') for p in value['points'])
    assert all(n['id'] in {p['id'] for p in value['points']} for n in value['layout']['nodes'])


def test_portable_items_do_not_become_walkable_areas():
    value=build_layout({'id':'room','name':'房间','description':'简单的房间。'},[], '水壶和钥匙放在背包里。')
    assert len(value['nodes'])==1


def test_known_place_without_route_is_not_an_exit(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);definition=deepcopy(cp.definition);state=deepcopy(cp.state)
        definition['entities'].append({'id':'remote-place','name':'旧城','type':'place','description':'遥远的旧城','traits':'','source':'user','evidence':[],'knownBy':[state['playerId']],'knownFacts':[],'aliases':[]})
        state['entities']['remote-place']={'alive':None,'location':None,'fields':{},'inventory':{},'relations':{},'goal':'','known':[]}
        state['discovered'].append('remote-place');definition['rules']['routes']={}
        cp.state=state;cp.definition=definition;cp.content_hash=checkpoint_hash(state,definition,cp.text,None);db.commit()
    assert not any(e['id']=='remote-place' for e in request(client,w)['exits'])
    result=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'place','targetId':'remote-place'},headers={'Idempotency-Key':'unconnected'})
    assert result.status_code==409


def test_new_pose_changes_geometry_with_owned_room_appearance_reference(client,scene_world):
    w=scene_world;first=request(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    result=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'point','targetId':w['bench']},headers={'Idempotency-Key':'actual-pose'})
    assert result.status_code==200
    next_world={**w,'cp':result.json()['checkpointId']};second=request(client,next_world,'next-pose')
    assert first['id']!=second['id'] and first['sceneHash']!=second['sceneHash']
    assert first['descriptor']['viewpoint']!=second['descriptor']['viewpoint']
    with patch('app.scenes.worker.generate',return_value=generated()) as provider:
        assert process_one();assert provider.call_args.args[2]==generated()[0]
        prompt=prompt_for(provider.call_args.args[0]);assert 'PHYSICALLY' in prompt and '条凳旁' in prompt
        assert 'NOT\ncamera framing' in prompt and second['descriptor']['movementRenderVersion']==4
        assert second['descriptor']['viewpoint']['lookAt']=='条凳旁'
        assert 'MANDATORY new foreground: 条凳旁' in prompt


def test_hotspots_are_stable_when_entities_reorder(client,scene_world):
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,scene_world['cp']);a=scene_view(cp)
        definition=deepcopy(cp.definition);definition['entities'].reverse();cp.definition=definition
        b=scene_view(cp)
        assert a['hotspots']==b['hotspots']


def test_fixture_mentions_exclude_past_and_absence_but_keep_unrelated_negation():
    layout=build_layout({'id':'room','name':'客舍','description':''},[],
        '昨晚在桥头见过火炉。这里没有柜台。台阶已被拆走。陆遥没有回答，眼前的木桌亮着灯。')
    assert {n.get('fixture') for n in layout['nodes'][1:]}=={'table'}


def test_removed_fixture_is_not_revived_by_old_spatial_context(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);cp.memory={'sceneSpatialContext':{'location':cp.state['location'],'playerId':cp.state['playerId'],'text':cp.text}}
        cp.text='火炉被拆走了。条凳仍留在屋里。';cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None);db.commit()
    view=request(client,w)
    assert w['hearth'] not in {p['id'] for p in view['points']}
    response=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'point','targetId':w['bench']},headers={'Idempotency-Key':'after-removal'})
    assert response.status_code==200
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,response.json()['checkpointId'])
        assert w['hearth'] not in {p['id'] for p in scene_view(cp)['points']}


@pytest.mark.parametrize('binding',['location','playerId'])
def test_move_does_not_rebind_another_location_or_identity_context(client,scene_world,binding):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);context={'location':cp.state['location'],'playerId':cp.state['playerId'],'text':'柜台亮着一盏灯。'};context[binding]='someone-or-somewhere-else'
        cp.memory={'sceneSpatialContext':context};db.commit()
    response=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'point','targetId':w['bench']},headers={'Idempotency-Key':'correct-binding'})
    assert response.status_code==200
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,response.json()['checkpointId'])
        assert 'counter' not in {p.get('fixture') for p in scene_view(cp)['points']}


def test_cached_image_gains_new_public_click_target_without_regeneration(client,scene_world):
    w=scene_world;first=request(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);cp.text+='眼前的烛台发亮。';cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None);db.commit()
    second=request(client,w,'new-click-target')
    assert second['id']==first['id']
    candle=next(e for e in second['items'] if e['name']=='烛台')
    assert candle['id'] in second['hotspots']
    assert all(second['hotspots'][key]==value for key,value in first['hotspots'].items())


def test_absent_observation_does_not_survive_local_move_memory(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);cp.kind='scene_move';cp.text='水壶被搬走了。'
        cp.memory={'sceneObservations':{'location':cp.state['location'],'playerId':cp.state['playerId'],'items':[{'id':'old-kettle','name':'水壶','type':'observation','description':'水壶在桌上。','nearby':True}]}}
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None)
        db.commit()
    assert '水壶' not in {e['name'] for e in request(client,w)['items'] if e['type']=='observation'}


def test_past_removal_and_current_reappearance_do_not_hide_a_fixture():
    value=build_layout({'id':'room','name':'客舍','description':''},[],
        '昨天，火炉被拆走了。如今火炉重新装好。',removed_fixtures=['hearth'])
    assert 'hearth' in {n.get('fixture') for n in value['nodes']}


def test_removal_survives_an_ordinary_turn_before_walking(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        original=db.get(WorldCheckpoint,w['cp']);original.text='火炉不见了。条凳仍在。';original.content_hash=checkpoint_hash(original.state,original.definition,original.text,None)
        cp=WorldCheckpoint(id=uid(),owner_id=w['owner'],world_id=w['world'],line_id=w['line'],parent_checkpoint_id=original.id,sequence=2,title='继续',text='旅人抬头看着你。',state=deepcopy(original.state),definition=deepcopy(original.definition),memory=deepcopy(original.memory),kind='turn',content_hash=checkpoint_hash(original.state,original.definition,'旅人抬头看着你。',original.id));db.add(cp);db.flush()
        db.get(WorldLine,w['line']).head_checkpoint_id=cp.id;w={**w,'cp':cp.id};db.commit()
    assert w['hearth'] not in {p['id'] for p in request(client,w)['points']}


def test_same_sentence_reappearance_follows_clause_order():
    value=build_layout({'id':'room','name':'客舍','description':''},[],
        '火炉被拆走了，随后又重新装了一台火炉。',removed_fixtures=['hearth'])
    assert 'hearth' in {n.get('fixture') for n in value['nodes']}


def test_current_fixture_removal_overrides_old_reference_in_prompt():
    layout=build_layout({'id':'room','name':'客舍','description':'火炉燃着。'},[], '火炉被拆走了。')
    prompt=prompt_for({'description':'客舍','placeName':'客舍','pointLabel':'门口','lighting':'夜','environment':{'房门':'已拆除'},'renderLayout':layout})
    assert 'CURRENT STATE OVERRIDES THE REFERENCE' in prompt
    assert 'obsolete fixtures' in prompt and '["炉边"]' in prompt and '已拆除' in prompt


def test_truncated_evidence_does_not_authorize_old_fixture(client,scene_world):
    from app.scenes.service import spatial_evidence
    w=scene_world
    with SessionLocal() as db:
        parent=db.get(WorldCheckpoint,w['cp']);parent.text='火炉被拆走了。';parent.content_hash=checkpoint_hash(parent.state,parent.definition,parent.text,None)
        definition=deepcopy(parent.definition)
        next(e for e in definition['entities'] if e['id']==parent.state['location'])['description']='火炉和条凳陈设在客舍内。'
        parent.definition=definition;parent.content_hash=checkpoint_hash(parent.state,parent.definition,parent.text,None)
        memory={'sceneSpatialContext':{'location':parent.state['location'],'playerId':parent.state['playerId'],'text':'火炉燃着。'}}
        for sequence in range(2,132):
            text='条凳旁，旅人仍在等你。'
            cp=WorldCheckpoint(id=uid(),owner_id=w['owner'],world_id=w['world'],line_id=w['line'],parent_checkpoint_id=parent.id,sequence=sequence,title='等待',text=text,state=deepcopy(parent.state),definition=deepcopy(parent.definition),memory=deepcopy(memory),kind='turn',content_hash=checkpoint_hash(parent.state,parent.definition,text,parent.id));db.add(cp);parent=cp
        db.flush()
        assert spatial_evidence(parent)[1]['evidenceTruncated']
        assert 'hearth' not in {p.get('fixture') for p in scene_view(parent)['points']}
        db.get(WorldLine,w['line']).head_checkpoint_id=parent.id;w={**w,'cp':parent.id};db.commit()
    response=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'point','targetId':w['bench']},headers={'Idempotency-Key':'walk-after-truncation'})
    assert response.status_code==200,response.text
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,response.json()['checkpointId'])
        assert cp.memory['sceneSpatialContext']['evidenceTruncated']
        assert 'hearth' not in {p.get('fixture') for p in scene_view(cp)['points']}
