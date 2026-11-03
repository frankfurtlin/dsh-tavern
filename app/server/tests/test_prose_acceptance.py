"""Formal-prefix, lease and migration boundaries for reader-prose-v1."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, func, text, inspect
from app.db import SessionLocal, engine
from app.models import (Task, TaskEvent, Book, Branch, Revision, GenerationBatch,
    GenerationChapterVersion, GenerationAcceptance, UsageRecord, Progress, uid)
from app.providers import MockAdapter
from app import worker
from app.storage import private_path
from app.domain import canonical, digest
from .helpers import BASE, read_chapter, observe
from .test_prose_generations import isolated_prose, start, get, accept_body


def test_two_real_accept_connections_commit_one_prefix_and_preserve_other_draft(client,reader,demo,profile):
    receipt,payload=start(client,reader,demo,profile,count=3);worker.process(receipt['taskId'])
    snapshot=get(client,receipt);body=accept_body(snapshot,payload,count=1)
    with SessionLocal() as db:
        before=db.scalar(select(func.count()).select_from(Revision))
    barrier=threading.Barrier(2)
    def post(key):
        barrier.wait(timeout=5)
        return client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=body,headers={'Idempotency-Key':key})
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(post,'device-a');b=pool.submit(post,'device-b')
        results=[a.result(timeout=10),b.result(timeout=10)]
    assert sorted(r.status_code for r in results)==[200,409]
    assert next(r for r in results if r.status_code==409).json()['code']=='VERSION_CONFLICT'
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Revision))==before+1
        assert db.scalar(select(func.count()).select_from(GenerationAcceptance))==1
    after=get(client,receipt)
    assert after['acceptedPrefix']==1 and after['chapters'][1:]==snapshot['chapters'][1:]


def test_ai_natural_tail_appends_same_line_and_source_progress_does_not_unlock_new_prose(client,reader,demo,profile):
    first,payload=start(client,reader,demo,profile);worker.process(first['taskId'])
    accepted=client.post(BASE+'/generations/'+first['generationId']+'/accept',
        json=accept_body(get(client,first),payload,count=1),headers={'Idempotency-Key':'initial-child'})
    assert accepted.status_code==200,accepted.text
    child=accepted.json()
    book={'bookId':demo['bookId'],'branchId':child['branchId'],'revisionId':child['revisionId']}
    tail=read_chapter(client,book,1)['blocks'][-1]['endAnchor']
    progress=client.get(BASE+'/books/'+demo['bookId']+'/progress',params={'branchId':child['branchId'],'revisionId':child['revisionId']}).json()
    observe(client,reader,tail,expected=progress['version'],sequence=2)
    body={'protocol':'reader-prose-v1','originAnchor':tail,'operation':'continue_at_tail',
        'chapterCount':3,'chapterLength':'short','instruction':'继续核对证言。','profileId':profile['id'],
        'expectedHeadRevisionId':tail['revisionId'],'target':{'mode':'append_existing','name':'续写','branchId':child['branchId']}}
    response=client.post(BASE+'/books/'+demo['bookId']+'/generations',json=body,headers={'Idempotency-Key':'same-line-tail'})
    assert response.status_code==202,response.text
    receipt=response.json();assert get(client,receipt)['targetBranchId']==child['branchId']
    worker.process(receipt['taskId'])
    snapshot=get(client,receipt)
    answer=accept_body(snapshot,body,count=3);answer['target']='append'
    result=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=answer,headers={'Idempotency-Key':'same-line-prefix'})
    assert result.status_code==200 and result.json()['branchId']==child['branchId'],result.text
    with SessionLocal() as db:
        current=db.get(Revision,result.json()['revisionId'])
        assert len(current.manifest)==5
        observed=db.scalar(select(Progress).where(Progress.branch_id==child['branchId'],Progress.revision_id==current.id))
        assert observed.max_observed_anchor['blockId']==tail['blockId']
        assert db.get(Branch,demo['branchId']).head_revision_id==demo['revisionId']


def test_same_append_creation_can_explicitly_fork_before_first_accept_only(client,reader,demo,profile):
    first,payload=start(client,reader,demo,profile);worker.process(first['taskId'])
    result=client.post(BASE+'/generations/'+first['generationId']+'/accept',json=accept_body(get(client,first),payload),headers={'Idempotency-Key':'tail-base'}).json()
    book={'bookId':demo['bookId'],'branchId':result['branchId'],'revisionId':result['revisionId']}
    tail=read_chapter(client,book,1)['blocks'][-1]['endAnchor'];observe(client,reader,tail,expected=1,sequence=2)
    body={'protocol':'reader-prose-v1','originAnchor':tail,'operation':'continue_at_tail','chapterCount':3,
        'chapterLength':'short','instruction':'另一个方向。','profileId':profile['id'],'expectedHeadRevisionId':tail['revisionId'],
        'target':{'mode':'append_existing','name':'明确另开线','branchId':book['branchId']}}
    created=client.post(BASE+'/books/'+demo['bookId']+'/generations',json=body,headers={'Idempotency-Key':'explicit-tail-fork'})
    assert created.status_code==202,created.text
    receipt=created.json();worker.process(receipt['taskId']);snapshot=get(client,receipt)
    target={**accept_body(snapshot,body,count=1),'target':'child','sourcePolicy':'forkPinned'}
    fork=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=target,headers={'Idempotency-Key':'fork-once'})
    assert fork.status_code==200 and fork.json()['branchId']!=book['branchId'],fork.text
    sticky=get(client,receipt)
    second=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',
        json={**accept_body(sticky,body,count=3),'target':'child','sourcePolicy':'forkPinned'},headers={'Idempotency-Key':'no-second-target'})
    assert second.status_code==409 and second.json()['code']=='TARGET_INVALID'
    assert get(client,receipt)['acceptedPrefix']==1


def test_lease_reclaim_fences_late_final_phase_but_settles_received_usage(client,reader,demo,profile,monkeypatch):
    receipt,_=start(client,reader,demo,profile)
    entered,release=threading.Event(),threading.Event();calls=[]
    def held(self,p,prompt,data,limit,*,on_delta,**kw):
        calls.append(1);on_delta('旧执行器收到的字。');entered.set();assert release.wait(10)
        return {'text':'旧执行器收到的字。','finishReason':'stop','usage':{'state':'known','inputTokens':13,'outputTokens':17}}
    monkeypatch.setattr(MockAdapter,'generate_stream',held)
    thread=threading.Thread(target=worker.process,args=(receipt['taskId'],));thread.start()
    try:
        assert entered.wait(5)
        with SessionLocal() as db:
            task=db.get(Task,receipt['taskId']);task.lease_owner=uid();task.state='failed';task.persistence_state='saved'
            task.result={'phase':'new-owner-stopped','sentinel':'new-owner'}
            old_seq=task.event_seq;db.commit()
    finally:
        release.set();thread.join(10)
    assert not thread.is_alive() and calls==[1]
    with SessionLocal() as db:
        task=db.get(Task,receipt['taskId'])
        assert task.result=={'phase':'new-owner-stopped','sentinel':'new-owner'} and task.event_seq==old_seq
        usage=db.scalar(select(UsageRecord).where(UsageRecord.task_id==receipt['taskId'],UsageRecord.operation.like('generation:prose:%')))
        assert (usage.status,usage.usage_state,usage.input_tokens,usage.output_tokens)==('completed','known',13,17)
        assert db.get(Branch,demo['branchId']).head_revision_id==demo['revisionId']


def test_cancel_before_execution_has_zero_usage_and_no_false_complete_chapters(client,reader,demo,profile):
    receipt,_=start(client,reader,demo,profile,count=3)
    cancelled=client.post(BASE+'/generations/'+receipt['generationId']+'/cancel',json={},headers={'Idempotency-Key':'cancel-pre-send'})
    assert cancelled.status_code==200,cancelled.text
    worker.process(receipt['taskId']);snapshot=get(client,receipt)
    assert snapshot['state']=='cancelled' and snapshot['completedChapters']==0
    assert all(not c['text'] and not c['canAccept'] for c in snapshot['chapters'])
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(UsageRecord))==0


def test_migration_empty_roundtrip_keeps_old_book_progress_and_source_bytes(client,reader,demo):
    from app.models import SourceDocument
    with SessionLocal() as db:
        book=db.get(Book,demo['bookId']);source=db.get(SourceDocument,book.source_id)
        before={'book':book.id,'branch':demo['branchId'],'head':db.get(Branch,demo['branchId']).head_revision_id,
            'sourceMap':deepcopy(source.source_map),'sourceBytes':private_path(book.owner_id,source.normalized_ref).read_bytes()}
    config=Config('alembic.ini')
    try:
        command.downgrade(config,'0008')
        assert 'generation_batches' not in inspect(engine).get_table_names()
        assert client.get(BASE+'/generations/capabilities').json()=={'protocol':'reader-prose-v1','ready':False,'selectionWriting':True}
        command.upgrade(config,'head')
        assert client.get(BASE+'/generations/capabilities').json()=={'protocol':'reader-prose-v1','ready':True,'selectionWriting':True}
        with SessionLocal() as db:
            book=db.get(Book,demo['bookId']);source=db.get(SourceDocument,book.source_id)
            assert before=={'book':book.id,'branch':demo['branchId'],'head':db.get(Branch,demo['branchId']).head_revision_id,
                'sourceMap':source.source_map,'sourceBytes':private_path(book.owner_id,source.normalized_ref).read_bytes()}
            assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0022'
    finally:command.upgrade(config,'head')


def test_migration_with_received_generations_refuses_before_ddl(client,reader,demo,profile):
    receipt,_=start(client,reader,demo,profile);worker.process(receipt['taskId'])
    before=get(client,receipt)
    with pytest.raises(RuntimeError,match='Cannot downgrade'):
        command.downgrade(Config('alembic.ini'),'0008')
    assert get(client,receipt)==before
    with SessionLocal() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0022'
        assert db.scalar(select(func.count()).select_from(GenerationBatch))==1
