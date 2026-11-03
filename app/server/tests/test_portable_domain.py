"""Real fresh PostgreSQL round trips, separate from global reset conftest."""
import io
import base64
from copy import deepcopy
import hashlib
import json
import os
from queue import Queue
from threading import Thread
import signal
import subprocess
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from alembic import command
from alembic.config import Config
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, text

from app import models as m
from app.config import settings
from app.db import SessionLocal, engine
from app.domain import canonical, digest, make_anchor, validate_anchor
from app.errors import Problem
from app.portable.errors import ArchiveError
from app.portable.package import prepare_backup, write_backup
from app.portable.restore import restore_domain
from app.portable.snapshot import build_domain_snapshot
from app.storage import private_path, write_private
from app.world import models as w
from app.world.migrate import migrate
from app.world.schemas import WorldRules
from app.world.state import ancestry, checkpoint_hash, initial_state, public_view
from app.world.templates import template_definition
from app.portable.models import PORTABLE_TABLES, PortableBase, PortableOperation
from app.portable import service as portable_service
from app.portable.api import router as portable_router
from app.security import issue_access
from app.errors import register_errors
from app.book_deletion import prepare_delete,clean_deleted_books
from app.api_ai import router as role_router
from app.assets.models import ASSET_TABLES,AssetsBase,ImageAsset,AssetVariant,AssetPlacement
from app.assets.service import create_asset,place_asset,variant_bytes
from app.assets.schemas import PlacementInput


def test_unread_reader_avatar_private_restore_keeps_progress_and_display_guard(domain,tmp_path):
    from sqlalchemy import delete
    from app.assets.service import store_asset
    with SessionLocal() as db:
        scope={"kind":"book","bookId":domain["book"],"branchId":domain["branch"],"revisionId":domain["revision"],
               "anchor":domain["anchor"],"identityId":domain["identity"]}
        db.add(m.CharacterScopeRevision(owner_id=domain["owner"],identity_id=domain["identity"],scope_key="synthetic-future-card",
            scope_kind="reader",scope_id=domain["branch"],context={"anchor":domain["anchor"]},visible_data={"displayName":"林渡"}))
        db.flush()
        result=store_asset(db,domain["owner"],domain["image"],"image/png","avatar",{"x":0,"y":0,"width":8,"height":8},
            scope,"CC0 synthetic owned example artwork","licensed_example")
        row=place_asset(db,domain["owner"],PlacementInput(assetId=result["assetId"],scope=scope,purpose="avatar",alt="林渡"))
        db.execute(delete(m.Progress).where(m.Progress.owner_id==domain["owner"],m.Progress.book_id==domain["book"]))
        db.commit();asset_id=result["assetId"];placement_id=row["id"]
        old={item.name:item.sha256 for item in db.scalars(select(AssetVariant).where(AssetVariant.asset_id==asset_id))}
    receipt=restored(domain,tmp_path);ids=receipt["idMap"]
    with SessionLocal() as db:
        restored_asset=db.get(ImageAsset,ids[("asset",asset_id)]);placement=db.get(AssetPlacement,ids[("asset_ref",placement_id)])
        assert db.scalar(select(func.count()).select_from(m.Progress).where(m.Progress.owner_id==domain["target"]))==0
        assert restored_asset.owner_id==placement.owner_id==domain["target"]
        assert {item.name:item.sha256 for item in db.scalars(select(AssetVariant).where(AssetVariant.asset_id==restored_asset.id))}==old
        with pytest.raises(Problem) as denied:variant_bytes(db,domain["target"],restored_asset.id,"avatar128",placement.scope)
        assert denied.value.code=="AI_CONTEXT_NOT_READY"


def export_scope(domain,db,*,world=False):
    if world:
        instance=db.get(w.World,domain["world"])
        return {"kind":"world","worldId":instance.id,"lineId":instance.active_line_id,"checkpointId":domain["checkpoint"]}
    branch=db.scalar(select(m.Branch).where(m.Branch.book_id==domain["book"],m.Branch.parent_branch_id==domain["branch"]))
    return {"kind":"book","bookId":domain["book"],"branchId":branch.id,"revisionId":branch.head_revision_id}


def test_reading_capabilities_are_independent_and_do_not_allocate_jobs(domain):
    with http_client(domain["owner"]) as client:
        value=client.get("/api/reader/v1/exports/capabilities")
        assert value.status_code==200
        assert value.json()=={"protocol":"reader-export-v1","ready":True,"formats":["txt","epub"],
            "defaultRange":"observed","selectedUnseenRequiresConfirmation":True,"maxBytes":2*1024**3}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PortableOperation).where(PortableOperation.owner_id==domain["owner"]))==0


@pytest.mark.parametrize("format",["txt","epub"])
def test_public_reading_export_real_http_order_provenance_images_and_no_ui_badges(domain,tmp_path,format):
    from xml.etree import ElementTree as ET
    from zipfile import ZipFile,ZIP_STORED
    with SessionLocal() as db:scope=export_scope(domain,db)
    with http_client(domain["owner"]) as client:
        data={"format":format,"scope":scope,"range":"selected","startChapter":1,"endChapter":2,"allowUnseen":True}
        response=client.post("/api/reader/v1/exports",json=data,headers={"Idempotency-Key":"public-export"})
        assert response.status_code==201,response.text
        job=response.json();assert job["protocol"]=="reader-export-v1" and job["chapterCount"]==2 and job["imageCount"]==1
        body=client.get("/api/reader/v1/exports/"+job["id"]+"/download")
        assert body.status_code==200 and digest(body.content)==job["sha256"] and len(body.content)==job["size"]
        if format=="txt":
            text=body.content.decode();assert text.index("第一章 灯下")<text.index("第二章 来信")
            assert domain["prose"] in text and "林渡回到灯下。" in text
            assert "第一章 灯下：原文" in text and "第二章 来信：故事创作" in text and "图片与授权" in text
            assert "AI 生成 · 已接受" not in text and "data-asset" not in text
        else:
            with ZipFile(io.BytesIO(body.content)) as archive:
                first=archive.infolist()[0];assert first.filename=="mimetype" and first.compress_type==ZIP_STORED
                assert archive.read("mimetype")==b"application/epub+zip"
                ns={"o":"http://www.idpf.org/2007/opf","x":"http://www.w3.org/1999/xhtml"}
                package=ET.fromstring(archive.read("EPUB/package.opf"));items={item.attrib["id"]:item.attrib for item in package.findall("o:manifest/o:item",ns)}
                ids=[item.attrib["idref"] for item in package.findall("o:spine/o:itemref",ns)]
                assert ids==["chapter-00001","chapter-00002","sources"]
                for item in items.values():assert "EPUB/"+item["href"] in archive.namelist()
                for name in archive.namelist():
                    if name.endswith(("xhtml","opf","xml")):ET.fromstring(archive.read(name))
                text="".join("".join(ET.fromstring(archive.read("EPUB/"+items[key]["href"])).itertext()) for key in ids)
                assert domain["prose"] in text and "林渡回到灯下。" in text and "AI 生成 · 已接受" not in text
                assert archive.read("EPUB/images/image-0001.png").startswith(b"\x89PNG")
                assert b"<script" not in b"".join(archive.read(name) for name in archive.namelist() if name.endswith("xhtml"))


def test_observed_export_stops_at_exact_utf16_without_future_paragraph_or_image(domain,tmp_path):
    from app.domain import utf16_len
    with SessionLocal() as db:
        progress=db.scalar(select(m.Progress).where(m.Progress.owner_id==domain["owner"],m.Progress.revision_id==domain["revision"]))
        block=db.get(m.ContentBlock,domain["block"])
        prefix=block.text[:8];progress.max_observed_anchor=make_anchor(domain["book"],domain["branch"],domain["revision"],block,utf16_len(prefix),utf16_len(prefix))
        progress.anchor=progress.max_observed_anchor;db.commit()
    with http_client(domain["owner"]) as client:
        scope={"kind":"book","bookId":domain["book"],"branchId":domain["branch"],"revisionId":domain["revision"]}
        data={"format":"txt","scope":scope}
        response=client.post("/api/reader/v1/exports",json=data,headers={"Idempotency-Key":"observed-export"})
        assert response.status_code==201,response.text
        job=response.json();assert job["imageCount"]==0
        text=client.get("/api/reader/v1/exports/"+job["id"]+"/download").content.decode()
        assert prefix in text and domain["prose"] not in text and "仍有独立位置" not in text
        response=client.post("/api/reader/v1/exports",json={**data,"range":"selected","startChapter":1,"endChapter":1},headers={"Idempotency-Key":"must-confirm-unseen"})
        assert response.status_code==409 and response.json()["code"]=="EXPORT_UNSEEN_CONFIRMATION_REQUIRED"
    with SessionLocal() as db:
        assert db.scalar(select(m.Progress).where(m.Progress.owner_id==domain["owner"],m.Progress.revision_id==domain["revision"])).max_observed_anchor["end"]==utf16_len(prefix)


def test_world_public_export_ancestry_order_excludes_private_state_and_other_line(domain,tmp_path):
    with SessionLocal() as db:
        scope=export_scope(domain,db,world=True)
        checkpoint=db.get(w.WorldCheckpoint,domain["checkpoint"])
        checkpoint.summary="PRIVATE_SUMMARY_NOT_PUBLIC_EXPORT"
        checkpoint.memory={"private":"PRIVATE_MEMORY_NOT_PUBLIC_EXPORT"};db.commit()
    with http_client(domain["owner"]) as client:
        data={"format":"txt","scope":scope,"range":"selected","startChapter":1,"endChapter":2,"allowUnseen":True}
        response=client.post("/api/reader/v1/exports",json=data,headers={"Idempotency-Key":"world-public"})
        assert response.status_code==201,response.text
        text=client.get("/api/reader/v1/exports/"+response.json()["id"]+"/download").content.decode()
        assert text.index("灯亮了。")<text.index("信封摆在桌上。")
        assert "雨还在下。" not in text and "PRIVATE_" not in text and "playerId" not in text


def test_public_export_idempotency_pure_lookup_owner_expiry_and_source_revocation(domain,tmp_path):
    from app.portable.reading_export import download_reading
    with SessionLocal() as db:scope=export_scope(domain,db)
    with http_client(domain["owner"]) as client,http_client(domain["stranger"]) as stranger:
        data={"format":"txt","scope":scope,"range":"selected","startChapter":1,"endChapter":2,"allowUnseen":True}
        first=client.post("/api/reader/v1/exports",json=data,headers={"Idempotency-Key":"stable-public-key"});assert first.status_code==201,first.text
        job=first.json();assert client.post("/api/reader/v1/exports",json=data,headers={"Idempotency-Key":"stable-public-key"}).json()==job
        assert client.get("/api/reader/v1/exports/requests?requestId=stable-public-key").json()=={"found":True,"job":job}
        assert stranger.get("/api/reader/v1/exports/"+job["id"]).status_code==404
        assert stranger.get("/api/reader/v1/exports/"+job["id"]+"/download").status_code==404
        assert stranger.get("/api/reader/v1/exports/requests?requestId=stable-public-key").json()=={"found":False,"job":None}
        with SessionLocal() as db:
            row=db.get(PortableOperation,job["id"]);row.expires_at=m.now()-timedelta(seconds=1);db.commit()
        assert client.get("/api/reader/v1/exports/"+job["id"]+"/download").status_code==409
        with SessionLocal() as db:
            row=db.get(PortableOperation,job["id"]);row.expires_at=m.now()+timedelta(minutes=1)
            db.delete(db.get(m.Branch,scope["branchId"]));db.commit()
        assert client.get("/api/reader/v1/exports/"+job["id"]+"/download").status_code==404
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PortableOperation).where(PortableOperation.owner_id==domain["owner"],PortableOperation.kind=="reading_export"))==1


def test_executable_inline_export_is_rejected_without_ready_file_or_prose_change(domain,tmp_path):
    with SessionLocal() as db:
        scope=export_scope(domain,db)
        block=db.get(m.ContentBlock,domain["block"]);original=block.text
        block.html="<p>"+block.text+"<script>private()</script></p>";db.commit()
    with http_client(domain["owner"]) as client:
        response=client.post("/api/reader/v1/exports",json={"format":"epub","scope":scope,"range":"selected","startChapter":1,"endChapter":2,"allowUnseen":True},headers={"Idempotency-Key":"unsafe-inline"})
        assert response.status_code==422 and response.json()["code"]=="EXPORT_INLINE_INVALID",response.text
        receipt=client.get("/api/reader/v1/exports/requests?requestId=unsafe-inline").json()
        assert receipt["job"]["status"]=="failed"
    with SessionLocal() as db:
        assert db.get(m.ContentBlock,domain["block"]).text==original
        operation=db.scalar(select(PortableOperation).where(PortableOperation.owner_id==domain["owner"],PortableOperation.request_key=="unsafe-inline"))
        assert not portable_service.stage_path(domain["owner"],operation.stage_name,exists=False).exists()


@pytest.mark.parametrize("mode",["before","after","probe-unavailable"])
def test_public_export_real_pg_commit_ack_preserves_durable_file_or_unknown_stage(domain,tmp_path,monkeypatch,mode):
    from app.portable import reading_export as exports
    from app.portable.reading_export import ReadingExportInput,export_reading,download_reading
    with SessionLocal() as db:
        scope=export_scope(domain,db)
        data=ReadingExportInput.model_validate({"format":"epub","scope":scope,"range":"selected","startChapter":1,"endChapter":2,"allowUnseen":True})
        fired=False
        def fault(session):
            nonlocal fired
            if not fired and any(isinstance(row,PortableOperation) and row.status=="ready" for row in session.identity_map.values()):
                fired=True;raise ConnectionError("synthetic commit acknowledgment lost")
        event.listen(db,"before_commit" if mode=="before" else "after_commit",fault)
        original=exports.SessionLocal;opens=0
        if mode=="probe-unavailable":
            def probe_fault():
                nonlocal opens
                opens+=1
                if opens>1:raise ConnectionError("synthetic fresh probe unavailable")
                return original()
            monkeypatch.setattr(exports,"SessionLocal",probe_fault)
        if mode=="after":
            result=export_reading(db,domain["owner"],"commit-ack",data)
            assert result["status"]=="ready"
        else:
            with pytest.raises(Problem if mode=="probe-unavailable" else ConnectionError) as caught:
                export_reading(db,domain["owner"],"commit-ack",data)
            if mode=="probe-unavailable":assert caught.value.code=="EXPORT_RESPONSE_UNKNOWN"
        assert fired
        event.remove(db,"before_commit" if mode=="before" else "after_commit",fault)
        monkeypatch.setattr(exports,"SessionLocal",original)
    with SessionLocal() as db:
        row=db.scalar(select(PortableOperation).where(PortableOperation.owner_id==domain["owner"],PortableOperation.request_key=="commit-ack"))
        root=portable_service.stage_path(domain["owner"],row.stage_name,exists=False)
        if mode=="before":
            assert row.status=="failed" and not root.exists()
        else:
            assert row.status=="ready" and root.is_dir()
            file,result=download_reading(db,domain["owner"],row.id)
            assert digest(file.read_bytes())==result["sha256"]
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id==domain["owner"]))==0


@pytest.mark.parametrize("range",["observed","selected"])
def test_world_earlier_snapshot_is_read_when_same_line_progress_is_later(domain,tmp_path,range):
    with SessionLocal() as db:
        scope=export_scope(domain,db,world=True)
        line=db.get(w.WorldLine,scope["lineId"])
        line.reading_position={"checkpointId":domain["checkpoint"],"scroll":12};db.commit()
        scope["checkpointId"]=domain["root_checkpoint"]
    with http_client(domain["owner"]) as client:
        data={"format":"txt","scope":scope,"range":range}
        if range=="selected":data.update(startChapter=1,endChapter=1)
        response=client.post("/api/reader/v1/exports",json=data,headers={"Idempotency-Key":"earlier-read-checkpoint"})
        assert response.status_code==201,response.text
        text=client.get("/api/reader/v1/exports/"+response.json()["id"]+"/download").content.decode()
        assert "灯亮了。" in text and "信封摆在桌上。" not in text
    with SessionLocal() as db:
        line=db.get(w.WorldLine,scope["lineId"])
        assert line.head_checkpoint_id==domain["checkpoint"] and line.reading_position["checkpointId"]==domain["checkpoint"]


@pytest.fixture(scope="session", autouse=True)
def migrate_owned_database():
    from owned_test_runtime import require_owned_runtime
    require_owned_runtime("portable_test", "/tmp/portable-private")
    # New listening-copy fixtures execute the actual additive migration chain.
    # Earlier sealed reader0009 batches continue to use their original schema.
    command.upgrade(Config("alembic.ini"), "head")
    migrate()
    # Explicit fixture-only DDL; production remains on its additive migration
    # chain and main has not registered this new route yet.
    PortableBase.metadata.create_all(engine,tables=PORTABLE_TABLES)
    AssetsBase.metadata.create_all(engine,tables=ASSET_TABLES)


