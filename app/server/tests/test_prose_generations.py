"""Owner-only real PostgreSQL prose protocol; all providers are synthetic."""
import json
import threading
from copy import deepcopy
from sqlalchemy import select, func
import pytest
from app.db import SessionLocal
from app.config import settings
from app.models import (Task, GenerationBatch, GenerationChapterVersion, UsageRecord,
    TaskEvent, Branch, Revision, Progress, ModelProfile, Consent)
from app.providers import MockAdapter, ChatCompletionsAdapter
from app.worker import process
from app.errors import Problem
from app.storage import private_path
from .helpers import BASE, read_chapter, observe


@pytest.fixture(autouse=True)
def isolated_prose(tmp_path, monkeypatch):
    assert settings.ai_mode == 'mock'
    assert SessionLocal.kw['bind'].url.database.endswith('_test')
    monkeypatch.setattr(settings, 'storage_path', tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('No external provider is allowed')
    monkeypatch.setattr(ChatCompletionsAdapter, 'generate', forbidden)
    monkeypatch.setattr(ChatCompletionsAdapter, 'generate_stream', forbidden)
    def fast_stream(self, p, prompt, data, limit, *, on_delta, cancel_check, **kwargs):
        result = self.generate(p, prompt, data, limit)
        for offset in range(0, len(result['text']), 256):
            cancel_check(); on_delta(result['text'][offset:offset+256])
        return result
    monkeypatch.setattr(MockAdapter, 'generate_stream', fast_stream)


def start(client, reader, demo, profile, *, count=1, key='prose-create', length='short'):
    anchor = read_chapter(client, demo)['blocks'][1]['endAnchor']
    observe(client, reader, anchor)
    payload = {'protocol': 'reader-prose-v1', 'originAnchor': anchor,
        'operation': 'counterfactual', 'chapterCount': count, 'chapterLength': length,
        'instruction': '让林渡核对眼前证言。', 'profileId': profile['id'],
        'expectedHeadRevisionId': anchor['revisionId'],
        'target': {'mode': 'new_child_branch', 'name': '证言线'}}
    response = client.post(BASE + '/books/' + demo['bookId'] + '/generations', json=payload,
        headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    return response.json(), payload


def get(client, receipt):
    response = client.get(BASE + '/generations/' + receipt['generationId'])
    assert response.status_code == 200, response.text
    return response.json()


def accept_body(snapshot, payload, *, count=None):
    return {'candidateVersion': snapshot['candidateVersion'],
        'prefixCount': count or snapshot['completedChapters'],
        'expectedAcceptedPrefix': snapshot['acceptedPrefix'],
        'expectedHeadRevisionId': snapshot['lastAcceptedRevisionId'] or payload['expectedHeadRevisionId'],
        'target': 'append' if snapshot['acceptedPrefix'] else 'child', 'sourcePolicy': 'requireUnchanged'}


def test_three_separate_saved_chapters_and_atomic_prefix(client, reader, demo, profile):
    receipt, payload = start(client, reader, demo, profile, count=3)
    with SessionLocal() as db:
        source_head = db.get(Branch, demo['branchId']).head_revision_id
    process(receipt['taskId'])
    snapshot = get(client, receipt)
    assert snapshot['state'] == 'succeeded', snapshot
    assert snapshot['completedChapters'] == snapshot['requestedChapters'] == 3
    assert all(c['canAccept'] and c['durability'] == 'saved' and len(c['text']) >= 600 for c in snapshot['chapters'])
    assert len({c['contentId'] for c in snapshot['chapters']}) == 3
    first = client.post(BASE + '/generations/' + receipt['generationId'] + '/accept',
        json=accept_body(snapshot, payload, count=1), headers={'Idempotency-Key': 'prefix-1'})
    assert first.status_code == 200, first.text
    repeated = client.post(BASE + '/generations/' + receipt['generationId'] + '/accept',
        json=accept_body(snapshot, payload, count=1), headers={'Idempotency-Key': 'prefix-1'})
    assert repeated.json() == first.json()
    assert get(client, receipt)['acceptedPrefix'] == 1
    next_body = accept_body(get(client, receipt), payload, count=3)
    second = client.post(BASE + '/generations/' + receipt['generationId'] + '/accept',
        json=next_body, headers={'Idempotency-Key': 'prefix-3'})
    assert second.status_code == 200, second.text
    assert second.json()['branchId'] == first.json()['branchId']
    with SessionLocal() as db:
        assert db.get(Branch, demo['branchId']).head_revision_id == source_head
        branch = db.get(Branch, second.json()['branchId'])
        assert branch.head_revision_id == second.json()['revisionId']
        assert len(db.get(Revision, branch.head_revision_id).manifest) >= 3
        progress = db.scalar(select(Progress).where(Progress.branch_id == branch.id,
            Progress.revision_id == branch.head_revision_id))
        assert progress and progress.max_observed_anchor['blockId'] != second.json()['readingAnchor']['blockId']


def test_rewrite_new_version_generates_only_unaccepted_and_same_public_task(client, reader, demo, profile):
    receipt, payload = start(client, reader, demo, profile, count=3)
    process(receipt['taskId'])
    initial = get(client, receipt)
    assert initial['state'] == 'succeeded', initial
    accepted = client.post(BASE + '/generations/' + receipt['generationId'] + '/accept',
        json=accept_body(initial, payload, count=1), headers={'Idempotency-Key': 'one'})
    assert accepted.status_code == 200, accepted.text
    rewrite = client.post(BASE + '/generations/' + receipt['generationId'] + '/rewrite',
        json={'candidateVersion': 1, 'expectedAcceptedPrefix': 1, 'instruction': '重新调查两位见证人。'},
        headers={'Idempotency-Key': 'rewrite-two'})
    assert rewrite.status_code == 202 and rewrite.json() == receipt, rewrite.text
    with SessionLocal() as db:
        before_calls = db.scalar(select(func.count()).select_from(UsageRecord))
    process(receipt['taskId'])
    current = get(client, receipt)
    assert current['state'] == 'succeeded' and current['candidateVersion'] == 2, current
    assert current['acceptedPrefix'] == 1 and current['chapters'][0] == {**initial['chapters'][0], 'canAccept': False}
    assert all(c['contentId'] != old['contentId'] for c, old in zip(current['chapters'][1:], initial['chapters'][1:]))
    assert current['eventCursor'] > initial['eventCursor']
    historical = client.get(BASE + '/generations/' + receipt['generationId'], params={'candidateVersion': 1}).json()
    assert [c['text'] for c in historical['chapters']] == [c['text'] for c in initial['chapters']]
    assert all(not c['canAccept'] for c in historical['chapters'])
    assert historical['lastAcceptedRevisionId']==accepted.json()['revisionId']
    assert historical['eventCursor'] < current['eventCursor']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before_calls + 4


def test_create_receipt_is_lookup_only_and_owner_isolated(client, reader, demo, profile):
    receipt, payload = start(client, reader, demo, profile)
    same = client.post(BASE + '/books/' + demo['bookId'] + '/generations', json=payload,
        headers={'Idempotency-Key': 'prose-create'})
    assert same.json() == receipt
    lookup = client.get(BASE + '/generations/request-status', params={'requestId': 'prose-create', 'bookId': demo['bookId']})
    assert lookup.json() == {'found': True, 'receipt': receipt}
    missing = client.get(BASE + '/generations/request-status', params={'requestId': 'not-submitted', 'bookId': demo['bookId']})
    assert missing.json() == {'found': False}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0
    assert client.post(BASE + '/auth/register', json={'username': 'other_prose', 'password': 'reader-password-123'}).status_code == 201
    login = client.post(BASE + '/auth/login', json={'username': 'other_prose', 'password': 'reader-password-123'}).json()
    client.headers['Authorization'] = 'Bearer ' + login['accessToken']
    for suffix in ('', '/versions'):
        assert client.get(BASE + '/generations/' + receipt['generationId'] + suffix).status_code == 404
    assert client.get(BASE + '/generations/request-status', params={'requestId': 'prose-create', 'bookId': demo['bookId']}).status_code == 404


def test_budget_rejection_does_not_create_task_or_intent(client, reader, demo, profile):
    anchor = read_chapter(client, demo)['blocks'][1]['endAnchor']; observe(client, reader, anchor)
    with SessionLocal() as db:
        db.get(ModelProfile, profile['id']).call_budget = 1; db.commit()
    response = client.post(BASE + '/books/' + demo['bookId'] + '/generations',
        json={'protocol': 'reader-prose-v1', 'originAnchor': anchor, 'operation': 'counterfactual',
            'chapterCount': 3, 'chapterLength': 'short', 'instruction': '留下', 'profileId': profile['id'],
            'expectedHeadRevisionId': anchor['revisionId'], 'target': {'mode': 'new_child_branch', 'name': '新线'}},
        headers={'Idempotency-Key': 'too-small'})
    assert response.status_code == 403 and response.json()['code'] == 'BUDGET_NOT_AUTHORIZED', response.text
    with SessionLocal() as db:
        for model in (Task, Consent, GenerationBatch, UsageRecord):
            assert db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize('length,floor', [('short', 600), ('standard', 1200), ('long', 2000)])
def test_each_length_has_its_own_floor_and_frozen_cap(client, reader, demo, profile, length, floor):
    receipt, _ = start(client, reader, demo, profile, length=length)
    process(receipt['taskId'])
    snapshot = get(client, receipt)
    assert snapshot['state'] == 'succeeded', snapshot
    chapter = snapshot['chapters'][0]
    assert len(''.join(chapter['text'].split())) >= floor and chapter['canAccept']
    with SessionLocal() as db:
        task = db.get(Task, receipt['taskId'])
        assert task.checkpoint['completionPolicy']['stages']['generation']['totalCompletionTokens'] == {'short':4096, 'standard':6144, 'long':8192}[length]


def test_first_chapter_accepts_while_second_is_actually_inflight(client, reader, demo, profile, monkeypatch):
    receipt, payload = start(client, reader, demo, profile, count=3)
    blocked, release = threading.Event(), threading.Event()
    original = MockAdapter.generate_stream
    def stream(self, p, prompt, data, limit, **kwargs):
        if data['chapter']['index'] == 2:
            blocked.set(); assert release.wait(10)
        return original(self, p, prompt, data, limit, **kwargs)
    monkeypatch.setattr(MockAdapter, 'generate_stream', stream)
    thread = threading.Thread(target=process, args=(receipt['taskId'],))
    thread.start()
    try:
        assert blocked.wait(10)
        snapshot = get(client, receipt)
        assert snapshot['completedChapters'] == 1 and snapshot['chapters'][0]['canAccept']
        assert not snapshot['chapters'][1]['canAccept']
        accepted = client.post(BASE + '/generations/' + receipt['generationId'] + '/accept',
            json=accept_body(snapshot, payload, count=1), headers={'Idempotency-Key':'inflight-prefix'})
        assert accepted.status_code == 200, accepted.text
        with SessionLocal() as db:
            assert db.get(Branch, accepted.json()['branchId']).head_revision_id == accepted.json()['revisionId']
    finally:
        release.set(); thread.join(15)
    assert not thread.is_alive()
    final = get(client, receipt)
    assert final['state'] == 'succeeded' and final['acceptedPrefix'] == 1
    assert final['chapters'][0]['text'] == snapshot['chapters'][0]['text']
    assert final['completedChapters'] == 3


def test_short_quality_warning_keeps_three_chapters_without_hidden_repair(client, reader, demo, profile, monkeypatch):
    receipt, payload = start(client, reader, demo, profile, count=3)
    original = MockAdapter.generate
    generated = []
    def limited(self, p, prompt, data, limit):
        if prompt.startswith('prose-chapter-'):
            generated.append(deepcopy(data))
            return {'text':'标题：未完的证言\n\n已收到的两段。\n\n调查尚未完成。','finishReason':'stop',
                'usage':{'inputTokens':19,'outputTokens':10,'state':'known'}}
        return original(self,p,prompt,data,limit)
    monkeypatch.setattr(MockAdapter,'generate',limited)
    process(receipt['taskId'])
    snapshot = get(client, receipt)
    assert snapshot['state'] == 'succeeded' and snapshot['completedChapters'] == 3
    assert len(generated) == 3 and all('repair' not in data for data in generated)
    assert snapshot['chapters'][0]['text'] == '已收到的两段。\n\n调查尚未完成。'
    assert all(c['canAccept'] and {'code':'CHAPTER_TOO_SHORT'} in c['hints'] for c in snapshot['chapters'])
    accepted = client.post(BASE + '/generations/' + receipt['generationId'] + '/accept',
        json=accept_body(snapshot,payload,count=3),headers={'Idempotency-Key':'accept-short-warning'})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['acceptedPrefix'] == 3


def test_soft_semantic_warning_does_not_discard_received_complete_chapter(client, reader, demo, profile, monkeypatch):
    receipt, _ = start(client, reader, demo, profile)
    original = MockAdapter.generate
    def warn(self,p,prompt,data,limit):
        if prompt.startswith('prose-review-'):
            return {'text':json.dumps({'completeChapter':True,'issues':['CHARACTER_VOICE','PRIVATE-RAW']}),
                'finishReason':'stop','usage':{'state':'known','inputTokens':5,'outputTokens':7}}
        return original(self,p,prompt,data,limit)
    monkeypatch.setattr(MockAdapter,'generate',warn)
    process(receipt['taskId'])
    snapshot = get(client,receipt)
    assert snapshot['state'] == 'succeeded' and snapshot['chapters'][0]['canAccept']
    assert snapshot['chapters'][0]['hints'] == [{'code':'CHARACTER_VOICE'}]
    assert 'PRIVATE-RAW' not in json.dumps(snapshot)


def test_cancel_unknown_partial_preserves_bytes_and_save_never_resends(client, reader, demo, profile, monkeypatch):
    receipt, _ = start(client,reader,demo,profile)
    calls=[]
    def interrupted(self,p,prompt,data,limit,*,on_delta,**kwargs):
        calls.append(1); on_delta('眼前😀已收到。')
        raise Problem('PROVIDER_STATUS_UNKNOWN','PRIVATE-SUPPLIER',502,{'reason':'transport_error'})
    monkeypatch.setattr(MockAdapter,'generate_stream',interrupted)
    process(receipt['taskId'])
    unknown = get(client,receipt)
    assert unknown['state'] == 'result-unknown' and unknown['chapters'][0]['text'] == '眼前😀已收到。'
    assert not unknown['chapters'][0]['canAccept'] and 'PRIVATE-SUPPLIER' not in json.dumps(unknown)
    save = client.post(BASE+'/generations/'+receipt['generationId']+'/save',json={},headers={'Idempotency-Key':'save-received'})
    assert save.status_code == 202,save.text
    process(receipt['taskId'])
    recovered = get(client,receipt)
    assert recovered['chapters'][0]['text'] == unknown['chapters'][0]['text']
    assert recovered['chapters'][0]['durability'] == 'saved' and not recovered['chapters'][0]['canAccept']
    assert calls == [1]
    with SessionLocal() as db:
        usages=list(db.scalars(select(UsageRecord).where(UsageRecord.task_id==receipt['taskId'])))
        assert len(usages)==2 and sum(u.usage_state=='unknown' for u in usages)==1


def test_final_database_mirror_failure_recovers_full_receipt_without_new_calls(client, reader, demo, profile, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.prose import stream
    receipt,_=start(client,reader,demo,profile)
    original=stream.mirror
    failures=[]
    def fail_final(ctx,value,*,final=False):
        if final and not failures:
            failures.append(1);raise OperationalError('synthetic',{},Exception('isolated database interruption'))
        return original(ctx,value,final=final)
    monkeypatch.setattr(stream,'mirror',fail_final)
    process(receipt['taskId'])
    failed=get(client,receipt)
    assert failed['persistenceState']=='recoverable' and failed['chapters'][0]['text']
    assert failed['chapters'][0]['durability']=='journaled' and not failed['chapters'][0]['canAccept']
    with SessionLocal() as db:
        before=list(db.scalars(select(UsageRecord).where(UsageRecord.task_id==receipt['taskId'])))
        amounts=[(u.id,u.input_tokens,u.output_tokens,u.status) for u in before]
        reservations=(db.get(ModelProfile,profile['id']).input_reserved,db.get(ModelProfile,profile['id']).output_reserved)
    process(receipt['taskId'])
    restored=get(client,receipt)
    assert restored['state']=='succeeded' and restored['chapters'][0]['canAccept']
    assert restored['chapters'][0]['text']==failed['chapters'][0]['text']
    with SessionLocal() as db:
        assert [(u.id,u.input_tokens,u.output_tokens,u.status) for u in db.scalars(select(UsageRecord).where(UsageRecord.task_id==receipt['taskId']))]==amounts
        assert (db.get(ModelProfile,profile['id']).input_reserved,db.get(ModelProfile,profile['id']).output_reserved)==reservations


def test_frozen_encrypted_profile_is_used_after_edit_and_never_public(client, reader, demo, profile, monkeypatch):
    receipt,_=start(client,reader,demo,profile)
    with SessionLocal() as db:
        frozen=deepcopy(db.scalar(select(GenerationBatch)).profile_snapshot)
        p=db.get(ModelProfile,profile['id']);p.version+=1;p.model_id='changed-model';p.key_cipher='NOT-A-VALID-CIPHER';db.commit()
    seen=[];original=MockAdapter.generate
    def capture(self,p,prompt,data,limit):
        seen.append((p.model_id,p.version,p.key_cipher));return original(self,p,prompt,data,limit)
    monkeypatch.setattr(MockAdapter,'generate',capture)
    process(receipt['taskId'])
    assert get(client,receipt)['state']=='succeeded'
    assert seen and all(value==(frozen['model_id'],frozen['version'],frozen['key_cipher']) for value in seen)
    public=json.dumps(get(client,receipt)) + client.get(BASE+'/tasks/'+receipt['taskId']).text
    assert frozen['key_cipher'] not in public and 'profile_snapshot' not in public


def test_replay_events_are_nested_monotonic_and_utf16_offsets(client, reader, demo, profile):
    receipt,_=start(client,reader,demo,profile)
    process(receipt['taskId'])
    response=client.get(BASE+'/tasks/'+receipt['taskId']+'/events')
    assert response.status_code==200
    events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert events and all(set(event)=={'taskId','seq','type','payload'} for event in events)
    assert all(event['type'] in {'phase','text_delta','chapter_saved','content_hint','usage','terminal'} for event in events)
    assert [event['seq'] for event in events]==list(range(1,get(client,receipt)['eventCursor']+1))
    tail=client.get(BASE+'/tasks/'+receipt['taskId']+'/events',headers={'Last-Event-ID':receipt['taskId']+':'+str(events[-2]['seq'])})
    replay=[json.loads(line[6:]) for line in tail.text.splitlines() if line.startswith('data: ')]
    assert replay==events[-1:]


def test_conflict_keeps_candidate_and_only_explicit_pinned_fork_commits(client, reader, demo, profile):
    receipt,payload=start(client,reader,demo,profile)
    process(receipt['taskId']);snapshot=get(client,receipt)
    with SessionLocal() as db:
        branch=db.get(Branch,demo['branchId']);old=db.get(Revision,branch.head_revision_id)
        revised=Revision(owner_id=branch.owner_id,branch_id=branch.id,parent_revision_id=old.id,
            sequence=old.sequence+1,manifest=deepcopy(old.manifest),content_hash=old.content_hash,provenance={'type':'fixture'})
        db.add(revised);db.flush();branch.head_revision_id=revised.id;branch.version+=1;db.commit()
    body=accept_body(snapshot,payload,count=1)
    conflict=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=body,headers={'Idempotency-Key':'conflict'})
    assert conflict.status_code==409 and conflict.json()['code']=='VERSION_CONFLICT'
    assert get(client,receipt)['acceptedPrefix']==0
    fork=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json={**body,'sourcePolicy':'forkPinned'},headers={'Idempotency-Key':'explicit-fork'})
    assert fork.status_code==200,fork.text
    with SessionLocal() as db:
        assert db.get(Branch,demo['branchId']).head_revision_id==revised.id


def test_structurally_complete_chapter_keeps_soft_review_warning_and_can_be_accepted(client, reader, demo, profile, monkeypatch):
    receipt,payload=start(client,reader,demo,profile)
    original=MockAdapter.generate;reviews=[];generations=[]
    def unfinished(self,p,prompt,data,limit):
        if prompt.startswith('prose-chapter-'):generations.append(data)
        if prompt.startswith('prose-review-'):
            reviews.append(1)
            return {'text':json.dumps({'completeChapter':False,'issues':['CHAPTER_PROGRESS_UNCERTAIN']}),
                'finishReason':'stop','usage':{'state':'known','inputTokens':7,'outputTokens':9}}
        return original(self,p,prompt,data,limit)
    monkeypatch.setattr(MockAdapter,'generate',unfinished)
    process(receipt['taskId']);snapshot=get(client,receipt)
    assert len(snapshot['chapters'][0]['text'])>=600
    # Final product GEN-08 separates the model's uncertain content review from
    # structural completeness. The soft issue remains visible after acceptance.
    assert snapshot['chapters'][0]['completeness']=='complete' and snapshot['chapters'][0]['canAccept']
    assert any(h['code']=='CHAPTER_PROGRESS_UNCERTAIN' for h in snapshot['chapters'][0]['hints'])
    assert len(generations)==len(reviews)==1
    accepted=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=accept_body(snapshot,payload,count=1),headers={'Idempotency-Key':'with-soft-warning'})
    assert accepted.status_code==200,accepted.text


def test_ten_long_chapters_carry_bounded_prior_context_without_silent_count_change(client, reader, demo, profile, monkeypatch):
    receipt,_=start(client,reader,demo,profile,count=10,length='long')
    original=MockAdapter.generate;seen=[]
    def capture(self,p,prompt,data,limit):
        if prompt.startswith('prose-chapter-'):seen.append(deepcopy(data))
        return original(self,p,prompt,data,limit)
    monkeypatch.setattr(MockAdapter,'generate',capture)
    process(receipt['taskId']);snapshot=get(client,receipt)
    assert snapshot['state']=='succeeded' and snapshot['completedChapters']==10,snapshot
    assert len(seen)==10 and [data['chapter']['index'] for data in seen]==list(range(1,11))
    # The immediately preceding received chapter remains exact. Older chapters
    # may be bounded by explicit ending excerpts, rather than fabricated facts.
    assert seen[-1]['previousChapters'][-1]['text']==snapshot['chapters'][8]['text']
