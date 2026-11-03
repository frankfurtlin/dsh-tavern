"""Public role/world fragments: atomic private journal before database events.

Callers hold the task's lifecycle and lease row lock. The file contains only
the authorized public text projection, never director/reviewer/provider input.
"""
import json
import re
from sqlalchemy import select, case
from .domain import canonical, digest, utf16_len
from .storage import _write_task_journal, private_path
from .errors import Problem
from .models import User, Task
from .task_lease import assert_lease

_cursor=None
WORLD_STAGE=re.compile(r'^generation:(?:scene:([1-9][0-9]*)|rewrite)$')
FIELDS={'contentId','chapterIndex','streamVersion','snapshotVersion','text','contentHash','status','durability'}

def context(task, stage=None):
    if task.task_type=='role_chat' and task.checkpoint.get('chatStreamingPolicy')=='role-stream-v1':
        scope={'kind':'role','sessionId':task.payload['sessionId'],'turnId':task.payload['turnId']}
        stage='reply';index=1
    elif task.task_type=='world_character_chat':
        stage=stage or task.checkpoint.get('worldStream',{}).get('stage') or 'generation'
        if stage!='generation':return None
        index=1
        scope={'kind':'world-chat','worldId':task.payload['worldId'],'sessionId':task.payload['chatId'],'turnId':task.payload['chatTurnKey']}
    elif task.task_type.startswith('world_'):
        stage=stage or task.checkpoint.get('worldStream',{}).get('stage')
        match=WORLD_STAGE.fullmatch(stage or '')
        if not match:return None
        index=int(match[1] or 1)
        scope={'kind':'world','worldId':task.payload['worldId'],'lineId':task.payload.get('lineId'),'turnId':task.payload.get('turnId')}
    else:return None
    content_id=digest(task.id+':'+stage)[:32]
    return {'owner':task.owner_id,'taskId':task.id,'scope':scope,'stage':stage,'contentId':content_id,
            'chapterIndex':index,'name':f'journal-stream-{task.id}-{content_id}.json'}

def guard(db, task):
    assert_lease(task)
    user=db.get(User,task.owner_id)
    if task.result.get('deleted') or not user or user.status!='active':
        raise Problem('TASK_CANCELLED','内容已清理')

def read(ctx):
    if not ctx:return None
    path=private_path(ctx['owner'],ctx['name'])
    try:raw=path.read_text('utf-8')
    except FileNotFoundError:return None
    try:
        envelope=json.loads(raw);value=envelope['value']
        if set(envelope)!={'taskId','scope','stage','value','hash'} or any(envelope[k]!=ctx[k] for k in ('taskId','scope','stage')):raise ValueError()
        if set(value)!=FIELDS or value['contentId']!=ctx['contentId'] or value['chapterIndex']!=ctx['chapterIndex']:raise ValueError()
        if not isinstance(value['text'],str) or value['contentHash']!=digest(value['text']) or envelope['hash']!=digest(canonical(value)):raise ValueError()
        value['text'].encode('utf-8',errors='strict')
        if any(type(value[k]) is not int or not 1<=value[k]<=9007199254740991 for k in ('chapterIndex','streamVersion','snapshotVersion')):raise ValueError()
        if value['status'] not in ('streaming','complete','interrupted','stopped') or value['durability']!='journaled':raise ValueError()
        return value
    except (ValueError,KeyError,TypeError,UnicodeError):
        raise Problem('JOURNAL_INVALID','正文持久文件校验失败',409) from None

def overlay(task,key):
    stream=task.checkpoint.get(key,{})
    contexts=[context(task,stream.get('stage'))]
    if key=='worldStream' and task.task_type!='world_character_chat':
        if task.task_type=='world_rewrite':contexts=[context(task,'generation:rewrite')]
        else:contexts=[context(task,f'generation:scene:{i}') for i in range(1,min(task.payload.get('chapterCount',1),20)+1)]
    values=[(ctx,read(ctx)) for ctx in contexts if ctx]
    available=[(ctx,value) for ctx,value in values if value]
    ctx,value=max(available,key=lambda item:item[1]['snapshotVersion']) if available else (None,None)
    if value and value['snapshotVersion']>stream.get('snapshotVersion',0):
        return {**stream,**value,'version':max(stream.get('version',0),value['snapshotVersion']),
                **({'stage':ctx['stage']} if key=='worldStream' else {})}
    return stream

