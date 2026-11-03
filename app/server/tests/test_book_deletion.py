"""Real owner-scoped source deletion and independent reading publications."""
from copy import deepcopy
import json
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.domain import canonical, digest, make_anchor, utf16_len
from app.models import Book, BookDeletion, Branch, Revision, ContentBlock, SourceDocument, Progress, Bookmark, RoleSession, RoleMessage, KnowledgeSnapshot, ResumeTarget, Task, TaskEvent, UsageRecord, Candidate, uid
from app.storage import private_path
from app.world.models import World, WorldPublication
from .helpers import BASE
from .test_world import API, start, post
from .test_world_security import isolated_storage_and_mock_only


def queue_reader(client,profile,anchor):
    body={"operation":"counterfactual","profileId":profile["id"],"anchor":anchor,
        "expectedHeadRevisionId":anchor["revisionId"],"instruction":"留在原地观察"}
    planned=client.post(BASE+"/operation-plans",json=body)
    assert planned.status_code==201,planned.text
    p=planned.json()
    consent=client.post(BASE+"/consents",json={"planId":p["planId"],"planHash":p["planHash"],"profileId":profile["id"],
        "maxCalls":p["maxCalls"],"inputTokens":p["inputTokens"],"outputTokens":p["outputTokens"]})
    assert consent.status_code==201,consent.text
    queued=client.post(BASE+"/generation-tasks",json={**body,"consentId":consent.json()["consentId"]},headers={"Idempotency-Key":"source-reader-task"})
    assert queued.status_code==202,queued.text
    return queued.json()["taskId"]


def delete(client, book_id, version=1, key="delete-source"):
    return client.request("DELETE", BASE + "/books/" + book_id, json={"expectedVersion": version}, headers={"Idempotency-Key": key})


def legacy_publication(db, owner, demo, world_id, parent=None):
    """Historical same-book layout, hand-built from its real persisted contract."""
    base = db.get(Revision, demo["revisionId"])
    branch = Branch(id=uid(), owner_id=owner, book_id=demo["bookId"], parent_branch_id=parent or demo["branchId"],
        name="旧公开故事", head_revision_id=uid())
    db.add(branch); db.flush()
    chapter = uid(); block = ContentBlock(id=uid(), owner_id=owner, book_id=demo["bookId"], chapter_id=chapter,
        text="ONLY_PUBLIC_SCENE 中文🙂", content_hash=digest("ONLY_PUBLIC_SCENE 中文🙂"), source="ai")
    db.add(block); db.flush()
    revision = Revision(id=branch.head_revision_id, owner_id=owner, branch_id=branch.id,
        parent_revision_id=base.id, manifest=[deepcopy(base.manifest[0]), {"id":chapter,"title":"公开场景","blocks":[block.id]}],
        content_hash=digest("legacy-publication"), provenance={"operation":"world_publication","worldId":world_id})
    db.add(revision); db.flush()
    at = make_anchor(demo["bookId"], branch.id, revision.id, block, utf16_len(block.text))
    return branch, revision, block, at


