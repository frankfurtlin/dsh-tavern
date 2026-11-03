from fastapi import APIRouter,Depends
from pydantic import Field
from ..db import get_db
from ..security import current_user
from ..schemas import Strict
from ..models import ModelProfile
from ..domain import owned
from ..errors import Problem
from .models import VisionSettings,Config
from .core import require_admin,lock_owner
from .vision import KEY,FIELDS,provision,result

router=APIRouter(prefix='/product',tags=['vision-model'])

class Selection(Strict):
    expectedVersion:int=Field(ge=0)
    profileId:str|None=None
    usePlatform:bool=False

@router.get('/vision-model')
def read(user=Depends(current_user),db=Depends(get_db)):
    provision(db,user.id);db.commit();return result(db,user.id)

@router.put('/vision-model')
def choose(data:Selection,user=Depends(current_user),db=Depends(get_db)):
    lock_owner(db,user.id);row=db.get(VisionSettings,user.id)
    if (row.version if row else 0)!=data.expectedVersion:raise Problem('VERSION_CONFLICT','视觉校验模型已更新，请重新读取',409)
    identity=row.platform_profile_id if data.usePlatform and row else data.profileId
    if not identity:raise Problem('SCENE_VISION_NOT_CONFIGURED','请选择自己可用的视觉模型',409)
    profile=owned(db,ModelProfile,identity,user.id)
    if profile.status!='active' or not profile.key_cipher:raise Problem('NO_PROVIDER','视觉通道当前不可用',409)
    if not row:row=VisionSettings(owner_id=user.id,profile_id=identity,managed=False);db.add(row)
    else:row.profile_id=identity;row.managed=identity==row.platform_profile_id;row.version+=1
    db.commit();return result(db,user.id)

@router.get('/admin/vision-model')
def admin_read(user=Depends(current_user),db=Depends(get_db)):
    require_admin(db,user);row=db.get(Config,KEY)
    return {'version':row.value.get('version',1) if row else 0,'enabled':bool(row),'modelId':row.value['model_id'] if row else None}

@router.put('/admin/vision-model')
def admin_choose(data:Selection,user=Depends(current_user),db=Depends(get_db)):
    from sqlalchemy import text
    require_admin(db,user);db.execute(text('SELECT pg_advisory_xact_lock(817320019)'));row=db.get(Config,KEY)
    if (row.value.get('version',1) if row else 0)!=data.expectedVersion:raise Problem('VERSION_CONFLICT','平台视觉设置已变化，请重新读取',409)
    if not data.profileId:raise Problem('NO_PROVIDER','请选择管理员自己的视觉模型通道',409)
    profile=owned(db,ModelProfile,data.profileId,user.id)
    from .default_models import managed_text
    if managed_text(db,profile) or profile.status!='active' or not profile.key_cipher:raise Problem('PERSONAL_PROVIDER_REQUIRED','请选择管理员自行配置的可用视觉模型',409)
    value={k:getattr(profile,k) for k in FIELDS};value['version']=data.expectedVersion+1
    if row:row.value=value
    else:db.add(Config(key=KEY,value=value))
    db.commit();return {'version':value['version'],'enabled':True,'modelId':profile.model_id}
