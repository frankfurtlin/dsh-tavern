"""Real TLS/socket contract tests against a local, explicitly trusted test server.

Only the test URL validation is replaced to permit its loopback server. Production
SSRF restrictions remain covered separately. TLS verification is never disabled.
"""
import json
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.errors import Problem
from app.providers import ChatCompletionsAdapter, encrypt_key


@pytest.mark.parametrize('status',[200,'recover-connect',401,429,302])
def test_F24_real_https_errors_do_not_retry_redirect_or_fallback(tmp_path,monkeypatch,status):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'approved.test')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
          .public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('approved.test')]),critical=False)
          .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
          .sign(key,hashes.SHA256()))
    cert_path=tmp_path/'test-ca.pem';key_path=tmp_path/'test-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    recover = status == 'recover-connect'
    if recover: status = 200
    requests=[];sni=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'path':self.path,'host':self.headers['Host'],'body':body})
            payload=(b'{"choices":[{"message":{"content":"test-only reply"},"finish_reason":"stop"}],"usage":{"prompt_tokens":7,"completion_tokens":2}}' if status==200 else b'{"error":{"message":"isolated fixture rejection"}}')
            self.send_response(status)
            self.send_header('Location','https://unapproved.test/v1/chat/completions')
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert_path,key_path)
    tls.set_servername_callback(lambda socket,hostname,context:sni.append(hostname))
    server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client_tls=ssl.create_default_context(cafile=str(cert_path))
    factory=httpx.AsyncClient
    monkeypatch.setattr('app.providers.validate_url',lambda url:('approved.test',('127.0.0.2','127.0.0.1') if recover else '127.0.0.1'))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:factory(verify=client_tls,**kwargs))
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:1')
    profile=SimpleNamespace(base_url=f'https://approved.test:{server.server_port}/v1',key_cipher=encrypt_key('TEST-ONLY-NOT-REAL'),model_id='test-socket',output_parameter='max_completion_tokens',supports_json=False)
    try:
        if status==200:
            result=ChatCompletionsAdapter().generate(profile,'connection-v1',{'message':'fixture'},32)
            assert result['text']=='test-only reply'
            assert result['usage']=={'state':'known','inputTokens':7,'outputTokens':2}
            assert result['connectionAttempts']==(2 if recover else 1)
        else:
            with pytest.raises(Problem) as failure:ChatCompletionsAdapter().generate(profile,'connection-v1',{'message':'fixture'},32)
            assert failure.value.code=='PROVIDER_FAILED'
        assert len(requests)==1 and sni==['approved.test']
        assert requests[0]['path']=='/v1/chat/completions' and requests[0]['host']=='approved.test'
        assert requests[0]['body']['max_completion_tokens']==32
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