def test_real_delete_detaches_old_publication_even_without_live_world_and_preserves_exact_positions(client, reader, demo):
    owner=reader["user"]["id"]
    with SessionLocal() as db:
        branch, revision, block, at = legacy_publication(db, owner, demo, uid())
        child=Branch(id=uid(),owner_id=owner,book_id=demo["bookId"],parent_branch_id=branch.id,name="出版后子线",head_revision_id=uid())
        db.add(child);db.flush()
        cr=Revision(id=child.head_revision_id,owner_id=owner,branch_id=child.id,manifest=deepcopy(revision.manifest),
            parent_revision_id=revision.id,content_hash=revision.content_hash,provenance={"operation":"continue_at_tail"})
        db.add(cr)
        db.add(Progress(owner_id=owner,book_id=demo["bookId"],branch_id=branch.id,revision_id=revision.id,anchor=at,max_observed_anchor=at))
        db.add(Bookmark(owner_id=owner,book_id=demo["bookId"],branch_id=branch.id,revision_id=revision.id,anchor=at,note="保留笔记"))
        source=db.get(SourceDocument, db.get(Book,demo["bookId"]).source_id)
        refs={source.file_ref,source.normalized_ref}; old_manifest=deepcopy(revision.manifest)
        db.commit(); bid,rid,child_id=branch.id,revision.id,child.id
    response=delete(client,demo["bookId"])
    assert response.status_code==202,response.text
    receipt=response.json(); assert receipt["deleted"] and not receipt["cleanupPending"]
    carrier=receipt["retainedStories"][0]["bookId"]
    assert carrier != demo["bookId"]
    assert client.get(BASE+"/books/"+demo["bookId"]).status_code==404
    assert client.get(BASE+"/branches/"+demo["branchId"]+"/chapters").status_code==404
    resolved=client.post(BASE+"/reading-context/resolve",json={"bookId":demo["bookId"],"branchId":bid,"revisionId":rid,"anchor":at})
    assert resolved.status_code==200,resolved.text
    assert resolved.json()["anchor"]=={**at,"bookId":carrier} and resolved.json()["migrated"]
    assert client.post(BASE+"/reading-context/resolve",json={"bookId":demo["bookId"],"branchId":demo["branchId"]}).status_code==404
    assert delete(client,demo["bookId"]).json()["deletionId"]==receipt["deletionId"]
    with SessionLocal() as db:
        assert db.get(Book,demo["bookId"]) is None
        assert db.get(Revision,rid).manifest==old_manifest
        assert db.get(Branch,bid).book_id==carrier and db.get(Branch,bid).parent_branch_id is None
        assert db.get(Branch,child_id).parent_branch_id==bid
        assert db.scalar(select(Progress).where(Progress.branch_id==bid)).anchor=={**at,"bookId":carrier}
        assert db.scalar(select(Bookmark).where(Bookmark.branch_id==bid)).note=="保留笔记"
        assert all(not private_path(owner,ref).exists() for ref in refs)


