"""Selection edit HTTP with real owned imported text and isolated PostgreSQL."""
from copy import deepcopy
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.domain import make_anchor, digest
from app import models as m
from app.prose.selection_edits import validate_selection_revision
from app.worker import process
from .helpers import BASE, read_chapter


@pytest.fixture
def selection_book(client, reader):
    data='第一章 渡口\n林渡😀披着旧雨衣。\n梅舟e\u0301把信收好。\n第二章 远岸\n他们仍在原路。'
    uploaded=client.post(BASE+'/books/imports',files={'file':('own-selection.txt',data.encode(),'text/plain')},headers={'Idempotency-Key':'own-selection-import'})
    assert uploaded.status_code==202,uploaded.text
    process(uploaded.json()['taskId'])
    response=client.post(BASE+'/books/imports/'+uploaded.json()['taskId']+'/confirm',json={'title':'自有选区试验','parserVersion':'import-v1'},headers={'Idempotency-Key':'own-selection-confirm'})
    assert response.status_code==201,response.text
    book=response.json();chapter=read_chapter(client,book)
    with SessionLocal() as db:
        first=db.get(m.ContentBlock,chapter['blocks'][0]['id']);second=db.get(m.ContentBlock,chapter['blocks'][1]['id'])
        start=make_anchor(book['bookId'],book['branchId'],book['revisionId'],first,4)
        end=make_anchor(book['bookId'],book['branchId'],book['revisionId'],second,4)
    return book,chapter,start,end


def submit(client,fixture,text='借来一盏灯。',key='own-selection-apply',target=None,source_policy='requireUnchanged'):
    book,_,start,end=fixture
    payload={'startAnchor':start,'endAnchor':end,'replacementText':text,'expectedSourceHeadRevisionId':book['revisionId'],'sourcePolicy':source_policy}
    if target:payload.update(targetBranchId=target['branchId'],expectedTargetHeadRevisionId=target['revisionId'])
    response=client.post(BASE+'/books/'+book['bookId']+'/selection-edits',json=payload,headers={'Idempotency-Key':key})
    return response,payload


def counts():
    with SessionLocal() as db:return tuple(db.scalar(select(func.count()).select_from(cls)) for cls in (m.Branch,m.Revision,m.ContentBlock,m.Task,m.Idempotency))


def test_exact_multi_paragraph_replacement_is_a_new_line_and_idempotent(client,reader,selection_book):
    book,chapter,_,_=selection_book
    with SessionLocal() as db:
        original=deepcopy(db.get(m.Revision,book['revisionId']).manifest)
        source=[(r['id'],r['text'],r['contentHash']) for r in chapter['blocks']]
        tasks=db.scalar(select(func.count()).select_from(m.Task))
    response,payload=submit(client,selection_book);assert response.status_code==201,response.text
    receipt=response.json();assert receipt['branchId']!=book['branchId']
    before=counts();again=client.post(BASE+'/books/'+book['bookId']+'/selection-edits',json=payload,headers={'Idempotency-Key':'own-selection-apply'})
    assert again.json()==receipt and counts()==before
    with SessionLocal() as db:
        revision=db.get(m.Revision,receipt['revisionId']);validate_selection_revision(revision,db.get)
        block=db.get(m.ContentBlock,revision.manifest[0]['blocks'][0])
        assert block.text=='林渡😀借来一盏灯。把信收好。' and block.content_hash==digest(block.text) and block.source=='manual'
        assert db.get(m.Revision,book['revisionId']).manifest==original
        assert db.get(m.Branch,book['branchId']).head_revision_id==book['revisionId']
        assert [(db.get(m.ContentBlock,i).id,db.get(m.ContentBlock,i).text,db.get(m.ContentBlock,i).content_hash) for i,_,_ in source]==source
        assert db.scalar(select(func.count()).select_from(m.Task))==tasks


def test_candidate_revisions_restore_exact_paragraph_ids_and_keep_every_prior_version(client,reader,selection_book):
    book,_,_,_=selection_book
    first,_=submit(client,selection_book);assert first.status_code==201,first.text
    second,_=submit(client,selection_book,text='换一条路。',key='own-second',target=first.json());assert second.status_code==201,second.text
    restored,_=submit(client,selection_book,text=None,key='own-restore',target=second.json());assert restored.status_code==201,restored.text
    with SessionLocal() as db:
        old=db.get(m.Revision,first.json()['revisionId']);now=db.get(m.Revision,restored.json()['revisionId']);source=db.get(m.Revision,book['revisionId'])
        assert now.manifest==source.manifest and now.content_hash==source.content_hash
        assert old.manifest!=source.manifest and db.get(m.ContentBlock,old.manifest[0]['blocks'][0]).text=='林渡😀借来一盏灯。把信收好。'
        assert now.parent_revision_id==second.json()['revisionId'] and now.sequence==3
        validate_selection_revision(now,db.get)
    history=client.get(BASE+'/branches/'+first.json()['branchId']+'/revision-history',params={'limit':2}).json()
    assert [row['sequence'] for row in history['items']]==[3,2] and history['nextBeforeSequence']==2
    tail=client.get(BASE+'/branches/'+first.json()['branchId']+'/revision-history',params={'beforeSequence':2,'limit':2}).json()
    assert [row['sequence'] for row in tail['items']]==[1] and tail['nextBeforeSequence'] is None


