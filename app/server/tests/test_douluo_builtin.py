"""The new adventure follows the existing owned-copy and finite-ledger paths."""
import json
import pytest
from copy import deepcopy
from types import SimpleNamespace
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import Task
from app.world.douluo import KEY,TITLE,configure_author_value
from app.world.templates import template_definition
from app.world.schemas import WorldRules
from app.world.references import reference_problems
from app.world.models import World
from app.world.state import initial_state
from app.authoring.service import default_value,compile_definition
from .test_world_security import isolated_storage_and_mock_only
from .test_experience_completion import admin,API
from .test_authoring_publications import post,other


def compiled():
    definition=template_definition(KEY,WorldRules(genre='fantasy').model_dump())
    world=SimpleNamespace(definition=definition,name=TITLE,source={'kind':'standalone','templateId':KEY,'blocks':[]})
    value=default_value(world);configure_author_value(world,value)
    return compile_definition(world,value)


def test_new_worlds_have_closed_disjoint_references_and_two_real_unselected_endings():
    first,second=compiled(),compiled()
    assert not reference_problems(first) and not reference_problems(second)
    assert not {e['id'] for e in first['entities']}&{e['id'] for e in second['entities']}
    ids={e['name']:e['id'] for e in first['entities']}
    _,state=initial_state(first,ids['林澈'])
    from app.scripts.plot import advance
    advance(state,first,[])
    assert state['plot']['ending'] is None
    assert sum(s['inventory'].get(ids['铜制回营信标'],0) for s in state['entities'].values())==1
    assert {e['id'] for e in first['script']['endings']}=={'survey-complete','safe-withdrawal'}
    assert all(e['settlementSteps'] for e in first['script']['endings'])


@pytest.mark.parametrize('choice,ending,survey',[
    ('完成勘察','survey-complete',True),('主动撤回','safe-withdrawal',False)])
def test_each_ending_requires_explicit_choice_and_real_return_and_handoff(choice,ending,survey):
    from app.scripts.plot import advance
    definition=compiled();ids={e['name']:e['id'] for e in definition['entities']}
    definition,state=initial_state(definition,ids['林澈'])
    player=state['entities'][ids['林澈']];teacher=state['entities'][ids['赵无极']]
    advance(state,definition,[])
    assert state['plot']['ending'] is None
    if survey:
        lore=next(e['id'] for e in definition['lore'] if e['title']=='断枝与溪边足迹')
        player['known'].append(lore)
        player['location']=state['location']=ids['林间浅溪'];advance(state,definition,[])
    player['fields']['author_trial_choice']=choice
    advance(state,definition,[])
    assert state['plot']['ending'] is None,'A decision is not a completed handoff'
    player['location']=state['location']=ids['星斗外围营地']
    player['inventory'].pop(ids['铜制回营信标'])
    teacher['inventory'][ids['铜制回营信标']]=1
    advance(state,definition,[{'field':'inventory','fromId':ids['林澈'],'toId':ids['赵无极'],
        'itemId':ids['铜制回营信标'],'quantity':1,'visibleTo':['player']}])
    assert state['plot']['ending']==ending
    assert sum(s['inventory'].get(ids['铜制回营信标'],0) for s in state['entities'].values())==1


def test_curated_douluo_reuses_own_save_but_different_accounts_get_isolated_worlds(client,reader):
    admin(reader['user']['id']);post(client,API+'/curated/publish',{},'douluo-library')
    entry=next(e for e in client.get(API+'/curated').json()['items'] if e['key']==KEY)
    assert entry['available'] and entry['publicationId']
    one=post(client,API+'/curated/'+KEY+'/open',{},'douluo-own')
    same=post(client,API+'/curated/'+KEY+'/open',{},'douluo-own-again')
    assert one['worldId']==same['worldId']
    other(client,'douluo_second')
    two=post(client,API+'/curated/'+KEY+'/open',{},'douluo-other')
    assert two['worldId']!=one['worldId']
    with SessionLocal() as db:
        a,b=db.get(World,one['worldId']),db.get(World,two['worldId'])
        assert a.owner_id!=b.owner_id and a.definition['script']['endings']
        assert db.scalar(select(func.count()).select_from(Task))==0


def test_adding_douluo_never_rewrites_an_existing_blue_world(client,reader):
    admin(reader['user']['id']);post(client,API+'/curated/publish',{},'before-library')
    blue=post(client,API+'/curated/blue-carbuncle/open',{},'original-blue')
    with SessionLocal() as db:
        original=db.get(World,blue['worldId']);before=deepcopy(original.definition)
        assert len(before['script']['endings'])==3
    post(client,API+'/curated/'+KEY+'/open',{},'new-douluo')
    post(client,API+'/curated/publish',{},'publish-again')
    with SessionLocal() as db:assert db.get(World,blue['worldId']).definition==before
