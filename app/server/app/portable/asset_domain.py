"""Portable image records use licensed binary files, never Base64 JSON."""
from copy import deepcopy
from pathlib import Path

from sqlalchemy import func,or_,select,text

from .. import models as reader
from ..assets.models import ImageAsset,AssetVariant,AssetPlacement
from ..assets.portable import ASSET_FIELDS,VARIANT_FIELDS,PLACEMENT_FIELDS,validate_records
from ..assets.service import authorize
from ..domain import digest, owned, validate_anchor, anchor_order
from ..errors import Problem
from ..world import models as world
from .errors import ArchiveError
from .manifest import ArchiveLimits


def records(item,names):return {name:deepcopy(getattr(item,name)) for name in names}


def collect_images(db,owner,selected,writer):
    if db.scalar(text("SELECT to_regclass('image_assets')")) is None:return
    books={getattr(item,"_portable_original_book",item.id) for item in selected[reader.Book]}
    branches={item.id:item.book_id for item in selected[reader.Branch]}
    revisions={item.id for item in selected[reader.Revision]}
    worlds={item.id for item in selected[world.World]}
    lines={item.id for item in selected[world.WorldLine]};points={item.id for item in selected[world.WorldCheckpoint]}
    def in_scope(scope):
        if scope.get("kind")=="book":return scope.get("branchId") in branches and scope.get("revisionId") in revisions
        return scope.get("kind")=="world" and scope.get("worldId") in worlds and scope.get("lineId") in lines and scope.get("checkpointId") in points
    query=select(AssetPlacement).where(AssetPlacement.owner_id==owner,or_(AssetPlacement.scope["bookId"].astext.in_(books),AssetPlacement.scope["worldId"].astext.in_(worlds),
        AssetPlacement.scope["branchId"].astext.in_(branches),AssetPlacement.scope["lineId"].astext.in_(lines)))
    if db.scalar(select(func.count()).select_from(query.subquery()))>10000:raise ArchiveError("BACKUP_ASSET_LIMIT")
    placements=[item for item in db.scalars(query.order_by(AssetPlacement.id)) if in_scope(item.scope)]
    for item in placements:
        try:authorize_archived_scope(db,owner,item.scope,purpose=item.purpose,content_id=item.content_id,paragraph_id=item.paragraph_id)
        except Problem as exc:raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID") from exc
    used={item.asset_id for item in placements}
    query=select(ImageAsset).where(ImageAsset.owner_id==owner,or_(ImageAsset.id.in_(used),ImageAsset.origin_scope["bookId"].astext.in_(books),ImageAsset.origin_scope["worldId"].astext.in_(worlds)))
    if db.scalar(select(func.count()).select_from(query.subquery()))>2000:raise ArchiveError("BACKUP_ASSET_LIMIT")
    assets=[item for item in db.scalars(query.order_by(ImageAsset.id)) if item.id in used or in_scope(item.origin_scope)]
    for item in assets:
        if in_scope(item.origin_scope):
            try:authorize_archived_scope(db,owner,item.origin_scope,purpose=item.purpose)
            except Problem as exc:raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID") from exc
    query=select(AssetVariant).where(AssetVariant.asset_id.in_([item.id for item in assets])).order_by(AssetVariant.asset_id,AssetVariant.name)
    if db.scalar(select(func.count()).select_from(query.subquery()))>8000:raise ArchiveError("BACKUP_ASSET_LIMIT")
    variants=list(db.scalars(query))
    # A package's image limit applies to each actual stored file. Never silently
    # re-encode an existing image and claim its original hash was restored.
    if any(len(item.data)>ArchiveLimits().max_image_bytes for item in variants):raise ArchiveError("BACKUP_ASSET_LIMIT")
    asset_rows=[records(item,ASSET_FIELDS) for item in assets]
    variant_rows=[records(item,VARIANT_FIELDS) for item in variants]
    placement_rows=[records(item,PLACEMENT_FIELDS) for item in placements]
    def project(scope):
        scope=deepcopy(scope)
        if scope["kind"]=="book":
            scope["bookId"]=branches[scope["branchId"]]
            if scope.get("anchor"):scope["anchor"]["bookId"]=scope["bookId"]
        return scope
    for row in asset_rows:
        if not in_scope(row["origin_scope"]):
            row["origin_scope"]=deepcopy(next(item.scope for item in placements if item.asset_id==row["id"]))
        row["origin_scope"]=project(row["origin_scope"])
    for row in placement_rows:row["scope"]=project(row["scope"])
    # UUID checks run against original rows. Carrier source IDs are portable
    # identifiers and will become new UUIDs before the restore validation.
    originals=[records(item,ASSET_FIELDS) for item in assets]
    validate_records(originals,variant_rows,[records(item,PLACEMENT_FIELDS) for item in placements])
    by_asset={item.id:item for item in assets}
    for row in asset_rows:
        fields={key:value for key,value in row.items() if key not in {"id","owner_id","created_at"}}
        writer.add("asset",row["id"],"ImageAsset",fields,createdAt=row["created_at"].isoformat())
    for row in variant_rows:
        suffix={"image/png":"png","image/webp":"webp"}.get(row["mime"])
        if suffix is None:raise ArchiveError("BACKUP_ASSET_INVALID")
        path="assets/"+row["sha256"]+"."+suffix;target=writer.root/path
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(row["data"])
            writer.files.append({"path":path,"kind":"asset","mediaType":row["mime"],"size":len(row["data"]),"sha256":row["sha256"],"license":by_asset[row["asset_id"]].license_note})
        fields={key:value for key,value in row.items() if key!="data"}
        writer.add("asset_variant",row["asset_id"]+":"+row["name"],"AssetVariant",fields,
            blob={"path":path,"sha256":row["sha256"]},createdAt=by_asset[row["asset_id"]].created_at.isoformat())
    for row in placement_rows:
        fields={key:value for key,value in row.items() if key not in {"id","owner_id","created_at"}}
        writer.add("asset_ref",row["id"],"AssetPlacement",fields,createdAt=row["created_at"].isoformat())


