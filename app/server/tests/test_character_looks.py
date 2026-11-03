"""Opening portraits, immutable versions and real scene reference plumbing."""
from copy import deepcopy
from unittest.mock import patch
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import uid,User
from app.world.models import World,WorldLine
from app.scenes.models import SceneImage,CharacterLook
from app.scenes.service import queue_scene,scene_view,save_image
from app.scenes.worker import process_one
from app.scenes.looks import library,queue_look,selected_images
from app.errors import Problem
from .test_scenes import scene_world,generated,API


def test_scene_style_uses_frozen_checkpoint_instead_of_mutable_world_template(client,scene_world):
    from app.world.models import WorldCheckpoint
    w=scene_world
    with SessionLocal() as db:
        checkpoint=db.get(WorldCheckpoint,w['cp']);frozen=deepcopy(checkpoint.definition)
        world=db.get(World,w['world']);world.definition={**world.definition,'description':'未来新设定不能渗入旧存档','narrativeStyle':'未来时代'};db.commit()
    response=client.post(API+'/worlds/'+w['world']+'/lines/'+w['line']+'/scene',json={'expectedCheckpointId':w['cp']},headers={'Idempotency-Key':'frozen-visual-setting'})
    assert response.status_code==202,response.text
    with SessionLocal() as db:
        image=db.get(SceneImage,response.json()['id'])
        assert image.descriptor['setting']['description']==frozen.get('description','')[:1000]
        assert image.descriptor['narrativeStyle']!='未来时代'
        assert db.get(WorldCheckpoint,w['cp']).definition==frozen

def test_library_reference_keeps_fabric_instead_of_chroma_keying():
    import io
    from PIL import Image
    from app.scenes.characters import encode_portrait,character_prompt
    source=Image.new('RGB',(120,200),'#00ff00');buffer=io.BytesIO();source.save(buffer,format='PNG')
    data,meta=encode_portrait(buffer.getvalue())
    with Image.open(io.BytesIO(data)) as image:
        assert image.mode=='RGB' and image.size==(120,200) and image.getpixel((0,0))[1]>240
    text=character_prompt({'portraitLibrary':True,'worldName':'山海','name':'少年','description':'年轻旅人','appearance':'十六岁'})
    assert '十六岁' in text and 'warm ivory' in text and 'appearance reference' in text

def test_portrait_never_becomes_the_panorama_edit_canvas(client,scene_world):
    import base64,io,json,httpx
    from PIL import Image
    from app.scenes.provider import generate
    w=scene_world
    response=client.post(API+w['root']+'/scene',headers={'Idempotency-Key':'reference-protocol'},json={'expectedCheckpointId':w['cp']})
    with SessionLocal() as db:descriptor=deepcopy(db.get(SceneImage,response.json()['id']).descriptor)
    descriptor.update(characterReferenceOrder=[{'image':2,'entityId':'npc','name':'旅人'}],hasPanoramaReference=False)
    image,_,_=generated();requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(image).decode()}]})
    client_type=httpx.Client
    with patch('app.scenes.provider.public_address',return_value='1.1.1.1'),patch('app.scenes.provider.decrypt_key',return_value='ISOLATED-PORTRAIT-TEST'),patch('app.scenes.provider.httpx.Client',side_effect=lambda **kwargs:client_type(transport=httpx.MockTransport(handler),**kwargs)):
        generate(descriptor,'encrypted-test',character_references=[image])
    inputs=requests[0]['extra_body']['image'];assert len(inputs)==2
    with Image.open(io.BytesIO(base64.b64decode(inputs[0].split(',')[1]))) as canvas:assert canvas.size==(3136,1344)
    with Image.open(io.BytesIO(base64.b64decode(inputs[1].split(',')[1]))) as portrait, Image.open(io.BytesIO(image)) as original:
        assert portrait.size==original.size and portrait.convert('RGB').tobytes()==original.convert('RGB').tobytes()
    assert 'Images 2 onward' in requests[0]['prompt'] and 'SAME small size and position' in requests[0]['prompt']
    requests.clear()
    descriptor.update(hasPanoramaReference=True,referenceMode='edit-cast-and-state')
    with patch('app.scenes.provider.public_address',return_value='1.1.1.1'),patch('app.scenes.provider.decrypt_key',return_value='ISOLATED-PORTRAIT-TEST'),patch('app.scenes.provider.httpx.Client',side_effect=lambda **kwargs:client_type(transport=httpx.MockTransport(handler),**kwargs)):
        generate(descriptor,'encrypted-test',reference=image,character_references=[image])
    assert len(requests[0]['extra_body']['image'])==2
    with Image.open(io.BytesIO(base64.b64decode(requests[0]['extra_body']['image'][0].split(',')[1]))) as panorama, Image.open(io.BytesIO(image)) as original:
        assert panorama.size==original.size and panorama.convert('RGB').tobytes()==original.convert('RGB').tobytes()
    assert 'Keep the panorama framing' in requests[0]['prompt']

