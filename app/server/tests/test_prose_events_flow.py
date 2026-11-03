"""Actual owned PostgreSQL event flow; no real model or semantic quality claim."""
from copy import deepcopy
import json
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import Task, UsageRecord, KnowledgeSnapshot
from app.prose.events import validate, fold
from app.prose.worker import review_hints, review_complete
from app.prose.facts import from_review
from app.providers import MockAdapter
from app.domain import digest
from app.storage import private_path
from app.prose.worker import restore
from app.worker import process
from .test_prose_generations import isolated_prose, start, get, accept_body
from .helpers import BASE


def event(kind, quote, change, subject=None, certainty='narrated'):
    return {'kind':kind,'subjectId':subject,'certainty':certainty,'confidence':900,'statement':quote,'quote':quote,'change':change}


def install_five_chapters(monkeypatch):
    original=MockAdapter.generate; seen=[]
    bodies=['林渡把蓝信收在木箱里。','林渡从渡口走到南岸。','林渡决定寻找船夫。','前一夜。林渡曾把蓝信放在渡口。','船夫归来。林渡知道船夫已安全归来。']
    def generate(self,p,prompt,data,limit,**kwargs):
        if not prompt.startswith(('prose-chapter-','prose-review-')):
            return original(self,p,prompt,data,limit,**kwargs)
        index=data['chapter']['index']; seen.append((prompt.splitlines()[0],deepcopy(data)))
        if prompt.startswith('prose-chapter-'):text=bodies[index-1]
        else:
            lin=next(key for key,name in data['characterNames'].items() if name=='林渡')
            proposals={
                1:[event('item',bodies[0],{'item':'蓝信','fromHolderId':None,'toHolderId':lin,'location':'木箱'},lin)],
                2:[event('location',bodies[1],{'from':'渡口','to':'南岸'},lin)],
                3:[event('motivation',bodies[2],{'goal':'寻找船夫','state':'adopted'},lin)],
                4:[event('time','前一夜。',{'label':'前夜','relation':'before','relativeTo':'c1:e1'}),event('item','林渡曾把蓝信放在渡口。',{'item':'蓝信','fromHolderId':lin,'toHolderId':lin,'location':'渡口'},lin)],
                5:[event('unresolved','船夫归来。',{'threadKey':'失踪船夫','state':'resolved','detail':'已归来'}),event('knowledge','林渡知道船夫已安全归来。',{'fact':'船夫安全归来','learnedByIds':[lin]},lin)],
            }[index]
            text=json.dumps({'completeChapter':True,'issues':[],'factDelta':[],'events':proposals},ensure_ascii=False)
        return {'text':text,'finishReason':'stop','latencyMs':1,'usage':{'state':'known','inputTokens':20,'outputTokens':len(text)}}
    monkeypatch.setattr(MockAdapter,'generate',generate)
    return bodies,seen


def test_five_chapters_events_reach_each_generation_and_full_review_and_rewrite(client,reader,demo,profile,monkeypatch):
    bodies,seen=install_five_chapters(monkeypatch)
    receipt,payload=start(client,reader,demo,profile,count=5);process(receipt['taskId']);first=get(client,receipt)
    assert first['state']=='succeeded' and [row['text'] for row in first['chapters']]==bodies
    ledgers=[row['eventLedger'] for row in first['chapters']]
    assert all(validate(row,body)['status']=='model-proposed' for row,body in zip(ledgers,bodies))
    for prompt,data in seen:
        count=data['chapter']['index']-1
        assert data['generatedEventLedger']==ledgers[:count]
        assert data['generatedEventState']==fold(ledgers[:count])
        if prompt.startswith('prose-review-'):
            assert [row['text'] for row in data['previousChapters']]==bodies[:count]
    state=first['eventState'];lin=ledgers[0]['events'][0]['subjectId']
    assert state['items']['蓝信']['location']=='木箱' and state['historicalEvents']==['c4:e2']
    assert state['knowledge'][lin][0]['fact']=='船夫安全归来'
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id==receipt['taskId'],UsageRecord.operation.like('%:prose:%')))==10
    accepted=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=accept_body(first,payload,count=2),headers={'Idempotency-Key':'events-prefix'})
    assert accepted.status_code==200,accepted.text
    response=client.post(BASE+'/generations/'+receipt['generationId']+'/rewrite',json={'candidateVersion':1,'expectedAcceptedPrefix':2,'instruction':'保留蓝信来源和人物位置。'},headers={'Idempotency-Key':'events-rewrite'})
    assert response.status_code==202,response.text
    process(receipt['taskId']);current=get(client,receipt)
    assert current['state']=='succeeded' and current['chapters'][0]['eventLedger']==ledgers[0]
    old=client.get(BASE+'/generations/'+receipt['generationId']+'?candidateVersion=1')
    assert old.status_code==200 and old.json()['chapters'][0]['eventLedger']==ledgers[0]
    with SessionLocal() as db:
        task=db.get(Task,receipt['taskId'])
        assert task.checkpoint['proseEventLedger']['2']['2']==ledgers[1]


