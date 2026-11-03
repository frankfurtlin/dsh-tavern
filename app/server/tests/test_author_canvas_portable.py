from copy import deepcopy
from app.db import SessionLocal
from app.world.models import World,WorldCheckpoint
from app.authoring.service import default_value,save_draft,configure_start,initialize_fields
from app.authoring.schemas import SaveAuthor
from app.world.schemas import BeginWorld
from app.world.state import initial_state
from .test_portable_domain import domain,migrate_owned_database,restored


def test_canvas_future_starts_groups_and_trial_budget_survive_cross_account_restore(domain,tmp_path):
    with SessionLocal() as db:
        world=db.get(World,domain['world']);old=deepcopy(db.get(WorldCheckpoint,domain['checkpoint']).state)
        value=default_value(world)
        value['playerFields']=[{'id':'author_skills','label':'擅长','type':'multiselect','options':['观察','记录'],'defaultValue':['记录'],'group':'旅人','width':'half','required':True,'minHeight':120}]
        value['statusFields']=[{'id':'author_notes','label':'行囊清单','type':'list','defaultValue':['便笺'],'group':'行囊'}]
        value.update(formTitle='你的人物',statusTitle='旅人手记',statusTheme='scroll')
        save_draft(db,world,SaveAuthor(expectedVersion=0,value=value))
        world.source={**world.source,'authorTrial':{'originWorldId':world.id,'authorVersion':1,'limits':{'generations':4,'inputTokens':2000000,'outputTokens':500000},'reserved':{'generations':2,'inputTokens':50000,'outputTokens':9000}}}
        db.commit()
    receipt=restored(domain,tmp_path);target=receipt['idMap'][('world',domain['world'])]
    with SessionLocal() as db:
        world=db.get(World,target);settings=default_value(world)
        assert settings['formTitle']=='你的人物' and settings['statusTheme']=='scroll'
        assert settings['playerFields'][0]['group']=='旅人' and settings['playerFields'][0]['minHeight']==120
        assert world.source['authorTrial']['originWorldId']==target
        assert world.source['authorTrial']['reserved']['generations']==2
        actor=next(e['id'] for e in world.definition['entities'] if e['type']=='character')
        registry,who,opening=configure_start(world.definition,BeginWorld(characterId=actor,playerValues={'author_skills':['观察','记录']}))
        definition,state=initial_state(registry,who,'','');initialize_fields(definition,state)
        assert state['entities'][state['playerId']]['fields']['author_skills']==['观察','记录']
        assert state['entities'][state['playerId']]['fields']['author_notes']==['便笺']
        assert db.get(WorldCheckpoint,domain['checkpoint']).state==old
