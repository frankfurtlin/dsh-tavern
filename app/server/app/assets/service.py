from datetime import timedelta
import re
from sqlalchemy import select, func, delete
from ..models import User, Book, Branch, Revision, ContentBlock, CharacterIdentity, CharacterScopeRevision, uid, now
from ..domain import owned, anchor_order, validate_anchor
from ..knowledge import safe_scope
from ..errors import Problem
from .models import ImageAsset, AssetVariant, AssetPlacement
from .schemas import AssetScope
from .imaging import prepare

def authorize(db, owner, scope, write=False, purpose=None, content_id=None, paragraph_id=None):
    value = scope.public() if isinstance(scope, AssetScope) else AssetScope.model_validate(scope).public()
    # Fence status changes/deletion without excluding ordinary owner FK checks.
    # Reader writers already hold the book lifecycle before inserting those
    # references, so FOR UPDATE here would reverse their lock order. Re-read
    # status after waiting: authentication may have cached the earlier account.
    user = db.scalar(select(User).where(User.id == owner).with_for_update(read=True)
                     .execution_options(populate_existing=True))
    if not user or user.status != "active":
        raise Problem("AUTH_REQUIRED", "登录已过期，请重新登录", 401)
    if value["kind"] == "book":
        book = owned(db, Book, value["bookId"], owner)
        branch = owned(db, Branch, value["branchId"], owner)
        revision = owned(db, Revision, value["revisionId"], owner)
        if branch.book_id != book.id or revision.branch_id != branch.id:
            raise Problem("RESOURCE_NOT_FOUND", "图片作用域不存在或不可访问", 404)
        if write and (book.status != "active" or branch.status != "active"):
            raise Problem("BRANCH_ARCHIVED", "请先恢复这条故事线", 409)
        blocks = {b for c in revision.manifest for b in c["blocks"]}
        if content_id and content_id not in blocks:
            raise Problem("IMAGE_PLACEMENT_INVALID", "插图位置不属于当前修订")
        if paragraph_id and paragraph_id != content_id:
            raise Problem("IMAGE_PLACEMENT_INVALID", "小说插图须关联完整的实际段落 ID")
        if value.get("anchor"):
            _, authorized_revision, _ = validate_anchor(db, value["anchor"], owner)
            safe_scope(db, value["anchor"], authorized_revision, owner)
        if value.get("identityId"):
            identity = owned(db, CharacterIdentity, value["identityId"], owner)
            if not value.get("anchor"):
                raise Problem("IMAGE_SCOPE_INVALID", "人物图需要明确当前已读位置")
            boundary = anchor_order(revision, value["anchor"])
            facts = db.scalars(select(CharacterScopeRevision).where(
                CharacterScopeRevision.owner_id == owner, CharacterScopeRevision.identity_id == identity.id,
                CharacterScopeRevision.scope_kind == "reader", CharacterScopeRevision.scope_id == branch.id))
            visible = False
            for fact in facts:
                at = fact.context.get("anchor")
                if at and at.get("revisionId") == revision.id:
                    try:
                        if anchor_order(revision, at) <= boundary:
                            visible = True
                            break
                    except (ValueError, KeyError, Problem):
                        pass
            if not visible:
                raise Problem("RESOURCE_NOT_FOUND", "这个人物不在当前已读范围", 404)
        if purpose == "avatar" and not value.get("identityId"):
            raise Problem("IMAGE_SCOPE_INVALID", "请从已识别的稳定人物身份设置头像")
    else:
        from ..world.service import require_line, checked_checkpoint
        from ..world.state import ancestry, public_view
        world, line = require_line(db, owner, value["worldId"], value["lineId"], active=write)
        cp = checked_checkpoint(db, owner, world.id, value["checkpointId"])
        head = checked_checkpoint(db, owner, world.id, line.head_checkpoint_id)
        if cp.id not in {c.id for c in ancestry(db, head, owner)}:
            raise Problem("RESOURCE_NOT_FOUND", "这个图片时间点不在当前世界线", 404)
        if content_id and content_id != cp.id:
            raise Problem("IMAGE_PLACEMENT_INVALID", "世界插图内容须关联当前检查点")
        if paragraph_id:
            try:
                index = int(paragraph_id)
            except ValueError:
                raise Problem("IMAGE_PLACEMENT_INVALID", "世界插图段落序号无效")
            paragraphs = [text for text in re.split(r"\n\s*\n", cp.text) if text.strip()]
            if str(index) != paragraph_id or index < 0 or index >= len(paragraphs):
                raise Problem("IMAGE_PLACEMENT_INVALID", "世界插图段落不在当前公开正文")
        entities = public_view(cp.state, cp.definition)["entities"]
        entity = next((e for e in entities if e["id"] == value.get("entityId") and e["type"] == "character"), None)
        if value.get("entityId") and not entity:
            raise Problem("RESOURCE_NOT_FOUND", "人物不在当前可见资料中", 404)
        if value.get("identityId"):
            from ..models import CharacterAlias
            alias = db.scalar(select(CharacterAlias).where(CharacterAlias.owner_id == owner,
                CharacterAlias.identity_id == value["identityId"], CharacterAlias.scope_kind == "world",
                CharacterAlias.scope_id == world.id, CharacterAlias.entity_id == value.get("entityId")))
            if not entity or not alias:
                raise Problem("RESOURCE_NOT_FOUND", "人物身份不属于当前公开时间点", 404)
        if purpose == "avatar" and not entity:
            raise Problem("IMAGE_SCOPE_INVALID", "请从当前公开人物设置头像")
    return value

