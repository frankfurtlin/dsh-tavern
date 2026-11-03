"""Durable, credential-scoped visual cooldowns, without reserving a new call."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from math import ceil
from types import SimpleNamespace
from urllib.parse import urlsplit
from sqlalchemy import select,or_
from ..models import Task
from ..errors import Problem


def scope_of(profile):
    if isinstance(profile,dict):profile=SimpleNamespace(**profile)
    try:
        from ..providers import decrypt_key
        parsed=urlsplit(profile.base_url)
        if parsed.scheme!='https' or not parsed.hostname:return None
        credential=decrypt_key(profile.key_cipher)
        return sha256((parsed.hostname.lower()+'|'+credential).encode()).hexdigest()
    except Exception:return None


def parse_deadline(value):
    if not isinstance(value,str) or len(value)>80:return None
    try:
        parsed=datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:return None


def remaining(task,*,at=None):
    checkpoint=task.checkpoint if task else {}
    diagnostic=checkpoint.get('providerFailure',{})
    sources=[checkpoint.get('providerCooldown',{})]
    if diagnostic.get('httpStatus')==429:sources.append(diagnostic)
    dates=[parsed for source in sources if (parsed:=parse_deadline(source.get('retryAt'))) is not None]
    until=max(dates) if dates else None
    seconds=ceil((until-(at or datetime.now(timezone.utc))).total_seconds()) if until else 0
    return {'retryAt':until.isoformat(),'retryAfterSeconds':seconds} if seconds>0 else {}


def latest(db,scope):
    if not scope:return None
    rows=db.execute(select(Task.checkpoint['providerCooldown'],Task.checkpoint['providerFailure']).where(or_(
        Task.checkpoint['providerFailure']['cooldownScope'].astext==scope,
        Task.checkpoint['providerCooldown']['cooldownScope'].astext==scope)))
    dates=[parsed for row in rows for index,value in enumerate(row)
        if isinstance(value,dict) and value.get('cooldownScope')==scope
        and (index==0 or value.get('httpStatus')==429)
        and (parsed:=parse_deadline(value.get('retryAt'))) is not None]
    return max(dates) if dates else None


def failure_metadata(error,profile):
    if error.details.get('httpStatus')!=429:return {}
    seconds=error.details.get('retryAfterSeconds')
    seconds=seconds if type(seconds) is int and 1<=seconds<=999999999 else 30
    result={'retryAfterSeconds':seconds,'retryAt':(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()}
    scope=scope_of(profile)
    if scope:result['cooldownScope']=scope
    return result


def merge_receipt(checkpoint,metadata):
    """A later sibling timeout must not erase an already received backoff."""
    previous=checkpoint.get('providerCooldown',{})
    old=parse_deadline(previous.get('retryAt'));new=parse_deadline(metadata.get('retryAt'))
    safe={k:metadata[k] for k in ('retryAt','retryAfterSeconds','cooldownScope') if k in metadata}
    return {'providerCooldown':safe} if new and (not old or new>old) else {}


def hydrate(db,profile):
    from ..config import settings
    from .admission import remember
    if settings.ai_mode!='real':return None
    scope=scope_of(profile)
    if scope:remember(scope,latest(db,scope))
    return scope


def check_selected(db,owner):
    from ..config import settings
    if settings.ai_mode!='real':return
    from ..foundation.vision import selected
    from .admission import assert_available
    try:assert_available(hydrate(db,selected(db,owner)))
    except Problem as error:
        error.details={k:v for k,v in error.details.items() if k!='cooldownScope'}
        raise


def record_deferral(task_id,details):
    from ..db import SessionLocal
    from ..worker import assert_task_active
    from ..world.jobs import lock_task_world
    from .models import SceneImage
    with SessionLocal() as db:
        lock_task_world(db,task_id)
        task=db.scalar(select(Task).where(Task.id==task_id).with_for_update().execution_options(populate_existing=True))
        assert_task_active(db,task)
        picture=db.get(SceneImage,task.payload.get('sceneId')) if task.task_type=='world_scene_binding' else None
        if (not picture or picture.owner_id!=task.owner_id or picture.world_id!=task.payload.get('worldId')
                or picture.line_id!=task.payload.get('lineId') or picture.image.get('bindingTaskId')!=task.id):
            raise Problem('TASK_CANCELLED','校验画面已变化')
        previous=task.checkpoint.get('providerFailure',{})
        # An actual received rejection/unknown failure has priority over a
        # sibling's admission deferral. Never erase its paid request metadata.
        if previous and previous.get('reason')!='admission_cooldown':return
        until=parse_deadline(details.get('retryAt'))
        if not until:return
        diagnostic={'errorCode':'SCENE_VISION_COOLDOWN','reason':'admission_cooldown','httpStatus':429,
            'rejectionCategory':'rate_limit','requestSent':False,'responseReceived':False,
            'retryAt':until.isoformat(),'retryAfterSeconds':max(1,ceil((until-datetime.now(timezone.utc)).total_seconds()))}
        scope=details.get('cooldownScope')
        if isinstance(scope,str) and len(scope)==64 and all(c in '0123456789abcdef' for c in scope):diagnostic['cooldownScope']=scope
        task.checkpoint={**task.checkpoint,'providerFailure':diagnostic,**merge_receipt(task.checkpoint,diagnostic)}
        if not task.checkpoint.get('calls'):task.provider_state='not_sent';task.usage_state='known'
        db.commit()
