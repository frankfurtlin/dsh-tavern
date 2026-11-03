"""Actual image bytes + fresh PostgreSQL; no production registration or providers."""
import io
import json
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4
from threading import Event
from time import monotonic, sleep

import pytest
from PIL import Image, PngImagePlugin
from sqlalchemy import select, func, text, event

from app.config import settings
from app.db import engine, SessionLocal
from app.main import app
from app.models import User, Book, Branch, Revision, ContentBlock, Progress, ModelProfile, UsageRecord, Task, CharacterIdentity, CharacterScopeRevision, uid
from app.providers import MockAdapter, ChatCompletionsAdapter
from app.domain import digest, make_anchor
from app.errors import Problem
from app.assets.api import router
from app.assets.models import AssetsBase, ASSET_TABLES, ImageAsset, AssetVariant, AssetPlacement
from app.assets.imaging import prepare, decode, MAX_UPLOAD
from app.assets.schemas import PlacementInput
from app.assets.service import create_asset, place_asset, detach_book, purge_scope, purge_owner
from app.world.models import World, WorldLine, WorldCheckpoint
from app.world.state import initial_state, checkpoint_hash
from .helpers import BASE, read_chapter, observe

# Preserve standalone package testing; registered Main uses its actual router.
if not any(getattr(route, 'path', None) == BASE + '/assets/capabilities' for route in app.routes):
    app.include_router(router, prefix=BASE)

@pytest.fixture(autouse=True)
def private_assets(reset_db, monkeypatch, tmp_path):
    assert engine.url.database == "reader_test" and settings.ai_mode == "mock"
    monkeypatch.setattr(settings, "storage_path", tmp_path)
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Image upload/placement must never invoke a provider")
    for adapter in (MockAdapter, ChatCompletionsAdapter):
        monkeypatch.setattr(adapter, "generate", forbidden)
        monkeypatch.setattr(adapter, "generate_stream", forbidden)
    # Exactly these three new tables, only in our fresh_test fixture.
    AssetsBase.metadata.create_all(engine, tables=ASSET_TABLES)
    yield
    assert attempts == []

def png(size=(120,180), metadata=False):
    image = Image.new("RGB", size, "#7d9874")
    out = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_text("GPS", "PRIVATE-LOCATION-NOT-PERSISTED")
        info.add_text("Comment", "PRIVATE-METADATA-NOT-PERSISTED")
    image.save(out, "PNG", pnginfo=info)
    return out.getvalue()

def scope(demo):
    return {"kind":"book","bookId":demo["bookId"],"branchId":demo["branchId"],"revisionId":demo["revisionId"]}

def upload(client, at, purpose="cover", data=None, mime="image/png", rect=None, key=None):
    data = png() if data is None else data
    rect = {"x":0,"y":0,"width":120,"height":180} if rect is None else rect
    return client.post(BASE+"/assets", files={"file":("synthetic.png",data,mime)},
        data={"purpose":purpose,"scope":json.dumps(at),"crop":json.dumps(rect),
              "licenseNote":"自编测试图，CC0","rightsConfirmed":"true"},
        headers={"Idempotency-Key":key or uuid4().hex})

def attach(client, at, asset_id, purpose="cover", **extra):
    return client.post(BASE+"/asset-placements", json={"assetId":asset_id,"scope":at,"purpose":purpose,**extra},
        headers={"Idempotency-Key":uuid4().hex})

def get_variant(client, asset, name="cover320", at=None):
    return client.get(BASE+f"/assets/{asset['assetId']}/variants/{name}", params={"scope":json.dumps(at or asset["scope"])})