def visible_scope(db, origin, target, owner):
    if origin.get("kind") != target.get("kind"):
        return False
    if origin["kind"] == "book":
        fields = ("bookId", "branchId", "revisionId")
        if any(origin.get(name) != target.get(name) for name in fields):
            return False
        if target.get("identityId") and origin.get("identityId") and origin["identityId"]!=target["identityId"]:
            return False
        if origin.get("anchor"):
            if not target.get("anchor"):
                return False
            revision = owned(db, Revision, target["revisionId"], owner)
            return anchor_order(revision, origin["anchor"]) <= anchor_order(revision, target["anchor"])
        return True
    if any(origin.get(name) != target.get(name) for name in ("worldId", "lineId")):
        return False
    if any(origin.get(name) and target.get(name) and origin[name]!=target[name] for name in ("entityId","identityId")):
        return False
    from ..world.service import checked_checkpoint
    from ..world.state import ancestry
    cp = checked_checkpoint(db, owner, target["worldId"], target["checkpointId"])
    if origin.get("entityId"):
        from ..world.state import public_view
        if origin["entityId"] not in {e["id"] for e in public_view(cp.state,cp.definition)["entities"] if e["type"]=="character"}:
            return False
    return origin["checkpointId"] in {c.id for c in ancestry(db, cp, owner)}

def asset_owned(db, owner, asset_id, lock=False):
    query = select(ImageAsset).where(ImageAsset.id == asset_id, ImageAsset.owner_id == owner)
    asset = db.scalar(query.with_for_update() if lock else query)
    if not asset:
        raise Problem("RESOURCE_NOT_FOUND", "图片不存在或不可访问", 404)
    return asset

def asset_result(db, asset, scope):
    from urllib.parse import quote
    from ..domain import canonical
    query = "?scope=" + quote(canonical(scope), safe="")
    variants = {}
    query_rows = select(AssetVariant.name, AssetVariant.width, AssetVariant.height, AssetVariant.mime,
        AssetVariant.sha256, func.octet_length(AssetVariant.data).label("bytes")).where(
        AssetVariant.asset_id == asset.id).order_by(AssetVariant.name)
    for variant in db.execute(query_rows):
        variants[variant.name] = {"width": variant.width, "height": variant.height, "mime": variant.mime,
            "sha256": variant.sha256, "bytes": variant.bytes, "path": "/assets/" + asset.id + "/variants/" + variant.name + query}
    count = db.scalar(select(func.count()).select_from(AssetPlacement).where(AssetPlacement.asset_id == asset.id)) or 0
    return {"assetId": asset.id, "ownerId": asset.owner_id, "sourceKind": asset.source_kind,
        "licenseNote": asset.license_note, "sha256": asset.sha256, "mime": asset.mime,
        "width": asset.width, "height": asset.height, "variants": variants, "scope": scope,
        "createdAt": asset.created_at.isoformat(), "version": asset.version, "referenceCount": count}

def create_asset(db, owner, data, mime, purpose, crop, scope, license_note, source_kind="user_upload"):
    value = authorize(db, owner, scope, write=True, purpose=purpose)
    return store_asset(db, owner, data, mime, purpose, crop, value, license_note, source_kind)

