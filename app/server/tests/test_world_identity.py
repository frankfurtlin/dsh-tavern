"""Selected identities and public prose on the isolated DB, no paid requests."""
from copy import deepcopy
import json
from unittest.mock import patch
import pytest
from app.db import SessionLocal
from app.world.models import World, WorldLine, WorldCheckpoint
from app.world.schemas import DirectorOutput
from app.world.state import initial_state, checkpoint_hash
from app.world.identity import actor_contract, identity_conflict
from app.world.output import story_text, narrative_text, narrator_prompt_name
from app.world.memory import context, narrator_context
from app.world.worker import process
from app.scenes.service import scene_view
from .test_scenes import scene_world
from .test_scene_interaction import executable_scene_source, body, provider, API


@pytest.mark.parametrize('selected',['萧炎','萧薰儿','自定义旅人'])
def test_selected_player_is_explicit_at_both_model_stages(client, scene_world, selected):
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,scene_world['cp']);world=db.get(World,cp.world_id)
        definition=deepcopy(cp.definition)
        people=[e for e in definition['entities'] if e['type']=='character']
        people[0]['name']='萧炎';people[1]['name']='萧薰儿'
        if selected=='自定义旅人':definition,state=initial_state(definition,None,selected,'来自城外的旅人')
        else:definition,state=initial_state(definition,next(e['id'] for e in people if e['name']==selected))
        cp.state=state;cp.definition=definition;cp.text='你看着石阶。'
        data=context(db,cp,world,'观察',path=[cp],state=state)
        plan=DirectorOutput(title='清晨',elapsedMinutes=1,timeLabel='清晨',publicOutcome='你看见石阶。').model_dump()
        narration=narrator_context(cp,state,definition,plan,'观察')
        for stage in (data,narration):
            assert stage['actors']['player']['id']==state['playerId']
            assert stage['actors']['player']['name']==selected
            assert state['playerId'] not in {e['id'] for e in stage['actors']['presentNPCs']}
        assert selected not in {e['name'] for e in scene_view(cp)['characters']}


@pytest.mark.parametrize('text',[
    '萧薰儿倚在石碑旁，见你停下脚步，她便直起身，朝你招呼。',
    '你在练武场遇见萧薰儿。', '【台词：萧薰儿】你来了。',
    '萧薰儿抬头打量你一眼。'])
def test_self_npc_is_an_identity_conflict(text):
    assert identity_conflict(text,{'player':{'id':'x','name':'萧薰儿','aliases':[]}})


@pytest.mark.parametrize('text',[
    '你低声说：“我是萧薰儿。”', '萧薰儿想起了小时候的事。',
    '你看见石碑刻着“萧薰儿”三个字。', '你对着镜子自言自语。',
    '萧炎朝你招手，问：“薰儿，你来练功吗？”'])
def test_normal_self_reference_and_other_npc_remain_valid(text):
    assert not identity_conflict(text,{'player':{'id':'x','name':'萧薰儿','aliases':[]}})


def test_protocol_preface_never_flashes_during_stream_and_english_is_preserved():
    preface='outputMode is "dialogue", so I\'ll use the dialogue format.\n\n'
    for index in range(1,len(preface)+1):assert story_text(preface[:index],streaming=True)==''
    assert story_text(preface+'【旁白】你看见石阶。')=='【旁白】你看见石阶。'
    assert story_text('“outputMode is dialogue”，纸条上写着。')=='“outputMode is dialogue”，纸条上写着。'
    assert story_text('Welcome to the city.\n\n你推开门。')=='Welcome to the city.\n\n你推开门。'
    assert narrator_prompt_name('world_narrator',{})=='world_narrator'
    assert narrator_prompt_name('world_narrator',{'outputMode':'dialogue'})=='world_dialogue'
    assert narrative_text('【旁白】林掌柜抬头。\n【台词：林掌柜】小少爷要点什么？')=='林掌柜抬头。\n\n林掌柜说：“小少爷要点什么？”'


def test_bad_old_opening_is_not_reused_as_model_history(client,scene_world):
    from app.world.models import WorldEvent
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,scene_world['cp']);world=db.get(World,cp.world_id)
        actor=actor_contract(cp.state,cp.definition)['player']
        cp.text=f"{actor['name']}朝你招手。"
        db.add(WorldEvent(owner_id=cp.owner_id,world_id=cp.world_id,checkpoint_id=cp.id,
            entity_id=actor['id'],type='对话',data={'id':'bad-old-event','type':'对话',
                'entityId':actor['id'],'description':'你来了。','visibleTo':['player']},visible_to=['player'],minute=0))
        db.flush()
        data=context(db,cp,world,'观察',path=[cp],state=cp.state)
        assert data['recentScenes']==[]
        assert data['recalledEvents']==[]
        assert narrator_context(cp,cp.state,cp.definition,{'publicOutcome':'你看见山道。','title':'山道'},'观察')['recentText']==''


def test_self_npc_reply_is_a_visible_draft_and_never_committed(client,scene_world,profile):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);name=actor_contract(cp.state,cp.definition)['player']['name']
    response=client.post(API+w['root']+'/actions',json=body(w,'环顾四周','observe'),headers={'Idempotency-Key':'self-npc'})
    assert response.status_code==202,response.text
    job=response.json()
    plan=DirectorOutput(title='清晨',elapsedMinutes=1,timeLabel='清晨',publicOutcome='你看见石阶。').model_dump()
    with patch('app.world.worker.call_provider',side_effect=provider(plan,f'{name}朝你招手。')):process(job['taskId'])
    receipt=client.get(API+'/tasks/'+job['taskId']).json()
    assert receipt['errorCode']=='WORLD_PLAYER_IDENTITY_CONFLICT'
    assert name in receipt['stream']['text']
    assert client.get(API+w['root']).json()['headCheckpointId']==w['cp']


def test_actual_arrival_registers_npc_and_clean_prose_atomically(client,scene_world,profile):
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);npc=next(e for e in cp.definition['entities'] if e['type']=='character' and e['id']!=cp.state['playerId'])
        person_id,person_name=npc['id'],npc['name'];location=cp.state['location']
        state=deepcopy(cp.state);state['entities'][person_id]['location']=None;cp.state=state
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,cp.parent_checkpoint_id);db.commit()
    response=client.post(API+w['root']+'/actions',json=body(w,'环顾四周','observe'),headers={'Idempotency-Key':'arriving-npc'})
    assert response.status_code==202,response.text
    job=response.json()
    plan=DirectorOutput(title='相遇',elapsedMinutes=1,timeLabel='清晨',publicOutcome=f'{person_name}来到你面前，向你问好。',
        updates=[{'entityId':person_id,'field':'location','value':location,'reason':'从外面走进来','visibleTo':['player']}]).model_dump()
    preface='outputMode is "dialogue", so I\'ll use the dialogue format.\n\n'
    text=f'【旁白】{person_name}走进客舍。\n【台词：{person_name}】你好。'
    with patch('app.world.worker.call_provider',side_effect=provider(plan,preface+text)):process(job['taskId'])
    assert client.get(API+'/tasks/'+job['taskId']).json()['state']=='succeeded'
    head=client.get(API+w['root']).json()['head']
    assert 'outputMode' not in head['text'] and '【台词' not in head['text']
    assert person_name+'说：“你好。”' in head['text']
    with SessionLocal() as db:
        view=scene_view(db.get(WorldCheckpoint,head['id']))
        assert person_id in {p['id'] for p in view['characters']}
