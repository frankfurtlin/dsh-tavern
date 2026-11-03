from datetime import timedelta
from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy import select, func
from ..db import get_db
from ..models import Task, now
from ..schemas import Strict
from ..domain import owned, idempotent
from ..security import current_user, rate_limit
from ..errors import Problem
from ..foundation.core import require_admin, audit
from .models import Feedback, Notice, notify

router = APIRouter(prefix='/experience', tags=['experience'])


class Diagnostic(Strict):
    path: str = Field(default='', pattern=r'^[/A-Za-z0-9_?=&%.-]{0,250}$')
    taskId: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    errorCode: str = Field(default='', pattern=r'^[A-Z0-9_]{0,80}$')
    appVersion: str = Field(default='', max_length=60)
    device: str = Field(default='', max_length=180)


class FeedbackInput(Strict):
    subject: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=5, max_length=4000)
    context: Diagnostic = Field(default_factory=Diagnostic)
    attachment: str = Field(default='', max_length=350000)


class FeedbackReply(Strict):
    expectedVersion: int = Field(ge=1)
    state: str = Field(pattern=r'^(open|investigating|resolved)$')
    reply: str = Field(min_length=3, max_length=2000)


def feedback_view(row):
    return {'id': row.id, 'subject': row.subject, 'description': row.description,
            'context': row.context, 'state': row.state, 'reply': row.reply,
            'version': row.version, 'createdAt': row.created_at.isoformat(),
            'updatedAt': row.updated_at.isoformat()}


@router.post('/feedback', status_code=201)
def submit_feedback(data: FeedbackInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    rate_limit(db, 'feedback:'+user.id, 20, 3600)
    if not data.subject.strip() or len(data.description.strip()) < 5:
        raise Problem('FEEDBACK_CONTENT_REQUIRED', '请写明遇到的问题', 422)
    if data.context.taskId:
        owned(db, Task, data.context.taskId, user.id)
    if data.attachment:
        from ..authoring.service import validate_cover
        validate_cover(data.attachment)
    def submit():
        row = Feedback(owner_id=user.id, subject=data.subject.strip(), description=data.description.strip(),
                       context={**data.context.model_dump(), **({'attachment': data.attachment} if data.attachment else {})})
        db.add(row); db.flush()
        return feedback_view(row)
    return idempotent(db, user.id, 'experience:feedback', request.headers.get('idempotency-key'), data.model_dump(), submit)


@router.get('/feedback')
def feedback_list(offset: int = Query(0, ge=0), admin: bool = False, user=Depends(current_user), db=Depends(get_db)):
    if admin: require_admin(db, user)
    query = select(Feedback)
    if not admin: query = query.where(Feedback.owner_id == user.id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Feedback.updated_at.desc(), Feedback.id).offset(offset).limit(20))
    return {'items': [feedback_view(row) for row in rows], 'total': total, 'offset': offset}


@router.get('/feedback/{identity}')
def read_feedback(identity: str, user=Depends(current_user), db=Depends(get_db)):
    return feedback_view(owned(db, Feedback, identity, user.id))


@router.post('/feedback/{identity}/reply')
def reply_feedback(identity: str, data: FeedbackReply, request: Request, user=Depends(current_user), db=Depends(get_db)):
    require_admin(db, user)
    row = db.scalar(select(Feedback).where(Feedback.id == identity).with_for_update())
    if not row: raise Problem('RESOURCE_NOT_FOUND', '反馈不存在', 404)
    def reply():
        if row.version != data.expectedVersion: raise Problem('VERSION_CONFLICT', '反馈已经更新，请重新载入', 409)
        row.reply = data.reply.strip(); row.state = data.state; row.version += 1; row.updated_at = now()
        notify(db, row.owner_id, '反馈有了新回复', row.reply, '/help/feedback?receipt='+row.id)
        audit(db, user.id, 'feedback.reply', row.id, 'feedback:'+request.headers.get('idempotency-key', ''), {'version': row.version})
        return feedback_view(row)
    return idempotent(db, user.id, 'experience:feedback-reply:'+identity, request.headers.get('idempotency-key'), data.model_dump(), reply)


@router.get('/notices')
def notices(offset: int = Query(0, ge=0), user=Depends(current_user), db=Depends(get_db)):
    query = select(Notice).where(Notice.owner_id == user.id)
    rows = list(db.scalars(query.order_by(Notice.created_at.desc(), Notice.id).offset(offset).limit(30)))
    return {'items': [{'id': r.id, 'title': r.title, 'text': r.text, 'path': r.path, 'read': r.read,
                       'createdAt': r.created_at.isoformat()} for r in rows],
            'nextOffset': offset+30 if len(rows)==30 else None,
            'unread': db.scalar(select(func.count()).select_from(Notice).where(Notice.owner_id == user.id, Notice.read.is_(False)))}


@router.post('/notices/{identity}/read')
def read_notice(identity: str, user=Depends(current_user), db=Depends(get_db)):
    row = owned(db, Notice, identity, user.id); row.read = True; db.commit()
    return {'read': True}


@router.get('/tasks')
def tasks(offset: int = Query(0, ge=0), user=Depends(current_user), db=Depends(get_db)):
    from ..world.queue import progress
    rows = db.scalars(select(Task).where(Task.owner_id == user.id).order_by(Task.created_at.desc(), Task.id).offset(offset).limit(30))
    return {'items': [{'id': t.id, 'kind': t.task_type, 'state': t.state, 'errorCode': t.error_code,
                      'worldId': t.payload.get('worldId') if not t.result.get('deleted') else None,
                      'chatId': t.payload.get('chatId') if not t.result.get('deleted') else None,
                      'removed': t.result.get('deleted') is True,
                      'bookId': t.payload.get('bookId'), 'progress': progress(db, t),
                      'createdAt': t.created_at.isoformat()} for t in rows]}
