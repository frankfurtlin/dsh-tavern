"""Reading bytes vs original content, actual durable PostgreSQL interleavings."""
from copy import deepcopy
import json
import threading
import time
import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import OperationalError
from app.db import SessionLocal
from app.models import Task, TaskEvent, GenerationChapterVersion, UsageRecord
from app.prose.presentation import project, record, validate_record, validate_ledger
from app.prose.worker import received_chapter
from app.prose import stream
from app.providers import MockAdapter
from app.worker import process
from app.errors import Problem
from .test_prose_generations import isolated_prose, start, get, accept_body
from .helpers import BASE


@pytest.mark.parametrize('raw', [
    '{"title":"信的去向","body":"她接过信。\\n\\n🙂她走向南岸。"}',
    '```json\n{"标题":"信的去向","正文":"她接过信。\\n\\n🙂她走向南岸。"}\n```',
    '<think>PRIVATE分析<analysis>内部</analysis></think>{"title":"信的去向","text":"她接过信。\\n\\n🙂她走向南岸。"}',
    '{"analysis":{"private":"PRIVATE"},"content":"她接过信。\\n\\n🙂她走向南岸。","title":"信的去向"}',
])
def test_every_prefix_projects_body_without_control_or_reasoning(raw):
    for end in range(len(raw) + 1):
        value = project(raw[:end], 1)
        assert 'PRIVATE' not in value['text'] and '<think' not in value['text']
        assert not value['text'].startswith(('{', '```'))
        value['text'].encode('utf-8')
    full = project(raw, 1)
    assert full == {'text':'她接过信。\n\n🙂她走向南岸。', 'title':'信的去向', 'format':'json', 'thoughtOmitted':'<think>' in raw, 'pending':False}
    saved = record(raw, 1, finish_reason='stop')
    assert validate_record(saved, full['text'], full['title']) == saved


@pytest.mark.parametrize('raw', [
    '{"body":"一","body":"二"}', '{"body":"一","text":"二"}',
    '[{"body":"一"}]', '{"body":123}', '{"tool_call":{"body":"内部"}}',
    '{"body":"\\ud800"}', '{"body":"\\uZZZZ"}', '{"body":"\\u0000正文"}',
])
def test_ambiguous_or_invalid_content_never_becomes_a_complete_chapter(raw):
    value = project(raw, 2)
    assert value['text'] == '' and value['format'] == 'unrecognized'
    assert not received_chapter({'text':raw,'finishReason':'stop'}, 2, 'short')[2]


def test_nested_and_mismatched_thought_markers_cannot_reveal_hidden_text():
    assert project('<think>一<think>二</think>仍隐藏</think>正文', 1)['text'] == '正文'
    mismatched = project('<think><analysis>私密</think>仍隐藏</analysis>', 1)
    assert mismatched['text'] == '' and mismatched['pending']
    for partial in ('<', '<th', '<think', '</analys', '{"body":"甲\\uD83D', '{"body":"甲\\uD83D\\uD'):
        value = project(partial, 1)
        assert value['pending'] and 'th' not in value['text'] and '\\u' not in value['text']


def test_history_keeps_original_and_repair_bytes_and_refuses_forged_projection():
    original = record('{"tool":"无正文"}', 2, finish_reason='stop')
    fixed = record('{"title":"第二章","body":"她把信交给梅舟。"}', 2, kind='repair', finish_reason='stop', previous=original)
    assert len(fixed['attempts']) == 2 and fixed['attempts'][0] == original['attempts'][0]
    assert validate_ledger({'1':{'2':fixed}})['1']['2'] == fixed
    for poison in ('raw', 'body', 'index', 'duplicate'):
        bad = deepcopy(fixed)
        if poison == 'raw': bad['attempts'][0]['raw'] += '改'
        elif poison == 'body': bad['presentation']['text'] += '改'
        elif poison == 'index': bad['chapterIndex'] = 3
        else: bad['attempts'][1]['kind'] = 'original'
        with pytest.raises(ValueError): validate_ledger({'1':{'2':bad}})


def test_deep_supplier_metadata_retains_original_instead_of_recursively_crashing():
    raw = '{"body":"合法文本","metadata":' + '[' * 10000 + '0' + ']' * 10000 + '}'
    saved = record(raw,1,finish_reason='stop')
    assert saved['attempts'][0]['raw'] == raw
    assert saved['presentation']['format'] == 'unrecognized' and not saved['presentation']['text']
    assert validate_record(saved) == saved
    assert not received_chapter({'text':raw,'finishReason':'stop'},1,'short')[2]


@pytest.mark.parametrize('depth', [65, 1500, 20000])
@pytest.mark.parametrize('complete', [False, True])
def test_depth_bound_precedes_full_and_partial_decoder(depth, complete):
    raw = '{"metadata":' + '[' * depth + '0'
    if complete:
        raw += ']' * depth + ',"body":"可读正文。"}'
    result = record(raw, 1, finish_reason='stop' if complete else None)
    assert result['attempts'][0]['raw'] == raw
    assert result['presentation']['format'] == 'unrecognized'
    assert not result['presentation']['text']
    assert validate_record(result) == result


def test_json_depth_counter_ignores_brackets_inside_escaped_string():
    body = '[{' * 10000 + '\\"正文' + '}]' * 10000
    raw = json.dumps({'body':body}, ensure_ascii=False)
    assert project(raw, 1)['text'] == body


