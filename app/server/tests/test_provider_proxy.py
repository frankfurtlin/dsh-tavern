import asyncio
from types import SimpleNamespace
import httpx
import pytest
from app.config import settings
from app.errors import Problem
from app.provider_network import ProxyStream, AsyncProxyStream, proxy_options
from app.scenes.provider import public_address, HOST
from app.provider_transport import send_connected


def test_proxy_tls_verifies_approved_hostname_without_changing_context():
    calls=[];context=object()
    raw=SimpleNamespace(start_tls=lambda *args:calls.append(args) or 'secured')
    assert ProxyStream(raw,'approved.example').start_tls(context,'93.184.216.34',9)=='secured'
    assert calls==[(context,'approved.example',9)]


def test_async_proxy_tls_verifies_approved_hostname():
    calls=[]
    async def start(*args):calls.append(args);return 'secured'
    assert asyncio.run(AsyncProxyStream(SimpleNamespace(start_tls=start),'approved.example').start_tls('context','93.184.216.34',9))=='secured'
    assert calls==[('context','approved.example',9)]


def test_no_proxy_retains_direct_transport(monkeypatch):
    monkeypatch.setattr(settings,'provider_http_proxy','')
    assert proxy_options('approved.example')=={}


def test_private_vpn_dns_and_failed_resolvers_are_definitely_unsent(monkeypatch):
    monkeypatch.setattr(settings,'provider_allowed_hosts',HOST)
    monkeypatch.setattr('app.scenes.provider.socket.getaddrinfo',lambda *a,**k:[(0,0,0,0,('198.18.0.7',443))])
    factory=httpx.Client;calls=[]
    def handle(request):calls.append(request);raise httpx.ConnectError('offline')
    monkeypatch.setattr(httpx,'Client',lambda **kw:factory(transport=httpx.MockTransport(handle),**kw))
    with pytest.raises(Problem) as failed:public_address()
    assert failed.value.code=='SCENE_DNS_FAILED' and failed.value.details['requestSent'] is False
    assert len(calls)==3 and all(r.method=='GET' for r in calls)


def test_connect_tunnel_headers_do_not_mark_model_post_as_submitted():
    class Client:
        def build_request(self,*args,**kwargs):return httpx.Request(*args,**kwargs)
        async def send(self,request,**kwargs):
            await request.extensions['trace']('http11.send_request_headers.started',{'request':SimpleNamespace(method=b'CONNECT')})
            raise httpx.ConnectError('TLS failed before POST')
    with pytest.raises(Problem) as failed:
        asyncio.run(send_connected(Client(),httpx.URL('https://approved.example/v1'),'approved.example',['93.184.216.34'],{}, {},stream=True,cancel_check=None,started=__import__('time').monotonic()))
    assert failed.value.code=='PROVIDER_CONNECTION_FAILED' and failed.value.details['requestSent'] is False
