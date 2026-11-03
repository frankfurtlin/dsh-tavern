from datetime import timedelta
import hashlib
import hmac
import re
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, Query
from fastapi.responses import Response
from sqlalchemy import select, func, text
from ..db import get_db, SessionLocal
from ..config import settings
from ..errors import Problem
from ..security import current_user, rate_limit
from ..models import User, DeviceSession, Task, UsageRecord, now
from .models import Account, Config, CreditLot, Ledger, Hold, Membership, RedeemCard, Invite, InviteClaim, Product, Order, Audit, StoredObject, KnowledgeDoc, KnowledgeChunk, PaymentReceipt
from .core import account, require_admin, audit, safe_owned, hash_code, lock_owner, config
from .wallet import balances, grant, grant_membership
from .growth import daily_claim, admission, claim_invite
from .events import emit, sanitize, client_metadata
from .storage import upload, read, remove, object_result, MAX_UPLOAD
from .knowledge import create_document, search_documents, search_reading, search_player
from .payments import payment_options, new_order, checkout, order_result, reconcile, i6ls_notification, waffo, waffo_event
from .schemas import AccountInput, GrantInput, UserAdminInput, InviteInput, CardInput, RedeemInput, ProductInput, PolicyInput, OrderInput, SearchInput, TelemetryInput

router = APIRouter(prefix="/product", tags=["product-foundation"])


def admin(user=Depends(current_user), db=Depends(get_db)):
    require_admin(db, user)
    return user


def key(actor, kind, request_key):
    return kind + ":" + actor.id + ":" + request_key


def generated_code(actor, kind, request_key):
    material = key(actor, kind, request_key).encode()
    return kind.upper() + "-" + hmac.new(settings.jwt_signing_secret.encode(), material, hashlib.sha256).hexdigest()[:28].upper()


@router.get("/public/config")
def public_config(db=Depends(get_db)):
    from .default_models import summary
    return {"registrationEnabled": settings.registration_enabled, "defaultModels": summary(db),
            "inviteRequired": bool(config(db, "beta").get("inviteRequired")), "payment": payment_options(),
            "modules": ["account", "admin", "telemetry", "wallet", "membership", "payments", "growth", "storage", "knowledge", "content", "i18n"],
            "excluded": ["llm-dispatch", "prompt-assets"]}


@router.get("/admin/default-models")
def default_models(user=Depends(admin), db=Depends(get_db)):
    from .default_models import summary
    return summary(db)


from .schemas import DefaultModelsInput


@router.put("/admin/default-models")
def set_default_models(data: DefaultModelsInput, user=Depends(admin), db=Depends(get_db)):
    from .default_models import configure
    result = configure(db, user, data)
    db.commit()
    return result


@router.get("/account")
def me(user=Depends(current_user), db=Depends(get_db)):
    row = account(db, user)
    db.commit()
    return {"id": user.id, "username": user.username, "role": row.role, "displayName": row.display_name,
            "language": row.language, "avatarId": row.avatar_id}


@router.put("/account")
def update_account(data: AccountInput, user=Depends(current_user), db=Depends(get_db)):
    row = account(db, user)
    if data.avatarId:
        obj = safe_owned(db, StoredObject, data.avatarId, user.id)
        if obj.deleted_at or not obj.content_type.startswith("image/"):
            raise Problem("AVATAR_INVALID", "请选择有效的已上传图片")
    row.display_name, row.language, row.avatar_id = data.displayName.strip(), data.language, data.avatarId
    db.commit()
    return {"updated": True}


