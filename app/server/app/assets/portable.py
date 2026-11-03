"""Finite image-byte/record validation for the portable domain allowlist.

Call before remapping/inserting, then authorize the remapped placement closure.
No DB/session, network, files, provider or automatic restore is used here.
"""
import io
import re
from PIL import Image
from pydantic import ValidationError
from ..domain import digest
from ..errors import Problem
from .imaging import decode, MAX_EDGE, MAX_PIXELS, MAX_STORED, MAX_UPLOAD
from .schemas import AssetScope, Crop

ID = re.compile(r"^[a-f0-9]{32}$")
HASH = re.compile(r"^[a-f0-9]{64}$")
ASSET_FIELDS = {"id","owner_id","created_at","purpose","source_kind","license_note","sha256","mime","width","height","origin_scope","crop","version"}
VARIANT_FIELDS = {"asset_id","name","width","height","sha256","mime","data"}
PLACEMENT_FIELDS = {"id","owner_id","created_at","asset_id","scope","purpose","content_id","paragraph_id","side","order","alt","version"}

def invalid(message="备份图片字节、引用或裁切信息无效"):
    raise Problem("ASSET_ARCHIVE_INVALID", message, 422)

def integer(value, minimum=1, maximum=MAX_EDGE):
    if type(value) is not int or not minimum <= value <= maximum:
        invalid()
    return value

def identifier(value):
    if not isinstance(value,str) or not ID.fullmatch(value):
        invalid()
    return value

def fields(row, names):
    if not isinstance(row,dict) or set(row) != names:
        invalid("备份图片记录字段不符合公开合同")

def exact_scope(value, purpose):
    try:
        scope = AssetScope.model_validate(value).public()
        if scope != value:
            invalid()
        if purpose == "avatar" and not (scope.get("identityId") if scope["kind"]=="book" else scope.get("entityId")):
            invalid()
        return scope
    except (ValidationError, TypeError, ValueError):
        invalid()

