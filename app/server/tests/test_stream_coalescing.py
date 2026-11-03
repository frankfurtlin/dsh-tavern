"""Actual pipeline one-character bursts. All records are fresh own mock fixtures."""
import threading
import time
import pytest
from sqlalchemy import select,func
from app.config import settings
from app.db import SessionLocal
from app.models import Task,TaskEvent,UsageRecord
from app.providers import MockAdapter,ChatCompletionsAdapter
from app.worker import process
from tests.test_role_streaming import prepare_chat,send
from tests.test_prose_generations import start as prose_start,get as prose_get
from tests.test_world import start as world_start,action as world_action,API as WORLD_API,fast_fixture_stream

@pytest.fixture(autouse=True)
def guard(tmp_path,monkeypatch,fast_fixture_stream):
    assert settings.ai_mode=='mock'
    assert SessionLocal.kw['bind'].url.database=='reader_test'
    monkeypatch.setattr(settings,'storage_path',tmp_path)
    def forbidden(*args,**kwargs): raise AssertionError('No real provider attempt')
    monkeypatch.setattr(ChatCompletionsAdapter,'generate',forbidden)
    monkeypatch.setattr(ChatCompletionsAdapter,'generate_stream',forbidden)

@pytest.mark.parametrize('kind',['role','prose','world'])
def test_actual_one_character_burst_is_coalesced_and_pause_is_published_within_budget(client,reader,demo,profile,monkeypatch,kind):
    if kind=='role':
        session=prepare_chat(client,reader,demo,profile);task_id=send(client,session,turn='one-character-burst')
        prompt_prefix='role_chat-';runner=process
        def observe():
            value=client.get('/api/reader/v1/tasks/'+task_id).json()
            return value.get('replyStream',{}).get('text',''),value['state']
    elif kind=='prose':
        receipt,_=prose_start(client,reader,demo,profile,key='one-character-burst');task_id=receipt['taskId']
        prompt_prefix='prose-chapter-';runner=process
        def observe():
            value=prose_get(client,receipt)
            return (value['chapters'][0]['text'] if value['chapters'] else ''),value['state']
    else:
        world,line=world_start(client,reader,demo,profile);receipt=world_action(client,world,line,key='one-character-burst');task_id=receipt['taskId']
        from app.world.worker import process as world_process
        prompt_prefix='world-narrator-';runner=world_process
        def observe():
            value=client.get(WORLD_API+'/tasks/'+task_id).json()
            return value.get('stream',{}).get('text',''),value['state']
    from app import worker
    from app.prose import stream as prose_stream
    from app.world import jobs
    mirror_calls=[];journal_calls=[];pause=[];provider_calls=[]
    if kind=='prose':
        original=prose_stream.mirror
        def mirror(ctx,value,**kwargs):
            if ctx['taskId']==task_id: mirror_calls.append(len(value['text']))
            return original(ctx,value,**kwargs)
        monkeypatch.setattr(prose_stream,'mirror',mirror)
        original_journal=prose_stream._write_task_journal
        def journal(owner,tid,name,body):
            if tid==task_id: journal_calls.append(len(body))
            return original_journal(owner,tid,name,body)
        monkeypatch.setattr(prose_stream,'_write_task_journal',journal)
    else:
        name='persist_reply_stream' if kind=='role' else 'persist_stream'
        module=worker if kind=='role' else jobs;original=getattr(module,name)
        def mirror(tid,*args,**kwargs):
            if tid==task_id: mirror_calls.append(len(args[0] if kind=='role' else args[1]))
            return original(tid,*args,**kwargs)
        monkeypatch.setattr(module,name,mirror)
    emitted=[]
    def controlled(self,p,prompt,data,limit,*,on_delta,cancel_check,**kwargs):
        result=self.generate(p,prompt,data,limit)
        if not prompt.startswith(prompt_prefix):
            for offset in range(0,len(result['text']),256): cancel_check();on_delta(result['text'][offset:offset+256])
            return result
        provider_calls.append(prompt);text=result['text'];count=min(128,len(text));emitted.append(count)
        from app.world.state import public_stream
        with SessionLocal() as db: denied=list(db.get(Task,task_id).checkpoint.get('worldDeniedPhrases',[]))
        expected=public_stream(text[:count],denied) if kind=='world' else text[:count]
        if kind=='prose' and expected.startswith('标题：'):
            # Chapter titles are metadata; the reading snapshot exposes body prose.
            expected=expected.partition('\n\n')[2]
        cancel_check()
        for character in text[:count]: on_delta(character)
        started=time.perf_counter();seen=''
        while time.perf_counter()-started<.15:
            seen,state=observe()
            # World public filtering may withhold a suffix; count only authorized prose.
            if seen==expected: break
            time.sleep(.002)
        pause.append({'elapsed':time.perf_counter()-started,'visible':seen,'state':state,'expected':expected})
        time.sleep(.025) # no extra fragment; the deadline must independently publish
        for offset in range(count,len(text),256): cancel_check();on_delta(text[offset:offset+256])
        return result
    monkeypatch.setattr(MockAdapter,'generate_stream',controlled)
    from app.worker import claim as reader_claim
    from app.world.worker import claim as world_claim
    claimed=(world_claim if kind=='world' else reader_claim)('coalescing-fixture-worker')
    assert claimed==task_id
    runner(claimed)
    text,state=observe()
    with SessionLocal() as db:
        task=db.get(Task,task_id);assert task.state=='succeeded' and task.persistence_state=='saved'
        events=db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id==task_id))
        usages=list(db.scalars(select(UsageRecord).where(UsageRecord.task_id==task_id)))
        assert usages and all(u.status=='completed' for u in usages)
    assert len(provider_calls)==1 and emitted and pause[0]['state'] not in {'queued','succeeded','failed','cancelled'}
    assert pause[0]['elapsed']<.15
    assert pause[0]['visible']==pause[0]['expected']
    prefix=pause[0]['visible']
    assert text.startswith(prefix)
    assert len(mirror_calls)<max(8,emitted[0]//4), {'kind':kind,'rawCharacters':emitted[0],'mirrorCalls':len(mirror_calls),'journalCalls':len(journal_calls)}
    if kind=='prose': assert len(journal_calls)<max(8,emitted[0]//4)
    before=(len(mirror_calls),len(journal_calls),events)
    time.sleep(.05)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id==task_id))==before[2]
    assert (len(mirror_calls),len(journal_calls))==before[:2]
    assert not [t for t in threading.enumerate() if t.name.startswith('reader-stream-deadline-')]