@router.get("/wallet")
def wallet(user=Depends(current_user), db=Depends(get_db)):
    members = list(db.scalars(select(Membership).where(Membership.owner_id == user.id, Membership.ends_at > now()).order_by(Membership.ends_at.desc())))
    ledger = list(db.scalars(select(Ledger).where(Ledger.owner_id == user.id).order_by(Ledger.created_at.desc()).limit(100)))
    holds = list(db.scalars(select(Hold).where(Hold.owner_id == user.id, Hold.state.in_(("held", "unknown"))).order_by(Hold.created_at.desc()).limit(100)))
    orders = list(db.scalars(select(Order).where(Order.owner_id == user.id).order_by(Order.created_at.desc()).limit(100)))
    return {**balances(db, user.id), "unlimitedUsage":settings.product_unlimited_usage,"billingEnabled": bool(config(db, "billing").get("enabled")) and not settings.product_unlimited_usage, "payment": payment_options(),
        "memberships": [{"id": m.id, "title": m.title, "startsAt": m.starts_at.isoformat(), "endsAt": m.ends_at.isoformat(),
                         "dailyCredits": m.daily_credits, "source": m.source} for m in members],
        "ledger": [{"id": l.id, "kind": l.kind, "amount": l.amount, "reference": l.reference, "details": l.details, "createdAt": l.created_at.isoformat()} for l in ledger],
        "holds": [{"id": h.id, "taskId": h.task_id, "operation": h.operation, "reserved": h.reserved, "state": h.state} for h in holds],
        "orders": [order_result(o) for o in orders]}


@router.post("/growth/daily-claim")
def claim_daily(user=Depends(current_user), db=Depends(get_db)):
    result = daily_claim(db, user.id)
    db.commit()
    return result


@router.post("/growth/invite")
def redeem_invite(data: RedeemInput, user=Depends(current_user), db=Depends(get_db)):
    rate_limit(db, "invite:" + user.id, 20, 3600)
    lock_owner(db, user.id)
    prior = db.scalar(select(InviteClaim).where(InviteClaim.owner_id == user.id))
    if prior:
        existing = db.get(Invite, prior.invite_id)
        if existing.code_hash != hash_code(data.code):
            raise Problem("INVITE_ALREADY_CLAIMED", "此账号已使用其他邀请码", 409)
        return {"claimed": True, "alreadyClaimed": True}
    row = admission(db, data.code)
    claim_invite(db, user.id, row)
    db.commit()
    return {"claimed": True, "reward": row.reward}


@router.post("/membership/redeem")
def redeem_card(data: RedeemInput, user=Depends(current_user), db=Depends(get_db)):
    rate_limit(db, "redeem:" + user.id, 20, 3600)
    lock_owner(db, user.id)
    row = db.scalar(select(RedeemCard).where(RedeemCard.code_hash == hash_code(data.code)).with_for_update())
    if not row or (row.redeemed_at and row.redeemed_by != user.id):
        raise Problem("CARD_UNAVAILABLE", "兑换码无效或已被使用", 409)
    if row.redeemed_at:
        return {"redeemed": True, "alreadyRedeemed": True}
    if row.expires_at <= now():
        raise Problem("CARD_EXPIRED", "兑换码已过期", 409)
    if row.credits:
        grant(db, user.id, row.credits, "card:" + row.id, reason="redeem_card")
    if row.member_days:
        grant_membership(db, user.id, row.member_days, "card-member:" + row.id, source="card")
    row.redeemed_by, row.redeemed_at = user.id, now()
    emit(db, user.id, "redeem:" + row.id, "fact.membership.redeemed")
    db.commit()
    return {"redeemed": True, "credits": row.credits, "memberDays": row.member_days}


@router.get("/catalog")
def catalog(user=Depends(current_user), db=Depends(get_db)):
    from .payments import snapshot
    return {"items": [{"id": p.id, **snapshot(p)} for p in db.scalars(select(Product).where(Product.active).order_by(Product.amount_minor))], "payment": payment_options()}


@router.post("/orders", status_code=201)
def create_order(data: OrderInput, user=Depends(current_user), db=Depends(get_db)):
    row = new_order(db, user, data.productId, data.requestKey)
    db.commit()  # Persist intent before any gateway network operation.
    return order_result(row)


