"""Finite PostgreSQL interleavings and accounting-only recovery boundaries."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
import json
import time
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from app.db import SessionLocal,engine
from app.domain import digest,owned
from app.models import Book,BookDeletion,Branch,ContentBlock,Task,UsageRecord,ModelProfile,User,uid
from app import book_deletion,worker
from app.providers import MockAdapter
from app.storage import private_path
from .helpers import BASE,read_chapter,observe,submit
from .test_book_deletion import delete,queue_reader,legacy_publication
from .test_world_security import isolated_storage_and_mock_only


def wait_book(book_id,owner):
    lock=int(digest("book-lifecycle:"+owner+":"+book_id)[:15],16)
    until=time.monotonic()+5
    while time.monotonic()<until:
        with SessionLocal() as db:
            found=db.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND classid=:high AND objid=:low AND NOT granted"),
                {"high":lock>>32,"low":lock&0xffffffff})
        if found:return
        time.sleep(.02)
    raise AssertionError("second real connection never waited on the book lifecycle")


def test_connection_test_with_string_operation_still_uses_actual_provider_path(client,reader,profile):
    task,_,_=submit(client,profile,{"operation":"connection_test","profileId":profile["id"]})
    assert task["state"]=="succeeded" and task["result"]=={"connected":True},task
    with SessionLocal() as db:
        assert book_deletion.task_book(db,db.get(Task,task["id"])) is None


def test_parent_flow_holds_book_before_task_and_delete_waits_without_deadlock(client,reader,demo,profile,monkeypatch):
    from app import story_flow
    at=read_chapter(client,demo)["blocks"][-1]["endAnchor"];observe(client,reader,at)
    response=client.post(BASE+"/story-runs",json={"operation":"counterfactual","anchor":at,"profileId":profile["id"],
        "expectedHeadRevisionId":at["revisionId"],"instruction":"留在原地观察","chapterCount":1},headers={"Idempotency-Key":"flow-delete-race"})
    assert response.status_code==202,response.text
    task_id=response.json()["id"];entered,release=Event(),Event();original=story_flow.validate_anchor
    def pause(db,*args,**kwargs):
        entered.set();assert release.wait(10)
        return original(db,*args,**kwargs)
    monkeypatch.setattr(story_flow,"validate_anchor",pause)
    monkeypatch.setattr(MockAdapter,"generate",lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError("parent scheduling cannot call model")))
    with ThreadPoolExecutor(max_workers=2) as pool:
        flowing=pool.submit(worker.process,task_id)
        try:
            assert entered.wait(5)
            removing=pool.submit(delete,client,demo["bookId"])
            wait_book(demo["bookId"],reader["user"]["id"])
        finally:release.set()
        flowing.result(timeout=15);removed=removing.result(timeout=15)
    assert removed.status_code==202,removed.text
    with SessionLocal() as db:
        assert db.get(Task,task_id).result=={"bookId":demo["bookId"],"deleted":True}
        assert all(t.state=="cancelled" and t.persistence_state=="saved" for t in db.scalars(select(Task)))
        assert not list(db.scalars(select(UsageRecord)))


def test_duplicate_import_confirm_enters_book_before_task_and_later_replay_is_deleted(client,reader,demo,monkeypatch):
    from app import api_books
    from app.models import SourceDocument
    with SessionLocal() as db:
        source=db.get(SourceDocument,db.get(Book,demo["bookId"]).source_id)
        data=private_path(reader["user"]["id"],source.file_ref).read_bytes()
    imported=client.post(BASE+"/books/imports",files={"file":("own.txt",data,"text/plain")},headers={"Idempotency-Key":"duplicate-race-import"})
    assert imported.status_code==202,imported.text
    task_id=imported.json()["taskId"];worker.process(task_id)
    entered,release=Event(),Event();original=api_books.owned
    def pause(db,cls,identity,owner,*args,**kwargs):
        if cls is Book and identity==demo["bookId"]:
            entered.set();assert release.wait(10)
        return original(db,cls,identity,owner,*args,**kwargs)
    body={"title":"duplicate","reuseDuplicate":True}
    monkeypatch.setattr(api_books,"owned",pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        confirming=pool.submit(client.post,BASE+"/books/imports/"+task_id+"/confirm",json=body,headers={"Idempotency-Key":"duplicate-confirm-race"})
        try:
            assert entered.wait(5)
            removing=pool.submit(delete,client,demo["bookId"])
            wait_book(demo["bookId"],reader["user"]["id"])
        finally:release.set()
        confirmed=confirming.result(timeout=15);removed=removing.result(timeout=15)
    assert confirmed.status_code==201 and confirmed.json()["bookId"]==demo["bookId"],confirmed.text
    assert removed.status_code==202,removed.text
    replay=client.post(BASE+"/books/imports/"+task_id+"/confirm",json=body,headers={"Idempotency-Key":"duplicate-confirm-race"})
    assert replay.status_code==404 and replay.json()["code"]=="BOOK_DELETED"


def test_branch_scope_refresh_precedes_row_lock_across_two_real_deletions(client,reader,demo,monkeypatch):
    from app.api_books import branch_update,Metadata
    owner=reader["user"]["id"]
    with SessionLocal() as db:
        branch,_,_,_=legacy_publication(db,owner,demo,uid());db.commit();bid=branch.id
    probed,release_patch,held_carrier,release_delete=Event(),Event(),Event(),Event()
    original_lifecycle=book_deletion.lifecycle;original_prepare=book_deletion.prepare_delete
    def pause_lifecycle(db,owner_id,book_id):
        from threading import current_thread
        if current_thread().name.startswith("branch-scope") and book_id==demo["bookId"]:
            probed.set();assert release_patch.wait(10)
        return original_lifecycle(db,owner_id,book_id)
    def patch_branch():
        with SessionLocal() as db:
            try:return branch_update(bid,Metadata(expectedVersion=1,name="late rename"),db.get(User,owner),db)
            except Exception as error:return error
    monkeypatch.setattr(book_deletion,"lifecycle",pause_lifecycle)
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix="branch-scope") as patch_pool, ThreadPoolExecutor(max_workers=1) as delete_pool:
        changing=patch_pool.submit(patch_branch)
        try:
            assert probed.wait(5)
            first=delete(client,demo["bookId"]);assert first.status_code==202,first.text
            carrier=first.json()["retainedStories"][0]["bookId"]
            def paused_prepare(db,owner_id,book_id,version):
                if book_id==carrier:
                    original_lifecycle(db,owner_id,book_id);held_carrier.set();assert release_delete.wait(10)
                return original_prepare(db,owner_id,book_id,version)
            monkeypatch.setattr(book_deletion,"prepare_delete",paused_prepare)
            second=delete_pool.submit(delete,client,carrier,1,"delete-carrier-race")
            assert held_carrier.wait(5);release_patch.set();wait_book(carrier,owner)
        finally:release_patch.set();release_delete.set()
        result=changing.result(timeout=15);removed=second.result(timeout=15)
    assert removed.status_code==202,removed.text
    assert getattr(result,"status",None)==404 and getattr(result,"code",None)=="RESOURCE_NOT_FOUND",repr(result)
    with SessionLocal() as db:assert db.get(Branch,bid) is None


@pytest.mark.parametrize("phase",["commit_before_marker","after_marker"])
def test_minimum_usage_backup_covers_deleted_receipt_db_outage_without_body(client,reader,demo,profile,monkeypatch,phase):
    at=read_chapter(client,demo)["blocks"][-1]["endAnchor"];observe(client,reader,at)
    task_id=queue_reader(client,profile,at);entered,release=Event(),Event();original=MockAdapter.generate
    receipt={"text":"KNOWN_RECEIPT_BODY","finishReason":"stop","usage":{"state":"known","inputTokens":7,"outputTokens":9}}
    def paused(adapter,p,prompt,data,limit,*args,**kwargs):
        if prompt.startswith("counterfactual-"):
            entered.set();assert release.wait(10);return deepcopy(receipt)
        return original(adapter,p,prompt,data,limit,*args,**kwargs)
    original_cleanup=book_deletion.clean_deleted_books;original_settle=worker.settle_usage
    def outage(task,call,result,**kwargs):
        if task==task_id and result["usage"].get("inputTokens")==7 and not kwargs.get("accounting_only"):
            raise OperationalError("controlled known receipt settlement outage",{},RuntimeError("synthetic"))
        return original_settle(task,call,result,**kwargs)
    monkeypatch.setattr(MockAdapter,"generate",paused);monkeypatch.setattr(worker,"settle_usage",outage)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running=pool.submit(worker.process,task_id)
        assert entered.wait(5)
        if phase=="commit_before_marker":
            def interrupted_cleanup(owner=None,book_id=None):
                release.set();running.result(timeout=10)
                return original_cleanup(owner,book_id)
            monkeypatch.setattr(book_deletion,"clean_deleted_books",interrupted_cleanup)
        response=delete(client,demo["bookId"]);assert response.status_code==202,response.text
        release.set();running.result(timeout=10)
    book_deletion.clean_received_book_usage(reader["user"]["id"])
    with SessionLocal() as db:
        row=db.scalar(select(UsageRecord).where(UsageRecord.task_id==task_id,UsageRecord.operation=="generation"))
        assert (row.status,row.usage_state,row.input_tokens,row.output_tokens)==("completed","known",7,9)
        assert db.get(Task,task_id).result=={"bookId":demo["bookId"],"deleted":True}
    folder=private_path(reader["user"]["id"],"unused").parent
    assert not list(folder.glob("journal-"+task_id+"-*")) and not list(folder.glob("book-usage-"+task_id+"-*"))


def test_cleanup_rotates_past_twenty_persistent_disk_failures(client,reader,monkeypatch):
    from pathlib import Path
    owner=reader["user"]["id"];monkeypatch.setattr(book_deletion,"_cleanup_cursor",None)
    with SessionLocal() as db:
        for index in range(21):
            db.add(BookDeletion(id=f"{index:032x}",owner_id=owner,book_id=uid(),book_version=1,retained=[],
                cleanup_manifest={"files":["blocked-file"] if index<20 else [],"taskIds":[],"receipts":[]},cleanup_state="pending"))
        db.commit()
    original=Path.unlink
    def fail(path,*args,**kwargs):
        if path.name=="blocked-file":raise OSError("controlled persistent disk fault")
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"unlink",fail)
    assert not book_deletion.clean_deleted_books()
    with SessionLocal() as db:assert db.get(BookDeletion,f"{20:032x}").cleanup_state=="pending"
    assert not book_deletion.clean_deleted_books()
    with SessionLocal() as db:assert db.get(BookDeletion,f"{20:032x}").cleanup_state=="completed"


def test_sidecar_only_disk_failure_preserves_normal_received_journal_and_generation(client,reader,demo,profile,monkeypatch):
    at=read_chapter(client,demo)["blocks"][-1]["endAnchor"];observe(client,reader,at)
    task_id=queue_reader(client,profile,at);original=book_deletion.write_private;failures=[]
    def fail(owner,name,data):
        if name.startswith("book-usage-"):
            failures.append(name);raise OSError("controlled sidecar-only failure")
        return original(owner,name,data)
    monkeypatch.setattr(book_deletion,"write_private",fail)
    worker.process(task_id)
    with SessionLocal() as db:
        task=db.get(Task,task_id)
        assert task.state=="succeeded" and task.result["candidateId"],task.error_code
        assert task.provider_state=="completed"
        assert all(u.status=="completed" for u in db.scalars(select(UsageRecord).where(UsageRecord.task_id==task_id)))
    assert failures and list(private_path(reader["user"]["id"],"unused").parent.glob("journal-"+task_id+"-*"))


def test_maintenance_does_not_steal_active_receipt_task_projection(client,reader,demo,profile,monkeypatch):
    at=read_chapter(client,demo)["blocks"][-1]["endAnchor"];observe(client,reader,at)
    task_id=queue_reader(client,profile,at);entered,release=Event(),Event();original_generate=MockAdapter.generate;original_settle=worker.settle_usage
    def known(adapter,p,prompt,data,limit,*args,**kwargs):
        result=original_generate(adapter,p,prompt,data,limit,*args,**kwargs)
        if prompt.startswith("counterfactual-"):result={**result,"usage":{"state":"known","inputTokens":7,"outputTokens":9}}
        return result
    def pause(task,call,result,**kwargs):
        if task==task_id and result["usage"].get("inputTokens")==7 and not kwargs.get("accounting_only"):
            entered.set();assert release.wait(10)
        return original_settle(task,call,result,**kwargs)
    monkeypatch.setattr(MockAdapter,"generate",known);monkeypatch.setattr(worker,"settle_usage",pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running=pool.submit(worker.process,task_id)
        try:
            assert entered.wait(5)
            book_deletion.clean_received_book_usage(reader["user"]["id"])
            with SessionLocal() as db:
                task=db.get(Task,task_id);usage=db.scalar(select(UsageRecord).where(UsageRecord.task_id==task_id,UsageRecord.operation=="generation"))
                assert task.provider_state=="running" and usage.status=="intent" and usage.input_tokens is None
        finally:release.set()
        running.result(timeout=15)
    with SessionLocal() as db:
        task=db.get(Task,task_id);usage=db.scalar(select(UsageRecord).where(UsageRecord.task_id==task_id,UsageRecord.operation=="generation"))
        assert task.state=="succeeded" and task.provider_state=="completed"
        assert (usage.status,usage.usage_state,usage.input_tokens,usage.output_tokens)==("completed","known",7,9)
