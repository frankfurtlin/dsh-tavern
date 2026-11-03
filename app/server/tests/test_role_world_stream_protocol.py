"""Finite actual PostgreSQL/API/atomic journal tests; synthetic own mock only."""
import json
from unittest.mock import patch
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from app.config import settings
from app.db import SessionLocal
from app.domain import digest, canonical
from app.models import Task, TaskEvent
from app.worker import persist_reply_stream, process
from app.world.jobs import persist_stream
from tests.test_role_streaming import prepare_chat, send
from tests.test_world import start, action

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    assert settings.ai_mode=='mock' and SessionLocal.kw['bind'].url.database=='reader_test'
    monkeypatch.setattr(settings,'storage_path',tmp_path/'private')

def record(task_id):
    with SessionLocal() as db:
        t=db.get(Task,task_id)
        return t.owner_id,t.checkpoint,list(db.scalars(select(TaskEvent).where(TaskEvent.task_id==task_id).order_by(TaskEvent.seq)))

def journal(task_id):
    owner,_,_=record(task_id)
    paths=list((settings.storage_path/owner).glob('journal-stream-'+task_id+'-*.json'))
    assert len(paths)==1,'received public fragment has no incremental journal'
    value=json.loads(paths[0].read_text('utf-8'))
    assert value['hash']==digest(canonical(value['value']))
    assert value['value']['contentHash']==digest(value['value']['text'])
    return value

def chat_task(client,reader,demo,profile):
    session=prepare_chat(client,reader,demo,profile)
    return send(client,session,'stream-protocol'),session

def test_role_emoji_delta_and_nested_replay(client,reader,demo,profile):
    tid,_=chat_task(client,reader,demo,profile)
    persist_reply_stream(tid,'你😀');persist_reply_stream(tid,'你😀好')
    _,cp,events=record(tid)
    deltas=[e.payload for e in events if e.type=='text_delta']
    assert [e['startUtf16'] for e in deltas]==[0,3]
    assert [e['delta'] for e in deltas]==['你😀','好']
    assert len({e['contentId'] for e in deltas})==1
    assert all(e['chapterIndex']==1 and e['streamVersion']==1 and e['durability']=='journaled' for e in deltas)
    assert journal(tid)['value']['text']=='你😀好'
    persist_reply_stream(tid,'你😀好')
    assert len(record(tid)[2])==len(events)
    # A terminal synthetic snapshot lets TestClient consume the finite SSE.
    with SessionLocal() as db:
        t=db.get(Task,tid);t.state='cancelled';t.persistence_state='saved';db.commit()
    response=client.get('/api/reader/v1/tasks/'+tid+'/events?afterSeq='+str(deltas and next(e.seq for e in events if e.type=='text_delta')))
    frames=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert frames and all('payload' in f for f in frames)
    assert any(f['payload'].get('startUtf16')==3 for f in frames)

def test_role_journal_survives_database_mirror_failure_and_snapshot_recovers(client,reader,demo,profile,monkeypatch):
    tid,_=chat_task(client,reader,demo,profile)
    from sqlalchemy.orm import Session
    commit=Session.commit
    def unavailable(db):raise OperationalError('COMMIT',{},Exception('synthetic mirror loss'))
    monkeypatch.setattr(Session,'commit',unavailable)
    try:persist_reply_stream(tid,'先收到😀再断库')
    except OperationalError:pass
    monkeypatch.setattr(Session,'commit',commit)
    assert journal(tid)['value']['text']=='先收到😀再断库'
    result=client.get('/api/reader/v1/tasks/'+tid).json()
    assert result['replyStream']['text']=='先收到😀再断库'
    assert result['replyStream']['durability']=='journaled'
    persist_reply_stream(tid,'先收到😀再断库')
    assert record(tid)[1]['replyStream']['text']=='先收到😀再断库'

