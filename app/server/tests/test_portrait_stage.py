"""Portraits follow the public roster and never need a paid image inspection."""
import io
from copy import deepcopy
from PIL import Image
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import Task,uid
from app.world.models import World,WorldCheckpoint,WorldLine
from app.scenes.models import SceneImage,CharacterArtwork
from app.scenes.artworks import prepare_pair,create_pair,choose
from app.scenes.service import scene_view,save_image
from app.scenes.portrait_stage import background_signature,background_basis,compatible_visit
from .test_scenes import scene_world,API,generated


def sprite():
    out=io.BytesIO();image=Image.new('RGBA',(200,300),(12,80,170,0))
    for x in range(40,160):
        for y in range(20,280):image.putpixel((x,y),(80,140,200,245))
    image.save(out,format='PNG');return out.getvalue()


def test_transparent_body_and_avatar_keep_real_alpha_after_storage_encoding():
    body,face,metadata=prepare_pair(sprite())
    for raw in (body,face):
        image=Image.open(io.BytesIO(raw));assert image.mode=='RGBA'
        assert image.getchannel('A').getextrema()[0]==0
    assert metadata['transparent'] is True


def setup_stage(w):
    with SessionLocal() as db:
        world=db.get(World,w['world']);cp=db.get(WorldCheckpoint,w['cp']);view=scene_view(cp,w['line'])
        person=view['characters'][0]
        neutral=create_pair(db,w['owner'],world,person['id'],sprite(),None,'ai_generated','Synthetic owned fixture',person)
        other=create_pair(db,w['owner'],world,person['id'],sprite(),None,'ai_generated','Synthetic owned fixture',person)
        choose(db,w['owner'],world.id,person['id'],neutral.id,0,line_id=w['line'])
        pixels,metadata,_=generated()
        picture=SceneImage(id=uid(),owner_id=w['owner'],world_id=w['world'],line_id=w['line'],checkpoint_id=w['cp'],
            profile_id=db.scalar(select(__import__('app.scenes.models',fromlist=['SceneProfile']).SceneProfile.id)),profile_version=1,
            scene_hash='a'*64,generation=1,status='ready',descriptor={'renderStage':'environment','sceneCast':[],
                'placeId':view['descriptor']['placeId']},image={})
        db.add(picture);db.flush();picture.image=save_image(picture,pixels,metadata,None)
        world.source={**world.source,'portraitStage':{
            'sets':{person['id']:{'baseArtworkId':neutral.id,'expressions':{'neutral':neutral.id,'smile':other.id}}},
            'backgrounds':{view['descriptor']['placeId']:{'sceneId':picture.id,'sha256':picture.image['sha256'],
                'signature':background_signature(view)}}}}
        db.commit();return person['id'],neutral.id,other.id,picture.id


def test_public_stage_read_and_background_are_canon_neutral_and_submit_no_model_jobs(client,scene_world):
    w=scene_world;person,neutral,other,picture=setup_stage(w)
    with SessionLocal() as db:
        state=deepcopy(db.get(WorldCheckpoint,w['cp']).state)
        head=db.get(WorldLine,w['line']).head_checkpoint_id
        count=db.scalar(select(func.count()).select_from(Task))
    response=client.get(API+w['root']+'/portrait-scene');assert response.status_code==200,response.text
    value=response.json();role=next(p for p in value['characters'] if p['id']==person)
    assert role['expressions']['neutral'].endswith(neutral+'/image')
    assert role['expressions']['smile'].endswith(other+'/image')
    assert all(p['id']!=state['playerId'] for p in value['characters'])
    assert client.get(value['background']['url']).status_code==200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task))==count
        assert db.get(WorldCheckpoint,w['cp']).state==state
        assert db.get(WorldLine,w['line']).head_checkpoint_id==head


def test_world_exposes_integrated_dialogue_capability_without_a_separate_play_mode(client,scene_world):
    w=scene_world
    assert client.get(API+'/worlds/'+w['world']).json()['portraitDialogue'] is False
    setup_stage(w)
    response=client.get(API+'/worlds/'+w['world'])
    assert response.status_code==200,response.text
    assert response.json()['portraitDialogue'] is True


def test_unknown_or_stale_background_does_not_prevent_loading_current_people(client,scene_world):
    w=scene_world;setup_stage(w)
    with SessionLocal() as db:
        world=db.get(World,w['world']);source=deepcopy(world.source)
        for item in source['portraitStage']['backgrounds'].values():item['sha256']='b'*64
        world.source=source;db.commit()
    r=client.get(API+w['root']+'/portrait-scene');assert r.status_code==200
    assert r.json()['characters'] and r.json()['background'] is None
    assert client.get(API+w['root']+'/portrait-scene/background').status_code==404


def test_expression_cannot_use_another_role_and_stale_sets_do_not_override_a_new_selection(client,scene_world):
    w=scene_world;person,neutral,other,picture=setup_stage(w)
    with SessionLocal() as db:
        wrong=db.get(CharacterArtwork,other);wrong.entity_id='another-role';db.commit()
    role=next(p for p in client.get(API+w['root']+'/portrait-scene').json()['characters'] if p['id']==person)
    assert 'smile' not in role['expressions']
    with SessionLocal() as db:
        world=db.get(World,w['world']);new=create_pair(db,w['owner'],world,person,sprite(),None,'ai_generated','Fixture',{'name':'new'})
        choose(db,w['owner'],w['world'],person,new.id,1,line_id=w['line']);db.commit();new_id=new.id
    role=next(p for p in client.get(API+w['root']+'/portrait-scene').json()['characters'] if p['id']==person)
    assert set(role['expressions'])=={'neutral'} and new_id in role['expressions']['neutral']


