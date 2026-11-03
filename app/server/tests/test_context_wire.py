"""Lossless budget reduction must retain every character-knowledge constraint."""
from copy import deepcopy
from types import SimpleNamespace
import json
import httpx
import pytest
from app.context_wire import encode_context, decode_context, wire_json
from app.domain import canonical
from pathlib import Path


def test_legacy_v1_fixture_and_v2_evidence_ids_preserve_every_quote_and_optional_field():
    legacy = json.loads((Path(__file__).parent / 'fixtures/context-rows-v1.json').read_text('utf-8'))
    assert legacy['encoded']['contextEncoding'] == 'context-rows-v1'
    assert decode_context(legacy['encoded']) == legacy['original']
    original = dense_context()
    # Include unused voice support, an empty quotation and an ID-only entry.
    original['evidence'] = [{'id': 'e' * 32, 'quote': 'e0 c0 p0 是原始引用'},
                            {'id': 'q' * 32, 'quote': ''}, {'id': 'v' * 32}]
    encoded = encode_context(original)
    assert encoded['contextEncoding'] == 'context-rows-v2'
    assert decode_context(encoded) == original
    assert encoded['evidence'][0]['quote'] == original['evidence'][0]['quote']
    assert encoded['evidence'][1]['quote'] == '' and 'quote' not in encoded['evidence'][2]
    assert original['evidence'][2]['id'] in encoded['evidenceRefs'].values()
    assert encoded['message'] == original['message'] and encoded['history'] == original['history']
    assert encode_context(encoded) == encoded


def test_future_evidence_fields_fall_back_and_corrupt_evidence_refs_are_rejected():
    for evidence in [[{'id': 'e' * 32, 'quote': '原文', 'futureConstraint': False}],
                     [{'id': 0}], [{'id': 'e' * 32, 'quote': None}], {'id': 'e' * 32}]:
        original = dense_context()
        original['evidence'] = evidence
        assert encode_context(original) == original
        assert decode_context(encode_context(original)) == original
    encoded = encode_context(dense_context())
    for evidence in [[{'id': 'e999'}], [{'id': 'c0'}], [{'id': False}],
                     [{'id': 'e0', 'quote': 0}], [{'id': 'e0', 'future': None}], {}]:
        corrupted = deepcopy(encoded)
        corrupted['evidence'] = evidence
        with pytest.raises(ValueError):
            decode_context(corrupted)


def test_dense_validation_fits_strict_cap_with_full_body_facts_quotes_and_hidden_original():
    from app.worker import PROMPTS
    # A realistic dense gate retains all required safety material. The old gate
    # cost is frozen from the pre-change prompt, rather than inferred from v7.
    original = dense_context(12)
    original['facts'] = original.pop('characterFacts')
    original['output'] = '桥边的风卷起灰尘，林渡仍守着已修好的木板。' * 45
    original['hiddenOriginal'] = [{'id': 'future', 'text': '隔离审查专用未读身份，原文全部保留。'}]
    original['evidence'] = [{'id': f'{i:032x}', 'quote': '当时他就在桥边。' * 5} for i in range(9)]
    for i, fact in enumerate(original['facts']):
        fact['value'] = '林渡在桥边谨慎检查木板，记得他先前的行动。' * 2
        fact['evidenceIds'] = [original['evidence'][i % 9]['id']]
        fact['knowledgeAt'][0]['evidenceIds'] = fact['evidenceIds'][:]
    before = deepcopy(original)
    packed = encode_context(original)
    legacy = deepcopy(packed)
    legacy['contextEncoding'] = 'context-rows-v1'
    for evidence in legacy['evidence']:
        evidence['id'] = legacy['evidenceRefs'][evidence['id']]
    overhead = (PROMPTS / 'validation.txt').stat().st_size + 256
    dump = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    # Calibrate only fixture body length until the historical 12k failure occurs;
    # no production input or cap is modified by this test.
    while len(dump(legacy)) + 3959 + 256 <= 12000:
        original['output'] += '风声仍停在桥边。'
        packed['output'] = legacy['output'] = original['output']
    assert len(dump(packed)) + overhead <= 12000
    assert decode_context(packed) == original and decode_context(legacy) == original
    assert len(original['facts']) == 12 and len(original['evidence']) == 9
    assert original['hiddenOriginal'] == before['hiddenOriginal']
    assert all('quote' in evidence for evidence in original['evidence'])


def dense_context(count=28):
    character = 'c' * 32
    evidence = 'e' * 32
    position = 'p' + 'a' * 16
    return {'characterFacts': [{'id': f'{i:032x}', 'subjectId': character, 'subject': '林渡',
        'predicate': 'action', 'value': {'literal': 'c0 e0 p0', 'zero': 0, 'false': False, 'null': None},
        'evidenceIds': [evidence], 'knownBy': [character], 'occurredOrder': None,
        'temporalRelation': 'at_scope', 'readerRevealAnchor': position,
        'knowledgeAt': [{'characterId': character, 'evidenceIds': [evidence], 'learnedAt': position,
                         'learnedOrder': None, 'validity': 'at_scope'}]} for i in range(count)],
        'sourcePositions': {position: {'chapterId': '1' * 32, 'blockId': '2' * 32, 'start': 0, 'end': 42}},
        'evidence': [{'id': evidence}], 'characterNames': {character: '林渡'},
        'character': {'id': character, 'voice': {'traits': ['句式简短'], 'evidenceIds': [evidence]}},
        'prefix': [], 'scope': {'branchId': '3' * 32, 'revisionId': '4' * 32, 'end': 42},
        'message': 'c0 e0 p0 是我的原话', 'history': [{'role': 'user', 'text': 'e0 c0 p0'}]}


