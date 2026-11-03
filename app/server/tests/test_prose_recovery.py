"""Real HTTP/SSE bytes and finite destructive interleavings in the test DB."""
import json
import threading
import time
from copy import deepcopy
from types import SimpleNamespace
from sqlalchemy import select, func
from sqlalchemy.exc import OperationalError
from app import worker
from app.db import SessionLocal
from app.config import settings
from app.models import Task, TaskEvent, GenerationBatch, GenerationChapterVersion, ModelProfile, UsageRecord, Consent
from app.providers import MockAdapter, ChatCompletionsAdapter
from app.storage import private_path
from app.errors import Problem
from .helpers import BASE
from .test_prose_generations import isolated_prose, start, get
from .test_role_streaming import tls_stream

REAL_STREAM = ChatCompletionsAdapter.generate_stream


def _await_prose_fragment(client, receipt, expected):
    """Observe the real timer publication while the provider remains blocked.

    This is a finite DB/journal visibility barrier, not a replacement for the
    separate adapter-to-HTTP 150ms performance gate.
    """
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        visible = get(client, receipt)
        # The journal may precede the DB event. Wait for the committed mirror
        # before the deliberate second writer tests offset/epoch ordering.
        with SessionLocal() as db:
            mirrored = db.scalar(select(GenerationChapterVersion).where(
                GenerationChapterVersion.generation_id == receipt["generationId"],
                GenerationChapterVersion.candidate_version == 1,
                GenerationChapterVersion.chapter_index == 1))
            published = mirrored and mirrored.text == expected and db.scalar(
                select(TaskEvent.id).where(TaskEvent.task_id == receipt["taskId"],
                    TaskEvent.type == "text_delta").limit(1))
        if visible["chapters"][0]["text"] == expected and published:
            return visible
        time.sleep(.005)
    raise AssertionError("received prose fragment never became durably observable")


def test_actual_local_tls_stream_reaches_private_snapshot_before_full_return(client,reader,demo,profile,tls_stream,monkeypatch):
    state,local=tls_stream
    state.mode='hold'
    with SessionLocal() as db:
        p=db.get(ModelProfile,profile['id']);p.base_url=local.base_url;p.key_cipher=local.key_cipher
        p.model_id=local.model_id;p.supports_json=False;db.commit()
    receipt,_=start(client,reader,demo,profile)
    adapter=ChatCompletionsAdapter()
    monkeypatch.setattr(worker,'ChatCompletionsAdapter',lambda:SimpleNamespace(
        generate=lambda p,prompt,data,limit,**kw:MockAdapter().generate(p,prompt,data,limit),
        generate_stream=lambda *args,**kw:REAL_STREAM(adapter,*args,**kw)))
    monkeypatch.setattr(settings,'ai_mode','real')
    thread=threading.Thread(target=worker.process,args=(receipt['taskId'],));thread.start()
    try:
        end=time.monotonic()+2.5;visible=None
        while time.monotonic()<end:
            visible=get(client,receipt)
            if visible['chapters'][0]['text']:break
            time.sleep(.01)
        assert visible['chapters'][0]['text']=='眼前' and thread.is_alive()
        assert not state.completed.is_set()
        assert visible['chapters'][0]['durability']=='journaled' and not visible['chapters'][0]['canAccept']
        assert 'PRIVATE-REASONING' not in json.dumps(visible) and len(state.requests)==1
        assert state.requests[0]['body']['stream'] is True
        # The durable-file fallback can become observable just before the
        # independent DB mirror commits; wait for its actual event, keeping the
        # provider blocked rather than assuming both commits are simultaneous.
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            with SessionLocal() as db:
                deltas=list(db.scalars(select(TaskEvent).where(TaskEvent.task_id==receipt['taskId'],TaskEvent.type=='text_delta')))
            if deltas:break
            time.sleep(.01)
        assert deltas and deltas[-1].payload['delta']=='眼前' and thread.is_alive()
    finally:
        state.release.set();thread.join(10)
    assert not thread.is_alive() and len(state.requests)==1
    stopped=get(client,receipt)
    assert stopped['state']=='result-unknown' and stopped['chapters'][0]['text']=='眼前'
    assert not stopped['chapters'][0]['canAccept']