def test_empty_original_receipt_and_repair_both_remain_in_actual_history(client,reader,demo,profile,monkeypatch):
    receipt,_ = start(client,reader,demo,profile)
    original = MockAdapter.generate; generations = []
    def generated(self,p,prompt,data,limit):
        if prompt.startswith('prose-chapter-'):
            generations.append(1)
            return {'text':'' if len(generations)==1 else '{"body":"修整后的可读正文。"}', 'finishReason':'stop', 'usage':{'state':'known','inputTokens':5,'outputTokens':7}}
        return original(self,p,prompt,data,limit)
    monkeypatch.setattr(MockAdapter,'generate',generated)
    process(receipt['taskId']); chapter = get(client,receipt)['chapters'][0]
    assert chapter['canAccept'] and chapter['text'] == '修整后的可读正文。' and generations == [1,1]
    attempts = chapter['rawPresentation']['attempts']
    assert [a['kind'] for a in attempts] == ['original','repair']
    assert attempts[0]['raw'] == '' and attempts[0]['finishReason'] == 'stop'


def test_new_legacy_journal_without_raw_receipt_cannot_display_previous_raw_or_facts(client,reader,demo,profile,monkeypatch):
    receipt,_ = start(client,reader,demo,profile); process(receipt['taskId'])
    original = get(client,receipt)['chapters'][0]
    assert original['rawPresentation']
    with SessionLocal() as db:
        ctx = stream.context(db,db.get(Task,receipt['taskId']),'generation:prose:v1:c1')
    monkeypatch.setattr(stream,'mirror',lambda *a,**k: (_ for _ in ()).throw(OperationalError('synthetic',{},Exception('db'))))
    stream.persist(ctx,'NEW_READING_ONLY')
    snapshot = get(client,receipt); current = snapshot['chapters'][0]
    assert current['text'] == 'NEW_READING_ONLY' and 'rawPresentation' not in current
    assert current['factDelta'] is None and not current['canAccept']
    assert snapshot['completedChapters'] == 0


def test_live_json_body_is_durable_before_provider_return_and_only_body_enters_events(client, reader, demo, profile, monkeypatch):
    receipt, payload = start(client, reader, demo, profile)
    release, observed = threading.Event(), threading.Event()
    raw = '<think>PRIVATE_CONTENT_THOUGHT</think>{"title":"信","body":"甲🙂'
    def held(self, p, prompt, data, limit, *, on_delta, **kwargs):
        on_delta(raw)
        observed.set()
        assert release.wait(10)
        ending = '乙。"}'
        on_delta(ending)
        return {'text':raw + ending, 'finishReason':'stop', 'usage':{'state':'known','inputTokens':7,'outputTokens':9}}
    monkeypatch.setattr(MockAdapter, 'generate_stream', held)
    thread = threading.Thread(target=process, args=(receipt['taskId'],)); thread.start()
    try:
        assert observed.wait(5)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            chapter = get(client, receipt)['chapters'][0]
            if chapter['text'] == '甲🙂': break
            time.sleep(.01)
        assert thread.is_alive() and chapter['text'] == '甲🙂' and not chapter['canAccept']
        raw_record = chapter['rawPresentation']
        assert raw_record['attempts'][0]['raw'] == raw and raw_record['presentation']['pending']
        validate_record(raw_record, chapter['text'], chapter['title'])
    finally:
        release.set(); thread.join(15)
    assert not thread.is_alive()
    value = get(client, receipt); chapter = value['chapters'][0]
    assert chapter['text'] == '甲🙂乙。' and chapter['canAccept']
    assert chapter['rawPresentation']['attempts'][0]['raw'] == raw + '乙。"}'
    with SessionLocal() as db:
        events = list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == receipt['taskId'])))
        assert all('PRIVATE_CONTENT_THOUGHT' not in json.dumps(e.payload) and '"body"' not in json.dumps(e.payload) for e in events)
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id == receipt['taskId'], UsageRecord.operation.like('generation:prose:%'))) == 1
    response = client.post(BASE + '/generations/' + receipt['generationId'] + '/accept', json=accept_body(value,payload), headers={'Idempotency-Key':'projection-accept'})
    assert response.status_code == 200, response.text


def test_failed_database_mirror_keeps_raw_and_body_and_save_only_does_not_generate(client, reader, demo, profile, monkeypatch):
    receipt, _ = start(client, reader, demo, profile)
    original_mirror = stream.mirror
    calls = []
    raw = '{"title":"片段","body":"已收到🙂正文"}'
    def interrupted(self, p, prompt, data, limit, *, on_delta, **kwargs):
        calls.append(1); on_delta(raw)
        raise Problem('PROVIDER_STATUS_UNKNOWN','合成断流',502)
    monkeypatch.setattr(MockAdapter, 'generate_stream', interrupted)
    monkeypatch.setattr(stream, 'mirror', lambda *a, **k: (_ for _ in ()).throw(OperationalError('synthetic',{},Exception('db'))))
    process(receipt['taskId'])
    first = get(client, receipt)['chapters'][0]
    assert first['text'] == '已收到🙂正文' and first['rawPresentation']['attempts'][0]['raw'] == raw
    assert first['durability'] == 'journaled' and not first['canAccept']
    monkeypatch.setattr(stream, 'mirror', original_mirror)
    save = client.post(BASE + '/generations/' + receipt['generationId'] + '/save', json={}, headers={'Idempotency-Key':'save-projection-only'})
    assert save.status_code == 202, save.text
    process(receipt['taskId'])
    saved = get(client, receipt)['chapters'][0]
    assert saved['text'] == first['text'] and saved['rawPresentation'] == first['rawPresentation']
    assert saved['completeness'] == 'partial' and saved['durability'] == 'saved' and not saved['canAccept'] and calls == [1]
