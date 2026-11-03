"""Numeric accounting and authorized labels only; never read a prose journal."""
import math
import re
from sqlalchemy import select, or_, func, case, cast, Numeric, and_
from .models import Book, BookDeletion, RoleSession, Task, UsageRecord, ModelProfile
from .errors import Problem


def billing(value):
    """Reasoning is a subset of completion tokens, not another output charge."""
    value=value if isinstance(value,dict) else {}
    details=value.get('completion_tokens_details')
    total=value.get('completion_tokens')
    reasoning=details.get('reasoning_tokens') if isinstance(details,dict) else None
    if type(reasoning) is not int or not 0<=reasoning<=9007199254740991 or type(total) is not int or reasoning>total:
        reasoning=None
    amount=None; currency=None
    cost=value.get('cost')
    if isinstance(cost,dict) and type(cost.get('amount')) in (int,float) and 0<=cost['amount']<=9007199254740991 and math.isfinite(cost['amount']) and isinstance(cost.get('currency'),str) and re.fullmatch('[A-Z]{3}',cost['currency']):
        amount,currency=cost['amount'],cost['currency']
    return {'reasoningTokens':reasoning,'reasoningIncludedInOutput':True if reasoning is not None else None,
        'cost':amount,'currency':currency,'costState':'known' if amount is not None else 'unknown'}


def normalized_billing(usage):
    """Defend persisted metadata too. Legacy unknown fields never become zero."""
    usage=usage if isinstance(usage,dict) else {}
    normalized=billing({'completion_tokens':usage.get('outputTokens'),
        'completion_tokens_details':{'reasoning_tokens':usage.get('reasoningTokens')},
        'cost':{'amount':usage.get('cost'),'currency':usage.get('currency')}})
    return normalized


def world_schema_available(db):
    return db.scalar(select(func.to_regclass('world.instances'))) is not None


def story_for_task(db,task,lookup=None):
    from .world.models import World
    payload=task.payload if isinstance(task.payload,dict) else {}
    result=task.result if isinstance(task.result,dict) else {}
    world_id=payload.get('worldId') or (result.get('worldId') if result.get('deleted') is True else None)
    if isinstance(world_id,str):
        world=lookup['worlds'].get(world_id) if lookup is not None else (db.scalar(select(World).where(World.owner_id==task.owner_id,World.id==world_id)) if world_schema_available(db) else None)
        if world:return {'id':'world-'+world.id,'kind':'world','title':world.name,'removed':False}
        if result.get('deleted') is True and result.get('worldId')==world_id:
            return {'id':'world-'+world_id,'kind':'world','title':'已移除的世界','removed':True}
    from .book_deletion import task_book
    book_id=task_book(db,task)
    if not book_id and isinstance(payload.get('sessionId'),str):
        session=lookup['sessions'].get(payload['sessionId']) if lookup is not None else db.scalar(select(RoleSession).where(RoleSession.owner_id==task.owner_id,RoleSession.id==payload['sessionId']))
        if session:book_id=session.book_id
    if book_id:
        book=lookup['books'].get(book_id) if lookup is not None else db.scalar(select(Book).where(Book.owner_id==task.owner_id,Book.id==book_id))
        if book:return {'id':'book-'+book.id,'kind':'book','title':book.title,'removed':False}
        deleted=book_id in lookup['deleted'] if lookup is not None else db.scalar(select(BookDeletion).where(BookDeletion.owner_id==task.owner_id,BookDeletion.book_id==book_id))
        if deleted:return {'id':'book-'+book_id,'kind':'book','title':'已移除的故事','removed':True}
    return None


def frozen_labels(db,task,profile):
    return {'story':story_for_task(db,task),'profile':{'id':profile.id,'name':profile.name,'modelId':profile.model_id,'version':profile.version}}


