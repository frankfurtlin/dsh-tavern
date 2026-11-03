"""Real API/PostgreSQL, independent accounts, frozen publication and start contracts."""
from copy import deepcopy
import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User
from app.foundation.models import Account
from app.world.models import World,WorldCheckpoint
from app.world.worker import process
from .test_world_security import isolated_storage_and_mock_only

API='/api/reader/v1/authoring';WORLD='/api/world/v1'


def test_authored_player_field_contract_survives_later_director_updates():
    from app.authoring.service import validate_fields,configure_start
    from app.errors import Problem
    from app.world.schemas import BeginWorld
    config={'openings':[{'id':'first','characterId':'recommended','locationId':'shop','text':'观察眼前。'}],
            'playerFields':[{'id':'author_origin','label':'来历','type':'select','required':True,'options':['送信人'],'defaultValue':'送信人'}]}
    definition={'authoring':config,'location':'shop','initial':{}}
    _,character,_=configure_start(definition,BeginWorld(customName='访客',openingId='first'))
    assert character is None  # A recommended role cannot override a custom identity.
    state={'playerId':'visitor','entities':{'visitor':{'fields':{'author_origin':'送信人'}}}}
    validate_fields(definition,state)
    state['entities']['visitor']['fields']['author_origin']='未定义的来历'
    with pytest.raises(Problem) as error:validate_fields(definition,state)
    assert error.value.code=='AUTHOR_FIELD_INVALID'


def post(client,path,body,key='request'):
    result=client.post(path,json=body,headers={'Idempotency-Key':key})
    assert result.status_code in (200,201,202),result.text
    return result.json()


def login(client,name):
    client.headers.pop('Authorization',None)
    result=client.post('/api/reader/v1/auth/login',json={'username':name,'password':'reader-password-123','clientType':'android','deviceName':'test'})
    assert result.status_code==200,result.text
    client.headers['Authorization']='Bearer '+result.json()['accessToken']
    return result.json()


def other(client,name='public_reader'):
    result=client.post('/api/reader/v1/auth/register',json={'username':name,'password':'reader-password-123'})
    assert result.status_code==201,result.text
    return login(client,name)


def moderator(client):
    user=other(client,'moderator')
    with SessionLocal() as db:
        row=db.get(Account,user['user']['id'])
        if row:row.role='ADMIN'
        else:db.add(Account(owner_id=user['user']['id'],role='ADMIN'))
        db.commit()
    return user


def draft(client):
    world=post(client,WORLD+'/worlds',{'templateId':'original-mystery-v1'},'template')['worldId']
    current=client.get(API+'/worlds/'+world).json();value=current['value']
    actor=next(e['id'] for e in value['definition']['entities'] if e['type']=='character')
    value['title']='可公开的钟楼故事';value['summary']='一封信，两种不同的开场。'
    value['authorInstructions']='以可观察细节叙述，不替玩家做决定。'
    second='author-second-place';value['definition']['entities'].append({'id':second,'name':'钟楼门口','type':'place','description':'雨中的钟楼入口','source':'user'})
    first=value['definition']['location'];value['rules']['routes']={first:[second],second:[first]}
    value['openings']=[{'id':'shop','title':'从书店开始','text':'我查看眼前的书信。','characterId':actor,'locationId':first},
        {'id':'tower','title':'从钟楼开始','text':'我观察钟楼门口。','characterId':actor,'locationId':second}]
    value['playerFields']=[{'id':'author_origin','label':'你的来历','type':'select','required':True,'defaultValue':None,'options':['送信人','修钟匠']}]
    value['statusFields']=[{'id':'author_energy','label':'精力','type':'number','defaultValue':30,'minimum':0,'maximum':100,'required':True}]
    response=client.put(API+'/worlds/'+world,json={'expectedVersion':0,'value':value},headers={'Idempotency-Key':'save-one'})
    assert response.status_code==200,response.text
    return world,response.json()