@pytest.fixture
def domain(tmp_path):
    with SessionLocal() as db:
        owners = [m.User(id=m.uid(), username="portable_" + m.uid()[:16], password_hash="synthetic-not-a-secret") for _ in range(3)]
        db.add_all(owners); db.flush(); owner, target, stranger = [row.id for row in owners]
        db.add_all([m.UserSettings(owner_id=row.id) for row in owners]);db.flush()
        book, source, branch, revision, second, rev2, chap1, chap2, block1, block2 = [m.uid() for _ in range(10)]
        prose = "林渡把信交给另一位林渡。🙂同一段话再次出现，仍有独立位置。"
        continuation = "林渡回到灯下。🙂同一段话再次出现，仍有独立位置。"
        img = io.BytesIO(); Image.new("RGB", (8, 12), "green").save(img, format="PNG"); raw = img.getvalue(); asset = m.uid()
        write_private(owner, source + ".txt", prose.encode()); write_private(owner, asset + ".png", raw)
        b = m.Book(id=book, owner_id=owner, title="原创新故事", source_hash=digest(prose), source_id=source, original_branch_id=branch, original_revision_id=revision)
        db.add(b); db.flush()
        br = m.Branch(id=branch, owner_id=owner, book_id=book, name="原文", head_revision_id=revision)
        other = m.Branch(id=second, owner_id=owner, book_id=book, parent_branch_id=branch, name="另一条线", head_revision_id=rev2)
        db.add_all([br, other]); db.flush()
        manifest = [{"id": chap1, "title": "第一章 灯下", "blocks": [block1]}]
        manifest2 = manifest + [{"id": chap2, "title": "第二章 来信", "blocks": [block2]}]
        r = m.Revision(id=revision, owner_id=owner, branch_id=branch, manifest=manifest, content_hash=digest(canonical(manifest)), provenance={"operation": "import"})
        r2 = m.Revision(id=rev2, owner_id=owner, branch_id=second, manifest=manifest2, content_hash=digest(canonical(manifest2)), provenance={"operation": "continue", "parentRevisionId": revision})
        p = m.ContentBlock(id=block1, owner_id=owner, book_id=book, chapter_id=chap1, text=prose, html=f'<p>林渡把信交给另一位<strong>林渡</strong>。🙂同一段话再次出现，仍有独立位置。<img data-asset="{asset}" alt="灯下的信"/></p>', content_hash=digest(prose), source="original")
        p2 = m.ContentBlock(id=block2, owner_id=owner, book_id=book, chapter_id=chap2, text=continuation, content_hash=digest(continuation), source="ai_accepted")
        db.add_all([r, r2, p, p2]); db.flush()
        anchor = make_anchor(book, branch, revision, p, len(prose.encode("utf-16-le"))//2, len(prose.encode("utf-16-le"))//2)
        selected = make_anchor(book, branch, revision, p, 0, 2)
        other.fork_anchor = anchor
        task = m.Task(id=m.uid(), owner_id=owner, task_type="continue", state="succeeded", payload={"bookId": book, "anchor": anchor}, result={"bookId":book,"branchId":second,"revisionId":rev2}, persistence_state="saved")
        interrupted = m.Task(id=m.uid(), owner_id=owner, task_type="continue", state="queued", payload={"bookId":book,"anchor":anchor}, checkpoint={"calls": {"raw-private-journal": {"callId":m.uid()}}}, result={})
        db.add_all([task,interrupted]); db.flush()
        source_map = {"blocks": {block1:{"chapter":0,"paragraph":0}}, "assets":{asset:asset+".png"}, "characterBindings":[]}
        identities = []
        for index in range(2):
            identity = m.CharacterIdentity(id=m.uid(), owner_id=owner, origin_key="synthetic:"+m.uid(), source_ref={"kind":"reader-proof","bookId":book,"branchId":branch,"blockId":block1,"start":index*9,"originalLabel":"林渡"})
            identities.append(identity); db.add(identity)
        db.flush()
        for index, identity in enumerate(identities):
            db.add(m.CharacterAlias(id=m.uid(), owner_id=owner, identity_id=identity.id, scope_kind="reader", scope_id=branch, entity_id="local-"+str(index)))
            source_map["characterBindings"].append({"identityId":identity.id,"blockId":block1,"start":index*9,"name":"林渡"})
        src = m.SourceDocument(id=source, owner_id=owner, book_id=book, file_hash=digest(prose), file_ref=source+".txt", normalized_ref=source+".txt", format="txt", encoding="utf-8", source_map=source_map)
        ks = m.KnowledgeSnapshot(id=m.uid(), owner_id=owner, book_id=book, branch_id=branch, revision_id=revision, boundary=anchor, scope_hash=digest("synthetic"), profile_version="synthetic-v1", completeness="complete", data={"characters":[{"id":"local-0","displayName":"林渡","identityProofVersion":2,"evidenceIds":["e1"]}], "evidence":[{"id":"e1","quote":"林渡","anchor":selected}]})
        ks.data={**ks.data,"identityProofVersion":2,"facts":[],"summary":"灯下收到一封信。","unresolved":[],"style":{}}
        session = m.RoleSession(id=m.uid(), owner_id=owner, book_id=book, branch_id=branch, pinned_revision_id=revision, pinned_anchor=anchor, character_id="local-0", character_name="林渡", character_identity_id=identities[0].id, snapshot_id=ks.id)
        db.add_all([src,ks,session]); db.flush()
        db.add_all([m.RoleMessage(id=m.uid(),owner_id=owner,session_id=session.id,turn_id="old-local-turn",sequence=1,role="assistant",text="我记得灯下的约定。",task_id=task.id),
                    m.Candidate(id=m.uid(),owner_id=owner,task_id=task.id,text=continuation,content_hash=digest(continuation),validation_status="passed",outcome="accepted",status="accepted",accepted_revision_id=rev2,accept_result={"branchId":second,"revisionId":rev2}),
                    m.TaskEvent(id=m.uid(),task_id=task.id,seq=1,type="result_saved",payload={"text":continuation,"revisionId":rev2}),
                    m.Progress(id=m.uid(),owner_id=owner,book_id=book,branch_id=branch,revision_id=revision,anchor=anchor,max_observed_anchor=anchor,version=8),
                    m.Bookmark(id=m.uid(),owner_id=owner,book_id=book,branch_id=branch,revision_id=revision,anchor=selected,note="保留私人笔记🙂",version=3),
                    m.ResumeTarget(id=m.uid(),owner_id=owner,story_id="book-"+book,mode="read",context={"storyId":"book-"+book,"mode":"read","bookId":book,"readerBranchId":branch,"anchor":anchor,"perspective":"reader","entryOrigin":"library"},version=6)])
        definition = template_definition("original-mystery-v1",WorldRules().model_dump())
        actor = next(item for item in definition["entities"] if item["type"]=="character")
        definition, state = initial_state(definition,actor["id"])
        wid, lid, lid2, cp, cp2, cp3 = [m.uid() for _ in range(6)]
        frozen = {"kind":"book","anchor":anchor,"title":b.title,"contentHash":digest(prose),"blocks":[{"blockId":block1,"chapterId":chap1,"text":prose,"html":p.html,"source":"original"}],"chapters":[{"id":chap1,"title":"第一章 灯下","blocks":[{"blockId":block1,"chapterId":chap1,"text":prose}]}],"referenceBlocks":[],"assets":{asset:base64.b64encode(raw).decode("ascii")},"characterRefs":[{"identityId":identities[0].id,"blockId":block1,"start":0,"originalLabel":"林渡","name":"林渡","entityId":"local-0"}]}
        world = w.World(id=wid,owner_id=owner,name="灯下世界",source_book_id=book,source_title=b.title,source=frozen,definition=definition,status="active",active_line_id=lid)
        db.add(world); db.flush()
        lines = [w.WorldLine(id=lid,owner_id=owner,world_id=wid,name="主线",head_checkpoint_id=cp2),w.WorldLine(id=lid2,owner_id=owner,world_id=wid,name="另一选择",parent_line_id=lid,fork_checkpoint_id=cp,head_checkpoint_id=cp3)]
        db.add_all(lines); db.flush()
        points = [w.WorldCheckpoint(id=cp,owner_id=owner,world_id=wid,line_id=lid,sequence=1,title="起点",text="灯亮了。",state=state,definition=definition,content_hash=checkpoint_hash(state,definition,"灯亮了。",None),kind="start"),
                  w.WorldCheckpoint(id=cp2,owner_id=owner,world_id=wid,line_id=lid,parent_checkpoint_id=cp,sequence=2,title="来信",text="信封摆在桌上。",action="读信",state=deepcopy(state),definition=definition,content_hash=checkpoint_hash(state,definition,"信封摆在桌上。",cp)),
                  w.WorldCheckpoint(id=cp3,owner_id=owner,world_id=wid,line_id=lid2,parent_checkpoint_id=cp,sequence=2,title="出门",text="雨还在下。",action="出门",state=deepcopy(state),definition=definition,content_hash=checkpoint_hash(state,definition,"雨还在下。",cp))]
        db.add_all(points); db.flush()
        event_token=digest(cp2+":0")[:24]
        event = w.WorldEvent(id=m.uid(),owner_id=owner,world_id=wid,checkpoint_id=cp2,entity_id=actor["id"],type="heard",data={"id":event_token,"text":"约定"},visible_to=[actor["id"]],minute=1)
        db.add(event);db.flush()
        points[1].memory={"episodes":[{"firstCheckpointId":cp,"lastCheckpointId":cp2,"text":"读信的约定","eventIds":[event_token]}]}
        db.add_all([w.WorldMemory(id=m.uid(),owner_id=owner,world_id=wid,line_id=lid,checkpoint_id=cp2,first_checkpoint_id=cp,last_checkpoint_id=cp2,level=1,text="你在灯下读信。",event_ids=[event_token]),
                    m.CharacterAlias(id=m.uid(),owner_id=owner,identity_id=identities[0].id,scope_kind="world",scope_id=wid,entity_id=actor["id"])])
        db.commit()
        result={"owner":owner,"target":target,"stranger":stranger,"book":book,"world":wid,"branch":branch,"revision":revision,"anchor":anchor,"prose":prose,"task":task.id,"interrupted":interrupted.id,"identity":identities[0].id,"identities":[i.id for i in identities],"checkpoint":cp2,"fork":cp3,"root_checkpoint":cp,"block":block1,"asset":asset,"image":raw}
    yield result
    # These are deliberately seeded historical, never dispatched tasks in this
    # owned project. End only our original interrupted fixture, never other work.
    with SessionLocal() as db:
        old=db.get(m.Task,result["interrupted"])
        if old: old.state="cancelled"; old.persistence_state="saved"
        db.commit()


def encrypted(domain, tmp_path, *, world=False, include_source=False):
    output=tmp_path/"private.srbackup"
    with SessionLocal() as db:
        db.connection(execution_options={"isolation_level":"REPEATABLE READ"})
        with build_domain_snapshot(db,domain["owner"],("world-" if world else "book-")+domain["world" if world else "book"],tmp_path/"snapshot",include_source=include_source) as snapshot:
            with output.open("wb") as stream: write_backup(snapshot.root,snapshot.manifest,stream,"synthetic-test-password",tmp_path/"export")
    return output


def seed_public_stream_history(domain, kind):
    """Seed display-only terminal history; no provider request or worker claim."""
    with SessionLocal() as db:
        tid=m.uid();text="已收到的公开正文🙂，保留尚未保存的一段。"
        slot={"contentId":digest(tid+":"+("reply" if kind=="role" else "generation:scene:1"))[:32],"chapterIndex":1,"streamVersion":2,
              "snapshotVersion":9,"version":9,"text":text,"contentHash":digest(text),
              "status":"interrupted","durability":"journaled"}
        if kind=="role":
            session=db.scalar(select(m.RoleSession).where(m.RoleSession.owner_id==domain["owner"]))
            slot.update(sessionId=session.id,turnId="opaque-reader-request",userMessage="请谈谈这封信。")
            payload={"sessionId":session.id,"turnId":slot["turnId"],"bookId":domain["book"]};key="replyStream";task_type="role_chat"
        else:
            line=db.scalar(select(w.WorldLine).where(w.WorldLine.world_id==domain["world"]))
            slot["stage"]="generation:scene:1";payload={"worldId":domain["world"],"lineId":line.id,"chapterCount":1}
            key="worldStream";task_type="world_autoplay"
        task=m.Task(id=tid,owner_id=domain["owner"],task_type=task_type,state="failed",payload=payload,
                    checkpoint={key:slot,"calls":{"private-raw-journal":{"callId":m.uid()}},"privateDirector":{"secret":"not-exported"}},
                    error_code="PROVIDER_STATUS_UNKNOWN",provider_state="unknown",persistence_state="partial",event_seq=1)
        db.add(task);db.flush()
        db.add(m.TaskEvent(id=m.uid(),task_id=tid,seq=1,type="text_delta",payload={"contentId":slot["contentId"],
            "chapterIndex":1,"streamVersion":2,"startUtf16":0,"delta":text,"durability":"journaled"}))
        db.commit()
        return tid,key,deepcopy(slot)


@pytest.mark.parametrize("kind",["role","world"])
def test_public_stream_history_encrypted_copy_and_second_copy_keep_scoped_body_without_replaying(domain,tmp_path,kind):
    tid,key,source=seed_public_stream_history(domain,kind)
    original=encrypted(domain,tmp_path,world=kind=="world")
    with original.open("rb") as raw,prepare_backup(raw,"synthetic-test-password",tmp_path/"stream-import") as prepared:
        assert not any("journal-stream-" in file["path"] for file in prepared.manifest["files"])
        with SessionLocal() as db:first=restore_domain(db,prepared,domain["target"])
    mapped=first["idMap"][("task",tid)];content=first["idMap"][("stream_content",source["contentId"])]
    with SessionLocal() as db:
        task=db.get(m.Task,mapped);stream=task.checkpoint[key]
        assert stream["text"]==source["text"] and stream["contentHash"]==digest(source["text"])
        assert stream["historical"] is True and stream["durability"]=="unconfirmed"
        assert stream["contentId"]==content and (stream["streamVersion"],stream["snapshotVersion"])==(2,9)
        assert "calls" not in task.checkpoint and "privateDirector" not in task.checkpoint
        assert task.cancel_requested and task.state=="cancelled" and task.provider_state=="not_started" and task.payload["importedHistory"] is True
        event=db.scalar(select(m.TaskEvent).where(m.TaskEvent.task_id==mapped));assert event.payload["contentId"]==content
        if kind=="role":assert stream["sessionId"]==first["idMap"][("role_session",source["sessionId"])] and stream["turnId"]==source["turnId"]
    second_domain={**domain,"owner":domain["target"],"book":first["idMap"].get(("book",domain["book"])),
                   "world":first["idMap"].get(("world",domain["world"]))}
    second_root=tmp_path/"second";second_root.mkdir()
    second=encrypted(second_domain,second_root,world=kind=="world")
    with second.open("rb") as raw,prepare_backup(raw,"synthetic-test-password",tmp_path/"second-import") as prepared:
        with SessionLocal() as db:receipt=restore_domain(db,prepared,domain["stranger"])
    with SessionLocal() as db:
        copied=db.get(m.Task,receipt["idMap"][("task",mapped)])
        assert copied.checkpoint[key]["text"]==source["text"] and copied.checkpoint[key]["contentId"]==receipt["idMap"][("stream_content",content)]
        for owner in (domain["target"],domain["stranger"]):
            for model in (m.ModelProfile,m.Consent,m.OperationPlan,m.UsageRecord):assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==owner))==0


def seed_received_world_turn(domain,tid,*,chapters=False):
    with SessionLocal() as db:
        task=db.get(m.Task,tid);line=db.get(w.WorldLine,task.payload['lineId']);cp=db.get(w.WorldCheckpoint,line.head_checkpoint_id)
        turn_id=m.uid();task.payload={**task.payload,'turnId':turn_id};task.task_type='world_write'
        result={}
        if chapters:
            scene={'id':m.uid(),'text':'第一章已收到的完整正文。','plan':{'title':'灯下','options':[]},
                'changes':[],'events':[],'state':deepcopy(cp.state),'definition':deepcopy(cp.definition)}
            scene['contentHash']=digest(canonical(scene));result['chapters']=[scene]
            task.payload={**task.payload,'chapterCount':2}
            value={**task.checkpoint['worldStream'],'chapterIndex':2,'stage':'generation:scene:2',
                   'contentId':digest(tid+':generation:scene:2')[:32]}
            task.checkpoint={**task.checkpoint,'worldStream':value}
            event=db.scalar(select(m.TaskEvent).where(m.TaskEvent.task_id==tid))
            event.payload={**event.payload,'contentId':value['contentId'],'chapterIndex':2}
        row=w.WorldTurn(id=turn_id,owner_id=domain['owner'],world_id=domain['world'],line_id=line.id,
            task_id=tid,parent_checkpoint_id=cp.id,turn_key='owned-stream-history-'+tid,
            action='再看一封信',status='draft',draft_text='',result=result)
        db.add(row);db.commit();return turn_id,line.id


@pytest.mark.parametrize('mode',['role','role-saved','role-partial','world','world-multi'])
def test_encrypted_received_history_is_discoverable_by_actual_http_and_second_copy_without_replay(domain,tmp_path,monkeypatch,mode):
    kind='role' if mode.startswith('role') else 'world'
    tid,key,source=seed_public_stream_history(domain,kind)
    turn_id=line_id=None
    with SessionLocal() as db:
        if kind=='role' and mode!='role':
            db.add(m.RoleMessage(owner_id=domain['owner'],session_id=source['sessionId'],turn_id=source['turnId'],
                sequence=2,role='assistant',text='正式已保存回复。' if mode=='role-saved' else '较早收到的片段。',
                task_id=tid,status='saved' if mode=='role-saved' else 'partial'));db.commit()
    if kind=='world':turn_id,line_id=seed_received_world_turn(domain,tid,chapters=mode=='world-multi')
    first=restored(domain,tmp_path,world=kind=='world');ids=first['idMap']
    second_domain={**domain,'owner':domain['target'],'book':ids.get(('book',domain['book'])),'world':ids.get(('world',domain['world']))}
    second_root=tmp_path/'readable-second';second_root.mkdir();archive=encrypted(second_domain,second_root,world=kind=='world')
    with archive.open('rb') as raw,prepare_backup(raw,'synthetic-test-password',tmp_path/'readable-second-import') as prepared:
        with SessionLocal() as db:second=restore_domain(db,prepared,domain['stranger'])
    def forbidden(*args,**kwargs):pytest.fail('Imported display-only receipts must not read or overlay executable journals')
    monkeypatch.setattr('app.durable_stream.overlay',forbidden);monkeypatch.setattr('app.durable_stream.read',forbidden)
    for owner,mapping,prior in ((domain['target'],ids,None),(domain['stranger'],second['idMap'],ids)):
        remap=lambda k,i:mapping[(k,prior[(k,i)] if prior else i)]
        with SessionLocal() as db:
            tasks_before=[(t.id,t.state,t.event_seq,deepcopy(t.checkpoint)) for t in db.scalars(select(m.Task).where(m.Task.owner_id==owner))]
            messages_before=[(t.id,t.status,t.text,t.sequence) for t in db.scalars(select(m.RoleMessage).where(m.RoleMessage.owner_id==owner))]
            heads_before=[(t.id,t.head_checkpoint_id,t.pending_task_id) for t in db.scalars(select(w.WorldLine).where(w.WorldLine.owner_id==owner))]
        with http_client(owner) as client:
            if kind=='role':
                sid=remap('role_session',source['sessionId']);root='/api/reader/v1/role-sessions/'+sid
                current=client.get(root);assert current.status_code==200,current.text
                assert current.json()['receivedHistory'] is True and not current.json()['pendingTaskId']
                receipt=client.get(root+'/received-replies');assert receipt.status_code==200,receipt.text
                received=receipt.json()['items'];assert len(received)==(0 if mode=='role-saved' else 1)
                if received:
                    assert received[0]['text']==source['text'] and received[0]['historical'] is True
                    assert received[0]['durability']=='unconfirmed' and received[0]['sessionId']==sid
                task=client.get('/api/reader/v1/tasks/'+remap('task',tid));assert task.status_code==200,task.text
                assert task.json()['replyStream']['text']==source['text']
                messages=client.get(root+'/messages');assert messages.status_code==200,messages.text
                if mode=='role-partial':assert any(x['text']=='较早收到的片段。' and x['status']=='partial' for x in messages.json()['items'])
                if mode=='role-saved':assert any(x['text']=='正式已保存回复。' and x['status']=='saved' for x in messages.json()['items'])
            else:
                wid=remap('world',domain['world']);lid=remap('world_line',line_id);root='/api/reader/v1/worlds/'+wid
                drafts=client.get(root+'/lines/'+lid+'/drafts');assert drafts.status_code==200,drafts.text
                assert len(drafts.json()['items'])==1;item=drafts.json()['items'][0]
                assert item['status']=='historical' and item['draftText']==source['text'] and item['checkpointId'] is None
                assert len(item['chapters'])==(1 if mode=='world-multi' else 0)
                if item['chapters']:assert item['chapters'][0]['text']=='第一章已收到的完整正文。'
                detail=client.get(root+'/turns/'+remap('world_turn',turn_id));assert detail.status_code==200,detail.text
                assert detail.json()==item
                # Final projection uses the actual world endpoint function; its
                # /tasks path overlaps the reader router in this minimal ASGI app.
                from app.world.jobs import task_view
                with SessionLocal() as db:assert task_view(db.get(m.Task,remap('task',tid)))['stream']['text']==source['text']
                denied=client.post(root+'/turns/'+item['id']+'/accept',json={'chapterCount':1,'expectedCheckpointId':item['parentCheckpointId']},headers={'Idempotency-Key':'historical-readonly-'+mode})
                assert denied.status_code==409,denied.text
        with SessionLocal() as db:
            assert tasks_before==[(t.id,t.state,t.event_seq,deepcopy(t.checkpoint)) for t in db.scalars(select(m.Task).where(m.Task.owner_id==owner))]
            assert messages_before==[(t.id,t.status,t.text,t.sequence) for t in db.scalars(select(m.RoleMessage).where(m.RoleMessage.owner_id==owner))]
            assert heads_before==[(t.id,t.head_checkpoint_id,t.pending_task_id) for t in db.scalars(select(w.WorldLine).where(w.WorldLine.owner_id==owner))]
            for model in (m.ModelProfile,m.Consent,m.OperationPlan,m.UsageRecord):assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==owner))==0


