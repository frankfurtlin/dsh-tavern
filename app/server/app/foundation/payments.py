"""Real gateway adapters. No browser parameter can confirm payment."""
import base64
import hashlib
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy import select
from ..config import settings
from ..errors import Problem
from ..models import now
from .core import canonical_hash, lock_owner
from .events import emit
from .models import Order, Product, PaymentReceipt, Membership
from .wallet import grant, grant_membership


def sign_content(fields):
    return "&".join(k + "=" + str(fields[k]) for k in sorted(fields)
                    if k not in ("sign", "sign_type") and fields[k] is not None and str(fields[k]).strip()).encode()


def rsa_sign(fields, key):
    private = serialization.load_pem_private_key(key.replace("\\n", "\n").encode(), password=None)
    return base64.b64encode(private.sign(sign_content(fields), padding.PKCS1v15(), hashes.SHA256())).decode()


def rsa_verify(fields, key):
    try:
        public = serialization.load_pem_public_key(key.replace("\\n", "\n").encode())
        public.verify(base64.b64decode(fields["sign"], validate=True), sign_content(fields), padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def minor(value):
    if not re.fullmatch(r"\d{1,8}(?:\.\d{1,2})?", str(value or "")):
        raise Problem("PAYMENT_AMOUNT_INVALID", "收款金额无效", 400)
    try:
        return int(Decimal(str(value)) * 100)
    except (ValueError, InvalidOperation):
        raise Problem("PAYMENT_AMOUNT_INVALID", "收款金额无效", 400)


def payment_options():
    provider = settings.product_payment_provider
    configured = provider == "i6ls" and all((settings.product_i6ls_base_url, settings.product_i6ls_merchant,
                   settings.product_i6ls_private_key, settings.product_i6ls_public_key, settings.product_public_origin)) or (
                   provider == "waffo" and len(settings.product_waffo_service_token) >= 32 and bool(settings.product_public_origin))
    return {"enabled": bool(configured), "provider": provider, "environment": settings.product_payment_environment,
            "reason": "" if configured else "收款通道尚未配置，可使用内测兑换码或管理员发放权益"}


def waffo(path, data=None, raw=None, signature=None):
    url = settings.product_waffo_service_url.rstrip("/")
    parsed = urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1", "payment-waffo") or parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise Problem("PAYMENT_CONFIGURATION_INVALID", "支付适配器地址未受信任", 503)
    headers = {"Authorization": "Bearer " + settings.product_waffo_service_token}
    if signature:
        headers["X-Waffo-Signature"] = signature
    with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
        response = client.get(url + path, headers=headers) if data is None and raw is None else client.post(
            url + path, headers=headers, json=data if raw is None else None, content=raw)
    if not response.is_success:
        raise Problem("PAYMENT_VERIFICATION_FAILED" if raw is not None else "PAYMENT_PROVIDER_UNAVAILABLE", "支付通道暂不可用，请查询原订单状态", 503)
    return response.json()


def snapshot(product):
    return {"sku": product.sku, "title": product.title, "kind": product.kind, "amountMinor": product.amount_minor,
            "currency": product.currency, "credits": product.credits, "memberDays": product.member_days, "dailyCredits": product.daily_credits}


def order_result(row):
    return {"id": row.id, "product": row.snapshot, "state": row.state, "provider": row.provider,
            "environment": row.environment, "checkoutUrl": row.checkout_url,
            "createdAt": row.created_at.isoformat(), "paidAt": row.paid_at.isoformat() if row.paid_at else None,
            "expiresAt": row.expires_at.isoformat()}


def new_order(db, user, product_id, key):
    lock_owner(db, user.id)
    prior = db.scalar(select(Order).where(Order.owner_id == user.id, Order.request_key == key))
    if prior:
        if prior.product_id != product_id:
            raise Problem("IDEMPOTENCY_CONFLICT", "此订单编号已用于另一商品", 409)
        return prior
    options = payment_options()
    if not options["enabled"]:
        raise Problem("PAYMENT_NOT_CONFIGURED", options["reason"], 503)
    product = db.get(Product, product_id)
    if not product or not product.active:
        raise Problem("PRODUCT_UNAVAILABLE", "商品已下架", 404)
    if options["provider"] == "waffo":
        remote = waffo("/options")
        recurring = product.kind == "member"
        expected = remote.get("subscriptionAmount" if recurring else "amount")
        if not remote.get("ready") or remote.get("currency") != product.currency or minor(expected) != product.amount_minor or remote.get("environment") != options["environment"]:
            raise Problem("PAYMENT_PRODUCT_MISMATCH", "商品与支付通道的定价或环境不一致", 409)
        merchant = remote.get("storeId")
    else:
        if product.currency != "CNY":
            raise Problem("PAYMENT_CURRENCY_UNSUPPORTED", "此收款通道仅支持人民币商品", 409)
        merchant = settings.product_i6ls_merchant
    row = Order(owner_id=user.id, product_id=product.id, request_key=key, snapshot=snapshot(product),
                provider=options["provider"], environment=options["environment"], merchant=merchant,
                expires_at=now() + timedelta(minutes=45))
    db.add(row)
    db.flush()
    emit(db, user.id, "order:" + row.id, "fact.order.created", data={"orderId": row.id, "sku": product.sku})
    return row


def checkout(db, row, client_ip="127.0.0.1"):
    if row.state == "paid" or row.checkout_url:
        return row
    if row.expires_at <= now() or row.state in ("review", "checkout_unknown"):
        raise Problem("ORDER_RECONCILIATION_REQUIRED", "请先核查原订单，避免重复创建支付", 409)
    try:
        origin = settings.product_public_origin.rstrip("/")
        if urlparse(origin).scheme != "https":
            raise Problem("PAYMENT_CONFIGURATION_INVALID", "收款回调需要已配置的 HTTPS 站点", 503)
        if row.provider == "waffo":
            result = waffo("/checkout", {"orderNo": "WF_" + row.id,
                "purchaseMode": "SUBSCRIPTION" if row.snapshot["kind"] == "member" else "ONETIME",
                "buyerIdentity": "foundation_" + hashlib.sha256(row.owner_id.encode()).hexdigest()})
            url = result.get("checkoutUrl", "")
        else:
            fields = {"pid": row.merchant, "method": "jump", "type": "alipay", "out_trade_no": row.id,
                "notify_url": origin + "/api/reader/v1/product/payments/i6ls/notify", "return_url": origin + "/subscription",
                "name": row.snapshot["title"], "money": f'{row.snapshot["amountMinor"] / 100:.2f}', "clientip": client_ip,
                "timestamp": str(int(now().timestamp()))}
            fields["sign"] = rsa_sign(fields, settings.product_i6ls_private_key)
            fields["sign_type"] = "RSA"
            base = settings.product_i6ls_base_url.rstrip("/")
            if urlparse(base).scheme != "https" or urlparse(base).hostname not in settings.product_payment_allowed_hosts.split(","):
                raise Problem("PAYMENT_CONFIGURATION_INVALID", "收款地址未在允许列表中", 503)
            with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
                result = client.post(base + "/api/pay/create", data=fields).json()
            if str(result.get("code")) != "0" or not rsa_verify(result, settings.product_i6ls_public_key):
                raise Problem("PAYMENT_VERIFICATION_FAILED", "支付建单回执未通过验证", 503)
            url = result.get("pay_info", "")
            row.provider_reference = str(result.get("trade_no", "")) or None
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in settings.product_payment_allowed_hosts.split(",") or parsed.username or parsed.password:
            raise Problem("PAYMENT_REDIRECT_REJECTED", "收银台地址未通过验证", 503)
        row.checkout_url, row.state = url, "checkout_ready"
    except Problem:
        row.state = "checkout_unknown"
        raise
    except Exception:
        row.state = "checkout_unknown"
        raise Problem("PAYMENT_STATUS_UNKNOWN", "支付建单结果未知，请查询原订单后再操作", 503)
    return row


def confirm(db, row, *, provider, environment, merchant, amount, currency, payment_id, event_id, body_hash, period=None):
    # Serialize in the same owner -> order order as checkout/redemption.
    lock_owner(db, row.owner_id, active=False)
    row = db.scalar(select(Order).where(Order.id == row.id).with_for_update().execution_options(populate_existing=True))
    if (provider, environment, merchant, amount, currency) != (row.provider, row.environment, row.merchant,
            row.snapshot["amountMinor"], row.snapshot["currency"]) or not payment_id or not event_id:
        raise Problem("PAYMENT_RECEIPT_MISMATCH", "支付回执与订单不一致", 400)
    receipt_key = provider + ":" + environment + ":" + event_id
    prior = db.scalar(select(PaymentReceipt).where(PaymentReceipt.receipt_key == receipt_key))
    if prior:
        if prior.order_id != row.id or prior.body_hash != body_hash:
            raise Problem("PAYMENT_REPLAY_CONFLICT", "支付事件编号已用于另一回执", 409)
        return row
    entitlement_key = "pay:" + provider + ":" + environment + ":" + payment_id
    paid = db.scalar(select(PaymentReceipt).where(PaymentReceipt.payment_key == entitlement_key))
    if paid:
        if paid.order_id != row.id or paid.details.get('amountMinor') != amount or paid.details.get('currency') != currency or (period and paid.details.get('period') != period):
            raise Problem('PAYMENT_REPLAY_CONFLICT','此支付流水已用于另一订单或权益周期',409)
        return row
    if row.state == "review":
        raise Problem("PAYMENT_REVIEW_REQUIRED", "此订单需要人工核查", 409)
    db.add(PaymentReceipt(receipt_key=receipt_key, payment_key=entitlement_key, order_id=row.id, body_hash=body_hash, verified=True,
           details={"paymentId": payment_id, "amountMinor": amount, "currency": currency, "environment": environment, "period": period}))
    if row.snapshot["credits"]:
        grant(db, row.owner_id, row.snapshot["credits"], entitlement_key + ":credits", reason="verified_payment")
    if row.snapshot["kind"] == "member":
        if provider == "waffo":
            if not period:
                raise Problem("PAYMENT_PERIOD_MISSING", "订阅支付缺少经核验的权益周期", 400)
            try:
                start, end = (datetime.fromisoformat(v.replace("Z", "+00:00")) for v in period)
            except (ValueError, TypeError, AttributeError):
                raise Problem("PAYMENT_PERIOD_INVALID", "订阅权益周期无效", 400)
            if start.tzinfo is None or end.tzinfo is None or not start < end or end - start > timedelta(days=40):
                raise Problem("PAYMENT_PERIOD_INVALID", "订阅权益周期无效", 400)
            existing = db.scalar(select(Membership).where(Membership.grant_key == entitlement_key + ":member"))
            if not existing:
                db.add(Membership(owner_id=row.owner_id, grant_key=entitlement_key + ":member", title=row.snapshot["title"],
                    starts_at=start, ends_at=end, daily_credits=row.snapshot["dailyCredits"], source="waffo"))
        else:
            grant_membership(db, row.owner_id, row.snapshot["memberDays"], entitlement_key + ":member",
                             title=row.snapshot["title"], daily_credits=row.snapshot["dailyCredits"], source="payment")
    row.state, row.paid_at = "paid", row.paid_at or now()
    emit(db, row.owner_id, "paid:" + entitlement_key, "fact.order.paid",
         data={"orderId": row.id, "amountMinor": amount, "currency": currency, "environment": environment})
    db.flush()
    return row


def i6ls_notification(db, fields):
    if settings.product_payment_provider != "i6ls" or not rsa_verify(fields, settings.product_i6ls_public_key):
        raise Problem("PAYMENT_SIGNATURE_INVALID", "支付签名无效", 400)
    row = db.get(Order, fields.get("out_trade_no"))
    if not row:
        raise Problem("RESOURCE_NOT_FOUND", "订单不存在", 404)
    if fields.get("trade_status", "").upper() not in ("TRADE_SUCCESS", "SUCCESS", "PAID", "1"):
        return row
    return confirm(db, row, provider="i6ls", environment=settings.product_payment_environment,
        merchant=fields.get("pid"), amount=minor(fields.get("money")), currency="CNY", payment_id=fields.get("trade_no"),
        event_id=fields.get("trade_no"), body_hash=canonical_hash({k: v for k, v in fields.items() if k not in ("sign", "sign_type")}))


def waffo_event(db, event, *, recovered=None):
    fields = event.get("data", {})
    order_no = fields.get("orderMerchantExternalId", "")
    if not re.fullmatch(r"WF_[a-f0-9]{32}", order_no):
        raise Problem("PAYMENT_RECEIPT_MISMATCH", "支付订单编号无效", 400)
    row = db.get(Order, order_no[3:])
    if not row or row.provider != "waffo" or row.environment != event.get("mode") or row.merchant != event.get("storeId"):
        raise Problem("PAYMENT_RECEIPT_MISMATCH", "支付订单归属不匹配", 400)
    event_type = event.get("eventType")
    if event_type in ("refund.succeeded", "subscription.plan_changed", "subscription.plan_change_scheduled"):
        lock_owner(db, row.owner_id, active=False)
        row.state = "review"
        emit(db, row.owner_id, "review:" + str(event.get("eventId") or event.get("id")), "fact.payment.review_required", data={"orderId": row.id, "reason": event_type})
        return row
    if event_type not in ("order.completed", "subscription.payment_succeeded"):
        return row
    period = None
    if row.snapshot["kind"] == "member":
        previous_payment = db.scalar(select(PaymentReceipt).where(PaymentReceipt.payment_key == 'pay:waffo:' + str(event.get('mode')) + ':' + str(fields.get('paymentId'))))
        if previous_payment:
            period = previous_payment.details.get('period')
        else:
            remote = recovered or waffo("/reconcile", {"orderNo": order_no, "purchaseMode": "SUBSCRIPTION"})
            match = next((e for e in remote.get("events", []) if e.get("eventType") == "subscription.snapshot" and
                          e.get("data", {}).get("periodNumber") == fields.get("periodNumber") and e.get("storeId") == row.merchant), None)
            if match:
                period = [match["data"].get("currentPeriodStart", ""), match["data"].get("currentPeriodEnd", "")]
    return confirm(db, row, provider="waffo", environment=event.get("mode"), merchant=event.get("storeId"),
        amount=minor(fields.get("chargedAmount")), currency=fields.get("currency"), payment_id=fields.get("paymentId"),
        event_id=str(event.get("eventId") or event.get("id") or ""), body_hash=canonical_hash(event), period=period)


def reconcile(db, row):
    if row.provider != "waffo":
        raise Problem("MANUAL_RECONCILIATION_REQUIRED", "此通道需通过签名通知或商户后台核查", 409)
    result = waffo("/reconcile", {"orderNo": "WF_" + row.id, "purchaseMode": "SUBSCRIPTION" if row.snapshot["kind"] == "member" else "ONETIME"})
    for event in result.get("events", []):
        waffo_event(db, event, recovered=result)
    return row