def test_new_derived_publication_is_independent_and_source_deletion_leaves_world_unchanged(client,reader,demo,profile):
    world,line=start(client,reader,demo,profile)
    saved=post(client,f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",{},"independent-publish")
    assert saved["bookId"]!=demo["bookId"]
    with SessionLocal() as db:
        before=deepcopy(db.get(World,world["id"]).source)
        body="\n".join(db.get(ContentBlock,b).text for c in db.get(Revision,saved["revisionId"]).manifest for b in c["blocks"])
    assert delete(client,demo["bookId"]).status_code==202
    assert client.get(BASE+"/books/"+saved["bookId"]).status_code==200
    assert client.get(API+"/worlds/"+world["id"]).json()["sourceRemoved"]
    with SessionLocal() as db:
        assert db.get(World,world["id"]).source==before
        assert "\n".join(db.get(ContentBlock,b).text for c in db.get(Revision,saved["revisionId"]).manifest for b in c["blocks"])==body
    assert post(client,f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",{},"independent-again")==saved


def test_delete_cas_and_owner_are_authoritative_and_old_demo_receipt_cannot_recreate(client,reader,demo):
    assert delete(client,demo["bookId"],version=2).status_code==409
    assert client.get(BASE+"/books/"+demo["bookId"]).status_code==200
    assert delete(client,uid()).status_code==404
    assert delete(client,demo["bookId"]).status_code==202
    replay=client.post(BASE+"/books/demo",json={},headers={"Idempotency-Key":"demo"})
    assert replay.status_code==404 and replay.json()["code"]=="BOOK_DELETED"
    again=client.post(BASE+"/books/demo",json={},headers={"Idempotency-Key":"explicit-new-import"})
    assert again.status_code==201 and again.json()["bookId"]!=demo["bookId"]


@pytest.mark.parametrize("receipt_kind",["known","not_sent","unknown"])
def test_actual_inflight_reader_delete_settles_receipt_once_without_private_resurrection(client,reader,demo,profile,receipt_kind):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from unittest.mock import patch
    from app.errors import Problem
    from app.models import Consent,ModelProfile
    from app.providers import MockAdapter
    from app.worker import process,claim,settle_usage
    from .helpers import read_chapter,observe
    at=read_chapter(client,demo)["blocks"][-1]["endAnchor"]
    observe(client,reader,at);task_id=queue_reader(client,profile,at)
    claimed=claim("own-source-delete-fixture");assert claimed==task_id
    entered,release=Event(),Event();calls=[];original=MockAdapter.generate
    result={"text":"LATE_PRIVATE_READER_BODY","finishReason":"stop","usage":{"state":"known","inputTokens":7,"outputTokens":9}}
    if receipt_kind=="not_sent":result={"notSent":"PROVIDER_HOST_UNAVAILABLE","usage":{"state":"known","inputTokens":0,"outputTokens":0}}
    def paused(adapter,p,prompt,data,limit,*args,**kwargs):
        calls.append(prompt.splitlines()[0])
        if prompt.startswith("counterfactual-"):
            entered.set();assert release.wait(15)
            if receipt_kind in ("not_sent","unknown"):
                raise Problem("PROVIDER_HOST_UNAVAILABLE" if receipt_kind=="not_sent" else "PROVIDER_TRANSPORT_ERROR","controlled mock receipt")
            return deepcopy(result)
        return original(adapter,p,prompt,data,limit,*args,**kwargs)
    with patch.object(MockAdapter,"generate",paused),ThreadPoolExecutor(max_workers=1) as pool:
        running=pool.submit(process,claimed)
        try:
            assert entered.wait(15)
            with SessionLocal() as db:
                usage=db.scalar(select(UsageRecord).where(UsageRecord.task_id==task_id,UsageRecord.operation=="generation"));assert usage.status=="intent"
                call_id=usage.provider_call_id;reserved=(usage.reserved_input,usage.reserved_output);consent_id=usage.consent_id
                before=[getattr(db.get(ModelProfile,profile["id"]),name) for name in ("calls_reserved","input_reserved","output_reserved")]
            response=delete(client,demo["bookId"]);assert response.status_code==202,response.text
        finally:release.set()
        running.result(timeout=20)
    with SessionLocal() as db:
        usage=db.scalar(select(UsageRecord).where(UsageRecord.provider_call_id==call_id))
        assert usage.status==("intent" if receipt_kind=="unknown" else "not_sent" if receipt_kind=="not_sent" else "completed")
        if receipt_kind!="unknown":assert (usage.input_tokens,usage.output_tokens,usage.usage_state)==(result["usage"]["inputTokens"],result["usage"]["outputTokens"],"known")
        task=db.get(Task,task_id);assert task.result=={"bookId":demo["bookId"],"deleted":True}
        assert (task.state,task.persistence_state)==("cancelled","saved")
        assert not db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id==task_id))
        assert not db.scalar(select(func.count()).select_from(Candidate).where(Candidate.task_id==task_id))
        assert not db.get(Book,demo["bookId"])
        after=[getattr(db.get(ModelProfile,profile["id"]),name) for name in ("calls_reserved","input_reserved","output_reserved")]
        refund=[0,0,0] if receipt_kind=="unknown" else [int(receipt_kind=="not_sent"),reserved[0]-result["usage"]["inputTokens"],reserved[1]-result["usage"]["outputTokens"]]
        assert after==[x-y for x,y in zip(before,refund)]
        assert db.get(Consent,consent_id).calls_reserved==after[0]
    assert len(calls)==2
    assert client.get(BASE+"/tasks/"+task_id).status_code==404
    assert not list(private_path(reader["user"]["id"],"dummy").parent.glob("journal-"+task_id+"-*"))
    if receipt_kind!="unknown":settle_usage(task_id,call_id,result);settle_usage(task_id,call_id,result)
    with patch.object(MockAdapter,"generate",side_effect=AssertionError("deleted task cannot send")):process(claimed)
    with SessionLocal() as db:
        assert [getattr(db.get(ModelProfile,profile["id"]),name) for name in ("calls_reserved","input_reserved","output_reserved")]==after


