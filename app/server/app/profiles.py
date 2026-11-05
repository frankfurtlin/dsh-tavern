"""Model-profile selection shared by the native tavern and card assist.

The old story stack had a large story_flow module; the native app only needs
"which text profile should this owner use by default".
"""
from sqlalchemy import select

from .domain import owned
from .errors import Problem
from .models import ModelProfile, UserSettings


def effective_default(rows, preferred, *, excluded=()):
    active = [p for p in rows if p.status == "active" and p.key_cipher and p.id not in excluded]
    return next((p.id for p in active if p.id == preferred), None) or (active[0].id if active else None)


def default_profile(db, owner):
    rows = list(db.scalars(select(ModelProfile).where(ModelProfile.owner_id == owner)
                           .order_by(ModelProfile.created_at, ModelProfile.id)))
    preference = db.get(UserSettings, owner)
    selected = effective_default(rows, preference.default_profile_id if preference else None)
    if not selected:
        raise Problem("NO_PROVIDER", "平台文本模型暂不可用，请稍后重试或联系管理员")
    return owned(db, ModelProfile, selected, owner)
