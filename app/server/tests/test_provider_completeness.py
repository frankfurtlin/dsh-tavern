"""Incomplete provider replies cannot establish facts or authorize stored content."""
from unittest.mock import patch
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import AnalysisChunk,KnowledgeSnapshot,UsageRecord
from app.providers import MockAdapter
from .helpers import BASE,read_chapter,observe,operation,submit,accept
from .test_reader import analyzed_session


@pytest.mark.parametrize('shape', ['unquoted-expression', 'json-wrapper', 'internal-field-prose'])
def test_protocol_shaped_role_reply_is_not_validated_or_saved_and_turn_can_recover(client, reader, demo, profile, shape):
    import json
    from app.models import RoleSession
    created, _, _ = analyzed_session(client, reader, demo, profile)
    session = created.json()
    prose = ('我想着眼前的绳子和码头，不把没有见过的事说成事实。'
             '心里有担忧，就把能够确定的事情一件件理清。'
             '木箱上那句“{绳索}”是字样，花括号也可以出现在叙述里。'
             '我会按此刻已有的判断回答你的问题，不替谁编造过去，也不把你当成故事里的同伴。')
    bad = ('{"character": data.character, "content": ' + json.dumps(prose, ensure_ascii=False) + '}'
           if shape == 'unquoted-expression' else json.dumps({'character': '林渡', 'content': prose}, ensure_ascii=False)
           if shape == 'json-wrapper' else '我正在读取 data.characterFacts。' + prose)
    original = MockAdapter.generate
    validations = []
    def malformed(self, p, prompt, data, limit):
        result = original(self, p, prompt, data, limit)
        if prompt.startswith('role_chat-'):
            result['text'] = bad
        if prompt.startswith('validation-'):
            validations.append(data)
        return result
    payload = {'operation': 'role_chat', 'profileId': profile['id'], 'sessionId': session['id'],
               'message': '此刻你在想什么？', 'turnId': 'malformed-protocol'}
    with patch.object(MockAdapter, 'generate', malformed):
        task, _, _ = submit(client, profile, payload)
    assert task['resultOutcome'] == 'invalid_format' and task['result']['replySaved'] is False, task
    assert validations == []
    assert client.get(BASE + '/role-sessions/' + session['id'] + '/messages').json()['items'] == []
    with SessionLocal() as db:
        assert db.get(RoleSession, session['id']).pending_task_id is None
    diagnostics = client.get(BASE + '/tasks/' + task['id'] + '/diagnostics').json()
    assert len(diagnostics['usage']) == 1 and diagnostics['usage'][0]['operation'] == 'generation'
    def legitimate(self, p, prompt, data, limit):
        result = original(self, p, prompt, data, limit)
        if prompt.startswith('role_chat-'):
            result['text'] = prose
        return result
    with patch.object(MockAdapter, 'generate', legitimate):
        recovered, _, _ = submit(client, profile, {**payload, 'turnId': 'plain-prose'})
    assert recovered['resultOutcome'] == 'complete' and recovered['result']['replySaved'] is True, recovered
    messages = client.get(BASE + '/role-sessions/' + session['id'] + '/messages').json()['items']
    assert len(messages) == 2 and messages[-1]['text'] == prose


