"""Connection recovery never repeats a POST that could have reached a model."""
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.errors import Problem
from app.models import Task, UsageRecord, ModelProfile, Consent
from app.db import SessionLocal
from app.providers import ChatCompletionsAdapter, MockAdapter, encrypt_key, validate_url
from app.worker import process
from .test_regressions import queue_operation


PRIVATE = 'TEST-ONLY-PRIVATE-CREDENTIAL'


def setup_transport(monkeypatch, respond):
    requests = []
    async def handle(request):
        requests.append(request)
        return await respond(request, len(requests))
    factory = httpx.AsyncClient
    monkeypatch.setattr('app.providers.validate_url', lambda _: ('approved.example', ('93.184.216.34', '93.184.216.35', '93.184.216.36')))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: factory(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr('app.provider_transport.CONNECT_BACKOFF_SECONDS', 0)
    profile = SimpleNamespace(base_url='https://approved.example/v1', key_cipher=encrypt_key(PRIVATE), model_id='fixture', output_parameter='max_tokens', supports_json=False)
    return profile, requests


def success():
    return httpx.Response(200, json={'choices': [{'message': {'content': '连接正常'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 7, 'completion_tokens': 2}})


def test_bounded_identity_references_follow_nine_scene_views(monkeypatch):
    async def respond(request, attempt):return success()
    profile, requests=setup_transport(monkeypatch,respond)
    pictures=['data:image/jpeg;base64,AAAA']*11
    ChatCompletionsAdapter().generate(profile,'Inspect',{'_sceneImages':pictures},100)
    assert len(json.loads(requests[0].content)['messages'][1]['content'])==12
    with pytest.raises(Problem) as error:
        ChatCompletionsAdapter().generate(profile,'Inspect',{'_sceneImages':pictures+[pictures[0]]},100)
    assert error.value.code=='SCENE_IMAGE_INVALID' and len(requests)==1


def test_agnes_visual_measurement_uses_complete_json_without_replaying_a_stream(monkeypatch):
    async def respond(request, attempt):
        body=json.loads(request.content)
        assert 'stream' not in body
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"characters":[]}'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':30,'completion_tokens':9}})
    profile,requests=setup_transport(monkeypatch,respond)
    profile.model_id='agnes-2.5-flash'
    monkeypatch.setattr('app.providers.validate_url',lambda _:('apihub.agnes-ai.com',('93.184.216.34',)))
    result=ChatCompletionsAdapter().generate(profile,'Inspect',{'_sceneImages':['data:image/jpeg;base64,AAAA'],
        '_structured':True,'_visionStream':True},100)
    assert json.loads(result['text'])=={'characters':[]} and result['finishReason']=='stop'
    assert result['usage']['outputTokens']==9 and len(requests)==1


def test_connect_failure_recovers_using_another_validated_address_before_http_submission(monkeypatch):
    async def respond(request, attempt):
        if attempt == 1:
            raise httpx.ConnectError(PRIVATE, request=request)
        return success()
    profile, requests = setup_transport(monkeypatch, respond)
    result = ChatCompletionsAdapter().generate(profile, 'fixture', {}, 32)
    assert result['text'] == '连接正常' and result['connectionAttempts'] == 2
    assert [r.url.host for r in requests] == ['93.184.216.34', '93.184.216.35']
    assert all(r.headers['Host'] == r.extensions['sni_hostname'] == 'approved.example' for r in requests)
    assert requests[0].content == requests[1].content


def test_fourth_validated_address_is_attempted_when_first_three_cannot_connect(monkeypatch):
    async def respond(request, attempt):
        if attempt < 4:raise httpx.ConnectError(PRIVATE, request=request)
        return success()
    profile, requests = setup_transport(monkeypatch, respond)
    monkeypatch.setattr('app.providers.validate_url', lambda _: ('approved.example', tuple('93.184.216.'+str(i) for i in range(34,38))))
    result = ChatCompletionsAdapter().generate(profile, 'fixture', {}, 32)
    assert result['connectionAttempts']==4 and requests[-1].url.host=='93.184.216.37'


def test_structured_json_uses_low_variance_without_constraining_narrative(monkeypatch):
    async def respond(request, attempt):return success()
    profile, requests=setup_transport(monkeypatch,respond);profile.supports_json=True
    ChatCompletionsAdapter().generate(profile,'JSON',{'_structured':True},32)
    body=json.loads(requests[0].content)
    assert body['temperature']==.2 and body['response_format']=={'type':'json_object'}
    ChatCompletionsAdapter().generate(profile,'narrative',{},32)
    assert 'temperature' not in json.loads(requests[1].content)


@pytest.mark.parametrize('exception', [httpx.ConnectError, httpx.ConnectTimeout])
def test_failed_connects_are_bounded_and_report_confirmed_not_sent(monkeypatch, exception):
    async def respond(request, attempt):
        raise exception(PRIVATE, request=request)
    profile, requests = setup_transport(monkeypatch, respond)
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate(profile, 'fixture', {}, 32)
    assert failed.value.code == 'PROVIDER_CONNECTION_FAILED'
    assert failed.value.details['requestSent'] is False
    assert failed.value.details['connectionAttempts'] == len(requests) == 3
    assert PRIVATE not in json.dumps(failed.value.details)


