from copy import deepcopy
from unittest.mock import patch
import pytest
from app.db import SessionLocal
from app.world.models import WorldCheckpoint
from app.world.schemas import DirectorOutput, EntityDefinition
from app.world.state import checkpoint_hash
from app.world.worker import process
from app.scenes.service import scene_view
from .test_scenes import scene_world
from .test_scene_interaction import executable_scene_source, body, provider, API


def setup_exits(w, *, text=None, allowed=True):
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);definition=deepcopy(cp.definition);state=deepcopy(cp.state)
        here=state['location'];place=next(e for e in definition['entities'] if e['id']==here)
        place['description']='露天练武场。后山石阶接入场边，另一侧的敞开院门通往正厅。'
        for key,name,alias in [('mountain','萧家后山','后山'),('hall','萧家正厅','正厅'),('secret','HIDDEN_DESTINATION','地下密室')]:
            definition['entities'].append(EntityDefinition(id=key,name=name,aliases=[alias],type='place',source='game',description='HIDDEN_DESTINATION_DETAIL').model_dump())
            state['entities'][key]={'alive':None,'location':None,'fields':{},'inventory':{},'relations':{},'known':[]}
        definition['rules']['routes']={here:['mountain','hall','secret'] if allowed else [],'mountain':[here],'hall':[here]}
        cp.definition=definition;cp.state=state;cp.text=text or '你看见后山的石阶蜿蜒而上，另一侧院门敞开，可直通正厅。'
        cp.content_hash=checkpoint_hash(state,definition,cp.text,cp.parent_checkpoint_id);db.commit()


def test_public_unvisited_routes_are_clickable_without_exposing_hidden_destinations(client,scene_world):
    w=scene_world;setup_exits(w)
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);before=deepcopy(cp.state);view=scene_view(cp)
        exits={e['id']:e for e in view['exits']}
        assert set(exits)=={'mountain','hall'}
        assert exits['mountain']['name']=='后山' and exits['hall']['name']=='正厅'
        assert 'HIDDEN_DESTINATION' not in str(view) and '地下密室' not in str(view)
        assert cp.state==before and not {'mountain','hall'}&set(cp.state['discovered'])


def test_unvisited_exit_uses_normal_director_and_discovers_only_after_arrival(client,scene_world,profile):
    w=scene_world;setup_exits(w)
    response=client.post(API+w['root']+'/scene/travel',json=body(w,'前往后山',sceneExitId='mountain'),headers={'Idempotency-Key':'unvisited-exit'})
    assert response.status_code==202,response.text
    plan=DirectorOutput(title='后山',elapsedMinutes=3,timeLabel='清晨',location='mountain',publicOutcome='你沿石阶到达后山。').model_dump()
    with patch('app.world.worker.call_provider',side_effect=provider(plan,'你沿石阶到达后山。')) as calls:process(response.json()['taskId'])
    assert client.get(API+'/tasks/'+response.json()['taskId']).json()['state']=='succeeded'
    sent=calls.call_args_list[0].args[3]
    assert next(e['name'] for e in sent['registry'] if e['id']==sent['sceneIntent']['destinationId'])=='萧家后山'
    head=client.get(API+w['root']).json()['head']
    assert head['state']['location']=='mountain'
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,head['id']);assert 'mountain' in cp.state['discovered'] and 'hall' not in cp.state['discovered']


def test_explicit_route_closure_still_hides_visible_doorways(client,scene_world):
    setup_exits(scene_world,allowed=False)
    with SessionLocal() as db:assert not scene_view(db.get(WorldCheckpoint,scene_world['cp']))['exits']


def test_compiled_script_exposes_adjacent_route_names_without_future_scene_contents(client,scene_world):
    setup_exits(scene_world)
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,scene_world['cp']);definition=deepcopy(cp.definition)
        here=cp.state['location'];definition['rules']['routes'][here]=['hall']
        definition['script']={'objectives':[],'endings':[]}
        next(e for e in definition['entities'] if e['id']==here)['description']='楼梯通向临街大门。'
        cp.definition=definition;cp.text='屋里炉火明亮。'
        before=deepcopy(cp.state);view=scene_view(cp)
        assert {e['id'] for e in view['exits']}=={'hall'}
        assert 'HIDDEN_DESTINATION_DETAIL' not in str(view) and cp.state==before
        definition['rules']['routes'][here]=[];cp.definition=definition
        assert not scene_view(cp)['exits']


def test_household_short_names_work_even_when_import_did_not_supply_aliases(client,scene_world):
    setup_exits(scene_world)
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,scene_world['cp']);definition=deepcopy(cp.definition)
        for e in definition['entities']:
            if e['id']==cp.state['location']:e['name']='萧家练武场'
            elif e['id'] in ('mountain','hall'):e['aliases']=[]
        cp.definition=definition
        assert {e['id'] for e in scene_view(cp)['exits']}=={'mountain','hall'}


@pytest.mark.parametrize('text',['你记得以前有一条路通往地下密室。','听说那条走廊通往地下密室。','如果明天出发，可以沿路通往地下密室。'])
def test_recollections_and_future_plans_do_not_reveal_a_hidden_route(client,scene_world,text):
    setup_exits(scene_world,text=text)
    with SessionLocal() as db:
        view=scene_view(db.get(WorldCheckpoint,scene_world['cp']))
        assert 'secret' not in {e['id'] for e in view['exits']}