def cast(client,w):return client.get(API+'/worlds/'+w['world']).json()['characters']
def enqueue(client,w,entity,key='look',**data):
    response=client.post(API+'/worlds/'+w['world']+'/character-looks',headers={'Idempotency-Key':key},json={'entityId':entity,**data})
    assert response.status_code==202,response.text
    return response.json()
def finish():
    with patch('app.scenes.worker.generate',return_value=generated()) as provider:
        assert process_one();return provider
def listing(client,w):return client.get(API+'/worlds/'+w['world']+'/character-looks').json()['characters']

def test_portraits_before_first_game_and_idempotent_refresh(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        original=db.get(World,w['world']);world=World(owner_id=w['owner'],name=original.name,source_title='独立',source={'kind':'standalone'},definition=deepcopy(original.definition),status='active');db.add(world);db.commit();w={**w,'world':world.id}
    entity=cast(client,w)[0]['id']
    a=enqueue(client,w,entity);b=enqueue(client,w,entity,'two-tabs');c=enqueue(client,w,entity)
    assert a['id']==b['id']==c['id']
    with SessionLocal() as db:
        row=db.get(SceneImage,a['id']);assert row.line_id is None and row.checkpoint_id is None
        assert db.scalar(select(func.count()).select_from(WorldLine).where(WorldLine.world_id==w['world']))==0
    finish()
    status=client.get(API+'/scenes/'+a['id']);assert status.status_code==200 and status.json()['status']=='ready'
    assert client.get(API+'/scenes/'+a['id']+'/image').status_code==200
    entry=listing(client,w)[0];assert entry['selectedId'] is None
    assert len(entry['versions'])==1


def test_unlimited_image_setting_preserves_profile_version_and_cached_images(client,scene_world):
    from app.scenes.models import SceneProfile
    w=scene_world
    with SessionLocal() as db:
        profile=db.scalar(select(SceneProfile).where(SceneProfile.owner_id==w['owner']))
        profile.daily_limit=1;before=profile.version;db.commit()
    first=enqueue(client,w,cast(client,w)[0]['id']);finish()
    response=client.patch(API+'/scene-profile',json={'enabled':True,'dailyLimit':0})
    assert response.status_code==200 and response.json()['dailyLimit']==0
    with SessionLocal() as db:
        assert db.scalar(select(SceneProfile).where(SceneProfile.owner_id==w['owner'])).version==before
        assert db.get(SceneImage,first['id']).status=='ready'
    second=enqueue(client,w,cast(client,w)[1]['id'],'unlimited-second')
    assert second['id']!=first['id']

def test_versions_selection_conflict_and_failed_regeneration_keep_ready(client,scene_world):
    w=scene_world;entity=cast(client,w)[0]['id'];a=enqueue(client,w,entity);finish()
    b=enqueue(client,w,entity,'new-look',appearance={'age':'十六岁少年','hair':'黑发'},regenerate=True);finish()
    entry=next(c for c in listing(client,w) if c['entityId']==entity)
    assert entry['selectedId'] is None and len(entry['versions'])==2
    endpoint=API+'/worlds/'+w['world']+'/character-looks/'+entity+'/selection'
    body={'sceneId':b['id'],'expectedVersion':entry['version']}
    changed=client.post(endpoint,json=body,headers={'Idempotency-Key':'select'});assert changed.status_code==200,changed.text
    assert client.post(endpoint,json=body,headers={'Idempotency-Key':'select'}).json()==changed.json()
    assert client.post(endpoint,json={**body,'sceneId':a['id']},headers={'Idempotency-Key':'stale'}).status_code==409
    failed=enqueue(client,w,entity,'failed',regenerate=True)
    with patch('app.scenes.worker.generate',side_effect=Problem('SCENE_STATUS_UNKNOWN','未知')):assert process_one()
    entry=next(c for c in listing(client,w) if c['entityId']==entity)
    assert entry['selectedId']==changed.json()['selectedId'] and next(v for v in entry['versions'] if v['id']==failed['id'])['status']=='unknown'

def test_player_portrait_allowed_but_only_visible_npcs_guide_scene(client,scene_world):
    w=scene_world;people=cast(client,w)
    first=enqueue(client,w,people[0]['id']);finish()
    second=enqueue(client,w,people[1]['id'],'second');finish()
    for entity,candidate in [(people[0]['id'],first),(people[1]['id'],second)]:
        entry=next(c for c in listing(client,w) if c['entityId']==entity)
        response=client.post(API+'/worlds/'+w['world']+'/character-looks/'+entity+'/selection',headers={'Idempotency-Key':'adopt-'+entity},json={'sceneId':candidate['id'],'expectedVersion':entry['version']});assert response.status_code==200,response.text
    r=client.post(API+w['root']+'/scene',headers={'Idempotency-Key':'scene-with-looks'},json={'expectedCheckpointId':w['cp']});assert r.status_code==202,r.text
    view=r.json();assert view['descriptor'].get('characterLooks')
    assert people[0]['id'] not in view['descriptor']['characterLooks']
    # Corrective compositions rebuild the cast but retain the frozen look map.
    with SessionLocal() as db:
        row=db.get(SceneImage,view['id']);descriptor=deepcopy(row.descriptor)
        for person in descriptor['sceneCast']:person.pop('lookImageId',None)
        row.descriptor=descriptor;db.commit()
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier:
        assert process_one()
        kwargs=supplier.call_args.kwargs;assert len(kwargs['character_references'])==1
        descriptor=supplier.call_args.args[0]
        assert descriptor['characterReferenceOrder'][0]['entityId']==people[1]['id']
        assert descriptor['characterReferenceOrder'][0]['image']==2
    # A different selected NPC image changes the cache; the unseen player's does not.
    r2=client.post(API+w['root']+'/scene',headers={'Idempotency-Key':'same-scene'},json={'expectedCheckpointId':w['cp']});assert r2.json()['id']==view['id']
    third=enqueue(client,w,people[1]['id'],'npc-new',regenerate=True);finish()
    entry=next(c for c in listing(client,w) if c['entityId']==people[1]['id'])
    client.post(API+'/worlds/'+w['world']+'/character-looks/'+people[1]['id']+'/selection',headers={'Idempotency-Key':'npc-select'},json={'sceneId':third['id'],'expectedVersion':entry['version']})
    r3=client.post(API+w['root']+'/scene',headers={'Idempotency-Key':'updated-scene'},json={'expectedCheckpointId':w['cp']});assert r3.json()['id']!=view['id']

def test_library_authorization_and_world_delete_cascade(client,scene_world):
    w=scene_world;entity=cast(client,w)[0]['id'];a=enqueue(client,w,entity);finish()
    with SessionLocal() as db:
        original=db.get(World,w['world']);other=User(username='portrait-other',password_hash='none');db.add(other);db.flush()
        foreign=World(owner_id=other.id,name='别人',source_title='独立',source={},definition=deepcopy(original.definition),status='active');db.add(foreign);db.commit();foreign_id=foreign.id
    assert client.get(API+'/worlds/'+foreign_id+'/character-looks').status_code==404
    assert client.post(API+'/worlds/'+foreign_id+'/character-looks',headers={'Idempotency-Key':'foreign'},json={'entityId':entity}).status_code==404
    # Invalid entity and unavailable/pending art cannot be selected.
    assert client.post(API+'/worlds/'+w['world']+'/character-looks',headers={'Idempotency-Key':'missing'},json={'entityId':'missing'}).status_code==404
    with SessionLocal() as db:
        world=db.get(World,w['world']);db.delete(world);db.commit()
        assert db.get(SceneImage,a['id']) is None
        assert db.scalar(select(func.count()).select_from(CharacterLook).where(CharacterLook.world_id==w['world']))==0

def test_archiving_during_generation_prevents_publication(client,scene_world):
    w=scene_world;a=enqueue(client,w,cast(client,w)[0]['id'])
    def dispatch(*args,**kwargs):
        with SessionLocal() as db:db.get(World,w['world']).status='archived';db.commit()
        kwargs['authorize']();return generated()
    with patch('app.scenes.worker.generate',side_effect=dispatch):assert process_one()
    with SessionLocal() as db:
        assert db.get(SceneImage,a['id']).status=='failed'
        assert not db.scalar(select(CharacterLook)).selected_scene_id

def test_missing_portrait_file_does_not_stall_the_scene_queue(client,scene_world):
    from app.storage import private_path
    w=scene_world;entity=cast(client,w)[1]['id'];a=enqueue(client,w,entity);finish()
    with SessionLocal() as db:
        row=db.get(SceneImage,a['id']);private_path(row.owner_id,row.image['file']).unlink()
    response=client.post(API+w['root']+'/scene',headers={'Idempotency-Key':'missing-reference'},json={'expectedCheckpointId':w['cp']})
    assert response.status_code==202,response.text
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier:
        assert process_one();assert supplier.call_count==1
        assert not supplier.call_args.kwargs.get('character_references')
    with SessionLocal() as db:assert db.get(SceneImage,response.json()['id']).status=='ready'
