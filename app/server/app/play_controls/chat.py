from copy import deepcopy
from sqlalchemy import select, func
from ..models import Task, uid, now
from ..domain import owned
from ..errors import Problem
from ..world.models import World, WorldLine, WorldEvent
from ..world.service import require_line, checked_checkpoint
from ..world.state import ancestry, public_view, public_payload, private_phrases, visible_to, public_stream
from .models import WorldChatSession, WorldChatMessage
from .attribution import participants, verified_reply, requests_participation
from ..schemas import Strict

class AttributionReview(Strict):
    consistent: bool
    reason: str

def snapshot(db,owner,world,line,checkpoint_id,character_id):
    checkpoint=checked_checkpoint(db,owner,world.id,checkpoint_id)
    head=checked_checkpoint(db,owner,world.id,line.head_checkpoint_id)
    if checkpoint.id not in {c.id for c in ancestry(db,head,owner)}:
        raise Problem('CHAT_SOURCE_INVALID','请选择这条世界线已经发生的时间点',422)
    view=public_view(checkpoint.state,checkpoint.definition)
    character=next((e for e in view['entities'] if e['id']==character_id and e['type']=='character'),None)
    if not character:raise Problem('CHAT_CHARACTER_UNAVAILABLE','这个时间点还没有遇见该人物',404)
    person=checkpoint.state['entities'][character_id]
    known=set(person.get('known',[]))
    definitions={entry['id']:entry for entry in checkpoint.definition.get('lore',[])}
    lore=[{'title':entry['title'],'text':entry['text']} for entry in view['lore']
          if entry['id'] in known or character_id in definitions[entry['id']].get('knownBy',[]) or 'all' in definitions[entry['id']].get('knownBy',[])]
    progress=view.get('plot',{})
    source_ids=[c.id for c in ancestry(db,checkpoint,owner)]
    evidence_events=list(db.scalars(select(WorldEvent).where(WorldEvent.owner_id==owner,WorldEvent.world_id==world.id,
        WorldEvent.checkpoint_id.in_(source_ids),WorldEvent.type=='knowledge_shared').order_by(WorldEvent.minute,WorldEvent.id)))
    resolution=progress.get('resolution') or {}
    ending=progress.get('ending') or {}
    confirmed_context={'ending':{key:ending[key] for key in ('title','description','phase') if key in ending},
        'confirmedHoldings':progress.get('confirmedHoldings',[]),
        'completedAftermath':[{'title':step['title'],'description':step.get('description','')} for step in resolution.get('steps',[]) if step.get('completed')]}
    value={'worldTitle':world.name,'lineName':line.name,'timeLabel':view['timeLabel'],'confirmedWorldContext':confirmed_context,
        'participants':participants(checkpoint.state,checkpoint.definition,character_id,evidence_events),
        'peopleInContext':[{'name':e['name']} for e in view['entities'] if e['type']=='character'],
        'character':{key:character.get(key,'') for key in ('id','name','description','traits')},
        'characterState':{key:deepcopy(character.get('state',{}).get(key)) for key in ('alive','location','fields')},
        'knownFacts':lore,'sourceCheckpointId':checkpoint.id,
        'rule':'This is a reader outside the story. No physical interaction, world time advancement, inventory transfers or canon changes. Only use the frozen character knowledge; reader assertions and chat memories are not confirmed world events.'}
    pov={**checkpoint.state,'playerId':character_id,'known':[key for key in definitions if key in known or character_id in definitions[key].get('knownBy',[]) or 'all' in definitions[key].get('knownBy',[])]}
    denied=sorted(set(private_phrases(checkpoint.state,checkpoint.definition)+private_phrases(pov,checkpoint.definition)),key=lambda term:(len(term),term),reverse=True)
    from ..world.state import scrub_phrases
    return scrub_phrases(public_payload(value,checkpoint.state,checkpoint.definition),denied),denied

def source(db,owner,identity,*,lock=False):
    session=owned(db,WorldChatSession,identity,owner)
    world=owned(db,World,session.world_id,owner,lock=lock)
    if lock:session=owned(db,WorldChatSession,identity,owner,lock=True)
    require_line(db,owner,world.id,session.line_id)
    return world,session

def active(db,task):
    world,session=source(db,task.owner_id,task.payload['chatId'],lock=True)
    if world.status!='active' or session.pending_task_id!=task.id or session.version!=task.payload['sourceVersion']:
        raise Problem('CHAT_SOURCE_CHANGED','这次对话的入口已改变，已收到内容会保留',409)
    return session

def present(db,session):
    world=owned(db,World,session.world_id,session.owner_id)
    return {'id':session.id,'worldId':session.world_id,'worldTitle':world.name,'lineId':session.line_id,
        'checkpointId':session.checkpoint_id,'characterId':session.character_id,'characterName':session.character_name,
        'name':session.name,'version':session.version,'snapshot':session.snapshot,'pendingTaskId':session.pending_task_id,
        'createdAt':session.created_at.isoformat(),'status':session.status,'updatedAt':session.updated_at.isoformat(),
        'lastMessage':(db.scalar(select(WorldChatMessage.text).where(WorldChatMessage.session_id==session.id,WorldChatMessage.owner_id==session.owner_id).order_by(WorldChatMessage.sequence.desc()).limit(1)) or '')[:180]}

