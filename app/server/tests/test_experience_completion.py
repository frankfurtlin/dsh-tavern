from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import User, Task
from app.foundation.core import account
from app.experience.models import Feedback, Notice
from app.authoring.models import Publication
from app.world.models import World
from .test_world_security import isolated_storage_and_mock_only
from .test_authoring_publications import post, other, login

API = '/api/reader/v1/experience'


def admin(owner):
    with SessionLocal() as db:
        account(db, db.get(User, owner)).role = 'ADMIN'; db.commit()


def test_curated_library_publishes_idempotently_and_readers_get_own_copy(client, reader):
    assert client.post(API+'/curated/publish', json={}, headers={'Idempotency-Key':'denied'}).status_code == 403
    admin(reader['user']['id'])
    assert post(client, API+'/curated/publish', {}, 'library')['published'] == 4
    post(client, API+'/curated/publish', {}, 'library-again')
    with SessionLocal() as db: assert db.scalar(select(func.count()).select_from(Publication)) == 4
    initial = client.get(API+'/curated').json()['items']
    assert all(row['available'] for row in initial)
    other(client, 'beta_second')
    first = post(client, API+'/curated/blue-carbuncle/open', {}, 'open')
    again = post(client, API+'/curated/blue-carbuncle/open', {}, 'another-open')
    assert first['worldId'] == again['worldId'] and not again['lineId']
    login(client, 'reader_one')
    own = post(client, API+'/curated/blue-carbuncle/open', {}, 'admin-open')
    assert own['worldId'] != first['worldId']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        original = db.get(World, own['worldId']); clone = db.get(World, first['worldId'])
        assert original.owner_id != clone.owner_id
        assert clone.definition['script']['endings']


def test_chat_archive_search_delete_only_removes_selected_conversation(client, reader, profile):
    from .test_play_controls_world_chat import world, create_chat, fingerprint, settings
    from app.world.worker import process
    from app.play_controls.models import WorldChatMessage, MemoryRevision
    base='/api/reader/v1/play-controls'
    w,line=world(client);before=fingerprint();a=create_chat(client,w,line,'a');b=create_chat(client,w,line,'b')
    assert settings(client,'chats',a['id']).status_code==200
    sent=post(client,base+'/world-chats/'+a['id']+'/send',{'message':'请记住我叫北斗','turnId':'one'},'one');process(sent['taskId'])
    changed=client.patch(base+'/world-chats/'+a['id'],json={'expectedVersion':1,'name':'北斗的聊天','status':'archived'},headers={'Idempotency-Key':'archive'})
    assert changed.status_code==200
    assert len(client.get(base+'/world-chats').json()['items'])==1
    assert client.get(base+'/world-chats?status=archived&q=北斗').json()['items'][0]['name']=='北斗的聊天'
    assert client.post(base+'/world-chats/'+a['id']+'/send',json={'message':'继续','turnId':'blocked'},headers={'Idempotency-Key':'blocked'}).status_code==409
    transcript=client.get(base+'/world-chats/'+a['id']+'/transcript').json()['text'];assert '北斗' in transcript
    assert client.delete(base+'/world-chats/'+a['id']+'?expectedVersion=2').status_code==200
    assert client.get(base+'/world-chats/'+a['id']).status_code==404
    assert client.get(base+'/world-chats/'+b['id']).status_code==200
    assert fingerprint()==before
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WorldChatMessage))==0
        assert db.scalar(select(func.count()).select_from(MemoryRevision))==0
        task=db.get(Task,sent['taskId']);assert task.result['deleted'] is True
        assert not {'message','messages','chatSnapshot','conversationMemory'} & set(task.payload)