def store_asset(db, owner, data, mime, purpose, crop, value, license_note, source_kind):
    # Internal authored-bundle installer supplies an already owned, newly
    # created scope; public uploads always enter through create_asset.
    variants = prepare(data, mime, purpose, crop)
    original = variants[0]
    asset = ImageAsset(id=uid(), owner_id=owner, purpose=purpose, source_kind=source_kind,
        license_note=license_note, sha256=original.sha256, mime=original.mime,
        width=original.width, height=original.height, origin_scope=value, crop=crop, version=1)
    db.add(asset); db.flush()
    for image in variants:
        db.add(AssetVariant(asset_id=asset.id, name=image.name, width=image.width, height=image.height,
            sha256=image.sha256, mime=image.mime, data=image.data))
    db.flush()
    return asset_result(db, asset, value)

def variant_bytes(db, owner, asset_id, name, scope):
    value = authorize(db, owner, scope)
    asset = asset_owned(db, owner, asset_id)
    references = db.scalars(select(AssetPlacement).where(AssetPlacement.owner_id == owner, AssetPlacement.asset_id == asset.id))
    if not visible_scope(db, asset.origin_scope, value, owner) and not any(visible_scope(db, p.scope, value, owner) for p in references):
        raise Problem("RESOURCE_NOT_FOUND", "这张图片不属于当前故事位置", 404)
    variant = db.get(AssetVariant, (asset.id, name))
    if not variant:
        raise Problem("RESOURCE_NOT_FOUND", "图片尺寸不存在", 404)
    return variant

def placement_result(db, row, scope):
    return {"id": row.id, "scope": row.scope, "purpose": row.purpose, "contentId": row.content_id,
        "paragraphId": row.paragraph_id, "side": row.side, "order": row.order, "assetId": row.asset_id,
        "alt": row.alt, "version": row.version, "asset": asset_result(db, asset_owned(db, row.owner_id, row.asset_id), scope)}

def placements(db, owner, scope):
    value = authorize(db, owner, scope)
    root_field = "bookId" if value["kind"] == "book" else "worldId"
    candidates = db.scalars(select(AssetPlacement).where(AssetPlacement.owner_id == owner,
        AssetPlacement.scope[root_field].astext == value[root_field]).order_by(AssetPlacement.order, AssetPlacement.created_at, AssetPlacement.id))
    return {"items": [placement_result(db, p, value) for p in candidates if visible_scope(db, p.scope, value, owner)]}

def place_asset(db, owner, data):
    value = authorize(db, owner, data.scope, write=True, purpose=data.purpose, content_id=data.contentId, paragraph_id=data.paragraphId)
    asset = asset_owned(db, owner, data.assetId, lock=True)
    if asset.purpose != data.purpose:
        raise Problem("IMAGE_PURPOSE_INVALID", "请使用与展示位置一致的图片用途")
    if data.purpose == "illustration" and not data.contentId:
        raise Problem("IMAGE_PLACEMENT_INVALID", "请选择公开正文中的插图位置")
    old = None
    if data.placementId:
        row = db.scalar(select(AssetPlacement).where(AssetPlacement.id == data.placementId, AssetPlacement.owner_id == owner).with_for_update())
        if not row or row.scope != value:
            raise Problem("RESOURCE_NOT_FOUND", "图片位置不存在或不可访问", 404)
        if row.version != data.expectedVersion:
            raise Problem("ASSET_CONFLICT", "图片已在另一处更新，请载入最新信息", 409)
        old = row.asset_id
        row.asset_id, row.version = asset.id, row.version + 1
    else:
        row = AssetPlacement(id=uid(), owner_id=owner, asset_id=asset.id, scope=value, purpose=data.purpose)
        db.add(row)
    row.content_id, row.paragraph_id, row.side, row.order, row.alt = data.contentId, data.paragraphId, data.side, data.order, data.alt
    db.flush()
    result = placement_result(db, row, value)
    if old and old != asset.id:
        collect_unreferenced(db, owner, [old])
    return result