@pytest.mark.parametrize('kind',['role','world'])
def test_encrypted_received_history_actual_http_paginates_older_than_twenty_and_denies_foreign_owner(domain,tmp_path,kind):
    source_tasks=[];source_turns=[];slot=None;line_id=None
    for _ in range(21):
        tid,_,slot=seed_public_stream_history(domain,kind);source_tasks.append(tid)
        if kind=='world':
            turn_id,line_id=seed_received_world_turn(domain,tid);source_turns.append(turn_id)
    result=restored(domain,tmp_path,world=kind=='world');ids=result['idMap']
    if kind=='role':root='/api/reader/v1/role-sessions/'+ids[('role_session',slot['sessionId'])]+'/received-replies'
    else:root='/api/reader/v1/worlds/'+ids[('world',domain['world'])]+'/lines/'+ids[('world_line',line_id)]+'/drafts'
    with http_client(domain['target']) as client:
        first=client.get(root);assert first.status_code==200,first.text;page=first.json()
        assert len(page['items'])==20 and page['nextCursor']
        last=client.get(root,params={'cursor':page['nextCursor']});assert last.status_code==200,last.text
        assert len(last.json()['items'])==1 and last.json()['nextCursor'] is None
        expected={ids[('task',i)] for i in source_tasks} if kind=='role' else {ids[('world_turn',i)] for i in source_turns}
        assert {i['id'] for i in page['items']+last.json()['items']}==expected
    with http_client(domain['stranger']) as client:
        denied=client.get(root);assert denied.status_code==404,denied.text


def test_encrypted_received_role_history_saved_same_turn_other_task_wins_without_rewriting(domain,tmp_path):
    tid,_,slot=seed_public_stream_history(domain,'role')
    with SessionLocal() as db:
        db.add(m.RoleMessage(owner_id=domain['owner'],session_id=slot['sessionId'],turn_id=slot['turnId'],
            sequence=2,role='assistant',text='同一轮的正式回复仍然保留。',task_id=domain['task'],status='saved'));db.commit()
    result=restored(domain,tmp_path);sid=result['idMap'][('role_session',slot['sessionId'])]
    with http_client(domain['target']) as client:
        root='/api/reader/v1/role-sessions/'+sid
        assert client.get(root+'/received-replies').json()['items']==[]
        messages=client.get(root+'/messages');assert messages.status_code==200,messages.text
        assert any(x['text']=='同一轮的正式回复仍然保留。' and x['status']=='saved' for x in messages.json()['items'])


@pytest.mark.parametrize('scope',['earlier-same-scope','different-branch','future-same-scope'])
def test_encrypted_received_role_history_respects_session_reader_ids_without_requiring_current_anchor(domain,tmp_path,scope):
    tid,_,slot=seed_public_stream_history(domain,'role')
    with SessionLocal() as db:
        branch=db.get(m.Branch,domain['branch']) if scope!='different-branch' else db.scalar(select(m.Branch).where(m.Branch.book_id==domain['book'],m.Branch.id!=domain['branch']))
        task=db.get(m.Task,tid);task.payload={**task.payload,'anchor':make_anchor(domain['book'],branch.id,branch.head_revision_id,db.get(m.ContentBlock,domain['block']),0,2)}
        if scope=='future-same-scope':
            session=db.get(m.RoleSession,slot['sessionId']);session.pinned_anchor=task.payload['anchor'];task.payload={**task.payload,'anchor':domain['anchor']}
        db.commit()
    original=encrypted(domain,tmp_path)
    with original.open('rb') as raw,prepare_backup(raw,'synthetic-test-password',tmp_path/'reader-receipt-scope') as prepared:
        with SessionLocal() as db:
            if scope!='earlier-same-scope':
                with pytest.raises(ArchiveError,match='BACKUP_STREAM_HISTORY_INVALID'):restore_domain(db,prepared,domain['target'])
                assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain['target']))==0
                return
            result=restore_domain(db,prepared,domain['target'])
    with http_client(domain['target']) as client:
        root='/api/reader/v1/role-sessions/'+result['idMap'][('role_session',slot['sessionId'])]
        response=client.get(root+'/received-replies');assert response.status_code==200,response.text
        assert len(response.json()['items'])==1 and response.json()['items'][0]['text']==slot['text']


def test_public_typed_role_receipt_without_task_turn_id_is_rejected_before_export(domain,tmp_path):
    tid,_,_=seed_public_stream_history(domain,'role')
    with SessionLocal() as db:
        task=db.get(m.Task,tid);task.payload={k:v for k,v in task.payload.items() if k!='turnId'};db.commit()
    with pytest.raises(ArchiveError,match='BACKUP_STREAM_HISTORY_INVALID'):encrypted(domain,tmp_path)


@pytest.mark.parametrize("field,bad",[("contentHash","0000000000000000000000000000000000000000000000000000000000000000"),("snapshotVersion",True),("sessionId","eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"),("turnId","another-reader-request"),("status",[]),("durability",{}),("stage",1)])
def test_public_stream_history_export_rejects_corrupt_metadata(domain,tmp_path,field,bad):
    tid,key,_=seed_public_stream_history(domain,"world" if field=="stage" else "role")
    with SessionLocal() as db:
        task=db.get(m.Task,tid);task.checkpoint={**task.checkpoint,key:{**task.checkpoint[key],field:bad}};db.commit()
    with pytest.raises(ArchiveError,match="BACKUP_STREAM_HISTORY_INVALID"):encrypted(domain,tmp_path,world=field=="stage")



@pytest.mark.parametrize("field",["payload","result","checkpoint"])
def test_public_stream_history_export_rejects_non_object_task_metadata(domain,tmp_path,field):
    tid,_,_=seed_public_stream_history(domain,"role")
    with SessionLocal() as db:
        task=db.get(m.Task,tid);setattr(task,field,[]);db.commit()
    with pytest.raises(ArchiveError,match="BACKUP_TASK_HISTORY_INVALID"):
        if field=="payload":
            # Losing bookId makes the corrupt row outside the book selector.
            # Exercise the public stream adapter directly rather than pretending
            # that an unscoped row was included in an authorized story export.
            from app.portable.stream_history import safe_streams
            with SessionLocal() as db:safe_streams(db.get(m.Task,tid))
        else:encrypted(domain,tmp_path)


@pytest.mark.parametrize("field",["payload","result","checkpoint"])
def test_public_stream_history_encrypted_import_rejects_non_object_task_metadata_without_domain_writes(domain,tmp_path,field):
    tid,_,_=seed_public_stream_history(domain,"role")
    package=tmp_path/"invalid-task.srbackup"
    with SessionLocal() as db:
        with build_domain_snapshot(db,domain["owner"],"book-"+domain["book"],tmp_path/"invalid-export") as snapshot:
            changed=0
            for entry in snapshot.manifest["files"]:
                if entry["kind"]!="data":continue
                path=snapshot.root/entry["path"];document=json.loads(path.read_text("utf-8"))
                for row in document["rows"]:
                    if row["rowType"]=="Task":
                        row["fields"][field]=[];changed+=1
                raw=json.dumps(document,ensure_ascii=False,separators=(",",":")).encode("utf-8")
                path.write_bytes(raw);entry.update(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
            assert changed>=3
            # The forged row must remain a structurally coherent package so it
            # reaches the domain adapter; obsolete pointers would reject earlier.
            def retained(ref):
                return not (ref["from"]["type"]=="task" and ("/fields/"+field+"/" in ref["field"] or ref["field"].endswith("/fields/"+field)))
            snapshot.manifest["references"]=[ref for ref in snapshot.manifest["references"] if retained(ref)]
            snapshot.manifest["externalSources"]=[ref for ref in snapshot.manifest["externalSources"] if retained(ref)]
            with package.open("wb") as output:write_backup(snapshot.root,snapshot.manifest,output,"synthetic-test-password",tmp_path/"invalid-encrypt")
    with package.open("rb") as raw,prepare_backup(raw,"synthetic-test-password",tmp_path/"invalid-import") as prepared:
        with SessionLocal() as db:
            with pytest.raises(ArchiveError,match="BACKUP_ROW_INVALID"):restore_domain(db,prepared,domain["target"])
    with SessionLocal() as db:
        for model in (m.Book,m.Task,m.RoleSession):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain["target"]))==0
        assert db.get(m.Task,tid).owner_id==domain["owner"]


@pytest.mark.parametrize("kind",["role","world"])
def test_public_stream_history_encrypted_copy_reads_scoped_atomic_journal_ahead_of_database(domain,tmp_path,kind):
    from app import durable_stream
    from app.storage import _write_task_journal
    tid,key,source=seed_public_stream_history(domain,kind)
    text=source["text"]+"数据库尚未镜像的片段👩‍💻。"
    with SessionLocal() as db:
        task=db.get(m.Task,tid)
        if kind=="role":task.checkpoint={**task.checkpoint,"chatStreamingPolicy":"role-stream-v1"};db.commit()
        ctx=durable_stream.context(task,source.get("stage"))
        value={name:deepcopy(source[name]) for name in durable_stream.FIELDS}
        value.update(text=text,contentHash=digest(text),snapshotVersion=10)
        envelope={name:ctx[name] for name in ("taskId","scope","stage")}
        envelope.update(value=value,hash=digest(canonical(value)))
        # Public storage-receipt fixture only; no request, lease or worker dispatch.
        _write_task_journal(domain["owner"],tid,ctx["name"],canonical(envelope).encode("utf-8"))
    original=encrypted(domain,tmp_path,world=kind=="world")
    with original.open("rb") as raw,prepare_backup(raw,"synthetic-test-password",tmp_path/"journal-import") as prepared:
        assert not any("journal-stream-" in file["path"] for file in prepared.manifest["files"])
        with SessionLocal() as db:receipt=restore_domain(db,prepared,domain["target"])
    with SessionLocal() as db:
        imported=db.get(m.Task,receipt["idMap"][("task",tid)])
        stream=imported.checkpoint[key]
        assert stream["text"]==text and stream["contentHash"]==digest(text)
        assert stream["snapshotVersion"]==stream["version"]==10 and stream["streamVersion"]==2
        assert stream["historical"] and stream["durability"]=="unconfirmed" and imported.payload["importedHistory"]
        assert db.get(m.Task,tid).checkpoint[key]["snapshotVersion"]==9
        assert not list((settings.storage_path/domain["target"]).glob("journal-stream-*.json"))


@pytest.mark.parametrize("kind",["role","world"])
def test_public_stream_history_export_rejects_foreign_scope_atomic_journal(domain,tmp_path,kind):
    from app import durable_stream
    from app.storage import _write_task_journal
    tid,key,source=seed_public_stream_history(domain,kind)
    with SessionLocal() as db:
        task=db.get(m.Task,tid)
        if kind=="role":task.checkpoint={**task.checkpoint,"chatStreamingPolicy":"role-stream-v1"};db.commit()
        ctx=durable_stream.context(task,source.get("stage"))
        value={name:deepcopy(source[name]) for name in durable_stream.FIELDS};value["snapshotVersion"]=10
        envelope={name:ctx[name] for name in ("taskId","scope","stage")}
        envelope["scope"]={**ctx["scope"],"turnId":"foreign-request"}
        envelope.update(value=value,hash=digest(canonical(value)))
        _write_task_journal(domain["owner"],tid,ctx["name"],canonical(envelope).encode("utf-8"))
    with pytest.raises(ArchiveError,match="BACKUP_STREAM_HISTORY_INVALID"):encrypted(domain,tmp_path,world=kind=="world")



def test_public_stream_history_snapshot_freezes_one_projection_and_next_export_refreshes(domain,tmp_path,monkeypatch):
    from app import durable_stream
    tid,key,source=seed_public_stream_history(domain,"world")
    with SessionLocal() as db:
        task=db.get(m.Task,tid);task.payload={**task.payload,"chapterCount":2};db.commit()
    calls=[]
    def advance(task,stream_key):
        assert task.id==tid and stream_key==key
        calls.append(task.id);value=deepcopy(source)
        if len(calls)>1:
            text="下一次快照中的第二场景。"
            value.update(contentId=digest(tid+":generation:scene:2")[:32],chapterIndex=2,
                stage="generation:scene:2",snapshotVersion=10,version=10,text=text,contentHash=digest(text))
        return value
    # Controlled concurrent public-receipt change; actual snapshot/archive/restore.
    monkeypatch.setattr(durable_stream,"overlay",advance)
    for index,owner in enumerate((domain["target"],domain["stranger"])):
        work=tmp_path/str(index);work.mkdir()
        package=encrypted(domain,work,world=True)
        assert len(calls)==index+1
        with package.open("rb") as raw,prepare_backup(raw,"synthetic-test-password",work/"import") as prepared:
            with SessionLocal() as db:receipt=restore_domain(db,prepared,owner)
        with SessionLocal() as db:
            copied=db.get(m.Task,receipt["idMap"][("task",tid)])
            assert copied.checkpoint[key]["chapterIndex"]==index+1
            assert copied.checkpoint[key]["text"]==(source["text"] if index==0 else "下一次快照中的第二场景。")


def restored(domain,tmp_path,**options):
    original=encrypted(domain,tmp_path,**options)
    with original.open("rb") as source,prepare_backup(source,"synthetic-test-password",tmp_path/"import") as prepared:
        with SessionLocal() as db: receipt=restore_domain(db,prepared,domain["target"])
    return receipt


def seed_prose_history(domain):
    from app.prose.service import accept,snapshot
    from app.prose.schemas import AcceptGeneration
    from app.portable.prose_history import META
    with SessionLocal() as db:
        task=m.Task(id=m.uid(),owner_id=domain["owner"],task_type="story_flow",state="failed",
            payload={"bookId":domain["book"],"anchor":domain["anchor"],"protocol":"reader-prose-v1","profileId":m.uid(),"operation":"continue_from_anchor"},
            result={"phase":"incomplete"},error_code="CHAPTER_TOO_SHORT",provider_state="response_received",persistence_state="saved",usage_state="settled")
        db.add(task);db.flush()
        profile={"id":task.payload["profileId"],"version":5,"name":"历史测试模型","model_id":"synthetic-mock",
            "base_url":"https://must-not-migrate.invalid","key_cipher":"NEVER_COPY_PRIVATE_KEY_SENTINEL","context_window":32000,"output_parameter":"max_tokens","supports_json":True}
        request={"protocol":"reader-prose-v1","originAnchor":domain["anchor"],"operation":"continue_from_anchor","chapterCount":3,"chapterLength":"short",
            "instruction":"保留草稿与软提示","profileId":profile["id"],"expectedHeadRevisionId":domain["revision"],"target":{"mode":"new_child_branch","name":"历史续写","branchId":None}}
        batch=m.GenerationBatch(id=m.uid(),owner_id=domain["owner"],task_id=task.id,book_id=domain["book"],origin_anchor=domain["anchor"],request=request,
            profile_snapshot=profile,candidate_version=1,accepted_prefix=0,version_times={"1":m.now().isoformat()})
        db.add(batch);db.flush()
        old=[]
        for index in range(1,4):
            body=("未来草稿仅作为私人写作记录。\n" if index==1 else "旧版还没有写完。")
            row=m.GenerationChapterVersion(id=m.uid(),owner_id=domain["owner"],generation_id=batch.id,candidate_version=1,chapter_index=index,
                content_id=m.uid(),title="历史章节"+str(index),text=body,content_hash=digest(body),stream_version=2,snapshot_version=2,
                completeness="complete" if index==1 else "partial",durability="saved",hints=[{"code":"VOICE_WARNING","message":"可继续阅读"}])
            db.add(row);old.append(row)
        db.commit()
        receipt=accept(db,domain["owner"],batch.id,AcceptGeneration(candidateVersion=1,prefixCount=1,expectedAcceptedPrefix=0,
            expectedHeadRevisionId=domain["revision"],target="child",sourcePolicy="requireUnchanged"),"owned-seed-accept-"+batch.id)
        prior=snapshot(db,batch,task)
        checkpoint={"proseVersionStates":{"1":{name:prior[name] for name in META}},
                    "calls":{"private":"NEVER_COPY_RAW_JOURNAL_SENTINEL"},"proseStreamRefs":["NEVER_COPY_RAW_JOURNAL_SENTINEL"]}
        batch.candidate_version=2;batch.version_times={**batch.version_times,"2":m.now().isoformat()};task.checkpoint=checkpoint
        for index in range(1,4):
            body=old[0].text if index==1 else "新版完整草稿。\n可以阅读并接受。" if index==2 else "这份草稿中途停止。"
            db.add(m.GenerationChapterVersion(id=m.uid(),owner_id=domain["owner"],generation_id=batch.id,candidate_version=2,chapter_index=index,
                content_id=old[0].content_id if index==1 else m.uid(),title="新版章节"+str(index),text=body,content_hash=digest(body),stream_version=3,snapshot_version=3,
                completeness="partial" if index==3 else "complete",durability="saved",hints=[{"code":"SPOILER_RISK","message":"保留正文与提示"}]))
        db.add(m.TaskEvent(id=m.uid(),task_id=task.id,seq=task.event_seq+1,type="prose-chunk",payload={"generationId":batch.id,"candidateId":batch.id,
            "contentId":old[0].content_id,"text":old[0].text}));task.event_seq+=1
        db.commit()
        return {"batch":batch.id,"task":task.id,"profile":profile["id"],"content":old[0].content_id,"acceptanceRevision":receipt["revisionId"]}