@router.post("/orders/{order_id}/checkout")
def order_checkout(order_id: str, request: Request, user=Depends(current_user), db=Depends(get_db)):
    lock_owner(db, user.id)
    row = safe_owned(db, Order, order_id, user.id)
    try:
        checkout(db, row, request.client.host)
    except Problem:
        db.commit()  # Unknown outbound result is durable and never blindly retried.
        raise
    db.commit()
    return order_result(row)


@router.get("/orders/{order_id}")
def order_status(order_id: str, user=Depends(current_user), db=Depends(get_db)):
    return order_result(safe_owned(db, Order, order_id, user.id))


@router.post("/orders/{order_id}/reconcile")
def order_reconcile(order_id: str, user=Depends(current_user), db=Depends(get_db)):
    row = safe_owned(db, Order, order_id, user.id)
    reconcile(db, row)
    db.commit()
    return order_result(row)


@router.post("/orders/{order_id}/cancel-renewal")
def cancel_renewal(order_id: str, user=Depends(current_user), db=Depends(get_db)):
    row = safe_owned(db, Order, order_id, user.id)
    if row.provider != "waffo" or row.snapshot["kind"] != "member":
        raise Problem("RENEWAL_UNAVAILABLE", "此订单没有自动续费", 409)
    result = waffo("/cancel", {"orderNo": "WF_" + row.id, "purchaseMode": "SUBSCRIPTION"})
    for event in result.get("events", []):
        waffo_event(db, event, recovered=result)
    db.commit()
    return {"requested": True, "providerStatus": result.get("status"), "accessEndsAt": max((m.ends_at.isoformat() for m in db.scalars(select(Membership).where(Membership.owner_id == user.id))), default=None)}


@router.api_route("/payments/i6ls/notify", methods=["GET", "POST"])
async def i6ls_notify(request: Request, db=Depends(get_db)):
    from urllib.parse import parse_qsl
    raw = await request.body()
    if len(raw) > 65536:
        raise Problem("PAYMENT_BODY_TOO_LARGE", "支付回执过大", 413)
    pairs = list(request.query_params.multi_items()) + (parse_qsl(raw.decode("utf-8")) if raw else [])
    if len(pairs) != len({k for k, v in pairs}):
        raise Problem("PAYMENT_FIELDS_AMBIGUOUS", "支付回执字段重复", 400)
    i6ls_notification(db, dict(pairs))
    db.commit()
    return Response("success", media_type="text/plain")


@router.post("/payments/waffo/webhook")
async def waffo_webhook(request: Request, db=Depends(get_db)):
    if settings.product_payment_provider != "waffo" or not payment_options()["enabled"]:
        raise Problem("PAYMENT_NOT_CONFIGURED", "支付通道未启用", 503)
    raw = await request.body()
    if len(raw) > 262144:
        raise Problem("PAYMENT_BODY_TOO_LARGE", "支付回执过大", 413)
    signature = request.headers.get("x-waffo-signature")
    if not signature:
        raise Problem("PAYMENT_SIGNATURE_INVALID", "支付签名缺失", 400)
    event = waffo("/verify-webhook", raw=raw, signature=signature)
    waffo_event(db, event)
    db.commit()
    return {"received": True}


