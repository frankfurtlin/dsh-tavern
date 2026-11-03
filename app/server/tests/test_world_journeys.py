from copy import deepcopy
import pytest
from app.errors import Problem
from app.world.state import initial_state,apply_plan
from app.world.routes import journey_path
from .test_world import definition,plan


def route_world():
    d=definition()
    for key in ('yard','hall','street'):
        d['entities'].append({**deepcopy(next(e for e in d['entities'] if e['id']=='pier')),'id':key,'name':key})
    d['rules']['routes']={'pier':['yard'],'yard':['hall'],'hall':['street'],'street':['far'],'far':[]}
    return initial_state(d,'player')


@pytest.mark.parametrize('use_update',[False,True])
def test_whole_journey_and_companion_follow_declared_path(use_update):
    d,s=route_world();original=deepcopy(s)
    updates=[{'entityId':'npc','field':'location','value':'far','reason':'同行','visibleTo':['player']}]
    if use_update:updates.append({'entityId':'player','field':'location','value':'far','reason':'下山买药','visibleTo':['player']})
    after,_,changes,_=apply_plan(s,d,plan(location='far',updates=updates,elapsedMinutes=25),'journey')
    assert after['location']==after['entities']['player']['location']==after['entities']['npc']['location']=='far'
    assert {'yard','hall','street'}<=set(after['discovered'])
    assert after['minute']==s['minute']+25
    assert original==s
    assert any(c['field']=='location' for c in changes)


def test_npc_journey_does_not_reveal_intermediate_places_to_player():
    d,s=route_world()
    after,_,_,_=apply_plan(s,d,plan(updates=[{'entityId':'npc','field':'location','value':'far','reason':'独自出发','visibleTo':[]}]),'npc')
    assert not {'yard','hall','street'}&set(after['discovered'])


@pytest.mark.parametrize('routes',[
    {'pier':[]},
    {'pier':['yard'],'hall':['far']},
    {'pier':['yard'],'yard':['pier'],'far':['yard']},
    {'pier':['letter'],'letter':['far']},
])
def test_blocked_missing_reverse_cyclic_and_non_place_edges_cannot_teleport(routes):
    d,s=route_world();d['rules']['routes']=routes
    with pytest.raises(Problem) as error:apply_plan(s,d,plan(location='far'),'blocked')
    assert error.value.code=='WORLD_ROUTE_RULE'


def test_journey_still_stops_at_pending_decision_and_pays_cost():
    d,s=route_world()
    s['entities']['player']['fields']['stamina']=10
    d['rules']['actionCosts']={'action':{'minutes':10,'stamina':2}}
    with pytest.raises(Problem):apply_plan(s,d,plan(location='far',elapsedMinutes=25),'unpaid',action_kind='action')
    s['queue']=[{'id':'choice','type':'choice','entityId':'player','description':'守卫要求通行凭据','visibleTo':['player'],
                 'audienceIds':['player'],'audienceAll':False,'decisionActorId':'player','dueMinute':5,'requiresDecision':True,'minute':0}]
    with pytest.raises(Problem) as error:apply_plan(s,d,plan(location='far',elapsedMinutes=25),'decision')
    assert error.value.code=='WORLD_DECISION_REQUIRED'


def test_undeclared_world_keeps_free_exploration_but_missing_bridge_does_not():
    d,s=route_world();d['rules']['routes']={}
    assert journey_path(d,'pier','far')==['pier','far']
    with pytest.raises(Problem):journey_path(d,'pier','letter')
