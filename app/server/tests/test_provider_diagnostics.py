"""Failure causes stay useful and private without repeating uncertain calls."""
import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.errors import Problem
from app.models import Task, UsageRecord
from app.providers import ChatCompletionsAdapter, MockAdapter, encrypt_key
from app.worker import process
from .helpers import BASE
from .test_regressions import queue_operation

PRIVATE = 'PRIVATE-ERROR-MESSAGE-HEADER-AND-RESPONSE-FIXTURE'


@pytest.mark.parametrize('failure,reason,exception,status', [
    ('connect', 'connection_failed', 'ConnectError', None),
    ('timeout', 'timeout', 'ReadTimeout', None),
    ('malformed', 'invalid_response', 'JSONDecodeError', 200),
    ('http', 'http_error', None, 429),
])
def test_provider_failure_metadata_distinguishes_transport_and_response_without_secrets(
        monkeypatch, failure, reason, exception, status):
    requests = []
    def respond(request):
        requests.append(request)
        if failure == 'connect':
            raise httpx.ConnectError(PRIVATE, request=request)
        if failure == 'timeout':
            raise httpx.ReadTimeout(PRIVATE, request=request)
        return httpx.Response(status, text=PRIVATE)
    factory = httpx.AsyncClient
    monkeypatch.setattr('app.providers.validate_url', lambda _: ('approved.example', '93.184.216.34'))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs))
    profile = SimpleNamespace(base_url='https://approved.example/v1', key_cipher=encrypt_key(PRIVATE),
        model_id='fixture', output_parameter='max_tokens', supports_json=False)
    with pytest.raises(Problem) as caught:
        ChatCompletionsAdapter().generate(profile, 'fixture', {}, 32)
    error = caught.value
    assert error.code == ('PROVIDER_FAILED' if failure == 'http' else 'PROVIDER_CONNECTION_FAILED' if failure == 'connect' else 'PROVIDER_STATUS_UNKNOWN')
    assert error.details['reason'] == reason and error.details['httpStatus'] == status
    assert error.details.get('exceptionType') == exception
    assert type(error.details['latencyMs']) is int and error.details['latencyMs'] >= 0
    assert PRIVATE not in error.message+json.dumps(error.details)
    assert len(requests) == (3 if failure == 'connect' else 1)


def test_worker_persists_safe_stage_diagnostic_and_does_not_repeat_unknown_call(
        client, reader, profile, monkeypatch, caplog):
    task_id = queue_operation(client, profile, {'operation': 'connection_test', 'profileId': profile['id']})
    calls = []
    def fail(self, *args):
        calls.append(1)
        raise Problem('PROVIDER_STATUS_UNKNOWN', PRIVATE, 502, {
            'reason': 'transport_error', 'exceptionType': 'ReadError', 'latencyMs': 3100,
            'httpStatus': None, 'responseBody': PRIVATE, 'requestHeaders': {'Authorization': PRIVATE}})
    monkeypatch.setattr(MockAdapter, 'generate', fail)
    with caplog.at_level(logging.WARNING, logger='worker'):
        process(task_id)
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        diagnostic = task.checkpoint['providerFailure']
        assert diagnostic == {'stage': 'connection_test', 'errorCode': 'PROVIDER_STATUS_UNKNOWN',
            'reason': 'transport_error', 'exceptionType': 'ReadError', 'latencyMs': 3100}
        assert PRIVATE not in json.dumps(diagnostic)
        assert task.provider_state == 'unknown' and task.state == 'failed'
        assert db.scalar(select(UsageRecord).where(UsageRecord.task_id == task_id)).status == 'intent'
    messages = '\n'.join(record.getMessage() for record in caplog.records)
    assert 'exception_type=ReadError' in messages and 'stage=connection_test' in messages
    assert PRIVATE not in messages
    assert 'providerFailure' not in client.get(BASE+'/tasks/'+task_id).json()
    process(task_id)
    assert len(calls) == 1
