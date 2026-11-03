"""Durable selected AI transport on a dedicated PostgreSQL test database."""
from copy import deepcopy
import json
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app.config import settings
from app.domain import make_anchor,utf16_slice,digest
from app.models import Branch,Revision,ContentBlock,GenerationChapterVersion,ModelProfile,UsageRecord,Idempotency
from app.providers import MockAdapter,ChatCompletionsAdapter
from app.worker import process
from app.prose.schemas import CreateGeneration
from .helpers import BASE,read_chapter,observe

BODY='林渡把封好的信轻轻放下，手指停在潮湿的纸边。😀'

@pytest.fixture(autouse=True)
def finite_selected_provider(tmp_path,monkeypatch):
    assert settings.ai_mode=='mock' and SessionLocal.kw['bind'].url.database=='selection_generation_test'
    monkeypatch.setattr(settings,'storage_path',tmp_path)
    calls=[]
    original=MockAdapter.generate
    def forbidden(*a,**kw):raise AssertionError('Real provider forbidden')
    monkeypatch.setattr(ChatCompletionsAdapter,'generate',forbidden)
    monkeypatch.setattr(ChatCompletionsAdapter,'generate_stream',forbidden)
    def generated(self,p,prompt,data,limit,**kwargs):
        if prompt.startswith('prose-selection-v1'):
            calls.append({'data':deepcopy(data),'model':p.model_id,'prompt':prompt})
            return {'text':BODY,'finishReason':'stop','latencyMs':1,'usage':{'state':'known','inputTokens':100,'outputTokens':50}}
        if prompt.startswith('prose-review'):
            return {'text':json.dumps({'completeChapter':True,'issues':['SPOILER','CONTINUITY']}),'finishReason':'stop','latencyMs':1,'usage':{'state':'known','inputTokens':100,'outputTokens':30}}
        return original(self,p,prompt,data,limit,**kwargs)
    def streamed(self,p,prompt,data,limit,*,on_delta,cancel_check,**kwargs):
        output=self.generate(p,prompt,data,limit,**kwargs)
        for chunk in (output['text'][:8],output['text'][8:]):
            cancel_check();on_delta(chunk)
        return output
    monkeypatch.setattr(MockAdapter,'generate',generated)
    monkeypatch.setattr(MockAdapter,'generate_stream',streamed)
    return calls

def request(client,reader,demo,profile,purpose='rewrite',key='own-selection-generate'):
    chapter=read_chapter(client,demo)
    with SessionLocal() as db:
        block=db.get(ContentBlock,chapter['blocks'][0]['id'])
        left=make_anchor(demo['bookId'],demo['branchId'],chapter['revisionId'],block,2)
        right=make_anchor(demo['bookId'],demo['branchId'],chapter['revisionId'],block,8)
        original=utf16_slice(block.text,2,8)
    observe(client,reader,right)
    body={'protocol':'reader-prose-v1','originAnchor':right,'operation':'continue_from_anchor','chapterCount':1,
        'chapterLength':'short','instruction':'写出具体动作。','profileId':profile['id'],
        'expectedHeadRevisionId':chapter['revisionId'],'target':{'mode':'new_child_branch','name':'选区写法'},
        'selection':{'purpose':purpose,'startAnchor':left,'endAnchor':right,'sourcePolicy':'requireUnchanged'}}
    response=client.post(BASE+'/books/'+demo['bookId']+'/generations',json=body,headers={'Idempotency-Key':key})
    assert response.status_code==202,response.text
    return response.json(),body,original

def snapshot(client,receipt,version=None):
    response=client.get(BASE+'/generations/'+receipt['generationId']+('' if version is None else '?candidateVersion='+str(version)))
    assert response.status_code==200,response.text
    return response.json()

def apply(client,receipt,body,key='own-selected-accept',version=1,target=None):
    data={'candidateVersion':version,'expectedSourceHeadRevisionId':body['expectedHeadRevisionId'],
        'sourcePolicy':body['selection']['sourcePolicy'],'targetBranchId':target['branchId'] if target else None,
        'expectedTargetHeadRevisionId':target['revisionId'] if target else None}
    return client.post(BASE+'/generations/'+receipt['generationId']+'/selection-accept',json=data,headers={'Idempotency-Key':key})