def query(db,owner,*,task_id=None,story_id=None,profile_id=None):
    filters=[UsageRecord.owner_id==owner]
    if task_id:
        if not db.scalar(select(Task.id).where(Task.id==task_id,Task.owner_id==owner)):
            raise Problem('RESOURCE_NOT_FOUND','内容不存在',404)
        filters.append(UsageRecord.task_id==task_id)
    if profile_id:
        if not db.scalar(select(ModelProfile.id).where(ModelProfile.id==profile_id,ModelProfile.owner_id==owner)):
            raise Problem('RESOURCE_NOT_FOUND','内容不存在',404)
        filters.append(UsageRecord.profile_id==profile_id)
    if story_id:
        if not re.fullmatch(r'(book|world)-[0-9a-f]{32}',story_id):
            raise Problem('RESOURCE_NOT_FOUND','内容不存在',404)
        kind,identity=story_id.split('-',1)
        if kind=='world':
            if not world_schema_available(db):raise Problem('RESOURCE_NOT_FOUND','内容不存在',404)
            from .world.models import World
            exists=db.scalar(select(World.id).where(World.owner_id==owner,World.id==identity)) or db.scalar(select(Task.id).where(
                Task.owner_id==owner,Task.result['worldId'].astext==identity,Task.result['deleted'].astext=='true').limit(1))
            scope=or_(Task.payload['worldId'].astext==identity,Task.result['worldId'].astext==identity)
        else:
            exists=db.scalar(select(Book.id).where(Book.owner_id==owner,Book.id==identity)) or db.scalar(select(BookDeletion.id).where(BookDeletion.owner_id==owner,BookDeletion.book_id==identity))
            sessions=select(RoleSession.id).where(RoleSession.owner_id==owner,RoleSession.book_id==identity)
            scope=and_(Task.payload['worldId'].astext.is_(None),Task.result['worldId'].astext.is_(None),or_(Task.payload['bookId'].astext==identity,Task.payload['anchor']['bookId'].astext==identity,
                Task.payload['operation']['anchor']['bookId'].astext==identity,Task.result['bookId'].astext==identity,
                Task.result['duplicateBookId'].astext==identity,Task.payload['sessionId'].astext.in_(sessions)))
        if not exists:raise Problem('RESOURCE_NOT_FOUND','内容不存在',404)
        tasks=select(Task.id).where(Task.owner_id==owner,scope)
        filters.append(or_(UsageRecord.task_id.in_(tasks),UsageRecord.billing_metadata['story']['id'].astext==story_id))
    return select(UsageRecord).where(*filters)


def item(db,row,lookup=None):
    task=lookup['tasks'].get(row.task_id) if lookup is not None else db.scalar(select(Task).where(Task.owner_id==row.owner_id,Task.id==row.task_id))
    profile=lookup['profiles'].get(row.profile_id) if lookup is not None else db.scalar(select(ModelProfile).where(ModelProfile.owner_id==row.owner_id,ModelProfile.id==row.profile_id))
    metadata=row.billing_metadata if isinstance(row.billing_metadata,dict) else {}
    configured=(task.checkpoint or {}).get('providerConfig',{}) if task else {}
    safe_profile={'id':row.profile_id,'name':profile.name if profile else '已移除的通道',
        'modelId':profile.model_id if profile else None,'version':None}
    if isinstance(configured,dict):
        if isinstance(configured.get('provider'),str):safe_profile['name']=configured['provider'][:100]
        if isinstance(configured.get('modelId'),str):safe_profile['modelId']=configured['modelId'][:100]
        if type(configured.get('profileVersion')) is int:safe_profile['version']=configured['profileVersion']
    frozen=metadata.get('profile') if isinstance(metadata,dict) else None
    if isinstance(frozen,dict) and frozen.get('id')==row.profile_id:
        for key in ('name','modelId'):
            if isinstance(frozen.get(key),str):safe_profile[key]=frozen[key][:100]
        if type(frozen.get('version')) is int:safe_profile['version']=frozen['version']
    # Re-authorize a current story rather than trusting arbitrary persisted JSON.
    story=story_for_task(db,task,lookup) if task else None
    details=metadata.get('billing') if isinstance(metadata.get('billing'),dict) else {}
    phase=row.operation if re.fullmatch(r'[a-zA-Z0-9_:-]{1,60}',row.operation or '') else 'unknown'
    return {'id':row.id,'taskId':row.task_id,'providerCallId':row.provider_call_id,'profileId':row.profile_id,
        'operation':phase,'phase':phase,'task':{'id':row.task_id,'type':task.task_type if task else 'historical','state':task.state if task else 'unknown'},
        'story':story,'profile':safe_profile,'reservedInput':row.reserved_input,'reservedOutput':row.reserved_output,
        'inputTokens':row.input_tokens,'outputTokens':row.output_tokens,'usageState':row.usage_state,'status':row.status,
        'createdAt':row.created_at.isoformat(),'latencyMs':row.latency_ms,
        **normalized_billing({**details,'outputTokens':row.output_tokens})}


def items(db,rows):
    """Batch the labels for one bounded page, rather than SELECT per record."""
    if not rows:return []
    owner=rows[0].owner_id
    if any(row.owner_id!=owner for row in rows):raise Problem('RESOURCE_NOT_FOUND','内容不存在',404)
    tasks={t.id:t for t in db.scalars(select(Task).where(Task.owner_id==owner,Task.id.in_({r.task_id for r in rows})))}
    profiles={p.id:p for p in db.scalars(select(ModelProfile).where(ModelProfile.owner_id==owner,ModelProfile.id.in_({r.profile_id for r in rows})))}
    session_ids={t.payload.get('sessionId') for t in tasks.values() if isinstance(t.payload,dict) and isinstance(t.payload.get('sessionId'),str)}
    sessions={s.id:s for s in db.execute(select(RoleSession.id,RoleSession.book_id).where(RoleSession.owner_id==owner,RoleSession.id.in_(session_ids)))} if session_ids else {}
    from .book_deletion import task_book
    book_ids={task_book(db,t) for t in tasks.values()}|{s.book_id for s in sessions.values()}
    book_ids.discard(None)
    # Fetch only labels for content resources, never their source/definition.
    books={b.id:b for b in db.execute(select(Book.id,Book.title).where(Book.owner_id==owner,Book.id.in_(book_ids)))} if book_ids else {}
    deleted=set(db.scalars(select(BookDeletion.book_id).where(BookDeletion.owner_id==owner,BookDeletion.book_id.in_(book_ids)))) if book_ids else set()
    world_ids={t.payload.get('worldId') or (t.result.get('worldId') if t.result.get('deleted') is True else None) for t in tasks.values()}
    world_ids.discard(None)
    from .world.models import World
    worlds={w.id:w for w in db.execute(select(World.id,World.name).where(World.owner_id==owner,World.id.in_(world_ids)))} if world_ids and world_schema_available(db) else {}
    lookup={'tasks':tasks,'profiles':profiles,'sessions':sessions,'books':books,'deleted':deleted,'worlds':worlds}
    return [item(db,row,lookup) for row in rows]