def test_invalid_event_review_is_soft_and_received_save_replay_never_calls_again(client,reader,demo,profile,monkeypatch):
    original=MockAdapter.generate
    def generate(self,p,prompt,data,limit,**kwargs):
        result=original(self,p,prompt,data,limit,**kwargs)
        if prompt.startswith('prose-review-'):
            value=json.loads(result['text']);value['events']=[event('item','不存在的引文',{'item':'蓝信','fromHolderId':None,'toHolderId':'unknown','location':None})]
            result={**result,'text':json.dumps(value,ensure_ascii=False)}
        return result
    monkeypatch.setattr(MockAdapter,'generate',generate)
    receipt,_=start(client,reader,demo,profile);process(receipt['taskId']);first=get(client,receipt)
    assert first['chapters'][0]['eventLedger']['status']=='unavailable' and first['chapters'][0]['canAccept']
    monkeypatch.setattr(MockAdapter,'generate',lambda *a,**kw:pytest.fail('Received restore cannot repeat model'))
    from app.prose.worker import restore
    restore(receipt['taskId']);restored=get(client,receipt)
    assert restored['chapters'][0]['text']==first['chapters'][0]['text']
    assert restored['chapters'][0]['eventLedger']==first['chapters'][0]['eventLedger']


@pytest.mark.parametrize('raw',['{"completeChapter":true,"completeChapter":false,"factDelta":[]}', '{"extension":'+'['*10000+'0'+']'*10000+',"completeChapter":true}'])
def test_all_review_consumers_reject_duplicate_and_deep_json_without_recursion(raw):
    result={'text':raw,'finishReason':'stop'}
    assert review_hints(result)==[{'code':'CHECK_UNAVAILABLE'}] and review_complete(result) is None
    assert from_review(result,'正文。')['status']=='unavailable'


@pytest.mark.parametrize('has_original_review',[True,False])
def test_recovery_original_review_is_bound_to_selected_received_attempt(client,reader,demo,profile,monkeypatch,has_original_review):
 receipt,_=start(client,reader,demo,profile)
 original_generate=MockAdapter.generate
 def healthy(self,p,prompt,data,limit,**kw):
  if prompt.startswith('prose-chapter-'):return {'text':'🙂原始已知正文。','finishReason':'stop','usage':{'state':'known','inputTokens':3,'outputTokens':6}}
  return original_generate(self,p,prompt,data,limit,**kw)
 monkeypatch.setattr(MockAdapter,'generate',healthy);process(receipt['taskId']);before=get(client,receipt)
 with SessionLocal() as db:
  task=db.get(Task,receipt['taskId']);snapshot=db.get(KnowledgeSnapshot,task.checkpoint['snapshotId']);actor=snapshot.data['characters'][0]['id'];stage='generation:prose:v1:c1'
  calls=deepcopy(task.checkpoint['calls']);repair_id='OWN_KNOWN_REPAIR';review_id='OWN_KNOWN_REPAIR_REVIEW'
  original_review_id=calls['validation:prose:v1:c1']['callId']
  calls[stage+':repair']={'callId':repair_id};calls['validation:prose:v1:c1:repair']={'callId':review_id}
  if not has_original_review:calls.pop('validation:prose:v1:c1')
  task.checkpoint={**task.checkpoint,'calls':calls};db.commit();owner=task.owner_id
 def write_receipt(identifier,result):
  body={'taskId':receipt['taskId'],'callId':identifier,'result':result,'hash':digest(json.dumps(result,ensure_ascii=False,sort_keys=True))}
  private_path(owner,'journal-'+receipt['taskId']+'-'+identifier+'.json').write_text(json.dumps(body,ensure_ascii=False),'utf-8')
 good_review={'completeChapter':True,'issues':[],'factDelta':[],'events':[event('location','🙂原始已知正文。',{'from':None,'to':'南岸'},actor)]}
 bad_review={'completeChapter':False,'issues':['CONTINUITY'],'factDelta':[],'events':[event('location','并非原正文。',{'from':None,'to':'北岸'},actor)]}
 write_receipt(original_review_id,{'text':json.dumps(good_review,ensure_ascii=False),'finishReason':'stop','usage':{'state':'known','inputTokens':3,'outputTokens':3}})
 write_receipt(repair_id,{'text':'{"tool":"no-readable-body"}','finishReason':'stop','usage':{'state':'known','inputTokens':3,'outputTokens':3}})
 write_receipt(review_id,{'text':json.dumps(bad_review,ensure_ascii=False),'finishReason':'stop','usage':{'state':'known','inputTokens':3,'outputTokens':3}})
 monkeypatch.setattr(MockAdapter,'generate',lambda *a,**kw:pytest.fail('Recovery must never request a model'))
 # Known synthetic receipts, actual restore/PG/journal. Their usage collaborators
 # are not paid calls and this test does not synthesize real provider execution.
 restore(receipt['taskId']);after=get(client,receipt);chapter=after['chapters'][0]
 assert chapter['text']==before['chapters'][0]['text'] and chapter['rawPresentation']['selected']=='original'
 assert chapter['eventLedger']['status']==('model-proposed' if has_original_review else 'unavailable')
 if has_original_review:assert chapter['eventLedger']['events'][0]['change']['to']=='南岸'
 else:assert chapter['eventLedger']['events']==[] and any(hint['code']=='CHECK_UNAVAILABLE' for hint in chapter['hints'])
 assert not any(hint['code']=='CONTINUITY' for hint in chapter['hints'])
