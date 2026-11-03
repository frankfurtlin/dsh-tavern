import json
from app.prose.facts import from_review,validate_delta,validate_ledger
from app.portable.prose_history import safe_checkpoint,validate_checkpoint
from app.portable.errors import ArchiveError
from types import SimpleNamespace
import pytest
from .test_prose_generations import isolated_prose

def reviewed(facts):return {'text':json.dumps({'completeChapter':True,'issues':[],'factDelta':facts},ensure_ascii=False),'finishReason':'stop'}

def test_fact_quotes_bind_exact_source_and_utf16_without_creating_semantic_truth():
    text='灯亮了。🙂林渡把信交给梅舟。\n他决定回到南岸。'
    value=from_review(reviewed([{'kind':'item','subjectId':'lin','statement':'林渡将信交给梅舟','quote':'🙂林渡把信交给梅舟。'}]),text,{'lin'})
    assert value['status']=='quoted-candidates' and value['entries'][0]['start']==4
    assert value['entries'][0]['end']==15
    assert validate_delta(value,text)==value
    with pytest.raises(ValueError):validate_delta(value,text+'新改动')

@pytest.mark.parametrize('proposal',[{'kind':'item','subjectId':'lin','statement':'物品','quote':'没有给出的正文'},
    {'kind':'item','subjectId':'future-character','statement':'物品','quote':'原文'},
    {'kind':'item','subjectId':'lin','statement':'物品','quote':'重复'}])
def test_unsupported_or_ambiguous_fact_does_not_synthesize_knowledge(proposal):
    value=from_review(reviewed([proposal]),'原文与重复。重复。',{'lin'})
    assert value['status']=='unavailable' and value['entries']==[]

def test_fact_ledger_portable_history_retains_quotes_but_no_execution_secrets():
    delta=from_review(reviewed([{'kind':'location','subjectId':None,'statement':'灯在南岸','quote':'南岸灯亮。'}]),'南岸灯亮。')
    task=SimpleNamespace(task_type='story_flow',checkpoint={'proseFactLedger':{'1':{'1':delta}},'executionProfile':{'key_cipher':'SECRET_NOT_PORTABLE'},'calls':{}},payload={},result={})
    value=safe_checkpoint(task)
    validate_checkpoint(value)
    assert value['proseFactLedger']['1']['1']==delta
    assert 'SECRET_NOT_PORTABLE' not in json.dumps(value)
    with pytest.raises(ArchiveError):validate_checkpoint({**value,'proseFactLedger':{'1':{'1':{**delta,'sourceHash':'invalid'}}}})
    with pytest.raises(ValueError):validate_ledger({'1':{'0':delta}})

def test_saved_fact_ledger_flows_to_later_chapters_and_survives_version_rewrite(client,reader,demo,profile,monkeypatch):
    from copy import deepcopy
    from app.providers import MockAdapter
    from app.worker import process
    from app.db import SessionLocal
    from app.models import Task,UsageRecord
    from sqlalchemy import select,func
    from .test_prose_generations import start,get,accept_body,isolated_prose
    from .helpers import BASE
    original=MockAdapter.generate;seen=[]
    quote='林渡把蓝色信封留在南岸的木桌上。'
    def generate(self,p,prompt,data,limit,**kwargs):
        result=original(self,p,prompt,data,limit,**kwargs)
        if prompt.startswith('prose-chapter-'):
            seen.append(deepcopy(data))
            if data['chapter']['index']==1:result={**result,'text':result['text']+'\n\n'+quote}
        elif prompt.startswith('prose-review-'):
            value=json.loads(result['text']);value['factDelta']=[{'kind':'item','subjectId':None,'statement':'蓝色信封留在南岸木桌','quote':quote}] if quote in data['text'] else []
            result={**result,'text':json.dumps(value,ensure_ascii=False)}
        return result
    monkeypatch.setattr(MockAdapter,'generate',generate)
    receipt,payload=start(client,reader,demo,profile,count=3);process(receipt['taskId']);first=get(client,receipt)
    assert first['state']=='succeeded' and first['completedChapters']==3
    delta=first['chapters'][0]['factDelta'];validate_delta(delta,first['chapters'][0]['text'])
    assert delta['status']=='quoted-candidates'
    assert seen[1]['generatedFactLedger'][0]['delta']==delta
    assert seen[2]['generatedFactLedger'][0]['delta']==delta
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id==receipt['taskId'],UsageRecord.operation.like('%:prose:%')))==6
        assert safe_checkpoint(db.get(Task,receipt['taskId']))['proseFactLedger']['1']['1']==delta
    accepted=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=accept_body(first,payload,count=1),headers={'Idempotency-Key':'fact-prefix'})
    assert accepted.status_code==200,accepted.text
    result=client.post(BASE+'/generations/'+receipt['generationId']+'/rewrite',json={'candidateVersion':1,'expectedAcceptedPrefix':1,'instruction':'保持信封位置。'},headers={'Idempotency-Key':'fact-rewrite'})
    assert result.status_code==202,result.text
    process(receipt['taskId']);current=get(client,receipt)
    assert current['state']=='succeeded' and current['chapters'][0]['factDelta']==delta
    assert seen[-2]['generatedFactLedger'][0]['delta']==delta

def test_queued_prose_executes_frozen_model_while_live_revocation_remains_effective(client,reader,demo,profile,monkeypatch,tmp_path):
    from app.providers import MockAdapter,encrypt_key,decrypt_key
    from app.worker import process
    from app.db import SessionLocal
    from app.models import ModelProfile
    from .test_prose_generations import start,get
    seen=[];original=MockAdapter.generate
    def generate(self,p,prompt,data,limit,**kwargs):
        seen.append((p.model_id,p.context_window,decrypt_key(p.key_cipher)))
        return original(self,p,prompt,data,limit,**kwargs)
    monkeypatch.setattr(MockAdapter,'generate',generate)
    receipt,_=start(client,reader,demo,profile)
    from app import worker
    # Queue-time templates must survive a deployment replacing live templates.
    changed=tmp_path/'changed-prompts';changed.mkdir();(changed/'analysis.txt').write_text('not-the-queued-analysis\nWRONG',encoding='utf-8')
    monkeypatch.setattr(worker,'PROMPTS',changed)
    with SessionLocal() as db:
        live=db.get(ModelProfile,profile['id']);live.model_id='edited-after-queue';live.context_window=65536;live.key_cipher=encrypt_key('SECOND_SYNTHETIC_KEY');live.version+=1;db.commit()
    process(receipt['taskId']);assert get(client,receipt)['state']=='succeeded'
    assert seen and all(value==('fixture-v1',32768,'TEST-KEY-NOT-REAL') for value in seen)
    second,_=start(client,reader,demo,profile,key='frozen-revoked');before=len(seen)
    with SessionLocal() as db:
        live=db.get(ModelProfile,profile['id']);live.status='disabled';db.commit()
    process(second['taskId']);assert get(client,second)['state']!='succeeded';assert len(seen)==before

def test_frozen_prompt_integrity_fails_before_provider_request():
    from app.execution_prompts import freeze_prompts,frozen_prompt
    from app.errors import Problem
    task=SimpleNamespace(checkpoint={'executionPrompts':freeze_prompts()})
    assert frozen_prompt(task,'prose_chapter').startswith('prose-chapter-v3')
    task.checkpoint['executionPrompts']['prose_chapter']['text']+='tampered'
    with pytest.raises(Problem) as error:frozen_prompt(task,'prose_chapter')
    assert error.value.code=='EXECUTION_CONFIG_INVALID'
