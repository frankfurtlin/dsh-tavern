import json
import asyncio
import httpx
from app.provider_rejection import category


def test_rejection_diagnostic_returns_fixed_categories_never_supplier_text_or_keys():
    for message,expected in [('max_tokens is invalid','output_configuration'),
        ('thinking budget invalid','thinking_configuration'),('response_format not supported','structured_output'),
        ('image input rejected','image_input'),('context length exceeded','context_limit'),
        ('insufficient balance','quota'),('rate limit reached','rate_limit'),
        ('model not found','model_unavailable'),('arbitrary secret=EXAMPLE-SECRET','unspecified')]:
        result=category(json.dumps({'error':{'message':message,'key':'EXAMPLE-SECRET'}}))
        assert result==expected and 'SECRET' not in result
    assert category('not json')=='unspecified' and category('[]')=='unspecified'


def test_measured_model_coming_soon_and_nested_gateway_errors_are_classified():
    measured={'error':{'param':'model','type':'invalid_request_error',
        'message':'Upstream request failed: [model_coming_soon] This model is coming soon.'}}
    assert category(json.dumps(measured))=='model_unavailable'
    assert category(json.dumps({'error':{'name':'APICallError','data':measured}}))=='model_unavailable'


def test_bounded_response_diagnostics_keep_only_fixed_codes_and_safe_request_id():
    from app.provider_rejection import diagnostics
    secret='EXAMPLE-PRIVATE-KEY-DO-NOT-RETAIN'
    response=httpx.Response(400,json={'error':{'message':'[model_coming_soon] '+secret}},
        headers={'x-request-id':'req-123:4'})
    result=asyncio.run(diagnostics(response,credential=secret))
    assert result=={'rejectionCategory':'model_unavailable','responseReceived':True,
        'rejectionCode':'model_coming_soon','upstreamRequestId':'req-123:4'}
    assert secret not in json.dumps(result)
    bad=httpx.Response(400,text='x'*9000,headers={'x-request-id':secret})
    assert asyncio.run(diagnostics(bad,credential=secret))=={'rejectionCategory':'unspecified','responseReceived':True}