@pytest.mark.parametrize('directive', ['你也别空手站着，先看看手上有没有能搭一把的东西。', '你来帮我把船推下水。', '你先握住绳子。', '你在旁边看着也行，别伸手。', '现在，先把手头这根绳收好。'])
def test_outside_reader_direct_physical_instruction_is_not_saved(client, reader, demo, profile, directive):
    from app.models import RoleSession
    from app.worker import outside_reader_physical_directive
    created, _, _ = analyzed_session(client, reader, demo, profile)
    session = created.json()
    with SessionLocal() as db:
        fixed = db.get(RoleSession, session['id'])
        fixed_snapshot_id, fixed_anchor = fixed.snapshot_id, fixed.pinned_anchor
    suffix = '我想着眼前能确定的事情，不凭空编造没有见过的经历。心里的担忧还在，我愿把已有的判断一件件说明，至于未曾向读者揭示的身份，不在此处作肯定或否定。此刻已有的事实就是这些，我愿意把这点判断说清楚。'
    bad = directive + suffix
    assert 80 <= len(bad) <= 300
    assert outside_reader_physical_directive(bad, 'reader_out_of_story')
    assert not outside_reader_physical_directive(bad, 'named_story_character')
    assert not outside_reader_physical_directive('我曾对阿岑说：“' + directive + '”' + suffix, 'reader_out_of_story', ('阿岑',))
    assert outside_reader_physical_directive('“' + directive + '”' + suffix, 'reader_out_of_story', ('阿岑',))
    original = MockAdapter.generate
    validations = []
    def directed(self, p, prompt, data, limit):
        result = original(self, p, prompt, data, limit)
        if prompt.startswith('role_chat-'):
            result['text'] = bad
        if prompt.startswith('validation-'):
            validations.append(data)
        return result
    payload = {'operation': 'role_chat', 'profileId': profile['id'], 'sessionId': session['id'],
               'message': '此刻你在想什么？', 'turnId': 'physical-reader-instruction'}
    with patch.object(MockAdapter, 'generate', directed):
        task, _, _ = submit(client, profile, payload)
    assert task['resultOutcome'] == 'blocked' and task['result']['replySaved'] is False, task
    assert validations == []
    assert client.get(BASE + '/role-sessions/' + session['id'] + '/messages').json()['items'] == []
    with SessionLocal() as db:
        assert db.get(RoleSession, session['id']).pending_task_id is None
        assert db.get(RoleSession, session['id']).snapshot_id == fixed_snapshot_id
        assert db.get(RoleSession, session['id']).pinned_anchor == fixed_anchor
    diagnostics = client.get(BASE + '/tasks/' + task['id'] + '/diagnostics').json()
    assert len(diagnostics['usage']) == 1 and diagnostics['usage'][0]['operation'] == 'generation'
    bad = '“' + directive + '”' + suffix
    with patch.object(MockAdapter, 'generate', directed):
        quoted, _, _ = submit(client, profile, {**payload, 'turnId': 'quoted-physical-instruction'})
    assert quoted['resultOutcome'] == 'blocked' and quoted['result']['replySaved'] is False, quoted
    assert validations == []
    assert client.get(BASE + '/role-sessions/' + session['id'] + '/messages').json()['items'] == []
    with SessionLocal() as db:
        fixed = db.get(RoleSession, session['id'])
        assert fixed.snapshot_id == fixed_snapshot_id and fixed.pinned_anchor == fixed_anchor
    good = '你可以看看本章码头的文字，想一想这些描述。你若拿不准，可以继续向我提问。' + suffix
    assert not outside_reader_physical_directive(good, 'reader_out_of_story')
    def reading(self, p, prompt, data, limit):
        result = original(self, p, prompt, data, limit)
        if prompt.startswith('role_chat-'):
            result['text'] = good
        return result
    with patch.object(MockAdapter, 'generate', reading):
        restored, _, _ = submit(client, profile, {**payload, 'turnId': 'reading-dialogue'})
    assert restored['resultOutcome'] == 'complete' and restored['result']['replySaved'] is True, restored

