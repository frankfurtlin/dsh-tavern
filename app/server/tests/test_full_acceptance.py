"""Remaining source-level acceptance gates, using actual PostgreSQL transactions."""
import io,json,zipfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.db import SessionLocal
from app.models import Branch,Revision,Candidate,UsageRecord,Book,AnalysisChunk,KnowledgeSnapshot,Progress,Outbox
from app.providers import MockAdapter
from app.importer import epub_parse,txt_parse
from app.errors import Problem
from .helpers import BASE,read_chapter,observe,operation,accept
from .test_reader import prepare_generation

def test_F04_no_heading_directory_confirmation_and_original_hash(client,reader):
    from app.models import SourceDocument
    from app.storage import private_path
    from app.domain import digest
    raw='无标题故事。\r\n甲😀乙。\r\n同名段落。\r\n同名段落。'.encode('utf-8-sig')
    uploaded=client.post(BASE+'/books/imports',files={'file':('untitled.txt',raw,'text/plain')},headers={'Idempotency-Key':'untitled'}).json()
    from app.worker import process
    process(uploaded['taskId'])
    preview=client.get(BASE+'/books/imports/'+uploaded['taskId']).json()
    assert preview['importStatus']=='awaiting_confirmation' and len(preview['chapters'])==1
    confirmed=client.post(BASE+f"/books/imports/{uploaded['taskId']}/confirm",json={'title':'无标题','parserVersion':'import-v1','chapterOrder':[preview['chapters'][0]['id']]},headers={'Idempotency-Key':'untitled-confirm'})
    assert confirmed.status_code==201
    chapter=read_chapter(client,confirmed.json())
    assert [b['text'] for b in chapter['blocks']]==['无标题故事。','甲😀乙。','同名段落。','同名段落。']
    with SessionLocal() as db:
        source=db.scalar(select(SourceDocument).where(SourceDocument.book_id==confirmed.json()['bookId']))
        assert source.file_hash==digest(raw)==digest(private_path(source.owner_id,source.file_ref).read_bytes())
        assert db.scalar(select(func.count()).select_from(UsageRecord))==0

def test_F04_upload_and_parser_failure_never_create_readable_book(client,reader):
    from app.worker import process
    from app.importer import MAX_UPLOAD
    oversize=client.post(BASE+'/books/imports',files={'file':('too-large.txt',b'x'*(MAX_UPLOAD+1),'text/plain')},headers={'Idempotency-Key':'oversize'})
    assert oversize.status_code==422 and oversize.json()['code']=='IMPORT_LIMIT'
    upload=client.post(BASE+'/books/imports',files={'file':('broken.epub',b'broken','application/epub+zip')},headers={'Idempotency-Key':'broken'}).json()
    process(upload['taskId'])
    failed=client.get(BASE+'/books/imports/'+upload['taskId']).json()
    assert failed['importStatus']=='failed' and failed['state']=='failed'
    assert client.post(BASE+f"/books/imports/{upload['taskId']}/confirm",json={'title':'不可读'},headers={'Idempotency-Key':'broken-confirm'}).status_code==422
    assert client.get(BASE+'/books').json()['items']==[]
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(UsageRecord))==0

@pytest.mark.parametrize('kind',['entries','resource','total','ratio'])
def test_F02_actual_zip_limits_rejected_before_resource_extraction(kind):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED if kind=='ratio' else zipfile.ZIP_STORED) as z:
        if kind=='entries':
            for index in range(10001):z.writestr(f'{index}.txt',b'')
        elif kind=='resource':z.writestr('oversize.bin',b'x'*(20*1024*1024+1))
        elif kind=='total':
            resource=b'x'*(19*1024*1024)
            for index in range(11):z.writestr(f'{index}.bin',resource)
        else:z.writestr('compressed.bin',b'0'*65536)
    with pytest.raises(Problem) as error:epub_parse(buffer.getvalue(),'0'*32,'limit')
    assert error.value.code=='IMPORT_LIMIT'

def test_F04_actual_10001_chapter_limit():
    text=''.join(f'第{i+1}章\n正文。\n' for i in range(10001))
    with pytest.raises(Problem) as error:txt_parse(text.encode())
    assert error.value.code=='IMPORT_LIMIT'

