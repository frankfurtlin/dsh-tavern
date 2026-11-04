from sqlalchemy import select, func, delete
import json
from copy import deepcopy
from fastapi import APIRouter, Depends, Request, Query, Response
from pydantic import Field
from ..db import get_db
from ..models import Task, uid, now
from ..security import current_user
from ..schemas import Strict
from ..domain import owned, idempotent, digest
from ..errors import Problem
from ..world.api import available
from ..world.models import World, WorldLine
from ..world.service import require_line, checked_checkpoint
from ..world.state import public_view, public_payload, ancestry
from ..world.jobs import create_job, TERMINAL
from ..cards import exporting as card_exporting
from ..cards import personas as card_personas
from ..cards import space as card_space
from .models import PlayControl, MemoryRevision, WorldChatSession, WorldChatMessage
from .service import save,presentation
from . import chat

router=APIRouter(prefix='/play-controls',dependencies=[Depends(available)])

class ControlInput(Strict):
    expectedVersion:int=Field(ge=0,strict=True)
    profileId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    summary:str=Field(default='',max_length=4000)
    notes:str=Field(default='',max_length=4000)

class RestoreInput(Strict):
    expectedVersion:int=Field(ge=1,strict=True)
    revision:int=Field(ge=1,strict=True)

class ChatInput(Strict):
    worldId:str=Field(pattern=r'^[a-f0-9]{32}$')
    lineId:str=Field(pattern=r'^[a-f0-9]{32}$')
    checkpointId:str=Field(pattern=r'^[a-f0-9]{32}$')
    characterId:str=Field(min_length=1,max_length=80)

class ChatUpdate(Strict):
    expectedVersion:int=Field(ge=1,strict=True)
    name:str|None=Field(default=None,min_length=1,max_length=120)
    updateSource:bool=False
    status:str|None=Field(default=None,pattern=r'^(active|archived)$')
    personaId:str|None=Field(default=None,max_length=32)

class SendInput(Strict):
    message:str=Field(min_length=1,max_length=4000)
    turnId:str=Field(min_length=1,max_length=100)

class MessageEditInput(Strict):
    text:str=Field(max_length=4000)

class ForkInput(Strict):
    sequence:int=Field(ge=1)

def scope(db,owner,kind,identity):
    if kind=='chats':
        world,session=chat.source(db,owner,identity,lock=True)
        return world,'chat',session
    if kind!='lines':raise Problem('RESOURCE_NOT_FOUND','内容不存在或不可访问',404)
    line=owned(db,WorldLine,identity,owner)
    world,line=require_line(db,owner,line.world_id,line.id,lock=True)
    return world,'line',line