def submit(client,world,author_version=1,version=0,key='submit'):
    return post(client,API+'/worlds/'+world+'/submit',{'expectedVersion':version,'authorVersion':author_version,'rightsConfirmed':True,'attribution':'原创模板的测试改编；仅用于验收。'},key)


def approve(client,publication,decision='approve',key='review'):
    return post(client,API+'/moderation/'+publication['id'],{'expectedVersion':publication['version'],'decision':decision,'note':'已检查公开内容及来源说明。'},key)


def test_draft_validation_version_and_owner_isolation(client,reader):
    world,current=draft(client)
    assert current['version']==1 and current['value']['openings'][1]['id']=='tower'
    value=current['value'];response=client.put(API+'/worlds/'+world,json={'expectedVersion':0,'value':value},headers={'Idempotency-Key':'stale'})
    assert response.status_code==409
    other(client)
    assert client.get(API+'/worlds/'+world).status_code==404
    assert client.put(API+'/worlds/'+world,json={'expectedVersion':1,'value':value},headers={'Idempotency-Key':'foreign'}).status_code==404
    assert client.get(API+'/moderation').status_code==403


def test_public_copy_can_edit_without_original_private_evidence_or_character_ids(client,reader):
    world,current=draft(client);value=deepcopy(current['value'])
    quote='仅用于验证的私人原文，不应随副本分发。';block='private-original-block';identity='a'*32
    with SessionLocal() as db:
        original=db.get(World,world);original.source={**original.source,'blocks':[{'blockId':block,'text':quote}]};db.commit()
    person=next(e for e in value['definition']['entities'] if e['type']=='character')
    person.update(source='original',sourceCharacterId=identity,evidence=[{'blockId':block,'quote':quote}])
    saved=client.put(API+'/worlds/'+world,json={'expectedVersion':1,'value':value},headers={'Idempotency-Key':'private-source-evidence'});assert saved.status_code==200,saved.text
    publication=submit(client,world,author_version=2);moderator(client);approve(client,publication);other(client)
    copied=post(client,API+'/publications/'+publication['id']+'/copy',{},'copy-private-evidence')['worldId']
    response=client.get(API+'/worlds/'+copied);assert response.status_code==200
    assert all(secret not in response.text for secret in [quote,block,identity])
    own=response.json();own['value']['title']='读者独立编辑的版本'
    saved=client.put(API+'/worlds/'+copied,json={'expectedVersion':own['version'],'value':own['value']},headers={'Idempotency-Key':'edit-copied-source'});assert saved.status_code==200,saved.text
    assert len(saved.json()['value']['openings'])==2
    with SessionLocal() as db:assert db.get(World,world).source['blocks'][0]['text']==quote


@pytest.mark.parametrize('change',[ 'missing-opening','duplicate-opening','unknown-character','unknown-place','bad-number','reserved-field','overlap-field','invalid-cover','missing-status-default'])
def test_invalid_author_configuration_never_saves(client,reader,change):
    world,current=draft(client);value=current['value']
    if change=='missing-opening':value['openings']=[]
    if change=='duplicate-opening':value['openings'].append(deepcopy(value['openings'][0]))
    if change=='unknown-character':value['openings'][0]['characterId']='foreign'
    if change=='unknown-place':value['openings'][0]['locationId']='foreign'
    if change=='bad-number':value['statusFields'][0]['defaultValue']=500
    if change=='reserved-field':value['statusFields'][0]['id']='money'
    if change=='overlap-field':value['playerFields'][0]['id']=value['statusFields'][0]['id']
    if change=='invalid-cover':value['coverDataUrl']='data:image/svg+xml;base64,PHN2Zz4='
    if change=='missing-status-default':value['statusFields'][0]['defaultValue']=None
    response=client.put(API+'/worlds/'+world,json={'expectedVersion':1,'value':value},headers={'Idempotency-Key':'invalid'})
    assert response.status_code==422,response.text
    assert client.get(API+'/worlds/'+world).json()['version']==1