def test_chat_recall_looks_beyond_recent_thirty_and_freezes_queue_memory(client, reader, profile):
    from .test_play_controls_world_chat import world, create_chat, fingerprint
    from app.play_controls.models import WorldChatMessage
    from app.models import uid
    w,line=world(client);session=create_chat(client,w,line);before=fingerprint()
    with SessionLocal() as db:
        for turn in range(100):
            task=Task(id=uid(),owner_id=reader['user']['id'],task_type='world_character_chat',state='succeeded',
                      payload={'worldId':w,'lineId':line['id'],'chatId':session['id'],'chatTurnKey':'past-'+str(turn),'parentCheckpointId':line['headCheckpointId']},result={})
            db.add(task);db.flush()
            for n,role in enumerate(('user','assistant')):
                text='普通的第 '+str(turn)+' 次对话'
                if turn==0 and role=='user':text='以后请叫我北斗，我们约定下次聊钟楼。'
                db.add(WorldChatMessage(owner_id=reader['user']['id'],session_id=session['id'],task_id=task.id,turn_key='past-'+str(turn),sequence=turn*2+n+1,role=role,text=text,status='saved',checkpoint_id=line['headCheckpointId']))
        db.commit()
    base='/api/reader/v1/play-controls/world-chats/'+session['id']
    recalled=client.get(base+'/recall?q=我们最初约定的称呼').json()
    assert recalled['archivedMessageCount']==170 and any('北斗' in r['text'] for r in recalled['excerpts'])
    assert any('北斗' in r['text'] for r in client.get(base+'/messages?q=北斗').json()['items'])
    sent=post(client,base+'/send',{'message':'还记得最初的称呼和约定吗？','turnId':'recall'},'recall')
    with SessionLocal() as db:
        task=db.get(Task,sent['taskId']);assert len(task.payload['messages'])==30
        assert any('北斗' in r['text'] for r in task.payload['conversationMemory']['excerpts'])
    assert fingerprint()==before


def test_sixty_memory_versions_can_page_and_restore_earliest(client, reader, profile):
    from .test_play_controls_world_chat import world, settings
    w,line=world(client)
    for version in range(60):
        assert settings(client,'lines',line['id'],version=version,summary='摘要 '+str(version+1),key='version-'+str(version)).status_code==200
    base='/api/reader/v1/play-controls/lines/'+line['id']
    first=client.get(base+'/history').json();assert len(first['items'])==50 and first['nextBefore']==11
    second=client.get(base+'/history?before=11').json();assert len(second['items'])==10 and second['items'][-1]['version']==1
    restored=post(client,base+'/settings/restore',{'expectedVersion':60,'revision':1},'restore-earliest')
    assert restored['version']==61 and restored['memory']['summary']=='摘要 1'


