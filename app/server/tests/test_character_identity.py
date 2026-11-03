"""Evidence-based identity must survive chunk boundaries and legacy snapshots."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.domain import digest, make_anchor, utf16_len
from app.errors import Problem
from app.knowledge import validate_analysis, merge_analysis, visible_characters, require_role_character, prior_identity_context
from app.models import AnalysisChunk, KnowledgeSnapshot, RoleMessage, UsageRecord
from app.worker import process
from .helpers import BASE
from .test_reader import analyzed_session


def extraction(block, quotes):
    return {'schemaVersion': 1, 'scopeHash': 'a' * 64,
        'characters': [{'localId': f'c{i}', 'displayName': '阿岑', 'evidenceIds': [f'e{i}'],
                        'priorRef': None, 'continuity': 'uncertain'} for i in range(len(quotes))],
        'facts': [{'localId': f'f{i}', 'subjectRef': f'c{i}', 'predicate': 'action', 'value': quote,
                   'certainty': 'explicit', 'evidenceIds': [f'e{i}'], 'knownBy': [f'c{i}'],
                   'knowledge': [{'characterRef': f'c{i}', 'evidenceIds': [f'e{i}'], 'validity': 'at_scope'}]}
                  for i, quote in enumerate(quotes)],
        'evidence': [{'id': f'e{i}', 'blockId': block.id, 'quote': quote} for i, quote in enumerate(quotes)],
        'unresolved': [], 'summary': ''}


def validator(monkeypatch, text):
    block = SimpleNamespace(id='one-block', chapter_id='chapter', owner_id='owner', text=text)
    monkeypatch.setattr('app.knowledge.owned', lambda db, model, identifier, owner: block)
    anchor = make_anchor('book', 'branch', 'revision', block, utf16_len(text), utf16_len(text))
    def run(payload, chunk=None, prior=None):
        return validate_analysis(json.dumps(payload, ensure_ascii=False),
            chunk or [{'blockId': block.id, 'text': text, 'sourceStart': 0}],
            payload['scopeHash'], None, block.owner_id, anchor, prior or [])
    return block, run


def test_distinct_same_name_people_and_explicit_continuity_across_chunks(monkeypatch):
    q1, q2 = '船夫阿岑亲手藏了蓝色船票。', '医师阿岑亲眼看见红色病历。'
    text = q1 + '风' * (400 - len(q1)) + q2
    block, run = validator(monkeypatch, text)
    together = run(extraction(block, [q1, q2]))
    assert len({c['id'] for c in together['characters']}) == 2
    assert len({f['subjectId'] for f in together['facts']}) == 2
    assert len({f['knownBy'][0] for f in together['facts']}) == 2
    assert all(not c['available'] and c['reasonCode'] == 'ROLE_IDENTITY_UNCERTAIN'
               for c in visible_characters(SimpleNamespace(data=merge_analysis([together]))))
    first = run(extraction(block, [q1]), [{'blockId': block.id, 'text': text[:400], 'sourceStart': 0}])
    later = extraction(block, [q2])
    second = run(later, [{'blockId': block.id, 'text': text[400:], 'sourceStart': 400}], first['characters'])
    assert len(merge_analysis([first, second])['characters']) == 2
    # A later independently quoted mention may inherit only a supplied confirmed identity.
    later['characters'][0].update(priorRef=first['characters'][0]['id'], continuity='explicit')
    continued = run(later, [{'blockId': block.id, 'text': text[400:], 'sourceStart': 400}], first['characters'])
    assert continued['characters'][0]['id'] == first['characters'][0]['id']
    assert len(merge_analysis([first, continued])['characters']) == 1
    later['characters'][0]['priorRef'] = 'not-in-fixed-prefix'
    with pytest.raises(Problem, match='角色继承'):
        run(later, prior=first['characters'])


def test_repeated_first_evidence_or_local_ids_cannot_merge_unconfirmed_people(monkeypatch):
    quote = '阿岑听见远处传来钟声。'
    block, run = validator(monkeypatch, quote)
    payload = extraction(block, [quote, quote])
    with pytest.raises(Problem) as problem:
        run(payload)
    assert problem.value.code == 'ANALYSIS_IDENTITY_INVALID'
    payload['characters'][1]['localId'] = 'c0'
    with pytest.raises(Problem) as problem:
        run(payload)
    assert problem.value.code == 'ANALYSIS_INVALID'
    legacy_id = digest('branch' + '阿岑' + block.id)[:32]
    legacy = {'characters': [{'id': legacy_id, 'displayName': '阿岑', 'evidenceIds': ['one']},
                             {'id': legacy_id, 'displayName': '阿岑', 'evidenceIds': ['two']}],
              'facts': [], 'evidence': [], 'unresolved': []}
    merged = merge_analysis([legacy])
    assert len(merged['characters']) == 1
    assert visible_characters(SimpleNamespace(data=merged))[0]['reasonCode'] == 'ROLE_IDENTITY_UNCERTAIN'


def test_legacy_same_block_collision_blocks_new_old_and_queued_sessions_without_call(client, reader, demo, profile):
    session, characters, anchor = analyzed_session(client, reader, demo, profile, name='阿岑')
    assert session.status_code == 201, session.text
    sid, cid = session.json()['id'], session.json()['characterId']
    payload = {'operation': 'role_chat', 'profileId': profile['id'], 'sessionId': sid,
               'message': '此刻你在想什么？', 'turnId': 'queued-before-identity-recheck'}
    plan = client.post(BASE + '/operation-plans', json=payload).json()
    consent = client.post(BASE + '/consents', json={'planId': plan['planId'], 'planHash': plan['planHash'],
        'profileId': profile['id'], 'maxCalls': plan['maxCalls'], 'inputTokens': plan['inputTokens'],
        'outputTokens': plan['outputTokens']}).json()
    submitted = client.post(BASE + '/role-sessions/' + sid + '/turns',
        json={**payload, 'consentId': consent['consentId']}, headers={'Idempotency-Key': 'identity-queued'})
    assert submitted.status_code == 202, submitted.text
    with SessionLocal() as db:
        snapshot = db.get(KnowledgeSnapshot, characters['snapshotId'])
        original = deepcopy(snapshot.data)
        target = next(c for c in original['characters'] if c['id'] == cid)
        evidence = next(e for e in original['evidence'] if e['id'] in target['evidenceIds'])
        # Simulate a pre-upgrade merged snapshot and its two original colliding local people.
        legacy = deepcopy(original); legacy['identityProofVersion'] = 0
        legacy_target = next(c for c in legacy['characters'] if c['id'] == cid)
        second_evidence = {**evidence, 'id': 'legacy-private-evidence'}
        legacy_target['evidenceIds'].append(second_evidence['id'])
        legacy['evidence'].append(second_evidence)
        chunk = AnalysisChunk(owner_id=snapshot.owner_id, book_id=snapshot.book_id,
            branch_id=snapshot.branch_id, cache_hash=digest('legacy-identity-test'), data={
                'characters': [{**target}, {**target, 'evidenceIds': [second_evidence['id']]}],
                'facts': [], 'evidence': [evidence, second_evidence], 'unresolved': []})
        db.add(chunk); snapshot.data = legacy
        before = db.scalar(select(func.count()).select_from(UsageRecord)); db.commit()
    refused = client.post(BASE + '/operation-plans', json={**payload, 'turnId': 'new-after-recheck'})
    assert refused.status_code == 422 and refused.json()['code'] == 'ROLE_UNAVAILABLE'
    refused = client.post(BASE + '/role-sessions', json={'bookId': demo['bookId'], 'characterId': cid,
        'snapshotId': characters['snapshotId'], 'anchor': anchor})
    assert refused.status_code == 422 and refused.json()['code'] == 'ROLE_UNAVAILABLE'
    listed = client.get(BASE + '/books/' + demo['bookId'] + '/characters', params={
        'branchId': demo['branchId'], 'revisionId': demo['revisionId'], 'anchor': json.dumps(anchor)}).json()
    assert next(c for c in listed['items'] if c['id'] == cid)['reasonCode'] == 'ROLE_IDENTITY_UNCERTAIN'
    process(submitted.json()['taskId'])
    task = client.get(BASE + '/tasks/' + submitted.json()['taskId']).json()
    assert task['state'] == 'failed' and task['errorCode'] == 'ROLE_UNAVAILABLE'
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before
        assert db.scalar(select(func.count()).select_from(RoleMessage)) == 0
        snapshot = db.get(KnowledgeSnapshot, characters['snapshotId'])
        # Keep a future colliding chunk cached, but exclude its evidence from this fixed scope.
        past = deepcopy(original); past['identityProofVersion'] = 0
        snapshot.data = past
        chunk = db.scalar(select(AnalysisChunk).where(AnalysisChunk.cache_hash == digest('legacy-identity-test')))
        chunk.data = {**chunk.data, 'characters': [{**target, 'evidenceIds': ['future-only']},
                     {**target, 'evidenceIds': ['future-only']}], 'evidence': [{**evidence, 'id': 'future-only'}]}
        db.commit()
    allowed = client.post(BASE + '/operation-plans', json={**payload, 'turnId': 'fixed-past-still-valid'})
    assert allowed.status_code == 201, allowed.text


def test_legacy_collision_across_two_cached_subchunks_uses_32_character_identity(client, reader, demo, profile):
    session, characters, _ = analyzed_session(client, reader, demo, profile, name='阿岑')
    assert session.status_code == 201, session.text
    with SessionLocal() as db:
        snapshot = db.get(KnowledgeSnapshot, characters['snapshotId'])
        target = next(c for c in snapshot.data['characters'] if c['id'] == session.json()['characterId'])
        evidence = next(e for e in snapshot.data['evidence'] if e['id'] in target['evidenceIds'])
        old_id = digest(snapshot.branch_id + target['displayName'] + evidence['anchor']['blockId'])[:32]
        old_evidence = [{**evidence, 'id': 'old-first-quote'}, {**evidence, 'id': 'old-second-quote'}]
        old_character = {'id': old_id, 'displayName': target['displayName'],
                         'evidenceIds': [e['id'] for e in old_evidence]}
        fixed = SimpleNamespace(owner_id=snapshot.owner_id, branch_id=snapshot.branch_id,
            data={'characters': [old_character], 'facts': [], 'evidence': old_evidence})
        for index, e in enumerate(old_evidence):
            db.add(AnalysisChunk(owner_id=snapshot.owner_id, book_id=snapshot.book_id,
                branch_id=snapshot.branch_id, cache_hash=digest(f'old-subchunk-{index}'),
                data={'characters': [{**old_character, 'evidenceIds': [e['id']]}],
                      'facts': [], 'evidence': [e], 'unresolved': []}))
        db.flush()
        with pytest.raises(Problem) as problem:
            require_role_character(fixed, old_id, db)
        assert problem.value.code == 'ROLE_UNAVAILABLE'
        # Later cached evidence cannot invalidate the earlier immutable snapshot.
        fixed.data['characters'][0]['evidenceIds'] = [old_evidence[0]['id']]
        fixed.data['evidence'] = old_evidence[:1]
        assert require_role_character(fixed, old_id, db)['id'] == old_id


def test_identity_references_preserve_first_and_recent_exact_evidence_and_refuse_oversize():
    revision = SimpleNamespace(manifest=[{'blocks': ['first', 'middle', 'recent']}])
    initial = '林渡和阿岑沿码头向东走。' + '潮声轻轻拍着木板。' * 35
    recent = '林渡将方才的木楔塞进门框，接着提起锤子。'
    def evidence(identifier, block, quote, start=0):
        return {'id': identifier, 'quote': quote, 'anchor': {'blockId': block, 'start': start, 'end': start + utf16_len(quote)}}
    merged = {'characters': [
        {'id': 'lin-one', 'displayName': '林渡', 'evidenceIds': ['recent-e', 'first-e', 'middle-e']},
        {'id': 'lin-two', 'displayName': '林渡', 'evidenceIds': ['same-quote-e']},
        {'id': 'unmentioned', 'displayName': '阿岑', 'evidenceIds': ['first-e']}],
        'evidence': [evidence('first-e', 'first', initial), evidence('middle-e', 'middle', '不可带入的中间证据。'),
                     evidence('recent-e', 'recent', recent), evidence('same-quote-e', 'first', initial)],
        'summary': '不可带入的自由总结和未来秘密'}
    context = prior_identity_context(merged, '林渡继续修门。', revision)
    assert [c['id'] for c in context['priorCharacters']] == ['lin-one', 'lin-two']
    assert all(c['identityUncertain'] for c in context['priorCharacters'])
    assert context['priorCharacters'][0]['evidenceIds'] == ['first-e', 'recent-e']
    assert len(context['priorIdentityEvidence']) == 2
    assert context['priorIdentityEvidence'][0] == {'evidenceIds': ['first-e', 'same-quote-e'], 'quote': initial}
    assert context['priorIdentityEvidence'][1]['quote'] == recent
    assert '不可带入' not in json.dumps(context, ensure_ascii=False)
    oversized = deepcopy(merged); oversized['evidence'][0]['quote'] = '完整证据' * 1000
    with pytest.raises(Problem) as problem:
        prior_identity_context(oversized, '林渡', revision)
    assert problem.value.code == 'CONTEXT_BUDGET_EXCEEDED'
