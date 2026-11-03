"""Owned PostgreSQL cross-generation accepted context; zero real provider calls."""
from copy import deepcopy
import json
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import Task,Revision,ContentBlock,GenerationBatch,UsageRecord,Progress
from app.domain import make_anchor,canonical,utf16_len
from app.providers import MockAdapter
from app.worker import process
from app.prose.accepted_basis import collect_accepted_basis,prompt_basis,display_basis
from .test_prose_generations import isolated_prose,start,get,accept_body
from .test_prose_events_flow import install_five_chapters,event
from .helpers import BASE,observe


def first_accepted(client,reader,demo,profile,monkeypatch,count=5):
    bodies,seen=install_five_chapters(monkeypatch)
    receipt,payload=start(client,reader,demo,profile,count=count);process(receipt['taskId']);view=get(client,receipt)
    response=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=accept_body(view,payload,count=count),headers={'Idempotency-Key':'basis-first-accept'})
    assert response.status_code==200,response.text
    return receipt,response.json(),view,bodies,seen


def next_request(client,reader,accepted,profile,*,index=-1,cut=None,count=2,key='basis-next'):
    with SessionLocal() as db:
        revision=db.get(Revision,accepted['revisionId']);block=db.get(ContentBlock,revision.manifest[index]['blocks'][-1]);book=db.get(GenerationBatch,accepted['generationId']).book_id
        anchor=make_anchor(book,accepted['branchId'],revision.id,block,cut if cut is not None else utf16_len(block.text))
        prior=db.scalar(select(Progress).where(Progress.branch_id==accepted['branchId'],Progress.revision_id==revision.id))
        expected=prior.version if prior else 0
    observe(client,reader,anchor,expected=expected,sequence=2)
    payload={'protocol':'reader-prose-v1','originAnchor':anchor,'operation':'counterfactual','chapterCount':count,'chapterLength':'short','instruction':'继续核对蓝信来源。','profileId':profile['id'],'expectedHeadRevisionId':anchor['revisionId'],'target':{'mode':'new_child_branch','name':'第二批'}}
    response=client.post(BASE+'/books/'+book+'/generations',json=payload,headers={'Idempotency-Key':key})
    assert response.status_code==202,response.text
    return response.json(),payload


def test_actual_cross_generation_create_generation_review_and_replay_share_frozen_basis(client,reader,demo,profile,monkeypatch):
    first,accepted,view,bodies,_=first_accepted(client,reader,demo,profile,monkeypatch)
    receipt,payload=next_request(client,reader,accepted,profile)
    with SessionLocal() as db:
        basis=deepcopy(db.get(Task,receipt['taskId']).checkpoint['acceptedEventBasis'])
        assert len(basis['sources'])==5 and len(basis['events'])==7 and len(basis['subjects'])==1
        assert basis['state']['items']['蓝信']['location']=='木箱'
        assert basis['sources'][0]['generationId']==first['generationId']
        assert all(s['candidateVersion']==1 for s in basis['sources'])
    original=MockAdapter.generate;seen=[]
    def generate(self,p,prompt,data,limit,**kwargs):
        if not prompt.startswith(('prose-chapter-','prose-review-')):return original(self,p,prompt,data,limit,**kwargs)
        seen.append((prompt.splitlines()[0],deepcopy(data)))
        body='林渡把蓝信放到岸上。' if data['chapter']['index']==1 else '林渡从南岸走到东岸。'
        if prompt.startswith('prose-chapter-'):text=body
        else:
            lin=next(key for key,name in data['characterNames'].items() if name=='林渡')
            change={'item':'蓝信','fromHolderId':lin,'toHolderId':None,'location':'岸上'} if data['chapter']['index']==1 else {'from':'南岸','to':'东岸'}
            text=json.dumps({'completeChapter':True,'issues':[],'factDelta':[],'events':[event('item' if data['chapter']['index']==1 else 'location',body,change,lin)]},ensure_ascii=False)
        return {'text':text,'finishReason':'stop','usage':{'state':'known','inputTokens':20,'outputTokens':len(text)}}
    monkeypatch.setattr(MockAdapter,'generate',generate);process(receipt['taskId']);current=get(client,receipt)
    assert current['state']=='succeeded',current
    with SessionLocal() as db:assert current['acceptedEventBasis']==display_basis(basis,db)
    assert current['acceptedEventBasis']['sourceCount']==5
    assert not {'prefix','sources','calls','state','profileSnapshot'} & current['acceptedEventBasis'].keys()
    assert len(seen)==4
    for prompt,data in seen:
        assert data['acceptedEventBasis']==prompt_basis(basis)
        assert data['combinedEventState']['items']['蓝信']['location']==('木箱' if data['chapter']['index']==1 else '岸上')
        assert data['combinedEventState']['hints']==[]
        assert data['acceptedCandidateBindings']
    with SessionLocal() as db:
        assert db.get(Task,receipt['taskId']).checkpoint['acceptedEventBasis']==basis
        before=db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id==receipt['taskId']))
    repeated=client.post(BASE+'/books/'+payload['originAnchor']['bookId']+'/generations',json=payload,headers={'Idempotency-Key':'basis-next'})
    assert repeated.status_code==202 and repeated.json()==receipt
    monkeypatch.setattr(MockAdapter,'generate',lambda *a,**kw:pytest.fail('received restore cannot call a model'))
    from app.prose.worker import restore
    restore(receipt['taskId'])
    with SessionLocal() as db:
        assert db.get(Task,receipt['taskId']).checkpoint['acceptedEventBasis']==basis
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id==receipt['taskId']))==before


