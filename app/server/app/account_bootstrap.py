"""One authenticated startup response; credentials and provider secrets stay private."""
from sqlalchemy import select

from .config import settings
from .models import ModelProfile, UserSettings


def build(db, user, session):
    from .api_auth import settings_result
    profile = db.scalar(select(ModelProfile).where(
        ModelProfile.owner_id == user.id, ModelProfile.status == "active",
        ModelProfile.key_cipher.isnot(None)).order_by(ModelProfile.created_at, ModelProfile.id))
    ai_mode = settings.ai_mode if settings.ai_mode in ("real", "mock") else "real"
    return {"version": 1,
            "user": {"id": user.id, "username": user.username, "sessionId": session.id,
                     "settings": settings_result(db.get(UserSettings, user.id)), "aiMode": ai_mode},
            "world": {"enabled": False, "ready": False, "releaseStatus": "preview"},
            "defaultModels": {"text": {"enabled": profile is not None}, "image": {"enabled": False}}}
