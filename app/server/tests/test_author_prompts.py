"""Owner isolation and frozen preferences, against the guarded mock test database."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Task, User, uid
from app.foundation.models import AuthorPrompt, WorldPromptSelection
from app.foundation.prompts import freeze_selection, narrator_preferences
from app.world.models import World
from app.world.jobs import create_job
from app.errors import Problem

API='/api/reader/v1/product/prompts'


def create(client, **fields):
    body={'title':'克制叙事', 'description':'测试用私人写作偏好', 'content':'短句，减少无关景物描写。', 'requestKey':uid(), **fields}
    response=client.post(API,json=body)
    assert response.status_code==201, response.text
    return response.json(), body


def world(reader):
    with SessionLocal() as db:
        row=World(id=uid(),owner_id=reader['user']['id'],name='提示词验收',source_title='独立测试',source={},definition={},status='active')
        db.add(row);db.commit()
        return row.id


def test_private_crud_retry_languages_and_archive(client,reader):
    row,body=create(client,language='ja')
    assert client.post(API,json=body).json()['id']==row['id']
    assert client.post(API,json={**body,'content':'changed'}).status_code==409
    assert client.get(API).json()['items'][0]['language']=='ja'
    updated=client.put(API+'/'+row['id'],json={**body,'expectedVersion':1,'content':'保持严谨。'}).json()
    assert updated['version']==2
    assert client.put(API+'/'+row['id'],json={**body,'expectedVersion':1}).status_code==409
    archived=client.patch(API+'/'+row['id'],json={'expectedVersion':2,'status':'archived'}).json()
    assert archived['status']=='archived'
    assert client.delete(API+'/'+row['id']+'?expectedVersion=2').status_code==409
    restored=client.patch(API+'/'+row['id'],json={'expectedVersion':3,'status':'active'}).json()
    assert restored['version']==4
    assert client.delete(API+'/'+row['id']+'?expectedVersion=4').status_code==409
    client.patch(API+'/'+row['id'],json={'expectedVersion':4,'status':'archived'})
    assert client.delete(API+'/'+row['id']+'?expectedVersion=5').status_code==200
    assert client.get(API).json()['items']==[]


def test_prompt_and_world_ownership_are_not_interchangeable(client,reader):
    row,body=create(client)
    identity=world(reader)
    client.post('/api/reader/v1/auth/register',json={'username':'other_prompt_user','password':'other-password-123'})
    login=client.post('/api/reader/v1/auth/login',json={'username':'other_prompt_user','password':'other-password-123','clientType':'android'}).json()
    client.headers['Authorization']='Bearer '+login['accessToken']
    assert client.get(API).json()['items']==[]
    assert client.put(API+'/'+row['id'],json={**body,'expectedVersion':1}).status_code==404
    assert client.patch(API+'/'+row['id'],json={'expectedVersion':1,'status':'archived'}).status_code==404
    assert client.get(API+'/world/'+identity).status_code==404
    assert client.put(API+'/world/'+identity,json={'expectedVersion':0,'promptIds':[]}).status_code==404
    other_world=world(login)
    assert client.put(API+'/world/'+other_world,json={'expectedVersion':0,'promptIds':[row['id']]}).status_code==400


def test_selection_versions_limits_and_recycle(client,reader):
    row,_=create(client)
    identity=world(reader)
    path=API+'/world/'+identity
    assert client.put(path,json={'expectedVersion':0,'promptIds':[row['id'],row['id']]}).status_code==400
    assert client.put(path,json={'expectedVersion':0,'promptIds':[row['id']]}).json()['version']==1
    assert client.put(path,json={'expectedVersion':0,'promptIds':[]}).status_code==409
    client.patch(API+'/'+row['id'],json={'expectedVersion':1,'status':'archived'})
    assert client.get(path).json()['promptIds']==[]
    assert client.put(path,json={'expectedVersion':1,'promptIds':[row['id']]}).status_code==400
    long,_=create(client,content='中'*5000)
    assert client.put(path,json={'expectedVersion':1,'promptIds':[long['id']]}).status_code==400
    assert client.put(path,json={'expectedVersion':1,'promptIds':[]}).json()['version']==2
    assert client.put(path,json={'expectedVersion':2,'promptIds':['a']*21}).status_code==422


def test_task_freezes_style_and_cannot_change_director_data(client,reader,profile):
    row,body=create(client)
    identity=world(reader)
    client.put(API+'/world/'+identity,json={'expectedVersion':0,'promptIds':[row['id']]})
    with SessionLocal() as db:
        task=create_job(db,reader['user']['id'],'world_turn',{'worldId':identity},['analysis','generation','validation'])
        db.commit();task_id=task.id;frozen=deepcopy(task.checkpoint['authorPrompts'])
    client.put(API+'/'+row['id'],json={**body,'expectedVersion':1,'content':'改用长句。'})
    client.patch(API+'/'+row['id'],json={'expectedVersion':2,'status':'archived'})
    client.delete(API+'/'+row['id']+'?expectedVersion=3')
    with SessionLocal() as db:
        task=db.get(Task,task_id)
        assert task.checkpoint['authorPrompts']==frozen
        data={'state':{'plot':{'ending':'end_return'},'inventory':{'gem':1}},'action':'只观察'}
        assert narrator_preferences(task,data,'world_director') is data
        output=narrator_preferences(task,data,'world_narrator')
        assert output['authorStylePreferences'][0]['text']==body['content']
        assert output['state']==data['state'] and output['action']==data['action']
        assert 'authorStylePreferences' not in data
        assert freeze_selection(db,reader['user']['id'],identity)==[]
        task.checkpoint={**task.checkpoint,'authorPrompts':[{**frozen[0],'text':'篡改'}]}
        with pytest.raises(Problem):narrator_preferences(task,data,'world_narrator')


def test_legacy_tasks_and_account_cleanup(client,reader):
    assert narrator_preferences(SimpleNamespace(checkpoint={}),{'state':1},'world_narrator')=={'state':1}
    row,_=create(client)
    identity=world(reader)
    client.put(API+'/world/'+identity,json={'expectedVersion':0,'promptIds':[row['id']]})
    with SessionLocal() as db:
        db.delete(db.get(User,reader['user']['id']));db.commit()
        assert db.get(AuthorPrompt,row['id']) is None
        assert db.get(WorldPromptSelection,identity) is None


def test_mock_worker_receives_frozen_style_in_narrator_only(client,reader,demo,profile):
    from .test_world import start, action
    from app.world.worker import process
    from app.providers import MockAdapter
    row,_=create(client)
    w,line=start(client,reader,demo,profile)
    assert client.put(API+'/world/'+w['id'],json={'expectedVersion':0,'promptIds':[row['id']]}).status_code==200
    queued=action(client,w,line,key='frozen-style-worker')
    seen=[]
    original=MockAdapter.generate
    def record(self,profile,prompt,data,limit,**kwargs):
        seen.append((prompt,deepcopy(data)))
        return original(self,profile,prompt,data,limit,**kwargs)
    with patch.object(MockAdapter,'generate',record):process(queued['taskId'])
    narration=[data for prompt,data in seen if prompt.startswith('world-narrator')]
    assert narration and narration[0]['authorStylePreferences'][0]['text']==row['content']
    assert all('authorStylePreferences' not in data for prompt,data in seen if prompt.startswith('world-director'))


@pytest.mark.parametrize('field,value',[('title',' '),('content','\n'),('language','fr'),('content','x'*6001)])
def test_invalid_assets_are_rejected(client,reader,field,value):
    response=client.post(API,json={'title':'test','content':'test','requestKey':uid(),field:value})
    assert response.status_code==422