@router.post("/telemetry/track")
def telemetry(data: TelemetryInput, user=Depends(current_user), db=Depends(get_db)):
    if data.expectedUserId != user.id or any(e.ownerUserId != user.id for e in data.events):
        raise Problem("TELEMETRY_OWNER_CHANGED", "事件所属账号已变化", 409)
    rate_limit(db, "telemetry:" + user.id, 120, 60)
    for e in data.events:
        if e.name.startswith(("fact.", "ai.provider.")) or not re.fullmatch(r"[A-Za-z0-9_./ :{}-]{1,120}", e.name):
            raise Problem("TELEMETRY_FACT_FORBIDDEN", "客户端不能记录服务端业务事实", 403)
        path = e.path.split("?")[0].split("#")[0]
        path = re.sub(r"/[a-f0-9]{32}(?=/|$)", "/:id", path)
        if not path.startswith(("/worlds", "/settings", "/product", "/library", "/notebook", "/books", "/stories", "/conversations", "/insights", "/downloads", "/explore", "/works", "/recent", "/published", "/authors", "/help", "/tasks", "/my-prompts")):
            path = "/other"
        emit(db, user.id, "client:" + user.id + ":" + e.eventId, e.name, category=e.type, path=path,
             session=e.sessionId, anon=e.anonId, data={"deviceType": e.deviceType, "statusCode": e.statusCode,
                "durationMs": e.durationMs, "metadata": client_metadata(e.metadata), "version": 2})
    db.commit()
    return {"code": 200, "data": {"persisted": True, "accepted": len(data.events)}}


@router.post('/telemetry/public')
def public_telemetry(data:TelemetryInput,request:Request,db=Depends(get_db)):
    if data.expectedUserId is not None or any(e.ownerUserId is not None for e in data.events):
        raise Problem('TELEMETRY_OWNER_CHANGED','公开访问不能归属到账号',409)
    import hashlib
    rate_limit(db,'public-telemetry:'+hashlib.sha256((request.client.host if request.client else 'local').encode()).hexdigest(),120,60)
    for e in data.events:
        # Only public entry visits are accepted without authentication. Other
        # queued hints are acknowledged and discarded, never stored as facts.
        path=e.path.split('?')[0].split('#')[0]
        if e.type!='page_view' or path not in ('/login','/register','/recover','/help','/data-use'):continue
        emit(db,None,'public:'+e.anonId+':'+e.eventId,'public.visit',category='page_view',path=path,
            session=e.sessionId,anon=e.anonId,data={'deviceType':e.deviceType,'version':2})
    db.commit()
    return {'code':200,'data':{'persisted':True,'accepted':len(data.events)}}


@router.post("/objects", status_code=201)
async def upload_object(file: UploadFile = File(...), purpose: str = Form("attachment"), rights: str = Form("用户提供 · 权利未核验"), user=Depends(current_user), db=Depends(get_db)):
    if purpose not in ("attachment", "avatar", "knowledge"):
        raise Problem("UPLOAD_PURPOSE_INVALID", "文件用途无效")
    rate_limit(db, 'product-upload:' + user.id, 40, 3600)
    lock_owner(db, user.id)
    content = await file.read(MAX_UPLOAD + 1)
    row = upload(db, user.id, file.filename or "file.txt", content, purpose=purpose, rights=rights)
    try:
        document = create_document(db, user.id, row, content) if purpose == "knowledge" else None
        if document and document.object_id != row.id:
            remove(row)
            db.delete(row)
            row = safe_owned(db, StoredObject, document.object_id, user.id)
        result = {**object_result(row), "documentId": document.id if document else None}
        db.commit()
        return result
    except Exception:
        remove(row)
        db.rollback()
        raise


@router.get("/objects")
def objects(user=Depends(current_user), db=Depends(get_db)):
    return {"items": [object_result(o) for o in db.scalars(select(StoredObject).where(StoredObject.owner_id == user.id,
        StoredObject.deleted_at.is_(None)).order_by(StoredObject.created_at.desc()).limit(200))]}


@router.get("/objects/{object_id}/content")
def object_content(object_id: str, user=Depends(current_user), db=Depends(get_db)):
    lock_owner(db, user.id)
    row = safe_owned(db, StoredObject, object_id, user.id)
    from urllib.parse import quote
    return Response(read(row), media_type=row.content_type, headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(row.name)})


@router.delete("/objects/{object_id}")
def delete_object(object_id: str, user=Depends(current_user), db=Depends(get_db)):
    lock_owner(db, user.id)
    row = safe_owned(db, StoredObject, object_id, user.id)
    remove(row)
    profile = db.get(Account, user.id)
    if profile and profile.avatar_id == row.id:
        profile.avatar_id = None
    for document in db.scalars(select(KnowledgeDoc).where(KnowledgeDoc.object_id == row.id)):
        db.delete(document)
    db.commit()
    return {"deleted": True}