@pytest.mark.parametrize('exception', [httpx.ReadError, httpx.ReadTimeout, httpx.WriteError, httpx.WriteTimeout, httpx.RemoteProtocolError])
def test_any_potentially_submitted_request_is_never_repeated(monkeypatch, exception):
    async def respond(request, attempt):
        raise exception(PRIVATE, request=request)
    profile, requests = setup_transport(monkeypatch, respond)
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate(profile, 'fixture', {}, 32)
    assert failed.value.code == 'PROVIDER_STATUS_UNKNOWN' and len(requests) == 1


def test_even_a_misclassified_connect_error_cannot_retry_after_http_write_starts(monkeypatch):
    async def respond(request, attempt):
        await request.extensions['trace']('http11.send_request_headers.started', {})
        raise httpx.ConnectError(PRIVATE, request=request)
    profile, requests = setup_transport(monkeypatch, respond)
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate(profile, 'fixture', {}, 32)
    assert failed.value.code == 'PROVIDER_STATUS_UNKNOWN' and len(requests) == 1


def test_stream_connection_recovery_keeps_one_reply_and_never_retries_a_broken_reply(monkeypatch):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield ('data: '+json.dumps({'choices': [{'delta': {'content': '已经收到的正文。'}}]}, ensure_ascii=False)+'\n\n').encode()
            raise httpx.ReadError(PRIVATE)
    async def respond(request, attempt):
        if attempt == 1:
            raise httpx.ConnectError(PRIVATE, request=request)
        return httpx.Response(200, stream=Stream(), headers={'Content-Type': 'text/event-stream'})
    profile, requests = setup_transport(monkeypatch, respond)
    deltas = []
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate_stream(profile, 'fixture', {}, 32, on_delta=deltas.append, cancel_check=lambda: None)
    assert failed.value.code == 'PROVIDER_STATUS_UNKNOWN' and len(requests) == 2
    assert ''.join(deltas) == '已经收到的正文。'


def test_internal_vision_stream_waits_for_complete_json_and_usage(monkeypatch):
    import asyncio
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            # First evidence arrives after the ordinary whole-response limit.
            await asyncio.sleep(.05)
            for payload in (
                {'choices':[{'delta':{'reasoning_content':'private reasoning'}}]},
                {'choices':[{'delta':{'content':'{"characters":'}}]},
                {'choices':[{'delta':{'content':'[]}'},'finish_reason':'stop'}]},
                {'choices':[],'usage':{'prompt_tokens':80,'completion_tokens':9}},
            ):
                yield ('data: '+json.dumps(payload)+'\n\n').encode()
            yield b'data: [DONE]\n\n'
    async def respond(request, attempt):return httpx.Response(200,stream=Stream())
    profile, requests=setup_transport(monkeypatch,respond);profile.supports_json=True
    monkeypatch.setattr('app.providers.REQUEST_TOTAL_TIMEOUT_SECONDS',.025)
    monkeypatch.setattr('app.providers.STREAM_TOTAL_TIMEOUT_SECONDS',1)
    result=ChatCompletionsAdapter().generate(profile,'Inspect',{
        '_sceneImages':['data:image/jpeg;base64,AAAA'],'_structured':True,'_visionStream':True},100)
    assert json.loads(result['text'])=={'characters':[]}
    assert result['finishReason']=='stop' and result['usage']['outputTokens']==9
    assert len(requests)==1
    body=json.loads(requests[0].content)
    assert body['stream'] is True and body['stream_options']=={'include_usage':True}
    assert '_visionStream' not in body['messages'][1]['content'][0]['text']
    assert body['response_format']=={'type':'json_object'}


def test_incomplete_internal_vision_stream_never_returns_or_repeats_partial_anchors(monkeypatch):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"{\\\"characters\\\":[]}"}}]}\n\n'
            raise httpx.ReadError(PRIVATE)
    async def respond(request, attempt):return httpx.Response(200,stream=Stream())
    profile, requests=setup_transport(monkeypatch,respond)
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate(profile,'Inspect',{
            '_sceneImages':['data:image/jpeg;base64,AAAA'],'_structured':True,'_visionStream':True},100)
    assert failed.value.code=='PROVIDER_STATUS_UNKNOWN' and len(requests)==1
    stats=failed.value.details['streamDiagnostic']
    assert stats['frames']==1 and stats['contentCharacters']==17 and stats['doneSeen'] is False
    assert PRIVATE not in json.dumps(stats)


def test_internal_vision_stream_total_deadline_remains_finite(monkeypatch):
    import asyncio
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(.01)
                yield b': heartbeat\n\n'
    async def respond(request, attempt):return httpx.Response(200,stream=Stream())
    profile, requests=setup_transport(monkeypatch,respond)
    monkeypatch.setattr('app.providers.STREAM_TOTAL_TIMEOUT_SECONDS',.06)
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate(profile,'Inspect',{
            '_sceneImages':['data:image/jpeg;base64,AAAA'],'_structured':True,'_visionStream':True},100)
    assert failed.value.code=='PROVIDER_STATUS_UNKNOWN' and failed.value.details['reason']=='timeout'
    assert len(requests)==1
    assert failed.value.details['streamDiagnostic']['frames']==0
    assert failed.value.details['streamDiagnostic']['lines']>0