def summary(db,statement):
    # Two aggregate queries; never materialize the account's whole ledger.
    scoped=statement.with_only_columns(UsageRecord.status,UsageRecord.input_tokens,
        UsageRecord.output_tokens,UsageRecord.billing_metadata).subquery()
    c=scoped.c;details=c.billing_metadata['billing']
    numeric=lambda field:case((func.jsonb_typeof(details[field])=='number',cast(details[field].astext,Numeric)),else_=None)
    reasoning=numeric('reasoningTokens');amount=numeric('cost');currency=details['currency'].astext
    reasoning=case((and_(details['reasoningTokens'].astext.op('~')('^(0|[1-9][0-9]*)$'),reasoning>=0,reasoning<=9007199254740991,
        reasoning<=c.output_tokens,func.floor(reasoning)==reasoning),reasoning),else_=None)
    amount=case((and_(amount>=0,amount<=9007199254740991,currency.op('~')('^[A-Z]{3}$')),amount),else_=None)
    fields=[func.count().label('records'),func.count().filter(c.status!='not_sent').label('calls'),
        func.count().filter(c.status=='not_sent').label('notSentCalls'),
        func.count().filter(c.status=='intent').label('unconfirmedCalls'),
        (func.count()-func.count(amount)).label('unknownCostCalls')]
    for key,value in [('inputTokens',c.input_tokens),('outputTokens',c.output_tokens),('reasoningTokens',reasoning)]:
        fields.append(case((func.count(value)==func.count(),func.coalesce(func.sum(value),0)),else_=None).label(key))
    totals=dict(db.execute(select(*fields)).one()._mapping)
    totals['reasoningTokens']=int(totals['reasoningTokens']) if totals['reasoningTokens'] is not None else None
    costs={unit:float(total) for unit,total in db.execute(select(currency,func.sum(amount))
        .where(amount.is_not(None)).group_by(currency))}
    return {**totals,'knownCostByCurrency':costs,
        'note':'reasoning is included in output tokens; unknown is not zero'}


def options(db,owner):
    """Owner-scoped selectors. No task payload, result or provider body."""
    tasks=list(db.execute(select(Task.id,Task.task_type,Task.created_at).where(Task.owner_id==owner,
        Task.id.in_(select(UsageRecord.task_id).where(UsageRecord.owner_id==owner)))
        .order_by(Task.created_at.desc(),Task.id.desc()).limit(200)))
    profiles=list(db.execute(select(ModelProfile.id,ModelProfile.name,ModelProfile.model_id).where(ModelProfile.owner_id==owner)
        .order_by(ModelProfile.created_at.desc(),ModelProfile.id.desc())))
    books=list(db.execute(select(Book.id,Book.title).where(Book.owner_id==owner).order_by(Book.created_at.desc())))
    deleted=list(db.scalars(select(BookDeletion.book_id).where(BookDeletion.owner_id==owner)))
    stories={'book-'+b.id:{'id':'book-'+b.id,'kind':'book','title':b.title,'removed':False} for b in books}
    for book_id in deleted:
        stories.setdefault('book-'+book_id,{'id':'book-'+book_id,'kind':'book','title':'已移除的故事','removed':True})
    if world_schema_available(db):
        from .world.models import World
        worlds=db.execute(select(World.id,World.name).where(World.owner_id==owner).order_by(World.created_at.desc()))
        for world in worlds:
            stories['world-'+world.id]={'id':'world-'+world.id,'kind':'world','title':world.name,'removed':False}
    removed_worlds=db.scalars(select(Task.result['worldId'].astext).where(Task.owner_id==owner,
        Task.result['deleted'].astext=='true',Task.result['worldId'].astext.is_not(None)).distinct())
    for world_id in removed_worlds:
        if re.fullmatch('[0-9a-f]{32}',world_id):
            stories.setdefault('world-'+world_id,{'id':'world-'+world_id,'kind':'world','title':'已移除的世界','removed':True})
    return {'tasks':[{'id':t.id,'type':t.task_type,'createdAt':t.created_at.isoformat()} for t in tasks],
        'stories':list(stories.values()),'profiles':[{'id':p.id,'name':p.name,'modelId':p.model_id} for p in profiles],
        'taskLimit':200}