@router.get("/knowledge")
def documents(user=Depends(current_user), db=Depends(get_db)):
    counts = select(KnowledgeChunk.doc_id, func.count().label("chunks")).group_by(KnowledgeChunk.doc_id).subquery()
    rows = db.execute(select(KnowledgeDoc, counts.c.chunks).join(counts, counts.c.doc_id == KnowledgeDoc.id).where(
        KnowledgeDoc.owner_id == user.id, KnowledgeDoc.status == "ready").order_by(KnowledgeDoc.created_at.desc()).limit(200)).all()
    return {"items": [{"id": d.id, "title": d.title, "objectId": d.object_id, "sourceVersion": d.source_version,
        "scope": d.scope, "chunks": chunks} for d, chunks in rows]}


@router.post("/knowledge/search")
def search(data: SearchInput, user=Depends(current_user), db=Depends(get_db)):
    if data.scope == "read":
        if not data.anchor:
            raise Problem("READING_SCOPE_REQUIRED", "请选择已读位置")
        hits = search_reading(db, user.id, data.query, data.anchor.model_dump())
    elif data.scope == "player":
        if not data.worldId or not data.lineId:
            raise Problem("PLAYER_SCOPE_REQUIRED", "请选择世界和故事线")
        hits = search_player(db, user.id, data.query, data.worldId, data.lineId)
    else:
        hits = search_documents(db, user.id, data.query)
    return {"items": hits, "scope": data.scope, "searchType": "lexical", "modelCalls": 0}


@router.get("/admin/users")
def users(query: str = Query(default="", max_length=32), offset: int = Query(default=0, ge=0), user=Depends(admin), db=Depends(get_db)):
    filters = [User.username.ilike("%" + query.replace("%", "\\%").replace("_", "\\_") + "%", escape="\\")] if query else []
    rows = db.execute(select(User, Account).outerjoin(Account, Account.owner_id == User.id).where(*filters).order_by(User.created_at.desc()).offset(offset).limit(50)).all()
    return {"total": db.scalar(select(func.count()).select_from(User).where(*filters)), "items": [{"id": u.id, "username": u.username,
        "status": u.status, "role": a.role if a else "USER", "memo": a.memo if a else "", "createdAt": u.created_at.isoformat(),
        **balances(db, u.id)} for u, a in rows]}


@router.put("/admin/users/{owner}")
def change_user(owner: str, data: UserAdminInput, user=Depends(admin), db=Depends(get_db)):
    db.execute(text("SELECT pg_advisory_xact_lock(817320015)"))
    target = db.scalar(select(User).where(User.id == owner).with_for_update())
    if not target or target.status not in ("active", "disabled"):
        raise Problem("ACCOUNT_UNAVAILABLE", "账号不可修改", 409)
    row = account(db, target) if not db.get(Account, owner) else db.get(Account, owner)
    if row.role == "ADMIN" and (data.role != "ADMIN" or data.status != "active"):
        remaining = db.scalar(select(func.count()).select_from(Account).join(User, User.id == Account.owner_id).where(Account.role == "ADMIN", User.status == "active", Account.owner_id != owner))
        if not remaining:
            raise Problem("LAST_ADMIN_REQUIRED", "须保留至少一个可用管理员", 409)
    record = audit(db, user.id, "user.update", owner, key(user, "user", data.requestKey), data.model_dump(exclude={"requestKey"}))
    if record._replayed:
        return {"updated": True, "replayed": True}
    target.status, row.role, row.memo = data.status, data.role, data.memo
    if data.status == "disabled":
        for session in db.scalars(select(DeviceSession).where(DeviceSession.owner_id == owner)):
            session.revoked_at = now()
    db.commit()
    return {"updated": True}