def test_mimo_internal_plan_stream_waits_for_complete_json_without_exposing_reasoning(monkeypatch):
    import asyncio
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(.05)
            for value in (
                {'choices':[{'delta':{'reasoning_content':'PRIVATE PLANNING'}}]},
                {'choices':[{'delta':{'content':'{"location":'}}]},
                {'choices':[{'delta':{'content':'"street"}'},'finish_reason':'stop'}]},
                {'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':8}},
            ):yield ('data: '+json.dumps(value)+'\n\n').encode()
            yield b'data: [DONE]\n\n'
    async def respond(request,attempt):return httpx.Response(200,stream=Stream())
    profile,requests=setup_transport(monkeypatch,respond)
    profile.base_url='https://opencode.ai/zen/go/v1';profile.model_id='mimo-v2.6-flash'
    profile.supports_json=True
    monkeypatch.setattr('app.providers.REQUEST_TOTAL_TIMEOUT_SECONDS',.025)
    monkeypatch.setattr('app.providers.STREAM_TOTAL_TIMEOUT_SECONDS',1)
    result=ChatCompletionsAdapter().generate(profile,'Plan JSON',{'_structured':True,'action':'walk'},4096)
    assert json.loads(result['text'])=={'location':'street'}
    assert result['finishReason']=='stop' and result['usage']['outputTokens']==8
    assert len(requests)==1 and json.loads(requests[0].content)['stream'] is True
    assert 'PRIVATE PLANNING' not in result['text']


@pytest.mark.parametrize('end',['disconnect','heartbeats'])
def test_mimo_incomplete_internal_plan_never_returns_partial_state_or_repeats(monkeypatch,end):
    import asyncio
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"{\\\"location\\\":\\\"street\\\"}"}}]}\n\n'
            if end=='disconnect':raise httpx.ReadError(PRIVATE)
            while True:
                await asyncio.sleep(.01)
                yield b': heartbeat\n\n'
    async def respond(request,attempt):return httpx.Response(200,stream=Stream())
    profile,requests=setup_transport(monkeypatch,respond)
    profile.base_url='https://opencode.ai/zen/go/v1';profile.model_id='mimo-v2.6-flash'
    monkeypatch.setattr('app.providers.STREAM_TOTAL_TIMEOUT_SECONDS',.06)
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate(profile,'Plan JSON',{'_structured':True},4096)
    assert failed.value.code=='PROVIDER_STATUS_UNKNOWN' and len(requests)==1
    assert failed.value.details['streamDiagnostic']['doneSeen'] is False
    assert PRIVATE not in json.dumps(failed.value.details)


def test_cancellation_during_connect_recovery_stops_before_another_attempt(monkeypatch):
    async def respond(request, attempt):
        raise httpx.ConnectError(PRIVATE, request=request)
    profile, requests = setup_transport(monkeypatch, respond)
    def cancel():
        if requests:
            raise Problem('TASK_CANCELLED', 'stopped')
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate_stream(profile, 'fixture', {}, 32, on_delta=lambda _: None, cancel_check=cancel)
    assert failed.value.code == 'TASK_CANCELLED' and len(requests) == 1


def test_alternative_addresses_still_reject_any_private_dns_result(monkeypatch):
    monkeypatch.setattr('app.providers.settings.provider_allowed_hosts', 'approved.example')
    monkeypatch.setattr('app.providers.socket.getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443)), (2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(Problem) as failed:
        validate_url('https://approved.example/v1')
    assert failed.value.code == 'PROVIDER_HOST_REJECTED'


def test_confirmed_connect_failure_releases_reserved_budget_and_cannot_be_resent_on_recovery(client, reader, profile, monkeypatch):
    task_id = queue_operation(client, profile, {'operation': 'connection_test', 'profileId': profile['id']})
    calls = []
    def fail(self, *args):
        calls.append(1)
        raise Problem('PROVIDER_CONNECTION_FAILED', PRIVATE, 502, {'reason': 'connection_failed', 'exceptionType': 'ConnectError', 'connectionAttempts': 3, 'latencyMs': 5, 'requestSent': False})
    monkeypatch.setattr(MockAdapter, 'generate', fail)
    process(task_id)
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task.state == 'failed' and task.provider_state == 'not_sent'
        assert task.checkpoint['providerFailure']['requestSent'] is False
        usage = db.scalar(select(UsageRecord).where(UsageRecord.task_id == task_id))
        consent = db.get(Consent, usage.consent_id)
        model = db.get(ModelProfile, profile['id'])
        assert (usage.status, usage.input_tokens, usage.output_tokens) == ('not_sent', 0, 0)
        assert (model.calls_reserved, model.input_reserved, model.output_reserved) == (0, 0, 0)
        assert (consent.calls_reserved, consent.input_reserved, consent.output_reserved) == (0, 0, 0)
    process(task_id)
    assert len(calls) == 1
