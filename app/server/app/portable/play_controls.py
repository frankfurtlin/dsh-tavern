"""Portable independent conversations and editorial memory, outside canon."""
from copy import deepcopy
from ..models import Task, uid
from ..play_controls.models import PlayControl, MemoryRevision, WorldChatSession, WorldChatMessage
from .errors import ArchiveError


def invalid():
    raise ArchiveError('BACKUP_PLAY_CONTROLS_INVALID')


def scope_type(kind):
    if kind not in {'line','chat'}: invalid()
    return 'world_line' if kind=='line' else 'world_chat'


def collect(selected, rows, world_ids):
    selected[WorldChatSession]=rows(WorldChatSession,[WorldChatSession.world_id.in_(world_ids)])
    chat_ids={c.id for c in selected[WorldChatSession]}
    selected[Task]=[t for t in selected[Task] if t.task_type!='world_character_chat' or t.payload.get('chatId') in chat_ids]
    from ..models import TaskEvent
    task_ids={t.id for t in selected[Task]}
    if TaskEvent in selected:selected[TaskEvent]=[e for e in selected[TaskEvent] if e.task_id in task_ids]
    selected[WorldChatMessage]=rows(WorldChatMessage,[WorldChatMessage.session_id.in_(chat_ids)])
    selected[PlayControl]=rows(PlayControl,[PlayControl.world_id.in_(world_ids)])
    controls={c.id for c in selected[PlayControl]}
    selected[MemoryRevision]=rows(MemoryRevision,[MemoryRevision.control_id.in_(controls)])


def capture_received(selected, streams):
    """Include a newer durable reply even when its SQL message mirror failed.

    Only detached export copies change. Import keeps the received text and
    stops historical work; it does not claim a live reply finished successfully.
    """
    records=list(selected[WorldChatMessage])
    sessions={s.id:s for s in selected[WorldChatSession]}
    for task in selected[Task]:
        if task.task_type!='world_character_chat':continue
        session=sessions.get(task.payload.get('chatId'))
        stream=streams[task.id].get('worldStream',{})
        if not session or not stream.get('text','').strip():continue
        turn=task.payload.get('chatTurnKey')
        if not isinstance(turn,str) or not turn:invalid()
        existing=next((m for m in records if m.session_id==session.id and m.task_id==task.id and m.role=='assistant'),None)
        if existing and existing.text==stream['text']:continue
        if existing:
            records.remove(existing)
            message=WorldChatMessage(**{c.key:deepcopy(getattr(existing,c.key)) for c in WorldChatMessage.__table__.columns})
        else:
            sequence=max((m.sequence for m in records if m.session_id==session.id),default=0)+1
            message=WorldChatMessage(id=uid(),owner_id=session.owner_id,session_id=session.id,task_id=task.id,
                turn_key=turn,sequence=sequence,role='assistant',checkpoint_id=task.payload['parentCheckpointId'],
                model_name=task.payload.get('modelName',''),created_at=task.created_at)
        message.text=stream['text'];message.status='saved' if task.state=='succeeded' and task.result_outcome=='complete' else 'interrupted'
        records.append(message)
    selected[WorldChatMessage]=records


def validate(rows, ancestor):
    chats=rows.get('world_chat',{});lines=rows.get('world_line',{});points=rows.get('checkpoint',{})
    for chat in chats.values():
        if chat.status not in ('active','archived'): invalid()
        line=lines.get(chat.line_id);point=points.get(chat.checkpoint_id)
        if (not line or not point or line.world_id!=chat.world_id or point.world_id!=chat.world_id
                or not ancestor(point.id,line.head_checkpoint_id) or chat.version<1 or not chat.name.strip()):invalid()
        from ..world.state import public_view
        if not any(e['id']==chat.character_id and e['type']=='character' for e in public_view(point.state,point.definition)['entities']):invalid()
    scopes=set();controls=rows.get('play_control',{})
    def memory(value):
        if not isinstance(value,dict) or set(value)!={'summary','notes'} or any(not isinstance(v,str) or len(v)>4000 for v in value.values()):invalid()
    for control in controls.values():
        target=rows.get(scope_type(control.scope_kind),{}).get(control.scope_id)
        key=(control.scope_kind,control.scope_id)
        if not target or target.world_id!=control.world_id or key in scopes or control.version<1:invalid()
        memory(control.memory);scopes.add(key)
    versions={}
    for revision in rows.get('play_memory_revision',{}).values():
        control=controls.get(revision.control_id)
        key=(revision.control_id,revision.version)
        if not control or not 1<=revision.version<=control.version or key in versions:invalid()
        memory(revision.memory);versions[key]=revision.memory
    for control in controls.values():
        if versions.get((control.id,control.version))!=control.memory:invalid()
    sequences=set();turns=set();user_tasks={}
    for message in rows.get('world_chat_message',{}).values():
        chat=chats.get(message.session_id);task=rows.get('task',{}).get(message.task_id);point=points.get(message.checkpoint_id)
        if (not chat or not task or task.task_type!='world_character_chat' or not point
                or point.world_id!=chat.world_id or not ancestor(point.id,chat.checkpoint_id)
                or task.payload.get('chatId')!=chat.id or task.payload.get('worldId')!=chat.world_id
                or task.payload.get('lineId')!=chat.line_id or task.payload.get('chatTurnKey')!=message.turn_key
                or task.payload.get('parentCheckpointId')!=point.id or message.sequence<1
                or not message.turn_key or message.role not in {'user','assistant'}
                or message.status not in {'saved','partial','interrupted','stopped','failed','cancelled'}):invalid()
        seq=(chat.id,message.sequence);turn=(chat.id,message.turn_key,message.role)
        if seq in sequences or turn in turns:invalid()
        sequences.add(seq);turns.add(turn)
        if message.role=='user':user_tasks[task.id]=message.sequence
    for message in rows.get('world_chat_message',{}).values():
        if message.role=='assistant' and user_tasks.get(message.task_id,message.sequence)>=message.sequence:invalid()
    for task in rows.get('task',{}).values():
        if task.task_type=='world_character_chat':
            chat=chats.get(task.payload.get('chatId'))
            if (not chat or task.payload.get('worldId')!=chat.world_id or task.payload.get('lineId')!=chat.line_id
                    or task.id not in user_tasks or task.result.get('chatId',chat.id)!=chat.id):invalid()


def rebuild_sources(db,rows,owner):
    from ..play_controls.chat import snapshot
    from ..errors import Problem
    for chat in rows.get('world_chat',{}).values():
        try:
            chat.snapshot,chat.denied_phrases=snapshot(db,owner,rows['world'][chat.world_id],rows['world_line'][chat.line_id],chat.checkpoint_id,chat.character_id)
        except Problem as error:
            raise ArchiveError('BACKUP_PLAY_CONTROLS_INVALID') from error
        chat.character_name=chat.snapshot['character']['name']
        chat.pending_task_id=None
