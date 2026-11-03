"""Pace fresh visual intents and release workers during supplier cooldowns."""
from collections import deque
from datetime import datetime,timezone,timedelta
from math import ceil
from threading import Condition
from time import monotonic
from ..config import settings
from ..errors import Problem

_condition=Condition()
_waiting=deque()
_next_start=0.0
_cooldowns={}


def remember(scope,until):
    if until is None:return
    with _condition:
        key=scope or 'default'
        if until>_cooldowns.get(key,datetime.min.replace(tzinfo=timezone.utc)):_cooldowns[key]=until
        _condition.notify_all()


def assert_available(scope=None):
    if settings.ai_mode!='real':return
    with _condition:until=_cooldowns.get(scope or 'default')
    seconds=ceil((until-datetime.now(timezone.utc)).total_seconds()) if until else 0
    if seconds>0:
        details={'httpStatus':429,'rejectionCategory':'rate_limit','reason':'admission_cooldown',
            'requestSent':False,'responseReceived':False,'retryAfterSeconds':seconds,'retryAt':until.isoformat()}
        if scope:details['cooldownScope']=scope
        raise Problem('SCENE_VISION_COOLDOWN','场景校验服务仍在等待恢复，已有图片已保留，可先继续文字游玩',429,details)


def wait_for_start(check,scope=None):
    global _next_start
    interval=max(0.0,min(10.0,settings.scene_vision_start_interval_seconds)) if settings.ai_mode=='real' else 0.0
    ticket=object()
    with _condition:_waiting.append(ticket)
    try:
        while True:
            check();assert_available(scope)
            with _condition:
                now=monotonic();delay=_next_start-now
                if _waiting[0] is ticket and (not interval or delay<=0):
                    _waiting.popleft();_next_start=now+interval;_condition.notify_all();return
                _condition.wait(timeout=min(.1,max(.01,delay)) if delay>0 else .1)
    finally:
        with _condition:
            if ticket in _waiting:_waiting.remove(ticket)
            _condition.notify_all()


def observe_failure(error,scope=None):
    details=getattr(error,'details',{})
    if details.get('httpStatus')!=429:return
    from .cooldown import parse_deadline
    until=parse_deadline(details.get('retryAt'))
    if until is None:
        seconds=details.get('retryAfterSeconds')
        seconds=seconds if type(seconds) is int and 1<=seconds<=999999999 else 30
        until=datetime.now(timezone.utc)+timedelta(seconds=seconds)
    remember(scope or details.get('cooldownScope'),until)