@router.post("/admin/users/{owner}/grant")
def grant_user(owner: str, data: GrantInput, user=Depends(admin), db=Depends(get_db)):
    lock_owner(db, owner)
    if not data.credits and not data.memberDays:
        raise Problem("GRANT_EMPTY", "请输入积分或会员天数")
    operation = key(user, "admin-grant", data.requestKey)
    audit(db, user.id, "entitlement.grant", owner, operation, data.model_dump(exclude={"requestKey"}))
    if data.credits:
        grant(db, owner, data.credits, operation, reason="admin_gift")
    if data.memberDays:
        grant_membership(db, owner, data.memberDays, operation, daily_credits=data.dailyCredits)
    db.commit()
    return {"granted": True, **balances(db, owner)}


@router.post("/admin/invites", status_code=201)
def create_invite(data: InviteInput, user=Depends(admin), db=Depends(get_db)):
    lock_owner(db, user.id)
    code = generated_code(user, "invite", data.requestKey)
    audit(db, user.id, "invite.create", None, key(user, "invite", data.requestKey), data.model_dump(exclude={"requestKey"}))
    row = db.scalar(select(Invite).where(Invite.code_hash == hash_code(code)))
    if not row:
        row = Invite(code_hash=hash_code(code), label=data.label, creator_id=user.id, max_uses=data.maxUses,
            expires_at=now() + timedelta(days=data.validDays), reward=data.reward, admission=data.admission)
        db.add(row)
    db.commit()
    return {"id": row.id, "code": code, "label": row.label, "maxUses": row.max_uses, "uses": row.uses}


@router.get("/admin/invites")
def invites(user=Depends(admin), db=Depends(get_db)):
    return {"items": [{"id": i.id, "label": i.label, "maxUses": i.max_uses, "uses": i.uses, "reward": i.reward,
        "disabled": i.disabled, "admission": i.admission, "expiresAt": i.expires_at.isoformat()}
        for i in db.scalars(select(Invite).order_by(Invite.created_at.desc()).limit(100))]}


@router.delete("/admin/invites/{invite_id}")
def disable_invite(invite_id: str, user=Depends(admin), db=Depends(get_db)):
    row = db.scalar(select(Invite).where(Invite.id == invite_id).with_for_update())
    if not row:
        raise Problem("RESOURCE_NOT_FOUND", "邀请不存在", 404)
    row.disabled = True
    audit(db, user.id, "invite.disable", row.id, key(user, "invite-disable", row.id), {})
    db.commit()
    return {"disabled": True}


@router.post("/admin/cards", status_code=201)
def create_card(data: CardInput, user=Depends(admin), db=Depends(get_db)):
    lock_owner(db, user.id)
    if not data.credits and not data.memberDays:
        raise Problem("GRANT_EMPTY", "兑换卡必须有积分或会员权益")
    code = generated_code(user, "card", data.requestKey)
    operation = key(user, "card", data.requestKey)
    audit(db, user.id, "card.create", None, operation, data.model_dump(exclude={"requestKey"}))
    row = db.scalar(select(RedeemCard).where(RedeemCard.request_key == operation))
    if not row:
        row = RedeemCard(code_hash=hash_code(code), request_key=operation, credits=data.credits, member_days=data.memberDays,
                         expires_at=now() + timedelta(days=data.validDays))
        db.add(row)
    db.commit()
    return {"id": row.id, "code": code, "credits": row.credits, "memberDays": row.member_days}


@router.get("/admin/catalog")
def admin_catalog(offset: int = Query(default=0, ge=0), user=Depends(admin), db=Depends(get_db)):
    from .payments import snapshot
    rows = db.scalars(select(Product).order_by(Product.created_at.desc(), Product.id).offset(offset).limit(50))
    return {"total": db.scalar(select(func.count()).select_from(Product)),
            "items": [{"id": p.id, **snapshot(p), "active": p.active} for p in rows]}


