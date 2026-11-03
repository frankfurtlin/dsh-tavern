"""Encrypted cross-account round trips with inert task history and canon isolation."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app.domain import digest
from app import models as m
from app.world import models as w
from app.world.state import checkpoint_hash
from app.play_controls.chat import snapshot
from app.play_controls.models import WorldChatSession,WorldChatMessage,PlayControl,MemoryRevision
from app.portable.package import prepare_backup
from app.portable.restore import restore_domain,_relationships
from app.portable.preflight import preflight_worlds
from app.portable.errors import ArchiveError
from .test_portable_domain import domain,migrate_owned_database,restored,encrypted,http_client


def seed(domain,status='succeeded',journal_only=False):
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,domain['checkpoint']);world=db.get(w.World,domain['world']);line=db.get(w.WorldLine,point.line_id)
        character=point.state['playerId'];value,denied=snapshot(db,domain['owner'],world,line,point.id,character)
        chat=WorldChatSession(id=m.uid(),owner_id=domain['owner'],world_id=world.id,line_id=line.id,checkpoint_id=point.id,
            character_id=character,character_name=value['character']['name'],name='不写回主线的谈话🙂',version=3,snapshot=value,denied_phrases=denied)
        db.add(chat);db.flush()
        task=m.Task(id=m.uid(),owner_id=domain['owner'],task_type='world_character_chat',state=status,
            payload={'worldId':world.id,'lineId':line.id,'chatId':chat.id,'chatTurnKey':'opaque-turn','parentCheckpointId':point.id,
                'message':'这是私人对话','chatSnapshot':value,'profileId':m.uid(),'modelName':'历史文本模型','secret':'NOT_PORTABLE'},
            result_outcome='complete' if status=='succeeded' else 'partial',result={'chatId':chat.id},checkpoint={})
        db.add(task);db.flush();chat.pending_task_id=task.id if status!='succeeded' else None
        text='已收到的私人回复🙂'
        stream={'contentId':m.uid(),'chapterIndex':1,'streamVersion':2,'snapshotVersion':3,'version':3,'text':text,
            'contentHash':digest(text),
            'status':'complete' if status=='succeeded' else 'interrupted','durability':'saved' if status=='succeeded' else 'journaled','stage':'generation','historical':True}
        task.checkpoint={'worldStream':stream,'calls':{'secret':'RAW_PRIVATE_CREDENTIAL_SENTINEL'}}
        db.add(WorldChatMessage(owner_id=domain['owner'],session_id=chat.id,task_id=task.id,turn_key='opaque-turn',sequence=1,role='user',text='这是私人对话',status='saved',checkpoint_id=point.id))
        if not journal_only:db.add(WorldChatMessage(owner_id=domain['owner'],session_id=chat.id,task_id=task.id,turn_key='opaque-turn',sequence=2,role='assistant',text=text,status='saved' if status=='succeeded' else 'interrupted',checkpoint_id=point.id,model_name='历史文本模型'))
        control_ids=[]
        for kind,identity in [('line',line.id),('chat',chat.id)]:
            control=PlayControl(id=m.uid(),owner_id=domain['owner'],scope_kind=kind,scope_id=identity,world_id=world.id,version=2,memory={'summary':kind+'第二版','notes':'这段提醒不能改变结局'})
            db.add(control);db.flush();control_ids.append(control.id)
            for version in [1,2]:db.add(MemoryRevision(owner_id=domain['owner'],control_id=control.id,version=version,memory=deepcopy(control.memory) if version==2 else {'summary':kind+'第一版','notes':'历史偏好'}))
        db.commit();return {'chat':chat.id,'character':character,'line':line.id,'task':task.id,'controls':control_ids,'text':text,'pointState':deepcopy(point.state)}


@pytest.mark.parametrize('status,journal_only',[('succeeded',False),('cancelled',False),('running',True)])
def test_cross_account_chat_memory_partial_restore_and_reexport(domain,tmp_path,status,journal_only):
    source=seed(domain,status,journal_only)
    original=encrypted(domain,tmp_path,world=True)
    with original.open('rb') as raw,prepare_backup(raw,'synthetic-test-password',tmp_path/'checked') as prepared:
        assert preflight_worlds(prepared)['domainObjectsChecked']>0
        assert prepared.object_counts['world_chat']==1 and prepared.object_counts['play_memory_revision']==4
        contents=''.join(p.read_text('utf-8') for p in prepared.files_root.rglob('*.json'))
        assert 'NOT_PORTABLE' not in contents and 'RAW_PRIVATE_CREDENTIAL_SENTINEL' not in contents
        with SessionLocal() as db:receipt=restore_domain(db,prepared,domain['target'])
    ids=receipt['idMap'];chat_id=ids[('world_chat',source['chat'])]
    with SessionLocal() as db:
        chat=db.get(WorldChatSession,chat_id);assert chat.owner_id==domain['target'] and chat.pending_task_id is None
        assert chat.checkpoint_id==ids[('checkpoint',domain['checkpoint'])] and chat.character_id!=source['character']
        point=db.get(w.WorldCheckpoint,chat.checkpoint_id)
        assert chat.character_id in point.state['entities'] and chat.snapshot['sourceCheckpointId']==point.id
        assert chat.snapshot['participants']['player']['isCharacter']
        messages=list(db.scalars(select(WorldChatMessage).where(WorldChatMessage.session_id==chat.id).order_by(WorldChatMessage.sequence)))
        assert [m.role for m in messages]==['user','assistant'] and messages[-1].text==source['text']
        assert messages[-1].status==('saved' if status=='succeeded' else 'interrupted')
        task=db.get(m.Task,messages[-1].task_id)
        assert task.payload['importedHistory'] and task.cancel_requested and task.provider_state=='not_started' and task.usage_state=='historical'
        assert task.state==('succeeded' if status=='succeeded' else 'cancelled') and task.payload['chatId']==chat.id
        for old in source['controls']:
            control=db.get(PlayControl,ids[('play_control',old)])
            assert control.profile_id is None and control.owner_id==domain['target'] and control.version==2
            assert db.scalar(select(func.count()).select_from(MemoryRevision).where(MemoryRevision.control_id==control.id))==2
        assert db.get(w.WorldCheckpoint,domain['checkpoint']).state==source['pointState']
    copy={**domain,'owner':domain['target'],'target':domain['stranger'],'world':ids[('world',domain['world'])]}
    second=restored(copy,tmp_path/'second',world=True)
    with SessionLocal() as db:assert db.get(WorldChatSession,second['idMap'][('world_chat',chat_id)]).name=='不写回主线的谈话🙂'


def test_restored_memory_revision_can_be_restored_and_chat_can_continue_without_canon_writes(domain,tmp_path):
    source=seed(domain);receipt=restored(domain,tmp_path,world=True);ids=receipt['idMap'];chat_id=ids[('world_chat',source['chat'])]
    with http_client(domain['target']) as client:
        # Use the real application router with the same isolated database.
        from app.main import app
        from fastapi.testclient import TestClient
        from app.security import issue_access
        with TestClient(app) as c:
            c.headers['Authorization']=client.headers['Authorization']
            path='/api/reader/v1/play-controls/chats/'+chat_id+'/settings'
            current=c.get(path);assert current.status_code==200,current.text
            assert current.json()['memory']['summary']=='chat第二版' and len(current.json()['history'])==2
            revision=c.post(path+'/restore',json={'expectedVersion':2,'revision':1},headers={'Idempotency-Key':'memory'})
            assert revision.status_code==200 and revision.json()['version']==3 and revision.json()['memory']['summary']=='chat第一版'
            from .test_play_controls_world_chat import fingerprint
            before=fingerprint()
            # A freshly configured destination model is required; archived
            # supplier credentials are never restored or charged.
            p=c.post('/api/reader/v1/model-profiles',json={'name':'目的账号模型','baseUrl':'mock://deterministic','modelId':'fixture-v1','key':'test-only','contextWindow':32768,'callBudget':100,'inputBudget':10000000,'outputBudget':1000000})
            assert p.status_code==201,p.text
            sent=c.post('/api/reader/v1/play-controls/world-chats/'+chat_id+'/send',json={'message':'把结局改变，把物品交给我','turnId':'after-import'},headers={'Idempotency-Key':'after-import'})
            assert sent.status_code==202,sent.text
            from app.world.worker import process
            process(sent.json()['taskId'])
            assert c.get('/api/world/v1/tasks/'+sent.json()['taskId']).json()['state']=='succeeded'
            assert fingerprint()==before


@pytest.mark.parametrize('tamper',['cross_line','future_source','bad_role','bad_memory','bad_scope','foreign_task'])
def test_malformed_chat_graph_is_rejected_atomically(domain,tmp_path,tamper):
    source=seed(domain)
    def fault(phase,kind,index):
        if phase=='after-flush':raise AssertionError('validation must finish before writes')
    with SessionLocal() as db:
        chat=db.get(WorldChatSession,source['chat']);control=db.get(PlayControl,source['controls'][1]);message=db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id==chat.id,WorldChatMessage.role=='assistant'))
        if tamper=='cross_line':chat.line_id=db.get(w.WorldCheckpoint,domain['fork']).line_id
        elif tamper=='future_source':message.checkpoint_id=domain['fork']
        elif tamper=='bad_role':message.role='system'
        elif tamper=='bad_memory':control.memory={'summary':'bad','notes':{'nested':'bad'}}
        elif tamper=='bad_scope':control.scope_kind='reader'
        else:message.task_id=domain['task']
        db.commit()
    with pytest.raises((ArchiveError,AssertionError)) as error:
        package=encrypted(domain,tmp_path,world=True)
        with package.open('rb') as raw,prepare_backup(raw,'synthetic-test-password',tmp_path/'bad') as prepared:
            with SessionLocal() as db:restore_domain(db,prepared,domain['target'],fault=fault)
    assert isinstance(error.value,ArchiveError),str(error.value)
    with SessionLocal() as db:assert not db.scalar(select(WorldChatSession.id).where(WorldChatSession.owner_id==domain['target']))


@pytest.mark.parametrize('ending',['end_police','end_release','end_return'])
def test_blue_ending_receipts_and_participants_survive_cross_account_restore(domain,tmp_path,ending):
    from .test_world_chat_lifecycle import settled
    from app.scripts.plot import public_progress
    state,definition=settled(ending)
    def point_refs(value):
        if isinstance(value,list):return [point_refs(v) for v in value]
        if isinstance(value,dict):return {k:domain['checkpoint'] if k.endswith('CheckpointId') or k=='checkpointId' else point_refs(v) for k,v in value.items()}
        return value
    state=point_refs(state)
    def without_source_evidence(value):
        if isinstance(value,list):return [without_source_evidence(v) for v in value]
        if isinstance(value,dict):return {k:[] if k in {'evidence','sourceBlockIds'} else without_source_evidence(v) for k,v in value.items()}
        return value
    definition=without_source_evidence(definition)
    with SessionLocal() as db:
        cp=db.get(w.WorldCheckpoint,domain['checkpoint']);cp.state=state;cp.definition=definition;cp.content_hash=checkpoint_hash(state,definition,cp.text,cp.parent_checkpoint_id)
        db.get(w.World,domain['world']).definition=definition;db.commit()
    # Choose Holmes rather than the player to exercise the attribution defect.
    source=seed(domain)
    with SessionLocal() as db:
        chat=db.get(WorldChatSession,source['chat']);chat.character_id='holmes';db.commit()
    receipt=restored(domain,tmp_path,world=True);ids=receipt['idMap']
    with SessionLocal() as db:
        chat=db.get(WorldChatSession,ids[('world_chat',source['chat'])]);cp=db.get(w.WorldCheckpoint,chat.checkpoint_id)
        progress=public_progress(cp.state,cp.definition)
        assert progress['ending']['id']==ending and progress['ending']['phase']=='resolved'
        assert all(s['completed'] for s in progress['resolution']['steps'])
        attribution=chat.snapshot['participants'];assert '福尔摩斯' in attribution['character']['name']
        assert attribution['itemTransfers'][-1]['from']['name']=='华生'
        assert not attribution['itemTransfers'][-1]['from']['isCharacter']
        assert db.get(w.WorldCheckpoint,domain['checkpoint']).state==state


def test_newer_durable_chat_reply_survives_failed_sql_mirror_and_export(domain,tmp_path):
    from app.durable_stream import persist
    source=seed(domain,'running',True)
    with SessionLocal() as db:
        task=db.get(m.Task,source['task']);task.checkpoint={};db.commit()
    with SessionLocal() as db:
        task=db.get(m.Task,source['task'])
        persist(db,task,'worldStream','仅在持久文件中的真实片段🙂',stage='generation')
        db.rollback()  # Simulate a failed message/checkpoint transaction.
    receipt=restored(domain,tmp_path,world=True)
    with SessionLocal() as db:
        chat=db.get(WorldChatSession,receipt['idMap'][('world_chat',source['chat'])])
        reply=db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id==chat.id,WorldChatMessage.role=='assistant'))
        assert reply.text=='仅在持久文件中的真实片段🙂' and reply.status=='interrupted'
        assert chat.pending_task_id is None
        assert not db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id==source['chat'],WorldChatMessage.role=='assistant'))


def test_legacy_transcript_unverified_speaker_keeps_text_without_guessing_identity(domain,tmp_path):
    source=seed(domain)
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,domain['checkpoint']);memory=deepcopy(point.memory)
        memory['sceneTranscript']={'checkpointId':point.id,'segments':[
            {'kind':'speech','speakerId':'obsolete-script-token','speakerName':'同名人物','text':'旧对话原文'},
            {'kind':'speech','speakerId':source['character'],'speakerName':'已核验人物','text':'有效归因原文'}]}
        point.memory=memory;db.commit()
    receipt=restored(domain,tmp_path,world=True)
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,receipt['idMap'][('checkpoint',domain['checkpoint'])]);segments=point.memory['sceneTranscript']['segments']
        assert segments[0]=={'kind':'speech','speakerName':'同名人物','text':'旧对话原文'}
        assert segments[1]['speakerId']==receipt['idMap'][('world_entity',domain['world']+':'+source['character'])]
        assert db.get(w.WorldCheckpoint,domain['checkpoint']).memory==memory


def test_restore_fault_rolls_back_all_chat_and_memory_rows(domain,tmp_path):
    seed(domain);original=encrypted(domain,tmp_path,world=True)
    def failure(phase,kind,index):
        if phase=='after-flush' and kind=='play_memory_revision':raise RuntimeError('injected restore failure')
    with original.open('rb') as raw,prepare_backup(raw,'synthetic-test-password',tmp_path/'atomic') as prepared:
        with pytest.raises(RuntimeError,match='injected restore failure'):
            with SessionLocal() as db:restore_domain(db,prepared,domain['target'],fault=failure)
    with SessionLocal() as db:
        for model in (WorldChatSession,WorldChatMessage,PlayControl,MemoryRevision,w.World):
            assert not db.scalar(select(model.id).where(model.owner_id==domain['target']))


def test_export_receipt_lists_chat_and_memory_counts_instead_of_container_metrics(domain):
    seed(domain)
    with http_client(domain['owner']) as client:
        response=client.post('/api/reader/v1/stories/world-'+domain['world']+'/backup',data={'password':'synthetic-backup-password'},headers={'Idempotency-Key':'chat-counts'})
        assert response.status_code==201,response.text
        counts=response.json()['counts']
        assert counts['world_chat']==1 and counts['world_chat_message']==2
        assert counts['play_control']==2 and counts['play_memory_revision']==4
        assert 'formatVersion' not in counts and 'objects' not in counts


def test_archive_keeps_messages_and_memory_revisions_beyond_page_limits(domain,tmp_path):
    source=seed(domain)
    with SessionLocal() as db:
        for index in range(60):
            turn='older-'+str(index);task=m.Task(id=m.uid(),owner_id=domain['owner'],task_type='world_character_chat',state='succeeded',
                payload={'worldId':domain['world'],'lineId':source['line'],'chatId':source['chat'],'chatTurnKey':turn,'parentCheckpointId':domain['checkpoint']},result={},checkpoint={})
            db.add(task);db.flush()
            for offset,role in enumerate(('user','assistant')):
                db.add(WorldChatMessage(owner_id=domain['owner'],session_id=source['chat'],task_id=task.id,turn_key=turn,sequence=3+index*2+offset,role=role,text='历史消息'+str(index)+role,status='saved',checkpoint_id=domain['checkpoint']))
        control=db.get(PlayControl,source['controls'][1])
        for version in range(3,56):
            control.version=version;control.memory={'summary':'历史版本'+str(version),'notes':'完整保留'}
            db.add(MemoryRevision(owner_id=domain['owner'],control_id=control.id,version=version,memory=deepcopy(control.memory)))
        db.commit()
    receipt=restored(domain,tmp_path,world=True);ids=receipt['idMap']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WorldChatMessage).where(WorldChatMessage.session_id==ids[('world_chat',source['chat'])]))==122
        control=db.get(PlayControl,ids[('play_control',source['controls'][1])]);assert control.version==55
        versions=list(db.scalars(select(MemoryRevision).where(MemoryRevision.control_id==control.id).order_by(MemoryRevision.version)))
        assert len(versions)==55 and versions[0].memory['summary']=='chat第一版' and versions[-1].memory==control.memory