def variant_values(record,root,files):
    fields=record.get("fields",{});blob=record.get("blob",{})
    if set(fields)!=VARIANT_FIELDS-{"data"} or set(blob)!={"path","sha256"}:raise ArchiveError("BACKUP_ASSET_INVALID")
    entry=files.get(blob["path"])
    if entry is None or entry["kind"]!="asset" or entry["sha256"]!=blob["sha256"] or entry["sha256"]!=fields["sha256"] or entry["mediaType"]!=fields["mime"]:
        raise ArchiveError("BACKUP_ASSET_INVALID")
    return {**fields,"data":(root/entry["path"]).read_bytes()}


def validate_restored_images(db,owner,rows):
    assets=[records(item,ASSET_FIELDS) for item in rows.get("asset",{}).values()]
    variants=[records(item,VARIANT_FIELDS) for item in rows.get("asset_variant",{}).values()]
    placements=[records(item,PLACEMENT_FIELDS) for item in rows.get("asset_ref",{}).values()]
    if assets or variants or placements:
        validate_records(assets,variants,placements)
        for row in assets:authorize_archived_scope(db,owner,row["origin_scope"],purpose=row["purpose"])
        for row in placements:authorize_archived_scope(db,owner,row["scope"],purpose=row["purpose"],content_id=row["content_id"],paragraph_id=row["paragraph_id"])


def authorize_archived_scope(db,owner,value,*,purpose,content_id=None,paragraph_id=None):
    """Validate private archive structure without manufacturing read progress.

    Normal asset display/upload still uses assets.authorize/safe_scope. A full
    private backup contains future owned illustrations and hidden-world images;
    those records must survive import even while ordinary UI access is denied.
    """
    from ..assets.schemas import AssetScope
    value=AssetScope.model_validate(value).public()
    # Restore holds its owner lock already; export uses a consistent read view.
    # Avoid upgrading two concurrent snapshot share-locks to exclusive locks.
    user=db.scalar(select(reader.User).where(reader.User.id==owner))
    if not user or user.status!="active":raise Problem("AUTH_REQUIRED","登录已过期，请重新登录",401)
    if value["kind"]=="book":
        book=owned(db,reader.Book,value["bookId"],owner)
        branch=owned(db,reader.Branch,value["branchId"],owner)
        revision=owned(db,reader.Revision,value["revisionId"],owner)
        if branch.book_id!=book.id or revision.branch_id!=branch.id:raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
        blocks={identifier for chapter in revision.manifest for identifier in chapter["blocks"]}
        if content_id is not None:
            block=owned(db,reader.ContentBlock,content_id,owner)
            if content_id not in blocks or block.book_id!=book.id:raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
        if paragraph_id is not None and paragraph_id!=content_id:raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
        anchor=value.get("anchor")
        if anchor:validate_anchor(db,anchor,owner)
        if value.get("identityId"):
            identity=owned(db,reader.CharacterIdentity,value["identityId"],owner)
            if not anchor:raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
            boundary=anchor_order(revision,anchor)
            facts=db.scalars(select(reader.CharacterScopeRevision).where(reader.CharacterScopeRevision.owner_id==owner,
                reader.CharacterScopeRevision.identity_id==identity.id,reader.CharacterScopeRevision.scope_kind=="reader",
                reader.CharacterScopeRevision.scope_id==branch.id))
            matched=False
            for fact in facts:
                at=fact.context.get("anchor")
                if at and at.get("revisionId")==revision.id:
                    validate_anchor(db,at,owner)
                    if anchor_order(revision,at)<=boundary:matched=True
            if not matched:raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
        if purpose=="avatar" and not value.get("identityId"):raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
    else:
        from ..world.service import require_line, checked_checkpoint
        from ..world.state import ancestry
        import re
        instance,line=require_line(db,owner,value["worldId"],value["lineId"])
        cp=checked_checkpoint(db,owner,instance.id,value["checkpointId"])
        head=checked_checkpoint(db,owner,instance.id,line.head_checkpoint_id)
        if cp.id not in {item.id for item in ancestry(db,head,owner)}:raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
        if content_id is not None and content_id!=cp.id:raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
        if paragraph_id is not None:
            parts=[part for part in re.split(r"\n\s*\n",cp.text) if part.strip()]
            if not paragraph_id.isdecimal() or str(int(paragraph_id))!=paragraph_id or int(paragraph_id)>=len(parts):raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
        entity=next((item for item in cp.definition["entities"] if item["id"]==value.get("entityId") and item["type"]=="character"),None)
        if value.get("entityId") and (not entity or value["entityId"] not in cp.state["entities"]):raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
        if value.get("identityId"):
            owned(db,reader.CharacterIdentity,value["identityId"],owner)
            alias=db.scalar(select(reader.CharacterAlias).where(reader.CharacterAlias.owner_id==owner,
                reader.CharacterAlias.identity_id==value["identityId"],reader.CharacterAlias.scope_kind=="world",
                reader.CharacterAlias.scope_id==instance.id,reader.CharacterAlias.entity_id==value.get("entityId")))
            if not entity or not alias:raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
        if purpose=="avatar" and not entity:raise ArchiveError("BACKUP_ASSET_SCOPE_INVALID")
    return value