def stable(db):
    return {"texts":[(b.id,b.text,b.content_hash) for b in db.scalars(select(ContentBlock).order_by(ContentBlock.id))],
        "revision":[(r.id,deepcopy(r.manifest),r.content_hash) for r in db.scalars(select(Revision).order_by(Revision.id))],
        "heads":[(b.id,b.head_revision_id) for b in db.scalars(select(Branch).order_by(Branch.id))],
        "progress":[(p.id,deepcopy(p.anchor),deepcopy(p.max_observed_anchor),p.version) for p in db.scalars(select(Progress).order_by(Progress.id))],
        "usage":db.scalar(select(func.count()).select_from(UsageRecord)),
        "tasks":db.scalar(select(func.count()).select_from(Task)),
        "profiles":[(p.id,p.calls_reserved,p.input_reserved,p.output_reserved) for p in db.scalars(select(ModelProfile))],
        "world":[(l.id,l.head_checkpoint_id,deepcopy(l.reading_position),l.version) for l in db.scalars(select(WorldLine).order_by(WorldLine.id))]}

def world_fixture(client, reader, key="one"):
    made=client.post("/api/world/v1/worlds",json={"templateId":"original-mystery-v1"},headers={"Idempotency-Key":"asset-world-"+key})
    assert made.status_code in (200,202),made.text
    with SessionLocal() as db:
        world=db.get(World,made.json()["worldId"])
        actor=next(e["id"] for e in world.definition["entities"] if e["type"]=="character")
        definition,state=initial_state(world.definition,actor)
        line=WorldLine(id=uid(),owner_id=world.owner_id,world_id=world.id,name="图片范围测试",head_checkpoint_id=uid())
        cp=WorldCheckpoint(id=line.head_checkpoint_id,owner_id=world.owner_id,world_id=world.id,line_id=line.id,
            sequence=1,title="公开现场",text="街灯照着门。\n\n纸信留在桌上。",action="",state=state,definition=definition,
            options=[],changes=[],summary="",memory={},kind="opening",
            content_hash=checkpoint_hash(state,definition,"街灯照着门。\n\n纸信留在桌上。",None))
        db.add(line);db.flush();db.add(cp);world.active_line_id=line.id;db.commit()
        return {"kind":"world","worldId":world.id,"lineId":line.id,"checkpointId":cp.id},actor

def test_upload_decodes_actual_variants_strips_metadata_and_never_changes_text_progress(client,reader,demo):
    at=scope(demo)
    with SessionLocal() as db: before=stable(db)
    response=upload(client,at,data=png(metadata=True),key="upload-receipt")
    assert response.status_code==201,response.text
    asset=response.json()
    assert set(asset["variants"])=={"original","cover320","cover640"}
    for name,dimensions in {"original":(120,180),"cover320":(320,480),"cover640":(640,960)}.items():
        image=get_variant(client,asset,name)
        assert image.status_code==200,image.text
        assert digest(image.content)==asset["variants"][name]["sha256"]
        assert int(image.headers["content-length"])==len(image.content)
        with Image.open(io.BytesIO(image.content)) as decoded:
            decoded.load()
            assert decoded.size==dimensions and not decoded.getexif()
            assert not any(k in decoded.info for k in ("GPS","Comment","exif","icc_profile"))
        assert b"PRIVATE-" not in image.content
    again=upload(client,at,data=png(metadata=True),key="upload-receipt")
    assert again.status_code==201 and again.json()["assetId"]==asset["assetId"]
    receipt=client.get(BASE+"/assets/requests",params={"requestId":"upload-receipt"}).json()
    assert receipt["found"] and receipt["asset"]["assetId"]==asset["assetId"]
    assert client.get(BASE+"/assets/requests",params={"requestId":"missing"}).json()=={"found":False}
    with SessionLocal() as db:
        assert stable(db)==before and db.scalar(select(func.count()).select_from(ImageAsset))==1