def test_world_emoji_public_journal_provider_final_not_saved(client,reader,demo,profile):
    world,line=start(client,reader,demo,profile)
    tid=action(client,world,line)['taskId']
    with SessionLocal() as db:
        t=db.get(Task,tid);t.checkpoint={**t.checkpoint,'worldDeniedPhrases':['暗号甲']};db.commit()
    persist_stream(tid,'generation:scene:1','你😀',final=True)
    persist_stream(tid,'generation:scene:1','你😀好暗号甲',final=True)
    _,cp,events=record(tid)
    deltas=[e.payload for e in events if e.type=='text_delta']
    assert deltas[-1]['startUtf16']==3 and deltas[-1]['delta']=='好尚未明确的事情'
    value=journal(tid)
    assert '暗号甲' not in canonical(value) and value['value']['text']==cp['worldStream']['text']
    assert cp['worldStream']['durability']=='journaled'
    assert not [e for e in events if e.type=='chapter_saved']
    assert client.get('/api/world/v1/worlds/'+world['id']+'/lines/'+line['id']).json()['headCheckpointId']==line['headCheckpointId']

def test_role_final_message_saved_once_and_terminal_contract(client,reader,demo,profile):
    tid,session=chat_task(client,reader,demo,profile)
    process(tid)
    _,cp,events=record(tid)
    saved=[e for e in events if e.type=='chapter_saved']
    assert len(saved)==1 and saved[0].payload['durability']=='saved'
    assert cp['replyStream']['durability']=='saved'
    terminal=next(e.payload for e in reversed(events) if e.type=='terminal')
    assert {'outcome','providerState','persistenceState','acceptedPrefix','fee'}<=terminal.keys()
    process(tid)
    assert len(record(tid)[2])==len(events)

def test_world_final_checkpoint_saved_once(client,reader,demo,profile):
    from app.world.worker import process as world_process
    world,line=start(client,reader,demo,profile)
    tid=action(client,world,line)['taskId'];world_process(tid)
    _,cp,events=record(tid)
    saved=[e for e in events if e.type=='chapter_saved']
    assert len(saved)==1 and saved[0].payload['durability']=='saved'
    assert cp['worldStream']['durability']=='saved'
    world_process(tid);assert len(record(tid)[2])==len(events)

def test_role_epoch_replacement_is_snapshot_not_append(client,reader,demo,profile):
    tid,_=chat_task(client,reader,demo,profile)
    persist_reply_stream(tid,'旧😀');persist_reply_stream(tid,'新稿')
    stream=client.get('/api/reader/v1/tasks/'+tid).json()['replyStream']
    assert stream['text']=='新稿' and stream['streamVersion']==2
    assert record(tid)[2][-2].type=='phase'
    assert record(tid)[2][-2].payload['phase']=='snapshot_replaced'

def test_role_corrupt_journal_is_not_public_or_overwritten(client,reader,demo,profile):
    from app.errors import Problem
    tid,_=chat_task(client,reader,demo,profile);persist_reply_stream(tid,'公开正文')
    owner,_,_=record(tid);file=next((settings.storage_path/owner).glob('journal-stream-'+tid+'-*'))
    value=json.loads(file.read_text());value['value']['text']='篡改';file.write_text(json.dumps(value))
    response=client.get('/api/reader/v1/tasks/'+tid)
    assert response.status_code==409 and response.json()['code']=='JOURNAL_INVALID'
    with pytest.raises(Problem):persist_reply_stream(tid,'再次收到')
    assert record(tid)[1]['replyStream']['text']=='公开正文'

def test_role_lease_reclaim_and_tombstone_fence_incremental_journal(client,reader,demo,profile):
    from app.task_lease import ClaimedTask, leased_task
    from app.errors import Problem
    tid,_=chat_task(client,reader,demo,profile)
    with SessionLocal() as db:
        t=db.get(Task,tid);t.lease_owner='new-claim';db.commit()
    @leased_task
    def old_claim(t):persist_reply_stream(t,'旧执行器')
    old_claim(ClaimedTask(tid,'old-claim'))
    assert record(tid)[1]['replyStream']['text']==''
    persist_reply_stream(tid,'本人收到');owner,cp,events=record(tid)
    marker=settings.storage_path/owner/('book-deleted-'+tid+'.marker');marker.touch()
    with pytest.raises(Problem):persist_reply_stream(tid,'本人收到但已删除')
    assert record(tid)[1]==cp and len(record(tid)[2])==len(events)

