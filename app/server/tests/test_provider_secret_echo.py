"""Known synthetic credential echoes must stop before any public raw receipt."""
import json
import pytest
from app.provider_secrets import CredentialEchoFilter, reject_echo
from app.errors import Problem
from app.prose.presentation import record, project
from app.prose import stream
from app.domain import canonical
from .test_prose_generations import isolated_prose

KEY = 'SYNTHETIC_PRIVATE_42'  # gitleaks:allow -- synthetic test fixture


def test_literal_prefix_is_held_at_every_boundary_and_never_published_as_a_full_key():
    for boundary in range(1,len(KEY)):
        guard = CredentialEchoFilter(KEY)
        first = guard.add('可读文本。' + KEY[:boundary])
        assert first == '可读文本。'
        with pytest.raises(Problem,match='敏感凭据') as stopped:
            guard.add(KEY[boundary:] + '后续')
        assert stopped.value.code == 'PROVIDER_CREDENTIAL_ECHO' and KEY not in str(stopped.value)


def test_a_nonsecret_shared_prefix_is_released_without_changing_received_bytes():
    guard = CredentialEchoFilter(KEY)
    fragments = ['故事','SYNTHETIC_','PRIVATE_','4','结尾','SYN']
    visible = ''.join(guard.add(chunk) for chunk in fragments) + guard.finish()
    assert visible == ''.join(fragments)


@pytest.mark.parametrize('representation',[KEY, ''.join('\\u%04x'%ord(char) for char in KEY),KEY[:4]+''.join('\\u%04x'%ord(char) for char in KEY[4:])])
def test_json_unicode_and_mixed_echo_is_stopped_before_publication(representation):
    raw = '{"body":"' + representation + '"}'
    guard = CredentialEchoFilter(KEY)
    visible = []
    with pytest.raises(Problem):
        for char in raw:
            visible.append(guard.add(char))
    assert KEY not in project(''.join(visible),1)['text']
    with pytest.raises(Problem): reject_echo(raw,KEY)


def test_raw_stream_secondary_guard_rejects_before_reading_or_writing_any_journal(monkeypatch):
    def forbidden(*args,**kwargs): raise AssertionError('guard must precede journal access')
    monkeypatch.setattr(stream,'read',forbidden)
    monkeypatch.setattr(stream,'persist',forbidden)
    with pytest.raises(Problem): stream.persist_raw({'credentialGuard':KEY,'chapterIndex':1},json.dumps({'body':KEY}))


@pytest.mark.parametrize('key',['OWN/SECRET','OWN"SECRET','OWN\\SECRET','OWN\nSECRET','OWN\tSECRET','OWN🙂SECRET'])
def test_all_json_escapes_hold_credential_prefix_and_stop_before_a_complete_echo(key):
    raw = json.dumps({'body':key},ensure_ascii=True).replace('/',r'\/')
    guard = CredentialEchoFilter(key); visible = []
    with pytest.raises(Problem):
        for char in raw:
            visible.append(guard.add(char))
    projected = project(''.join(visible),1)['text']
    assert key not in projected and 'OWN' not in projected
    with pytest.raises(Problem): reject_echo(raw,key)


@pytest.mark.parametrize('title',['\\ud800','\\udfff','\\u0000',123])
def test_invalid_title_does_not_lose_readable_body_or_original_utf8_bytes(title):
    raw = '{"title":' + (str(title) if type(title) is int else '"'+title+'"') + ',"body":"合法正文🙂"}'
    saved = record(raw,3,finish_reason='stop')
    assert saved['presentation']['text'] == '合法正文🙂' and saved['presentation']['title'] == '第 3 章'
    assert saved['attempts'][0]['raw'] == raw
    canonical(saved).encode('utf-8')