@pytest.mark.parametrize("purpose,size,rect,expected",[
    ("avatar",(90,90),{"x":5,"y":5,"width":80,"height":80},{"original":(80,80),"avatar128":(128,128),"avatar256":(256,256),"avatar512":(512,512)}),
    ("scene",(1800,900),{"x":0,"y":0,"width":1800,"height":900},{"original":(1800,900),"reading":(1600,800),"thumbnail":(320,160)}),
])
def test_all_variant_bytes_and_oriented_crop(purpose,size,rect,expected):
    for value in prepare(png(size,True),"image/png",purpose,rect):
        assert (value.width,value.height)==expected[value.name] and digest(value.data)==value.sha256
        with Image.open(io.BytesIO(value.data)) as image:
            image.load()
            assert image.size==expected[value.name] and not image.getexif()
    original=Image.new("RGB",(120,80),"red")
    exif=Image.Exif();exif[274]=6;exif[270]="private-original-exif";exif[305]="private-camera"
    out=io.BytesIO();original.save(out,"JPEG",exif=exif)
    values=prepare(out.getvalue(),"image/jpeg","avatar",{"x":0,"y":0,"width":80,"height":80})
    assert values[0].width==80 and not Image.open(io.BytesIO(values[0].data)).getexif()

@pytest.mark.parametrize("failure",["mime","svg","html","corrupt","truncated","animated","edge","pixels","bytes","crop","ratio"])
def test_unsafe_input_never_creates_asset_or_receipt(client,reader,demo,failure):
    data=png();mime="image/png";rect={"x":0,"y":0,"width":120,"height":180};purpose="cover"
    if failure=="mime":mime="image/jpeg"
    if failure=="svg":data=b'<svg onload="fetch(secret)"/>';mime="image/svg+xml"
    if failure=="html":data=b"<html><script>evil()</script></html>";mime="image/png"
    if failure=="corrupt":data=b"not-an-image"
    if failure=="truncated":data=data[:30]
    if failure=="animated":
        out=io.BytesIO()
        Image.new("RGB",(120,180),"red").save(out,"PNG",save_all=True,append_images=[Image.new("RGB",(120,180),"blue")],duration=100,loop=0)
        data=out.getvalue()
    if failure in ("edge","pixels"):
        width,height=(8193,1) if failure=="edge" else (6000,6000)
        header=struct.pack(">IIBBBBB",width,height,8,2,0,0,0)
        data=data[:16]+header+struct.pack(">I",zlib.crc32(b"IHDR"+header))+data[33:]
    if failure=="bytes":data=b"x"*(MAX_UPLOAD+1)
    if failure=="crop":rect["x"]=1
    if failure=="ratio":rect["height"]=120
    result=upload(client,scope(demo),data=data,mime=mime,rect=rect,purpose=purpose,key="bad-image")
    assert result.status_code in (413,422),result.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ImageAsset))==0
        from app.models import Idempotency
        assert not db.scalar(select(Idempotency).where(Idempotency.scope=="asset-upload"))


def test_three_authored_bundles_have_actual_eighteen_licensed_images_and_explicit_install(client,reader):
    from app.assets.bundles import manifest,example_bytes
    from app.assets.portable import validate_records
    listing=client.get(BASE+"/assets/examples")
    assert listing.status_code==200 and len(listing.json()["items"])==3
    assert listing.json()["license"]=="CC0-1.0"
    for row in manifest()["bundles"]:
        assert len(row["images"])==6
        assert [image["kind"] for image in row["images"]].count("avatar")==3
        for image in row["images"]:
            data=example_bytes(row,image["file"])
            with Image.open(io.BytesIO(data)) as actual:
                actual.load();assert actual.size==(image["width"],image["height"]) and not actual.getexif()
        installed=client.post(BASE+"/assets/examples/"+row["id"]+"/install",json={},headers={"Idempotency-Key":row["id"]})
        assert installed.status_code==201,installed.text
        assert installed.json()["imageCount"]==6
        assert client.get(BASE+"/assets/examples/"+row["id"]+"/requests",params={"requestId":row["id"]}).json()=={"found":True,"receipt":installed.json()}
        again=client.post(BASE+"/assets/examples/"+row["id"]+"/install",json={},headers={"Idempotency-Key":row["id"]})
        assert again.json()==installed.json()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Book))==3
        assert db.scalar(select(func.count()).select_from(ImageAsset))==18
        assert db.scalar(select(func.count()).select_from(AssetPlacement))==18
        assert db.scalar(select(func.count()).select_from(CharacterIdentity))==9
        assert db.scalar(select(func.count()).select_from(Progress))==0
        assert db.scalar(select(func.count()).select_from(UsageRecord))==0
        assert db.scalar(select(func.count()).select_from(Task))==0
        assert db.scalar(select(func.count()).select_from(ModelProfile))==0
        assert validate_records(*records(db,reader["user"]["id"]))["assets"]==18