def test_world_first_mirror_failure_snapshot_recovers_and_stream_is_public(client,reader,demo,profile,monkeypatch):
    from sqlalchemy.orm import Session
    world,line=start(client,reader,demo,profile);tid=action(client,world,line)['taskId']
    commit=Session.commit
    monkeypatch.setattr(Session,'commit',lambda db:(_ for _ in ()).throw(OperationalError('COMMIT',{},Exception('synthetic'))))
    persist_stream(tid,'generation:scene:1','已收到😀正文',final=True)
    monkeypatch.setattr(Session,'commit',commit)
    assert journal(tid)['value']['text']=='已收到😀正文'
    response=client.get('/api/world/v1/tasks/'+tid).json()
    assert response['stream']['text']=='已收到😀正文' and response['stream']['durability']=='journaled'
    persist_stream(tid,'generation:scene:1','已收到😀正文',final=True)
    assert record(tid)[1]['worldStream']['text']=='已收到😀正文'

def test_world_three_candidates_saved_content_ids_distinct_without_head_advance(client,reader,demo,profile):
    from app.world.worker import process as world_process
    world,line=start(client,reader,demo,profile)
    response=client.post('/api/world/v1/worlds/'+world['id']+'/lines/'+line['id']+'/write',json={'chapterCount':3,'expectedCheckpointId':line['headCheckpointId']},headers={'Idempotency-Key':'typed-three'})
    assert response.status_code==202;tid=response.json()['taskId'];world_process(tid)
    saved=[e.payload for e in record(tid)[2] if e.type=='chapter_saved']
    assert [e['chapterIndex'] for e in saved]==[1,2,3] and len({e['contentId'] for e in saved})==3
    assert client.get('/api/world/v1/worlds/'+world['id']+'/lines/'+line['id']).json()['headCheckpointId']==line['headCheckpointId']

def test_role_final_commit_failure_replays_known_calls_once(client,reader,demo,profile,monkeypatch):
    from sqlalchemy.orm import Session
    from app.models import UsageRecord,RoleMessage
    tid,_=chat_task(client,reader,demo,profile)
    original=Session.commit;fired=[]
    def fail_final(db):
        if not fired and any(isinstance(row,Task) and row.id==tid and row.state=='succeeded' for row in db.dirty):
            fired.append(True);raise OperationalError('COMMIT',{},Exception('after journal before final body'))
        return original(db)
    monkeypatch.setattr(Session,'commit',fail_final);process(tid);monkeypatch.setattr(Session,'commit',original)
    assert fired and record(tid)[1]['replyStream']['text']
    with SessionLocal() as db:
        before=list(db.scalars(select(UsageRecord.id).where(UsageRecord.task_id==tid)))
        assert not list(db.scalars(select(RoleMessage).where(RoleMessage.task_id==tid,RoleMessage.role=='assistant')))
    from app.providers import MockAdapter
    with patch.object(MockAdapter,'generate',side_effect=AssertionError('receipt recovery must not call again')):process(tid)
    with SessionLocal() as db:
        assert list(db.scalars(select(UsageRecord.id).where(UsageRecord.task_id==tid)))==before
        assert len(list(db.scalars(select(RoleMessage).where(RoleMessage.task_id==tid,RoleMessage.role=='assistant'))))==1
    assert len([e for e in record(tid)[2] if e.type=='chapter_saved'])==1

def test_world_deletion_committed_cleanup_fences_late_public_fragment(client,reader,demo,profile):
    from app.durable_stream import clean_deleted
    world,line=start(client,reader,demo,profile);tid=action(client,world,line)['taskId']
    persist_stream(tid,'generation:scene:1','删除前实际收到正文',final=True)
    owner,_,_=record(tid);file=next((settings.storage_path/owner).glob('journal-stream-'+tid+'-*'));assert file.exists()
    response=client.delete('/api/world/v1/worlds/'+world['id']);assert response.status_code==200
    assert client.get('/api/world/v1/tasks/'+tid).status_code==404
    clean_deleted();assert not file.exists()
    from app.errors import Problem
    with pytest.raises(Problem):persist_stream(tid,'generation:scene:1','迟到正文',final=True)
    assert not file.exists()