def test_delete_commit_rollback_preserves_original_rows_files_and_no_marker(client,reader,demo,monkeypatch):
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import OperationalError
    from .test_world_security import _snapshot
    owner=reader["user"]["id"]
    with SessionLocal() as db:
        legacy_publication(db,owner,demo,uid());db.commit()
    before=_snapshot([owner]);original=Session.commit
    def reject(db):
        if db.scalar(select(BookDeletion).where(BookDeletion.book_id==demo["bookId"])):
            raise OperationalError("controlled source deletion commit",{},RuntimeError("synthetic"))
        return original(db)
    monkeypatch.setattr(Session,"commit",reject)
    response=delete(client,demo["bookId"])
    assert response.status_code==503,response.text
    after=_snapshot([owner])
    # New publication staging can be orphaned on rollback; original rows and
    # every original file must be unchanged, and no irreversible marker exists.
    assert after["rows"]==before["rows"]
    assert all(after["files"].get(name)==body for name,body in before["files"].items())
    assert not any("book-deleted-" in name for name in after["files"])
    assert client.get(BASE+"/books/"+demo["bookId"]).status_code==200


def test_committed_cleanup_failure_is_queryable_and_actual_maintenance_retries(client,reader,demo,monkeypatch):
    from pathlib import Path
    from app.book_deletion import clean_deleted_books
    original=Path.unlink
    def reject(path,*args,**kwargs):
        if path.name.startswith("normalized-"):raise OSError("controlled cleanup failure")
        return original(path,*args,**kwargs)
    with monkeypatch.context() as scope:
        scope.setattr(Path,"unlink",reject)
        response=delete(client,demo["bookId"])
    assert response.status_code==202 and response.json()["cleanupPending"]
    assert client.get(BASE+"/books/"+demo["bookId"]).status_code==404
    assert client.get(BASE+"/books/"+demo["bookId"]+"/deletion").json()["cleanupPending"]
    assert clean_deleted_books()
    assert not client.get(BASE+"/books/"+demo["bookId"]+"/deletion").json()["cleanupPending"]
    assert delete(client,demo["bookId"]).json()["deletionId"]==response.json()["deletionId"]


def test_nested_old_publication_family_never_inherits_other_world_after_detach(client,reader,demo,profile):
    from app.api_stories import publication_branches
    world,line=start(client,reader,demo,profile);owner=reader["user"]["id"]
    with SessionLocal() as db:
        one,r1,_,a1=legacy_publication(db,owner,demo,world["id"])
        other_id=uid();two,r2,_,a2=legacy_publication(db,owner,demo,other_id,parent=one.id)
        db.commit();ids=(one.id,two.id)
        assert publication_branches(db,owner,db.get(World,world["id"]))=={one.id}
    context={"storyId":"world-"+world["id"],"mode":"read","bookId":demo["bookId"],"readerBranchId":ids[1],
        "anchor":a2,"perspective":"reader","entryOrigin":"reader"}
    assert client.post(BASE+"/stories/"+context["storyId"]+"/context/validate",json=context).status_code==404
    receipt=delete(client,demo["bookId"]).json();carrier=receipt["retainedStories"][0]["bookId"]
    assert set(receipt["retainedStories"][0]["branchIds"])==set(ids)
    with SessionLocal() as db:
        assert publication_branches(db,owner,db.get(World,world["id"]))=={ids[0]}
        assert db.get(Revision,r1.id).manifest[0]["blocks"]==db.get(Revision,r2.id).manifest[0]["blocks"]
    context={**context,"bookId":carrier,"anchor":{**a2,"bookId":carrier}}
    assert client.post(BASE+"/stories/"+context["storyId"]+"/context/validate",json=context).status_code==404
    own={**context,"readerBranchId":ids[0],"anchor":{**a1,"bookId":carrier}}
    assert client.post(BASE+"/stories/"+own["storyId"]+"/context/validate",json=own).status_code==200


