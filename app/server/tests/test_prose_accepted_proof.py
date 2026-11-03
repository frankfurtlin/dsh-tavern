"""Actual finite synthetic generation and atomic PostgreSQL prefix acceptance."""
from copy import deepcopy
import json
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import ContentBlock, GenerationAcceptance, GenerationChapterVersion, UsageRecord, Revision
from app.providers import MockAdapter
from app.worker import process
from app.prose.accepted_proof import validate_accepted_proof
from .test_prose_generations import isolated_prose, start, get, accept_body
from .helpers import BASE


def test_actual_two_prefix_commits_retain_original_utf16_maps_and_idempotency(client,reader,demo,profile,monkeypatch):
    original = MockAdapter.generate
    body = '  林渡接过蓝信🙂。  \r\n\n\t他收好了信。\t\r远处传来钟声。\u2028'
    calls = []
    def generate(self,p,prompt,data,limit,**kw):
        if prompt.startswith('prose-chapter-'):
            calls.append('chapter')
            return {'text':body,'finishReason':'stop','usage':{'state':'known','inputTokens':10,'outputTokens':20}}
        if prompt.startswith('prose-review-'):
            calls.append('review')
            return {'text':json.dumps({'completeChapter':True,'issues':[],'factDelta':[],'events':[]}),
                'finishReason':'stop','usage':{'state':'known','inputTokens':10,'outputTokens':20}}
        return original(self,p,prompt,data,limit,**kw)
    monkeypatch.setattr(MockAdapter,'generate',generate)
    receipt,payload=start(client,reader,demo,profile,count=3);process(receipt['taskId']);view=get(client,receipt)
    assert view['completedChapters']==3
    before_calls=len(calls)
    first_body=accept_body(view,payload,count=1)
    first=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=first_body,headers={'Idempotency-Key':'proof-first'})
    assert first.status_code==200,first.text
    repeated=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=first_body,headers={'Idempotency-Key':'proof-first'})
    assert repeated.json()==first.json()
    second=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=accept_body(get(client,receipt),payload,count=3),headers={'Idempotency-Key':'proof-rest'})
    assert second.status_code==200,second.text
    assert len(calls)==before_calls
    assert len(first.json()['acceptedProofs'])==1 and len(second.json()['acceptedProofs'])==2
    with SessionLocal() as db:
        for result in [first.json(),second.json()]:
            acceptance=db.scalar(select(GenerationAcceptance).where(GenerationAcceptance.revision_id==result['revisionId']))
            assert acceptance.result==result
            revision=db.get(Revision,result['revisionId'])
            for proof in result['acceptedProofs']:
                chapter=db.scalar(select(GenerationChapterVersion).where(GenerationChapterVersion.content_id==proof['contentId']))
                manifest=next(c for c in revision.manifest if c['id']==proof['chapterId'])
                validate_accepted_proof(proof,chapter.text,chapter.content_id,chapter.chapter_index,manifest['id'],manifest['blocks'])
                for part in proof['segments']:
                    block=db.get(ContentBlock,part['blockId'])
                    assert block.text==part['text'] and block.content_hash==part['blockHash']
        assert db.scalar(select(func.count()).select_from(GenerationAcceptance))==2


@pytest.mark.parametrize('fault',['missing-first','first-index-moved'])
def test_missing_candidate_chapter_never_accepts_a_later_row_as_prefix_one(client,reader,demo,profile,fault):
    receipt,payload=start(client,reader,demo,profile,count=3);process(receipt['taskId'])
    view=get(client,receipt);body=accept_body(view,payload,count=1)
    with SessionLocal() as db:
        row=db.scalar(select(GenerationChapterVersion).where(GenerationChapterVersion.generation_id==receipt['generationId'],GenerationChapterVersion.chapter_index==1))
        if fault=='missing-first':db.delete(row)
        else:row.chapter_index=4
        db.commit()
        before={model.__name__:db.scalar(select(func.count()).select_from(model)) for model in [ContentBlock,Revision,GenerationAcceptance]}
    response=client.post(BASE+'/generations/'+receipt['generationId']+'/accept',json=body,headers={'Idempotency-Key':'bad-prefix'})
    assert response.status_code==409 and response.json()['code']=='CANDIDATE_UNACCEPTABLE',response.text
    with SessionLocal() as db:
        assert before=={model.__name__:db.scalar(select(func.count()).select_from(model)) for model in [ContentBlock,Revision,GenerationAcceptance]}