def test_candidate_selection_and_restore_have_one_receipt_and_preserve_source(client,reader,selection_book):
    book,chapter,_,_=selection_book
    first,_=submit(client,selection_book)
    second,_=submit(client,selection_book,text='第二候选。',key='own-candidate-second',target=first.json())
    preview=client.get(BASE+'/revisions/'+first.json()['revisionId']+'/selection-edit')
    assert preview.status_code==200,preview.text
    detail=preview.json()
    assert detail['originalText']=='披着旧雨衣。\n\n梅舟e\u0301' and detail['replacementText']=='借来一盏灯。'
    assert detail['targetHeadRevisionId']==second.json()['revisionId'] and detail['sourceHeadRevisionId']==book['revisionId']
    path=BASE+'/branches/'+first.json()['branchId']+'/selection-revisions'
    action={'action':'select_candidate','candidateRevisionId':first.json()['revisionId'],'expectedSourceHeadRevisionId':book['revisionId'],'expectedTargetHeadRevisionId':second.json()['revisionId']}
    before=counts();chosen=client.post(path,json=action,headers={'Idempotency-Key':'own-candidate-choice'})
    assert chosen.status_code==201,chosen.text
    after=counts();assert after[0]==before[0] and after[1]==before[1]+1 and after[4]==before[4]+1
    retry=client.post(path,json=action,headers={'Idempotency-Key':'own-candidate-choice'})
    assert retry.json()==chosen.json() and counts()==after
    assert chosen.json()['sequence']==3
    with SessionLocal() as db:
        row=db.get(m.Revision,chosen.json()['revisionId']);validate_selection_revision(row,db.get)
        assert db.get(m.ContentBlock,row.manifest[0]['blocks'][0]).text=='林渡😀借来一盏灯。把信收好。'
        assert db.get(m.Revision,second.json()['revisionId']).provenance['selectionEdit']['replacementText']=='第二候选。'
    restore={'action':'restore_original','expectedSourceHeadRevisionId':book['revisionId'],'expectedTargetHeadRevisionId':chosen.json()['revisionId']}
    restored=client.post(path,json=restore,headers={'Idempotency-Key':'own-candidate-restore'})
    assert restored.status_code==201,restored.text
    with SessionLocal() as db:
        row=db.get(m.Revision,restored.json()['revisionId']);source=db.get(m.Revision,book['revisionId'])
        assert row.manifest==source.manifest and row.content_hash==source.content_hash
    unchanged=client.get(BASE+'/branches/'+book['branchId']+'/chapters/'+chapter['id']+'?revisionId='+book['revisionId']).json()
    assert unchanged==chapter
    before=counts();stale=client.post(path,json=restore,headers={'Idempotency-Key':'own-stale-choice'})
    assert stale.status_code==409 and counts()==before


def test_selection_detail_and_reselect_are_owner_and_family_scoped(client,reader,selection_book):
    first,_=submit(client,selection_book)
    other,_=submit(client,selection_book,key='own-other-selection',text='另一条线。')
    book=selection_book[0]
    action={'action':'select_candidate','candidateRevisionId':other.json()['revisionId'],'expectedSourceHeadRevisionId':book['revisionId'],'expectedTargetHeadRevisionId':first.json()['revisionId']}
    path=BASE+'/branches/'+first.json()['branchId']+'/selection-revisions'
    before=counts();wrong=client.post(path,json=action,headers={'Idempotency-Key':'own-cross-family'})
    assert wrong.status_code==422 and wrong.json()['code']=='SELECTION_TARGET' and counts()==before
    assert client.get(BASE+'/revisions/'+book['revisionId']+'/selection-edit').status_code==404
    action.pop('candidateRevisionId')
    assert client.post(path,json=action,headers={'Idempotency-Key':'own-shape'}).status_code==422 and counts()==before
    assert client.post(BASE+'/auth/register',json={'username':'selection_other','password':'owned-other-password-42'}).status_code==201
    login=client.post(BASE+'/auth/login',json={'username':'selection_other','password':'owned-other-password-42','clientType':'android','deviceName':'owned other'})
    client.headers['Authorization']='Bearer '+login.json()['accessToken']
    assert client.get(BASE+'/revisions/'+first.json()['revisionId']+'/selection-edit').status_code==404
    action.update(candidateRevisionId=first.json()['revisionId'])
    assert client.post(path,json=action,headers={'Idempotency-Key':'own-other-owner'}).status_code==404 and counts()==before


