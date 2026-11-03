import hashlib
import json
from sqlalchemy import select
from ..errors import Problem
from ..models import User, now
from .models import Account, Audit, Config


def hash_code(value):
    return hashlib.sha256(value.strip().encode()).hexdigest()


def config(db, key, default=None):
    row = db.get(Config, key)
    return row.value if row else (default or {})


def lock_owner(db, owner, *, active=True):
    user = db.scalar(select(User).where(User.id == owner).with_for_update())
    if not user or (active and user.status != "active"):
        raise Problem("ACCOUNT_UNAVAILABLE", "账号不可用", 403)
    return user


def account(db, user):
    row = db.get(Account, user.id)
    if not row:
        lock_owner(db, user.id, active=False)
        row = db.get(Account, user.id)
        if not row:
            row = Account(owner_id=user.id)
            db.add(row)
            db.flush()
    return row


def require_admin(db, user):
    row = db.get(Account, user.id)
    if not row or row.role != "ADMIN":
        raise Problem("ADMIN_REQUIRED", "此操作需要管理员权限", 403)
    return row


def audit(db, actor, action, target, key, details):
    existing = db.scalar(select(Audit).where(Audit.request_key == key))
    if existing:
        if existing.actor_id != actor or existing.action != action or existing.target_id != target or existing.details != details:
            raise Problem("IDEMPOTENCY_CONFLICT", "此操作编号已用于其他请求", 409)
        existing._replayed = True
        return existing
    row = Audit(actor_id=actor, action=action, target_id=target, request_key=key, details=details)
    db.add(row)
    db.flush()
    row._replayed = False
    return row


def safe_owned(db, model, identity, owner):
    row = db.get(model, identity)
    if not row or row.owner_id != owner:
        raise Problem("RESOURCE_NOT_FOUND", "内容不存在或无访问权限", 404)
    return row


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