def test_stage_and_image_require_authenticated_owned_world_and_line(client,scene_world):
    w=scene_world;setup_stage(w)
    assert client.get(API+'/worlds/'+'f'*32+'/lines/'+w['line']+'/portrait-scene').status_code==404
    client.headers.pop('Authorization')
    assert client.get(API+w['root']+'/portrait-scene').status_code==401
    assert client.get(API+w['root']+'/portrait-scene/background').status_code==401


def test_only_actual_routes_receive_pixel_bound_author_markers(client,scene_world):
    w=scene_world
    with SessionLocal() as db:
        from app.world.state import checkpoint_hash
        cp=db.get(WorldCheckpoint,w['cp']);cp.text+='你看见通往门外的房门。'
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None);db.commit()
    setup_stage(w)
    with SessionLocal() as db:
        world=db.get(World,w['world']);view=scene_view(db.get(WorldCheckpoint,w['cp']),w['line'])
        target=view['exits'][0];source=deepcopy(world.source)
        config=source['portraitStage']['backgrounds'][view['descriptor']['placeId']]
        config['exits']={target['id']:{'yaw':1.2,'pitch':-.15,'sha256':config['sha256'],'source':'author'},
            'forged-unreachable-place':{'yaw':0,'pitch':0,'sha256':config['sha256'],'source':'author'}}
        world.source=source;db.commit()
    result=client.get(API+w['root']+'/portrait-scene').json()
    assert 'scenes' not in result
    assert {p['id'] for p in result['exits']}=={p['id'] for p in view['exits']}
    bound=next(p for p in result['exits'] if p['id']==target['id'])
    assert bound['imageBound'] is True and bound['yaw']==1.2 and bound['markerSource']=='author'
    with SessionLocal() as db:
        world=db.get(World,w['world']);source=deepcopy(world.source)
        config=next(iter(source['portraitStage']['backgrounds'].values()))
        config['exits'][target['id']]['sha256']='c'*64;world.source=source;db.commit()
    result=client.get(API+w['root']+'/portrait-scene').json()
    assert result['background'] is not None
    assert not any(p['imageBound'] for p in result['exits'])


def test_owned_authored_background_can_be_shared_across_lines_only_explicitly(client,scene_world):
    w=scene_world;setup_stage(w)
    with SessionLocal() as db:
        line=WorldLine(owner_id=w['owner'],world_id=w['world'],name='另一个存档',head_checkpoint_id=w['cp'],status='active')
        db.add(line);db.flush();other=line.id;db.commit()
    root=API+'/worlds/'+w['world']+'/lines/'+other+'/portrait-scene'
    assert client.get(root).json()['background'] is None
    with SessionLocal() as db:
        world=db.get(World,w['world']);source=deepcopy(world.source)
        for config in source['portraitStage']['backgrounds'].values():config['shareWithinWorld']=True
        world.source=source;db.commit()
    assert client.get(root).json()['background'] is not None
    assert client.get(root+'/background').status_code==200


def test_visit_reuse_rejects_changed_environment_new_or_removed_fixtures_and_visual_facts(client,scene_world):
    w=scene_world
    with SessionLocal() as db:view=scene_view(db.get(WorldCheckpoint,w['cp']),w['line'])
    config={'shareWithinWorld':True,'visitBasis':background_basis(view)}
    assert compatible_visit(config,view)
    changed=deepcopy(view);changed['descriptor']['lighting']='深夜'
    assert not compatible_visit(config,changed)
    changed=deepcopy(view);changed['descriptor']['visualFacts']={'requirements':[{'id':'no-live-goose'}]}
    assert not compatible_visit(config,changed)
    changed=deepcopy(view);changed['layout']['nodes'].append({'id':'new-prop','fixture':'table','worldX':1,'worldZ':2})
    assert not compatible_visit(config,changed)
    fixture=next(p for p in view['layout']['nodes'] if p.get('fixture'))
    changed=deepcopy(view);changed['layout']['removedFixtures']=[fixture['fixture']]
    assert not compatible_visit(config,changed)


def test_outdoor_route_synonyms_do_not_invalidate_authored_paths_but_new_furniture_does(client,scene_world):
    w=scene_world
    with SessionLocal() as db:view=scene_view(db.get(WorldCheckpoint,w['cp']),w['line'])
    view['descriptor']['spaceKind']='exterior'
    view['exits']=[{'id':'same-legal-road','portalId':None}]
    config={'shareWithinWorld':True,'visitBasis':background_basis(view)}
    changed=deepcopy(view);changed['exits'][0]['portalId']='new-word-for-path'
    changed['layout']['nodes'].append({'id':'new-word-for-path','fixture':'entrance','worldX':0,'worldZ':3})
    assert compatible_visit(config,changed)
    changed['layout']['nodes'].append({'id':'new-building-prop','fixture':'counter','worldX':1,'worldZ':2})
    assert not compatible_visit(config,changed)
