"""Explicit install of our hand-authored CC0 stories/images; never a model."""
from pathlib import Path
import json
from sqlalchemy import select
from ..models import Book, Revision, ContentBlock, SourceDocument
from ..api_books import create_book
from ..importer import txt_parse
from ..domain import digest, make_anchor
from ..storage import write_private
from ..errors import Problem
from ..character_identity import bind, scoped_revision
from .service import store_asset
from .models import AssetPlacement

FOLDER=Path(__file__).parent/"examples"
def manifest():
    value=json.loads((FOLDER/"manifest.json").read_text(encoding="utf-8"))
    if value.get("schema")!=1 or value.get("license")!="CC0-1.0":raise RuntimeError("Invalid authored example manifest")
    return value

def example(identifier):
    row=next((row for row in manifest()["bundles"] if row["id"]==identifier),None)
    if not row:raise Problem("RESOURCE_NOT_FOUND","这套示例故事不存在",404)
    return row

def example_bytes(row,file):
    # The path is from the authored manifest, never a request filename.
    root=(FOLDER/row["id"]).resolve()
    path=(root/file).resolve()
    if path.parent!=root:raise RuntimeError("Invalid authored example path")
    data=path.read_bytes()
    expected=row["storySha256"] if file==row["storyFile"] else next(image["sha256"] for image in row["images"] if image["file"]==file)
    if digest(data)!=expected:raise RuntimeError("Authored example hash mismatch")
    return data

def list_examples():
    return {"license":"CC0-1.0","items":[{"id":row["id"],"title":row["title"],"description":row["description"],
        "imageCount":len(row["images"]),"license":row["license"]} for row in manifest()["bundles"]]}

def install(db,owner,identifier):
    row=example(identifier);data=example_bytes(row,row["storyFile"])
    file_hash=digest(data)
    existing=db.scalar(select(Book).where(Book.owner_id==owner,Book.source_hash==file_hash,Book.status=="active"))
    if existing:
        return {"bookId":existing.id,"branchId":existing.original_branch_id,"revisionId":existing.original_revision_id,"storyId":"book-"+existing.id}
    ref=write_private(owner,"original-"+file_hash[:24]+".txt",data)
    made=create_book(db,owner,row["title"],txt_parse(data),file_hash,ref,"txt","本项目原创图文示例 · CC0-1.0")
    at={"kind":"book","bookId":made["bookId"],"branchId":made["branchId"],"revisionId":made["revisionId"]}
    revision=db.get(Revision,made["revisionId"])
    blocks=[db.get(ContentBlock,identifier) for chapter in revision.manifest for identifier in chapter["blocks"]]
    bindings=[]
    scene=0
    for image in row["images"]:
        image_scope=at;purpose=image["kind"];content=None
        if purpose=="avatar":
            block=next((block for block in blocks if image["name"] in block.text),None)
            if not block:raise RuntimeError("Authored portrait must have actual story evidence")
            start=len(block.text[:block.text.index(image["name"])].encode("utf-16-le"))//2
            identity=bind(db,owner,"reader",at["branchId"],"authored:"+image["name"],
                origin="original:"+row["id"]+":"+made["bookId"]+":"+image["name"],
                source_ref={"kind":"reader-proof","bookId":at["bookId"],"branchId":at["branchId"],"blockId":block.id,"start":start,"originalLabel":image["name"]})
            anchor=make_anchor(at["bookId"],at["branchId"],at["revisionId"],block,len(block.text.encode("utf-16-le"))//2)
            image_scope={**at,"identityId":identity.id,"anchor":anchor}
            scoped_revision(db,identity,"reader",at["branchId"],"authored:"+block.id,
                {"bookId":at["bookId"],"readerBranchId":at["branchId"],"revisionId":at["revisionId"],"anchor":anchor},
                {"name":image["name"],"source":"authored","quote":block.text})
            bindings.append({"identityId":identity.id,"name":image["name"],"blockId":block.id,"start":start})
        if purpose=="scene":
            content=revision.manifest[min(scene,len(revision.manifest)-1)]["blocks"][0];scene+=1;purpose="illustration"
        result=store_asset(db,owner,example_bytes(row,image["file"]),"image/png",purpose,
            {"x":0,"y":0,"width":image["width"],"height":image["height"]},
            image_scope,"本项目自编几何插画 · CC0-1.0","original_example")
        db.add(AssetPlacement(owner_id=owner,asset_id=result["assetId"],scope=image_scope,purpose=purpose,
            content_id=content,paragraph_id=content,alt=image["description"],order=0))
    source=db.get(SourceDocument,db.get(Book,made["bookId"]).source_id)
    source.source_map={**source.source_map,"characterBindings":bindings,"imageLicense":"CC0-1.0"}
    db.flush()
    return {**made,"storyId":"book-"+made["bookId"],"imageCount":len(row["images"]),"license":"CC0-1.0"}