def test_publication_pending_approval_snapshot_favorites_and_author_privacy(client,reader):
    world,current=draft(client);publication=submit(client,world)
    assert not publication['live'] and client.get(API+'/catalog').json()['total']==0
    other(client)
    assert client.get(API+'/publications/'+publication['id']).status_code==404
    assert client.post(API+'/publications/'+publication['id']+'/copy',json={},headers={'Idempotency-Key':'pending-copy'}).status_code==404
    assert client.post(API+'/moderation/'+publication['id'],json={'expectedVersion':1,'decision':'approve','note':'越权尝试'},headers={'Idempotency-Key':'forbidden'}).status_code==403
    moderator(client);approved=approve(client,publication)
    login(client,'public_reader')
    listing=client.get(API+'/catalog?q=钟楼&genre=mystery').json();assert listing['total']==1
    detail=client.get(API+'/publications/'+publication['id']);assert detail.status_code==200
    text=detail.text
    assert 'hiddenGoal' not in text and 'authorInstructions' not in text and 'definition' not in text
    assert '账册藏在' not in text and 'memo' not in text and 'key_cipher' not in text
    for _ in range(2):assert client.put(API+'/publications/'+publication['id']+'/favorite',json={'favorite':True}).status_code==200
    assert client.get(API+'/catalog?favorites=true').json()['total']==1
    assert client.get(API+'/publications/'+publication['id']).json()['favorites']==1
    author=client.get(API+'/authors/'+reader['user']['id']);assert set(author.json())=={'id','displayName'}
    assert client.get(API+'/catalog?authorId='+reader['user']['id']).json()['total']==1
    assert approved['state']=='published'


def test_edit_resubmit_reject_keeps_approved_version_and_withdraw_blocks_new_copies(client,reader):
    world,current=draft(client);publication=submit(client,world);moderator(client);publication=approve(client,publication)
    login(client,'reader_one');value=current['value'];value['title']='新版尚未审核'
    response=client.put(API+'/worlds/'+world,json={'expectedVersion':1,'value':value},headers={'Idempotency-Key':'save-two'});assert response.status_code==200
    assert client.get(API+'/publications/'+publication['id']).json()['title']=='可公开的钟楼故事'
    publication=submit(client,world,2,publication['version'],'submit-two')
    assert publication['live'] and publication['state']=='pending'
    login(client,'moderator');publication=approve(client,publication,'reject','reject-two')
    assert publication['live'] and client.get(API+'/publications/'+publication['id']).json()['version']==1
    login(client,'reader_one');publication=submit(client,world,2,publication['version'],'resubmit-two')
    login(client,'moderator');publication=approve(client,publication,key='approve-two')
    assert client.get(API+'/publications/'+publication['id']).json()['title']=='新版尚未审核'
    login(client,'reader_one');publication=post(client,API+'/publications/'+publication['id']+'/withdraw',{'expectedVersion':publication['version']},'withdraw')
    assert not publication['live'] and client.get(API+'/catalog').json()['total']==0
    assert client.get(API+'/publications/'+publication['id']).status_code==404
    assert client.post(API+'/publications/'+publication['id']+'/copy',json={},headers={'Idempotency-Key':'after-withdraw'}).status_code==404


def test_reports_removal_and_pagination(client,reader):
    world,current=draft(client);publication=submit(client,world);moderator(client);publication=approve(client,publication)
    other(client)
    assert client.post(API+'/publications/'+publication['id']+'/report',json={'reason':'测试举报：请检查来源说明。'}).status_code==200
    assert client.get(API+'/catalog?limit=1&offset=0').json()['total']==1
    assert client.get(API+'/catalog?limit=1&offset=1').json()['items']==[]
    login(client,'moderator');review=client.get(API+'/moderation').json();assert len(review['items'])==1 and len(review['items'][0]['reports'])==1
    approve(client,publication,'remove','report-removal');assert client.get(API+'/moderation').json()['items']==[]
    assert client.get(API+'/publications/'+publication['id']).status_code==404


