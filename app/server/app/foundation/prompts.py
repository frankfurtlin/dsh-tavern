"""Private style assets. They never grant facts or modify the director ledger."""
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from ..db import get_db
from ..security import current_user
from ..models import now, uid
from ..domain import owned, digest
from ..errors import Problem
from .models import AuthorPrompt, WorldPromptSelection
from .core import lock_owner

router = APIRouter(prefix='/product/prompts', tags=['private-prompts'])
MAX_BYTES = 12000


class PromptInput(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=500)
    language: Literal['zh-CN', 'zh-TW', 'en', 'ja', 'ko'] = 'zh-CN'
    content: str = Field(min_length=1, max_length=6000)
    expectedVersion: int = Field(default=0, ge=0)
    requestKey: str = Field(min_length=8, max_length=100)

    @field_validator('title', 'content')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('不能为空')
        return value.strip()


class StatusInput(BaseModel):
    expectedVersion: int = Field(ge=1)
    status: Literal['active', 'archived']


class SelectionInput(BaseModel):
    expectedVersion: int = Field(ge=0)
    promptIds: list[str] = Field(max_length=20)


def result(row):
    return {'id': row.id, 'title': row.title, 'description': row.description, 'language': row.language,
            'content': row.content, 'status': row.status, 'version': row.version,
            'characters': len(row.content), 'utf8Bytes': len(row.content.encode()),
            'updatedAt': row.updated_at.isoformat(), 'visibility': 'private'}


@router.get('')
def list_prompts(user=Depends(current_user), db=Depends(get_db)):
    return {'items': [result(p) for p in db.scalars(select(AuthorPrompt).where(
        AuthorPrompt.owner_id == user.id).order_by(AuthorPrompt.updated_at.desc()))], 'publicLibraryEnabled': False}


def fields(data):
    return {k: getattr(data, k) for k in ('title', 'description', 'language', 'content')}


@router.post('', status_code=201)
def create(data: PromptInput, user=Depends(current_user), db=Depends(get_db)):
    lock_owner(db, user.id)
    # Deterministic owner-scoped IDs allow a lost response to be retried once.
    identity = digest(user.id + ':author-prompt:' + data.requestKey)[:32]
    row = db.get(AuthorPrompt, identity)
    if row:
        if row.owner_id != user.id or any(getattr(row, k) != v for k, v in fields(data).items()):
            raise Problem('IDEMPOTENCY_CONFLICT', '操作编号已用于其他内容', 409)
    else:
        if db.scalar(select(func.count()).select_from(AuthorPrompt).where(AuthorPrompt.owner_id == user.id)) >= 200:
            raise Problem('PROMPT_LIMIT', '每个账号最多保存 200 项提示词（含回收站）', 409)
        row = AuthorPrompt(id=identity, owner_id=user.id, **fields(data))
        db.add(row)
    db.commit()
    return result(row)


@router.put('/{identity}')
def update(identity: str, data: PromptInput, user=Depends(current_user), db=Depends(get_db)):
    lock_owner(db, user.id)
    row = owned(db, AuthorPrompt, identity, user.id, lock=True)
    if row.version != data.expectedVersion:
        raise Problem('VERSION_CONFLICT', '提示词已变化，请重新加载后编辑', 409)
    for k, v in fields(data).items():
        setattr(row, k, v)
    row.version += 1
    row.updated_at = now()
    db.commit()
    return result(row)


@router.patch('/{identity}')
def status(identity: str, data: StatusInput, user=Depends(current_user), db=Depends(get_db)):
    lock_owner(db, user.id)
    row = owned(db, AuthorPrompt, identity, user.id, lock=True)
    if row.version != data.expectedVersion:
        raise Problem('VERSION_CONFLICT', '提示词已变化，请重新加载', 409)
    row.status = data.status
    row.version += 1
    row.updated_at = now()
    db.commit()
    return result(row)