def validate_records(assets:list[dict], variants:list[dict], placements:list[dict]):
    """Validate complete, decoded bytes + exact record/FK shapes, return counts.

    A portable writer must first select only this owner's authorized reference
    closure. Import code still performs typed ID remapping and ordinary
    current-owner scope authorization in its existing transaction.
    """
    if not all(isinstance(rows,list) for rows in (assets,variants,placements)):
        invalid()
    if len(assets)>2000 or len(variants)>8000 or len(placements)>10000:
        invalid("备份图片资源数量超出限制")
    by_id={};owners=set();by_variant={};total=0
    for asset in assets:
        fields(asset,ASSET_FIELDS)
        asset_id=identifier(asset["id"]);owners.add(identifier(asset["owner_id"]))
        if asset_id in by_id or asset["purpose"] not in ("cover","avatar","scene","illustration"):
            invalid()
        if not isinstance(asset["source_kind"],str) or not 1<=len(asset["source_kind"])<=40:
            invalid()
        if not isinstance(asset["license_note"],str) or not asset["license_note"].strip() or len(asset["license_note"])>1000:
            invalid("备份图片缺少来源与许可")
        if not isinstance(asset["sha256"],str) or not HASH.fullmatch(asset["sha256"]) or asset["mime"]!="image/png":
            invalid()
        width=integer(asset["width"]);height=integer(asset["height"])
        if width*height>MAX_PIXELS:invalid()
        integer(asset["version"],1,2**31-1)
        try:crop=Crop.model_validate(asset["crop"])
        except (ValidationError,TypeError,ValueError):invalid()
        if (crop.width,crop.height)!=(width,height):invalid()
        if asset["purpose"]=="avatar" and width!=height:invalid()
        if asset["purpose"]=="cover" and abs(width/height-2/3)>.01:invalid()
        exact_scope(asset["origin_scope"],asset["purpose"])
        by_id[asset_id]=asset
    if len(owners)>1:invalid("备份图片不能混入其他账号")
    for variant in variants:
        fields(variant,VARIANT_FIELDS)
        asset=by_id.get(variant["asset_id"]);name=variant["name"]
        if not asset or not isinstance(name,str) or (asset["id"],name) in by_variant:
            invalid()
        data=variant["data"]
        if not isinstance(data,bytes) or not data or len(data)>MAX_UPLOAD or digest(data)!=variant["sha256"]:
            invalid("备份图片哈希不一致")
        total+=len(data)
        if total>512*1024*1024:invalid("备份图片累计体积超出限制")
        integer(variant["width"]);integer(variant["height"])
        try:
            # Inspect the original container metadata before decode strips it.
            with Image.open(io.BytesIO(data)) as probe:
                if probe.getexif() or any(key not in {"transparency","background","loop"} for key in probe.info):
                    invalid("备份图片包含未清理的附加元数据")
            decoded=decode(data,variant["mime"],max_bytes=MAX_UPLOAD)
        except Problem as exc:
            if exc.code=="ASSET_ARCHIVE_INVALID":raise
            invalid("备份图片无法安全完整解码")
        except (OSError,ValueError,TypeError):invalid()
        if decoded.size!=(variant["width"],variant["height"]):invalid()
        width,height=asset["width"],asset["height"]
        purpose=asset["purpose"]
        if name=="original":
            expected=(width,height)
            if variant["sha256"]!=asset["sha256"] or variant["mime"]!=asset["mime"]:invalid()
        elif purpose=="avatar" and name in {"avatar128","avatar256","avatar512"}:
            edge=int(name[6:]);expected=(edge,edge)
        elif purpose=="cover" and name in {"cover320","cover640"}:
            edge=int(name[5:]);expected=(edge,round(edge*height/width))
        elif purpose in {"scene","illustration"} and name in {"reading","thumbnail"}:
            limit=1600 if name=="reading" else 320
            # Pillow adjusts rounding to preserve aspect ratio. A 1-bit image
            # obtains the exact encode geometry with bounded temporary memory.
            geometry=Image.new("1",(width,height))
            geometry.thumbnail((limit,limit),Image.Resampling.NEAREST)
            expected=geometry.size
        else:invalid()
        if (variant["width"],variant["height"])!=expected:invalid()
        if name!="original" and variant["mime"]!="image/webp":invalid()
        by_variant[(asset["id"],name)]=variant
    for asset in assets:
        required={"original"}|({"avatar128","avatar256","avatar512"} if asset["purpose"]=="avatar" else
            {"cover320","cover640"} if asset["purpose"]=="cover" else {"reading","thumbnail"})
        if {name for asset_id,name in by_variant if asset_id==asset["id"]}!=required:
            invalid("备份图片缺少完整尺寸")
    seen=set()
    for row in placements:
        fields(row,PLACEMENT_FIELDS)
        identifier(row["id"]);identifier(row["owner_id"])
        if row["id"] in seen:invalid()
        seen.add(row["id"])
        asset=by_id.get(row["asset_id"])
        if not asset or asset["owner_id"]!=row["owner_id"] or row["purpose"]!=asset["purpose"]:
            invalid()
        scope=exact_scope(row["scope"],row["purpose"])
        if row["content_id"] is not None:identifier(row["content_id"])
        if row["paragraph_id"] is not None and (not isinstance(row["paragraph_id"],str) or not 1<=len(row["paragraph_id"])<=80):
            invalid()
        if scope["kind"]=="book" and row["paragraph_id"] is not None and row["paragraph_id"]!=row["content_id"]:invalid()
        if scope["kind"]=="world":
            if row["content_id"] is not None and row["content_id"]!=scope["checkpointId"]:invalid()
            p=row["paragraph_id"]
            if p is not None and (not p.isdecimal() or str(int(p))!=p):invalid()
        if row["purpose"]=="illustration" and not row["content_id"]:invalid()
        if row["side"] not in ("before","after") or not isinstance(row["alt"],str) or len(row["alt"])>300:invalid()
        integer(row["order"],0,1000);integer(row["version"],1,2**31-1)
    return {"assets":len(assets),"variants":len(variants),"placements":len(placements),"bytes":total}