def test_delete_source_after_delta_before_known_return_settles_once_without_body_reviving(client,reader,demo,profile,monkeypatch):
    receipt,_=start(client,reader,demo,profile)
    received,release=threading.Event(),threading.Event();calls=[]
    def late(self,p,prompt,data,limit,*,on_delta,**kw):
        calls.append(1);on_delta('DELETE_PRIVATE_BODY😀')
        received.set();assert release.wait(10)
        return {'text':'DELETE_PRIVATE_BODY😀','finishReason':'stop','usage':{'state':'known','inputTokens':7,'outputTokens':9}}
    monkeypatch.setattr(MockAdapter,'generate_stream',late)
    thread=threading.Thread(target=worker.process,args=(receipt['taskId'],));thread.start()
    try:
        assert received.wait(5)
        _await_prose_fragment(client, receipt, 'DELETE_PRIVATE_BODY😀')
        assert thread.is_alive() and not release.is_set()
        assert get(client,receipt)['chapters'][0]['text']=='DELETE_PRIVATE_BODY😀'
        deleted=client.request('DELETE',BASE+'/books/'+demo['bookId'],json={'expectedVersion':1},headers={'Idempotency-Key':'delete-prose-source'})
        assert deleted.status_code==202,deleted.text
    finally:
        release.set();thread.join(10)
    assert not thread.is_alive() and calls==[1]
    assert client.get(BASE+'/generations/'+receipt['generationId']).status_code==404
    with SessionLocal() as db:
        usage=db.scalar(select(UsageRecord).where(UsageRecord.task_id==receipt['taskId'],UsageRecord.operation.like('generation:prose:%')))
        assert (usage.status,usage.usage_state,usage.input_tokens,usage.output_tokens)==('completed','known',7,9)
        p=db.get(ModelProfile,profile['id']);c=db.get(Consent,usage.consent_id)
        assert p.input_reserved==c.input_reserved and p.output_reserved==c.output_reserved
        assert not list(db.scalars(select(TaskEvent).where(TaskEvent.task_id==receipt['taskId'])))
        assert not list(db.scalars(select(GenerationBatch))) and not list(db.scalars(select(GenerationChapterVersion)))
        assert db.get(Task,receipt['taskId']).result=={'bookId':demo['bookId'],'deleted':True}
    folder=private_path(reader['user']['id'],'unused').parent
    assert not list(folder.glob('prose-stream-'+receipt['taskId']+'-*'))
    assert not list(folder.glob('journal-'+receipt['taskId']+'-*'))


def test_real_utf16_delta_offset_and_replacement_epoch_are_not_flush_versions(client,reader,demo,profile,monkeypatch):
    receipt,_=start(client,reader,demo,profile)
    from app.prose import stream
    def pieces(self,p,prompt,data,limit,*,on_delta,**kw):
        on_delta('甲😀')
        _await_prose_fragment(client, receipt, '甲😀')
        with SessionLocal() as db:
            task=db.get(Task,receipt['taskId']);ctx=stream.context(db,task,'generation:prose:v1:c1')
        stream.persist(ctx,'甲😀乙')
        raise Problem('PROVIDER_STATUS_UNKNOWN','interrupted',502)
    monkeypatch.setattr(MockAdapter,'generate_stream',pieces)
    worker.process(receipt['taskId'])
    with SessionLocal() as db:
        deltas=list(db.scalars(select(TaskEvent).where(TaskEvent.task_id==receipt['taskId'],TaskEvent.type=='text_delta').order_by(TaskEvent.seq)))
        assert [(e.payload['startUtf16'],e.payload['delta']) for e in deltas[:2]]==[(0,'甲😀'),(3,'乙')]
        row=db.scalar(select(GenerationChapterVersion));before_epoch=row.stream_version
        # The retained upstream buffer replaces the deliberately distinct newer
        # private fragment in finally; its smaller text must advance the epoch.
        assert before_epoch==2 and row.text=='甲😀'
        assert row.snapshot_version>row.stream_version
        replacements=list(db.scalars(select(TaskEvent).where(TaskEvent.task_id==receipt['taskId'],TaskEvent.type=='phase')))
        assert any(e.payload.get('phase')=='snapshot_replaced' for e in replacements)


def test_review_unknown_keeps_complete_prose_readable_and_acceptible(client,reader,demo,profile,monkeypatch):
    receipt,_=start(client,reader,demo,profile)
    original=MockAdapter.generate;calls=[]
    def fail_review(self,p,prompt,data,limit):
        calls.append(prompt.splitlines()[0])
        if prompt.startswith('prose-review-'):raise Problem('PROVIDER_STATUS_UNKNOWN','PRIVATE',502)
        return original(self,p,prompt,data,limit)
    monkeypatch.setattr(MockAdapter,'generate',fail_review)
    worker.process(receipt['taskId']);value=get(client,receipt)
    assert value['chapters'][0]['text'] and value['chapters'][0]['completeness']=='complete'
    assert value['chapters'][0]['canAccept'] and value['chapters'][0]['durability']=='saved'
    assert value['chapters'][0]['hints']==[{'code':'CHECK_UNAVAILABLE'}]
    assert value['chapters'][0]['factDelta']['status']=='unavailable'
    assert sum(name.startswith('prose-chapter-') for name in calls)==1
    assert sum(name.startswith('prose-review-') for name in calls)==1