def test_multi_version_prose_restore_preserves_safe_history_without_model_or_journal(domain,tmp_path):
    history=seed_prose_history(domain)
    with SessionLocal() as db:
        with build_domain_snapshot(db,domain["owner"],"book-"+domain["book"],tmp_path/"safe-inspect") as snapshot:
            for entry in snapshot.manifest["files"]:
                raw=(snapshot.root/entry["path"]).read_bytes()
                assert b"NEVER_COPY_PRIVATE_KEY_SENTINEL" not in raw and b"NEVER_COPY_RAW_JOURNAL_SENTINEL" not in raw
                assert b"must-not-migrate.invalid" not in raw
    result=restored(domain,tmp_path);ids=result["idMap"]
    with SessionLocal() as db:
        batch=db.get(m.GenerationBatch,ids[("generation_batch",history["batch"])])
        original=db.get(m.GenerationBatch,history["batch"])
        assert batch.owner_id==domain["target"] and batch.candidate_version==2 and batch.accepted_prefix==1 and batch.version_times==original.version_times
        assert batch.profile_snapshot=={"id":ids[("historical_profile",history["profile"])],"name":"历史测试模型","model_id":"synthetic-mock","version":5,"historical":True}
        assert batch.request["profileId"]==batch.profile_snapshot["id"] and batch.origin_anchor["bookId"]==ids[("book",domain["book"])]
        assert batch.last_accepted_revision_id==ids[("revision",history["acceptanceRevision"])]
        new=list(db.scalars(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==batch.id).order_by(m.GenerationChapterVersion.candidate_version,m.GenerationChapterVersion.chapter_index)))
        old=list(db.scalars(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==original.id).order_by(m.GenerationChapterVersion.candidate_version,m.GenerationChapterVersion.chapter_index)))
        assert len(new)==len(old)==6
        for left,right in zip(new,old):
            assert left.id!=right.id and left.content_id==ids[("chapter_content",right.content_id)] and left.owner_id==domain["target"]
            assert (left.title,left.text,left.content_hash,left.stream_version,left.snapshot_version,left.completeness,left.durability,left.hints)==(right.title,right.text,right.content_hash,right.stream_version,right.snapshot_version,right.completeness,right.durability,right.hints)
        task=db.get(m.Task,batch.task_id)
        assert task.state=="cancelled" and task.cancel_requested and task.payload["importedHistory"] and task.lease_owner is None
        assert set(task.checkpoint)=={"proseVersionStates","portableHistoricalCurrent"}
        assert task.checkpoint["proseVersionStates"]["1"]["state"]=="failed" and task.checkpoint["portableHistoricalCurrent"]["errorCode"]=="CHAPTER_TOO_SHORT"
        acceptance=db.scalar(select(m.GenerationAcceptance).where(m.GenerationAcceptance.generation_id==batch.id))
        assert acceptance.result["candidateId"]==batch.id and acceptance.result["generationId"]==batch.id
        for model in (m.ModelProfile,m.Consent,m.OperationPlan,m.UsageRecord):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain["target"]))==0


@pytest.mark.parametrize("kind",["body-hash","different-profile"])
def test_prose_history_inconsistent_body_or_profile_is_atomic(domain,tmp_path,kind):
    history=seed_prose_history(domain)
    with SessionLocal() as db:
        if kind=="body-hash":
            row=db.scalar(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==history["batch"],m.GenerationChapterVersion.candidate_version==2,m.GenerationChapterVersion.chapter_index==2));row.content_hash="0"*64
        else:
            batch=db.get(m.GenerationBatch,history["batch"]);batch.request={**batch.request,"profileId":m.uid()}
        db.commit()
    with pytest.raises(ArchiveError,match="BACKUP_BODY_HASH_MISMATCH" if kind=="body-hash" else "BACKUP_GENERATION_PROFILE_INVALID"):restored(domain,tmp_path)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain["target"]))==0
        assert db.scalar(select(func.count()).select_from(m.GenerationBatch).where(m.GenerationBatch.owner_id==domain["target"]))==0


def test_world_only_publication_does_not_export_original_novel_prose_drafts(domain,tmp_path):
    history=seed_prose_history(domain);seed_legacy_publication(domain)
    result=restored(domain,tmp_path,world=True)
    assert ("generation_batch",history["batch"]) not in result["idMap"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.GenerationBatch).where(m.GenerationBatch.owner_id==domain["target"]))==0
        assert all("未来草稿仅作为私人写作记录" not in block.text for block in db.scalars(select(m.ContentBlock).where(m.ContentBlock.owner_id==domain["target"])))


def test_v1_task_without_display_checkpoint_restores_with_empty_safe_history(domain,tmp_path):
    encrypted_file=tmp_path/"earlier-v1.srbackup"
    with SessionLocal() as db:
        with build_domain_snapshot(db,domain["owner"],"book-"+domain["book"],tmp_path/"v1-old") as snapshot:
            removed=0
            for entry in snapshot.manifest["files"]:
                if entry["kind"]!="data":continue
                path=snapshot.root/entry["path"];document=json.loads(path.read_text("utf-8"))
                for row in document["rows"]:
                    if row["rowType"]=="Task":assert row["fields"].pop("checkpoint")=={};removed+=1
                raw=json.dumps(document,ensure_ascii=False,separators=(",",":")).encode("utf-8");path.write_bytes(raw);entry.update(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
            assert removed>=2
            with encrypted_file.open("wb") as output:write_backup(snapshot.root,snapshot.manifest,output,"synthetic-test-password",tmp_path/"export-v1")
    with encrypted_file.open("rb") as source,prepare_backup(source,"synthetic-test-password",tmp_path/"import-v1") as prepared:
        with SessionLocal() as db:result=restore_domain(db,prepared,domain["target"])
    with SessionLocal() as db:assert db.get(m.Task,result["idMap"][("task",domain["task"])]).checkpoint=={}


def test_story_new_owner_roundtrip(domain,tmp_path):
    result=restored(domain,tmp_path); ids=result["idMap"]
    assert result["storyId"]=="book-"+ids[("book",domain["book"])]
    assert not result["historicalTasksReplayed"] and not result["creditTransferred"]
    with SessionLocal() as db:
        book=db.get(m.Book,ids[("book",domain["book"])])
        assert book.owner_id==domain["target"] and book.title=="原创新故事"
        block=db.get(m.ContentBlock,ids[("paragraph",domain["block"])])
        assert block.text==domain["prose"] and '<strong>林渡</strong>' in block.html
        assert ids[("asset",domain["asset"])] in block.html and domain["asset"] not in block.html
        progress=db.scalar(select(m.Progress).where(m.Progress.owner_id==domain["target"]))
        assert progress.version==8 and progress.anchor["end"]==domain["anchor"]["end"]
        validate_anchor(db,progress.anchor,domain["target"])
        note=db.scalar(select(m.Bookmark).where(m.Bookmark.owner_id==domain["target"]))
        assert note.note=="保留私人笔记🙂" and note.version==3
        validate_anchor(db,note.anchor,domain["target"])
        src=db.get(m.SourceDocument,book.source_id)
        assert private_path(domain["target"],src.normalized_ref).read_text("utf-8")==domain["prose"]
        assert private_path(domain["target"],src.source_map["assets"][ids[("asset",domain["asset"])]]).read_bytes()==domain["image"]
        assert db.get(m.ContentBlock,domain["block"]).text==domain["prose"]
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id==domain["target"]))==0
        assert db.scalar(select(func.count()).select_from(m.ModelProfile).where(m.ModelProfile.owner_id==domain["target"]))==0


def test_world_source_omitted_preserves_private_state_and_ancestry(domain,tmp_path):
    result=restored(domain,tmp_path,world=True); ids=result["idMap"]
    assert result["storyId"]=="world-"+ids[("world",domain["world"])] and ("book",domain["book"]) not in ids
    with SessionLocal() as db:
        world=db.get(w.World,ids[("world",domain["world"])])
        assert world.source_book_id is None and world.source["anchor"] is None
        assert world.source_title=="原创新故事" and world.source["sourceStatus"]=="source-not-imported"
        assert world.source["blocks"][0]["text"]==domain["prose"]
        original=db.get(w.WorldCheckpoint,domain["checkpoint"]); point=db.get(w.WorldCheckpoint,ids[("checkpoint",domain["checkpoint"])])
        assert undo_local_ids(point.state,ids,domain["world"])==original.state and point.text==original.text
        assert [p.id for p in ancestry(db,point,domain["target"])][-1]==point.id
        assert db.get(w.WorldCheckpoint,ids[("checkpoint",domain["fork"])]).parent_checkpoint_id==ids[("checkpoint",domain["root_checkpoint"])]
        memory=db.scalar(select(w.WorldMemory).where(w.WorldMemory.owner_id==domain["target"]))
        event=db.scalar(select(w.WorldEvent).where(w.WorldEvent.owner_id==domain["target"]))
        assert event.data["id"]==memory.event_ids[0]==point.memory["episodes"][0]["eventIds"][0]
        assert event.id!=event.data["id"]
        assert undo_local_ids(public_view(point.state,point.definition),ids,domain["world"])==public_view(original.state,original.definition)


def undo_local_ids(value,mapping,source_world):
    reverse={new:old[len(source_world)+1:] for (kind,old),new in mapping.items()
        if kind in {"world_entity","world_lore","promise"} and old.startswith(source_world+":")}
    def scalar(value):
        if not isinstance(value,str):return value
        for prefix in ("relation:","relationEvidence:"):
            if value.startswith(prefix):return prefix+reverse.get(value[len(prefix):],value[len(prefix):])
        return reverse.get(value,value)
    if isinstance(value,list):return [undo_local_ids(v,mapping,source_world) for v in value]
    if isinstance(value,dict):return {scalar(k):undo_local_ids(v,mapping,source_world) for k,v in value.items()}
    return scalar(value)


def test_world_local_entity_lore_promise_and_relationship_ids_are_new_without_text_replacement(domain,tmp_path):
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,domain["checkpoint"])
        definition,state=deepcopy(point.definition),deepcopy(point.state)
        actor,place=state["playerId"],state["location"];item,lore="portable-test-letter","portable-test-lore"
        definition["entities"].append({"id":item,"name":"旧信","type":"item","stackable":True})
        definition["lore"].append({"id":lore,"title":"暗号","text":"正文保留符号 "+actor,"entityIds":[actor],"knownBy":[actor]})
        definition["rules"]["routes"]={place:[place]}
        state["entities"][item]={"alive":None,"location":place,"inventory":{},"fields":{},"relations":{},"goal":"","known":[]}
        state["entities"][actor]["inventory"]={item:2};state["entities"][actor]["relations"]={actor:"同伴"}
        state["entities"][actor]["fields"]["opaqueSymbol"]=actor
        state["entities"][actor]["known"]=[lore];state["known"]=[lore]
        state["observed"]={actor:{"location":place,"relation:"+actor:"同伴","relationEvidence:"+actor:[]}}
        state["unresolved"].append({"id":"portable-test-promise","description":"读信，不修改这个名字","source":"initial"})
        point.definition,point.state=definition,state;point.text="正文里的 "+actor+" 保持原样。"
        point.changes=[{"entityId":actor,"field":"location","before":place,"after":place,"visibleTo":[actor]},
            {"entityId":actor,"field":"relation:"+actor,"before":None,"after":"同伴","visibleTo":[actor]}]
        original_text=point.text;db.commit()
    result=restored(domain,tmp_path,world=True);ids=result["idMap"]
    actor_new=ids[("world_entity",domain["world"]+":"+actor)];place_new=ids[("world_entity",domain["world"]+":"+place)]
    item_new=ids[("world_entity",domain["world"]+":"+item)];lore_new=ids[("world_lore",domain["world"]+":"+lore)]
    promise_new=ids[("promise",domain["world"]+":portable-test-promise")]
    assert len({actor_new,place_new,item_new,lore_new,promise_new})==5
    assert not {actor_new,place_new,item_new,lore_new,promise_new}&{actor,place,item,lore,"portable-test-promise"}
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,ids[("checkpoint",domain["checkpoint"])])
        assert point.text==original_text and point.state["playerId"]==actor_new and point.state["location"]==place_new
        player=point.state["entities"][actor_new]
        assert player["inventory"]=={item_new:2} and player["relations"]=={actor_new:"同伴"}
        assert player["fields"]["opaqueSymbol"]==actor and player["known"]==point.state["known"]==[lore_new]
        assert point.state["observed"][actor_new]["relation:"+actor_new]=="同伴"
        assert point.definition["rules"]["routes"]=={place_new:[place_new]}
        entry=next(v for v in point.definition["lore"] if v["id"]==lore_new)
        assert entry["entityIds"]==entry["knownBy"]==[actor_new] and entry["text"]=="正文保留符号 "+actor
        assert next(v for v in point.state["unresolved"] if v["id"]==promise_new)["description"]=="读信，不修改这个名字"
        assert point.changes[0]["before"]==point.changes[0]["after"]==place_new and point.changes[1]["field"]=="relation:"+actor_new
        event=db.scalar(select(w.WorldEvent).where(w.WorldEvent.owner_id==domain["target"]))
        assert event.entity_id==actor_new and event.visible_to==[actor_new]
        alias=db.scalar(select(m.CharacterAlias).where(m.CharacterAlias.owner_id==domain["target"],m.CharacterAlias.scope_kind=="world"))
        assert alias.entity_id==actor_new and alias.identity_id==ids[("character_identity",domain["identity"])]


def test_actual_committed_relation_evidence_survives_private_restore_and_remaps_checkpoint(domain,tmp_path):
    from app.world.schemas import DirectorOutput
    from app.world.state import apply_plan
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,domain["checkpoint"]);actor=point.state["playerId"]
        related=next((e["id"] for e in point.definition["entities"] if e["type"]=="character" and e["id"]!=actor),actor)
        plan=DirectorOutput.model_validate({"title":"灯下的信任","elapsedMinutes":1,"timeLabel":"片刻后","publicOutcome":"一起核对了信上的字。",
            "updates":[{"entityId":actor,"field":"relation:"+related,"value":"互相信任","reason":"一起核对旧信","visibleTo":[actor]}]}).model_dump()
        state,definition,changes,_=apply_plan(point.state,point.definition,plan,point.id,action_kind="dialogue")
        point.state,point.definition,point.changes=state,definition,changes;db.commit()
        assert state["entities"][actor]["relationEvidence"][related]["checkpointId"]==point.id
    result=restored(domain,tmp_path,world=True);ids=result["idMap"]
    actor_new=ids[("world_entity",domain["world"]+":"+actor)];related_new=ids[("world_entity",domain["world"]+":"+related)]
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,ids[("checkpoint",domain["checkpoint"])])
        proof=point.state["entities"][actor_new]["relationEvidence"][related_new]
        assert proof=={"checkpointId":point.id,"minute":state["minute"],"reason":"一起核对旧信"}
        assert point.state["observed"][actor_new]["relationEvidence:"+related_new]==proof
        assert point.state["entities"][actor_new]["relations"][related_new]=="互相信任"


@pytest.mark.parametrize("fault",[None,"body-hash","sibling-proof","future-minute"])
def test_saved_world_candidate_scene_private_restore_preserves_draft_without_committing_it(domain,tmp_path,fault):
    from app.world.schemas import DirectorOutput
    from app.world.state import apply_plan
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,domain["checkpoint"]);actor=point.state["playerId"]
        cp=m.uid()
        plan=DirectorOutput.model_validate({"title":"尚未接受的约定","elapsedMinutes":1,"timeLabel":"片刻后","publicOutcome":"核对了旧信。",
            "updates":[{"entityId":actor,"field":"relation:"+actor,"value":"保留信心","reason":"核对旧信","visibleTo":[actor]}]}).model_dump()
        state,definition,changes,events=apply_plan(point.state,point.definition,plan,cp,action_kind="dialogue")
        state["entities"][actor]["fields"]["bookId"]="故事里的旧账簿"
        scene={"id":cp,"state":state,"definition":definition,"changes":changes,"events":events,"plan":plan,"text":"候选正文保留 local-0。"}
        scene={**scene,"contentHash":digest(canonical(scene))}
        if fault=="body-hash":scene["contentHash"]="0"*64
        elif fault in {"sibling-proof","future-minute"}:
            proof=scene["state"]["entities"][actor]["relationEvidence"][actor]
            if fault=="sibling-proof":proof["checkpointId"]=domain["fork"]
            else:proof["minute"]+=1
            scene["contentHash"]=digest(canonical({k:v for k,v in scene.items() if k!="contentHash"}))
        task=m.Task(owner_id=domain["owner"],task_type="world_autoplay",state="failed",
            payload={"worldId":domain["world"],"lineId":point.line_id},persistence_state="saved")
        db.add(task);db.flush()
        turn=w.WorldTurn(owner_id=domain["owner"],world_id=domain["world"],line_id=point.line_id,parent_checkpoint_id=point.id,
            task_id=task.id,turn_key="synthetic-owned-scene",action="核对旧信",kind="autoplay",status="draft_available",
            draft_text=scene["text"],result={"chapters":[scene]})
        db.add(turn);db.commit();turn_id=turn.id
    if fault:
        with pytest.raises(ArchiveError):restored(domain,tmp_path,world=True)
        with SessionLocal() as db:
            for model in (w.World,w.WorldCheckpoint,w.WorldTurn,m.Task):
                assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain["target"]))==0
        return
    receipt=restored(domain,tmp_path,world=True);ids=receipt["idMap"]
    with SessionLocal() as db:
        turn=db.get(w.WorldTurn,ids[("world_turn",turn_id)])
        candidate=turn.result["chapters"][0];actor_new=ids[("world_entity",domain["world"]+":"+actor)]
        assert candidate["id"]!=cp and candidate["state"]["playerId"]==actor_new
        assert candidate["state"]["entities"][actor_new]["relationEvidence"][actor_new]["checkpointId"]==candidate["id"]
        assert candidate["state"]["entities"][actor_new]["fields"]["bookId"]=="故事里的旧账簿"
        assert candidate["text"]==scene["text"] and candidate["plan"]["updates"][0]["entityId"]==actor_new
        assert candidate["changes"][0]["field"]=="relation:"+actor_new
        assert candidate["contentHash"]==digest(canonical({k:v for k,v in candidate.items() if k!="contentHash"}))
        line=db.get(w.WorldLine,turn.line_id)
        assert line.head_checkpoint_id==turn.parent_checkpoint_id and turn.checkpoint_id is None and turn.status=="cancelled"
        assert db.get(w.WorldCheckpoint,candidate["id"]) is None
        assert db.get(m.Task,turn.task_id).payload["importedHistory"]


