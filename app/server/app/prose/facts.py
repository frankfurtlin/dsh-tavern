"""Quoted candidate facts. Extraction shares the existing finite review call.

Quote verification proves provenance, not semantic truth. These entries never
become original-book knowledge or override it without accepting the chapter.
"""
from copy import deepcopy
import json
import re
from ..domain import canonical,digest,utf16_len,utf16_slice
from .presentation import bounded_json_structure, unique_object

KINDS={'time','location','item','motivation','knowledge','relationship','event'}

def validate_delta(value,text=None):
    if (not isinstance(value,dict) or set(value)!={'schema','sourceHash','status','entries'}
        or value['schema']!='prose-facts-v1' or not isinstance(value['status'],str) or value['status'] not in {'quoted-candidates','unavailable'}
        or not isinstance(value['sourceHash'],str) or not re.fullmatch('[a-f0-9]{64}',value['sourceHash'])
        or not isinstance(value['entries'],list) or len(value['entries'])>12
        or len(canonical(value).encode())>32*1024):raise ValueError('fact delta format')
    if text is not None and digest(text)!=value['sourceHash']:raise ValueError('fact delta source')
    if value['status']=='unavailable' and value['entries']:raise ValueError('unavailable facts')
    for entry in value['entries']:
        if (not isinstance(entry,dict) or set(entry)!={'kind','subjectId','statement','quote','start','end','offsetUnit'}
            or not isinstance(entry['kind'],str) or entry['kind'] not in KINDS or entry['offsetUnit']!='utf16'
            or entry['subjectId'] is not None and (not isinstance(entry['subjectId'],str) or not 0<len(entry['subjectId'])<=160)
            or not isinstance(entry['statement'],str) or not 0<len(entry['statement'])<=600
            or not isinstance(entry['quote'],str) or not 0<len(entry['quote'])<=1200
            or type(entry['start']) is not int or type(entry['end']) is not int
            or not 0<=entry['start']<entry['end']<=6_000_000
            or entry['end']-entry['start']!=utf16_len(entry['quote'])):raise ValueError('fact entry format')
        if text is not None and utf16_slice(text,entry['start'],entry['end'])!=entry['quote']:raise ValueError('fact quote source')
    return deepcopy(value)

def from_review(result,text,subjects=None):
    empty={'schema':'prose-facts-v1','sourceHash':digest(text),'status':'unavailable','entries':[]}
    try:
        if not result or result.get('finishReason') not in {'stop','completed'}:return empty
        raw=result['text']
        if not isinstance(raw,str) or len(raw)>100000 or not bounded_json_structure(raw):return empty
        data=json.loads(raw,object_pairs_hook=unique_object);proposals=data.get('factDelta')
        if not isinstance(proposals,list) or len(proposals)>12:return empty
        entries=[]
        for proposal in proposals:
            if not isinstance(proposal,dict) or set(proposal)!={'kind','subjectId','statement','quote'}:raise ValueError()
            quote=proposal['quote'];start=text.find(quote) if isinstance(quote,str) else -1
            if start<0 or text.find(quote,start+1)>=0:raise ValueError('ambiguous quote')
            subject=proposal['subjectId']
            if subject is not None and subjects is not None and subject not in subjects:raise ValueError('unknown subject')
            offset=utf16_len(text[:start]);entries.append({**proposal,'start':offset,'end':offset+utf16_len(quote),'offsetUnit':'utf16'})
        return validate_delta({**empty,'status':'quoted-candidates','entries':entries},text)
    except (ValueError,KeyError,TypeError,AttributeError,RecursionError):return empty

def validate_ledger(value):
    if not isinstance(value,dict) or len(value)>10000:raise ValueError('ledger versions')
    for version,chapters in value.items():
        if not isinstance(version,str) or not version.isdecimal() or not 1<=int(version)<=10000 or not isinstance(chapters,dict) or len(chapters)>200:raise ValueError('ledger version')
        for index,delta in chapters.items():
            if not isinstance(index,str) or not index.isdecimal() or not 1<=int(index)<=200:raise ValueError('ledger chapter')
            validate_delta(delta)
    return deepcopy(value)