def test_cross_owner_branch_checkpoint_and_private_avatar_future_are_rejected(client,reader,demo):
    at=scope(demo)
    asset=upload(client,at).json()
    original_auth=client.headers["Authorization"]
    assert client.post(BASE+"/auth/register",json={"username":"assets_other","password":"assets-other-pass"}).status_code==201
    other=client.post(BASE+"/auth/login",json={"username":"assets_other","password":"assets-other-pass","clientType":"web","deviceName":"other"}).json()
    client.headers["Authorization"]="Bearer "+other["accessToken"]
    assert get_variant(client,asset).status_code==404
    assert attach(client,at,asset["assetId"]).status_code==404
    client.headers["Authorization"]=original_auth
    chapter=read_chapter(client,demo);early=chapter["blocks"][0]["startAnchor"];late=chapter["blocks"][-1]["endAnchor"]
    observe(client,reader,late)
    with SessionLocal() as db:
        identity=CharacterIdentity(id=uid(),owner_id=reader["user"]["id"],origin_key="assets-char",source_ref={},ambiguous=False)
        db.add(identity);db.flush()
        db.add(CharacterScopeRevision(id=uid(),owner_id=identity.owner_id,identity_id=identity.id,
            scope_key="assets-char-at-late",scope_kind="reader",scope_id=at["branchId"],context={"anchor":late},visible_data={"name":"阿岑"},version=1))
        db.commit();identity_id=identity.id
    private={**at,"identityId":identity_id,"anchor":early}
    denied=upload(client,private,"avatar",png((80,80)),rect={"x":0,"y":0,"width":80,"height":80})
    assert denied.status_code==404,denied.text
    visible={**private,"anchor":late}
    avatar=upload(client,visible,"avatar",png((80,80)),rect={"x":0,"y":0,"width":80,"height":80})
    assert avatar.status_code==201,avatar.text
    assert get_variant(client,avatar.json(),"avatar128",private).status_code==404
    assert get_variant(client,avatar.json(),"avatar128",{**at,"anchor":late}).status_code==200
    assert get_variant(client,avatar.json(),"avatar128",{**at,"anchor":early}).status_code==404
    a,_=world_fixture(client,reader,"a");b,_=world_fixture(client,reader,"b")
    world_image=upload(client,a,"scene",png((100,80)),rect={"x":0,"y":0,"width":100,"height":80})
    assert world_image.status_code==201,world_image.text
    assert get_variant(client,world_image.json(),"reading",b).status_code==404
    mismatched={**a,"checkpointId":b["checkpointId"]}
    assert get_variant(client,world_image.json(),"reading",mismatched).status_code==404