@router.get("/admin/orders")
def admin_orders(state: str = Query(default="", max_length=24), query: str = Query(default="", max_length=100),
                 offset: int = Query(default=0, ge=0), user=Depends(admin), db=Depends(get_db)):
    from sqlalchemy import or_
    filters = [Order.state == state] if state else []
    if query:
        literal = "%" + query.replace("%", "\\%").replace("_", "\\_") + "%"
        filters.append(or_(User.username.ilike(literal, escape="\\"), Order.id == query))
    receipts = select(PaymentReceipt.order_id, func.count().label("count")).where(PaymentReceipt.verified).group_by(PaymentReceipt.order_id).subquery()
    rows = db.execute(select(Order, User.username, func.coalesce(receipts.c.count, 0)).join(User, User.id == Order.owner_id)
                      .outerjoin(receipts, receipts.c.order_id == Order.id).where(*filters)
                      .order_by(Order.created_at.desc(), Order.id).offset(offset).limit(50)).all()
    return {"total": db.scalar(select(func.count()).select_from(Order).join(User, User.id == Order.owner_id).where(*filters)),
            "items": [{**order_result(row), "checkoutUrl": None, "username": username,
                       "verifiedPayments": count} for row, username, count in rows]}


@router.put("/admin/catalog")
def save_product(data: ProductInput, user=Depends(admin), db=Depends(get_db)):
    lock_owner(db, user.id)
    if (data.kind == "credits" and not data.credits) or (data.kind == "member" and not data.memberDays):
        raise Problem("PRODUCT_ENTITLEMENT_REQUIRED", "商品缺少对应权益")
    record = audit(db, user.id, "catalog.save", data.sku, key(user, "catalog", data.requestKey), data.model_dump(exclude={"requestKey"}))
    if record._replayed:
        row = db.scalar(select(Product).where(Product.sku == data.sku))
        return {"id": row.id, "saved": True, "replayed": True}
    row = db.scalar(select(Product).where(Product.sku == data.sku).with_for_update())
    if not row:
        row = Product(sku=data.sku)
        db.add(row)
    for field, attr in (("title", "title"), ("kind", "kind"), ("amountMinor", "amount_minor"), ("currency", "currency"),
        ("credits", "credits"), ("memberDays", "member_days"), ("dailyCredits", "daily_credits"), ("active", "active")):
        setattr(row, attr, getattr(data, field))
    db.commit()
    return {"id": row.id, "saved": True}


@router.get("/admin/policy")
def policy(user=Depends(admin), db=Depends(get_db)):
    return {"beta": config(db, "beta", {"inviteRequired": False}), "billing": config(db, "billing", {"enabled": False, "prices": {}, "version": 1}),
            "growth": config(db, "growth", {"signinCredits": 0}), "payment": payment_options(), "storageDriver": settings.product_storage_driver}


@router.put("/admin/policy")
def update_policy(data: PolicyInput, user=Depends(admin), db=Depends(get_db)):
    lock_owner(db, user.id)
    if any(not re.fullmatch(r"[A-Za-z0-9_:-]{1,60}", op) or isinstance(cost, bool) or not 0 <= cost <= 100000 for op, cost in data.prices.items()):
        raise Problem("PRICING_INVALID", "积分定价无效")
    record = audit(db, user.id, "policy.update", None, key(user, "policy", data.requestKey), data.model_dump(exclude={"requestKey"}))
    if record._replayed:
        return {"updated": True, "replayed": True}
    prior = config(db, "billing")
    for name, value in (("beta", {"inviteRequired": data.inviteRequired}), ("billing", {"enabled": data.billingEnabled,
        "prices": data.prices, "version": int(prior.get("version", 0)) + 1}), ("growth", {"signinCredits": data.signinCredits})):
        row = db.get(Config, name)
        if row:
            row.value = value
        else:
            db.add(Config(key=name, value=value))
    db.commit()
    return {"updated": True}


