from fastapi import APIRouter, Depends, Request
from ..db import get_db
from ..security import current_user
from ..domain import owned
from ..models import Task, now
from ..errors import Problem
from ..world.models import World
from ..world.api import available, mutate
from .schemas import ScriptInput, ScriptPublish
from . import service

router=APIRouter(dependencies=[Depends(available)])


@router.get('/scripts/samples')
def samples(user=Depends(current_user)):
    return {'items':service.SAMPLES}


@router.post('/scripts',status_code=202)
def create(data:ScriptInput,request:Request,user=Depends(current_user),db=Depends(get_db)):
    return mutate(db,user,request,'script-create',data.model_dump(),lambda:service.create(db,user.id,data))


@router.post('/scripts/samples/{sample_id}/prepared',status_code=201)
def prepared_sample(sample_id:str,request:Request,user=Depends(current_user),db=Depends(get_db)):
    return mutate(db,user,request,'script-prepared:'+sample_id,{},lambda:service.create_prepared_sample(db,user.id,sample_id))


@router.get('/worlds/{world_id}/script')
def read(world_id:str,user=Depends(current_user),db=Depends(get_db)):
    world=owned(db,World,world_id,user.id)
    result=service.view(world)
    task=db.get(Task,world.prepare_task_id) if world.prepare_task_id else None
    if task and task.owner_id==user.id:result['task']={'id':task.id,'state':task.state,'phase':task.result.get('phase'), 'errorCode':task.error_code}
    return result


@router.post('/worlds/{world_id}/script/retry',status_code=202)
def retry(world_id:str,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world=owned(db,World,world_id,user.id,lock=True)
    def execute():
        if world.active_line_id:raise Problem('SCRIPT_ALREADY_PUBLISHED','已有游玩存档，请新建拆解以保护现有世界',409)
        if not world.source.get('script'):raise Problem('SCRIPT_NOT_FOUND','没有拆解记录',404)
        prior=db.get(Task,world.prepare_task_id)
        if prior and prior.state not in ('succeeded','failed','cancelled'):raise Problem('GENERATION_RUNNING','拆解正在进行',409)
        # A complete, known response may now validate after a contract update.
        # Reuse that received result before reserving a paid independent retry.
        raw=world.source['script'].get('raw')
        if raw and prior and prior.provider_state=='completed' and prior.error_code!='SCRIPT_OUTPUT_INCOMPLETE':
            import json
            try:
                decoded=json.loads(raw)
                if 'people' in decoded:
                    from .compiler import compile_blueprint
                    decoded=compile_blueprint(decoded,world.source['blocks'])
                value,compiled=service.validate(decoded,world)
            except (ValueError,Problem):pass
            else:
                world.source={**world.source,'script':{**world.source['script'],'status':'review','value':value,'processedBlocks':len(world.source['blocks'])}}
                world.status='preparing';world.version+=1
                prior.state='succeeded';prior.error_code=None;prior.persistence_state='saved';prior.result_outcome='complete'
                prior.result={**prior.result,'phase':'script_review','worldId':world.id,'reusedReceivedResult':True}
                return {'worldId':world.id,'taskId':prior.id,'status':'review','reusedReceivedResult':True}
        return service.enqueue(db,world)
    return mutate(db,user,request,'script-retry:'+world_id,{},execute)


@router.post('/worlds/{world_id}/script/publish')
def publish(world_id:str,data:ScriptPublish,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world=owned(db,World,world_id,user.id,lock=True)
    def execute():
        if world.version!=data.expectedVersion:raise Problem('VERSION_CONFLICT','拆解已更新，请载入最新版本',409)
        if world.active_line_id:raise Problem('SCRIPT_ALREADY_PUBLISHED','已有游玩存档，请新建拆解以保护现有世界',409)
        if world.source.get('script',{}).get('status') not in ('review','published'):raise Problem('SCRIPT_NOT_READY','请先完成拆解',409)
        value,definition=service.validate(data.value.model_dump() if data.value else world.source['script']['value'],world,publication=True)
        world.definition=definition;world.status='active';world.version+=1;world.updated_at=now()
        world.source={**world.source,'summary':value['opening'],'script':{**world.source['script'],'value':value,'status':'published'}}
        return {'worldId':world.id,'status':'active','recommendedCharacterId':value['recommendedCharacterId'],'opening':value['opening']}
    return mutate(db,user,request,'script-publish:'+world_id,data.model_dump(),execute)


@router.put('/worlds/{world_id}/script/draft')
def save_draft(world_id:str,data:ScriptPublish,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world=owned(db,World,world_id,user.id,lock=True)
    def execute():
        if world.version!=data.expectedVersion:raise Problem('VERSION_CONFLICT','草稿已更新，请载入最新版本；本次输入仍可保留',409)
        if world.active_line_id or world.source.get('script',{}).get('status')!='review':
            raise Problem('SCRIPT_REVISION_REQUIRED','已发布或已有存档，请制作新版本后编辑',409)
        if not data.value:raise Problem('SCRIPT_OUTPUT_INVALID','请先提供完整校对稿',422)
        value,_=service.validate(data.value.model_dump(),world)
        # Saving never changes an executable definition or creates a model job.
        world.source={**world.source,'script':{**world.source['script'],'value':value}}
        world.version+=1;world.updated_at=now()
        return {'worldId':world.id,'worldVersion':world.version,'saved':True}
    return mutate(db,user,request,'script-draft:'+world_id,data.model_dump(),execute)


@router.post('/worlds/{world_id}/script/revision',status_code=201)
def revision(world_id:str,request:Request,user=Depends(current_user),db=Depends(get_db)):
    original=owned(db,World,world_id,user.id,lock=True)
    def execute():
        from copy import deepcopy
        from ..models import uid
        script=original.source.get('script',{})
        if not script.get('value'):raise Problem('SCRIPT_NOT_READY','请先完成拆解',409)
        source=deepcopy(original.source)
        source['script']={**source['script'],'status':'review','revisionOfWorldId':original.id}
        source['script'].pop('taskId',None)
        draft=World(id=uid(),owner_id=user.id,name=original.name,source_title=original.source_title,
            source=source,definition={'rules':deepcopy(original.definition['rules']),'version':0},status='preparing')
        db.add(draft);db.flush()
        from ..authoring.models import AuthorDraft
        author=db.get(AuthorDraft,original.id)
        if author:db.add(AuthorDraft(world_id=draft.id,owner_id=user.id,version=1,value=deepcopy(author.value)))
        return {'worldId':draft.id,'status':'review'}
    return mutate(db,user,request,'script-revision:'+world_id,{},execute)