def test_refcounts_replace_remove_cas_and_idempotency_do_not_resurrect(client,reader,demo):
    at=scope(demo);a=upload(client,at,key="same-create").json();b=upload(client,at,key="another-create").json()
    first=attach(client,at,a["assetId"]).json()
    second=attach(client,at,a["assetId"],order=1).json()
    assert second["asset"]["referenceCount"]==2
    assert client.delete(BASE+"/assets/"+a["assetId"],headers={"Idempotency-Key":"used"}).status_code==409
    conflict=attach(client,at,b["assetId"],placementId=first["id"],expectedVersion=0)
    assert conflict.status_code==422
    response=client.request("DELETE",BASE+"/asset-placements/"+first["id"],json={"expectedVersion":1},headers={"Idempotency-Key":"remove-one"})
    assert response.status_code==200,response.text
    assert get_variant(client,a).status_code==200
    changed=attach(client,at,b["assetId"],placementId=second["id"],expectedVersion=1)
    assert changed.status_code==200 and changed.json()["version"]==2,changed.text
    stale=attach(client,at,b["assetId"],placementId=second["id"],expectedVersion=1)
    assert stale.status_code==409 and stale.json()["code"]=="ASSET_CONFLICT",stale.text
    assert get_variant(client,a).status_code==404
    replay=upload(client,at,key="same-create")
    assert replay.status_code==404 and replay.json()["code"]=="ASSET_DELETED"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ImageAsset))==1
        assert db.scalar(select(func.count()).select_from(AssetPlacement))==1

def test_replacement_and_remove_race_cannot_revive_old_references(client,reader,demo):
    at=scope(demo);a=upload(client,at).json();b=upload(client,at).json();placed=attach(client,at,a["assetId"]).json()
    def replace():
        return attach(client,at,b["assetId"],placementId=placed["id"],expectedVersion=1)
    def remove():
        return client.request("DELETE",BASE+"/asset-placements/"+placed["id"],json={"expectedVersion":1},headers={"Idempotency-Key":"race-remove"})
    with ThreadPoolExecutor(max_workers=2) as threads:
        x,y=list(threads.map(lambda fn:fn(),[replace,remove]))
    assert sorted([x.status_code,y.status_code]) in ([200,409],[200,404]),(x.text,y.text)
    with SessionLocal() as db:
        for row in db.scalars(select(AssetPlacement)):
            assert db.get(ImageAsset,row.asset_id) and row.version in (1,2)
        assert not db.get(ImageAsset,a["assetId"])

def test_placement_and_first_progress_insert_do_not_deadlock(client,reader,demo):
    at=scope(demo);asset=upload(client,at).json()
    anchor=read_chapter(client,demo)["blocks"][0]["endAnchor"]
    lock_id=int(digest("book-lifecycle:"+reader["user"]["id"]+":"+demo["bookId"])[:15],16)
    book_held,placement_waiting,continue_progress=Event(),Event(),Event()
    connection_marks=[]

    def is_book_lock(statement,parameters):
        values=parameters.values() if isinstance(parameters,dict) else parameters
        return "pg_advisory_xact_lock" in statement and lock_id in values

    def before(connection,cursor,statement,parameters,context,many):
        if is_book_lock(statement,parameters) and connection.info.get("asset_fence_race"):
            placement_waiting.set()

    def after(connection,cursor,statement,parameters,context,many):
        normalized=" ".join(statement.lower().split())
        if "from users" in normalized and ("for update" in normalized or "for share" in normalized):
            connection.info["asset_fence_race"]=True;connection_marks.append(connection.info)
        elif is_book_lock(statement,parameters) and not connection.info.get("asset_fence_race") and not book_held.is_set():
            book_held.set()
            assert continue_progress.wait(5),"Progress interleaving was not released"

    event.listen(engine,"before_cursor_execute",before)
    event.listen(engine,"after_cursor_execute",after)
    try:
        with ThreadPoolExecutor(max_workers=2) as threads:
            progress=threads.submit(client.put,BASE+"/books/"+demo["bookId"]+"/progress",json={
                "branchId":demo["branchId"],"revisionId":demo["revisionId"],"anchor":anchor,
                "expectedProgressVersion":0,"deviceId":reader["sessionId"],"clientSeq":1})
            assert book_held.wait(5)
            placed=threads.submit(attach,client,at,asset["assetId"])
            assert placement_waiting.wait(5),"Placement did not reach the actual held book lock"
            continue_progress.set()
            saved,attached=progress.result(timeout=10),placed.result(timeout=10)
    finally:
        continue_progress.set()
        event.remove(engine,"before_cursor_execute",before)
        event.remove(engine,"after_cursor_execute",after)
        for marks in connection_marks:marks.pop("asset_fence_race",None)
    assert saved.status_code==200,saved.text
    assert attached.status_code==200,attached.text
    assert saved.json()["anchor"]==anchor
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AssetPlacement))==1
        assert db.scalar(select(func.count()).select_from(Progress))==1

