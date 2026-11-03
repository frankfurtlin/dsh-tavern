"""Paired images must be explicit, scoped, portable and canon-neutral."""
import base64,io
from copy import deepcopy
from PIL import Image
from sqlalchemy import select
from app.db import SessionLocal
from app.scenes.models import CharacterArtwork,CharacterLook
from app.world.models import WorldCheckpoint,WorldLine
from .test_scenes import scene_world,API
from .test_character_looks import cast,enqueue,finish,listing

def upload_body(entity,version=0):
    image=Image.new('RGB',(200,300),'#486581');raw=io.BytesIO();image.save(raw,format='PNG')
    return {'entityId':entity,'expectedVersion':version,'imageDataUrl':'data:image/png;base64,'+base64.b64encode(raw.getvalue()).decode(),
        'crop':{'x':30,'y':10,'width':120,'height':120},'rightsConfirmed':True,'licenseNote':'Synthetic owned fixture'}

def test_uploaded_pair_is_idempotent_and_does_not_change_canon(client,scene_world):
    w=scene_world;entity=cast(client,w)[0]['id']
    with SessionLocal() as db:
        original=deepcopy(db.get(WorldCheckpoint,w['cp']).state);head=db.get(WorldLine,w['line']).head_checkpoint_id
    path=API+'/worlds/'+w['world']+'/character-looks/upload';headers={'Idempotency-Key':'upload-avatar-pair'};body=upload_body(entity)
    a=client.post(path,json=body,headers=headers);assert a.status_code==201,a.text
    b=client.post(path,json=body,headers=headers);assert a.json()==b.json()
    art=a.json()['artwork'];assert art['id']==a.json()['selectedId']
    full=client.get(art['imageUrl']);avatar=client.get(art['avatarUrl']);assert full.status_code==avatar.status_code==200
    with Image.open(io.BytesIO(avatar.content)) as image:assert image.size==(512,512)
    with SessionLocal() as db:
        assert db.get(WorldCheckpoint,w['cp']).state==original and db.get(WorldLine,w['line']).head_checkpoint_id==head
        assert db.get(CharacterArtwork,art['id']).entity_id==entity
    bad=client.post(path,json=body,headers={'Idempotency-Key':'stale-pair'});assert bad.status_code==409

def test_ai_candidate_is_not_adopted_before_explicit_pair_selection(client,scene_world):
    w=scene_world;entity=cast(client,w)[0]['id'];candidate=enqueue(client,w,entity);finish()
    entry=next(e for e in listing(client,w) if e['entityId']==entity);assert entry['selectedId'] is None
    picked=client.post(API+'/worlds/'+w['world']+'/character-looks/'+entity+'/selection',json={'sceneId':candidate['id'],'expectedVersion':entry['version']},headers={'Idempotency-Key':'adopt-pair'})
    assert picked.status_code==200,picked.text;assert picked.json()['artwork']['avatarUrl']
    entry=next(e for e in listing(client,w) if e['entityId']==entity);assert entry['selectedId']==picked.json()['selectedId']

def test_cross_role_selection_and_invalid_crop_never_replace_selected(client,scene_world):
    w=scene_world;a,b=[e['id'] for e in cast(client,w)[:2]]
    response=client.post(API+'/worlds/'+w['world']+'/character-looks/upload',json=upload_body(a),headers={'Idempotency-Key':'role-a'});assert response.status_code==201,response.text
    art=response.json()['selectedId'];endpoint=API+'/worlds/'+w['world']+'/character-looks/'+b+'/selection'
    wrong=client.post(endpoint,json={'sceneId':art,'expectedVersion':0},headers={'Idempotency-Key':'wrong-role'});assert wrong.status_code==404
    bad=upload_body(a,response.json()['version']);bad['crop']['x']=1000
    invalid=client.post(API+'/worlds/'+w['world']+'/character-looks/upload',json=bad,headers={'Idempotency-Key':'invalid-crop'});assert invalid.status_code==422
    assert next(e for e in listing(client,w) if e['entityId']==a)['selectedId']==art