def remove_placement(db, owner, placement_id, version):
    probe = db.scalar(select(AssetPlacement).where(AssetPlacement.id == placement_id, AssetPlacement.owner_id == owner))
    if not probe:
        raise Problem("RESOURCE_NOT_FOUND", "图片位置不存在或不可访问", 404)
    # Match placement writes: account/lifecycle locks must precede row locks.
    # An update waiting on this row must not hold the account lock in reverse.
    scope = dict(probe.scope)
    authorize(db, owner, scope, write=True)
    row = db.scalar(select(AssetPlacement).where(AssetPlacement.id == placement_id,
        AssetPlacement.owner_id == owner).with_for_update().execution_options(populate_existing=True))
    if not row or row.scope != scope:
        raise Problem("ASSET_CONFLICT", "图片位置已变化，请载入最新信息", 409)
    if row.version != version:
        raise Problem("ASSET_CONFLICT", "图片已在另一处更新，请载入最新信息", 409)
    asset_id = row.asset_id
    db.delete(row); db.flush()
    collect_unreferenced(db, owner, [asset_id])
    return {"deleted": True, "placementId": placement_id, "assetId": asset_id}

def collect_unreferenced(db, owner, asset_ids):
    for asset_id in sorted(set(asset_ids)):
        asset = db.scalar(select(ImageAsset).where(ImageAsset.id == asset_id, ImageAsset.owner_id == owner).with_for_update())
        if asset and not db.scalar(select(AssetPlacement.id).where(AssetPlacement.asset_id == asset.id).limit(1)):
            db.delete(asset)
    db.flush()

def purge_owner(db, owner):
    db.execute(delete(AssetPlacement).where(AssetPlacement.owner_id == owner))
    db.execute(delete(ImageAsset).where(ImageAsset.owner_id == owner))
    db.flush()

def _retained_origin(db, asset):
    retained = db.scalar(select(AssetPlacement).where(AssetPlacement.owner_id == asset.owner_id,
        AssetPlacement.asset_id == asset.id).order_by(AssetPlacement.created_at, AssetPlacement.id).limit(1))
    if retained:
        asset.origin_scope = dict(retained.scope)
        asset.version += 1

def purge_scope(db, owner, kind, scope_id):
    field = {"book": "bookId", "branch": "branchId", "world": "worldId", "line": "lineId"}.get(kind)
    if not field:
        raise ValueError("Exact book/branch/world/line scope is required")
    rows = list(db.scalars(select(AssetPlacement).where(AssetPlacement.owner_id == owner, AssetPlacement.scope[field].astext == scope_id)))
    ids = {row.asset_id for row in rows}
    for row in rows:
        db.delete(row)
    db.flush()
    # Includes never-attached uploads, but surviving references still win.
    for asset in db.scalars(select(ImageAsset).where(ImageAsset.owner_id == owner, ImageAsset.origin_scope[field].astext == scope_id)):
        ids.add(asset.id)
        _retained_origin(db, asset)
    collect_unreferenced(db, owner, ids)

def detach_book(db, owner, old_book_id, retained):
    targets = {branch: item["bookId"] for item in retained for branch in item["branchIds"]}
    rows = list(db.scalars(select(AssetPlacement).where(AssetPlacement.owner_id == owner, AssetPlacement.scope["bookId"].astext == old_book_id)))
    ids = {row.asset_id for row in rows}
    for row in rows:
        target = targets.get(row.scope.get("branchId"))
        if target:
            value = {**row.scope, "bookId": target}
            if value.get("anchor"):
                value["anchor"] = {**value["anchor"], "bookId": target}
            row.scope = value
            row.version += 1
        else:
            db.delete(row)
    db.flush()
    for asset in db.scalars(select(ImageAsset).where(ImageAsset.owner_id == owner, ImageAsset.origin_scope["bookId"].astext == old_book_id)):
        ids.add(asset.id)
        target = targets.get(asset.origin_scope.get("branchId"))
        if target:
            value = {**asset.origin_scope, "bookId": target}
            if value.get("anchor"):
                value["anchor"] = {**value["anchor"], "bookId": target}
            asset.origin_scope = value
        else:
            _retained_origin(db, asset)
    collect_unreferenced(db, owner, ids)

def expired_uploads(db, before=None, limit=100):
    before = before or now() - timedelta(days=1)
    candidates = db.scalars(select(ImageAsset).where(ImageAsset.created_at < before).order_by(ImageAsset.created_at).limit(min(100,max(1,limit))))
    for asset in candidates:
        collect_unreferenced(db, asset.owner_id, [asset.id])

# Lifecycle callers own their existing transaction; these helpers only flush.
purgeOwner = purge_owner
purgeScope = purge_scope
detachBook = detach_book