def view(db,owner,kind,resource,world):
    result=presentation(db,owner,kind,resource.id);result['worldId']=world.id
    if kind=='chat':
        result['source']=chat.present(db,resource);result['confirmedFacts']={'characterKnownFacts':resource.snapshot.get('knownFacts',[]),'confirmedWorldContext':resource.snapshot.get('confirmedWorldContext',{})}
        result['automaticSummary']='人物来源：'+resource.snapshot['timeLabel'];result['characters']=[resource.snapshot['character']]
        from .recall import recollect
        recalled=recollect(db,resource)
        result['automaticSummary']+='。已整理早期消息 '+str(recalled['archivedMessageCount'])+' 条；以下为对话摘录，不是主线事实。'
        result['episodes']=[{'checkpointId':r['checkpointId'],'sequence':r['sequence'],'kind':'chat','href':'/conversations/world/'+resource.id+'?message='+str(r['sequence']),
            'title':'第 '+str(r['sequence'])+' 条','text':('读者说' if r['role']=='user' else '人物当时回复')+'：'+r['text']} for r in recalled['excerpts']]
    else:
        head=checked_checkpoint(db,owner,world.id,resource.head_checkpoint_id);public=public_view(head.state,head.definition)
        result['checkpointId']=head.id;result['confirmedFacts']={'progress':public.get('plot',{}),'knownFacts':[{'title':e['title'],'text':e['text']} for e in public['lore']]}
        result['automaticSummary']=public_payload(head.summary,head.state,head.definition)
        result['fieldDefinitions']=public['statusFields']
        result['characters']=[{'id':e['id'],'name':e['name'],'description':e.get('description',''),'fields':e.get('state',{}).get('fields',{}),
            'knownFacts':[{'title':entry['title'],'text':entry['text']} for entry in public['lore'] if entry['id'] in head.state['entities'][e['id']].get('known',[]) or e['id'] in entry.get('entityIds',[])]} for e in public['entities'] if e['type']=='character']
        result['episodes']=[{'checkpointId':c.id,'sequence':c.sequence,'kind':'world','title':public_payload(c.title,head.state,head.definition),
            'href':'/worlds/'+world.id+'/read?line='+resource.id+'&checkpoint='+c.id,
            'text':public_payload(c.summary,head.state,head.definition)} for c in ancestry(db,head,owner)[-20:] if c.summary]
    query=select(Task).where(Task.owner_id==owner,Task.payload['chatId' if kind=='chat' else 'lineId'].astext==resource.id,
        Task.task_type.in_(['world_character_chat'] if kind=='chat' else ['world_turn','world_write','world_rewrite'])).order_by(Task.created_at.desc(),Task.id.desc()).limit(1)
    latest=db.scalar(query)
    result['generationContext']=None
    if latest and latest.checkpoint.get('playControl'):
        frozen=latest.checkpoint['playControl']
        result['generationContext']={'taskId':latest.id,'state':latest.state,'modelName':latest.payload.get('modelName',''),
            'memoryVersion':frozen['version'],'memory':deepcopy(frozen['memory'])}
    return result

@router.get('/{kind}/{identity}/settings')
def read_control(kind:str,identity:str,user=Depends(current_user),db=Depends(get_db)):
    world,scope_kind,resource=scope(db,user.id,kind,identity)
    return view(db,user.id,scope_kind,resource,world)

@router.get('/{kind}/{identity}/history')
def memory_history(kind:str,identity:str,before:int|None=Query(None,ge=1),user=Depends(current_user),db=Depends(get_db)):
    from .service import control
    _,scope_kind,_=scope(db,user.id,kind,identity)
    row=control(db,user.id,scope_kind,identity)
    query=select(MemoryRevision).where(MemoryRevision.owner_id==user.id,MemoryRevision.control_id==row.id) if row else None
    if query is None:return {'items':[],'nextBefore':None}
    if before is not None:query=query.where(MemoryRevision.version<before)
    rows=list(db.scalars(query.order_by(MemoryRevision.version.desc()).limit(51)))
    return {'items':[{'version':r.version,'memory':r.memory,'createdAt':r.created_at.isoformat()} for r in rows[:50]],
            'nextBefore':rows[49].version if len(rows)>50 else None}