def test_lossless_context_preserves_prose_types_missing_fields_and_rejects_invalid_refs():
    original = dense_context()
    before = deepcopy(original)
    encoded = encode_context(original)
    assert encoded['contextEncoding'] == 'context-rows-v2'
    assert original == before and decode_context(encoded) == original
    assert len(wire_json(original).encode()) < len(canonical(original).encode()) / 2
    assert encoded['message'] == original['message'] and encoded['history'] == original['history']
    assert encoded['evidenceRefs'][encoded['evidence'][0]['id']] == original['evidence'][0]['id']
    assert 'quote' not in encoded['evidence'][0]
    for key in ('occurredOrder', 'value'):
        incomplete = deepcopy(original)
        incomplete['characterFacts'][0].pop(key)
        assert encode_context(incomplete) == incomplete
    extra = deepcopy(original)
    extra['characterFacts'][0]['futureConstraint'] = False
    assert decode_context(encode_context(extra)) == extra
    for index, bad in [(1, 'c999'), (1, 'e0'), (1, 0), (5, ['e999']), (9, 'p999')]:
        corrupted = deepcopy(encoded)
        corrupted['characterFacts'][0][index] = bad
        with pytest.raises(ValueError):
            decode_context(corrupted)
    corrupted = deepcopy(encoded)
    corrupted['characterFacts'][0][10][0][2] = 'c0'
    with pytest.raises(ValueError):
        decode_context(corrupted)
    validation = {**deepcopy(original), 'facts': deepcopy(original['characterFacts']),
                  'hiddenOriginal': [{'id': 'future', 'text': '隔离审查专用原文'}]}
    validation.pop('characterFacts')
    assert decode_context(encode_context(validation)) == validation
    assert 'hiddenOriginal' not in encoded


def test_dense_role_context_fits_without_dropping_known_facts_or_position_provenance(client, reader, demo, profile):
    from app.db import SessionLocal
    from app.models import KnowledgeSnapshot, RoleSession, Task
    from app.worker import generation_context, PROMPTS
    from .test_reader import analyzed_session
    created, _, anchor = analyzed_session(client, reader, demo, profile)
    with SessionLocal() as db:
        session = db.get(RoleSession, created.json()['id'])
        snapshot = db.get(KnowledgeSnapshot, session.snapshot_id)
        seed = deepcopy(next(f for f in snapshot.data['facts'] if session.character_id in f['knownBy']))
        source = []
        for i in range(28):
            fact = deepcopy(seed)
            fact['id'] = f'{i:032x}'
            fact['readerRevealAnchor'] = deepcopy(anchor)
            fact['occurredOrder'] = 'explicit-scene-' + str(i)
            fact['temporalRelation'] = 'at_scope'
            fact['knowledge'] = [{'characterId': session.character_id, 'evidenceIds': fact['evidenceIds'],
                                  'learnedAt': deepcopy(anchor), 'learnedOrder': None, 'validity': 'at_scope'}]
            source.append(fact)
        snapshot.data = {**snapshot.data, 'facts': source}
        task = Task(owner_id=session.owner_id, task_type='role_chat', payload={
            'anchor': anchor, 'profileId': profile['id'], 'sessionId': session.id,
            'operation': 'role_chat', 'message': '你此刻知道什么？'})
        data = generation_context(db, task, snapshot)
        overhead = (PROMPTS / 'role_chat.txt').stat().st_size + 256
        assert len(canonical(data).encode()) + overhead > 12000
        assert len(wire_json(data).encode()) + overhead <= 12000
        assert decode_context(json.loads(wire_json(data))) == data
        assert len(data['characterFacts']) == 28 and data['prefix'] == []
        for original, fact in zip(source, data['characterFacts']):
            assert fact['id'] == original['id'] and fact['value'] == original['value']
            assert fact['evidenceIds'] == original['evidenceIds']
            assert fact['occurredOrder'] == original['occurredOrder'] and fact['temporalRelation'] == 'at_scope'
            assert fact['knowledgeAt'][0]['learnedOrder'] is None
            assert data['sourcePositions'][fact['knowledgeAt'][0]['learnedAt']] == {k: anchor[k] for k in ('chapterId', 'blockId', 'start', 'end')}
        assert all('quote' not in e for e in data['evidence'])


def test_actual_http_user_content_equals_budget_serialization_and_is_nonmutating(monkeypatch):
    from app.providers import ChatCompletionsAdapter, encrypt_key
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '完成'}, 'finish_reason': 'stop'}],
                                       'usage': {'prompt_tokens': 7, 'completion_tokens': 2}})
    factory = httpx.AsyncClient
    monkeypatch.setattr('app.providers.validate_url', lambda url: ('approved.example', '93.184.216.34'))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs))
    profile = SimpleNamespace(base_url='https://approved.example/v1', key_cipher=encrypt_key('WIRE-KEY-NOT-REAL'),
                              model_id='contract-model', output_parameter='max_tokens', supports_json=True)
    original = dense_context()
    original['_structured'] = True
    before = deepcopy(original)
    ChatCompletionsAdapter().generate(profile, 'role_chat-v6', original, 1024)
    assert len(calls) == 1 and calls[0].extensions['sni_hostname'] == 'approved.example'
    body = json.loads(calls[0].content)
    assert body['messages'][1]['content'] == wire_json(original)
    assert decode_context(json.loads(body['messages'][1]['content'])) == before and original == before
    assert body['max_tokens'] == 1024 and body['response_format'] == {'type': 'json_object'}
