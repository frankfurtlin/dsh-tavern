"""Explicit administrator proxy with pinned destinations and verified TLS SNI."""
import httpx
import ipaddress
from .config import settings


class ProxyStream:
    def __init__(self, stream, hostname):
        self.stream, self.hostname = stream, hostname

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        # HTTP CONNECT remains addressed to the validated public IP. httpcore's
        # proxy transport ignores the request SNI extension; restore the approved
        # certificate hostname here without weakening certificate verification.
        return self.stream.start_tls(ssl_context, self.hostname, timeout)


class AsyncProxyStream(ProxyStream):
    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return await self.stream.start_tls(ssl_context, self.hostname, timeout)


class ProxyBackend:
    def __init__(self, backend, hostname):
        self.backend, self.hostname = backend, hostname

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def connect_tcp(self, *args, **kwargs):
        return ProxyStream(self.backend.connect_tcp(*args, **kwargs), self.hostname)


class AsyncProxyBackend(ProxyBackend):
    async def connect_tcp(self, *args, **kwargs):
        return AsyncProxyStream(await self.backend.connect_tcp(*args, **kwargs), self.hostname)


def proxy_options(hostname=None, *, asynchronous=False):
    proxy = settings.provider_http_proxy.strip()
    if not proxy:
        return {}
    if hostname is None:
        return {'proxy': proxy}
    # httpx 0.28 / httpcore 1.x expose no proxy tunnel SNI override. This bounded
    # adapter changes only the network stream's TLS name, retaining IP pinning.
    transport = (httpx.AsyncHTTPTransport if asynchronous else httpx.HTTPTransport)(proxy=proxy, trust_env=False)
    backend = transport._pool._network_backend
    transport._pool._network_backend = (AsyncProxyBackend if asynchronous else ProxyBackend)(backend, hostname)
    return {'transport': transport}


def resolve_public(hostname):
    """Fallback for VPN synthetic DNS; hostname has already passed allowlisting."""
    for resolver, tls_name in (('https://223.5.5.5/resolve','dns.alidns.com'),('https://dns.google/resolve',None),('https://cloudflare-dns.com/dns-query',None)):
        try:
            with httpx.Client(timeout=5,trust_env=False,follow_redirects=False,**proxy_options(tls_name)) as client:
                request=client.build_request('GET',resolver,params={'name':hostname,'type':'A'},headers={'Accept':'application/dns-json',**({'Host':tls_name} if tls_name else {})})
                if tls_name:request.extensions['sni_hostname']=tls_name
                response=client.send(request)
            response.raise_for_status()
            addresses=tuple(sorted({r['data'] for r in response.json().get('Answer',[]) if r.get('type')==1}))
            if addresses and all(ipaddress.ip_address(ip).is_global for ip in addresses):return addresses
        except (httpx.HTTPError,ValueError,KeyError,TypeError):
            continue
    return ()