def test_journal_write_failure_never_publishes_event_then_retry_is_durable(client,reader,demo,profile,monkeypatch):
    from app import durable_stream
    tid,_=chat_task(client,reader,demo,profile);owner,cp,events=record(tid)
    writer=durable_stream._write_task_journal
    monkeypatch.setattr(durable_stream,'_write_task_journal',lambda *args:(_ for _ in ()).throw(OSError('own disk failure')))
    with pytest.raises(OSError):persist_reply_stream(tid,'已收到但磁盘失败')
    assert record(tid)[1]==cp and len(record(tid)[2])==len(events)
    observations=[]
    def ordered(owner_id,task_id,name,data):
        with SessionLocal() as db:observations.append(len(list(db.scalars(select(TaskEvent).where(TaskEvent.task_id==tid)))))
        return writer(owner_id,task_id,name,data)
    monkeypatch.setattr(durable_stream,'_write_task_journal',ordered);persist_reply_stream(tid,'重新落盘')
    assert observations==[len(events)] and journal(tid)['value']['text']=='重新落盘'

def test_role_cancel_retains_partial_with_consistent_history_counters(client,reader,demo,profile):
    from app.worker import fail_task
    tid,_=chat_task(client,reader,demo,profile);persist_reply_stream(tid,'已收到😀半句话')
    client.post('/api/reader/v1/tasks/'+tid+'/cancel');fail_task(tid,'TASK_CANCELLED')
    stream=client.get('/api/reader/v1/tasks/'+tid).json()['replyStream']
    assert stream['status']=='stopped' and stream['text']=='已收到😀半句话'
    assert stream['version']==stream['snapshotVersion'] and stream['durability']=='saved'
    assert journal(tid)['value']['status']=='stopped'

def test_world_sse_protocol_opaque_ids_are_not_secret_scrubbed(client,reader,demo,profile):
    from app.durable_stream import context
    world,line=start(client,reader,demo,profile);tid=action(client,world,line)['taskId']
    with SessionLocal() as db:
        task=db.get(Task,tid);identifier=context(task,'generation:scene:1')['contentId']
        task.checkpoint={**task.checkpoint,'worldDeniedPhrases':[identifier[0]]};db.commit()
    persist_stream(tid,'generation:scene:1','你😀公开正文',final=True)
    with SessionLocal() as db:
        task=db.get(Task,tid);task.state='cancelled';task.persistence_state='saved';db.commit()
    response=client.get('/api/world/v1/tasks/'+tid+'/events')
    frames=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    frame=next(f for f in frames if f['type']=='text_delta')
    assert frame['payload']=={'contentId':identifier,'chapterIndex':1,'streamVersion':1,'startUtf16':0,'delta':'你😀公开正文','durability':'journaled'}
    assert frame['text']==frame['payload']['delta'] and frame['offset']==0


def test_role_cancel_before_first_fragment_retains_request_metadata(client,reader,demo,profile,monkeypatch):
    from app.models import UsageRecord, RoleMessage
    from app.providers import MockAdapter
    tid,session=chat_task(client,reader,demo,profile)
    def forbidden(*args,**kwargs):
        pytest.fail("cancelled request must not start a provider stream")
    monkeypatch.setattr(MockAdapter,'generate_stream',forbidden)
    response=client.post('/api/reader/v1/tasks/'+tid+'/cancel')
    assert response.status_code==200
    process(tid)
    result=client.get('/api/reader/v1/tasks/'+tid)
    assert result.status_code==200 and result.json()['state']=='cancelled'
    stream=result.json()['replyStream']
    assert stream['sessionId']==session['id'] and stream['turnId']=='stream-turn'
    assert stream['userMessage']=='stream-protocol' and stream['text']==''
    assert stream['status']=='stopped' and stream['durability']=='journaled'
    assert stream['version']==stream['snapshotVersion'] and stream['contentId']
    with SessionLocal() as db:
        task=db.get(Task,tid)
        assert task.checkpoint['replyStream']['sessionId']==session['id']
        assert list(db.scalars(select(UsageRecord).where(UsageRecord.task_id==tid)))==[]
        messages=list(db.scalars(select(RoleMessage).where(RoleMessage.task_id==tid).order_by(RoleMessage.sequence)))
        assert [(m.role,m.text,m.status) for m in messages]==[('user','stream-protocol','stopped')]
    events=record(tid)[2]
    assert not [e for e in events if e.type=='chapter_saved']
    process(tid)
    assert len(record(tid)[2])==len(events)
