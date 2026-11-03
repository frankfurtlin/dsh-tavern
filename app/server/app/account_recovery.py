import hmac
import secrets
from sqlalchemy import select
from .models import User, DeviceSession, now
from .foundation.models import RecoveryCode
from .foundation.core import hash_code, lock_owner
from .security import password_hasher
from .errors import Problem


def create_code(db, owner):
    lock_owner(db, owner)
    secret = secrets.token_urlsafe(32)
    row = db.get(RecoveryCode, owner)
    if row:
        row.code_hash, row.created_at, row.used_at = hash_code(secret), now(), None
    else:
        db.add(RecoveryCode(owner_id=owner, code_hash=hash_code(secret)))
    return secret


def reset_password(db, username, code, password):
    user = db.scalar(select(User).where(User.username == username, User.status == "active").with_for_update())
    row = db.get(RecoveryCode, user.id) if user else None
    expected = row.code_hash if row and row.code_hash else "0" * 64
    matches = hmac.compare_digest(expected, hash_code(code))
    if not user or not row or row.used_at or not matches:
        raise Problem("RECOVERY_FAILED", "用户名或恢复码不正确，或恢复码已使用", 400)
    user.password_hash = password_hasher.hash(password)
    row.code_hash, row.used_at = None, now()
    for session in db.scalars(select(DeviceSession).where(DeviceSession.owner_id == user.id)):
        session.revoked_at = now()
    db.commit()
