"""Explicit vision settings and private grants, with independent usage budgets."""
from sqlalchemy import select
from ..models import ModelProfile
from ..config import settings
from ..errors import Problem
from .models import VisionSettings,Config
from .core import lock_owner

KEY='platform-vision'
FIELDS=('base_url','model_id','key_cipher','context_window','output_parameter','supports_json','call_budget','input_budget','output_budget')


def platform_profile_ids(db,owner):
    """Retired grants keep their original usage and their visual-only purpose."""
    row=db.get(VisionSettings,owner)
    history=db.get(Config,'platform-vision-history')
    ids=set(history.value.get(owner,[])) if history else set()
    if row and row.platform_profile_id:ids.add(row.platform_profile_id)
    return ids


def provision(db,owner):
    policy=db.get(Config,KEY)
    if not policy:return False
    lock_owner(db,owner)
    row=db.get(VisionSettings,owner)
    if row and row.platform_profile_id:
        selected_profile=db.get(ModelProfile,row.profile_id) if row.profile_id else None
        fallback=db.get(ModelProfile,row.platform_profile_id)
        if (not selected_profile or selected_profile.status!='active' or not selected_profile.key_cipher) and fallback and fallback.owner_id==owner and fallback.status=='active' and fallback.key_cipher:
            row.profile_id=fallback.id;row.managed=True;row.version+=1
            return True
        return False
    profile=ModelProfile(owner_id=owner,name='平台视觉校验 · '+policy.value['model_id'],key_tail='',**{k:policy.value[k] for k in FIELDS})
    db.add(profile);db.flush()
    if row:
        row.platform_profile_id=profile.id
        if not row.profile_id:row.profile_id=profile.id;row.managed=True
        row.version+=1
    else:db.add(VisionSettings(owner_id=owner,profile_id=profile.id,platform_profile_id=profile.id,managed=True))
    return True


def selected(db,owner):
    provision(db,owner)
    row=db.get(VisionSettings,owner)
    profile=db.get(ModelProfile,row.profile_id) if row and row.profile_id else None
    if profile and profile.owner_id==owner and profile.status=='active' and profile.key_cipher:return profile
    if settings.ai_mode=='mock':
        from ..story_flow import default_profile
        return default_profile(db,owner)
    raise Problem('SCENE_VISION_NOT_CONFIGURED','平台画面校验暂不可用，请稍后重试或联系管理员；已生成图片会保留',409)


def result(db,owner):
    row=db.get(VisionSettings,owner);profile=db.get(ModelProfile,row.profile_id) if row and row.profile_id else None
    return {'version':row.version if row else 0,'profileId':profile.id if profile else None,'modelId':profile.model_id if profile else None,
        'enabled':bool(profile and profile.status=='active' and profile.key_cipher),'source':'platform' if row and row.managed else 'personal',
        'platformAvailable':bool(row and row.platform_profile_id)}
