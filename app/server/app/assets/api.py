import json
from fastapi import APIRouter, Depends, Request, File, Form, UploadFile, Query
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select, text
from ..db import get_db
from ..models import Idempotency
from ..security import current_user
from ..domain import idempotent, digest
from ..errors import Problem
from .models import ImageAsset, AssetPlacement
from .schemas import AssetScope, Crop, PlacementInput, PlacementDelete
from .imaging import MAX_UPLOAD
from .service import create_asset, asset_owned, asset_result, variant_bytes, authorize, placements, place_asset, remove_placement, collect_unreferenced

router = APIRouter()

def parse(model, value):
    try:
        return model.model_validate_json(value)
    except (ValidationError, ValueError, TypeError):
        raise Problem("IMAGE_FIELDS_INVALID", "图片用途、裁切或故事范围无效")

@router.get("/assets/capabilities")
def capabilities(user=Depends(current_user), db=Depends(get_db)):
    names = ("image_assets", "image_asset_variants", "image_asset_placements")
    ready = all(db.scalar(text("SELECT to_regclass(:name)"), {"name": name}) for name in names)
    return {"protocol": "reader-assets-v1", "ready": ready, "maxUploadBytes": MAX_UPLOAD,
        "maxEdge": 8192, "maxPixels": 32*1024*1024, "mimeTypes": ["image/jpeg","image/png","image/webp"],
        "avatarSizes": [128,256,512], "coverWidths": [320,640], "readingMaxEdge": 1600}

@router.get("/assets/examples")
def authored_examples(user=Depends(current_user)):
    from .bundles import list_examples
    return list_examples()

@router.get("/assets/examples/{example_id}/requests")
def example_request_status(example_id:str,requestId:str=Query(min_length=1,max_length=160),
    user=Depends(current_user),db=Depends(get_db)):
    row=db.scalar(select(Idempotency).where(Idempotency.owner_id==user.id,
        Idempotency.scope=="asset-example:"+example_id,Idempotency.key==requestId))
    return {"found":True,"receipt":row.result} if row else {"found":False}

@router.post("/assets/examples/{example_id}/install",status_code=201)
def install_example(example_id:str,request:Request,user=Depends(current_user),db=Depends(get_db)):
    from .bundles import install
    saved=idempotent(db,user.id,"asset-example:"+example_id,request.headers.get("idempotency-key"),{},lambda:install(db,user.id,example_id))
    from ..models import Book
    from ..domain import owned
    owned(db,Book,saved["bookId"],user.id)
    return saved

@router.post("/assets", status_code=201)
async def upload(request: Request, file: UploadFile=File(...), purpose: str=Form(...),
    scope: str=Form(...), crop: str=Form(...), licenseNote: str=Form(...), rightsConfirmed: bool=Form(...),
    user=Depends(current_user), db=Depends(get_db)):
    if not rightsConfirmed or not licenseNote.strip() or len(licenseNote) > 1000:
        raise Problem("IMAGE_LICENSE_REQUIRED", "请确认有权使用图片并保留来源说明")
    data = await file.read(MAX_UPLOAD+1)
    value, rect = parse(AssetScope,scope), parse(Crop,crop)
    # Only hashes/metadata form the idempotency identity, never original EXIF.
    payload = {"sha256": digest(data), "mime": file.content_type, "purpose": purpose,
        "scope": value.public(), "crop": rect.model_dump(), "licenseNote": licenseNote.strip()}
    saved = idempotent(db,user.id,"asset-upload",request.headers.get("idempotency-key"),payload,
        lambda:create_asset(db,user.id,data,file.content_type,purpose,rect.model_dump(),value,licenseNote.strip()))
    asset = db.scalar(select(ImageAsset).where(ImageAsset.id==saved["assetId"],ImageAsset.owner_id==user.id))
    if not asset:
        raise Problem("ASSET_DELETED", "原上传的图片已移除，不能重放创建", 404)
    return asset_result(db,asset,authorize(db,user.id,value))

