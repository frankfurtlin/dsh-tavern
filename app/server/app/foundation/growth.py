from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from ..models import now
from ..errors import Problem
from .core import hash_code, lock_owner, config
from .models import Invite, InviteClaim, Membership, Ledger
from .wallet import grant
from .events import emit


def admission(db, code):
    required = config(db, "beta", {"inviteRequired": False}).get("inviteRequired", False)
    if not code and not required:
        return None
    row = db.scalar(select(Invite).where(Invite.code_hash == hash_code(code or "")).with_for_update())
    if not row or row.disabled or row.expires_at <= now() or row.uses >= row.max_uses or (required and not row.admission):
        raise Problem("INVITE_UNAVAILABLE", "邀请码无效、已过期或名额已用完", 403)
    return row


def claim_invite(db, owner, row):
    existing = db.scalar(select(InviteClaim).where(InviteClaim.owner_id == owner))
    if existing:
        if existing.invite_id != row.id:
            raise Problem("INVITE_ALREADY_CLAIMED", "此账号已使用其他邀请码", 409)
        return existing
    lock_owner(db, owner)
    row.uses += 1
    claim = InviteClaim(owner_id=owner, invite_id=row.id)
    db.add(claim)
    if row.reward:
        grant(db, owner, row.reward, "invite:" + owner, reason="beta_invite")
    emit(db, owner, "invite:" + owner, "fact.invite.claimed", data={"inviteId": row.id})
    return claim


def daily_claim(db, owner):
    lock_owner(db, owner)
    policy = config(db, "growth", {"signinCredits": 0})
    active = list(db.scalars(select(Membership).where(Membership.owner_id == owner, Membership.starts_at <= now(), Membership.ends_at > now())))
    amount = max([int(policy.get("signinCredits", 0))] + [m.daily_credits for m in active])
    day = now().astimezone(ZoneInfo("Asia/Shanghai")).date()
    if not amount:
        return {"claimed": False, "amount": 0, "reason": "暂无可领取的每日积分"}
    end = datetime.combine(day + timedelta(days=1), time.min, ZoneInfo("Asia/Shanghai"))
    key = "daily:" + owner + ":" + day.isoformat()
    existing = db.scalar(select(Ledger).where(Ledger.entry_key == "grant:" + key))
    grant(db, owner, amount if not existing else existing.amount, key, bucket="DAILY", expires=end, reason="daily")
    return {"claimed": True, "amount": amount if not existing else existing.amount, "alreadyClaimed": bool(existing), "expiresAt": end.isoformat()}