@pytest.mark.parametrize('fence',['cancel','lease'])
def test_deadline_publication_rechecks_execution_fence_and_joins_before_terminal(client,reader,demo,profile,monkeypatch,fence):
    from app.worker import claim
    from app.models import uid
    session=prepare_chat(client,reader,demo,profile)
    task_id=send(client,session,turn='pending-deadline-'+fence)
    claimed=claim('deadline-fence-fixture-worker');assert claimed==task_id
    attempts=[];checks=[];at_fence=[]
    original=MockAdapter.generate_stream
    def controlled(self,p,prompt,data,limit,*,on_delta,cancel_check,**kwargs):
        if not prompt.startswith('role_chat-'):
            return original(self,p,prompt,data,limit,on_delta=on_delta,cancel_check=cancel_check,**kwargs)
        attempts.append(1)
        on_delta('已收到而未到截止时间的正文。')
        with SessionLocal() as db:
            task=db.get(Task,task_id)
            if fence=='cancel':task.cancel_requested=True
            else:task.lease_owner=uid()
            db.commit()
            at_fence.append((task.event_seq,task.checkpoint.get('replyStream',{}).get('text','')))
        time.sleep(.06) # timer must independently notice the invalid context
        try:
            cancel_check()
        except BaseException as error:
            checks.append(type(error).__name__)
            raise
        raise AssertionError('An invalid timer scope was not propagated')
    monkeypatch.setattr(MockAdapter,'generate_stream',controlled)
    process(claimed)
    assert attempts==[1]
    assert checks==(['Problem'] if fence=='cancel' else ['LeaseLost'])
    with SessionLocal() as db:
        task=db.get(Task,task_id)
        if fence=='cancel':
            assert task.state=='cancelled'
            assert task.checkpoint['replyStream']['text']=='已收到而未到截止时间的正文。'
        else:
            assert task.event_seq==at_fence[0][0]
            assert task.checkpoint.get('replyStream',{}).get('text','')==at_fence[0][1]
        before=(task.event_seq,dict(task.checkpoint))
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id==task_id))==1
    assert not [t for t in threading.enumerate() if t.name.startswith('reader-stream-deadline-')]
    time.sleep(.05)
    with SessionLocal() as db:
        task=db.get(Task,task_id);assert (task.event_seq,task.checkpoint)==before


def test_deadline_close_flushes_once_and_propagates_publisher_failure_without_a_surviving_thread():
    from app.worker import _DeadlineStreamBuffer
    from contextvars import ContextVar
    context=ContextVar('stream-test-context',default='missing');token=context.set('execution')
    seen=[]
    def publish(text):
        seen.append((context.get(),text))
        raise RuntimeError('synthetic publisher failure')
    try:
        buffer=_DeadlineStreamBuffer('own-close',publish)
        buffer.add('尾段')
        with pytest.raises(RuntimeError,match='synthetic publisher failure'):buffer.close()
        assert seen==[('execution','尾段')]
        with pytest.raises(RuntimeError,match='synthetic publisher failure'):buffer.add('不得继续')
        with pytest.raises(RuntimeError,match='synthetic publisher failure'):buffer.close()
        assert seen==[('execution','尾段')]
        assert not buffer._thread.is_alive()
    finally:
        context.reset(token)