def test_F06_exact_c1p3_start_boundary_and_sibling_revisions_immutable(client,reader,demo,profile):
    chapter=read_chapter(client,demo)
    anchor=chapter['blocks'][2]['startAnchor']
    observe(client,reader,chapter['blocks'][2]['endAnchor'])
    first,_,_=operation(client,profile,anchor,'continue_from_anchor')
    saved=accept(client,first,key='first').json()
    with SessionLocal() as db:
        hashes={r.id:r.content_hash for r in db.scalars(select(Revision))}
    second,_,_=operation(client,profile,anchor,'counterfactual','留下救人')
    sibling=accept(client,second,key='second').json()
    assert sibling['branchId']!=saved['branchId']
    with SessionLocal() as db:
        for revision_id,hashed in hashes.items():assert db.get(Revision,revision_id).content_hash==hashed
        revision=db.get(Revision,sibling['revisionId'])
        assert revision.manifest[0]['blocks']==[b['id'] for b in chapter['blocks'][:2]]
        assert all(chapter['blocks'][2]['id'] not in c['blocks'] for c in revision.manifest)

def test_F09_concurrent_accept_same_candidate_different_keys_commits_once(client,reader,demo,profile):
    task,_=prepare_generation(client,reader,demo,profile)
    candidate_id=task['result']['candidateId'];barrier=Barrier(2)
    headers={'Authorization':client.headers['Authorization']}
    body={'expectedHeadRevisionId':demo['revisionId'],'sourcePolicy':'require_unchanged_head','target':{'mode':'new_child_branch','name':'并发接受'}}
    def commit(index):
        with TestClient(app,raise_server_exceptions=False) as device:
            barrier.wait(timeout=10)
            response=device.post(BASE+'/candidates/'+candidate_id+'/accept',json=body,headers={**headers,'Idempotency-Key':f'concurrent-{index}'})
            return response.status_code,response.json()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(commit,[1,2]))
    assert all(code==200 for code,_ in results),results
    assert results[0][1]==results[1][1]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Branch))==2
        assert db.scalar(select(func.count()).select_from(Revision))==2
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event_type=='story_committed'))==1

def test_F22_failed_accept_transaction_rolls_back_then_same_candidate_retries_without_generation(client,reader,demo,profile):
    from sqlalchemy.orm import Session
    from app.models import Idempotency
    task,_=prepare_generation(client,reader,demo,profile)
    candidate_id=task['result']['candidateId']
    with SessionLocal() as db:usage_before=db.scalar(select(func.count()).select_from(UsageRecord))
    original=Session.commit
    injected=[]
    def fail_accept(session):
        if any(isinstance(row,Idempotency) and row.scope.startswith('accept:') for row in session.new):
            injected.append(True)
            raise RuntimeError('Injected acceptance persistence failure')
        return original(session)
    with patch.object(Session,'commit',fail_accept):response=accept(client,task,key='transaction-retry')
    assert response.status_code==503 and injected==[True]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Branch))==1
        assert db.scalar(select(func.count()).select_from(Revision))==1
        assert db.get(Candidate,candidate_id).status=='pending'
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.event_type=='story_committed'))==0
    with patch.object(MockAdapter,'generate',side_effect=AssertionError('Acceptance cannot call a model')):retried=accept(client,task,key='transaction-retry')
    assert retried.status_code==200
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(UsageRecord))==usage_before