@router.put('/{kind}/{identity}/settings')
def write_control(kind:str,identity:str,data:ControlInput,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world,scope_kind,resource=scope(db,user.id,kind,identity)
    def update():save(db,user.id,scope_kind,resource.id,world.id,data);return view(db,user.id,scope_kind,resource,world)
    return idempotent(db,user.id,'play-controls:'+kind+':'+identity,request.headers.get('idempotency-key'),data.model_dump(),update)

@router.post('/{kind}/{identity}/settings/restore')
def restore_control(kind:str,identity:str,data:RestoreInput,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world,scope_kind,resource=scope(db,user.id,kind,identity)
    from .service import control
    def update():
        row=control(db,user.id,scope_kind,identity,lock=True)
        revision=db.scalar(select(MemoryRevision).where(MemoryRevision.owner_id==user.id,MemoryRevision.control_id==row.id,MemoryRevision.version==data.revision)) if row else None
        if not revision:raise Problem('RESOURCE_NOT_FOUND','记忆版本不存在或不可访问',404)
        save(db,user.id,scope_kind,identity,world.id,ControlInput(expectedVersion=data.expectedVersion,profileId=row.profile_id,**revision.memory))
        return view(db,user.id,scope_kind,resource,world)
    return idempotent(db,user.id,'play-controls:restore:'+identity,request.headers.get('idempotency-key'),data.model_dump(),update)

@router.post('/world-chats',status_code=201)
def create_chat(data:ChatInput,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world,line=require_line(db,user.id,data.worldId,data.lineId,lock=True)
    def create():
        if world.status!='active':raise Problem('WORLD_ARCHIVED','请先恢复世界，再开始新的对话',409)
        value,denied=chat.snapshot(db,user.id,world,line,data.checkpointId,data.characterId)
        session=WorldChatSession(id=uid(),owner_id=user.id,world_id=world.id,line_id=line.id,checkpoint_id=data.checkpointId,
            character_id=data.characterId,character_name=value['character']['name'],name=value['character']['name']+' · 单独聊聊',
            snapshot=value,denied_phrases=denied,version=1)
        db.add(session);db.flush();return chat.present(db,session)
    return idempotent(db,user.id,'play-controls:create-chat:'+world.id,request.headers.get('idempotency-key'),data.model_dump(),create)

@router.get('/world-chats')
def list_chats(cursor:str|None=None,limit:int=Query(50,ge=1,le=100),q:str=Query('',max_length=100),status:str=Query('active',pattern=r'^(active|archived)$'),user=Depends(current_user),db=Depends(get_db)):
    from sqlalchemy import or_,and_
    query=select(WorldChatSession).join(World,World.id==WorldChatSession.world_id).join(WorldLine,WorldLine.id==WorldChatSession.line_id).where(
        WorldChatSession.owner_id==user.id,World.owner_id==user.id,WorldLine.owner_id==user.id,WorldLine.status!='deleted')
    query=query.where(WorldChatSession.status==status)
    if q:
        pattern='%'+q.replace('%','\\%').replace('_','\\_')+'%'
        query=query.where(or_(WorldChatSession.name.ilike(pattern),WorldChatSession.character_name.ilike(pattern),World.name.ilike(pattern)))
    if cursor:
        _,point=chat.source(db,user.id,cursor)
        query=query.where(or_(WorldChatSession.updated_at<point.updated_at,and_(WorldChatSession.updated_at==point.updated_at,WorldChatSession.id<point.id)))
    rows=list(db.scalars(query.order_by(WorldChatSession.updated_at.desc(),WorldChatSession.id.desc()).limit(limit+1)))
    return {'items':[chat.present(db,s) for s in rows[:limit]],'nextCursor':rows[limit-1].id if len(rows)>limit else None}

@router.get('/world-chats/{identity}')
def read_chat(identity:str,user=Depends(current_user),db=Depends(get_db)):
    _,session=chat.source(db,user.id,identity)
    return {**chat.present(db,session),'messages':chat.messages(db,user.id,identity)}

@router.get('/world-chats/{identity}/recall')
def recall_chat(identity:str,q:str=Query('',max_length=100),user=Depends(current_user),db=Depends(get_db)):
    _,session=chat.source(db,user.id,identity)
    from .recall import recollect
    return recollect(db,session,q)

@router.get('/world-chats/{identity}/source-preview')
def source_preview(identity:str,user=Depends(current_user),db=Depends(get_db)):
    world,session=chat.source(db,user.id,identity)
    _,line=require_line(db,user.id,world.id,session.line_id)
    latest,_=chat.snapshot(db,user.id,world,line,line.head_checkpoint_id,session.character_id)
    previous={(r['title'],r['text']) for r in session.snapshot.get('knownFacts',[])}
    return {'fromTime':session.snapshot['timeLabel'],'toTime':latest['timeLabel'],
            'changed':session.checkpoint_id!=line.head_checkpoint_id,
            'addedFacts':[r for r in latest.get('knownFacts',[]) if (r['title'],r['text']) not in previous]}

@router.get('/world-chats/{identity}/transcript')
def transcript(identity:str,user=Depends(current_user),db=Depends(get_db)):
    from ..world.state import public_stream
    world,session=chat.source(db,user.id,identity)
    rows=db.scalars(select(WorldChatMessage).where(WorldChatMessage.owner_id==user.id,WorldChatMessage.session_id==identity).order_by(WorldChatMessage.sequence))
    lines=[session.name,'来源：'+world.name+' · '+session.snapshot['timeLabel'],'仅供阅读；此文本不是可恢复的存档。','']
    for row in rows:
        lines.extend([('你' if row.role=='user' else session.character_name)+' · '+row.status,public_stream(row.text,session.denied_phrases,True),''])
    from .service import control
    settings=control(db,user.id,'chat',identity)
    if settings:lines.extend(['手动叙事摘要',settings.memory['summary'],'补充提醒',settings.memory['notes']])
    return {'text':'\n\n'.join(lines),'name':session.name}

@router.delete('/world-chats/{identity}')
def delete_chat(identity:str,expectedVersion:int=Query(ge=1),user=Depends(current_user),db=Depends(get_db)):
    from .management import remove
    world,session=chat.source(db,user.id,identity,lock=True)
    if session.version!=expectedVersion:raise Problem('CHAT_VERSION_CONFLICT','对话已更新，请重新载入',409)
    if session.pending_task_id:raise Problem('CHAT_PENDING','请先停止并保存当前回复，再删除对话',409)
    remove(db,user.id,session)
    db.commit()
    from ..storage import clean_deleted_world_journals
    clean_deleted_world_journals(user.id,world.id)
    return {'deleted':True,'chatId':identity,'worldId':world.id}

@router.get('/world-chats/{identity}/messages')
def older_messages(identity:str,before:int|None=Query(None,ge=1),around:int|None=Query(None,ge=1),q:str=Query('',max_length=100),user=Depends(current_user),db=Depends(get_db)):
    chat.source(db,user.id,identity)
    if around is not None or q:
        query=select(WorldChatMessage).where(WorldChatMessage.owner_id==user.id,WorldChatMessage.session_id==identity)
        if around is not None:query=query.where(WorldChatMessage.sequence.between(max(1,around-3),around+3))
        if q:query=query.where(WorldChatMessage.text.ilike('%'+q.replace('%','\\%').replace('_','\\_')+'%'))
        rows=list(db.scalars(query.order_by(WorldChatMessage.sequence.desc()).limit(50)))
        return {'items':[{'id':m.id,'taskId':m.task_id,'turnId':m.turn_key,'sequence':m.sequence,'role':m.role,'text':m.text,
                         'status':m.status,'checkpointId':m.checkpoint_id,'modelName':m.model_name} for m in reversed(rows)]}
    return {'items':chat.messages(db,user.id,identity,before=before)}

@router.patch('/world-chats/{identity}')
def update_chat(identity:str,data:ChatUpdate,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world,session=chat.source(db,user.id,identity,lock=True)
    def update():
        if session.version!=data.expectedVersion:raise Problem('CHAT_VERSION_CONFLICT','对话已在其他页面更新，请重新打开',409)
        if session.pending_task_id:raise Problem('CHAT_PENDING','请先等待当前回复结束',409)
        if data.name is not None:
            if not data.name.strip():raise Problem('CHAT_NAME_REQUIRED','请填写对话名称',422)
            session.name=data.name.strip()
        if data.status is not None:session.status=data.status
        if 'personaId' in data.model_fields_set:
            session.persona_id=card_personas.get_persona(db,user.id,data.personaId).id if data.personaId else None
        if data.updateSource:
            _,line=require_line(db,user.id,world.id,session.line_id)
            head=checked_checkpoint(db,user.id,world.id,line.head_checkpoint_id)
            if session.checkpoint_id not in {c.id for c in ancestry(db,head,user.id)}:raise Problem('CHAT_SOURCE_INVALID','来源世界线已经变化，请另开对话',409)
            session.snapshot,session.denied_phrases=chat.snapshot(db,user.id,world,line,head.id,session.character_id);session.checkpoint_id=head.id
        session.version+=1;session.updated_at=now();db.flush();return chat.present(db,session)
    return idempotent(db,user.id,'play-controls:update-chat:'+identity,request.headers.get('idempotency-key'),data.model_dump(),update)

def ensure_idle(db,session):
    if session.pending_task_id:
        pending=db.get(Task,session.pending_task_id)
        if pending and (pending.state not in TERMINAL or pending.persistence_state=='recoverable'):
            raise Problem('CHAT_PENDING','当前回复还在处理中',409)

def next_sequence(db,identity):
    return (db.scalar(select(func.max(WorldChatMessage.sequence)).where(WorldChatMessage.session_id==identity)) or 0)+1

def add_placeholder(db,user,identity,session,task,turn_key,sequence):
    db.add(WorldChatMessage(owner_id=user.id,session_id=identity,task_id=task.id,turn_key=turn_key,sequence=sequence,
        role='assistant',text='',status='pending',checkpoint_id=session.checkpoint_id,model_name=task.payload.get('modelName','')))

def queue_reply(db,user,world,session,message,turn_key,history):
    from .recall import recollect
    _,line=require_line(db,user.id,world.id,session.line_id)
    session.snapshot,session.denied_phrases=chat.snapshot(db,user.id,world,line,session.checkpoint_id,session.character_id)
    recalled=recollect(db,session,message)
    task=create_job(db,user.id,'world_character_chat',{'worldId':world.id,'lineId':session.line_id,'chatId':session.id,
        'chatTurnKey':turn_key,'parentCheckpointId':session.checkpoint_id,'sourceVersion':session.version,
        'chatSnapshot':session.snapshot,'messages':[{'role':m['role'],'text':m['text']} for m in history],
        'message':message,'conversationMemory':recalled},['generation','validation'])
    task.checkpoint={**task.checkpoint,'worldDeniedPhrases':session.denied_phrases}
    session.pending_task_id=task.id;session.updated_at=now()
    return task

@router.get('/world-chats/{identity}/injection')
def chat_injection_view(identity:str,before:int|None=Query(None,ge=1),user=Depends(current_user),db=Depends(get_db)):
    _,session=chat.source(db,user.id,identity)
    card=card_space.card_for_world(db,user.id,session.world_id)
    persona=card_personas.persona_for_chat(db,user.id,session.id)
    persona_view={'id':persona.id,'name':persona.name} if persona is not None else None
    if card is None:
        return {'cardId':None,'lore':[],'memories':[],'persona':persona_view,'preset':None,'systemSuffix':''}
    rows=chat.messages(db,user.id,identity,before=before,limit=60)
    texts=[row['text'] for row in rows if row['role']=='user' and (row.get('text') or '').strip()]
    report=card_space.injection_report(db,user.id,card,texts[-1] if texts else '',
        list(reversed(texts))[:4],persona)
    report['cardId']=card.id
    return report

@router.get('/world-chats/{identity}/export')
def export_chat(identity:str,format:str=Query('jsonl',pattern='^(jsonl|json)$'),
                user=Depends(current_user),db=Depends(get_db)):
    _,session=chat.source(db,user.id,identity)
    rows=list(db.scalars(select(WorldChatMessage).where(WorldChatMessage.owner_id==user.id,
        WorldChatMessage.session_id==identity).order_by(WorldChatMessage.sequence.asc())))
    if format=='json':
        body=json.dumps([{'role':row.role,'text':row.text,'sequence':row.sequence,
            'modelName':row.model_name or '','createdAt':row.created_at.isoformat()} for row in rows],
            ensure_ascii=False,indent=2)
        return Response(content=body,media_type='application/json',
            headers=card_exporting.filename_header(session.name,'.json'))
    return Response(content=card_exporting.chat_jsonl(rows),media_type='application/x-ndjson',
        headers=card_exporting.filename_header(session.name,'.jsonl'))

@router.post('/world-chats/{identity}/send',status_code=202)
def send_chat(identity:str,data:SendInput,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world,session=chat.source(db,user.id,identity,lock=True)
    if not data.message.strip():raise Problem('MESSAGE_REQUIRED','请输入消息',422)
    if session.status!='active':raise Problem('CHAT_ARCHIVED','请先恢复这段对话再发送消息',409)
    def send():
        previous=db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id==identity,WorldChatMessage.turn_key==data.turnId,WorldChatMessage.role=='user'))
        if previous:
            if previous.text!=data.message.strip():raise Problem('IDEMPOTENCY_CONFLICT','同一条消息的内容发生变化',409)
            return {'taskId':previous.task_id,'chatId':identity,'worldId':world.id,'reused':True}
        ensure_idle(db,session)
        if world.status!='active':raise Problem('WORLD_ARCHIVED','世界已归档，可先阅读已有聊天',409)
        # Upgrade old conversations at their original frozen timepoint. This
        # never advances the source to the line head or writes canon state.
        _,line=require_line(db,user.id,world.id,session.line_id)
        session.snapshot,session.denied_phrases=chat.snapshot(db,user.id,world,line,session.checkpoint_id,session.character_id)
        history=chat.messages(db,user.id,identity,limit=30)
        from .recall import recollect
        recalled=recollect(db,session,data.message.strip())
        task=create_job(db,user.id,'world_character_chat',{'worldId':world.id,'lineId':session.line_id,'chatId':identity,
            'chatTurnKey':data.turnId,'parentCheckpointId':session.checkpoint_id,'sourceVersion':session.version,
            'chatSnapshot':session.snapshot,'messages':[{'role':m['role'],'text':m['text']} for m in history],
            'message':data.message.strip(),'conversationMemory':recalled},['generation','validation'])
        task.checkpoint={**task.checkpoint,'worldDeniedPhrases':session.denied_phrases}
        sequence=next_sequence(db,identity)
        db.add(WorldChatMessage(owner_id=user.id,session_id=identity,task_id=task.id,turn_key=data.turnId,sequence=sequence,
            role='user',text=data.message.strip(),status='saved',checkpoint_id=session.checkpoint_id,model_name=task.payload.get('modelName','')))
        add_placeholder(db,user,identity,session,task,data.turnId,sequence+1)
        session.pending_task_id=task.id;session.updated_at=now();db.flush()
        return {'taskId':task.id,'chatId':identity,'worldId':world.id}
    return idempotent(db,user.id,'play-controls:send-chat:'+identity,request.headers.get('idempotency-key'),data.model_dump(),send)

@router.post('/world-chats/{identity}/regenerate',status_code=202)
def regenerate_chat(identity:str,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world,session=chat.source(db,user.id,identity,lock=True)
    if session.status!='active':raise Problem('CHAT_ARCHIVED','请先恢复这段对话再生成回复',409)
    ensure_idle(db,session)
    pending=db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id==identity,WorldChatMessage.role=='assistant',WorldChatMessage.status=='pending'))
    if pending:raise Problem('CHAT_PENDING','当前回复还在处理中',409)
    last=db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id==identity,WorldChatMessage.owner_id==user.id,
        WorldChatMessage.role=='assistant',WorldChatMessage.status!='pending').order_by(WorldChatMessage.sequence.desc()).limit(1))
    if not last:raise Problem('NOTHING_TO_REGENERATE','没有可重新生成的回复',409)
    def regenerate():
        current=db.get(WorldChatMessage,last.id)
        if not current:raise Problem('NOTHING_TO_REGENERATE','没有可重新生成的回复',409)
        user_message=db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id==identity,WorldChatMessage.turn_key==current.turn_key,
            WorldChatMessage.role=='user').with_for_update())
        if not user_message:raise Problem('NOTHING_TO_REGENERATE','没有可重新生成的回复',409)
        history=chat.messages(db,user.id,identity,before=user_message.sequence)
        db.delete(current);db.flush()
        task=queue_reply(db,user,world,session,user_message.text,current.turn_key,history)
        user_message.task_id=task.id
        add_placeholder(db,user,identity,session,task,current.turn_key,next_sequence(db,identity))
        db.flush()
        return {'taskId':task.id}
    return idempotent(db,user.id,'play-controls:regenerate:'+identity,request.headers.get('idempotency-key') or 'last:'+last.id,{},regenerate)

