"""Bounded author sandboxes use separate owned worlds, including on restore.

Reservations are deliberately conservative: failed/unknown calls do not refund
the trial's envelope. Provider accounting continues through normal consents.
"""
from copy import deepcopy
from sqlalchemy import select
from pydantic import Field
from ..domain import owned
from ..errors import Problem
from ..models import Task, uid
from ..world.models import World
from ..world.schemas import BeginWorld
from ..world.service import begin_world
from ..play_controls.service import text_profile
from ..story_flow import stage_budget
from ..completion_policy import input_ceiling,completion_limit
from .models import AuthorDraft
from .service import compile_definition
from ..model_budget import unlimited_usage


class TrialInput(BeginWorld):
    requestId:str=Field(default='',max_length=100)
    authorVersion:int=Field(ge=1,strict=True)
    profileId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    maxGenerations:int=Field(default=5,ge=1,le=20,strict=True)
    maxInputTokens:int=Field(default=2000000,ge=1000,le=10000000,strict=True)
    maxOutputTokens:int=Field(default=500000,ge=1000,le=2000000,strict=True)


def envelope(profile):
    stages=['analysis','analysis','generation','validation']
    value=stage_budget(profile,stages)
    value['inputTokens']=sum(input_ceiling(profile,completion_limit(profile,s,world=True),world=True) for s in stages)
    value['outputTokens']=sum(completion_limit(profile,s,world=True) for s in stages)
    return {k:value[k] for k in ('maxCalls','inputTokens','outputTokens')}


def model_estimate(db,owner,profile_id=None):
    p=text_profile(db,owner,profile_id)
    return {'profileId':p.id,'name':p.name,'modelId':p.model_id,'contextWindow':p.context_window,
            'reservation':envelope(p),'unlimitedUsage':unlimited_usage(),'remaining':{'calls':None if unlimited_usage() or p.call_budget==0 else max(0,p.call_budget-p.calls_reserved),
                'inputTokens':None if unlimited_usage() else max(0,p.input_budget-p.input_reserved),'outputTokens':None if unlimited_usage() else max(0,p.output_budget-p.output_reserved)},
            'moneyEstimate':None,'moneyNote':'当前未配置可信模型单价，金额未知；token 估算和预算上限不等于实际费用。'}


def create(db,owner,world,data):
    draft=db.get(AuthorDraft,world.id)
    if not draft or draft.version!=data.authorVersion:raise Problem('AUTHOR_VERSION_CONFLICT','请先保存当前配置，再开始这一版的试玩',409)
    if world.source.get('authorTrial'):raise Problem('AUTHOR_TRIAL_NESTED','请返回原作品创建新的测试，不在测试世界内再次复制',409)
    definition=compile_definition(world,draft.value)
    source=deepcopy(world.source)
    source['authorTrial']={'originWorldId':world.id,'authorVersion':draft.version,'requestId':data.requestId,
        'limits':{'generations':data.maxGenerations,'inputTokens':data.maxInputTokens,'outputTokens':data.maxOutputTokens},
        'reserved':{'generations':0,'inputTokens':0,'outputTokens':0}}
    trial=World(id=uid(),owner_id=owner,name=(draft.value['title'][:145]+' · 作者试玩'),source_book_id=world.source_book_id,
                source_title=world.source_title,source=source,definition=definition,status='active')
    db.add(trial);db.flush()
    db.add(AuthorDraft(world_id=trial.id,owner_id=owner,version=1,value=deepcopy(draft.value)));db.flush()
    from .artworks import freeze,clone
    clone(db,world,trial,freeze(db,world,definition))
    start=BeginWorld.model_validate({k:v for k,v in data.model_dump().items() if k in BeginWorld.model_fields})
    result=begin_world(db,owner,trial,start,profile_id=data.profileId)
    return {**result,'worldId':trial.id,'authorWorldId':world.id,'authorVersion':draft.version}


def reserve(db,owner,payload,budget,task_id):
    world_id=payload.get('worldId')
    if not world_id:return
    world=db.scalar(select(World).where(World.id==world_id,World.owner_id==owner).with_for_update().execution_options(populate_existing=True))
    if not world:return
    trial=deepcopy(world.source.get('authorTrial'))
    if not trial:return
    units=max(1,payload.get('chapterCount',1))
    increments={'generations':units,'inputTokens':budget['inputTokens'],'outputTokens':budget['outputTokens']}
    limits=trial.get('limits',{});reserved=trial.get('reserved',{})
    if not unlimited_usage() and any(type(limits.get(k)) is not int or type(reserved.get(k)) is not int or reserved[k]<0 or reserved[k]+v>limits[k] for k,v in increments.items()):
        raise Problem('AUTHOR_TRIAL_BUDGET_EXCEEDED','这条试玩的次数或 token 预算不足。原结果已保留；请返回作者工作台检查预算，再另建试玩。',403)
    trial['reserved']={k:reserved[k]+v for k,v in increments.items()}
    world.source={**world.source,'authorTrial':trial}


def present(db,world):
    value=deepcopy(world.source['authorTrial'])
    tasks=list(db.scalars(select(Task).where(Task.owner_id==world.owner_id,Task.payload['worldId'].astext==world.id).order_by(Task.created_at)))
    return {'worldId':world.id,'name':world.name,'lineId':world.active_line_id,'createdAt':world.created_at.isoformat(),
        **value,'unlimitedUsage':unlimited_usage(),'tasks':[{'id':t.id,'state':t.state,'errorCode':t.error_code,'providerState':t.provider_state,'createdAt':t.created_at.isoformat()} for t in tasks],
        'budgetNote':'内测不限额度，次数与 token 预留仅用于记录。失败和未知结果不会自动重发。' if unlimited_usage() else '次数包含开场、重生成与测试世界内的文字请求；失败和未知结果保留预留，不自动重发。不是免费积分或金额结算。'}


def text_only(world):
    if world.source.get('authorTrial') and not unlimited_usage():raise Problem('AUTHOR_TRIAL_TEXT_ONLY','这条作者试玩只授权文字预算，请在普通游玩中另行配置图像预算',403)