def test_visual_plot_random_images_and_actions_are_consumed_by_real_start(client, reader, profile):
    from .test_authoring_publications import draft
    from app.world.worker import process
    from app.world.models import WorldCheckpoint
    import io,base64
    from PIL import Image
    w,d=draft(client);value=d['value'];definition=value['definition']
    people=[e['id'] for e in definition['entities'] if e['type']=='character']
    places=[e['id'] for e in definition['entities'] if e['type']=='place'];item=next(e['id'] for e in definition['entities'] if e['type']=='item')
    check={'kind':'transfer','entityId':'player','itemId':item,'toId':people[1]}
    objective={'id':'handoff','title':'亲手交接','description':'确实把便笺交给对方。','requires':[],'checks':[check],'sourceBlockIds':['AUTHOR-CONFIG']}
    value['plot']={'synopsis':'交接后选择公开或保密。','recommendedCharacterId':people[0],'opening':'只观察身边环境，不交出物品。',
        'scenes':[{'id':'first','title':'交接前','summary':'讨论便笺去处。','placeId':places[0],'characterIds':people,'sourceBlockIds':['AUTHOR-CONFIG']}],
        'objectives':[objective],'endings':[{'id':'public','title':'公开','description':'公开经过。','requires':['handoff'],'checks':[{'kind':'field','entityId':'player','field':'author_decision','value':'公开'}]},
        {'id':'private','title':'保密','description':'保留双方约定。','requires':['handoff'],'checks':[{'kind':'field','entityId':'player','field':'author_decision','value':'保密'}]}], 'conservedItemIds':[item]}
    value['playerFields'].append({'id':'author_luck','label':'抽到的签','type':'random','options':['梅','竹'],'defaultValue':'梅'})
    picture=io.BytesIO();Image.new('RGB',(32,32),'blue').save(picture,format='PNG')
    image='data:image/png;base64,'+base64.b64encode(picture.getvalue()).decode()
    value['statusFields'].append({'id':'author_portrait','label':'身份图','type':'image','defaultValue':image})
    value['fixedActions']=[{'id':'look','label':'查看便笺','message':'只观察便笺，不交出。','kind':'observe'}]
    value['suggestedModel']='fixture-v1'
    saved=client.put('/api/reader/v1/authoring/worlds/'+w,json={'expectedVersion':1,'value':value},headers={'Idempotency-Key':'visual-save'})
    assert saved.status_code==200,saved.text
    body={'characterId':people[0],'openingId':'shop','playerValues':{'author_origin':'送信人'}}
    begin=post(client,'/api/world/v1/worlds/'+w+'/begin',body,'visual-begin')
    replay=post(client,'/api/world/v1/worlds/'+w+'/begin',body,'visual-begin')
    assert replay==begin
    with SessionLocal() as db:
        point=db.get(WorldCheckpoint,begin['checkpointId']);fields=point.state['entities'][point.state['playerId']]['fields']
        assert fields['author_luck'] in ('梅','竹') and fields['author_portrait']==image
        assert point.definition['script']['endings'][1]['id']=='private'
    process(begin['taskId'])
    public=client.get('/api/world/v1/worlds/'+w+'/lines/'+begin['lineId']).json()['head']['state']
    assert public['fixedActions'][0]['label']=='查看便笺'
    with SessionLocal() as db:
        head=db.get(WorldCheckpoint,client.get('/api/world/v1/worlds/'+w+'/lines/'+begin['lineId']).json()['headCheckpointId'])
        assert head.state['entities'][head.state['playerId']]['fields']['author_luck']==fields['author_luck']
    invalid=client.post('/api/world/v1/worlds/'+w+'/begin',json={**body,'playerValues':{'author_origin':'送信人','author_luck':'梅'}},headers={'Idempotency-Key':'manual-random'})
    assert invalid.status_code==422


def test_new_start_status_default_overrides_old_template_value_without_mutating_template():
    from app.authoring.service import initialize_fields
    from copy import deepcopy
    definition={'entities':[{'id':'person','type':'character'}],
        'initial':{'person':{'fields':{'author_decision':'旧选项'}}},
        'authoring':{'statusFields':[{'id':'author_decision','label':'决定','type':'select',
            'required':False,'scope':'player','options':['未选择','公开'],'defaultValue':'未选择'}]}}
    original=deepcopy(definition);state={'playerId':'person','entities':{'person':{'fields':{'author_decision':'旧选项'}}}}
    initialize_fields(definition,state)
    assert state['entities']['person']['fields']['author_decision']=='未选择'
    assert definition==original


def test_beta_cohort_is_admin_only_excludes_qa_and_does_not_change_permissions(client, reader):
    assert client.get(API+'/beta/config').status_code==403
    owner=reader['user']['id']; other(client,'cohort_player'); other(client,'cohort_qa')
    login(client,'reader_one'); admin(owner)
    body={'expectedVersion':0,'cohorts':[{'id':'first','label':'第一轮','usernames':['reader_one','cohort_player','cohort_qa']}], 'excludedUsernames':['cohort_qa']}
    saved=client.put(API+'/beta/config',json=body,headers={'Idempotency-Key':'cohort'})
    assert saved.status_code==200,saved.text
    replay=client.put(API+'/beta/config',json=body,headers={'Idempotency-Key':'cohort'})
    assert replay.json()==saved.json()
    report=client.get(API+'/beta/report?cohort=first').json()
    assert report['members']==1 and report['tasks']==0
    assert report['timings']['firstText']['medianMs'] is None
    assert client.put(API+'/beta/config',json=body,headers={'Idempotency-Key':'stale'}).status_code==409
    login(client,'cohort_player'); assert client.get(API+'/beta/report?cohort=first').status_code==403


