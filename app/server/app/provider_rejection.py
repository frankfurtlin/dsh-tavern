"""Categorize a bounded error body without retaining supplier prose or secrets."""
import json
import re

CATEGORIES = frozenset(('output_configuration','thinking_configuration','structured_output',
    'image_input','context_limit','quota','rate_limit','model_unavailable','authentication','unspecified'))
CODES = frozenset(('model_coming_soon','model_not_found','context_length_exceeded',
    'insufficient_quota','invalid_api_key','unsupported_parameter','rate_limit_exceeded'))


def error_text(raw):
    try:value=json.loads(raw)
    except (ValueError,TypeError):return ''
    parts=[]
    def visit(value,depth=0):
        if depth>3 or not isinstance(value,dict):return
        for key in ('message','type','code','name','param'):
            if isinstance(value.get(key),str):parts.append(value[key][:4096])
        for key in ('error','data','detail'):
            if isinstance(value.get(key),str):parts.append(value[key][:4096])
            else:visit(value.get(key),depth+1)
    visit(value)
    return ' '.join(parts).lower()


def category(raw):
    text=error_text(raw)
    if 'model_coming_soon' in text or ('model' in text and 'coming soon' in text):return 'model_unavailable'
    if any(p in text for p in ('max_tokens','max_completion_tokens','output token')):return 'output_configuration'
    if any(p in text for p in ('thinking','reasoning')):return 'thinking_configuration'
    if any(p in text for p in ('response_format','json_schema','json mode')):return 'structured_output'
    if 'image' in text or 'multimodal' in text:return 'image_input'
    if 'context' in text and any(p in text for p in ('length','limit','exceed')):return 'context_limit'
    if any(p in text for p in ('quota','credit','balance','insufficient')):return 'quota'
    if any(p in text for p in ('rate limit','too many requests')):return 'rate_limit'
    if 'model' in text and any(p in text for p in ('not found','not available','unsupported')):return 'model_unavailable'
    if any(p in text for p in ('invalid_api_key','invalid api key','authentication_error','unauthorized')):return 'authentication'
    return 'unspecified'


async def diagnostics(response, *, credential=''):
    """Persist only fixed categories/codes and a bounded, secret-free request ID."""
    raw=b''
    try:
        async for chunk in response.aiter_bytes():
            raw+=chunk[:8193-len(raw)]
            if len(raw)>8192:
                raw=b'';break
    except Exception:raw=b''
    text=error_text(raw)
    result={'rejectionCategory':category(raw),'responseReceived':True}
    if result['rejectionCategory']=='unspecified':
        if response.status_code==429:result['rejectionCategory']='rate_limit'
        elif response.status_code==402:result['rejectionCategory']='quota'
    if response.status_code==429:
        retry=response.headers.get('retry-after','')
        if len(retry)<=128 and not (credential and credential in retry):
            if len(retry)<=9 and retry.isdigit():result['retryAfterSeconds']=max(1,int(retry))
            else:
                try:
                    from email.utils import parsedate_to_datetime
                    from datetime import datetime,timezone
                    from math import ceil
                    until=parsedate_to_datetime(retry)
                    if until.tzinfo:result['retryAfterSeconds']=max(1,ceil((until-datetime.now(timezone.utc)).total_seconds()))
                except (ValueError,TypeError,OverflowError):pass
    for code in sorted(CODES):
        if re.search(r'(?<![a-z0-9_])'+code+r'(?![a-z0-9_])',text):
            result['rejectionCode']=code;break
    for header in ('x-request-id','request-id'):
        value=response.headers.get(header,'')
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',value) and not (credential and credential in value):
            result['upstreamRequestId']=value;break
    return result


async def from_response(response):
    try:
        raw=b''
        async for chunk in response.aiter_bytes():
            raw+=chunk[:8193-len(raw)]
            if len(raw)>8192:return 'unspecified'
        return category(raw)
    except Exception:return 'unspecified'