def test_world_source_explicitly_included_keeps_only_remapped_owned_link(domain,tmp_path):
    result=restored(domain,tmp_path,world=True,include_source=True); ids=result["idMap"]
    with SessionLocal() as db:
        world=db.get(w.World,ids[("world",domain["world"])])
        assert world.source_book_id==ids[("book",domain["book"])] and world.source_book_id!=domain["book"]
        assert world.source["anchor"]["bookId"]==world.source_book_id
        validate_anchor(db,world.source["anchor"],domain["target"])


def test_world_omitted_source_historical_prepare_task_detaches_anchor_without_replay(domain,tmp_path):
    with SessionLocal() as db:
        task=m.Task(id=m.uid(),owner_id=domain["owner"],task_type="world_prepare",state="succeeded",
            payload={"bookId":domain["book"],"anchor":domain["anchor"],"worldId":domain["world"]},
            result={"worldId":domain["world"],"phase":"complete"},provider_state="response_received",persistence_state="saved",usage_state="settled")
        db.add(task);db.commit();identifier=task.id
    result=restored(domain,tmp_path,world=True);ids=result["idMap"]
    with SessionLocal() as db:
        task=db.get(m.Task,ids[("task",identifier)])
        assert task.owner_id==domain["target"] and task.payload["importedHistory"] and task.cancel_requested
        assert task.payload["bookId"] is None and task.payload["anchor"] is None
        assert task.payload["worldId"]==ids[("world",domain["world"])] and task.result["worldId"]==task.payload["worldId"]
        assert task.lease_owner is None and task.provider_state=="not_started" and task.usage_state=="historical"
        assert db.get(m.Task,identifier).payload["anchor"]==domain["anchor"]


def test_fictional_scalar_password_field_is_preserved_without_transferring_credentials(domain,tmp_path):
    from app.portable.remap import validate_private_policy
    for unsafe in ({"rows":[{"rowType":"WorldCheckpoint","fields":{"password":"operational"}}]},
                   {"rows":[{"rowType":"WorldCheckpoint","fields":{"state":{"entities":{"person":{"fields":{"password":{"apiKey":"nested-secret"}}}}}}}]}):
        with pytest.raises(ArchiveError,match="BACKUP_PRIVATE_POLICY_INVALID"):
            validate_private_policy({"objects":[]},{"data/unsafe.json":unsafe})
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,domain["checkpoint"]);state=deepcopy(point.state)
        state["entities"][state["playerId"]]["fields"]["password"]="剧情里的门禁暗号"
        point.state=state;db.commit()
    result=restored(domain,tmp_path,world=True)
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,result["idMap"][("checkpoint",domain["checkpoint"])])
        assert point.state["entities"][point.state["playerId"]]["fields"]["password"]=="剧情里的门禁暗号"
        assert db.scalar(select(func.count()).select_from(m.ModelProfile).where(m.ModelProfile.owner_id==domain["target"]))==0


@pytest.mark.parametrize("location",["state","initial"])
def test_fictional_scalar_reserved_names_are_opaque_story_properties(domain,tmp_path,location):
    fields={"bookId":"旧账簿甲", "ownerId":"船的主人", "apiKey":"故事里的铜钥匙", "profileId":"人物档案编号甲",
            "text":"纸条上的文字", "html":"<符号，不是网页>",
            "portableTextRef":"故事道具的名称", "portableInline":"一串谜语"}
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,domain["checkpoint"]);actor=point.state["playerId"]
        if location=="state":
            value=deepcopy(point.state);value["entities"][actor]["fields"].update(fields);point.state=value
        else:
            value=deepcopy(point.definition);value["initial"][actor]["fields"].update(fields);point.definition=value
        db.commit()
    receipt=restored(domain,tmp_path,world=True)
    assert ("historical_profile",fields["profileId"]) not in receipt["idMap"]
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,receipt["idMap"][("checkpoint",domain["checkpoint"])])
        actor=point.state["playerId"]
        actual=(point.state["entities"] if location=="state" else point.definition["initial"])[actor]["fields"]
        assert all(actual[key]==value for key,value in fields.items())
        assert db.scalar(select(func.count()).select_from(m.ModelProfile).where(m.ModelProfile.owner_id==domain["target"]))==0


@pytest.mark.parametrize("fault",["sibling-proof","future-minute","boolean-minute"])
def test_relation_proof_cannot_import_other_path_or_impossible_time(domain,tmp_path,fault):
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,domain["checkpoint"]);actor=point.state["playerId"]
        state=deepcopy(point.state)
        proof={"reason":"一起核对旧信","checkpointId":domain["fork"] if fault=="sibling-proof" else point.id,
               "minute":True if fault=="boolean-minute" else state["minute"]+(1 if fault=="future-minute" else 0)}
        state["entities"][actor]["relationEvidence"]={actor:proof};point.state=state;db.commit()
    with pytest.raises(ArchiveError):restored(domain,tmp_path,world=True)
    with SessionLocal() as db:
        for model in (w.World,w.WorldCheckpoint,m.Book,m.Task):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain["target"]))==0


def test_image_writer_rejects_owned_but_mismatched_branch_revision_before_packaging(domain,tmp_path):
    with SessionLocal() as db:
        scope={"kind":"book","bookId":domain["book"],"branchId":domain["branch"],"revisionId":domain["revision"]}
        result=create_asset(db,domain["owner"],domain["image"],"image/png","cover",{"x":0,"y":0,"width":8,"height":12},scope,"CC0 synthetic")
        placement=place_asset(db,domain["owner"],PlacementInput(assetId=result["assetId"],scope=scope,purpose="cover"))
        revision=db.scalar(select(m.Revision).where(m.Revision.owner_id==domain["owner"],m.Revision.branch_id!=domain["branch"]))
        row=db.get(AssetPlacement,placement["id"]);row.scope={**row.scope,"revisionId":revision.id};db.commit()
        with pytest.raises(ArchiveError,match="BACKUP_ASSET_SCOPE_INVALID"):
            with build_domain_snapshot(db,domain["owner"],"book-"+domain["book"],tmp_path/"invalid-image-scope"):pass


def test_homonyms_stay_distinct_and_reader_world_keep_one_binding(domain,tmp_path):
    result=restored(domain,tmp_path); ids=result["idMap"]
    assert len({ids[("character_identity",key)] for key in domain["identities"]})==2
    with SessionLocal() as db:
        aliases=list(db.scalars(select(m.CharacterAlias).where(m.CharacterAlias.owner_id==domain["target"])))
        reader=next(a for a in aliases if a.scope_kind=="reader" and a.entity_id==ids[("reader_entity",domain["branch"]+":local-0")])
        world=next(a for a in aliases if a.scope_kind=="world")
        session=db.scalar(select(m.RoleSession).where(m.RoleSession.owner_id==domain["target"]))
        assert reader.identity_id==world.identity_id==session.character_identity_id==ids[("character_identity",domain["identity"])]
        assert db.get(m.KnowledgeSnapshot,session.snapshot_id).owner_id==domain["target"]
        assert session.pending_task_id is None


def test_reader_local_character_fact_evidence_ids_remap_without_changing_quotes(domain,tmp_path):
    with SessionLocal() as db:
        snapshot=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["owner"]))
        data=deepcopy(snapshot.data)
        data["characters"].append({"id":"local-1","displayName":"林渡","evidenceIds":["e1"],"identityProofVersion":2})
        data["facts"]=[{"id":"fact-1","subjectId":"local-0","knownBy":["local-1"],"evidenceIds":["e1"],
            "knowledge":[{"characterId":"local-1","evidenceIds":["e1"],"learnedAt":domain["anchor"]}],
            "value":"剧情保留 local-0 和 e1"}]
        snapshot.data=data
        db.add(m.CharacterScopeRevision(owner_id=domain["owner"],identity_id=domain["identity"],scope_key="reader:owned-synthetic-card",
            scope_kind="reader",scope_id=domain["branch"],context={"anchor":domain["anchor"]},
            visible_data={"id":"local-0","evidenceIds":["e1"],"name":"林渡"}))
        session=db.scalar(select(m.RoleSession).where(m.RoleSession.owner_id==domain["owner"]));session.card_id="local-0";db.commit()
    result=restored(domain,tmp_path);ids=result["idMap"]
    char0=ids[("reader_entity",domain["branch"]+":local-0")]
    char1=ids[("reader_entity",domain["branch"]+":local-1")]
    evidence=ids[("reader_evidence",domain["branch"]+":e1")]
    fact=ids[("reader_fact",domain["branch"]+":fact-1")]
    assert len({char0,char1,evidence,fact})==4 and not {char0,char1,evidence,fact}&{"local-0","local-1","e1","fact-1"}
    with SessionLocal() as db:
        snapshot=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["target"]))
        assert {v["id"] for v in snapshot.data["characters"]}=={char0,char1}
        assert snapshot.data["evidence"][0]["id"]==evidence and snapshot.data["evidence"][0]["quote"]=="林渡"
        value=snapshot.data["facts"][0]
        assert value["id"]==fact and value["subjectId"]==char0 and value["knownBy"]==[char1]
        assert value["evidenceIds"]==[evidence] and value["knowledge"][0]["characterId"]==char1
        assert value["knowledge"][0]["evidenceIds"]==[evidence] and value["value"]=="剧情保留 local-0 和 e1"
        session=db.scalar(select(m.RoleSession).where(m.RoleSession.owner_id==domain["target"]))
        assert session.character_id==session.card_id==char0
        card=db.scalar(select(m.CharacterScopeRevision).where(m.CharacterScopeRevision.owner_id==domain["target"],m.CharacterScopeRevision.scope_kind=="reader"))
        assert card.visible_data=={"id":char0,"evidenceIds":[evidence],"name":"林渡"}
        frozen=db.get(w.World,ids[("world",domain["world"])])
        assert frozen.source["characterRefs"][0]["entityId"]==char0


def seed_reader_recognition_correction(domain):
    with SessionLocal() as db:
        row=m.CharacterScopeRevision(owner_id=domain["owner"],identity_id=domain["identity"],
            scope_key="reader-correction:owned-portable-test",scope_kind="reader",scope_id=domain["branch"],version=1,
            context={"readerCorrection":True,"bookId":domain["book"],"readerBranchId":domain["branch"],
                "revisionId":domain["revision"],"anchor":deepcopy(domain["anchor"])},
            visible_data={"id":"local-0","displayName":"林渡","aliases":["林渡"],"evidenceIds":["e1"],
                "identityStatus":"uncertain","reason":"两位人物重名，保留当前证据并标记待确认。"})
        db.add(row);db.commit();return row.id


def test_reader_recognition_correction_encrypted_round_trip_keeps_exact_scope_and_identity(domain,tmp_path):
    correction_id=seed_reader_recognition_correction(domain)
    original=encrypted(domain,tmp_path)
    with http_client(domain["target"]) as client:
        prepared=prepared_http(client,original,"reader-correction-upload")
        response=client.post(f'/api/reader/v1/backups/{prepared["id"]}/restore',
            json={"confirmed":True,"expectedVersion":prepared["version"],"targetOwnerId":domain["target"]},
            headers={"Idempotency-Key":"reader-correction-confirm"})
        assert response.status_code==200,response.text
    with SessionLocal() as db:
        row=db.scalar(select(m.CharacterScopeRevision).where(m.CharacterScopeRevision.owner_id==domain["target"],
            m.CharacterScopeRevision.context["readerCorrection"].as_boolean().is_(True)))
        source=db.get(m.CharacterScopeRevision,correction_id)
        assert row.id!=source.id and row.identity_id!=source.identity_id and row.scope_id!=source.scope_id
        assert row.context["readerBranchId"]==row.scope_id==row.context["anchor"]["branchId"]
        assert row.context["bookId"]==row.context["anchor"]["bookId"] and row.context["revisionId"]==row.context["anchor"]["revisionId"]
        assert row.version==1 and row.visible_data["identityStatus"]=="uncertain" and row.visible_data["aliases"]==["林渡"]
        snapshot=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["target"],
            m.KnowledgeSnapshot.branch_id==row.scope_id))
        assert snapshot.boundary==row.context["anchor"] and snapshot.data["characters"][0]["id"]==row.visible_data["id"]!="local-0"
        assert snapshot.data["evidence"][0]["id"]==row.visible_data["evidenceIds"][0]!="e1"
        alias=db.scalar(select(m.CharacterAlias).where(m.CharacterAlias.owner_id==domain["target"],m.CharacterAlias.scope_kind=="reader",
            m.CharacterAlias.scope_id==row.scope_id,m.CharacterAlias.entity_id==row.visible_data["id"]))
        assert alias.identity_id==row.identity_id
        session=db.scalar(select(m.RoleSession).where(m.RoleSession.owner_id==domain["target"]))
        assert session.character_identity_id==row.identity_id and session.snapshot_id==snapshot.id
        assert source.context["anchor"]==domain["anchor"] and source.visible_data["id"]=="local-0"
        for model in (m.ModelProfile,m.Consent,m.OperationPlan,m.UsageRecord):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain["target"]))==0


@pytest.mark.parametrize("fault",["aliases-number","aliases-duplicate","unproven-alias","missing-reason","bad-status",
    "zero-version","string-marker","wrong-context-branch","wrong-identity","forged-quote","derived-alias-number","derived-unproven-alias",
    "derived-ambiguity-boolean","derived-ambiguity-type"])
def test_reader_recognition_correction_encrypted_round_trip_invalid_is_atomic(domain,tmp_path,fault):
    correction_id=seed_reader_recognition_correction(domain)
    with SessionLocal() as db:
        row=db.get(m.CharacterScopeRevision,correction_id);value=deepcopy(row.visible_data);context=deepcopy(row.context)
        snapshot=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["owner"]))
        if fault=="aliases-number":value["aliases"]=7
        elif fault=="aliases-duplicate":value["aliases"]=["林渡","林渡"]
        elif fault=="unproven-alias":value["aliases"]=["林渡","未来身份"]
        elif fault=="missing-reason":value.pop("reason")
        elif fault=="bad-status":value["identityStatus"]="revived"
        elif fault=="zero-version":row.version=0
        elif fault=="string-marker":context["readerCorrection"]="true"
        elif fault=="wrong-context-branch":context["readerBranchId"]=db.scalar(select(m.Branch.id).where(m.Branch.parent_branch_id==domain["branch"]))
        elif fault=="wrong-identity":row.identity_id=domain["identities"][1]
        elif fault=="forged-quote":
            data=deepcopy(snapshot.data);data["evidence"][0]["quote"]="未来身份";snapshot.data=data
            value.update(displayName="未来身份",aliases=["未来身份"])
        elif fault.startswith("derived-"):
            data=deepcopy(snapshot.data);data.update(readerCorrectionApplied=True,readerCorrectionVersion=1)
            data["characters"][0].update(readerIdentityConfirmed=False,readerAliases=7 if fault=="derived-alias-number" else
                ["林渡"] if fault.startswith("derived-ambiguity") else ["未来身份"])
            if fault.startswith("derived-ambiguity"):data["characters"][0]["identityUncertain"]=False if fault.endswith("boolean") else "true"
            snapshot.data=data
        row.visible_data=value;row.context=context;db.commit()
    original=encrypted(domain,tmp_path)
    with http_client(domain["target"]) as client:
        prepared=prepared_http(client,original,"invalid-reader-correction-upload")
        response=client.post(f'/api/reader/v1/backups/{prepared["id"]}/restore',
            json={"confirmed":True,"expectedVersion":prepared["version"],"targetOwnerId":domain["target"]},
            headers={"Idempotency-Key":"invalid-reader-correction-confirm"})
        assert response.status_code==422 and response.json()["code"]=="BACKUP_READER_CORRECTION_INVALID",response.text
        operation=client.get(f'/api/reader/v1/backups/{prepared["id"]}').json()
        assert operation["status"]=="ready" and operation["version"]==prepared["version"]
    with SessionLocal() as db:
        for model in (m.Book,m.CharacterIdentity,m.CharacterAlias,m.CharacterScopeRevision,m.RoleSession,m.KnowledgeSnapshot,
            m.ModelProfile,m.Consent,m.OperationPlan,m.UsageRecord):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain["target"]))==0


@pytest.mark.parametrize("status",["confirmed","uncertain"])
def test_reader_recognition_correction_encrypted_round_trip_derived_versions_remain_usable(domain,tmp_path,status):
    correction_id=seed_reader_recognition_correction(domain)
    with SessionLocal() as db:
        row=db.get(m.CharacterScopeRevision,correction_id);row.visible_data={**row.visible_data,"identityStatus":status}
        base=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["owner"]))
        data=deepcopy(base.data);data.update(readerCorrectionApplied=True,readerCorrectionVersion=1)
        data["characters"][0].update(readerAliases=["林渡"],readerIdentityConfirmed=status=="confirmed",identityUncertain=status=="uncertain")
        db.add(m.KnowledgeSnapshot(owner_id=base.owner_id,book_id=base.book_id,branch_id=base.branch_id,revision_id=base.revision_id,
            boundary=deepcopy(base.boundary),scope_hash=digest("owned-derived-"+status),profile_version=base.profile_version,
            analyzer_version=base.analyzer_version,data=data,completeness=base.completeness));db.commit()
    result=restored(domain,tmp_path)
    with SessionLocal() as db:
        snapshots=list(db.scalars(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["target"])))
        assert len(snapshots)==2
        derived=next(snapshot for snapshot in snapshots if snapshot.data.get("readerCorrectionApplied"))
        character=derived.data["characters"][0]
        assert character["readerIdentityConfirmed"] is (status=="confirmed") and character["identityUncertain"] is (status=="uncertain")
        assert character["readerAliases"]==["林渡"] and derived.data["readerCorrectionVersion"]==1
        row=db.get(m.CharacterScopeRevision,result["idMap"][("character_scope_revision",correction_id)])
        assert row.context["anchor"]==derived.boundary and row.visible_data["id"]==character["id"]


def test_reader_recognition_correction_encrypted_round_trip_other_profile_cache_is_independent(domain,tmp_path):
    correction_id=seed_reader_recognition_correction(domain)
    with SessionLocal() as db:
        base=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["owner"]))
        data=deepcopy(base.data);data["characters"][0]["evidenceIds"]=["other-profile-evidence"]
        data["evidence"][0]["id"]="other-profile-evidence"
        db.add(m.KnowledgeSnapshot(owner_id=base.owner_id,book_id=base.book_id,branch_id=base.branch_id,revision_id=base.revision_id,
            boundary=deepcopy(base.boundary),scope_hash=digest("owned-other-profile"),profile_version="synthetic-profile-v2",
            analyzer_version=base.analyzer_version,data=data,completeness=base.completeness));db.commit()
    result=restored(domain,tmp_path)
    with SessionLocal() as db:
        snapshots=list(db.scalars(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["target"])))
        assert len(snapshots)==2 and len({snapshot.data["evidence"][0]["id"] for snapshot in snapshots})==2
        row=db.get(m.CharacterScopeRevision,result["idMap"][("character_scope_revision",correction_id)])
        assert sum(row.visible_data["evidenceIds"]==snapshot.data["characters"][0]["evidenceIds"] for snapshot in snapshots)==1
        assert all(snapshot.boundary==row.context["anchor"] for snapshot in snapshots)