def persist(db,task,key,text,status='streaming',stage=None,extra=None):
    """Journal write happens with the caller's current claim/lifecycle locked."""
    from .worker import add_event
    guard(db,task)
    ctx=context(task,stage)
    if not ctx:raise Problem('STREAM_SCOPE_INVALID','正文阶段无效',409)
    current=task.checkpoint.get(key,{})
    previous=current if current.get('contentId')==ctx['contentId'] else {}
    retained=read(ctx)
    if retained and retained['snapshotVersion']>previous.get('snapshotVersion',0):previous=retained
    old_text=previous.get('text','')
    epoch=previous.get('streamVersion',1)+(0 if text.startswith(old_text) else 1)
    legacy_keys={'sessionId','turnId','userMessage','reviewWarnings'}
    extras_match=all(current.get(k)==v for k,v in (extra or {}).items()) and not ((set(current)&legacy_keys)-set(extra or {}))
    unchanged=text==old_text and status==previous.get('status') and extras_match
    if unchanged and retained:
        value=retained
    else:
        version=max(current.get('version',0),previous.get('snapshotVersion',0))+1
        value={'contentId':ctx['contentId'],'chapterIndex':ctx['chapterIndex'],'streamVersion':epoch,
            'snapshotVersion':version,'text':text,'contentHash':digest(text),'status':status,'durability':'journaled'}
        envelope={k:ctx[k] for k in ('taskId','scope','stage')}
        envelope.update(value=value,hash=digest(canonical(value)))
        _write_task_journal(ctx['owner'],ctx['taskId'],ctx['name'],canonical(envelope).encode('utf-8'))
    # An unchanged replay may still repair a failed database mirror.
    if current.get('snapshotVersion',0)>=value['snapshotVersion'] and all(current.get(k)==v for k,v in (extra or {}).items()):return current
    old=current if current.get('contentId')==ctx['contentId'] else {}
    stream={**(extra or {}),**value,'version':value['snapshotVersion']}
    if key=='worldStream':stream['stage']=ctx['stage']
    task.checkpoint={**task.checkpoint,key:stream}
    payload={k:value[k] for k in ('contentId','chapterIndex','streamVersion')}
    if value['text'].startswith(old.get('text','')) and value['streamVersion']==old.get('streamVersion',1):
        delta=value['text'][len(old.get('text','')):]
        if delta:
            compatibility={'stage':ctx['stage'],'offset':len(old.get('text','')),'text':delta,'version':value['snapshotVersion']} if key=='worldStream' else {}
            add_event(db,task,'text_delta',{**payload,'startUtf16':utf16_len(old.get('text','')),'delta':delta,'durability':'journaled',**compatibility})
    elif value['text']!=old.get('text','') or value['streamVersion']!=old.get('streamVersion',1):
        add_event(db,task,'phase',{**payload,'phase':'snapshot_replaced'})
    return stream

def saved(db,task,key,text,stage=None,extra=None):
    """Called in the same transaction as final body + candidate/message metadata."""
    from .worker import add_event
    if key=='worldStream':
        from .world.state import public_stream
        text=public_stream(text,task.checkpoint.get('worldDeniedPhrases',[]),True)
    old=task.checkpoint.get(key,{})
    value=persist(db,task,key,text,old.get('status','complete'),stage,extra or {k:old[k] for k in ('sessionId','turnId','userMessage','reviewWarnings') if k in old})
    if value.get('durability')=='saved':return
    value={**value,'durability':'saved'}
    task.checkpoint={**task.checkpoint,key:value}
    add_event(db,task,'chapter_saved',{k:value[k] for k in ('contentId','chapterIndex','streamVersion','snapshotVersion','contentHash','durability')})

def clean_deleted():
    """Bounded fair retry for committed tombstones; never unlink before DELETE commits."""
    from .db import SessionLocal
    global _cursor
    with SessionLocal() as db:
        statement=select(Task).where(Task.result['deleted'].astext=='true')
        if _cursor:statement=statement.order_by(case((Task.id>_cursor,0),else_=1))
        rows=list(db.scalars(statement.order_by(Task.id).limit(20).with_for_update(skip_locked=True)))
        for task in rows:
            # Install the existing writer fence before looking for a late fragment.
            marker=('world-deleted-' if task.task_type.startswith('world_') else 'book-deleted-')+task.id+'.marker'
            root=private_path(task.owner_id,marker).parent
            if root.exists():
                private_path(task.owner_id,marker).touch(mode=0o600)
                for file in root.glob('journal-stream-'+task.id+'-*.json'):file.unlink(missing_ok=True)
        if rows:_cursor=rows[-1].id
    return len(rows)

def event_payload(kind,payload):
    """Protocol metadata is opaque; never phrase-scrub IDs/enums/hashes.

    Text was filtered before journaling. The explicit projection also excludes
    new accidental private fields rather than trusting a blanket string scrub.
    """
    fields={
        'text_delta':{'contentId','chapterIndex','streamVersion','startUtf16','delta','durability'},
        'chapter_saved':{'contentId','chapterIndex','streamVersion','snapshotVersion','contentHash','durability'},
        'phase':{'contentId','chapterIndex','streamVersion','phase','state','errorCode','resultOutcome'},
        'content_hint':{'contentId','chapterIndex','streamVersion','code'},
        'usage':{'callId','operation','inputTokens','outputTokens','reasoningTokens','usageState','status','latencyMs'},
        'terminal':{'state','errorCode','resultOutcome','outcome','providerState','persistenceState','acceptedPrefix','fee'},
    }.get(kind)
    return {k:v for k,v in payload.items() if k in fields} if fields is not None else payload