def test_new_author_character_can_open_portrait_library_without_rewriting_old_definition(client, reader, profile):
    from .test_authoring_publications import draft
    w,d=draft(client); value=d['value']; place=value['definition']['location']
    value['definition']['entities'].append({'id':'new-courier','name':'新邮递员','type':'character','description':'成年男性邮递员。','source':'user'})
    value['definition']['initial']['new-courier']={'alive':True,'location':place,'inventory':{},'fields':{},'known':[]}
    result=client.put('/api/reader/v1/authoring/worlds/'+w,json={'expectedVersion':1,'value':value},headers={'Idempotency-Key':'new-character'})
    assert result.status_code==200,result.text
    library=client.get('/api/world/v1/worlds/'+w+'/character-looks')
    assert library.status_code==200,library.text
    assert any(person['entityId']=='new-courier' for person in library.json()['characters'])
    with SessionLocal() as db: assert not any(e['id']=='new-courier' for e in db.get(World,w).definition['entities'])


def test_authored_source_archived_chat_and_deleted_task_round_trip_to_new_owner(client,reader,profile):
    from .test_play_controls_world_chat import create_chat
    from app.world.worker import process
    from app.play_controls.models import WorldChatSession
    admin(reader['user']['id']);post(client,API+'/curated/publish',{},'portable-library')
    world=post(client,API+'/curated/blue-carbuncle/open',{},'portable-open')['worldId']
    begun=post(client,'/api/world/v1/worlds/'+world+'/begin',{'characterId':'watson'},'portable-begin');process(begun['taskId'])
    line=client.get('/api/world/v1/worlds/'+world+'/lines/'+begun['lineId']).json()
    first=create_chat(client,world,line,'portable-first');second=create_chat(client,world,line,'portable-second')
    root='/api/reader/v1/play-controls/world-chats/'
    sent=post(client,root+second['id']+'/send',{'message':'随后删除本对话，保留主线。','turnId':'delete'},'portable-chat');process(sent['taskId'])
    archived=client.patch(root+first['id'],json={'expectedVersion':first['version'],'status':'archived'},headers={'Idempotency-Key':'portable-archive'})
    assert archived.status_code==200
    latest=client.get(root+second['id']).json()
    assert client.delete(root+second['id'],params={'expectedVersion':latest['version']}).status_code==200
    password='Fixture-only-portable-20261015'
    export=client.post('/api/reader/v1/stories/world-'+world+'/backup',data={'password':password},headers={'Idempotency-Key':'managed-export'})
    assert export.status_code==201,export.text
    package=client.get('/api/reader/v1/backups/'+export.json()['id']+'/download').content
    target=other(client,'managed_restore')['user']['id']
    preview=client.post('/api/reader/v1/backups/prepare',data={'password':password},files={'file':('managed.srbackup',package)},headers={'Idempotency-Key':'managed-preview'})
    assert preview.status_code==201,preview.text
    info=preview.json();restored=post(client,'/api/reader/v1/backups/'+info['id']+'/restore',{'confirmed':True,'expectedVersion':info['version'],'targetOwnerId':target},'managed-restore')
    assert restored['receipt']['historicalTasksReplayed']==0
    chats=client.get(root+'?status=archived').json()['items'];assert len(chats)==1
    new_world=chats[0]['worldId']
    with SessionLocal() as db:
        row=db.get(World,new_world);entity_ids={e['id'] for e in row.definition['entities']}
        opening=row.definition['authoring']['openings'][0]
        assert opening['characterId'] in entity_ids and opening['locationId'] in entity_ids
        assert row.definition['authoring']['sourceBlocks'][0]['blockId']=='AUTHOR-CONFIG'
        assert row.definition['authoring']['sourceBlocks'][0]['text']