def test_anchor_in_first_accepted_chapter_excludes_later_accepted_and_unaccepted_events(client,reader,demo,profile,monkeypatch):
    first,accepted,view,bodies,_=first_accepted(client,reader,demo,profile,monkeypatch)
    receipt,_=next_request(client,reader,accepted,profile,index=-5,count=1)
    with SessionLocal() as db:
        basis=db.get(Task,receipt['taskId']).checkpoint['acceptedEventBasis']
        assert len(basis['sources'])==1 and len(basis['events'])==1
        assert basis['state']['locations']=={} and basis['state']['knowledge']=={}
        assert all('船夫' not in e['entry']['quote'] for e in basis['events'])


def test_partial_quote_collector_is_clipped_with_no_new_task_or_usage(client,reader,demo,profile,monkeypatch):
    _,accepted,_,_,_=first_accepted(client,reader,demo,profile,monkeypatch)
    with SessionLocal() as db:before=db.scalar(select(func.count()).select_from(UsageRecord))
    with SessionLocal() as db:
        revision=db.get(Revision,accepted['revisionId']);block=db.get(ContentBlock,revision.manifest[-5]['blocks'][-1]);batch=db.get(GenerationBatch,accepted['generationId']);anchor=make_anchor(batch.book_id,accepted['branchId'],revision.id,block,6)
        prior=db.scalar(select(Progress).where(Progress.branch_id==accepted['branchId'],Progress.revision_id==revision.id));version=prior.version;owner=batch.owner_id
    observe(client,reader,anchor,expected=version,sequence=2)
    with SessionLocal() as db:
        basis=collect_accepted_basis(db,owner,anchor,db.get(Revision,accepted['revisionId']))
        assert len(basis['sources'])==1 and basis['events']==[] and basis['state']['items']=={}
        assert basis['prefix'][-1]['limit']==6
        assert db.scalar(select(func.count()).select_from(UsageRecord))==before


def test_missing_old_metadata_stays_partial_with_body_and_does_not_rescan_other_book_tasks(client,reader,demo,profile,monkeypatch):
    first,accepted,view,bodies,_=first_accepted(client,reader,demo,profile,monkeypatch)
    with SessionLocal() as db:
        task=db.get(Task,first['taskId']);cp=deepcopy(task.checkpoint);cp['proseEventLedger']['1'].pop('1');task.checkpoint=cp;db.commit()
    receipt,_=next_request(client,reader,accepted,profile,count=1)
    with SessionLocal() as db:
        basis=db.get(Task,receipt['taskId']).checkpoint['acceptedEventBasis']
        assert basis['status']=='partial' and 'SOURCE_EVENTS_UNAVAILABLE' in basis['warnings'] and basis['events']==[]
        previous=get(client,first)
        assert basis['prefix'] and previous['chapters'][0]['text']==bodies[0]
        assert previous['eventStateStatus']=='unavailable'
        assert get(client,receipt)['acceptedEventBasis']==display_basis(basis,db)


def test_unreadable_old_event_metadata_never_hides_received_body(client,reader,demo,profile,monkeypatch):
    first,_,_,bodies,_=first_accepted(client,reader,demo,profile,monkeypatch)
    with SessionLocal() as db:
        task=db.get(Task,first['taskId']);checkpoint=deepcopy(task.checkpoint)
        checkpoint['proseEventLedger']['1']['1']['events'][0]['quote']='错误来源引用'
        task.checkpoint=checkpoint;db.commit()
    value=get(client,first)
    assert [chapter['text'] for chapter in value['chapters']]==bodies
    assert value['eventStateStatus']=='unavailable' and 'eventLedger' not in value['chapters'][0]
    assert any(hint['code']=='CHECK_UNAVAILABLE' for hint in value['chapters'][0]['hints'])


@pytest.mark.parametrize('fault',['missing','events','hash','scope'])
def test_missing_whole_basis_keeps_body_visible_but_never_dispatches_or_accepts_an_unverified_request(client,reader,demo,profile,monkeypatch,fault):
    first,_,view,bodies,_=first_accepted(client,reader,demo,profile,monkeypatch)
    with SessionLocal() as db:
        task=db.get(Task,first['taskId']);checkpoint=deepcopy(task.checkpoint)
        if fault=='missing':checkpoint.pop('acceptedEventBasis')
        elif fault=='events':del checkpoint['acceptedEventBasis']['events']
        elif fault=='hash':checkpoint['acceptedEventBasis']['hash']='0'*64
        else:checkpoint['acceptedEventBasis']['scope']['branchId']='0'*32
        task.checkpoint=checkpoint;db.commit()
        before=db.scalar(select(func.count()).select_from(UsageRecord))
    value=get(client,first)
    assert [chapter['text'] for chapter in value['chapters']]==bodies and value['acceptedBasisUnavailable']
    assert 'acceptedEventBasis' not in value
    response=client.post(BASE+'/generations/'+first['generationId']+'/rewrite',json={'candidateVersion':1,'expectedAcceptedPrefix':5,'instruction':'不得重新调用'},headers={'Idempotency-Key':'own-basis-missing-rewrite'})
    assert response.status_code==422 and response.json()['code']=='ACCEPTED_BASIS_INVALID',response.text
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(UsageRecord))==before
