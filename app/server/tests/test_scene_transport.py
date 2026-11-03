import base64
import httpx
import pytest
from app.errors import Problem
from app.providers import encrypt_key
from app.scenes.provider import generate


def transport(monkeypatch, handler):
    factory=httpx.Client
    monkeypatch.setattr('app.scenes.provider.public_address',lambda:'93.184.216.34')
    monkeypatch.setattr('app.scenes.provider.alternate_public_address',lambda address:'93.184.216.35' if address=='93.184.216.34' else '93.184.216.34')
    monkeypatch.setattr(httpx,'Client',lambda **kw:factory(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr('app.scenes.provider.encode_panorama',lambda data,**kwargs:(data,{'width':2,'height':1}))


def descriptor():return {'description':'露天石台','pointLabel':'石台','placeName':'后山','lighting':'清晨'}


@pytest.mark.parametrize('existing_image',[False,True])
def test_new_environment_never_sends_a_layout_as_finished_artwork(monkeypatch,existing_image):
    import io,json
    from PIL import Image
    requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(b'image').decode()}]})
    transport(monkeypatch,handle)
    def no_guide(*args,**kwargs):raise AssertionError('New environments must not inherit blockout pixels')
    monkeypatch.setattr('app.scenes.provider.character_layout',no_guide)
    actual=io.BytesIO();Image.new('RGB',(4,2),'#947763').save(actual,format='PNG')
    value={**descriptor(),'renderStage':'environment','environmentGeometryVersion':6,
        'spaceKind':'outdoor','exitRoutes':[{'id':'return','yaw':0,'visualCue':'Open stone doorway.'}]}
    generate(value,encrypt_key('ISOLATED-TEST-CREDENTIAL'),actual.getvalue() if existing_image else None)
    body=json.loads(requests[0].content)
    assert len(requests)==1 and 'FULL 360 degrees' in body['prompt']
    assert '"longitudeFraction": 0.5' in body['prompt']
    if existing_image:
        assert 'supplied ACTUAL panorama' in body['prompt']
        assert len(body['extra_body']['image'])==1 and 'return_base64' not in body
        assert base64.b64decode(body['extra_body']['image'][0].split(',',1)[1])==actual.getvalue()
    else:
        assert 'No layout drawing or blockout image is supplied' in body['prompt']
        assert body['return_base64'] is True and 'extra_body' not in body


def test_residence_generation_requires_the_same_actual_interior_as_inspection():
    from app.scenes.layout import route_visual_cues
    from app.scenes.provider import prompt_for
    from app.scenes.passages import cue_requirements
    exits=[{'id':'house','name':'贝克街寓所'},{'id':'market','name':'市场'}]
    cue=route_visual_cues(exits,'open-air street or market')['house']
    value={**descriptor(),'renderStage':'environment','environmentGeometryVersion':7,
           'exitRoutes':[{'id':'house','yaw':0,'visualCue':cue}]}
    actual_prompt=prompt_for(value)
    assert 'or continuing residential lane' not in actual_prompt
    assert 'OPEN DOORWAY INTO THE HOUSE' in actual_prompt
    assert 'open-residential-interior' in cue_requirements(cue)
    # Older actual receipts retain their original contract; the new fact is
    # not retrospectively invented for an older image.
    assert 'open-residential-interior' not in cue_requirements('Open townhouse threshold with exterior stone steps.')


@pytest.mark.parametrize('failure',[httpx.ConnectError,httpx.ConnectTimeout])
def test_image_connect_can_recover_only_before_http_headers(monkeypatch,failure):
    attempts=[]
    def handle(request):
        attempts.append(request)
        if len(attempts)==1:raise failure('confirmed not sent',request=request)
        request.extensions['trace']('http11.send_request_headers.started',{})
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(b'image').decode()}]})
    transport(monkeypatch,handle)
    data,metadata,_=generate(descriptor(),encrypt_key('ISOLATED-TEST-CREDENTIAL'))
    assert data==b'image' and len(attempts)==2
    assert attempts[0].url.host=='93.184.216.34' and attempts[1].url.host=='93.184.216.35'
    assert all(r.extensions['sni_hostname']=='apihub.agnes-ai.com' for r in attempts)


def test_image_reconnection_stays_finite_and_retains_known_unsent_diagnostic(monkeypatch):
    attempts=[]
    def handle(request):
        attempts.append(request)
        raise httpx.ConnectError('Confirmed not sent',request=request)
    transport(monkeypatch,handle)
    with pytest.raises(Problem) as failed:generate(descriptor(),encrypt_key('ISOLATED-TEST-CREDENTIAL'))
    assert len(attempts)==3 and failed.value.code=='SCENE_CONNECTION_FAILED'
    assert failed.value.details=={'reason':'ConnectError','requestSent':False,'responseReceived':False}


def test_alternate_image_address_never_accepts_synthetic_or_private_dns(monkeypatch):
    from app.scenes.provider import alternate_public_address,HOST
    monkeypatch.setattr('app.scenes.provider.settings.provider_allowed_hosts',HOST)
    monkeypatch.setattr('app.scenes.provider.socket.getaddrinfo',lambda *a,**kw:[(0,0,0,'',('198.18.0.70',443))])
    monkeypatch.setattr('app.scenes.provider.resolve_public',lambda *_:['127.0.0.1','104.18.19.62'])
    assert alternate_public_address('104.18.18.62')=='104.18.19.62'
    monkeypatch.setattr('app.scenes.provider.resolve_public',lambda *_:['10.0.0.1'])
    assert alternate_public_address('104.18.18.62')=='104.18.18.62'


def test_image_post_with_unknown_response_is_never_replayed(monkeypatch):
    attempts=[]
    def handle(request):
        attempts.append(request);request.extensions['trace']('http11.send_request_headers.started',{})
        raise httpx.ReadTimeout('no receipt',request=request)
    transport(monkeypatch,handle)
    with pytest.raises(Problem) as failed:generate(descriptor(),encrypt_key('ISOLATED-TEST-CREDENTIAL'))
    assert failed.value.code=='SCENE_STATUS_UNKNOWN' and len(attempts)==1
    assert failed.value.details=={'reason':'ReadTimeout','requestSent':True,'responseReceived':False}


def test_received_invalid_image_reply_is_definite_failure_not_unknown_delivery(monkeypatch):
    def handle(request):return httpx.Response(200,content=b'not-json')
    transport(monkeypatch,handle)
    with pytest.raises(Problem) as failed:generate(descriptor(),encrypt_key('ISOLATED-TEST-CREDENTIAL'))
    assert failed.value.code=='SCENE_IMAGE_INVALID' and failed.value.details['responseReceived']


@pytest.mark.parametrize('failure',[httpx.ConnectError,httpx.ConnectTimeout])
def test_known_unsent_retry_rechecks_authorization_before_another_post(monkeypatch,failure):
    attempts=[];authorizations=[]
    def handle(request):
        attempts.append(request)
        raise failure('Confirmed not sent; story changed during connection wait',request=request)
    def authorize():
        authorizations.append(True)
        if attempts:raise Problem('SCENE_CONTEXT_CHANGED','Story changed before POST',409)
    transport(monkeypatch,handle)
    with pytest.raises(Problem) as failed:
        generate(descriptor(),encrypt_key('ISOLATED-TEST-CREDENTIAL'),authorize=authorize)
    assert failed.value.code=='SCENE_CONTEXT_CHANGED'
    assert len(attempts)==1 and len(authorizations)>=2
