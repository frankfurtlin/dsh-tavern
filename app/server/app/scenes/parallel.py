"""Bound independent visual reads, preserving lease context and every receipt."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context,ContextVar
from contextlib import contextmanager
from threading import BoundedSemaphore,Event
from ..config import settings

_slots=BoundedSemaphore(max(1,min(8,settings.scene_vision_concurrency)))
_cancel=ContextVar('visual-read-cancellation',default=None)


def assert_read_active():
    signal=_cancel.get()
    if signal is not None and signal.is_set():
        from ..errors import Problem
        raise Problem('TASK_CANCELLED','同批核验已停止，未发送新请求',409)


@contextmanager
def visual_slot(check=None,scope=None):
    # The supplier sees at most the configured eight visual requests per worker,
    # even when several worlds each schedule an independent read batch.
    with _slots:
        assert_read_active()
        from .admission import wait_for_start,observe_failure
        import time
        next_check=0.0
        def active():
            nonlocal next_check
            assert_read_active()
            now=time.monotonic()
            if check and now>=next_check:
                check();next_check=now+1
        wait_for_start(active,scope)
        try:yield
        except BaseException as error:
            observe_failure(error,scope)
            raise


def ordered_reads(function, values, *, workers=4):
    values=list(values)
    if len(values)<2:return [function(value) for value in values]
    results=[];errors=[];signal=Event()
    def read(value):
        assert_read_active()
        try:return function(value)
        except BaseException:
            signal.set();raise
    with ThreadPoolExecutor(max_workers=min(workers,len(values))) as pool:
        scope=_cancel.set(signal)
        try:futures=[pool.submit(copy_context().run,read,value) for value in values]
        finally:_cancel.reset(scope)
        # Join all in-flight requests before propagating failure. Their received
        # journals and usage must settle even when a sibling fails or is stopped.
        for future in futures:
            try:results.append(future.result())
            except BaseException as error:results.append(None);errors.append(error)
    if errors:
        # An unknown paid request must never be hidden by a later known error.
        original=next((e for e in errors if getattr(e,'code',None)!='TASK_CANCELLED'),errors[0])
        raise next((e for e in errors if getattr(e,'code',None)=='PROVIDER_STATUS_UNKNOWN'),original)
    return results


def person_reads(function, parsed, cast, *, on_progress=None):
    """Merge independent person proofs without losing a sibling's progress.

    Each worker sees the complete original list, retaining its durable stage
    ordinals. Only that worker's selected entity may enter the shared result.
    Final composition and overlapping-head validation still run on all people.
    """
    from copy import deepcopy
    from threading import Lock
    value=deepcopy(parsed);lock=Lock()
    order=list(dict.fromkeys([*[p.get('entityId') for p in value.get('characters',[])],
                              *[person['entityId'] for person in cast]]))
    def read(person):
        ident=person['entityId']
        def save(progress):
            candidate=next((p for p in progress.get('characters',[]) if p.get('entityId')==ident),None)
            if candidate is None:return
            with lock:
                present=any(p.get('entityId')==ident for p in value.get('characters',[]))
                value['characters']=[deepcopy(candidate) if p.get('entityId')==ident else p
                    for p in value.get('characters',[])]
                if not present:value['characters'].append(deepcopy(candidate))
                value['characters'].sort(key=lambda p:order.index(p.get('entityId')) if p.get('entityId') in order else len(order))
                if on_progress:on_progress(deepcopy(value))
        result=function(deepcopy(parsed),[person],save)
        save(result)
        return ident
    ordered_reads(read,cast)
    return value
