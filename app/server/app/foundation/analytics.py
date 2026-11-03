"""Admin-only analytics from received events and durable execution receipts.

Client actions measure UI intent. Tasks, usage and checkpoints provide outcomes.
No dialogue bodies, model credentials or source text enter this projection.
"""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,time,timezone
from decimal import Decimal,InvalidOperation
from math import ceil
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,Query
from sqlalchemy import select,func,text
from ..db import get_db,SessionLocal
from ..models import User,Task,TaskEvent,UsageRecord,now
from ..security import current_user
from ..model_budget import unlimited_usage
from .core import require_admin,config
from .models import Account,Event

router=APIRouter(prefix='/product/analytics',tags=['analytics'])
SHANGHAI=ZoneInfo('Asia/Shanghai')
BUSINESS_EVENTS={'work.open','play.enter','dialogue.open','dialogue.close','route.select','route.cancel',
    'route.confirm','scene.ready','scene.error','action.submit','model.saved','memory.saved','page.engaged',
    'book.import.started','book.import.confirmed','author.saved','publication.submitted','chat.created','chat.sent'}


def distribution(values):
    values=sorted(v for v in values if v is not None and v>=0)
    return {'samples':len(values),'medianMs':values[(len(values)-1)//2] if values else None,
        'p95Ms':values[max(0,ceil(len(values)*.95)-1)] if values else None}


def report(db,days,include_admins=False):
    end=now();today=end.astimezone(SHANGHAI).date()
    start=datetime.combine(today-timedelta(days=days-1),time.min,SHANGHAI).astimezone(timezone.utc)
    users=list(db.execute(select(User.id,User.username,User.created_at,User.status,Account.role).outerjoin(Account,Account.owner_id==User.id)))
    excluded=set(config(db,'beta-cohorts').get('excludedUserIds',[]))
    owners={u.id for u in users if u.status=='active' and u.id not in excluded and (include_admins or u.role!='ADMIN')}
    # Bounded projections make a busy API safe; truncation is explicit in the UI.
    events=list(db.scalars(select(Event).where(Event.created_at>=start,Event.created_at<=end,
        Event.owner_id.in_(owners)|(Event.owner_id.is_(None))).order_by(Event.created_at.desc(),Event.id).limit(30001)))
    truncated_events=len(events)>30000;events=events[:30000]
    tasks=list(db.execute(select(Task.id,Task.owner_id,Task.created_at,Task.task_type,Task.state,Task.error_code,
        Task.provider_state,Task.payload['worldId'].astext.label('world_id'),Task.payload['interactionMode'].astext.label('interaction_mode'),
        Task.payload['sceneIntent']['kind'].astext.label('intent_kind')).where(Task.owner_id.in_(owners),Task.created_at>=start,Task.created_at<=end)
        .order_by(Task.created_at.desc(),Task.id).limit(10001)))
    truncated_tasks=len(tasks)>10000;tasks=tasks[:10000];task_ids=[t.id for t in tasks]
    usages=list(db.execute(select(UsageRecord.task_id,UsageRecord.owner_id,UsageRecord.profile_id,UsageRecord.operation,
        UsageRecord.status,UsageRecord.usage_state,UsageRecord.input_tokens,UsageRecord.output_tokens,UsageRecord.latency_ms,
        UsageRecord.billing_metadata).where(UsageRecord.owner_id.in_(owners),UsageRecord.created_at>=start,UsageRecord.created_at<=end)
        .order_by(UsageRecord.created_at.desc(),UsageRecord.id).limit(50001)))
    truncated_usage=len(usages)>50000;usages=usages[:50000]
    first_text=dict(db.execute(select(TaskEvent.task_id,func.min(TaskEvent.created_at)).where(TaskEvent.task_id.in_(task_ids),
        TaskEvent.type=='text_delta').group_by(TaskEvent.task_id)).all())
    terminal=dict(db.execute(select(TaskEvent.task_id,func.min(TaskEvent.created_at)).where(TaskEvent.task_id.in_(task_ids),TaskEvent.type=='terminal').group_by(TaskEvent.task_id)).all())
    task_states=Counter(t.state for t in tasks);errors=Counter(t.error_code for t in tasks if t.error_code)
    pages=Counter();page_users=defaultdict(set);active=set();sessions=set();visitors=set();actions=Counter();action_users=defaultdict(set)
    client_errors=Counter();client_latency=[]
    engaged_ms=0;trends={str(today-timedelta(days=i)):{'day':str(today-timedelta(days=i)),'users':set(),'pageViews':0,'tasks':0,'succeeded':0,'failed':0} for i in range(days)}
    work_views=Counter();work_users=defaultdict(set);last_seen={};by_owner=defaultdict(list)
    for event in events:
        day=str(event.created_at.astimezone(SHANGHAI).date());owner=event.owner_id
        if event.category=='page_view':
            pages[event.path]+=1;visitors.add(event.anon_id or owner or event.session_id or event.id)
            if event.session_id:sessions.add((event.anon_id or owner,event.session_id))
            if day in trends:trends[day]['pageViews']+=1
            if owner:page_users[event.path].add(owner)
        if owner and (event.category in ('page_view','fact') or event.name in BUSINESS_EVENTS):
            active.add(owner);last_seen[owner]=max(last_seen.get(owner,event.created_at),event.created_at)
            if day in trends:trends[day]['users'].add(owner)
        if event.name in BUSINESS_EVENTS:
            actions[event.name]+=1
            if owner:action_users[event.name].add(owner)
        duration=(event.data or {}).get('durationMs')
        if event.name=='page.engaged' and isinstance(duration,(int,float)):engaged_ms+=min(3600000,max(0,duration))
        meta=(event.data or {}).get('metadata') or {}
        status=(event.data or {}).get('statusCode')
        if event.category=='api' and isinstance(duration,(int,float)):client_latency.append(duration)
        if event.category=='error' or event.category=='api' and isinstance(status,int) and status>=400:
            client_errors[(event.path,status,meta.get('errorCode') or 'CLIENT_ERROR')]+=1
        if owner:
            by_owner[owner].append((event.created_at,event.name,event.category))
            if event.name in ('play.enter','work.open') and meta.get('worldId'):
                work_views[(owner,meta['worldId'])]+=1;work_users[(owner,meta['worldId'])].add(owner)
    per_user=defaultdict(lambda:Counter());work_tasks=defaultdict(lambda:Counter())
    for task in tasks:
        active.add(task.owner_id);last_seen[task.owner_id]=max(last_seen.get(task.owner_id,task.created_at),task.created_at)
        day=str(task.created_at.astimezone(SHANGHAI).date())
        if day in trends:
            trends[day]['users'].add(task.owner_id);trends[day]['tasks']+=1
            if task.state in ('succeeded','failed'):trends[day][task.state]+=1
        per_user[task.owner_id]['tasks']+=1
        per_user[task.owner_id][task.state]+=1
        if task.world_id:
            work_tasks[(task.owner_id,task.world_id)]['tasks']+=1
            work_tasks[(task.owner_id,task.world_id)][task.state]+=1
        by_owner[task.owner_id].append((task.created_at,'task.accepted','task'))
        if task.state=='succeeded' and task.id in terminal:by_owner[task.owner_id].append((terminal[task.id],'task.succeeded','task'))
    # Ordered users, not a ratio between unrelated event counters.
    stages=[('访问产品',lambda n,c:c=='page_view' or n=='work.open'),('进入游玩',lambda n,c:n in ('play.enter','fact.play.started')),
        ('请求受理',lambda n,c:n=='task.accepted'),('收到成功结果',lambda n,c:n=='task.succeeded')]
    funnel_counts=[0]*len(stages)
    for history in by_owner.values():
        index=0
        for _,name,category in sorted(history):
            if index<len(stages) and stages[index][1](name,category):funnel_counts[index]+=1;index+=1
    funnel=[{'label':label,'users':funnel_counts[i],'conversion':round(funnel_counts[i]/funnel_counts[i-1],4) if i and funnel_counts[i-1] else None} for i,(label,_) in enumerate(stages)]
    costs=defaultdict(Decimal);known_cost=0;unknown_usage=0;input_tokens=output_tokens=0;models=defaultdict(lambda:Counter())
    for usage in usages:
        if usage.input_tokens is not None:input_tokens+=usage.input_tokens
        if usage.output_tokens is not None:output_tokens+=usage.output_tokens
        if usage.input_tokens is None or usage.output_tokens is None:unknown_usage+=1
        models[usage.profile_id]['calls']+=1;models[usage.profile_id][usage.status]+=1
        models[usage.profile_id]['inputTokens']+=usage.input_tokens or 0;models[usage.profile_id]['outputTokens']+=usage.output_tokens or 0
        billing=(usage.billing_metadata or {}).get('billing',{})
        if billing.get('costState')=='known' and isinstance(billing.get('currency'),str) and len(billing['currency'])==3:
            try:
                amount=Decimal(str(billing.get('cost')))
                if amount.is_finite() and amount>=0:costs[billing['currency']]+=amount;known_cost+=1
            except (InvalidOperation,ValueError):pass
    from ..models import ModelProfile
    model_rows={p.id:p for p in db.execute(select(ModelProfile.id,ModelProfile.name,ModelProfile.model_id).where(ModelProfile.id.in_(models)))}
    from ..world.models import World
    keys=set(work_tasks)|set(work_views);worlds={w.id:w for w in db.execute(select(World.id,World.owner_id,World.name).where(World.id.in_([k[1] for k in keys])))}
    works=[]
    for owner,ident in keys:
        world=worlds.get(ident)
        if not world or world.owner_id!=owner:continue
        counts=work_tasks[(owner,ident)]
        works.append({'worldId':ident,'title':world.name,'views':work_views[(owner,ident)],'tasks':counts['tasks'],
            'succeeded':counts['succeeded'],'failed':counts['failed']})
    from ..scenes.models import SceneImage
    images=list(db.execute(select(SceneImage.status,SceneImage.image['sourceKind'].astext.label('source_kind')).where(SceneImage.owner_id.in_(owners),SceneImage.created_at>=start,SceneImage.created_at<=end).order_by(SceneImage.created_at.desc()).limit(10001)))
    truncated_images=len(images)>10000;images=images[:10000]
    image_counts=Counter(i.status for i in images)
    # Return cohorts only after their entire next-day window has elapsed.
    earliest=dict(db.execute(select(Event.owner_id,func.min(Event.created_at)).where(Event.owner_id.in_(owners),Event.category.in_(['page_view','fact'])).group_by(Event.owner_id)).all())
    cohort=defaultdict(set);activity_days=defaultdict(set)
    for owner,stamp in earliest.items():cohort[str(stamp.astimezone(SHANGHAI).date())].add(owner)
    for event in events:
        if event.owner_id and (event.category in ('page_view','fact') or event.name in BUSINESS_EVENTS):activity_days[event.owner_id].add(event.created_at.astimezone(SHANGHAI).date())
    retention=[]
    for day,members in sorted(cohort.items(),reverse=True)[:30]:
        date=datetime.fromisoformat(day).date();mature=date+timedelta(days=1)<today
        returned=sum(date+timedelta(days=1) in activity_days[o] for o in members) if mature and date>=start.astimezone(SHANGHAI).date() else None
        retention.append({'day':day,'users':len(members),'returned':returned,'d1':round(returned/len(members),4) if returned is not None else None})
    def timings(stamps):return distribution([int((stamps[t.id]-t.created_at).total_seconds()*1000) for t in tasks if t.id in stamps])
    return {'generatedAt':end.isoformat(),'timezone':'Asia/Shanghai','range':{'days':days,'start':start.isoformat(),'end':end.isoformat()},
        'includeAdmins':include_admins,'unlimitedUsage':unlimited_usage(),
        'summary':{'accounts':len(owners),'newAccounts':sum(u.id in owners and u.created_at>=start for u in users),'activeUsers':len(active),
            'visitors':len(visitors),'pageViews':sum(pages.values()),'sessions':len(sessions),'engagedMs':engaged_ms,
            'tasks':len(tasks),'successRate':round(task_states['succeeded']/(task_states['succeeded']+task_states['failed']),4) if task_states['succeeded']+task_states['failed'] else None},
        'trends':[{**row,'users':len(row['users'])} for _,row in sorted(trends.items())],
        'pages':[{'path':path,'views':count,'users':len(page_users[path])} for path,count in pages.most_common(20)],
        'events':[{'name':name,'count':count,'users':len(action_users[name])} for name,count in actions.most_common()],
        'funnel':funnel,'taskStates':dict(task_states),'errors':[{'code':code,'count':count} for code,count in errors.most_common(20)],
        'clientErrors':[{'path':path,'status':status,'code':code,'count':count} for (path,status,code),count in client_errors.most_common(20)],
        'clientLatency':distribution(client_latency),
        'timings':{'firstText':timings(first_text),'total':timings(terminal),'provider':distribution([u.latency_ms for u in usages])},
        'usage':{'calls':len(usages),'inputTokens':input_tokens,'outputTokens':output_tokens,'unknownTokenRecords':unknown_usage,
            'knownCostRecords':known_cost,'unknownCostRecords':len(usages)-known_cost,'costs':[{'currency':currency,'amount':str(amount)} for currency,amount in sorted(costs.items())],
            'models':[{'profileId':ident,'name':model_rows[ident].name if ident in model_rows else '已移除通道','modelId':model_rows[ident].model_id if ident in model_rows else None,**counts} for ident,counts in models.items()]},
        'images':{'records':len(images),'states':dict(image_counts),'reusedAssets':sum(i.source_kind=='reused-owned-empty-environment' for i in images)},
        'works':sorted(works,key=lambda w:(w['tasks'],w['views']),reverse=True)[:20],
        'users':[{'id':u.id,'username':u.username,'role':u.role or 'USER','lastSeenAt':last_seen[u.id].isoformat() if u.id in last_seen else None,**per_user[u.id]} for u in users if u.id in owners],
        'retention':retention,'quality':{'eventsTruncated':truncated_events,'tasksTruncated':truncated_tasks,'usageTruncated':truncated_usage,'imagesTruncated':truncated_images,
            'clientEventsAreHints':True,'costBasis':'只累计供应商明确返回的金额；未知费用保留未知，不使用积分换算。',
            'funnelBasis':'按账号在所选时间范围内依次出现的访问、游玩、提交和成功回执计算；历史缺失的前置埋点不回填。',
            'retentionBasis':'首个采集日为队列起点；次日完整结束后显示D1，尚未成熟或窗口外的样本显示未知。'}}


@router.get('/overview')
def overview(days:int=Query(7,ge=1,le=90),includeAdmins:bool=False,user=Depends(current_user),db=Depends(get_db)):
    require_admin(db,user)
    with SessionLocal() as projection:
        projection.execute(text('SET TRANSACTION READ ONLY'))
        projection.execute(text("SET LOCAL statement_timeout = '8s'"))
        return report(projection,days,includeAdmins)