@pytest.mark.parametrize('purpose',['expand','rewrite'])
def test_exact_selection_stream_and_receipt(client,reader,demo,profile,finite_selected_provider,purpose):
    receipt,body,original=request(client,reader,demo,profile,purpose)
    replay=client.post(BASE+'/books/'+demo['bookId']+'/generations',json=body,headers={'Idempotency-Key':'own-selection-generate'})
    assert replay.json()==receipt
    process(receipt['taskId']);value=snapshot(client,receipt)
    assert value['state']=='succeeded' and value['selection']==body['selection']
    assert value['chapters'][0]['text']==BODY and value['chapters'][0]['canAccept']
    assert len(finite_selected_provider)==1 and finite_selected_provider[0]['data']['selection']['originalText']==original
    assert {'SPOILER','CONTINUITY'} <= {h['code'] for h in value['chapters'][0]['hints']}
    assert value['chapters'][0]['rawPresentation']['attempts'][0]['raw']==BODY
    accepted=apply(client,receipt,body);assert accepted.status_code==201,accepted.text
    assert apply(client,receipt,body).json()==accepted.json()
    with SessionLocal() as db:
        result=accepted.json();revision=db.get(Revision,result['revisionId']);proof=revision.provenance['selectionEdit']
        assert proof['replacementText']==(original+'\n\n'+BODY if purpose=='expand' else BODY)
        assert revision.provenance['generationSource']['generationId']==receipt['generationId']
        assert db.get(Branch,demo['branchId']).head_revision_id==body['expectedHeadRevisionId']
        assert len(finite_selected_provider)==1
    ordinary=client.get(BASE+'/generations',params={'bookId':demo['bookId'],'branchId':demo['branchId']})
    selected=client.get(BASE+'/generations',params={'bookId':demo['bookId'],'branchId':demo['branchId'],'selections':'true','sourceRevisionId':body['originAnchor']['revisionId'],'startBlockId':body['selection']['startAnchor']['blockId'],'endOffset':8})
    assert ordinary.status_code==selected.status_code==200 and not ordinary.json()['items']
    assert [r['id'] for r in selected.json()['items']]==[receipt['generationId']]

def test_selection_cannot_enter_normal_chapter_acceptance(client,reader,demo,profile,finite_selected_provider):
    receipt,body,_=request(client,reader,demo,profile);process(receipt['taskId'])
    response=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json={'candidateVersion':1,'prefixCount':1,'expectedAcceptedPrefix':0,'expectedHeadRevisionId':body['expectedHeadRevisionId'],'target':'child','sourcePolicy':'requireUnchanged'},headers={'Idempotency-Key':'wrong-chapter-accept'})
    assert response.status_code==409 and response.json()['code']=='SELECTION_ACCEPT_REQUIRED'
    assert apply(client,receipt,body).status_code==201

def test_queued_archive_prevents_any_selected_transport(client,reader,demo,profile,finite_selected_provider):
    receipt,body,_=request(client,reader,demo,profile)
    with SessionLocal() as db:
        branch=db.get(Branch,demo['branchId']);branch.status='archived';db.commit()
    process(receipt['taskId'])
    assert not finite_selected_provider
    assert snapshot(client,receipt)['chapters'][0]['text']==''
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(UsageRecord))==0

def test_saved_candidate_archive_and_receipt_replay(client,reader,demo,profile,finite_selected_provider):
    receipt,body,_=request(client,reader,demo,profile);process(receipt['taskId'])
    with SessionLocal() as db:
        branch=db.get(Branch,demo['branchId']);branch.status='archived';db.commit()
    assert apply(client,receipt,body).status_code==409
    assert snapshot(client,receipt)['chapters'][0]['text']==BODY
    with SessionLocal() as db:
        branch=db.get(Branch,demo['branchId']);branch.status='active';db.commit()
    committed=apply(client,receipt,body);assert committed.status_code==201,committed.text
    with SessionLocal() as db:
        branch=db.get(Branch,demo['branchId']);branch.status='archived';db.commit()
    assert apply(client,receipt,body).json()==committed.json() and len(finite_selected_provider)==1

def test_frozen_model_and_old_candidates(client,reader,demo,profile,finite_selected_provider):
    receipt,body,_=request(client,reader,demo,profile)
    with SessionLocal() as db:
        live=db.get(ModelProfile,profile['id']);live.model_id='new-model-must-not-send';live.version+=1;db.commit()
    process(receipt['taskId']);assert finite_selected_provider[0]['model']=='fixture-v1'
    rewritten=client.post(BASE+'/generations/'+receipt['generationId']+'/rewrite',json={'candidateVersion':1,'expectedAcceptedPrefix':0,'instruction':'换一个角度。'},headers={'Idempotency-Key':'own-selection-rewrite'})
    assert rewritten.status_code==202,rewritten.text
    process(receipt['taskId']);assert snapshot(client,receipt)['candidateVersion']==2
    assert snapshot(client,receipt,1)['chapters'][0]['text']==BODY
    assert apply(client,receipt,body,version=1).status_code==201
    assert len(finite_selected_provider)==2 and finite_selected_provider[1]['model']=='fixture-v1'

@pytest.mark.parametrize('fault',['chapterCount','operation','target','originAnchor'])
def test_invalid_combination_never_dispatches(client,reader,demo,profile,finite_selected_provider,fault):
    receipt,body,_=request(client,reader,demo,profile)
    changed=deepcopy(body)
    if fault=='chapterCount':changed[fault]=3
    elif fault=='operation':changed[fault]='counterfactual'
    elif fault=='target':changed[fault]={'mode':'append_existing','name':'bad','branchId':demo['branchId']}
    else:changed[fault]=changed['selection']['startAnchor']
    response=client.post(BASE+'/books/'+demo['bookId']+'/generations',json=changed,headers={'Idempotency-Key':'bad-selection'})
    assert response.status_code==422 and not finite_selected_provider