def test_retained_role_history_is_readable_but_private_knowledge_requires_explicit_refresh(client,reader,demo):
    owner=reader["user"]["id"]
    with SessionLocal() as db:
        branch,revision,_,at=legacy_publication(db,owner,demo,uid())
        snapshot=KnowledgeSnapshot(owner_id=owner,book_id=demo["bookId"],branch_id=branch.id,revision_id=revision.id,boundary=at,
            scope_hash=digest("private-future-snapshot"),profile_version="fixture",data={"PRIVATE_FUTURE":"REMOVE_THIS"})
        db.add(snapshot);db.flush()
        session=RoleSession(owner_id=owner,book_id=demo["bookId"],branch_id=branch.id,pinned_revision_id=revision.id,
            pinned_anchor=at,character_id="person",character_name="公开人物",snapshot_id=snapshot.id)
        db.add(session);db.flush()
        db.add(RoleMessage(owner_id=owner,session_id=session.id,turn_id="history",sequence=1,role="user",text="已保存历史",task_id=uid()))
        db.commit();sid=session.id
    assert delete(client,demo["bookId"]).status_code==202
    detail=client.get(BASE+"/role-sessions/"+sid)
    assert detail.status_code==200 and detail.json()["status"]=="needs_refresh",detail.text
    assert client.get(BASE+"/role-sessions/"+sid+"/messages").json()["items"][0]["text"]=="已保存历史"
    with SessionLocal() as db:
        from app.character_cards import ensure_session_scope
        from app.errors import Problem
        assert db.scalar(select(func.count()).select_from(KnowledgeSnapshot))==0
        with pytest.raises(Problem) as blocked:ensure_session_scope(db,db.get(RoleSession,sid),owner)
        assert blocked.value.code=="ROLE_NEEDS_REFRESH"
        assert db.scalar(select(func.count()).select_from(UsageRecord))==0


