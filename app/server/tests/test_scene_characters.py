from copy import deepcopy
import io
from unittest.mock import patch
import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import Task,uid
from app.errors import Problem
from app.scenes.characters import encode_character, character_prompt
from app.scenes.models import SceneImage,SceneProfile
from app.scenes.service import scene_view
from app.scenes.dialogue import dialogue_segments
from app.scenes.layout import build_layout,hotspot_positions
from app.scenes.worker import process_one
from app.world.models import WorldCheckpoint,WorldLine,WorldTurn
from .test_scenes import scene_world,request,API


def test_spirit_appearance_is_distinct_without_changing_solid_character_identity():
    from app.scenes.characters import character_descriptor,character_key
    spirit=character_descriptor({'id':'yao','name':'药老','description':'寄居纳戒中的炼药师灵魂，淡淡投影。'},'斗破苍穹')
    assert spirit['presentation']=='spirit' and spirit['artVersion']==2
    assert 'white hair' in character_prompt(spirit)
    assert 'featureless solid green' in character_prompt(spirit)
    solid=character_descriptor({'id':'npc','name':'谷尼','description':'炼药师'},'斗破苍穹')
    assert solid['artVersion']==1 and 'presentation' not in solid
    assert character_key(spirit,'w',1)!=character_key({**spirit,'artVersion':1},'w',1)