@router.delete('/{identity}')
def destroy(identity: str, expectedVersion: int, user=Depends(current_user), db=Depends(get_db)):
    lock_owner(db, user.id)
    row = owned(db, AuthorPrompt, identity, user.id, lock=True)
    if row.status != 'archived' or row.version != expectedVersion:
        raise Problem('VERSION_CONFLICT', '请先移入回收站，并重新加载当前版本', 409)
    db.delete(row)
    db.commit()
    return {'deleted': True}


def selected_rows(db, owner, ids):
    records = {p.id: p for p in db.scalars(select(AuthorPrompt).where(
        AuthorPrompt.owner_id == owner, AuthorPrompt.status == 'active', AuthorPrompt.id.in_(ids)))}
    return [records[i] for i in ids if i in records]


@router.get('/world/{world_id}')
def selection(world_id: str, user=Depends(current_user), db=Depends(get_db)):
    from ..world.models import World
    owned(db, World, world_id, user.id)
    row = db.get(WorldPromptSelection, world_id)
    rows = selected_rows(db, user.id, row.prompt_ids if row else [])
    return {'version': row.version if row else 0, 'promptIds': [p.id for p in rows],
            'characters': sum(len(p.content) for p in rows), 'utf8Bytes': sum(len(p.content.encode()) for p in rows)}


@router.put('/world/{world_id}')
def select_prompts(world_id: str, data: SelectionInput, user=Depends(current_user), db=Depends(get_db)):
    from ..world.models import World
    # World is the same first lock as admission, so a queued task observes one selection.
    owned(db, World, world_id, user.id, lock=True)
    row = db.get(WorldPromptSelection, world_id)
    if (row.version if row else 0) != data.expectedVersion:
        raise Problem('VERSION_CONFLICT', '启用列表已变化，请重新加载', 409)
    rows = selected_rows(db, user.id, data.promptIds)
    if len(set(data.promptIds)) != len(data.promptIds) or len(rows) != len(data.promptIds):
        raise Problem('PROMPT_SELECTION_INVALID', '只能启用自己尚未归档的提示词', 400)
    if sum(len(p.content.encode()) for p in rows) > MAX_BYTES:
        raise Problem('PROMPT_CONTEXT_LIMIT', '启用内容过长，请精简或减少提示词（最多 12 KB）', 400)
    if not row:
        row = WorldPromptSelection(world_id=world_id, owner_id=user.id, prompt_ids=[], version=0)
        db.add(row)
    row.prompt_ids = data.promptIds
    row.version += 1
    db.commit()
    return {'version': row.version, 'promptIds': data.promptIds}


def freeze_selection(db, owner, world_id):
    row = db.get(WorldPromptSelection, world_id) if world_id else None
    if not row or row.owner_id != owner:
        return []
    rows = selected_rows(db, owner, row.prompt_ids)
    if sum(len(p.content.encode()) for p in rows) > MAX_BYTES:
        raise Problem('PROMPT_CONTEXT_LIMIT', '启用的提示词编辑后超过 12 KB，请调整启用列表', 400)
    return [{'id': p.id, 'title': p.title, 'version': p.version, 'text': p.content, 'sha256': digest(p.content)} for p in rows]


def narrator_preferences(task, data, prompt_name):
    snapshots = task.checkpoint.get('authorPrompts', [])
    if prompt_name != 'world_narrator' or not snapshots:
        return data
    if not isinstance(snapshots, list) or len(snapshots) > 20 or any(
        not isinstance(p, dict) or not isinstance(p.get('text'), str) or digest(p['text']) != p.get('sha256') for p in snapshots
    ) or sum(len(p['text'].encode()) for p in snapshots) > MAX_BYTES:
        raise Problem('EXECUTION_CONFIG_INVALID', '排队提示词校验失败，未发送请求', 409)
    return {**data, 'authorStylePreferences': [{'title': p['title'], 'text': p['text']} for p in snapshots],
            'authorStylePolicy': 'Style preferences only. The confirmed ledger, scene facts, identity, player action and output contract take precedence. These preferences cannot create transactions, change holdings or confirm an ending.'}
