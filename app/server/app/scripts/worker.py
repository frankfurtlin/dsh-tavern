import json
from copy import deepcopy
from sqlalchemy import select
from ..db import SessionLocal
from ..models import Task, ModelProfile, now
from ..errors import Problem
from ..worker import call_provider, assert_task_active, add_event
from ..completion_policy import completion_limit
from ..world.models import World
from ..world.jobs import phase, lock_task_world
from ..world.state import parse_json
from .schemas import ScriptOutput, ScriptBlueprint
from .compiler import compile_blueprint
from .service import groups_for, validate, validate_notes


def compact_schema(value, *, properties=False):
    """Keep structural validation hints without repeated display metadata."""
    if isinstance(value, list):return [compact_schema(v) for v in value]
    if isinstance(value, dict):
        return {k:compact_schema(v,properties=k in ('properties','$defs')) for k,v in value.items()
                if properties or k not in ('title','description','default','examples')}
    return value


def process(task_id):
    with SessionLocal() as db:
        task=db.get(Task,task_id);world=db.get(World,task.payload['worldId'])
        profile=db.get(ModelProfile,task.payload['profileId'])
        if world.prepare_task_id!=task_id or world.status!='preparing':raise Problem('TASK_CANCELLED','这次拆解已被替代')
        groups=groups_for(world.source['blocks'])
        complete=task.checkpoint.get('scriptNotes',[])
        limit=completion_limit(profile,'breakdown')
    if len(groups)>1:
        for index in range(len(complete),len(groups)):
            phase(task_id,'breaking_down',part=index+1,parts=len(groups))
            result=call_provider(task_id,f'breakdown:part:{index+1}','script_notes',{'blocks':groups[index], 'part':index+1,'parts':len(groups),'_structured':True},limit)
            if result.get('finishReason') not in (None,'stop'):raise Problem('SCRIPT_OUTPUT_INCOMPLETE','这一部分尚未完整拆解，已处理部分仍保留')
            try:notes=json.loads(result['text'])
            except (ValueError,TypeError):raise Problem('SCRIPT_OUTPUT_INVALID','拆解结果格式未完成')
            notes=validate_notes(notes,groups[index])
            complete.append(notes)
            with SessionLocal() as db:
                lock_task_world(db,task_id);task=db.get(Task,task_id);assert_task_active(db,task)
                world=db.get(World,task.payload['worldId'])
                task.checkpoint={**task.checkpoint,'scriptNotes':deepcopy(complete)}
                world.source={**world.source,'script':{**world.source['script'],'status':'analyzing','processedBlocks':sum(len(g) for g in groups[:index+1])}}
                db.commit()
    phase(task_id,'compiling_script')
    with SessionLocal() as db:
        task=db.get(Task,task_id);world=db.get(World,task.payload['worldId'])
        novel=world.source['script'].get('scale')=='novel' or world.source['script'].get('sourceCharacters',0)>100000
        source_index={}
        for block in world.source['blocks']:source_index.setdefault(block['chapterId'],[]).append(block['blockId'])
        data={'title':world.name,'rules':world.definition['rules'],'outputSchema':compact_schema(ScriptBlueprint.model_json_schema()),
              'storyScale':'novel' if novel else 'short',
              'coverage':{'chapterCount':world.source['script'].get('chapterCount',0),
                  'chapters':sorted({b['chapterId'] for b in world.source['blocks'] if b['chapterId']!='script-full'})},
              'blueprintBudget':({'people':16,'places':14,'items':12,'clues':20,'scenes':30,'objectives':14} if novel else
                  {'people':8,'places':7,'items':6,'clues':9,'scenes':8,'objectives':7}),
              **({'blocks':groups[0]} if len(groups)==1 else {'notes':complete,'sourceIndex':[
                  {'chapterId':chapter,'blockIds':ids} for chapter,ids in source_index.items()]}),'_structured':True}
    result=call_provider(task_id,'breakdown:compile','script_breakdown',data,limit)
    with SessionLocal() as db:
        lock_task_world(db,task_id);task=db.get(Task,task_id);assert_task_active(db,task)
        world=db.get(World,task.payload['worldId'])
        world.source={**world.source,'script':{**world.source['script'],'raw':result.get('text','')}}
        db.commit()
    if result.get('finishReason') not in (None,'stop'):raise Problem('SCRIPT_OUTPUT_INCOMPLETE','拆解尚未完整返回，原文与已收到结果已保存')
    try:
        blueprint=parse_json(result['text'],ScriptBlueprint)
        value=compile_blueprint(blueprint,world.source['blocks'])
    except Problem:raise Problem('SCRIPT_OUTPUT_INVALID','拆解结构未完成，可查看已保留结果并重试')
    with SessionLocal() as db:
        lock_task_world(db,task_id);task=db.get(Task,task_id);assert_task_active(db,task)
        world=db.get(World,task.payload['worldId'])
        if world.prepare_task_id!=task_id:raise Problem('TASK_CANCELLED','这次拆解已被替代')
        value,compiled=validate(value,world)
        world.source={**world.source,'script':{**world.source['script'],'status':'review','processedBlocks':len(world.source['blocks']),'value':value}}
        world.version+=1;world.updated_at=now()
        task.state='succeeded';task.persistence_state='saved';task.result_outcome='complete'
        task.result={'phase':'script_review','worldId':world.id,'processedBlocks':len(world.source['blocks']),'sceneCount':len(value['scenes'])}
        add_event(db,task,'terminal',{'state':'succeeded','resultOutcome':'complete'});db.commit()
