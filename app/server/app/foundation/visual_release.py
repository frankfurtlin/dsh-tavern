"""Install an explicitly tested vision release; never alter frozen executions."""
from copy import deepcopy
from sqlalchemy import select,func
from ..models import ModelProfile,Task
from ..errors import Problem
from .models import Config,VisionSettings
from .vision import FIELDS

MARKER='scene400-v17-tested-vision'


def install(db):
    previous=db.get(Config,MARKER)
    if previous:return previous.value
    if db.scalar(select(func.count()).select_from(Task).where(Task.state.notin_(('succeeded','failed','cancelled')))):
        raise Problem('ACTIVE_EXECUTIONS','请等待当前任务完成后更新视觉通道',409)
    defaults=db.get(Config,'platform-models')
    image=defaults.value.get('image') if defaults else None
    if not image or not image.get('key_cipher'):raise Problem('SCENE_NOT_CONFIGURED','平台图像凭据尚未配置',409)
    current=db.get(Config,'platform-vision')
    prior=deepcopy(current.value) if current else {}
    policy={k:prior.get(k) for k in FIELDS}
    policy.update(base_url='https://apihub.agnes-ai.com/v1',model_id='agnes-3.0-flash',
        key_cipher=image['key_cipher'],context_window=1048576,output_parameter='max_tokens',supports_json=True,
        call_budget=max(prior.get('call_budget',0),500),input_budget=max(prior.get('input_budget',0),50000000),
        output_budget=max(prior.get('output_budget',0),8192000),version=prior.get('version',0)+1)
    if current:current.value=policy
    else:db.add(Config(key='platform-vision',value=policy))
    history_row=db.get(Config,'platform-vision-history')
    history=deepcopy(history_row.value) if history_row else {}
    changed=0;personal=0
    for row in db.scalars(select(VisionSettings).with_for_update()):
        old=row.platform_profile_id
        if old:history[row.owner_id]=sorted(set(history.get(row.owner_id,[])+[old]))
        fresh=ModelProfile(owner_id=row.owner_id,name='平台视觉校验 · Agnes 3.0 Flash',key_tail='',**{k:policy[k] for k in FIELDS})
        db.add(fresh);db.flush()
        row.platform_profile_id=fresh.id
        if row.managed or not row.profile_id or row.profile_id==old:
            row.profile_id=fresh.id;row.managed=True;changed+=1
        else:personal+=1
        row.version+=1
    if history_row:history_row.value=history
    else:db.add(Config(key='platform-vision-history',value=history))
    result={'passed':True,'modelId':policy['model_id'],'managedSelectionsUpdated':changed,
        'personalSelectionsPreserved':personal,'textAndImageDefaultsUnchanged':True,'oldProfilesAndReceiptsPreserved':True}
    db.add(Config(key=MARKER,value=result));db.flush()
    return result