def test_cached_prefix_creates_exact_new_anchor_snapshot_without_provider_call(client,reader,demo,profile):
    from app.domain import make_anchor
    from app.models import ContentBlock,KnowledgeSnapshot
    chapter=read_chapter(client,demo)
    first=chapter['blocks'][1]['endAnchor']
    observe(client,reader,first)
    analyzed,_,_=operation(client,profile,first,'analysis')
    assert analyzed['state']=='succeeded'
    with SessionLocal() as db:
        block=db.get(ContentBlock,chapter['blocks'][2]['id'])
        second=make_anchor(demo['bookId'],demo['branchId'],demo['revisionId'],block,0,0)
        before=db.scalar(select(func.count()).select_from(UsageRecord))
    observe(client,reader,second,expected=1,sequence=2)
    plan=client.post(BASE+'/operation-plans',json={'operation':'analysis','profileId':profile['id'],'anchor':second,'expectedHeadRevisionId':demo['revisionId']})
    assert plan.status_code==201 and plan.json()['ready'] and plan.json()['snapshotId'],plan.text
    with SessionLocal() as db:
        snapshot=db.get(KnowledgeSnapshot,plan.json()['snapshotId'])
        assert snapshot.boundary==second and snapshot.completeness=='complete'
        assert db.scalar(select(func.count()).select_from(UsageRecord))==before
    import json
    characters=client.get(BASE+f"/books/{demo['bookId']}/characters",params={'branchId':demo['branchId'],'revisionId':demo['revisionId'],'anchor':json.dumps(second)}).json()
    lin=next(c for c in characters['items'] if c['displayName']=='林渡')
    session=client.post(BASE+'/role-sessions',json={'bookId':demo['bookId'],'characterId':lin['id'],'snapshotId':characters['snapshotId'],'anchor':second,'identityMode':'reader_out_of_story'})
    assert session.status_code==201,session.text

def test_incomplete_valid_analysis_never_establishes_snapshot(client,reader,demo,profile):
    anchor=read_chapter(client,demo)['blocks'][1]['endAnchor']
    observe(client,reader,anchor)
    original=MockAdapter.generate
    def truncated(self,p,prompt,data,limit):
        result=original(self,p,prompt,data,limit)
        if prompt.startswith('analysis-'):result['finishReason']='length'
        return result
    with patch.object(MockAdapter,'generate',truncated):
        task,_,_=operation(client,profile,anchor,'analysis')
    assert task['state']=='failed' and task['errorCode']=='ANALYSIS_OUTPUT_INCOMPLETE',task
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisChunk))==0
        assert db.scalar(select(func.count()).select_from(KnowledgeSnapshot))==0
        assert db.scalar(select(func.count()).select_from(UsageRecord))==1

@pytest.mark.parametrize('text,finish',[('','stop'),('连接正常','length')])
def test_connection_requires_nonempty_complete_reply(client,reader,profile,text,finish):
    with patch.object(MockAdapter,'generate',return_value={'text':text,'finishReason':finish,'usage':{'inputTokens':1,'outputTokens':1,'state':'estimated'},'latencyMs':1}):
        task,_,_=submit(client,profile,{'operation':'connection_test','profileId':profile['id']})
    assert task['state']=='failed' and task['errorCode']=='PROVIDER_CONNECTION_INCOMPLETE',task
    assert not task['result'].get('connected')
    diagnostics=client.get(BASE+'/tasks/'+task['id']+'/diagnostics').json()
    assert len(diagnostics['usage'])==1

@pytest.mark.parametrize('role',[False,True])
def test_truncated_verdict_is_a_novel_note_and_still_rejects_role_reply(client,reader,demo,profile,role):
    original=MockAdapter.generate
    if role:
        created,_,_=analyzed_session(client,reader,demo,profile)
        session=created.json()
        payload={'operation':'role_chat','profileId':profile['id'],'sessionId':session['id'],'message':'你此刻看到了什么？','turnId':'incomplete-safe-verdict'}
    else:
        anchor=read_chapter(client,demo)['blocks'][1]['endAnchor']
        observe(client,reader,anchor)
    def truncated(self,p,prompt,data,limit):
        result=original(self,p,prompt,data,limit)
        if prompt.startswith('validation-'):result['finishReason']='length'
        return result
    with patch.object(MockAdapter,'generate',truncated):
        task,_,_=submit(client,profile,payload) if role else operation(client,profile,anchor)
    if role:
        assert task['resultOutcome']=='blocked',task
        assert task['result']['replySaved'] is False
        assert client.get(BASE+'/role-sessions/'+session['id']+'/messages').json()['items']==[]
    else:
        candidate=client.get(BASE+'/candidates/'+task['result']['candidateId']).json()
        assert task['resultOutcome']=='complete',task
        assert candidate['validationStatus']=='unknown' and candidate['text'] and candidate['canAccept']
        assert candidate['reviewWarnings'] == [{'code': 'UNCERTAIN', 'chapterIndex': 1}]
        assert accept(client,task).status_code==200