def test_archived_lines_keep_reading_and_receipts_but_cannot_be_edited(client,reader,selection_book):
    book,_,_,_=selection_book
    first,payload=submit(client,selection_book)
    archived=client.patch(BASE+'/branches/'+first.json()['branchId'],json={'expectedVersion':first.json()['version'],'status':'archived'})
    assert archived.status_code==200,archived.text
    before=counts()
    action={'action':'restore_original','expectedSourceHeadRevisionId':book['revisionId'],'expectedTargetHeadRevisionId':first.json()['revisionId']}
    blocked=client.post(BASE+'/branches/'+first.json()['branchId']+'/selection-revisions',json=action,headers={'Idempotency-Key':'own-archived-action'})
    assert blocked.status_code==409 and blocked.json()['code']=='BRANCH_ARCHIVED' and counts()==before
    edited,_=submit(client,selection_book,text='归档后误改。',key='own-archived-target',target=first.json())
    assert edited.status_code==409 and edited.json()['code']=='BRANCH_ARCHIVED' and counts()==before
    preview=client.get(BASE+'/revisions/'+first.json()['revisionId']+'/selection-edit')
    assert preview.status_code==200
    chapter=client.get(BASE+'/branches/'+first.json()['branchId']+'/chapters/'+selection_book[1]['id']).json()
    child={'startAnchor':chapter['blocks'][0]['startAnchor'],'endAnchor':chapter['blocks'][0]['endAnchor'],'replacementText':'不隐式恢复。','expectedSourceHeadRevisionId':first.json()['revisionId']}
    rejected=client.post(BASE+'/books/'+book['bookId']+'/selection-edits',json=child,headers={'Idempotency-Key':'own-archived-source'})
    assert rejected.status_code==409 and rejected.json()['code']=='BRANCH_ARCHIVED' and counts()==before
    replay=client.post(BASE+'/books/'+book['bookId']+'/selection-edits',json=payload,headers={'Idempotency-Key':'own-selection-apply'})
    assert replay.json()==first.json() and counts()==before
    restored=client.patch(BASE+'/branches/'+first.json()['branchId'],json={'expectedVersion':archived.json()['version'],'status':'active'})
    assert restored.status_code==200
    changed=client.post(BASE+'/branches/'+first.json()['branchId']+'/selection-revisions',json=action,headers={'Idempotency-Key':'own-archived-action'})
    assert changed.status_code==201 and changed.json()['restored'],changed.text


@pytest.mark.parametrize('poison',['half-emoji','reverse','different-book','different-chapter','range-anchor'])
def test_invalid_selection_rejects_before_any_write(client,reader,selection_book,poison):
    book,chapter,start,end=selection_book
    payload={'startAnchor':deepcopy(start),'endAnchor':deepcopy(end),'replacementText':'无效','expectedSourceHeadRevisionId':book['revisionId']}
    if poison=='half-emoji':payload['startAnchor'].update(start=3,end=3)
    elif poison=='reverse':payload['startAnchor'],payload['endAnchor']=payload['endAnchor'],payload['startAnchor']
    elif poison=='different-book':payload['endAnchor']['bookId']='not-this-book'
    elif poison=='different-chapter':
        other=client.get(BASE+'/branches/'+book['branchId']+'/chapters').json()['items'][1]
        block=client.get(BASE+'/branches/'+book['branchId']+'/chapters/'+other['id']).json()['blocks'][0]
        payload['endAnchor']=block['endAnchor']
    else:payload['startAnchor']=deepcopy(chapter['blocks'][0]['startAnchor']);payload['startAnchor']['end']=4
    before=counts();response=client.post(BASE+'/books/'+book['bookId']+'/selection-edits',json=payload,headers={'Idempotency-Key':'own-invalid'})
    assert response.status_code==422,response.text
    assert counts()==before


def test_stale_target_rejects_and_wrong_family_never_overwrites(client,reader,selection_book):
    first,_=submit(client,selection_book);assert first.status_code==201,first.text
    second,_=submit(client,selection_book,text='第二候选。',key='second',target=first.json());assert second.status_code==201,second.text
    before=counts();stale,_=submit(client,selection_book,text='过时编辑。',key='stale',target=first.json());assert stale.status_code==409,stale.text;assert counts()==before
    fixture=list(deepcopy(selection_book));fixture[2]['start']=0;fixture[2]['end']=0
    with SessionLocal() as db:fixture[2]=make_anchor(fixture[0]['bookId'],fixture[0]['branchId'],fixture[0]['revisionId'],db.get(m.ContentBlock,fixture[2]['blockId']),0)
    wrong,_=submit(client,fixture,key='wrong-family',target=second.json());assert wrong.status_code==422,wrong.text;assert counts()==before


def test_other_owner_cannot_read_or_modify_selection_history(client,reader,selection_book):
    first,_=submit(client,selection_book);assert first.status_code==201,first.text
    client.post(BASE+'/auth/logout');client.headers.pop('Authorization',None)
    registered=client.post(BASE+'/auth/register',json={'username':'selection_other','password':'another-strong-password'});assert registered.status_code==201,registered.text
    login=client.post(BASE+'/auth/login',json={'username':'selection_other','password':'another-strong-password','clientType':'android','deviceName':'own-other-selection'});assert login.status_code==200,login.text
    client.headers['Authorization']='Bearer '+login.json()['accessToken']
    denied=client.get(BASE+'/branches/'+first.json()['branchId']+'/revision-history');assert denied.status_code==404,denied.text
    before=counts();denied,_=submit(client,selection_book,key='foreign');assert denied.status_code==404,denied.text;assert counts()==before
