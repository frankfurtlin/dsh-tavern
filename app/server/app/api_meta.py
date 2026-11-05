"""Small public/account meta endpoints the native client still calls."""
from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select

from .config import settings
from .db import get_db
from .errors import Problem
from .models import ModelProfile, UserSettings
from .providers import encrypt_key
from .schemas import Strict
from .security import current_user

router = APIRouter()
world_router = APIRouter()


class ProfileInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    baseUrl: str = Field(min_length=1, max_length=300)
    modelId: str = Field(min_length=1, max_length=100)
    key: str = Field(min_length=1, max_length=400)
    contextWindow: int = Field(ge=1024, le=2_000_000)
    callBudget: int = Field(default=100, ge=0)
    inputBudget: int = Field(default=1_000_000, ge=0)
    outputBudget: int = Field(default=100_000, ge=0)
    outputParameter: str = "max_tokens"
    supportsJson: bool = False


def profile_out(profile):
    return {"id": profile.id, "name": profile.name, "baseUrl": profile.base_url,
            "modelId": profile.model_id, "keyTail": profile.key_tail,
            "contextWindow": profile.context_window, "status": profile.status}


@router.get("/product/public/config")
def public_config():
    return {"inviteRequired": False, "registrationEnabled": settings.registration_enabled,
            "defaultModels": {"text": {"enabled": False}, "image": {"enabled": False}}}


@router.post("/product/telemetry/track")
@router.post("/product/telemetry/public")
def telemetry(payload: dict | None = None, user=Depends(current_user)):
    return {"accepted": True}


@router.get("/model-profiles")
def list_profiles(user=Depends(current_user), db=Depends(get_db)):
    rows = list(db.scalars(select(ModelProfile).where(ModelProfile.owner_id == user.id)
                           .order_by(ModelProfile.created_at, ModelProfile.id)))
    preference = db.get(UserSettings, user.id)
    default_id = preference.default_profile_id if preference else None
    if default_id and not any(row.id == default_id for row in rows):
        default_id = None
    if default_id is None:
        active = [row for row in rows if row.status == "active" and row.key_cipher]
        default_id = active[0].id if active else None
        if preference and default_id:
            preference.default_profile_id = default_id
            db.commit()
    return {"items": [profile_out(row) for row in rows], "defaultProfileId": default_id,
            "allowedHosts": [], "aiMode": settings.ai_mode}


@router.post("/model-profiles", status_code=201)
def create_profile(payload: ProfileInput, user=Depends(current_user), db=Depends(get_db)):
    profile = ModelProfile(owner_id=user.id, name=payload.name, base_url=payload.baseUrl,
        model_id=payload.modelId, key_cipher=encrypt_key(payload.key),
        key_tail=payload.key[-4:], context_window=payload.contextWindow,
        output_parameter=payload.outputParameter, supports_json=payload.supportsJson,
        call_budget=payload.callBudget, input_budget=payload.inputBudget,
        output_budget=payload.outputBudget)
    db.add(profile)
    db.flush()
    preference = db.get(UserSettings, user.id)
    if preference is not None and not preference.default_profile_id:
        preference.default_profile_id = profile.id
    db.commit()
    return profile_out(profile)


@router.put("/model-profiles/{profile_id}/default")
def set_default(profile_id: str, user=Depends(current_user), db=Depends(get_db)):
    profile = db.scalar(select(ModelProfile).where(ModelProfile.id == profile_id,
                                                   ModelProfile.owner_id == user.id))
    if not profile:
        raise Problem("RESOURCE_NOT_FOUND", "模型配置不存在", 404)
    preference = db.get(UserSettings, user.id)
    if preference is None:
        raise Problem("RESOURCE_NOT_FOUND", "账号设置不存在", 404)
    preference.default_profile_id = profile.id
    db.commit()
    return {"defaultProfileId": profile.id}


@world_router.get("/config")
def world_config():
    return {"enabled": False, "ready": False}