def test_continuation_diagnostics_identify_loaded_prompt(client,reader,demo,profile):
    anchor=read_chapter(client,demo,3)['blocks'][-1]['endAnchor']
    observe(client,reader,anchor)
    task,_,_=operation(client,profile,anchor,'continue_at_tail')
    diagnostics=client.get(BASE+'/tasks/'+task['id']+'/diagnostics').json()
    assert diagnostics['promptVersion']=='continuation-v6'

@pytest.mark.parametrize('fact_count,exceeds_budget',[(12,False),(120,True)])
def test_dense_fact_context_keeps_every_constraint_and_exact_position(client,reader,demo,profile,fact_count,exceeds_budget):
    from copy import deepcopy
    from app.models import Task
    from app.worker import generation_context
    from app.domain import canonical
    anchor=read_chapter(client,demo)['blocks'][1]['endAnchor']
    observe(client,reader,anchor)
    analyzed,_,_=operation(client,profile,anchor,'analysis')
    with SessionLocal() as db:
        source=db.get(KnowledgeSnapshot,analyzed['result']['snapshotId'])
        seed=deepcopy(source.data['facts'][0])
        facts=[]
        for index in range(fact_count):
            fact=deepcopy(seed);fact['id']='constraint-'+str(index)
            fact['readerRevealAnchor']=deepcopy(anchor)
            fact['occurredOrder']='explicit-scene-'+str(index)
            fact['temporalRelation']='past' if index==0 else 'at_scope'
            fact['knownBy']=[fact['subjectId']]
            fact['knowledge']=[{'characterId':fact['subjectId'],'evidenceIds':fact['evidenceIds'],'learnedAt':deepcopy(anchor),'learnedOrder':'explicit-learning-'+str(index),'validity':'at_scope'}]
            facts.append(fact)
        source.data={**source.data,'facts':facts}
        task=Task(owner_id=source.owner_id,task_type='generation',payload={'anchor':anchor,'profileId':profile['id'],'operation':'continue_from_anchor','instruction':''})
        if exceeds_budget:
            from app.errors import Problem
            with pytest.raises(Problem) as refused:
                generation_context(db,task,source)
            assert refused.value.code=='CONTEXT_BUDGET_EXCEEDED'
            return
        context=generation_context(db,task,source)
        assert [f['id'] for f in context['confirmedFacts']]==[f['id'] for f in facts]
        from app.worker import PROMPTS
        allowed_bytes=12000-(PROMPTS/'continuation.txt').stat().st_size-256
        from app.context_wire import wire_json
        assert len(wire_json(context).encode())<=allowed_bytes
        expanded=deepcopy(context)
        for fact in expanded['confirmedFacts']:
            fact['readerRevealAnchor']=deepcopy(anchor)
            fact['knowledgeAt'][0]['learnedAt']=deepcopy(anchor)
        assert len(canonical(expanded).encode())>allowed_bytes
        for model_fact,original in zip(context['confirmedFacts'],facts):
            assert model_fact['predicate']==original['predicate'] and model_fact['value']==original['value']
            assert model_fact['subjectId']==original['subjectId']
            assert model_fact['evidenceIds']==original['evidenceIds']
            assert model_fact['temporalRelation']==original['temporalRelation']
            assert model_fact['occurredOrder']==original['occurredOrder']
            assert context['sourcePositions'][model_fact['readerRevealAnchor']]=={key:anchor[key] for key in ['chapterId','blockId','start','end']}
            assert model_fact['knowledgeAt'][0]['learnedAt']==model_fact['readerRevealAnchor']
            assert model_fact['knowledgeAt'][0]['learnedOrder']==original['knowledge'][0]['learnedOrder']
            assert model_fact['knowledgeAt'][0]['validity']=='at_scope'
            assert model_fact['knownBy']==original['knownBy']
            assert model_fact['knowledgeAt'][0]['characterId']==original['knowledge'][0]['characterId']
            assert model_fact['knowledgeAt'][0]['evidenceIds']==original['knowledge'][0]['evidenceIds']