@pytest.mark.parametrize("fault",[None,"wrong-quote","wrong-anchor","missing-proof"])
def test_reader_recognition_correction_encrypted_round_trip_cross_profile_derived_proofs(domain,tmp_path,fault):
    correction_id=seed_reader_recognition_correction(domain)
    with SessionLocal() as db:
        base=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["owner"]))
        proof={"quote":base.data["evidence"][0]["quote"],"anchor":deepcopy(base.data["evidence"][0]["anchor"])}
        if fault=="wrong-quote":proof["quote"]="未来身份"
        elif fault=="wrong-anchor":proof["anchor"]=deepcopy(domain["anchor"])
        row=db.get(m.CharacterScopeRevision,correction_id)
        row.visible_data={**row.visible_data,"proofs":[] if fault=="missing-proof" else [proof]}
        data=deepcopy(base.data);data.update(readerCorrectionApplied=True,readerCorrectionVersion=1)
        data["characters"][0].update(evidenceIds=["new-profile-proof"],readerAliases=["林渡"],readerIdentityConfirmed=False,
            identityUncertain=True,readerCorrectionVersion=1)
        data["evidence"][0]["id"]="new-profile-proof"
        db.add(m.KnowledgeSnapshot(owner_id=base.owner_id,book_id=base.book_id,branch_id=base.branch_id,revision_id=base.revision_id,
            boundary=deepcopy(base.boundary),scope_hash=digest("owned-translated-proof"),profile_version="synthetic-profile-v2",
            analyzer_version=base.analyzer_version,data=data,completeness=base.completeness));db.commit()
    original=encrypted(domain,tmp_path)
    with http_client(domain["target"]) as client:
        prepared=prepared_http(client,original,"cross-profile-proof-upload")
        response=client.post(f'/api/reader/v1/backups/{prepared["id"]}/restore',
            json={"confirmed":True,"expectedVersion":prepared["version"],"targetOwnerId":domain["target"]},
            headers={"Idempotency-Key":"cross-profile-proof-confirm"})
        if fault:
            assert response.status_code==422 and response.json()["code"]=="BACKUP_READER_CORRECTION_INVALID",response.text
            with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain["target"]))==0
            return
        assert response.status_code==200,response.text
    with SessionLocal() as db:
        snapshots=list(db.scalars(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["target"])))
        derived=next(snapshot for snapshot in snapshots if snapshot.data.get("readerCorrectionApplied"))
        row=db.scalar(select(m.CharacterScopeRevision).where(m.CharacterScopeRevision.owner_id==domain["target"],
            m.CharacterScopeRevision.context["readerCorrection"].as_boolean().is_(True)))
        assert derived.data["characters"][0]["evidenceIds"]!=row.visible_data["evidenceIds"]
        assert derived.data["evidence"][0]["anchor"]==row.visible_data["proofs"][0]["anchor"]
        assert derived.data["evidence"][0]["quote"]==row.visible_data["proofs"][0]["quote"]=="林渡"
        assert row.context["anchor"]==derived.boundary and row.visible_data["id"]==derived.data["characters"][0]["id"]


def test_reader_recognition_correction_encrypted_round_trip_duplicate_citation_keeps_profile_projection(domain,tmp_path):
    correction_id=seed_reader_recognition_correction(domain)
    with SessionLocal() as db:
        base=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["owner"]))
        original=deepcopy(base.data)
        proof={"quote":original["evidence"][0]["quote"],"anchor":deepcopy(original["evidence"][0]["anchor"])}
        original["evidence"].append({**deepcopy(original["evidence"][0]),"id":"same-citation-second-id"})
        original["characters"][0]["evidenceIds"].append("same-citation-second-id");base.data=original
        row=db.get(m.CharacterScopeRevision,correction_id)
        row.visible_data={**row.visible_data,"evidenceIds":["e1","same-citation-second-id"],"proofs":[proof,deepcopy(proof)]}
        data=deepcopy(original);data.update(readerCorrectionApplied=True,readerCorrectionVersion=1)
        data["evidence"]=[{**deepcopy(original["evidence"][0]),"id":"single-new-profile-id"}]
        data["characters"][0].update(evidenceIds=["single-new-profile-id"],readerAliases=["林渡"],
            readerIdentityConfirmed=False,identityUncertain=True,readerCorrectionVersion=1)
        db.add(m.KnowledgeSnapshot(owner_id=base.owner_id,book_id=base.book_id,branch_id=base.branch_id,revision_id=base.revision_id,
            boundary=deepcopy(base.boundary),scope_hash=digest("owned-deduplicated-citation"),profile_version="synthetic-profile-v2",
            analyzer_version=base.analyzer_version,data=data,completeness=base.completeness));db.commit()
    result=restored(domain,tmp_path)
    with SessionLocal() as db:
        row=db.get(m.CharacterScopeRevision,result["idMap"][("character_scope_revision",correction_id)])
        snapshots=list(db.scalars(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["target"])))
        derived=next(snapshot for snapshot in snapshots if snapshot.data.get("readerCorrectionApplied"))
        assert len(row.visible_data["evidenceIds"])==2 and len(derived.data["characters"][0]["evidenceIds"])==1
        assert all(item=={"quote":derived.data["evidence"][0]["quote"],"anchor":derived.data["evidence"][0]["anchor"]}
            for item in row.visible_data["proofs"])


def test_interrupted_task_never_requeued_or_given_budget(domain,tmp_path):
    result=restored(domain,tmp_path); ids=result["idMap"]
    with SessionLocal() as db:
        task=db.get(m.Task,ids[("task",domain["interrupted"])])
        assert task.state=="cancelled" and task.checkpoint=={} and task.provider_state=="not_started"
        assert task.payload["importedHistory"] is True and task.result["originalState"]=="queued"
        assert task.lease_owner is None and task.lease_expires_at is None
        assert "raw-private-journal" not in canonical(task.checkpoint)
        assert db.scalar(select(func.count()).select_from(m.Consent).where(m.Consent.owner_id==domain["target"]))==0


@pytest.mark.parametrize("kind",["book","paragraph","character_identity","task","world","checkpoint","world_event","memory","world_publication"])
def test_every_transaction_failure_rolls_back_new_story_and_owned_files(domain,tmp_path,kind):
    original=encrypted(domain,tmp_path)
    with original.open("rb") as stream,prepare_backup(stream,"synthetic-test-password",tmp_path/"import") as prepared:
        def fail(phase,current,_):
            if phase=="after-flush" and current==kind: raise OSError("synthetic disk/database fault")
        with SessionLocal() as db:
            with pytest.raises(OSError,match="synthetic"): restore_domain(db,prepared,domain["target"],fault=fail)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain["target"]))==0
        assert db.scalar(select(func.count()).select_from(w.World).where(w.World.owner_id==domain["target"]))==0
        assert db.get(m.Book,domain["book"]).status=="active"
    folder=settings.storage_path/domain["target"]
    assert not folder.exists() or not list(folder.iterdir())
    assert original.exists()


def test_same_account_and_repeated_restore_always_create_independent_copies(domain,tmp_path):
    original=encrypted(domain,tmp_path)
    results=[]
    for index in range(2):
        with original.open("rb") as stream,prepare_backup(stream,"synthetic-test-password",tmp_path/("import"+str(index))) as prepared:
            with SessionLocal() as db: results.append(restore_domain(db,prepared,domain["owner"]))
    assert results[0]["storyId"]!=results[1]["storyId"]!="book-"+domain["book"]
    assert not set(results[0]["idMap"].values()) & set(results[1]["idMap"].values())


def test_export_rejects_other_owner_and_deleted_target(domain,tmp_path):
    with SessionLocal() as db:
        with pytest.raises(Problem): build_domain_snapshot(db,domain["stranger"],"book-"+domain["book"],tmp_path/"export")
    original=encrypted(domain,tmp_path)
    with SessionLocal() as db:
        target=db.get(m.User,domain["target"]);target.status="deleting";db.commit()
    with original.open("rb") as stream,prepare_backup(stream,"synthetic-test-password",tmp_path/"import") as prepared:
        with SessionLocal() as db:
            with pytest.raises(ArchiveError,match="BACKUP_OWNER_NOT_ACTIVE"): restore_domain(db,prepared,domain["target"])


def test_stage_file_change_after_preview_rejected_before_any_domain_write(domain,tmp_path):
    original=encrypted(domain,tmp_path)
    with original.open("rb") as stream,prepare_backup(stream,"synthetic-test-password",tmp_path/"import") as prepared:
        path=prepared.files_root/prepared.manifest["files"][0]["path"]
        path.write_bytes(path.read_bytes()+b"tamper")
        with SessionLocal() as db:
            with pytest.raises(ArchiveError,match="BACKUP_FILE_HASH_MISMATCH"): restore_domain(db,prepared,domain["target"])


@pytest.mark.parametrize("event_name",["before_commit","after_commit"])
def test_real_postgresql_commit_error_resolves_outcome_before_file_cleanup(domain,tmp_path,event_name):
    original=encrypted(domain,tmp_path)
    with original.open("rb") as stream,prepare_backup(stream,"synthetic-test-password",tmp_path/"import") as prepared:
        with SessionLocal() as db:
            def break_ack(session):raise ConnectionError("synthetic commit acknowledgement fault")
            event.listen(db,event_name,break_ack,once=True)
            if event_name=="before_commit":
                with pytest.raises(ConnectionError):restore_domain(db,prepared,domain["target"])
            else:
                result=restore_domain(db,prepared,domain["target"])
                assert result["storyId"].startswith("book-")
    with SessionLocal() as db:
        sources=list(db.scalars(select(m.SourceDocument).where(m.SourceDocument.owner_id==domain["target"])))
        assert len(sources)==(1 if event_name=="after_commit" else 0)
        for source in sources:assert private_path(domain["target"],source.normalized_ref).read_text("utf-8")==domain["prose"]


def http_client(owner,*,upload_limit=None,diagnostic=False):
    app=FastAPI();register_errors(app);app.include_router(portable_router,prefix="/api/reader/v1");app.include_router(role_router,prefix="/api/reader/v1")
    from app.prose.api import router as prose_router
    app.include_router(prose_router,prefix="/api/reader/v1")
    from app.world.api import router as world_router
    from app.api_characters import router as characters_router
    app.include_router(world_router,prefix="/api/reader/v1");app.include_router(characters_router,prefix="/api/reader/v1")
    if diagnostic:
        original_handler=app.exception_handlers[Exception]
        async def report_synthetic_error(request,error):
            import traceback
            traceback.print_exception(error)
            return await original_handler(request,error)
        app.add_exception_handler(Exception,report_synthetic_error)
    from app.portable.upload_limit import BackupUploadLimit,MAX_REQUEST_BYTES
    app.add_middleware(BackupUploadLimit,max_bytes=MAX_REQUEST_BYTES if upload_limit is None else upload_limit)
    @app.middleware("http")
    async def request_id(request,call_next):
        request.state.request_id=uuid4().hex
        return await call_next(request)
    with SessionLocal() as db:
        session=m.DeviceSession(owner_id=owner,refresh_hash=digest(m.uid()),client_type="android",device_name="synthetic owned backup test",expires_at=m.now()+timedelta(hours=1))
        db.add(session);db.commit();token=issue_access(session)
    return TestClient(app,headers={"Authorization":"Bearer "+token},raise_server_exceptions=False)


@pytest.mark.parametrize("chunked",[False,True])
def test_real_asgi_backup_quota_stops_multipart_before_any_operation_or_stage(domain,tmp_path,chunked):
    with http_client(domain["owner"],upload_limit=4096) as client:
        if chunked:
            body=b'--limited\r\nContent-Disposition: form-data; name="file"; filename="owned.srbackup"\r\nContent-Type: application/octet-stream\r\n\r\n'+b"x"*8192+b'\r\n--limited\r\nContent-Disposition: form-data; name="password"\r\n\r\nsynthetic\r\n--limited--\r\n'
            response=client.post("/api/reader/v1/backups/prepare",content=iter([body[:2000],body[2000:5000],body[5000:]]),headers={"Content-Type":"multipart/form-data; boundary=limited","Idempotency-Key":"quota-test"})
            assert "content-length" not in response.request.headers
        else:
            response=client.post("/api/reader/v1/backups/prepare",files={"file":("owned.srbackup",b"x"*8192,"application/octet-stream")},data={"password":"synthetic"},headers={"Idempotency-Key":"quota-test"})
        assert response.status_code==413 and response.json()["code"]=="BACKUP_SIZE_LIMIT",response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PortableOperation).where(PortableOperation.owner_id==domain["owner"]))==0
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id==domain["owner"]))==0
    root=settings.storage_path/".portable"/domain["owner"]
    assert not root.exists() or not list(root.iterdir())


def prepared_http(client,original,key="upload"):
    with original.open("rb") as source:
        response=client.post("/api/reader/v1/backups/prepare",files={"file":("private.srbackup",source,"application/octet-stream")},data={"password":"synthetic-test-password"},headers={"Idempotency-Key":key})
    assert response.status_code==201,response.text
    return response.json()


@pytest.mark.parametrize("stream_version,snapshot_version",[(1,37),(2,37),(3,3)])
def test_streaming_snapshot_encrypted_round_trip_preserves_independent_counters(domain,tmp_path,monkeypatch,stream_version,snapshot_version):
    history=seed_prose_history(domain)
    with SessionLocal() as db:
        source=db.scalar(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==history["batch"],
            m.GenerationChapterVersion.candidate_version==2,m.GenerationChapterVersion.chapter_index==2))
        source.stream_version=stream_version;source.snapshot_version=snapshot_version;db.commit()
        source_values=(source.id,source.text,source.content_hash,source.stream_version,source.snapshot_version)
    original=encrypted(domain,tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError("Restoring saved chapters must not call a model or overlay")
    monkeypatch.setattr("app.worker.call_provider",forbidden);monkeypatch.setattr("app.prose.stream.overlay",forbidden)
    with http_client(domain["target"]) as client:
        prepared=prepared_http(client,original,"stream-snapshot-upload")
        response=client.post(f'/api/reader/v1/backups/{prepared["id"]}/restore',
            json={"confirmed":True,"expectedVersion":prepared["version"],"targetOwnerId":domain["target"]},
            headers={"Idempotency-Key":"stream-snapshot-confirm"})
        assert response.status_code==200,response.text
        with SessionLocal() as db:
            batch=db.scalar(select(m.GenerationBatch).where(m.GenerationBatch.owner_id==domain["target"]))
            restored=db.scalar(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==batch.id,
                m.GenerationChapterVersion.candidate_version==2,m.GenerationChapterVersion.chapter_index==2))
            assert restored.id!=source_values[0] and restored.owner_id==domain["target"]
            assert (restored.text,restored.content_hash,restored.stream_version,restored.snapshot_version)==source_values[1:]
            batch_id,head=batch.id,batch.last_accepted_revision_id
        view=client.get('/api/reader/v1/generations/'+batch_id)
        assert view.status_code==200,view.text
        chapter=view.json()["chapters"][1]
        assert chapter["text"]==source_values[1] and chapter["canAccept"]
        body={"candidateVersion":2,"prefixCount":2,"expectedAcceptedPrefix":1,"expectedHeadRevisionId":head,
            "target":"append","sourcePolicy":"requireUnchanged"}
        accepted=client.post('/api/reader/v1/generations/'+batch_id+'/accept',json=body,
            headers={"Idempotency-Key":"stream-snapshot-accept"})
        assert accepted.status_code==200,accepted.text
        repeated=client.post('/api/reader/v1/generations/'+batch_id+'/accept',json=body,
            headers={"Idempotency-Key":"stream-snapshot-accept"})
        assert repeated.status_code==200 and repeated.json()==accepted.json()
    with SessionLocal() as db:
        source=db.get(m.GenerationChapterVersion,source_values[0])
        assert (source.text,source.content_hash,source.stream_version,source.snapshot_version)==source_values[1:]
        for model in (m.ModelProfile,m.Consent,m.OperationPlan,m.UsageRecord):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain["target"]))==0


@pytest.mark.parametrize("stream_version,snapshot_version",[(0,37),(2,-1)])
def test_streaming_snapshot_encrypted_round_trip_invalid_counter_is_atomic(domain,tmp_path,stream_version,snapshot_version):
    history=seed_prose_history(domain)
    with SessionLocal() as db:
        source=db.scalar(select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==history["batch"],
            m.GenerationChapterVersion.candidate_version==2,m.GenerationChapterVersion.chapter_index==2))
        source.stream_version=stream_version;source.snapshot_version=snapshot_version;db.commit()
    original=encrypted(domain,tmp_path)
    with http_client(domain["target"]) as client:
        with original.open('rb') as source:
            response=client.post('/api/reader/v1/backups/prepare',files={'file':('invalid.srbackup',source,'application/octet-stream')},
                data={'password':'synthetic-test-password'},headers={'Idempotency-Key':'invalid-stream-snapshot-upload'})
        assert response.status_code==422 and response.json()["code"]=="BACKUP_GENERATION_INVALID",response.text
    with SessionLocal() as db:
        for model in (m.Book,m.GenerationBatch,m.RoleSession,m.ModelProfile,m.Consent,m.OperationPlan,m.UsageRecord):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain["target"]))==0


