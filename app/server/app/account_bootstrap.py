"""One authenticated startup response; credentials and provider secrets stay private."""
from .config import settings
from .models import UserSettings


def build(db, user, session):
    from .api_auth import settings_result
    from .foundation.default_models import provision, summary
    from .world.migrate import ready
    provision(db, user.id)
    return {"version": 1,
            "user": {"id": user.id, "username": user.username, "sessionId": session.id,
                     "settings": settings_result(db.get(UserSettings, user.id)), "aiMode": settings.ai_mode},
            "world": {"enabled": settings.interactive_world_enabled,
                      "ready": settings.interactive_world_enabled and ready(db), "releaseStatus": "preview"},
            "defaultModels": summary(db)}
