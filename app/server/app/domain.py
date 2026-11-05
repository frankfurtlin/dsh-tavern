"""Small shared domain helpers: hashing, ownership checks, idempotency."""
import hashlib
import json
from datetime import timedelta

from sqlalchemy import select, text

from .errors import Problem
from .models import Idempotency, now


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def owned(db, cls, object_id, owner_id, lock=False):
    query = select(cls).where(cls.id == object_id, cls.owner_id == owner_id)
    if lock:
        query = query.with_for_update()
    value = db.scalar(query)
    if not value:
        raise Problem("RESOURCE_NOT_FOUND", "资源不存在", 404)
    return value


def idempotent(db, owner_id, scope, key, payload, action):
    if not key or len(key) > 160:
        raise Problem("IDEMPOTENCY_KEY_REQUIRED", "请求缺少有效幂等键")
    lock_id = int(digest(owner_id + scope + key)[:15], 16)
    db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock_id})
    hashed = digest(canonical(payload))
    existing = db.scalar(select(Idempotency).where(
        Idempotency.owner_id == owner_id, Idempotency.scope == scope, Idempotency.key == key))
    if existing:
        if existing.payload_hash != hashed:
            raise Problem("IDEMPOTENCY_CONFLICT", "同一请求标识对应了不同内容", 409)
        return existing.result
    result = action()
    db.add(Idempotency(owner_id=owner_id, scope=scope, key=key, payload_hash=hashed,
                       result=result, expires_at=now() + timedelta(days=7)))
    db.commit()
    return result