def test_encrypted_http_round_trip_imported_history_accepts_saved_prefix_without_replaying_task(domain,tmp_path,monkeypatch):
    history=seed_prose_history(domain)
    original=encrypted(domain,tmp_path)
    with http_client(domain["target"]) as client,http_client(domain["stranger"]) as stranger:
        prepared=prepared_http(client,original,"history-upload")
        response=client.post(f'/api/reader/v1/backups/{prepared["id"]}/restore',json={"confirmed":True,"expectedVersion":prepared["version"],"targetOwnerId":domain["target"]},headers={"Idempotency-Key":"history-confirm"})
        assert response.status_code==200,response.text
        with SessionLocal() as db:
            batch=db.scalar(select(m.GenerationBatch).where(m.GenerationBatch.owner_id==domain["target"]))
            batch_id,task_id,book_id,branch_id=batch.id,batch.task_id,batch.book_id,batch.target_branch_id
            head=batch.last_accepted_revision_id
            def immutable_rows():
                task=db.get(m.Task,task_id)
                task_values={column.name:deepcopy(getattr(task,column.name)) for column in m.Task.__table__.columns}
                def row_values(model,query):
                    return [{column.name:deepcopy(getattr(row,column.name)) for column in model.__table__.columns} for row in db.scalars(query)]
                return {"task":task_values,"events":row_values(m.TaskEvent,select(m.TaskEvent).where(m.TaskEvent.task_id==task_id).order_by(m.TaskEvent.id)),
                    "chapters":row_values(m.GenerationChapterVersion,select(m.GenerationChapterVersion).where(m.GenerationChapterVersion.generation_id==batch_id).order_by(m.GenerationChapterVersion.id))}
            before=immutable_rows()
        def forbidden(*args,**kwargs):raise AssertionError("Restored history must not call provider or stream overlay")
        monkeypatch.setattr("app.worker.call_provider",forbidden);monkeypatch.setattr("app.prose.stream.overlay",forbidden)
        url='/api/reader/v1/generations/'+batch_id
        view=client.get(url);assert view.status_code==200,view.text
        current=view.json();old=client.get(url,params={"candidateVersion":1}).json()
        assert current["importedHistory"] and current["readOnly"] and current["state"]=="failed"
        assert current["acceptedPrefix"]==1 and current["chapters"][1]["canAccept"] and not current["chapters"][2]["canAccept"]
        listing=client.get('/api/reader/v1/generations',params={"bookId":book_id,"branchId":branch_id}).json()
        assert next(item for item in listing["items"] if item["id"]==batch_id)["state"]==current["state"]
        assert stranger.get(url).status_code==404
        for operation,body in (("save",None),("rewrite",{"candidateVersion":2,"expectedAcceptedPrefix":1,"instruction":"继续"})):
            blocked=client.post(url+'/'+operation,json=body,headers={"Idempotency-Key":"blocked-history-"+operation})
            assert blocked.status_code==409 and blocked.json()["code"]=="IMPORTED_HISTORY_READ_ONLY",blocked.text
        body={"candidateVersion":2,"prefixCount":2,"expectedAcceptedPrefix":1,"expectedHeadRevisionId":head,"target":"append","sourcePolicy":"requireUnchanged"}
        accepted=client.post(url+'/accept',json=body,headers={"Idempotency-Key":"copy-history-prefix"})
        assert accepted.status_code==200,accepted.text
        repeated=client.post(url+'/accept',json=body,headers={"Idempotency-Key":"copy-history-prefix"})
        assert repeated.status_code==200 and repeated.json()==accepted.json()
        receipt=accepted.json();current=client.get(url).json()
        assert current["acceptedPrefix"]==2 and current["targetBranchId"]==branch_id==receipt["branchId"]
        assert current["lastAcceptedRevisionId"]==receipt["revisionId"]!=head
        assert client.get(url,params={"candidateVersion":1}).json()==old
        rejected=client.post(url+'/accept',json={**body,"prefixCount":3,"expectedAcceptedPrefix":2,"expectedHeadRevisionId":receipt["revisionId"]},headers={"Idempotency-Key":"reject-partial-history"})
        assert rejected.status_code==409,rejected.text
        with SessionLocal() as db:
            assert immutable_rows()==before
            assert db.get(m.Branch,branch_id).head_revision_id==receipt["revisionId"]
            assert db.scalar(select(func.count()).select_from(m.GenerationAcceptance).where(m.GenerationAcceptance.generation_id==batch_id))==2
            for model in (m.ModelProfile,m.Consent,m.OperationPlan,m.UsageRecord):
                assert db.scalar(select(func.count()).select_from(model).where(model.owner_id==domain["target"]))==0


@pytest.mark.parametrize("mode",["world-action","reader-chat"])
def test_restored_copy_can_run_new_mock_action_or_chat_with_new_account_profile(domain,tmp_path,mode,monkeypatch):
    result=restored(domain,tmp_path,world=mode=="world-action");ids=result["idMap"]
    with SessionLocal() as db:
        old_tasks={row.id:deepcopy(row.checkpoint) for row in db.scalars(select(m.Task).where(m.Task.owner_id==domain["target"]))}
        old_usage=db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id==domain["target"]))
        assert old_usage==0
    with http_client(domain["target"],diagnostic=True) as client:
        profile=client.post('/api/reader/v1/model-profiles',json={"name":"New account mock only","baseUrl":"mock://deterministic","modelId":"fixture-v1","key":"SYNTHETIC-ONLY-NOT-A-REAL-KEY","contextWindow":32768,"callBudget":100,"inputBudget":10000000,"outputBudget":1000000})
        assert profile.status_code==201,profile.text
        profile_id=profile.json()["id"]
        chosen=client.put('/api/reader/v1/model-profiles/'+profile_id+'/default');assert chosen.status_code==200,chosen.text
        if mode=="world-action":
            with SessionLocal() as db:
                world=db.get(w.World,ids[("world",domain["world"])]);world_id,line_id=world.id,world.active_line_id
                head=db.get(w.WorldLine,line_id).head_checkpoint_id
            url=f'/api/reader/v1/worlds/{world_id}/lines/{line_id}/actions'
            body={"message":"停下来观察四周","kind":"observe","expectedCheckpointId":head,"turnId":"new-restored-action"}
            from app.world.worker import process
            from app.world import worker as world_worker
            original_turn=world_worker.turn
            def traced_turn(identifier):
                try:return original_turn(identifier)
                except Exception:
                    import traceback
                    traceback.print_exc();raise
            monkeypatch.setattr(world_worker,"turn",traced_turn)
        else:
            with SessionLocal() as db:
                role=db.scalar(select(m.RoleSession).where(m.RoleSession.owner_id==domain["target"]))
                role_id=role.id
            url=f'/api/reader/v1/role-sessions/{role_id}/send'
            body={"message":"你好，可以聊聊这封信吗？","turnId":"new-restored-chat","profileId":profile_id}
            from app.worker import process
        response=client.post(url,json=body,headers={"Idempotency-Key":"new-owned-request"})
        assert response.status_code==202,response.text
        task_id=response.json()["taskId"];assert task_id not in old_tasks
        process(task_id)
        with SessionLocal() as db:
            task=db.get(m.Task,task_id)
            assert task.state=="succeeded" and task.persistence_state=="saved",(task.state,task.error_code,task.result)
            assert task.owner_id==domain["target"] and task.payload["profileId"]==profile_id
            usage=list(db.scalars(select(m.UsageRecord).where(m.UsageRecord.owner_id==domain["target"])))
            assert usage and all(row.profile_id==profile_id and row.task_id==task_id for row in usage)
            assert all(db.get(m.Task,identifier).checkpoint==checkpoint for identifier,checkpoint in old_tasks.items())
            if mode=="world-action":
                point=db.get(w.WorldCheckpoint,db.get(w.WorldLine,line_id).head_checkpoint_id)
                assert point.parent_checkpoint_id==head and point.id!=head and point.text.strip()
                assert point.state["playerId"] in point.state["entities"] and world_id==point.world_id
            else:
                assistant=db.scalar(select(m.RoleMessage).where(m.RoleMessage.session_id==role_id,m.RoleMessage.task_id==task_id,m.RoleMessage.role=="assistant"))
                assert assistant.status=="saved" and assistant.text.strip()
                assert db.get(m.RoleSession,role_id).pending_task_id is None


def test_actual_http_preview_confirm_replay_and_pure_lookup(domain,tmp_path):
    original=encrypted(domain,tmp_path)
    with http_client(domain["target"]) as client:
        assert client.get("/api/reader/v1/backups/capabilities").json()["encrypted"] is True
        prepared=prepared_http(client,original)
        assert prepared["status"]=="ready" and prepared["counts"]["world"]==1
        lookup=client.get("/api/reader/v1/backups/requests",params={"kind":"restore","requestId":"upload"}).json()
        assert lookup["found"] and lookup["operation"]["id"]==prepared["id"]
        body={"confirmed":True,"expectedVersion":prepared["version"],"targetOwnerId":domain["target"]}
        rejected=client.post(f'/api/reader/v1/backups/{prepared["id"]}/restore',json={**body,"confirmed":False},headers={"Idempotency-Key":"restore"})
        assert rejected.status_code==403
        saved=client.post(f'/api/reader/v1/backups/{prepared["id"]}/restore',json=body,headers={"Idempotency-Key":"restore"})
        assert saved.status_code==200,saved.text
        replay=client.post(f'/api/reader/v1/backups/{prepared["id"]}/restore',json=body,headers={"Idempotency-Key":"restore"})
        assert replay.status_code==200 and replay.json()==saved.json()
        assert client.get(f'/api/reader/v1/backups/{prepared["id"]}').json()==saved.json()
        assert original.exists()
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain["target"]))==1


def test_actual_http_encrypted_export_download_and_owner_isolation(domain,tmp_path):
    with http_client(domain["owner"]) as client:
        response=client.post(f'/api/reader/v1/stories/book-{domain["book"]}/backup',data={"password":"synthetic-test-password"},headers={"Idempotency-Key":"private-export"})
        assert response.status_code==201,response.text
        operation=response.json();download=client.get(f'/api/reader/v1/backups/{operation["id"]}/download')
        assert download.status_code==200 and b"\xe6\x9e\x97\xe6\xb8\xa1" not in download.content
        with prepare_backup(io.BytesIO(download.content),"synthetic-test-password",tmp_path/"decrypt") as prepared:
            assert prepared.object_counts["book"]==1
        with http_client(domain["stranger"]) as stranger:
            assert stranger.get(f'/api/reader/v1/backups/{operation["id"]}').status_code==404
            assert stranger.get(f'/api/reader/v1/backups/{operation["id"]}/download').status_code==404
            assert stranger.get("/api/reader/v1/backups/requests",params={"kind":"export","requestId":"private-export"}).json()["found"] is False


def test_actual_http_wrong_password_has_no_domain_write_or_plaintext_stage(domain,tmp_path):
    original=encrypted(domain,tmp_path)
    with http_client(domain["target"]) as client,original.open("rb") as stream:
        response=client.post("/api/reader/v1/backups/prepare",files={"file":("private.srbackup",stream)},data={"password":"wrong-password"},headers={"Idempotency-Key":"wrong-password"})
        assert response.status_code==422,response.text
    assert not list(portable_service.directory(domain["target"]).iterdir())
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain["target"]))==0
        op=db.scalar(select(PortableOperation).where(PortableOperation.owner_id==domain["target"]))
        assert op.status=="failed" and "password" not in canonical(op.result)


@pytest.mark.parametrize("corruption",["missing-state","string-alive","string-quantity","string-rules","unknown-actor","unknown-place"])
def test_invalid_persistent_world_state_never_becomes_a_half_story(domain,tmp_path,corruption):
    with SessionLocal() as db:
        point=db.get(w.WorldCheckpoint,domain["checkpoint"])
        state=deepcopy(point.state);definition=deepcopy(point.definition)
        if corruption=="missing-state":state={}
        elif corruption=="string-alive":state["entities"][state["playerId"]]["alive"]="false"
        elif corruption=="string-quantity":
            item=next(e for e in definition["entities"] if e["type"]=="item")
            item["stackable"]=True;state["entities"][state["playerId"]]["inventory"][item["id"]]="1"
        elif corruption=="string-rules":definition["rules"]["randomChecks"]="false"
        elif corruption=="unknown-actor":state["playerId"]="nonexistent-person"
        elif corruption=="unknown-place":state["location"]="nonexistent-place"
        point.state=state;point.definition=definition;db.commit()
    original=encrypted(domain,tmp_path)
    before=set((settings.storage_path/domain["target"]).glob("*"))
    with original.open("rb") as source,prepare_backup(source,"synthetic-test-password",tmp_path/"import") as prepared:
        with SessionLocal() as db,pytest.raises(ArchiveError,match="BACKUP_WORLD_STATE_INVALID"):
            restore_domain(db,prepared,domain["target"])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain["target"]))==0
        assert db.scalar(select(func.count()).select_from(w.World).where(w.World.owner_id==domain["target"]))==0
    assert set((settings.storage_path/domain["target"]).glob("*"))==before and original.is_file()


@pytest.mark.parametrize("corruption",["event","memory-line","memory-event","checkpoint-episode"])
def test_cross_world_references_rejected_even_when_every_row_is_in_archive(domain,tmp_path,corruption):
    with SessionLocal() as db:
        old=db.get(w.World,domain["world"]);point=db.get(w.WorldCheckpoint,domain["checkpoint"])
        wid,lid,cid=[m.uid() for _ in range(3)]
        other=w.World(id=wid,owner_id=domain["owner"],name="第二个世界",source_book_id=domain["book"],source_title=old.source_title,source=deepcopy(old.source),definition=deepcopy(point.definition),status="active",active_line_id=lid)
        db.add(other);db.flush()
        line=w.WorldLine(id=lid,owner_id=domain["owner"],world_id=wid,name="第二条主线",head_checkpoint_id=cid)
        db.add(line);db.flush()
        cp=w.WorldCheckpoint(id=cid,owner_id=domain["owner"],world_id=wid,line_id=lid,sequence=1,title="另一个起点",text="另一封信。",state=deepcopy(point.state),definition=deepcopy(point.definition),content_hash=digest("other-point"),kind="start")
        db.add(cp);db.flush()
        token=digest(cid+":event")[:24]
        other_event=w.WorldEvent(id=m.uid(),owner_id=domain["owner"],world_id=wid,checkpoint_id=cid,type="heard",data={"id":token,"text":"另一个约定"},visible_to=[cp.state["playerId"]],minute=1)
        db.add(other_event);db.flush()
        if corruption=="event":
            event=db.scalar(select(w.WorldEvent).where(w.WorldEvent.world_id==domain["world"]));event.checkpoint_id=cid
        elif corruption=="memory-line":
            memory=db.scalar(select(w.WorldMemory).where(w.WorldMemory.world_id==domain["world"]));memory.line_id=lid
        elif corruption=="memory-event":
            memory=db.scalar(select(w.WorldMemory).where(w.WorldMemory.world_id==domain["world"]));memory.event_ids=[other_event.id]
        else:
            point.memory={"episodes":[{"firstCheckpointId":domain["root_checkpoint"],"lastCheckpointId":cid,"text":"不属于本线的经历","eventIds":[other_event.id]}]}
        db.commit()
    original=encrypted(domain,tmp_path)
    with original.open("rb") as source,prepare_backup(source,"synthetic-test-password",tmp_path/"import") as prepared:
        with SessionLocal() as db,pytest.raises(ArchiveError,match="BACKUP_WORLD_GRAPH_INVALID"):
            restore_domain(db,prepared,domain["target"])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain["target"]))==0
        assert db.scalar(select(func.count()).select_from(w.World).where(w.World.owner_id==domain["target"]))==0


def test_two_real_http_confirmations_create_one_receipt_copy(domain,tmp_path):
    original=encrypted(domain,tmp_path)
    with http_client(domain["target"]) as first,http_client(domain["target"]) as second:
        operation=prepared_http(first,original)
        body={"confirmed":True,"expectedVersion":operation["version"],"targetOwnerId":domain["target"]}
        def confirm(client):return client.post(f'/api/reader/v1/backups/{operation["id"]}/restore',json=body,headers={"Idempotency-Key":"one-shared-confirm"})
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures=[executor.submit(confirm,client) for client in (first,second)]
            responses=[future.result() for future in futures]
        assert [response.status_code for response in responses]==[200,200],[r.text for r in responses]
        assert responses[0].json()==responses[1].json()
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain["target"]))==1


def test_expiry_cleanup_and_account_tombstone_remove_only_owned_private_stage(domain,tmp_path):
    original=encrypted(domain,tmp_path)
    with http_client(domain["target"]) as client:operation=prepared_http(client,original)
    with SessionLocal() as db:
        op=db.get(PortableOperation,operation["id"]);op.expires_at=m.now()-timedelta(seconds=1);db.commit()
    assert portable_service.maintenance()==1
    assert not list(portable_service.directory(domain["target"]).iterdir())
    preserved=portable_service.directory(domain["stranger"]);(preserved/"unrelated-owned-fixture.txt").write_text("preserve")
    tombstone=settings.storage_path/".deleted"/domain["target"]
    tombstone.parent.mkdir(exist_ok=True);tombstone.touch()
    portable_service.purge_owner_files(domain["target"])
    with pytest.raises(ArchiveError,match="BACKUP_OWNER_NOT_ACTIVE"):portable_service.directory(domain["target"])
    assert (preserved/"unrelated-owned-fixture.txt").read_text()=="preserve"


def test_expired_cleanup_continues_after_failed_stages_and_retains_retry_receipts(domain,tmp_path,monkeypatch):
    failed=[];good=[]
    with SessionLocal() as db:
        for index in range(8):
            operation,fresh=portable_service.create_operation(db,domain["target"],"restore","expired-"+str(index),{"fixture":index})
            assert fresh
            root=portable_service.allocate_stage(db,operation,domain["target"])
            (root/"synthetic-private.txt").write_text("owned private fixture")
            operation=portable_service.owned_operation(db,domain["target"],operation.id)
            operation.expires_at=m.now()-timedelta(hours=1)+timedelta(seconds=index);db.commit()
            (failed if index<6 else good).append((operation.id,root))
    original=portable_service._remove_owned_stage
    failures={root.name for _,root in failed}
    def denied(root,parent,prefix):
        if root.name in failures:raise PermissionError("synthetic owned cleanup fault")
        original(root,parent,prefix)
    monkeypatch.setattr(portable_service,"_remove_owned_stage",denied)
    with SessionLocal() as db:
        assert portable_service.cleanup_expired(db,limit=2,scan_limit=8)==2;db.commit()
        for identifier,root in failed:
            operation=db.get(PortableOperation,identifier)
            assert operation.stage_name==root.name and operation.status=="preparing" and operation.result["cleanupAttempts"]==1 and root.is_dir()
        for identifier,root in good:
            assert db.get(PortableOperation,identifier).stage_name is None and not root.exists()
    # Backoff lets a later row through even if the finite scan budget is smaller
    # than all preceding failures; it never claims their private files are gone.
    with SessionLocal() as db:
        operation,_=portable_service.create_operation(db,domain["target"],"restore","later-expired",{})
        root=portable_service.allocate_stage(db,operation,domain["target"])
        operation=portable_service.owned_operation(db,domain["target"],operation.id);operation.expires_at=m.now()-timedelta(minutes=1);db.commit()
        assert portable_service.cleanup_expired(db,limit=1,scan_limit=1)==1;db.commit()
        assert not root.exists()


def test_crash_after_stage_allocation_leaves_a_durable_cleanup_target(domain,tmp_path):
    with SessionLocal() as db:
        operation,fresh=portable_service.create_operation(db,domain["target"],"restore","interrupted-preparation",{})
        root=portable_service.allocate_stage(db,operation,domain["target"])
        (root/"partial-payload.zip").write_bytes(b"synthetic decrypted partial fixture")
        identifier=operation.id
    # New DB connection represents restart; no password/key/upload is stored.
    with SessionLocal() as db:
        operation=db.get(PortableOperation,identifier)
        assert operation.status=="preparing" and operation.stage_name==root.name and operation.payload_hash==digest(canonical({}))
        operation.expires_at=m.now()-timedelta(seconds=1);db.commit()
        assert portable_service.cleanup_expired(db)==1;db.commit()
        assert not root.exists() and db.get(PortableOperation,identifier).stage_name is None


