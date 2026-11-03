"""Actual import / HTTP / PostgreSQL character forks; synthetic providers only."""
from copy import deepcopy
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.domain import digest, make_anchor, utf16_len
from app import models as m
from app.worker import process
from app.prose.prefix_fragments import fragment_roots
from .helpers import BASE, read_chapter, observe
from .test_prose_generations import isolated_prose, get, accept_body


@pytest.fixture
def unicode_book(client, reader):
    literal='林渡😀把信带到港口。原文尚未发生的结局。'
    upload=client.post(BASE+'/books/imports',files={'file':('own-unicode.txt',
        ('第一章 港口\n'+literal+'\n第二章 灯塔\n另一章仍保留。').encode(),'text/plain')},
        headers={'Idempotency-Key':'own-unicode-upload'})
    assert upload.status_code==202,upload.text
    process(upload.json()['taskId'])
    response=client.post(BASE+'/books/imports/'+upload.json()['taskId']+'/confirm',
        json={'title':'合成字符分叉书','parserVersion':'import-v1'},
        headers={'Idempotency-Key':'own-unicode-confirm'})
    assert response.status_code==201,response.text
    return response.json(),literal


def submit_point(client, profile, anchor, key):
    payload={'protocol':'reader-prose-v1','originAnchor':anchor,
        'operation':'counterfactual','chapterCount':1,'chapterLength':'short',
        'instruction':'林渡把信留在港口。','profileId':profile['id'],
        'expectedHeadRevisionId':anchor['revisionId'],
        'target':{'mode':'new_child_branch','name':'合成分叉'}}
    response=client.post(BASE+'/books/'+anchor['bookId']+'/generations',json=payload,
        headers={'Idempotency-Key':key})
    return response,payload


@pytest.mark.parametrize('boundary',[0,2,4])
def test_exact_point_fork_preserves_original_and_lost_ack_is_idempotent(client,reader,unicode_book,profile,boundary):
    book,literal=unicode_book;chapter=read_chapter(client,book)
    with SessionLocal() as db:
        block=db.get(m.ContentBlock,chapter['blocks'][0]['id'])
        anchor=make_anchor(book['bookId'],book['branchId'],book['revisionId'],block,boundary)
        before=deepcopy(db.get(m.Revision,book['revisionId']).manifest)
        original_hash=db.get(m.Revision,book['revisionId']).content_hash
        source_id=block.id
    observe(client,reader,anchor)
    created,payload=submit_point(client,profile,anchor,'char-create')
    assert created.status_code==202,created.text
    process(created.json()['taskId']);snapshot=get(client,created.json())
    assert snapshot['state']=='succeeded',snapshot
    body=accept_body(snapshot,payload)
    response=client.post(BASE+'/generations/'+created.json()['generationId']+'/accept',
        json=body,headers={'Idempotency-Key':'char-accept'})
    assert response.status_code==200,response.text
    receipt=response.json()
    repeat=client.post(BASE+'/generations/'+created.json()['generationId']+'/accept',
        json=body,headers={'Idempotency-Key':'char-accept'})
    assert repeat.json()==receipt
    with SessionLocal() as db:
        source=db.get(m.ContentBlock,source_id);original=db.get(m.Revision,book['revisionId'])
        target=db.get(m.Revision,receipt['revisionId'])
        assert source.text==literal and source.content_hash==digest(literal)
        assert original.manifest==before and original.content_hash==original_hash
        assert db.get(m.Branch,book['branchId']).head_revision_id==original.id
        assert receipt['branchId']!=book['branchId']
        assert db.scalar(select(func.count()).select_from(m.GenerationAcceptance))==1
        progress=db.scalar(select(m.Progress).where(m.Progress.revision_id==target.id))
        if boundary:
            prefix=db.get(m.ContentBlock,target.manifest[0]['blocks'][0])
            assert prefix.text==('林渡' if boundary==2 else '林渡😀')
            assert prefix.id!=source.id and prefix.html is None
            assert fragment_roots([target,original],db.get)=={prefix.id:source.id}
            assert progress.max_observed_anchor['blockId']==prefix.id
            assert progress.max_observed_anchor['end']==boundary
        else:
            assert 'paragraphPrefixes' not in target.provenance
            assert not any(source_id in c['blocks'] for c in target.manifest)
            assert progress is None
        text=''.join(db.get(m.ContentBlock,i).text for c in target.manifest for i in c['blocks'])
        assert '原文尚未发生的结局' not in text


def test_half_surrogate_and_selected_range_fail_before_job_creation(client,reader,unicode_book,profile):
    book,_=unicode_book;chapter=read_chapter(client,book)
    with SessionLocal() as db:
        block=db.get(m.ContentBlock,chapter['blocks'][0]['id'])
        valid=make_anchor(book['bookId'],book['branchId'],book['revisionId'],block,4)
    observe(client,reader,valid)
    for start,end in [(3,3),(2,4)]:
        invalid={**valid,'start':start,'end':end}
        response,_=submit_point(client,profile,invalid,'invalid-'+str(start))
        assert response.status_code==422 and response.json()['code']=='ANCHOR_INVALID',response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.GenerationBatch))==0


def test_nested_point_fork_normalizes_origin_and_reading_points_are_independent(client,reader,unicode_book,profile):
    book,_=unicode_book;chapter=read_chapter(client,book)
    with SessionLocal() as db:
        source=db.get(m.ContentBlock,chapter['blocks'][0]['id'])
        anchor=make_anchor(book['bookId'],book['branchId'],book['revisionId'],source,4)
        source_id=source.id
    observe(client,reader,anchor)
    first,payload=submit_point(client,profile,anchor,'first-char')
    assert first.status_code==202,first.text
    process(first.json()['taskId'])
    accepted=client.post(BASE+'/generations/'+first.json()['generationId']+'/accept',
        json=accept_body(get(client,first.json()),payload),headers={'Idempotency-Key':'first-accept'})
    assert accepted.status_code==200,accepted.text
    with SessionLocal() as db:
        parent=db.get(m.Revision,accepted.json()['revisionId'])
        fragment=db.get(m.ContentBlock,parent.manifest[0]['blocks'][0])
        next_anchor=make_anchor(book['bookId'],parent.branch_id,parent.id,fragment,2)
        fragment_id=fragment.id
    second,next_payload=submit_point(client,profile,next_anchor,'nested-char')
    assert second.status_code==202,second.text
    process(second.json()['taskId'])
    second_snapshot=get(client,second.json());assert second_snapshot['state']=='succeeded',second_snapshot
    next_result=client.post(BASE+'/generations/'+second.json()['generationId']+'/accept',
        json=accept_body(second_snapshot,next_payload),headers={'Idempotency-Key':'nested-accept'})
    assert next_result.status_code==200,next_result.text
    with SessionLocal() as db:
        revision=db.get(m.Revision,next_result.json()['revisionId'])
        parent=db.get(m.Revision,parent.id);original=db.get(m.Revision,book['revisionId'])
        child_id=revision.manifest[0]['blocks'][0]
        assert db.get(m.ContentBlock,child_id).text=='林渡'
        assert fragment_roots([revision,parent,original],db.get)=={fragment_id:source_id,child_id:source_id}
        assert db.get(m.Branch,parent.branch_id).head_revision_id==parent.id
        points=list(db.scalars(select(m.Progress)))
        assert {p.branch_id:p.max_observed_anchor['end'] for p in points}=={
            original.branch_id:4,parent.branch_id:4,revision.branch_id:2}
