"""Small, explicitly selected beta cohorts; no inferred QA/user labels."""
from datetime import timedelta
from fastapi import Depends, Query, Request
from pydantic import Field
from sqlalchemy import select, text, func
from ..db import get_db
from ..models import User, Task, TaskEvent, UsageRecord, now
from ..schemas import Strict
from ..security import current_user
from ..domain import idempotent
from ..errors import Problem
from ..foundation.models import Config, Account, Event
from ..foundation.core import require_admin, audit
from .api import router


class Cohort(Strict):
    id: str = Field(pattern=r'^[a-z0-9-]{1,40}$')
    label: str = Field(min_length=1, max_length=80)
    usernames: list[str] = Field(default_factory=list, max_length=100)


class BetaConfig(Strict):
    expectedVersion: int = Field(ge=0)
    cohorts: list[Cohort] = Field(default_factory=list, max_length=20)
    excludedUsernames: list[str] = Field(default_factory=list, max_length=500)


def config_view(db):
    row = db.get(Config, 'beta-cohorts')
    value = row.value if row else {'version': 0, 'cohorts': [], 'excludedUserIds': []}
    names = dict(db.execute(select(User.id, User.username)).all())
    return {'version': value['version'],
            'cohorts': [{'id': c['id'], 'label': c['label'], 'usernames': [names[i] for i in c['userIds'] if i in names]} for c in value['cohorts']],
            'excludedUsernames': [names[i] for i in value['excludedUserIds'] if i in names]}


@router.get('/beta/config')
def beta_config(user=Depends(current_user), db=Depends(get_db)):
    require_admin(db, user)
    return config_view(db)


@router.put('/beta/config')
def save_beta(data: BetaConfig, request: Request, user=Depends(current_user), db=Depends(get_db)):
    require_admin(db, user)
    def save():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('beta-cohorts',0))"))
        row = db.get(Config, 'beta-cohorts', populate_existing=True)
        if (row.value['version'] if row else 0) != data.expectedVersion:
            raise Problem('VERSION_CONFLICT', '内测分组已经更新，请重新载入', 409)
        if len({c.id for c in data.cohorts}) != len(data.cohorts):
            raise Problem('BETA_COHORT_INVALID', '内测分组标识不能重复', 422)
        names = {name for c in data.cohorts for name in c.usernames} | set(data.excludedUsernames)
        users = dict(db.execute(select(User.username, User.id).where(User.username.in_(names))).all())
        if set(users) != names: raise Problem('BETA_USER_NOT_FOUND', '部分用户名不存在，请检查后保存', 422)
        value = {'version': data.expectedVersion + 1,
                 'cohorts': [{'id': c.id, 'label': c.label, 'userIds': sorted({users[n] for n in c.usernames})} for c in data.cohorts],
                 'excludedUserIds': sorted({users[n] for n in data.excludedUsernames})}
        if row: row.value = value
        else: db.add(Config(key='beta-cohorts', value=value))
        db.flush(); audit(db, user.id, 'beta.cohorts', 'beta-cohorts', request.headers.get('idempotency-key',''), {'version': value['version']})
        return config_view(db)
    return idempotent(db, user.id, 'beta:cohorts', request.headers.get('idempotency-key'), data.model_dump(), save)


def distribution(values):
    values = sorted(values)
    return {'samples': len(values), 'medianMs': values[len(values)//2] if values else None,
            'p95Ms': values[min(len(values)-1, int((len(values)-1)*.95))] if values else None}


@router.get('/beta/report')
def beta_report(cohort: str = '', days: int = Query(7, ge=1, le=90), user=Depends(current_user), db=Depends(get_db)):
    require_admin(db, user)
    from ..world.queue import capacity
    row = db.get(Config, 'beta-cohorts'); value = row.value if row else {}
    chosen = next((c for c in value.get('cohorts', []) if c['id'] == cohort), None)
    members = set(chosen['userIds'] if chosen else []) - set(value.get('excludedUserIds', []))
    members -= set(db.scalars(select(Account.owner_id).where(Account.role == 'ADMIN')))
    members &= set(db.scalars(select(User.id).where(User.id.in_(members), User.status == 'active')))
    since = now() - timedelta(days=days)
    rows = list(db.scalars(select(Task).where(Task.owner_id.in_(members), Task.created_at >= since).order_by(Task.created_at.desc()).limit(5001)))
    truncated = len(rows) > 5000; rows = rows[:5000]; ids = [r.id for r in rows]
    first_calls = dict(db.execute(select(UsageRecord.task_id, func.min(UsageRecord.created_at)).where(UsageRecord.task_id.in_(ids)).group_by(UsageRecord.task_id)).all())
    texts = dict(db.execute(select(TaskEvent.task_id, func.min(TaskEvent.created_at)).where(TaskEvent.task_id.in_(ids), TaskEvent.type == 'text_delta').group_by(TaskEvent.task_id)).all())
    ends = dict(db.execute(select(TaskEvent.task_id, func.max(TaskEvent.created_at)).where(TaskEvent.task_id.in_(ids), TaskEvent.type == 'terminal').group_by(TaskEvent.task_id)).all())
    def times(stamps): return [max(0,int((stamps[r.id]-r.created_at).total_seconds()*1000)) for r in rows if r.id in stamps]
    states = {}; errors = {}
    for task in rows:
        states[task.state] = states.get(task.state, 0) + 1
        if task.error_code: errors[task.error_code] = errors.get(task.error_code, 0) + 1
    facts = dict(db.execute(select(Event.name, func.count(func.distinct(Event.owner_id))).where(Event.owner_id.in_(members), Event.category == 'fact', Event.created_at >= since).group_by(Event.name)).all())
    return {'cohort': chosen['label'] if chosen else None, 'members': len(members), 'days': days,
            'tasks': len(rows), 'taskStates': states, 'errors': errors, 'factsUniqueUsers': facts,
            'timings': {'beforeFirstCall': distribution(times(first_calls)), 'firstText': distribution(times(texts)), 'total': distribution(times(ends))},
            'globalCapacity': capacity(), 'queued': sum(r.state == 'queued' for r in rows), 'truncated': truncated,
            'basis': '仅所选内测批次，排除管理员、明确排除的验收账号与停用账号；调用前等待含排队和本地准备，首字/完成从任务提交起算。无样本保留未知。最多统计最近5000个任务。'}