def test_F20_twenty_chunk_batches_require_new_consent_and_reuse_prior_evidence(client,reader,profile):
    from app.worker import process
    text='第一章\n'+'\n'.join('水岸船声雾灯'*250+str(i) for i in range(32))
    upload=client.post(BASE+'/books/imports',files={'file':('batches.txt',text.encode(),'text/plain')},headers={'Idempotency-Key':'batches'}).json();process(upload['taskId'])
    book=client.post(BASE+f"/books/imports/{upload['taskId']}/confirm",json={'title':'分批'},headers={'Idempotency-Key':'batch-confirm'}).json()
    anchor=read_chapter(client,book)['blocks'][-1]['endAnchor'];observe(client,reader,anchor)
    payload={'operation':'analysis','profileId':profile['id'],'anchor':anchor,'expectedHeadRevisionId':anchor['revisionId']}
    planned=client.post(BASE+'/operation-plans',json=payload).json()
    # This test authorizes the full synthetic book, independently of chunk size.
    profile=client.patch(BASE+'/model-profiles/'+profile['id'],json={'expectedVersion':profile['version'],'callBudget':planned['missingAnalysisBlocks']+10}).json()
    planned=client.post(BASE+'/operation-plans',json=payload).json()
    assert planned['missingAnalysisBlocks']>20 and planned['batchAnalysisBlocks']==20 and planned['maxCalls']==20
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(UsageRecord))==0
    denied=client.post(BASE+'/operation-plans',json={**payload,'operation':'continue_from_anchor'})
    assert denied.status_code==422 and denied.json()['code']=='AI_CONTEXT_NOT_READY'
    # Viewing plans/refusing consent leaves normal reading available and zero calls.
    assert read_chapter(client,book)['blocks']
    first,_,_=operation(client,profile,anchor,'analysis')
    assert first['result']['aiStatus']=='ai_partial' and first['result']['analysisCompletedBlocks']==20
    with SessionLocal() as db:
        chunks_before=db.scalar(select(func.count()).select_from(AnalysisChunk))
        usage_before=db.scalar(select(func.count()).select_from(UsageRecord))
    assert chunks_before==usage_before==20
    remaining=client.post(BASE+'/operation-plans',json=payload).json()
    assert remaining['missingAnalysisBlocks']==planned['missingAnalysisBlocks']-20
    second,_,_=operation(client,profile,anchor,'analysis')
    while second['result']['aiStatus']=='ai_partial':
        next_plan=client.post(BASE+'/operation-plans',json=payload).json()
        assert 0<next_plan['batchAnalysisBlocks']<=20
        second,_,_=operation(client,profile,anchor,'analysis')
    assert second['result']['aiStatus']=='ai_ready'
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord))==planned['missingAnalysisBlocks']
        assert db.get(KnowledgeSnapshot,second['result']['snapshotId']).completeness=='complete'

def test_F20_partial_analysis_failure_keeps_completed_chunk_and_new_consent_resumes(client,reader,profile):
    from app.worker import process
    text='第一章\n'+'\n'.join('水岸船声雾灯'*250+str(i) for i in range(5))
    uploaded=client.post(BASE+'/books/imports',files={'file':('partial.txt',text.encode(),'text/plain')},headers={'Idempotency-Key':'partial'}).json();process(uploaded['taskId'])
    book=client.post(BASE+f"/books/imports/{uploaded['taskId']}/confirm",json={'title':'局部分析'},headers={'Idempotency-Key':'partial-confirm'}).json()
    anchor=read_chapter(client,book)['blocks'][-1]['endAnchor'];observe(client,reader,anchor)
    original=MockAdapter.generate;calls=[]
    expected_chunks=client.post(BASE+'/operation-plans',json={'operation':'analysis','profileId':profile['id'],'anchor':anchor,'expectedHeadRevisionId':anchor['revisionId']}).json()['missingAnalysisBlocks']
    def fail_second(self,*arguments):
        calls.append(True)
        if len(calls)==2:raise Problem('PROVIDER_STATUS_UNKNOWN','Fixture interrupted call',502)
        return original(self,*arguments)
    with patch.object(MockAdapter,'generate',fail_second):failed,_,_=operation(client,profile,anchor,'analysis')
    assert failed['state']=='failed' and failed['errorCode']=='PROVIDER_STATUS_UNKNOWN'
    assert read_chapter(client,book)['blocks']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisChunk))==1
        assert db.scalar(select(func.count()).select_from(UsageRecord))==2
    resumed,_,_=operation(client,profile,anchor,'analysis')
    assert resumed['state']=='succeeded' and resumed['result']['aiStatus']=='ai_ready'
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisChunk))==expected_chunks
        # The completed first chunk is reused; the interrupted call remains accounted.
        assert db.scalar(select(func.count()).select_from(UsageRecord))==expected_chunks+1
        unknown=db.scalar(select(UsageRecord).where(UsageRecord.task_id==failed['id'],UsageRecord.usage_state=='unknown'))
        assert unknown is not None and unknown.output_tokens is None and unknown.reserved_output>0