def test_publication_images_are_self_contained_and_world_can_act_after_real_source_delete(client,reader,demo,profile):
    from app.storage import write_private
    from .helpers import read_chapter
    from .test_world import action
    from app.world.worker import process
    owner=reader["user"]["id"];asset_id=uid();image=b"own-synthetic-image"
    paragraph=read_chapter(client,demo)["blocks"][0]
    with SessionLocal() as db:
        block=db.get(ContentBlock,paragraph["id"])
        block.html='<p>'+block.text+'</p><img data-asset="'+asset_id+'" alt="fixture">'
        source=db.get(SourceDocument,db.get(Book,demo["bookId"]).source_id)
        ref=write_private(owner,"own-source-image.png",image);source.source_map={**source.source_map,"assets":{asset_id:ref}}
        db.commit()
    world,line=start(client,reader,demo,profile)
    with SessionLocal() as db:before=deepcopy(db.get(World,world["id"]).source)
    assert delete(client,demo["bookId"]).status_code==202
    assert not private_path(owner,ref).exists()
    saved=post(client,f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",{},"publish-frozen-image")
    response=client.get(BASE+"/books/"+saved["bookId"]+"/assets/"+asset_id)
    assert response.status_code==200 and response.content==image
    job=action(client,world,line,key="source-removed-action");process(job["taskId"])
    latest=client.get(API+f"/worlds/{world['id']}/lines/{line['id']}").json()
    assert latest["head"]["sequence"]==2
    with SessionLocal() as db:
        assert db.get(World,world["id"]).source==before
        assert not db.get(Book,demo["bookId"])
        publication=db.get(SourceDocument,db.get(Book,saved["bookId"]).source_id)
        assert publication.source_map["assets"][asset_id]!=ref


def test_deleted_receipt_and_resolver_do_not_reveal_other_owner_or_unrelated_revision(client,reader,demo):
    from .test_world_security import _login_other
    owner=reader["user"]["id"];token=reader["accessToken"]
    with SessionLocal() as db:
        branch,revision,_,at=legacy_publication(db,owner,demo,uid());db.commit()
    receipt=delete(client,demo["bookId"]).json()
    invalid={"bookId":demo["bookId"],"branchId":branch.id,"revisionId":demo["revisionId"],"anchor":at}
    assert client.post(BASE+"/reading-context/resolve",json=invalid).status_code==404
    _login_other(client,"source_delete_other")
    for path in ("/books/"+demo["bookId"],"/books/"+demo["bookId"]+"/deletion","/books/"+receipt["retainedStories"][0]["bookId"]):
        assert client.get(BASE+path).status_code==404
    assert delete(client,demo["bookId"]).status_code==404
    assert client.post(BASE+"/reading-context/resolve",json={"bookId":demo["bookId"],"branchId":branch.id,"revisionId":revision.id,"anchor":at}).status_code==404
    client.headers["Authorization"]="Bearer "+token
    assert client.get(BASE+"/books/"+receipt["retainedStories"][0]["bookId"]).status_code==200


def test_maximum_title_detaches_and_0006_migration_preserves_existing_content(client,reader,demo):
    from alembic import command
    from alembic.config import Config
    from app.db import engine
    owner=reader["user"]["id"]
    with SessionLocal() as db:
        legacy_publication(db,owner,demo,uid());db.commit()
        before=[(b.id,b.text,b.content_hash) for b in db.scalars(select(ContentBlock).order_by(ContentBlock.id))]
    cfg=Config("alembic.ini");command.downgrade(cfg,"0005")
    try:
        assert client.get("/health/ready").status_code==503
        command.upgrade(cfg,"head")
        with SessionLocal() as db:
            assert [(b.id,b.text,b.content_hash) for b in db.scalars(select(ContentBlock).order_by(ContentBlock.id))]==before
            assert db.scalar(select(func.count()).select_from(BookDeletion))==0
        assert client.get("/health/ready").status_code==200
    finally:command.upgrade(cfg,"head")
    renamed=client.patch(BASE+"/books/"+demo["bookId"],json={"expectedVersion":1,"title":"书"*200})
    assert renamed.status_code==200
    response=delete(client,demo["bookId"],version=2)
    assert response.status_code==202,response.text


def test_explicit_delete_of_independent_publication_does_not_copy_or_republish_it(client,reader,demo,profile):
    world,line=start(client,reader,demo,profile)
    endpoint=f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish"
    published=post(client,endpoint,{},"published-then-remove")
    response=delete(client,published["bookId"])
    assert response.status_code==202 and response.json()["retainedStories"]==[],response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Book))==1
        assert db.get(Book,demo["bookId"]) is not None
        assert db.get(Branch,published["branchId"]) is None
    replay=client.post(API+endpoint,json={},headers={"Idempotency-Key":"published-then-remove"})
    assert replay.status_code==404,replay.text
    fresh_key=client.post(API+endpoint,json={},headers={"Idempotency-Key":"no-silent-republish"})
    assert fresh_key.status_code==404,fresh_key.text


@pytest.mark.parametrize("shape",["flow","duplicate_import"])
def test_reader_task_book_lock_covers_actual_nested_and_duplicate_contract(client,reader,demo,shape):
    from app.book_deletion import task_book
    with SessionLocal() as db:
        task=Task(owner_id=reader["user"]["id"],task_type="story_flow" if shape=="flow" else "import",
            payload={"operation":{"anchor":demo["readingAnchor"]}} if shape=="flow" else {"filename":"own.txt"},
            result={} if shape=="flow" else {"duplicateBookId":demo["bookId"]})
        db.add(task);db.flush()
        assert task_book(db,task)==demo["bookId"]