def test_asset_account_fence_blocks_normal_deletion_until_commit(client,reader,demo):
    from app.assets.service import authorize
    at=scope(demo);asset=upload(client,at).json()
    with ThreadPoolExecutor(max_workers=1) as threads,SessionLocal() as db:
        authorize(db,reader["user"]["id"],at)
        deletion=threads.submit(client.post,BASE+"/auth/delete-account",json={
            "currentPassword":"reader-password-123","confirmation":"删除我的账号"})
        deadline=monotonic()+5;blocked=False
        while monotonic()<deadline:
            with SessionLocal() as observation:
                blocked=observation.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND (query LIKE 'UPDATE users%' OR query LIKE 'SELECT users%FOR UPDATE%'))"))
            if blocked:break
            sleep(.01)
        assert blocked,"Normal account deletion must wait for the active asset transaction"
        assert not deletion.done(),"Account deletion cannot finish while the asset transaction holds the account fence"
        db.commit()
        receipt=deletion.result(timeout=10)
    assert receipt.status_code==202,receipt.text
    assert get_variant(client,asset).status_code==401
    from app.worker import process
    process(receipt.json()["taskId"])
    with SessionLocal() as db:
        from app.models import User
        assert db.get(User,reader["user"]["id"]) is None

def test_waiting_asset_fence_rereads_normally_revoked_account(client,reader,demo):
    at=scope(demo);asset=upload(client,at).json()
    changed,fence_waiting,release_delete=Event(),Event(),Event()
    def before(connection,cursor,statement,parameters,context,many):
        normalized=" ".join(statement.lower().split())
        if "from users" in normalized and ("for update" in normalized or "for share" in normalized):
            fence_waiting.set()
    def after(connection,cursor,statement,parameters,context,many):
        if statement.lower().startswith("update users") and not changed.is_set():
            changed.set()
            assert release_delete.wait(5),"Normal account deletion was not released"
    event.listen(engine,"before_cursor_execute",before)
    event.listen(engine,"after_cursor_execute",after)
    try:
        with ThreadPoolExecutor(max_workers=2) as threads:
            deletion=threads.submit(client.post,BASE+"/auth/delete-account",json={
                "currentPassword":"reader-password-123","confirmation":"删除我的账号"})
            assert changed.wait(5)
            placement=threads.submit(attach,client,at,asset["assetId"])
            assert fence_waiting.wait(5)
            release_delete.set()
            removed,placed=deletion.result(timeout=10),placement.result(timeout=10)
    finally:
        release_delete.set()
        event.remove(engine,"before_cursor_execute",before)
        event.remove(engine,"after_cursor_execute",after)
    assert removed.status_code==202,removed.text
    assert placed.status_code==401,placed.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AssetPlacement))==0
    from app.worker import process
    process(removed.json()["taskId"])

