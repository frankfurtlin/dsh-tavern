"""Platform credentials never cross the API; every user keeps separate quotas."""
from sqlalchemy import select, text
from ..models import ModelProfile, UserSettings
from ..errors import Problem
from .models import Config, ModelGrant
from .core import lock_owner, config, audit

KEY = "platform-models"
TEXT_FIELDS = ("base_url", "model_id", "key_cipher", "context_window", "output_parameter",
               "supports_json", "call_budget", "input_budget", "output_budget")


def summary(db):
    from ..model_budget import unlimited_usage
    policy = config(db, KEY)
    model, image = policy.get("text"), policy.get("image")
    return {"version": policy.get("version", 0),"unlimitedUsage":unlimited_usage(),
            "text": {"enabled": bool(model), "modelId": model["model_id"] if model else None,
                     "callBudget": model["call_budget"] if model else 0},
            "image": {"enabled": bool(image), "modelId": "Agnes Image 2.5 Flash" if image else None,
                      "dailyLimit": 0 if unlimited_usage() else image["daily_limit"] if image else None}}


def configure(db, actor, data):
    # One policy lock makes version checks and retries deterministic.
    db.execute(text("SELECT pg_advisory_xact_lock(817320016)"))
    details = data.model_dump()
    receipt = audit(db, actor.id, "models.defaults", None,
                    "models.defaults:" + actor.id + ":" + data.requestKey, details)
    if receipt._replayed:
        return summary(db)
    prior = config(db, KEY)
    if data.expectedVersion != prior.get("version", 0):
        raise Problem("DEFAULT_MODELS_CONFLICT", "默认模型设置已更新，请重新加载", 409)
    policy = {"version": data.expectedVersion + 1, "text": None, "image": None}
    if data.textProfileId:
        model = db.scalar(select(ModelProfile).where(ModelProfile.id == data.textProfileId,
            ModelProfile.owner_id == actor.id).with_for_update())
        if not model or model.status != "active" or not model.key_cipher:
            raise Problem("NO_PROVIDER", "请选择管理员自己的可用文本通道", 409)
        if db.scalar(select(ModelGrant).where(ModelGrant.text_profile_id == model.id)):
            raise Problem("PERSONAL_PROVIDER_REQUIRED", "请选择自行配置的文本通道", 409)
        policy["text"] = {field: getattr(model, field) for field in TEXT_FIELDS}
        policy["text"]["name"] = "平台默认 · " + model.model_id
    if data.imageEnabled:
        if not db.scalar(text("SELECT to_regclass('world.scene_profiles')")):
            raise Problem("WORLD_UNAVAILABLE", "互动世界尚未就绪", 409)
        from ..scenes.models import SceneProfile
        image = db.scalar(select(SceneProfile).where(SceneProfile.owner_id == actor.id).with_for_update())
        if not image or image.status != "active" or not image.key_cipher:
            raise Problem("SCENE_NOT_CONFIGURED", "请先配置管理员的图像通道", 409)
        if db.scalar(select(ModelGrant).where(ModelGrant.image_profile_id == image.id)):
            raise Problem("PERSONAL_PROVIDER_REQUIRED", "请选择自行配置的图像通道", 409)
        policy["image"] = {"key_cipher": image.key_cipher, "daily_limit": image.daily_limit}
    row = db.get(Config, KEY)
    if row:
        row.value = policy
    else:
        db.add(Config(key=KEY, value=policy))
    db.flush()
    return summary(db)


def provision(db, owner):
    from .vision import provision as provision_vision
    policy = config(db, KEY)
    if not (policy.get("text") or policy.get("image")):
        return provision_vision(db,owner)
    # Serialize registration, login, and refresh-driven onboarding for one owner.
    lock_owner(db, owner)
    grant = db.get(ModelGrant, owner)
    changed = False
    if not grant:
        grant = ModelGrant(owner_id=owner, policy_version=policy["version"])
        db.add(grant)
        db.flush()
        changed = True
    model = policy.get("text")
    from .vision import platform_profile_ids
    visual_ids = platform_profile_ids(db, owner)
    usable = list(db.scalars(select(ModelProfile).where(ModelProfile.owner_id == owner,
        ModelProfile.status == "active", ModelProfile.key_cipher.is_not(None)).order_by(ModelProfile.created_at, ModelProfile.id)))
    usable = [p for p in usable if p.key_cipher and p.id not in visual_ids]
    if model and not grant.text_profile_id and not usable:
        profile = ModelProfile(owner_id=owner, name=model["name"], key_tail="", **{field: model[field] for field in TEXT_FIELDS})
        db.add(profile)
        db.flush()
        grant.text_profile_id = profile.id
        usable.append(profile)
        changed = True
    # A visual-only or incomplete personal profile must not strand a beta user.
    # Preserve a usable personal selection and all existing usage reservations.
    preference = db.get(UserSettings, owner)
    if usable and preference and preference.default_profile_id not in {p.id for p in usable}:
        preference.default_profile_id = usable[0].id
        changed = True
    image = policy.get("image")
    if image and not grant.image_profile_id and db.scalar(text("SELECT to_regclass('world.scene_profiles')")):
        from ..scenes.models import SceneProfile
        if not db.scalar(select(SceneProfile.id).where(SceneProfile.owner_id == owner)):
            profile = SceneProfile(owner_id=owner, key_cipher=image["key_cipher"], daily_limit=image["daily_limit"])
            db.add(profile)
            db.flush()
            grant.image_profile_id = profile.id
            changed = True
    vision_changed=provision_vision(db,owner)
    return changed or vision_changed


def managed_text(db, profile):
    from .vision import platform_profile_ids
    if profile.id in platform_profile_ids(db,profile.owner_id):return True
    return bool(db.scalar(select(ModelGrant.owner_id).where(ModelGrant.text_profile_id == profile.id,
                                                           ModelGrant.owner_id == profile.owner_id)))


def managed_image(db, profile):
    return bool(profile and db.scalar(select(ModelGrant.owner_id).where(ModelGrant.image_profile_id == profile.id,
                                                                      ModelGrant.owner_id == profile.owner_id)))