def test_received_journal_is_settled_before_delete_purges_its_private_body(client,reader,demo,profile,monkeypatch):
    from app import worker
    from app.providers import MockAdapter
    from sqlalchemy.exc import OperationalError
    from .helpers import read_chapter,observe
    at=read_chapter(client,demo)["blocks"][-1]["endAnchor"];observe(client,reader,at)
    task_id=queue_reader(client,profile,at)
    original_generate=MockAdapter.generate;original_settle=worker.settle_usage;calls=[]
    def known(adapter,p,prompt,data,limit,*args,**kwargs):
        calls.append(prompt.splitlines()[0]);result=original_generate(adapter,p,prompt,data,limit,*args,**kwargs)
        if prompt.startswith("counterfactual-"):result={**result,"usage":{"state":"known","inputTokens":7,"outputTokens":9}}
        return result
    def outage(task,call,result):
        if task==task_id and result.get("usage",{}).get("inputTokens")==7:
            raise OperationalError("controlled outage after journal",{},RuntimeError("synthetic"))
        return original_settle(task,call,result)
    with monkeypatch.context() as scope:
        scope.setattr(MockAdapter,"generate",known);scope.setattr(worker,"settle_usage",outage)
        worker.process(task_id)
    with SessionLocal() as db:
        task=db.get(Task,task_id);assert task.persistence_state=="recoverable"
        usage=db.scalar(select(UsageRecord).where(UsageRecord.task_id==task_id,UsageRecord.operation=="generation"))
        assert usage.status=="intent" and usage.input_tokens is None
        call_id=usage.provider_call_id
        journal=private_path(reader["user"]["id"],"journal-"+task_id+"-"+call_id+".json")
        assert journal.exists() and json.loads(journal.read_text("utf-8"))["result"]["usage"]["inputTokens"]==7
    response=delete(client,demo["bookId"]);assert response.status_code==202,response.text
    with SessionLocal() as db:
        usage=db.scalar(select(UsageRecord).where(UsageRecord.provider_call_id==call_id))
        assert (usage.status,usage.usage_state,usage.input_tokens,usage.output_tokens)==("completed","known",7,9)
        assert not db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id==task_id))
        assert db.get(Task,task_id).result=={"bookId":demo["bookId"],"deleted":True}
    assert not journal.exists() and len(calls)==2


@pytest.mark.parametrize("kind",["new_publication","detached_legacy"])
def test_publication_tail_remains_appendable_without_rewriting_import_root(client,reader,demo,profile,kind):
    from .helpers import observe
    from .test_story_flow import finish
    if kind=="new_publication":
        world,line=start(client,reader,demo,profile)
        saved=post(client,f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish",{},"append-publication")
    else:
        with SessionLocal() as db:
            branch,revision,_,_=legacy_publication(db,reader["user"]["id"],demo,uid());db.commit()
            saved={"bookId":demo["bookId"],"branchId":branch.id,"revisionId":revision.id}
        response=delete(client,demo["bookId"]);saved["bookId"]=response.json()["retainedStories"][0]["bookId"]
    with SessionLocal() as db:
        revision=db.get(Revision,saved["revisionId"]);block=db.get(ContentBlock,revision.manifest[-1]["blocks"][-1])
        at=make_anchor(saved["bookId"],saved["branchId"],revision.id,block,utf16_len(block.text))
        prior=db.scalar(select(Progress).where(Progress.branch_id==saved["branchId"],Progress.revision_id==revision.id))
        previous=prior.version if prior else 0
    observe(client,reader,at,expected=previous,sequence=2)
    body={"operation":"continue_at_tail","profileId":profile["id"],"anchor":at,"expectedHeadRevisionId":saved["revisionId"],"chapterCount":1}
    response=client.post(BASE+"/story-runs",json=body,headers={"Idempotency-Key":"tail-after-detach"})
    assert response.status_code==202,response.text
    run=response.json()
    with SessionLocal() as db:
        target=db.get(Task,run["id"]).payload["operation"]["target"]
        assert target["mode"]=="append_existing" and target["branchId"]==saved["branchId"]
    finish(run)
    complete=client.get(BASE+"/story-runs/"+run["id"]).json();assert complete["state"]=="succeeded",complete
    accepted=client.post(BASE+"/candidates/"+complete["candidateId"]+"/accept",json={"expectedHeadRevisionId":saved["revisionId"],
        "sourcePolicy":"require_unchanged_head","target":{"mode":"append_existing","branchId":saved["branchId"]}},headers={"Idempotency-Key":"append-publication-candidate"})
    assert accepted.status_code==200 and accepted.json()["branchId"]==saved["branchId"],accepted.text
    with SessionLocal() as db:
        assert db.get(Revision,saved["revisionId"]).manifest==revision.manifest
        if kind=="new_publication":assert db.get(Branch,demo["branchId"]).head_revision_id==demo["revisionId"]