def test_scope_lifecycle_detach_retains_only_precise_publication_and_other_world(client,reader,demo):
    at=scope(demo);a=upload(client,at).json()
    source=attach(client,at,a["assetId"]).json()
    world_at,_=world_fixture(client,reader,"retained")
    kept=attach(client,world_at,a["assetId"]).json()
    with SessionLocal() as db:
        before=stable(db)
        purge_scope(db,reader["user"]["id"],"book",at["bookId"])
        assert db.get(ImageAsset,a["assetId"])
        assert db.get(ImageAsset,a["assetId"]).origin_scope==world_at
        from app.assets.portable import validate_records
        assert validate_records(*records(db,reader["user"]["id"]))["assets"]==1
        db.commit()
        assert stable(db)==before
    assert get_variant(client,a,at=world_at).status_code==200
    with SessionLocal() as db:
        purge_scope(db,reader["user"]["id"],"world",world_at["worldId"]);db.commit()
        assert not db.get(ImageAsset,a["assetId"])
    b=upload(client,at).json();row=attach(client,at,b["assetId"]).json()
    # Canonical retained branch mapping is supplied by the deletion coordinator.
    carrier_id=uid()
    with SessionLocal() as db:
        detach_book(db,reader["user"]["id"],at["bookId"],[{"bookId":carrier_id,"branchIds":[at["branchId"]]}])
        moved=db.get(AssetPlacement,row["id"])
        assert moved.scope["bookId"]==carrier_id and moved.version==2
        assert db.get(ImageAsset,b["assetId"]).origin_scope["bookId"]==carrier_id
        db.rollback()
        assert db.get(AssetPlacement,row["id"]).scope["bookId"]==at["bookId"]
        detach_book(db,reader["user"]["id"],at["bookId"],[])
        db.commit();assert not db.get(ImageAsset,b["assetId"])

def test_flush_only_rollback_account_purge_and_archive_readonly(client,reader,demo):
    at=scope(demo)
    with SessionLocal() as db:
        result=create_asset(db,reader["user"]["id"],png(),"image/png","cover",{"x":0,"y":0,"width":120,"height":180},at,"自编图")
        place_asset(db,reader["user"]["id"],PlacementInput(assetId=result["assetId"],scope=at,purpose="cover"))
        db.rollback()
        assert not db.get(ImageAsset,result["assetId"])
    asset=upload(client,at).json();attach(client,at,asset["assetId"])
    with SessionLocal() as db:
        db.get(Branch,at["branchId"]).status="archived";db.commit()
    assert get_variant(client,asset).status_code==200
    assert attach(client,at,asset["assetId"]).status_code==409
    with SessionLocal() as db:
        purge_owner(db,reader["user"]["id"]);db.rollback()
        assert db.get(ImageAsset,asset["assetId"])
        purge_owner(db,reader["user"]["id"]);db.commit()
        for model in (ImageAsset,AssetVariant,AssetPlacement):
            assert db.scalar(select(func.count()).select_from(model))==0


def records(db,owner):
    assets=[{column.name:getattr(row,column.name) for column in ImageAsset.__table__.columns}
        for row in db.scalars(select(ImageAsset).where(ImageAsset.owner_id==owner))]
    variants=[{column.name:getattr(row,column.name) for column in AssetVariant.__table__.columns}
        for row in db.scalars(select(AssetVariant).join(ImageAsset).where(ImageAsset.owner_id==owner))]
    refs=[{column.name:getattr(row,column.name) for column in AssetPlacement.__table__.columns}
        for row in db.scalars(select(AssetPlacement).where(AssetPlacement.owner_id==owner))]
    return assets,variants,refs

def test_portable_actual_bytes_complete_variants_and_scope_references(client,reader,demo):
    from app.assets.portable import validate_records
    at=scope(demo);image=upload(client,at).json();attach(client,at,image["assetId"])
    with SessionLocal() as db:
        rows=records(db,reader["user"]["id"])
        count=validate_records(*rows)
        assert (count["assets"],count["variants"],count["placements"])==(1,3,1) and count["bytes"]>0
    for failure in ("hash","missing_variant","wrong_owner","bad_scope","metadata"):
        a,v,p=deepcopy(rows)
        if failure=="hash":v[0]["data"]+=b"altered"
        if failure=="missing_variant":v.pop()
        if failure=="wrong_owner":p[0]["owner_id"]=uid()
        if failure=="bad_scope":p[0]["scope"]["privateGoal"]="not-allowed"
        if failure=="metadata":
            dirty=png(metadata=True);original=next(x for x in v if x["name"]=="original")
            original["data"]=dirty;original["sha256"]=digest(dirty);a[0]["sha256"]=digest(dirty)
        with pytest.raises(Problem) as raised:validate_records(a,v,p)
        assert raised.value.code=="ASSET_ARCHIVE_INVALID"