def test_legacy_request_serialization_preserves_existing_intent_hash():
    # Use a static valid DTO, no provider/database material in the assertion.
    point={'bookId':'b','branchId':'l','revisionId':'r','chapterId':'c','blockId':'p','start':1,'end':1,'offsetUnit':'utf16','quoteHash':'0'*64}
    old={'protocol':'reader-prose-v1','originAnchor':point,'operation':'continue_from_anchor','chapterCount':1,'chapterLength':'short','instruction':'','profileId':'m','expectedHeadRevisionId':'r','target':{'mode':'new_child_branch','name':'line'}}
    assert 'selection' not in CreateGeneration.model_validate(old).model_dump()

def test_old_branch_candidate_uses_pinned_bytes_after_a_real_manual_family_advances(client,reader,demo,profile,finite_selected_provider):
    _,initial,_=request(client,reader,demo,profile)
    edit={'startAnchor':initial['selection']['startAnchor'],'endAnchor':initial['selection']['endAnchor'],
        'replacementText':'旧灯前的信','expectedSourceHeadRevisionId':initial['expectedHeadRevisionId']}
    first=client.post(BASE+'/books/'+demo['bookId']+'/selection-edits',json=edit,headers={'Idempotency-Key':'own-child-v1'})
    assert first.status_code==201,first.text
    child=first.json()
    edit.update(replacementText='LATEST_NO_SEND_SENTINEL',targetBranchId=child['branchId'],expectedTargetHeadRevisionId=child['revisionId'])
    latest=client.post(BASE+'/books/'+demo['bookId']+'/selection-edits',json=edit,headers={'Idempotency-Key':'own-child-v2'})
    assert latest.status_code==201,latest.text
    with SessionLocal() as db:
        revision=db.get(Revision,child['revisionId']);block=db.get(ContentBlock,revision.manifest[0]['blocks'][0])
        left=make_anchor(demo['bookId'],child['branchId'],revision.id,block,2)
        right=make_anchor(demo['bookId'],child['branchId'],revision.id,block,7)
        original=utf16_slice(block.text,2,7)
    old=deepcopy(initial);old.update(originAnchor=right,expectedHeadRevisionId=latest.json()['revisionId'])
    observe(client,reader,right)
    old['selection']={'purpose':'rewrite','startAnchor':left,'endAnchor':right,'sourcePolicy':'forkPinned'}
    created=client.post(BASE+'/books/'+demo['bookId']+'/generations',json=old,headers={'Idempotency-Key':'own-old-child-generation'})
    assert created.status_code==202,created.text
    process(created.json()['taskId'])
    assert snapshot(client,created.json())['state']=='succeeded'
    assert len(finite_selected_provider)==1 and finite_selected_provider[0]['data']['selection']['originalText']==original
    assert 'LATEST_NO_SEND_SENTINEL' not in json.dumps(finite_selected_provider[0]['data'],ensure_ascii=False)
    assert apply(client,created.json(),old).status_code==201

def test_other_account_cannot_read_or_accept_selected_candidates(client,reader,demo,profile,finite_selected_provider):
    receipt,body,_=request(client,reader,demo,profile);process(receipt['taskId'])
    registered=client.post(BASE+'/auth/register',json={'username':'other_selection_reader','password':'synthetic-other-password'})
    assert registered.status_code==201
    login=client.post(BASE+'/auth/login',json={'username':'other_selection_reader','password':'synthetic-other-password','clientType':'android','deviceName':'own IDOR test'})
    assert login.status_code==200;client.headers['Authorization']='Bearer '+login.json()['accessToken']
    assert client.get(BASE+'/generations/'+receipt['generationId']).status_code==404
    assert apply(client,receipt,body).status_code==404
    assert len(finite_selected_provider)==1

def test_incomplete_saved_fragment_cannot_apply_and_unknown_version_has_no_receipt(client,reader,demo,profile,finite_selected_provider):
    receipt,body,_=request(client,reader,demo,profile)
    with SessionLocal() as db:
        row=db.scalar(select(GenerationChapterVersion).where(GenerationChapterVersion.generation_id==receipt['generationId']))
        row.text='已收到的一半😀';row.content_hash=digest(row.text);row.durability='saved';row.completeness='partial';db.commit()
    assert snapshot(client,receipt)['chapters'][0]['text']=='已收到的一半😀'
    assert apply(client,receipt,body).status_code==409
    assert apply(client,receipt,body,key='unknown-selection-version',version=99).status_code==404
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(Idempotency).where(Idempotency.scope=='prose-selection-accept:'+receipt['generationId']))==0
    assert not finite_selected_provider