def test_custom_player_appearance_uses_the_viewed_branch_without_switching_mainline(client,scene_world):
    from app.world.models import World
    from app.world.state import initial_state,checkpoint_hash
    from app.models import uid
    w=scene_world
    with SessionLocal() as db:
        world=db.get(World,w['world']);world.active_line_id=w['line']
        definition,state=initial_state(deepcopy(world.definition),None,'分支访客','独立分支的新人物')
        entity=state['playerId'];identity=uid()
        line=WorldLine(owner_id=w['owner'],world_id=world.id,name='另一条线',head_checkpoint_id=identity);db.add(line);db.flush()
        db.add(WorldCheckpoint(id=identity,owner_id=w['owner'],world_id=world.id,line_id=line.id,sequence=1,title='分支',state=state,
            definition=definition,text='',content_hash=checkpoint_hash(state,definition,'',None),options=[],changes=[]))
        db.commit();line_id=line.id
    endpoint=API+'/worlds/'+w['world']+'/character-looks'
    assert entity not in {e['entityId'] for e in client.get(endpoint).json()['characters']}
    assert entity in {e['entityId'] for e in client.get(endpoint+'?lineId='+line_id).json()['characters']}
    body={**upload_body(entity),'lineId':line_id}
    result=client.post(endpoint+'/upload',json=body,headers={'Idempotency-Key':'branch-face'});assert result.status_code==201,result.text
    assert client.get(endpoint+'?lineId='+'0'*32).status_code==404
    with SessionLocal() as db:
        assert db.get(World,w['world']).active_line_id==w['line']
        assert db.get(WorldCheckpoint,identity).state==state


def test_migration_refuses_to_drop_saved_artwork(client,scene_world):
    import pytest
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    w=scene_world;entity=cast(client,w)[0]['id']
    response=client.post(API+'/worlds/'+w['world']+'/character-looks/upload',json=upload_body(entity),headers={'Idempotency-Key':'migration-preserve-face'})
    assert response.status_code==201,response.text
    with pytest.raises(RuntimeError,match='Cannot downgrade saved character artworks'):command.downgrade(Config('alembic.ini'),'0021')
    with SessionLocal() as db:assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0022'
    assert client.get(response.json()['artwork']['avatarUrl']).status_code==200


def test_character_upload_is_bounded_before_json_body_parsing(client,scene_world):
    path=API+'/worlds/'+scene_world['world']+'/character-looks/upload'
    response=client.post(path,content=b' '*(4*1024*1024+16385),headers={'Content-Type':'application/json'})
    assert response.status_code==413 and response.json()['code']=='PRODUCT_BODY_LIMIT'
    def chunks():
        for _ in range(5):yield b' '*(1024*1024)
    response=client.post(path,content=chunks(),headers={'Content-Type':'application/json'})
    assert response.status_code==413 and response.json()['code']=='PRODUCT_BODY_LIMIT'


def test_old_generation_and_selection_receipts_replay_without_new_calls_or_replacement(client,scene_world):
    from datetime import timedelta
    from app.models import Idempotency,now
    from app.domain import canonical,digest
    from app.scenes.models import SceneImage
    from sqlalchemy import func
    w=scene_world;entity=cast(client,w)[0]['id'];candidate=enqueue(client,w,entity);finish()
    entry=next(e for e in listing(client,w) if e['entityId']==entity)
    payload={'entityId':entity,'appearance':{'age':'','hair':'','clothing':'','temperament':''},'regenerate':False}
    generated={'id':candidate['id'],'status':'ready'}
    selected={'worldId':w['world'],'entityId':entity,'selectedId':candidate['id'],'version':entry['version']+1}
    selection={'sceneId':candidate['id'],'expectedVersion':entry['version']}
    with SessionLocal() as db:
        choice=db.scalar(select(CharacterLook).where(CharacterLook.world_id==w['world'],CharacterLook.entity_id==entity))
        choice.selected_scene_id=candidate['id'];choice.version=selected['version']
        for scope,key,body,result in [('character-look:'+w['world'],'legacy-generation',payload,generated),('character-look-selection:'+w['world']+':'+entity,'legacy-selection',selection,selected)]:
            db.add(Idempotency(owner_id=w['owner'],scope='world:'+scope,key=key,payload_hash=digest(canonical(body)),result=result,expires_at=now()+timedelta(days=1)))
        db.commit()
    endpoint=API+'/worlds/'+w['world']+'/character-looks'
    assert client.post(endpoint,json=payload,headers={'Idempotency-Key':'legacy-generation'}).json()==generated
    assert client.post(endpoint+'/'+entity+'/selection',json=selection,headers={'Idempotency-Key':'legacy-selection'}).json()==selected
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SceneImage))==1
        assert db.scalar(select(func.count()).select_from(CharacterArtwork))==0