def seed_legacy_publication(domain):
    with SessionLocal() as db:
        branch=db.scalar(select(m.Branch).where(m.Branch.book_id==domain["book"],m.Branch.id!=domain["branch"]))
        revision=db.get(m.Revision,branch.head_revision_id)
        revision.provenance={"operation":"world_publication","worldId":domain["world"],"checkpointId":domain["checkpoint"]}
        block=db.get(m.ContentBlock,revision.manifest[-1]["blocks"][-1])
        anchor=make_anchor(domain["book"],branch.id,revision.id,block,len(block.text.encode("utf-16-le"))//2)
        publication=w.WorldPublication(id=m.uid(),owner_id=domain["owner"],world_id=domain["world"],checkpoint_id=domain["checkpoint"],book_id=domain["book"],branch_id=branch.id,revision_id=revision.id,anchor=anchor)
        original=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.book_id==domain["book"]))
        snapshot=m.KnowledgeSnapshot(id=m.uid(),owner_id=domain["owner"],book_id=domain["book"],branch_id=branch.id,revision_id=revision.id,boundary=anchor,scope_hash=digest(m.uid()),profile_version="synthetic-publication",data=deepcopy(original.data),completeness="complete")
        role=m.RoleSession(id=m.uid(),owner_id=domain["owner"],book_id=domain["book"],branch_id=branch.id,pinned_revision_id=revision.id,pinned_anchor=anchor,character_id="local-0",character_name="林渡",character_identity_id=domain["identity"],snapshot_id=snapshot.id)
        db.add_all([publication,snapshot,role]);db.flush()
        db.add_all([m.CharacterAlias(id=m.uid(),owner_id=domain["owner"],identity_id=domain["identity"],scope_kind="reader",scope_id=branch.id,entity_id="local-0"),
            m.RoleMessage(id=m.uid(),owner_id=domain["owner"],session_id=role.id,turn_id="retained-chat",sequence=1,role="assistant",text="我还记得这条故事线。",task_id=domain["task"])])
        db.commit();return {"branch":branch.id,"revision":revision.id,"session":role.id,"publication":publication.id,"block":block.id}


def test_world_only_legacy_publication_restores_exact_family_without_original_novel(domain,tmp_path):
    future="这段原文尚未出版，也不属于世界冻结资料。未来真相不会随世界包出现。"
    with SessionLocal() as db:
        revision=db.get(m.Revision,domain["revision"]);block=m.ContentBlock(id=m.uid(),owner_id=domain["owner"],book_id=domain["book"],chapter_id=m.uid(),text=future,content_hash=digest(future),source="original")
        db.add(block);revision.manifest=revision.manifest+[{"id":block.chapter_id,"title":"未发布的未来","blocks":[block.id]}]
        source=db.get(m.SourceDocument,db.get(m.Book,domain["book"]).source_id)
        future_identity=m.CharacterIdentity(id=m.uid(),owner_id=domain["owner"],origin_key="future:"+m.uid(),source_ref={"kind":"reader-proof","bookId":domain["book"],"branchId":domain["branch"],"blockId":block.id,"start":0,"originalLabel":"未来人物"})
        db.add(future_identity);db.flush()
        source.source_map={**source.source_map,"characterBindings":source.source_map["characterBindings"]+[{"identityId":future_identity.id,"blockId":block.id,"start":0,"name":"未来人物"}]}
        future_id=future_identity.id
        write_private(domain["owner"],source.normalized_ref,(domain["prose"]+"\n\n"+future).encode());source.file_hash=digest(domain["prose"]+"\n\n"+future);db.commit()
    published=seed_legacy_publication(domain)
    result=restored(domain,tmp_path,world=True);ids=result["idMap"]
    assert ("book",domain["book"]) not in ids and ("branch",domain["branch"]) not in ids
    assert ("character_identity",future_id) not in ids
    with SessionLocal() as db:
        books=list(db.scalars(select(m.Book).where(m.Book.owner_id==domain["target"])))
        assert len(books)==1
        source=db.get(m.SourceDocument,books[0].source_id)
        assert future not in private_path(domain["target"],source.normalized_ref).read_text("utf-8")
        assert not any(block.text==future for block in db.scalars(select(m.ContentBlock).where(m.ContentBlock.owner_id==domain["target"])))
        branch=db.get(m.Branch,ids[("branch",published["branch"])])
        assert branch.book_id==books[0].id and branch.parent_branch_id is None and branch.fork_anchor is None
        publication=db.get(w.WorldPublication,ids[("world_publication",published["publication"])])
        assert publication.book_id==books[0].id;validate_anchor(db,publication.anchor,domain["target"])
        role=db.get(m.RoleSession,ids[("role_session",published["session"])])
        assert role.book_id==books[0].id and role.character_identity_id==ids[("character_identity",domain["identity"])]
        world=db.get(w.World,ids[("world",domain["world"])])
        assert world.source_book_id is None and world.source["sourceStatus"]=="source-not-imported"
        original=db.get(m.Branch,domain["branch"])
        assert original.book_id==domain["book"] and db.get(m.Book,domain["book"]) is not None


def test_actual_source_delete_then_backup_restores_retained_role_history_without_fabricated_snapshot(domain,tmp_path):
    published=seed_legacy_publication(domain)
    with SessionLocal() as db:
        book=db.get(m.Book,domain["book"]);receipt=prepare_delete(db,domain["owner"],book.id,book.version)
        db.commit();carrier=receipt.retained[0]["bookId"]
    clean_deleted_books(domain["owner"],domain["book"])
    with SessionLocal() as db:
        role=db.get(m.RoleSession,published["session"])
        assert role.snapshot_id=="" and role.status=="needs_refresh"
    original=tmp_path/"detached.srbackup"
    with SessionLocal() as db:
        with build_domain_snapshot(db,domain["owner"],"book-"+carrier,tmp_path/"snapshot") as snapshot:
            with original.open("wb") as stream:write_backup(snapshot.root,snapshot.manifest,stream,"synthetic-test-password",tmp_path/"export")
    with original.open("rb") as stream,prepare_backup(stream,"synthetic-test-password",tmp_path/"import") as prepared:
        with SessionLocal() as db:result=restore_domain(db,prepared,domain["target"])
    identifier=result["idMap"][("role_session",published["session"])]
    with http_client(domain["target"]) as client:
        response=client.get(f"/api/reader/v1/role-sessions/{identifier}")
        assert response.status_code==200,response.text
        assert response.json()["status"]=="needs_refresh"
        messages=client.get(f"/api/reader/v1/role-sessions/{identifier}/messages")
        assert messages.status_code==200 and messages.json()["items"][0]["text"]=="我还记得这条故事线。"
    with SessionLocal() as db:
        assert db.get(m.RoleSession,identifier).snapshot_id==""
        assert db.scalar(select(func.count()).select_from(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id==domain["target"]))==0


@pytest.mark.parametrize("kind",["note","progress","knowledge"])
def test_anchor_scope_must_match_the_row_even_when_both_branches_are_valid(domain,tmp_path,kind):
    with SessionLocal() as db:
        branch=db.scalar(select(m.Branch).where(m.Branch.book_id==domain["book"],m.Branch.id!=domain["branch"]))
        block=db.get(m.ContentBlock,domain["block"])
        anchor=make_anchor(domain["book"],branch.id,branch.head_revision_id,block,0,2)
        if kind=="note":row=db.scalar(select(m.Bookmark).where(m.Bookmark.book_id==domain["book"]));row.anchor=anchor
        elif kind=="progress":row=db.scalar(select(m.Progress).where(m.Progress.book_id==domain["book"]));row.max_observed_anchor=anchor
        else:row=db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.book_id==domain["book"]));row.boundary=anchor
        db.commit()
    original=encrypted(domain,tmp_path)
    with original.open("rb") as stream,prepare_backup(stream,"synthetic-test-password",tmp_path/"import") as prepared:
        with SessionLocal() as db,pytest.raises(ArchiveError,match="BACKUP_READER_GRAPH_INVALID"):restore_domain(db,prepared,domain["target"])


@pytest.mark.parametrize("kind",["branch","revision","line","reading-position"])
def test_cyclic_parent_or_foreign_reading_position_cannot_restore(domain,tmp_path,kind):
    with SessionLocal() as db:
        if kind=="branch":
            first=db.get(m.Branch,domain["branch"]);second=db.scalar(select(m.Branch).where(m.Branch.book_id==domain["book"],m.Branch.id!=first.id));first.parent_branch_id=second.id
        elif kind=="revision":
            first=db.get(m.Revision,domain["revision"]);second=db.scalar(select(m.Revision).where(m.Revision.owner_id==domain["owner"],m.Revision.id!=first.id));first.parent_revision_id=second.id;second.parent_revision_id=first.id
        else:
            first=db.get(w.WorldLine,db.get(w.World,domain["world"]).active_line_id);second=db.scalar(select(w.WorldLine).where(w.WorldLine.world_id==domain["world"],w.WorldLine.id!=first.id))
            if kind=="line":first.parent_line_id=second.id
            else:first.reading_position={"checkpointId":domain["fork"]}
        db.commit()
    original=encrypted(domain,tmp_path)
    with original.open("rb") as stream,prepare_backup(stream,"synthetic-test-password",tmp_path/"import") as prepared:
        with SessionLocal() as db,pytest.raises(ArchiveError,match="BACKUP_(READER|WORLD)_GRAPH_INVALID"):restore_domain(db,prepared,domain["target"])


@pytest.mark.parametrize("purpose",["cover","illustration","scene","avatar"])
def test_uploaded_images_restore_original_and_every_variant_hash_with_exact_scope(domain,tmp_path,purpose):
    with SessionLocal() as db:
        if purpose in {"scene","avatar"}:
            world=db.get(w.World,domain["world"]);cp=db.get(w.WorldCheckpoint,domain["checkpoint"])
            scope={"kind":"world","worldId":world.id,"lineId":world.active_line_id,"checkpointId":cp.id}
            if purpose=="avatar":scope.update(identityId=domain["identity"],entityId=cp.state["playerId"])
            content=cp.id if purpose=="scene" else None;paragraph="0" if content else None
        else:
            scope={"kind":"book","bookId":domain["book"],"branchId":domain["branch"],"revisionId":domain["revision"]}
            content=domain["block"] if purpose=="illustration" else None;paragraph=content
        asset=create_asset(db,domain["owner"],domain["image"],"image/png",purpose,{"x":0,"y":0,"width":8,"height":8 if purpose=="avatar" else 12},scope,"Original synthetic test artwork; private recovery allowed")
        placement=place_asset(db,domain["owner"],PlacementInput(assetId=asset["assetId"],scope=scope,purpose=purpose,contentId=content,paragraphId=paragraph,alt="原创灯下插图"))
        db.commit();aid=asset["assetId"];pid=placement["id"]
        old={row.name:row.sha256 for row in db.scalars(select(AssetVariant).where(AssetVariant.asset_id==aid))}
    result=restored(domain,tmp_path);ids=result["idMap"]
    with SessionLocal() as db:
        asset=db.get(ImageAsset,ids[("asset",aid)]);placement=db.get(AssetPlacement,ids[("asset_ref",pid)])
        assert asset.owner_id==placement.owner_id==domain["target"] and asset.id!=aid
        assert placement.alt=="原创灯下插图" and asset.license_note=="Original synthetic test artwork; private recovery allowed"
        actual={row.name:row.sha256 for row in db.scalars(select(AssetVariant).where(AssetVariant.asset_id==asset.id))}
        assert actual==old
        for name,sha in old.items():
            value=variant_bytes(db,domain["target"],asset.id,name,placement.scope)
            assert digest(value.data)==sha
        if purpose=="illustration":assert placement.content_id==placement.paragraph_id==ids[("paragraph",domain["block"])]
        if purpose=="scene":assert placement.content_id==ids[("checkpoint",domain["checkpoint"])] and placement.paragraph_id=="0"
        if purpose=="avatar":assert placement.scope["identityId"]==ids[("character_identity",domain["identity"])]


def test_confirmed_import_and_parent_flow_are_readonly_history_not_replayed(domain,tmp_path):
    with SessionLocal() as db:
        parent=m.Task(id=m.uid(),owner_id=domain["owner"],task_type="story_flow",state="failed",payload={"flowKind":"reader_continue","operation":{"anchor":domain["anchor"]},"profileId":"synthetic-old-private-profile"},result={})
        imported=m.Task(id=m.uid(),owner_id=domain["owner"],task_type="import",state="succeeded",payload={"fileRef":"old-private-upload"},result={"bookId":domain["book"],"confirmed":True})
        db.add_all([parent,imported]);db.commit();old_ids=[parent.id,imported.id]
    result=restored(domain,tmp_path);ids=result["idMap"]
    with SessionLocal() as db:
        rows=[db.get(m.Task,ids[("task",identifier)]) for identifier in old_ids]
        assert [row.state for row in rows]==["cancelled","succeeded"]
        assert all(row.checkpoint=={} and row.usage_state=="historical" and "profileId" not in row.payload for row in rows)


@pytest.mark.parametrize("kind",["inline-text","publication-anchor","self-fork"])
def test_individually_valid_body_or_anchor_cannot_conflict_with_owning_record(domain,tmp_path,kind):
    published=seed_legacy_publication(domain)
    with SessionLocal() as db:
        if kind=="inline-text":db.get(m.ContentBlock,domain["block"]).html="<p>这是另一段与所存正文不同的文字。</p>"
        elif kind=="publication-anchor":db.get(w.WorldPublication,published["publication"]).anchor=domain["anchor"]
        else:
            branch=db.get(m.Branch,published["branch"]);branch.fork_anchor=db.get(w.WorldPublication,published["publication"]).anchor
        db.commit()
    original=encrypted(domain,tmp_path)
    with original.open("rb") as stream,prepare_backup(stream,"synthetic-test-password",tmp_path/"import") as prepared:
        with SessionLocal() as db,pytest.raises(ArchiveError,match="BACKUP_(BODY_INLINE_MISMATCH|READER_GRAPH_INVALID)"):restore_domain(db,prepared,domain["target"])


def test_actual_owned_process_sigkill_during_decrypt_is_cleaned_from_durable_stage(domain,tmp_path):
    original=encrypted(domain,tmp_path);original_hash=digest(original.read_bytes())
    code='''
import json,time,sys
from app.db import SessionLocal
from app.portable import package,service
decrypt=package.decrypt_payload
class KillPoint:
    def __init__(self,target):self.target=target;self.first=True
    def write(self,value):
        result=self.target.write(value);self.target.flush()
        if self.first:
            self.first=False;print(json.dumps({"phase":"decrypted-first-bytes"}),flush=True);time.sleep(3600)
        return result
def stopped(source,output,password):return decrypt(source,KillPoint(output),password)
package.decrypt_payload=stopped
with SessionLocal() as db,open(sys.argv[2],"rb") as upload:
    service.preview_backup(db,sys.argv[1],"owned-sigkill-import",upload,"synthetic-test-password")
'''
    child=subprocess.Popen([sys.executable,"-c",code,domain["target"],str(original)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        reached=Queue()
        Thread(target=lambda:reached.put(child.stdout.readline()),daemon=True).start()
        assert json.loads(reached.get(timeout=15))=={"phase":"decrypted-first-bytes"}
        assert child.poll() is None
        with SessionLocal() as db:
            operation=db.scalar(select(PortableOperation).where(PortableOperation.owner_id==domain["target"],PortableOperation.request_key=="owned-sigkill-import"))
            assert operation.status=="preparing" and operation.stage_name
            root=portable_service.stage_path(domain["target"],operation.stage_name)
            assert (root/"payload.zip").stat().st_size>0
            identifier=operation.id
        child.kill()
        exit_code=child.wait(timeout=10)
        assert exit_code != 0 if os.name=='nt' else exit_code == -signal.SIGKILL
        with SessionLocal() as db:
            operation=db.get(PortableOperation,identifier);operation.expires_at=m.now()-timedelta(seconds=1);db.commit()
        # Windows may observe the virtualenv launcher exit before the actual
        # decrypting process has fully exited. Cleanup is eventual and another
        # maintenance pass may already have settled this operation; verify the
        # real postcondition rather than one call's processing counter.
        cleanup_deadline=time.monotonic()+5
        while True:
            portable_service.maintenance()
            with SessionLocal() as db:
                expired=db.get(PortableOperation,identifier).status=='expired'
            if expired or time.monotonic()>=cleanup_deadline:break
            time.sleep(.02)
        with SessionLocal() as db:
            operation=db.get(PortableOperation,identifier)
            assert operation.status=="expired" and operation.stage_name is None
            assert db.scalar(select(func.count()).select_from(m.Book).where(m.Book.owner_id==domain["target"]))==0
        assert not root.exists() and digest(original.read_bytes())==original_hash
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)


def test_encrypted_same_identity_duplicate_version_across_local_ids_is_atomic(domain, tmp_path):
    identifier = seed_reader_recognition_correction(domain)
    with SessionLocal() as db:
        old = db.get(m.CharacterScopeRevision, identifier)
        base = db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id == domain['owner']))
        fresh = 'different-analysis-local-id'
        data = deepcopy(base.data)
        data['characters'][0]['id'] = fresh
        db.add(m.KnowledgeSnapshot(owner_id=base.owner_id, book_id=base.book_id, branch_id=base.branch_id,
            revision_id=base.revision_id, boundary=deepcopy(base.boundary), scope_hash=digest('owned-duplicate-local-projection'),
            profile_version=base.profile_version, analyzer_version=base.analyzer_version, data=data, completeness=base.completeness))
        db.add(m.CharacterAlias(owner_id=old.owner_id, identity_id=old.identity_id, scope_kind='reader',
            scope_id=old.scope_id, entity_id=fresh))
        db.add(m.CharacterScopeRevision(owner_id=old.owner_id, identity_id=old.identity_id, scope_kind=old.scope_kind,
            scope_id=old.scope_id, scope_key='reader-correction:other-local-duplicate-version', context=deepcopy(old.context),
            visible_data={**deepcopy(old.visible_data), 'id': fresh}, version=old.version))
        db.commit()
    archive = encrypted(domain, tmp_path)
    with http_client(domain['target']) as client:
        prepared = prepared_http(client, archive, 'duplicate-identity-upload')
        response = client.post('/api/reader/v1/backups/' + prepared['id'] + '/restore',
            json={'confirmed': True, 'expectedVersion': prepared['version'], 'targetOwnerId': domain['target']},
            headers={'Idempotency-Key': 'duplicate-identity-confirm'})
        assert response.status_code == 422 and response.json()['code'] == 'BACKUP_READER_CORRECTION_INVALID', response.text
        current = client.get('/api/reader/v1/backups/' + prepared['id']).json()
        assert current['status'] == 'ready' and current['version'] == prepared['version']
    with SessionLocal() as db:
        for model in [m.Book, m.CharacterIdentity, m.CharacterAlias, m.CharacterScopeRevision, m.KnowledgeSnapshot,
            m.RoleSession, m.ModelProfile, m.UsageRecord]:
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id == domain['target'])) == 0