def test_spirit_hem_fades_and_muted_green_background_is_fully_removed():
    image=Image.new('RGB',(300,450),'#486032');draw=ImageDraw.Draw(image)
    draw.ellipse((95,20,205,140),fill='#b59977');draw.rectangle((75,140,225,420),fill='#eeeeee')
    output=io.BytesIO();image.save(output,format='PNG')
    data,_=encode_character(output.getvalue(),presentation='spirit')
    with Image.open(io.BytesIO(data)) as cutout:
        assert cutout.width<200
        assert cutout.getpixel((0,0))[3]==0
        assert cutout.getpixel((cutout.width//2,cutout.height//2))[3]>200
        assert cutout.getpixel((cutout.width//2,cutout.height-1))[3]<10


def test_soft_green_halo_does_not_remain_on_pale_clothes():
    image=Image.new('RGB',(300,450),'#00ff00');draw=ImageDraw.Draw(image)
    draw.rectangle((60,20,240,420),fill='#6e9f60')
    draw.rectangle((75,20,225,420),fill='#eeeeee')
    output=io.BytesIO();image.save(output,format='PNG')
    data,_=encode_character(output.getvalue())
    with Image.open(io.BytesIO(data)) as cutout:
        assert cutout.width<=151
        assert cutout.getpixel((cutout.width//2,cutout.height//2))[3]==255


def image_fixture():
    image=Image.new('RGB',(300,450),'#00ff00');draw=ImageDraw.Draw(image)
    draw.ellipse((90,20,210,140),fill='#b59977');draw.rectangle((75,140,225,420),fill='#454438')
    output=io.BytesIO();image.save(output,format='PNG');return output.getvalue()


def reserve(client,w,key='character',entity=None,regenerate=False):
    if not entity:
        with SessionLocal() as db:entity=scene_view(db.get(WorldCheckpoint,w['cp']))['characters'][0]['id']
    return client.post(API+w['root']+'/scene/characters',json={'expectedCheckpointId':w['cp'],'entityId':entity,'regenerate':regenerate},headers={'Idempotency-Key':key})


def test_alpha_cutout_removes_background_and_retains_person():
    data,meta=encode_character(image_fixture());assert meta['transparent']
    with Image.open(io.BytesIO(data)) as image:
        assert image.mode=='RGBA';assert image.getpixel((0,0))[3]==0
        assert image.getpixel((image.width//2,image.height//2))[3]==255
        assert image.height>image.width
    plain=Image.new('RGB',(300,450),'#a89778');output=io.BytesIO();plain.save(output,format='PNG')
    with pytest.raises(Problem,match='透明背景'):encode_character(output.getvalue())


def test_opaque_studio_matte_is_removed_without_erasing_light_costume():
    image=Image.new('RGB',(300,450),'#fafafa');draw=ImageDraw.Draw(image)
    draw.ellipse((95,20,205,140),fill='#b59977');draw.rectangle((75,140,225,420),fill='#fafafa',outline='#454438',width=7)
    output=io.BytesIO();image.save(output,format='PNG');data,meta=encode_character(output.getvalue())
    with Image.open(io.BytesIO(data)) as opened:
        assert opened.getpixel((0,0))[3]==0
        assert opened.getpixel((opened.width//2,opened.height//2))[3]>240


def test_stage_directions_inside_speech_are_narration():
    assert dialogue_segments('柜台前空着。\n【台词】（谷尼合上匣盖）今日拍品多，柜台前说话便好。\n【旁白】他抬眼看向你。','谷尼')==[
        {'kind':'narration','text':'柜台前空着。\n\n谷尼合上匣盖'},
        {'kind':'speech','text':'今日拍品多，柜台前说话便好。'},
        {'kind':'narration','text':'他抬眼看向你。'}]


def test_character_reservation_deduplicates_and_worker_keeps_alpha(client,scene_world):
    w=scene_world;a=reserve(client,w);assert a.status_code==202,a.text
    b=reserve(client,w,'second-tab');assert a.json()['id']==b.json()['id']
    data,meta=encode_character(image_fixture())
    with patch('app.scenes.worker.generate',return_value=(data,meta,'character-fixture')) as provider:
        assert process_one();assert not process_one();assert provider.call_count==1
        descriptor=provider.call_args.args[0];assert descriptor['kind']=='character'
        assert 'ONE individual' in character_prompt(descriptor)
    result=client.get(API+'/scenes/'+a.json()['id']).json();assert result['status']=='ready'
    image=client.get(result['imageUrl']);assert image.status_code==200
    with Image.open(io.BytesIO(image.content)) as opened:assert opened.mode=='RGBA'
    with SessionLocal() as db:
        view=scene_view(db.get(WorldCheckpoint,w['cp']))
        # Legacy sprite files remain downloadable, while current cast artwork
        # comes from the explicit portrait library rather than this sprite.
        assert a.json()['entityId'] not in view['characterArt']


def test_character_reuses_identity_art_across_lines_and_rejects_absent_target(client,scene_world):
    w=scene_world;a=reserve(client,w).json()
    with SessionLocal() as db:
        original=db.get(WorldLine,w['line']);other=WorldLine(owner_id=w['owner'],world_id=w['world'],name='另一条线',head_checkpoint_id=w['cp'],status='active');db.add(other);db.flush();line=other.id;db.commit()
    assert reserve(client,{**w,'root':f"/worlds/{w['world']}/lines/{line}"},'another-line').json()['id']==a['id']
    assert reserve(client,w,'not-here','unknown-person').status_code==409
    assert reserve(client,{**w,'cp':'f'*32},'stale',a['entityId']).status_code==409


def test_unknown_character_request_never_auto_retries_and_quota_is_shared(client,scene_world):
    w=scene_world;a=reserve(client,w).json()
    with patch('app.scenes.worker.generate',side_effect=Problem('SCENE_STATUS_UNKNOWN','未知')) as provider:
        assert process_one();assert not process_one();assert provider.call_count==1
    assert reserve(client,w,'refresh').json()['id']==a['id']
    with SessionLocal() as db:profile=db.scalar(select(SceneProfile));profile.daily_limit=1;db.commit()
    assert reserve(client,w,'manual-retry',regenerate=True).status_code==429
    assert client.post(API+w['root']+'/scene',json={'expectedCheckpointId':w['cp']},headers={'Idempotency-Key':'same-quota'}).status_code==429


def test_scene_exposes_received_opening_draft_without_accepting_it(client,scene_world):
    w=scene_world;text='【台词】（陆遥放下茶碗）你好，我们一道走。'
    with SessionLocal() as db:
        task=Task(owner_id=w['owner'],task_type='world_turn',state='failed',payload={},error_code='PROVIDER_STATUS_UNKNOWN');db.add(task);db.flush()
        turn=WorldTurn(owner_id=w['owner'],world_id=w['world'],line_id=w['line'],parent_checkpoint_id=w['cp'],task_id=task.id,turn_key='opening',action='观察眼前处境',kind='observe',draft_text=text,status='draft');db.add(turn);db.commit();turn_id=turn.id
        view=scene_view(db.get(WorldCheckpoint,w['cp']));reply=next(r for r in view['responses'] if r['id']==turn_id)
        assert reply['text']==text and reply['draft'] and reply['partnerId'] is None
        assert db.get(WorldLine,w['line']).head_checkpoint_id==w['cp']
        assert db.get(WorldTurn,turn_id).status=='draft'
        other=WorldLine(owner_id=w['owner'],world_id=w['world'],name='另一条线',head_checkpoint_id=w['cp'],status='active');db.add(other);db.flush()
        assert not any(r['id']==turn_id for r in scene_view(db.get(WorldCheckpoint,w['cp']),other.id)['responses'])
        db.commit()
    walked=client.post(API+w['root']+'/scene/move',json={'expectedCheckpointId':w['cp'],'targetKind':'point','targetId':w['bench']},headers={'Idempotency-Key':'walk-after-opening'})
    assert walked.status_code==200,walked.text
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,walked.json()['checkpointId']);assert cp.kind=='scene_move'
        assert any(r['id']==turn_id for r in scene_view(cp)['responses'])


def test_grounded_feet_and_old_panorama_horizon_are_corrected(client,scene_world):
    w=scene_world;panorama=request(client,w)
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);view=scene_view(cp);person=view['characters'][0]['id'];spot=view['hotspots'][person]
        assert spot['pitch']<-.5 and 1.8<=spot['distance']<=3
        row=db.get(SceneImage,panorama['id']);descriptor=deepcopy(row.descriptor);descriptor['hotspots'][person]={'yaw':0,'pitch':0};row.descriptor=descriptor;db.commit()
    refreshed=client.get(API+'/scenes/'+panorama['id']).json()
    assert refreshed['hotspots'][person]['pitch']<-.1


def test_counter_character_feet_are_in_foreground_floor_not_at_counter_height():
    place={'id':'auction','name':'拍卖场大厅','description':'柜台前有通路，大门敞开。'}
    layout=build_layout(place,[],place['description'])
    point=layout['nodes'][0]
    people=[{'id':str(index),'type':'character','name':'鉴定师','description':'柜台旁的炼药师'} for index in range(6)]
    spots=hotspot_positions(layout,point,people,[])
    assert all(spot['pitch']<-.75 for spot in spots.values())
    assert all(spot['height']>1.5 and spot['distance']<3 for spot in spots.values())