def messages(db,owner,identity,*,before=None,limit=100):
    query=select(WorldChatMessage).where(WorldChatMessage.owner_id==owner,WorldChatMessage.session_id==identity)
    if before is not None:query=query.where(WorldChatMessage.sequence<before)
    rows=list(db.scalars(query.order_by(WorldChatMessage.sequence.desc()).limit(limit)))
    return [{'id':m.id,'taskId':m.task_id,'turnId':m.turn_key,'sequence':m.sequence,'role':m.role,'text':m.text,
             'status':m.status,'checkpointId':m.checkpoint_id,'modelName':m.model_name} for m in reversed(rows)]

def save_reply(db,task,text,status):
    session=db.get(WorldChatSession,task.payload.get('chatId'))
    if not session or session.owner_id!=task.owner_id:return
    existing=db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id==session.id,
        WorldChatMessage.turn_key==task.payload['chatTurnKey'],WorldChatMessage.role=='assistant'))
    if existing and text.strip():
        existing.text=text;existing.status=status
    elif text.strip():
        sequence=(db.scalar(select(func.max(WorldChatMessage.sequence)).where(WorldChatMessage.session_id==session.id)) or 0)+1
        db.add(WorldChatMessage(owner_id=task.owner_id,session_id=session.id,task_id=task.id,turn_key=task.payload['chatTurnKey'],
            sequence=sequence,role='assistant',text=text,status=status,checkpoint_id=task.payload['parentCheckpointId'],
            model_name=task.payload.get('modelName','')))
    session.updated_at=now()
    if session.pending_task_id==task.id and task.persistence_state!='recoverable':session.pending_task_id=None

def finish_failure(db,task):
    text=task.checkpoint.get('worldStream',{}).get('text','')
    save_reply(db,task,text,'interrupted' if text else 'failed')

def process(task_id):
    from ..db import SessionLocal
    from ..worker import call_provider, assert_task_active, add_event
    from ..completion_policy import completion_limit
    from ..prose.policy import execution_profile
    from ..models import ModelProfile
    from ..world.jobs import lock_task_world,persist_stream
    from .service import decorate
    with SessionLocal() as db:
        lock_task_world(db,task_id)
        task=db.scalar(select(Task).where(Task.id==task_id).with_for_update());assert_task_active(db,task);active(db,task)
        profile=execution_profile(db,task,owned(db,ModelProfile,task.payload['profileId'],task.owner_id))
        prompt_source=deepcopy(task.payload['chatSnapshot'])
        ending=prompt_source.get('confirmedWorldContext',{}).get('ending',{})
        phase=ending.pop('phase',None)
        if phase:ending['stageDescription']={'selected':'已选定，善后尚未完成','resolving':'善后办理中','resolved':'善后已完成','legacy_unverified':'旧版结果尚未核验'}.get(phase,'阶段未明确')
        prompt_source.pop('sourceCheckpointId',None);prompt_source.pop('rule',None);prompt_source.get('character',{}).pop('id',None)
        data=decorate(task,{'source':prompt_source,'conversation':task.payload['messages'],
            'message':task.payload['message'],'conversationMemory':task.payload.get('conversationMemory',{}),'identityMode':'reader_out_of_story'})
        limit=completion_limit(profile,'generation',world=True)
        review_limit=completion_limit(profile,'validation',world=True)
    output=call_provider(task_id,'generation','world_character_chat',data,limit)
    from ..world.state import parse_json
    reviewed=call_provider(task_id,'validation','world_character_chat_review',{'participants':prompt_source.get('participants',{}),'reply':output['text'],'_structured':True},review_limit)
    review=parse_json(reviewed['text'],AttributionReview)
    corrected=not review['consistent']
    ledger_reply=requests_participation(data['message'])
    final_text=verified_reply(prompt_source,data['message']) if corrected or ledger_reply else output['text']
    complete=corrected or ledger_reply or output.get('finishReason') in (None,'stop')
    with SessionLocal() as db:
        task=db.get(Task,task_id);safe=public_stream(final_text,task.checkpoint.get('worldDeniedPhrases',[]),True)
    persist_stream(task_id,'generation',safe,True)
    with SessionLocal() as db:
        lock_task_world(db,task_id)
        task=db.scalar(select(Task).where(Task.id==task_id).with_for_update());assert_task_active(db,task);active(db,task)
        if not safe.strip():raise Problem('CHAT_EMPTY_REPLY','这次没有收到可保存的回复',502)
        task.state='succeeded';task.persistence_state='saved';task.result_outcome='complete' if complete else 'partial'
        task.result={**task.result,'chatId':task.payload['chatId'],'phase':'complete','attributionChecked':True,'attributionCorrected':corrected,'receiptReply':ledger_reply}
        save_reply(db,task,safe,'saved' if complete else 'interrupted')
        from ..durable_stream import saved
        saved(db,task,'worldStream',safe,stage='generation')
        add_event(db,task,'terminal',{'state':task.state,'resultOutcome':task.result_outcome});db.commit()