@pytest.mark.parametrize('opening',['shop','tower'])
def test_public_copy_actual_start_fields_frozen_and_source_private(client,reader,profile,opening):
    world,current=draft(client);publication=submit(client,world);moderator(client);publication=approve(client,publication)
    other(client)
    response=client.post('/api/reader/v1/model-profiles',json={'name':'mock','baseUrl':'mock://deterministic','modelId':'fixture-v1','key':'TEST-KEY-NOT-REAL','contextWindow':32768,'callBudget':100,'inputBudget':10000000,'outputBudget':1000000});assert response.status_code==201,response.text
    copy=post(client,API+'/publications/'+publication['id']+'/copy',{},'copy-one')
    assert post(client,API+'/publications/'+publication['id']+'/copy',{},'copy-one')==copy
    own=copy['worldId'];info=client.get(WORLD+'/worlds/'+own).json();assert len(info['authoring']['openings'])==2
    copied=client.get(API+'/worlds/'+own).json()
    assert copied['value']==current['value'] and copied['version']==1
    saved=client.put(API+'/worlds/'+own,json={'expectedVersion':1,'value':copied['value']},headers={'Idempotency-Key':'save-copied-settings'})
    assert saved.status_code==200,saved.text
    assert len(saved.json()['value']['openings'])==2
    assert client.get(WORLD+'/worlds/'+world).status_code==404
    body={'openingId':opening,'playerValues':{'author_origin':'修钟匠'}}
    begun=post(client,WORLD+'/worlds/'+own+'/begin',body,'begin')
    with SessionLocal() as db:
        saved=db.get(WorldCheckpoint,begun['checkpointId']);original=db.get(World,own)
        assert original.source['blocks']==[] and original.source_book_id is None
        assert saved.state['location']==next(o for o in current['value']['openings'] if o['id']==opening)['locationId']
        assert saved.state['entities'][saved.state['playerId']]['fields']['author_origin']=='修钟匠'
        assert saved.state['entities'][saved.state['playerId']]['fields']['author_energy']==30
        assert saved.definition['authoring']['openingId']==opening
        frozen=deepcopy(saved.definition)
    process(begun['taskId'])
    task=client.get(WORLD+'/tasks/'+begun['taskId']).json();assert task['state']=='succeeded',task
    line=client.get(WORLD+'/worlds/'+own+'/lines/'+begun['lineId']).json()
    assert any(f['label']=='精力' for f in line['head']['state']['statusFields'])
    login(client,'reader_one');post(client,API+'/publications/'+publication['id']+'/withdraw',{'expectedVersion':publication['version']},'withdraw-copy')
    login(client,'public_reader');assert client.get(WORLD+'/worlds/'+own).status_code==200
    with SessionLocal() as db:assert db.get(WorldCheckpoint,begun['checkpointId']).definition==frozen


@pytest.mark.parametrize('values',[{}, {'author_origin':'未知选项'}, {'author_origin':'修钟匠','unexpected':'x'}])
def test_start_rejects_incomplete_or_unregistered_form(client,reader,profile,values):
    world,current=draft(client)
    response=client.post(WORLD+'/worlds/'+world+'/begin',json={'openingId':'shop','playerValues':values},headers={'Idempotency-Key':'bad-start'})
    assert response.status_code==422,response.text


def test_world_delete_cascades_publication_and_own_favorites(client,reader):
    world,current=draft(client);publication=submit(client,world);moderator(client);approve(client,publication)
    login(client,'reader_one');assert client.delete(WORLD+'/worlds/'+world).status_code==200
    assert client.get(API+'/publications/'+publication['id']).status_code==404
    assert client.get(API+'/catalog').json()['total']==0