@router.patch('/world-chats/{identity}/messages/{sequence}',status_code=202)
def edit_message(identity:str,sequence:int,data:MessageEditInput,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world,session=chat.source(db,user.id,identity,lock=True)
    if session.status!='active':raise Problem('CHAT_ARCHIVED','请先恢复这段对话再编辑消息',409)
    text=data.text.strip()
    if not text:raise Problem('MESSAGE_REQUIRED','请输入消息',422)
    row=db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id==identity,WorldChatMessage.owner_id==user.id,WorldChatMessage.sequence==sequence))
    if not row:raise Problem('RESOURCE_NOT_FOUND','消息不存在或不可访问',404)
    if row.role!='user':raise Problem('MESSAGE_NOT_EDITABLE','只能编辑自己发送的消息',422)
    pending=db.scalar(select(WorldChatMessage).where(WorldChatMessage.session_id==identity,WorldChatMessage.role=='assistant',WorldChatMessage.status=='pending'))
    if pending:raise Problem('CHAT_PENDING','当前回复还在处理中',409)
    def edit():
        current=db.get(WorldChatMessage,row.id)
        if not current:raise Problem('RESOURCE_NOT_FOUND','消息不存在或不可访问',404)
        db.execute(delete(WorldChatMessage).where(WorldChatMessage.session_id==identity,WorldChatMessage.sequence>sequence))
        db.flush()
        current.text=text
        history=chat.messages(db,user.id,identity,before=sequence)
        task=queue_reply(db,user,world,session,text,current.turn_key,history)
        current.task_id=task.id
        add_placeholder(db,user,identity,session,task,current.turn_key,sequence+1)
        db.flush()
        return {'taskId':task.id,'sequence':sequence}
    return idempotent(db,user.id,'play-controls:edit-message:'+identity+':'+str(sequence),request.headers.get('idempotency-key') or 'edit:'+digest(text)[:16],data.model_dump(),edit)