@router.get("/admin/overview")
def dashboard(user=Depends(admin)):
    from .reporting import overview
    with SessionLocal() as report_db:
        report_db.execute(text('SET TRANSACTION READ ONLY'))
        return overview(report_db)


@router.get("/admin/tasks")
def tasks(user=Depends(admin), db=Depends(get_db)):
    rows = list(db.scalars(select(Task).order_by(Task.created_at.desc()).limit(100)))
    return {"items": [{"id": t.id, "ownerId": t.owner_id, "type": t.task_type, "state": t.state, "providerState": t.provider_state,
        "errorCode": t.error_code, "persistenceState": t.persistence_state, "createdAt": t.created_at.isoformat(),
        "calls": [{"id": u.provider_call_id, "operation": u.operation, "status": u.status, "usageState": u.usage_state,
                  "inputTokens": u.input_tokens, "outputTokens": u.output_tokens} for u in db.scalars(select(UsageRecord).where(UsageRecord.task_id == t.id))]}
        for t in rows], "unknownPolicy": "未知结果需核查原回执，不提供自动重发操作"}


@router.get("/admin/audit")
def audit_list(user=Depends(admin), db=Depends(get_db)):
    return {"items": [{"id": a.id, "actorId": a.actor_id, "action": a.action, "targetId": a.target_id,
        "details": a.details, "createdAt": a.created_at.isoformat()} for a in db.scalars(select(Audit).order_by(Audit.created_at.desc()).limit(100))]}


@router.get('/admin/holds')
def held_credits(user=Depends(admin),db=Depends(get_db)):
    return {'items':[{'id':h.id,'ownerId':h.owner_id,'taskId':h.task_id,'operation':h.operation,
        'reserved':h.reserved,'state':h.state,'createdAt':h.created_at.isoformat()} for h in db.scalars(select(Hold).where(
            Hold.state.in_(('held','unknown'))).order_by(Hold.created_at.desc()).limit(100))]}


@router.post('/admin/holds/{hold_id}/reconcile')
def reconcile_credits(hold_id:str,user=Depends(admin),db=Depends(get_db)):
    from .wallet import settle,settle_scene
    row=db.get(Hold,hold_id)
    if not row:raise Problem('RESOURCE_NOT_FOUND','预留记录不存在',404)
    lock_owner(db,row.owner_id,active=False)
    row=db.scalar(select(Hold).where(Hold.id==hold_id).with_for_update().execution_options(populate_existing=True))
    if row.state in ('held','unknown'):
        if row.business_key.startswith('provider:'):
            usage=db.scalar(select(UsageRecord).where(UsageRecord.owner_id==row.owner_id,UsageRecord.provider_call_id==row.business_key[9:]))
            if usage and usage.status in ('completed','not_sent'):
                settle(db,row.owner_id,row.business_key,0 if usage.status=='not_sent' else row.reserved)
            else:row.state='unknown'
        elif row.business_key.startswith('image:'):
            from ..scenes.models import SceneImage
            scene=db.get(SceneImage,row.business_key[6:])
            paid=bool(scene and scene.owner_id==row.owner_id and scene.status=='ready')
            if scene and not paid:
                from ..scenes.service import saved_receipt
                try:saved_receipt(scene);paid=True
                except Exception:pass
            if scene:settle_scene(db,scene,paid=paid,not_sent=scene.image.get('failureDiagnostic',{}).get('requestSent') is False)
            else:row.state='unknown'
    audit(db,user.id,'credits.reconcile',row.id,key(user,'hold-review',row.id+':'+row.state),{'state':row.state,'settled':row.settled})
    emit(db,row.owner_id,'hold-review:'+row.id+':'+row.state,'fact.credits.reconciled',data={'holdId':row.id,'state':row.state})
    db.commit()
    return {'state':row.state,'settled':row.settled,'verified':row.state in ('settled','released'),
            'message':'已根据现有回执结算' if row.state in ('settled','released') else '暂无已核验回执，继续保留预留积分'}
