"""Integer credits; owner locks serialize grants, holds and settlement in one transaction."""
from datetime import timedelta
from sqlalchemy import select, or_
from ..errors import Problem
from ..models import now
from .core import lock_owner, config
from .models import CreditLot, Ledger, Hold, Membership


def balances(db, owner):
    buckets = {name: {"available": 0, "frozen": 0} for name in ("DAILY", "TEMPORARY", "PERMANENT")}
    for lot in db.scalars(select(CreditLot).where(CreditLot.owner_id == owner)):
        bucket = buckets[lot.bucket]
        # Expired frozen credits remain reserved, never silently released.
        bucket["frozen"] += lot.frozen
        if lot.expires_at is None or lot.expires_at > now():
            bucket["available"] += lot.remaining - lot.frozen
    return {"buckets": buckets, "available": sum(v["available"] for v in buckets.values()),
            "frozen": sum(v["frozen"] for v in buckets.values())}


def grant(db, owner, amount, key, *, bucket="PERMANENT", expires=None, reason="grant"):
    lock_owner(db, owner)
    existing = db.scalar(select(CreditLot).where(CreditLot.grant_key == key))
    if existing:
        if (existing.owner_id, existing.amount, existing.bucket) != (owner, amount, bucket):
            raise Problem("IDEMPOTENCY_CONFLICT", "积分发放编号冲突", 409)
        return existing
    if amount <= 0 or amount > 100000000 or bucket not in ("DAILY", "TEMPORARY", "PERMANENT"):
        raise Problem("INVALID_CREDIT_GRANT", "积分发放参数无效")
    row = CreditLot(owner_id=owner, bucket=bucket, amount=amount, remaining=amount, grant_key=key, expires_at=expires)
    db.add(row)
    db.add(Ledger(owner_id=owner, entry_key="grant:" + key, kind="grant", amount=amount, reference=reason))
    db.flush()
    return row


def hold(db, owner, business_key, task_id, operation, amount, price):
    lock_owner(db, owner)
    existing = db.scalar(select(Hold).where(Hold.owner_id == owner, Hold.business_key == business_key))
    if existing:
        if (existing.task_id, existing.operation, existing.reserved, existing.price_snapshot) != (task_id, operation, amount, price):
            raise Problem("IDEMPOTENCY_CONFLICT", "积分预留编号冲突", 409)
        return existing
    if amount < 0:
        raise Problem("INVALID_CREDIT_HOLD", "积分预留参数无效")
    lots = list(db.scalars(select(CreditLot).where(CreditLot.owner_id == owner,
        or_(CreditLot.expires_at.is_(None), CreditLot.expires_at > now()),
        CreditLot.remaining > CreditLot.frozen).order_by(CreditLot.expires_at.asc().nulls_last(), CreditLot.created_at, CreditLot.id).with_for_update()))
    if sum(l.remaining - l.frozen for l in lots) < amount:
        raise Problem("INSUFFICIENT_CREDITS", "积分不足，请先领取内测积分或充值", 402)
    allocations, remaining = [], amount
    for lot in lots:
        take = min(remaining, lot.remaining - lot.frozen)
        if take:
            lot.frozen += take
            allocations.append({"lotId": lot.id, "amount": take})
            remaining -= take
        if not remaining:
            break
    row = Hold(owner_id=owner, business_key=business_key, task_id=task_id, operation=operation,
               reserved=amount, allocations=allocations, price_snapshot=price)
    db.add(row)
    db.flush()
    db.add(Ledger(owner_id=owner, entry_key="hold:" + row.id, kind="hold", amount=-amount, reference=row.id))
    return row


def settle(db, owner, business_key, amount, *, unknown=False):
    lock_owner(db, owner, active=False)
    row = db.scalar(select(Hold).where(Hold.owner_id == owner, Hold.business_key == business_key).with_for_update())
    if not row:
        return None  # Charging was disabled when this operation began.
    if row.state in ("settled", "released"):
        if not unknown and row.settled != amount:
            raise Problem("SETTLEMENT_CONFLICT", "此调用已按另一金额结算", 409)
        return row
    if unknown:
        row.state = "unknown"
        return row
    if amount < 0 or amount > row.reserved:
        raise Problem("INVALID_SETTLEMENT", "结算金额超出预留金额")
    to_charge = amount
    for item in row.allocations:
        lot = db.get(CreditLot, item["lotId"])
        consumed = min(to_charge, item["amount"])
        lot.frozen -= item["amount"]
        lot.remaining -= consumed
        to_charge -= consumed
    row.settled, row.state = amount, "settled" if amount else "released"
    db.add(Ledger(owner_id=owner, entry_key="settle:" + row.id, kind="settle", amount=-amount,
                  reference=row.id, details={"released": row.reserved - amount}))
    db.flush()
    return row


def grant_membership(db, owner, days, key, title="内测会员", daily_credits=0, source="admin"):
    lock_owner(db, owner)
    existing = db.scalar(select(Membership).where(Membership.grant_key == key))
    if existing:
        if (existing.owner_id, existing.title, existing.daily_credits, existing.source) != (owner, title, daily_credits, source) or existing.ends_at - existing.starts_at != timedelta(days=days):
            raise Problem('IDEMPOTENCY_CONFLICT','会员权益发放编号冲突',409)
        return existing
    if not 1 <= days <= 3660:
        raise Problem("INVALID_MEMBERSHIP", "会员天数无效")
    latest = db.scalar(select(Membership).where(Membership.owner_id == owner).order_by(Membership.ends_at.desc()))
    start = max(now(), latest.ends_at) if latest else now()
    row = Membership(owner_id=owner, grant_key=key, title=title, starts_at=start,
                     ends_at=start + timedelta(days=days), daily_credits=daily_credits, source=source)
    db.add(row)
    db.flush()
    return row


def reserve_usage(db, usage):
    from ..model_budget import unlimited_usage
    if unlimited_usage():return
    policy = config(db, "billing", {"enabled": False, "prices": {}})
    prices = policy.get("prices", {})
    amount = prices.get(usage.operation, prices.get(usage.operation.split(':')[0], prices.get('_default', 0)))
    if policy.get("enabled") and amount:
        hold(db, usage.owner_id, "provider:" + usage.provider_call_id, usage.task_id, usage.operation, amount,
             {"credits": amount, "operation": usage.operation, "policyVersion": policy.get("version", 1), "pricing": "fixed-per-confirmed-call"})


def settle_usage_credits(db, usage):
    row = db.scalar(select(Hold).where(Hold.owner_id == usage.owner_id, Hold.business_key == "provider:" + usage.provider_call_id))
    if row:
        settle(db, usage.owner_id, row.business_key, 0 if usage.status == "not_sent" else row.reserved,
               unknown=usage.status not in ("completed", "not_sent"))


def reserve_scene(db, row):
    from ..model_budget import unlimited_usage
    if unlimited_usage():return
    policy = config(db, 'billing', {'enabled':False,'prices':{}})
    amount = policy.get('prices',{}).get('scene_image', 0)
    if policy.get('enabled') and amount:
        hold(db,row.owner_id,'image:'+row.id,row.id,'scene_image',amount,
             {'credits':amount,'policyVersion':policy.get('version',1),'pricing':'fixed-per-confirmed-image'})


def settle_scene(db, row, *, paid=False, not_sent=False):
    existing = db.scalar(select(Hold).where(Hold.owner_id==row.owner_id,Hold.business_key=='image:'+row.id))
    if existing and existing.state not in ('settled','released'):
        settle(db,row.owner_id,existing.business_key,existing.reserved if paid else 0,unknown=not paid and not not_sent)