@router.post('/world-chats/{identity}/fork',status_code=201)
def fork_chat(identity:str,data:ForkInput,request:Request,user=Depends(current_user),db=Depends(get_db)):
    world,session=chat.source(db,user.id,identity,lock=True)
    if session.status!='active':raise Problem('CHAT_ARCHIVED','请先恢复这段对话再创建分支',409)
    def fork():
        rows=list(db.scalars(select(WorldChatMessage).where(WorldChatMessage.owner_id==user.id,
            WorldChatMessage.session_id==identity,WorldChatMessage.sequence<=data.sequence).order_by(WorldChatMessage.sequence)))
        if not rows:raise Problem('FORK_POINT_INVALID','分支位置不存在，请选择一条已有消息',422)
        if any(row.status=='pending' for row in rows):raise Problem('CHAT_PENDING','当前回复还在处理中',409)
        name=session.name+'（分支）'
        if len(name)>120:name=name[:120-len('（分支）')]+'（分支）'
        branch=WorldChatSession(id=uid(),owner_id=user.id,world_id=session.world_id,line_id=session.line_id,
            checkpoint_id=session.checkpoint_id,character_id=session.character_id,character_name=session.character_name,
            persona_id=session.persona_id,name=name,snapshot=deepcopy(session.snapshot),
            denied_phrases=deepcopy(session.denied_phrases or []),version=1)
        db.add(branch);db.flush()
        for row in rows:
            db.add(WorldChatMessage(owner_id=user.id,session_id=branch.id,task_id=row.task_id,turn_key=row.turn_key,
                sequence=row.sequence,role=row.role,text=row.text,status=row.status,checkpoint_id=row.checkpoint_id,
                model_name=row.model_name or ''))
        db.flush()
        return {'sessionId':branch.id,'name':branch.name}
    return idempotent(db,user.id,'play-controls:fork:'+identity,request.headers.get('idempotency-key') or 'fork:'+str(data.sequence),data.model_dump(),fork)