@router.get("/assets/requests")
def request_status(requestId: str=Query(min_length=1,max_length=160), user=Depends(current_user), db=Depends(get_db)):
    row = db.scalar(select(Idempotency).where(Idempotency.owner_id==user.id,Idempotency.scope=="asset-upload",Idempotency.key==requestId))
    if not row:
        return {"found": False}
    asset = db.scalar(select(ImageAsset).where(ImageAsset.owner_id==user.id,ImageAsset.id==row.result.get("assetId")))
    if not asset:
        return {"found": True, "deleted": True, "assetId": row.result.get("assetId")}
    scope = authorize(db,user.id,asset.origin_scope)
    return {"found":True,"asset":asset_result(db,asset,scope)}

@router.get("/assets/{asset_id}/variants/{name}")
def read_variant(asset_id: str, name: str, scope: str, user=Depends(current_user), db=Depends(get_db)):
    variant = variant_bytes(db,user.id,asset_id,name,parse(AssetScope,scope))
    return Response(content=variant.data,media_type=variant.mime,headers={"ETag":'"'+variant.sha256+'"',
        "Content-Length":str(len(variant.data)),"X-Content-Type-Options":"nosniff"})

@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, request: Request, user=Depends(current_user), db=Depends(get_db)):
    def remove():
        probe = asset_owned(db,user.id,asset_id)
        authorize(db,user.id,probe.origin_scope,write=True)
        asset = asset_owned(db,user.id,asset_id,lock=True)
        from .models import AssetPlacement
        if db.scalar(select(AssetPlacement.id).where(AssetPlacement.asset_id==asset.id).limit(1)):
            raise Problem("ASSET_IN_USE","图片仍被故事引用，请先移除对应图片位置",409)
        collect_unreferenced(db,user.id,[asset.id])
        return {"deleted":True,"assetId":asset_id}
    return idempotent(db,user.id,"asset-delete:"+asset_id,request.headers.get("idempotency-key"),{},remove)

@router.get("/asset-placements")
def read_placements(scope: str, user=Depends(current_user), db=Depends(get_db)):
    return placements(db,user.id,parse(AssetScope,scope))

@router.get("/asset-placements/requests")
def placement_status(requestId: str=Query(min_length=1,max_length=160), user=Depends(current_user), db=Depends(get_db)):
    row=db.scalar(select(Idempotency).where(Idempotency.owner_id==user.id,
        Idempotency.scope=="asset-placement",Idempotency.key==requestId))
    if not row:return {"found":False}
    saved=row.result
    placement=db.scalar(select(AssetPlacement).where(AssetPlacement.id==saved.get("id"),AssetPlacement.owner_id==user.id))
    if not placement:return {"found":True,"deleted":True,"placementId":saved.get("id")}
    authorize(db,user.id,placement.scope)
    return {"found":True,"receipt":saved}

@router.get("/asset-placements/{placement_id}/deletion")
def placement_deletion_status(placement_id: str,requestId: str=Query(min_length=1,max_length=160),
    user=Depends(current_user),db=Depends(get_db)):
    row=db.scalar(select(Idempotency).where(Idempotency.owner_id==user.id,
        Idempotency.scope=="asset-placement-delete:"+placement_id,Idempotency.key==requestId))
    return {"found":True,"receipt":row.result} if row else {"found":False}

@router.post("/asset-placements")
def set_placement(data: PlacementInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    authorize(db,user.id,data.scope,write=True,purpose=data.purpose,content_id=data.contentId,paragraph_id=data.paragraphId)
    saved = idempotent(db,user.id,"asset-placement",request.headers.get("idempotency-key"),data.model_dump(),
        lambda:place_asset(db,user.id,data))
    from .models import AssetPlacement
    row = db.scalar(select(AssetPlacement).where(AssetPlacement.id==saved["id"],AssetPlacement.owner_id==user.id))
    if not row:
        raise Problem("ASSET_PLACEMENT_DELETED","这处图片已移除，不能重放创建",404)
    return saved

@router.delete("/asset-placements/{placement_id}")
def delete_placement(placement_id: str, data: PlacementDelete, request: Request,
    user=Depends(current_user),db=Depends(get_db)):
    return idempotent(db,user.id,"asset-placement-delete:"+placement_id,request.headers.get("idempotency-key"),
        data.model_dump(),lambda:remove_placement(db,user.id,placement_id,data.expectedVersion))