def test_request_receipts_are_pure_owner_scoped_and_do_not_replay_mutations(client,reader,demo):
    at=scope(demo);image=upload(client,at).json()
    body={"assetId":image["assetId"],"scope":at,"purpose":"cover"}
    response=client.post(BASE+"/asset-placements",json=body,headers={"Idempotency-Key":"placement-receipt"})
    assert response.status_code==200,response.text
    row=response.json()
    assert client.get(BASE+"/asset-placements/requests",params={"requestId":"placement-receipt"}).json()=={"found":True,"receipt":row}
    removed=client.request("DELETE",BASE+"/asset-placements/"+row["id"],json={"expectedVersion":1},headers={"Idempotency-Key":"remove-receipt"})
    assert removed.status_code==200,removed.text
    assert client.get(BASE+"/asset-placements/"+row["id"]+"/deletion",params={"requestId":"remove-receipt"}).json()=={"found":True,"receipt":removed.json()}
    assert client.get(BASE+"/asset-placements/requests",params={"requestId":"placement-receipt"}).json()=={"found":True,"deleted":True,"placementId":row["id"]}
    assert client.get(BASE+"/asset-placements/requests",params={"requestId":"unknown"}).json()=={"found":False}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AssetPlacement))==0
        assert db.scalar(select(func.count()).select_from(ImageAsset))==0


def test_detach_origin_rebinds_to_retained_world_and_paragraph_slot_matches_rendered_prose(client,reader,demo):
    at=scope(demo);image=upload(client,at).json();attach(client,at,image["assetId"])
    world_at,_=world_fixture(client,reader,"image-retained-world")
    kept=attach(client,world_at,image["assetId"]).json()
    with SessionLocal() as db:
        from app.world.models import WorldCheckpoint
        cp=db.get(WorldCheckpoint,world_at["checkpointId"]);cp.text="第一段。\n \n第二段。"
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,cp.parent_checkpoint_id)
        detach_book(db,reader["user"]["id"],at["bookId"],[])
        assert db.get(ImageAsset,image["assetId"]).origin_scope==world_at
        from app.assets.portable import validate_records
        assert validate_records(*records(db,reader["user"]["id"]))["assets"]==1
        db.commit()
    assert get_variant(client,image,at=world_at).status_code==200
    illustration=upload(client,world_at,"illustration",png((100,80)),rect={"x":0,"y":0,"width":100,"height":80}).json()
    assert attach(client,world_at,illustration["assetId"],purpose="illustration",contentId=world_at["checkpointId"],paragraphId="1").status_code==200
    assert attach(client,world_at,illustration["assetId"],purpose="illustration",contentId=world_at["checkpointId"],paragraphId="2").status_code==422


def test_normalized_single_image_byte_quota_rejects_before_asset_persistence(client,reader,demo,monkeypatch):
    from app.assets import imaging
    data=png((120,180));assert len(data)>100
    monkeypatch.setattr(imaging,"MAX_UPLOAD",100)
    response=upload(client,scope(demo),data=data)
    assert response.status_code==413 and response.json()["code"]=="IMAGE_TOO_LARGE",response.text
    with SessionLocal() as db:
        for model in (ImageAsset,AssetVariant,AssetPlacement):
            assert db.scalar(select(func.count()).select_from(model))==0
        assert db.scalar(select(func.count()).select_from(UsageRecord))==0
