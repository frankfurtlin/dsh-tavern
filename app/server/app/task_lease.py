"""Bind each execution to the opaque token assigned by its queue claim.

The token lives in Task.lease_owner (32 characters). Worker identity is not a
fence: the same worker can reclaim a task while its previous thread is blocked.
Received provider receipts may still settle; only the current execution can
authorize another call or change the task's result.
"""
from contextvars import ContextVar
from functools import wraps


class LeaseLost(Exception):
    """An obsolete execution must leave the current owner's task untouched."""


class ClaimedTask(str):
    def __new__(cls, task_id, lease_token):
        value = super().__new__(cls, task_id)
        value.lease_token = lease_token
        return value


_execution = ContextVar("task_execution_lease", default=None)


def lease_matches(task):
    bound = _execution.get()
    return bool(task) and (bound is None or
                          (task.id == bound[0] and task.lease_owner == bound[1]))


def assert_lease(task):
    if not lease_matches(task):
        raise LeaseLost()


def execution_token():
    bound = _execution.get()
    if bound is None:
        raise LeaseLost()
    return bound[1]


def leased_task(function):
    @wraps(function)
    def execute(task_id, *args, **kwargs):
        if _execution.get() is not None:
            return function(task_id, *args, **kwargs)
        if isinstance(task_id, ClaimedTask):
            lease_token = task_id.lease_token
        else:
            # Direct executions used by isolated tests still detect a later
            # claim. Queue dispatch always carries its token, even before this
            # function begins running in a pool thread.
            from .db import SessionLocal
            from .models import Task
            with SessionLocal() as db:
                task = db.get(Task, task_id)
                if not task:
                    return
                lease_token = task.lease_owner
        context = _execution.set((str(task_id), lease_token))
        try:
            return function(task_id, *args, **kwargs)
        except LeaseLost:
            return
        finally:
            _execution.reset(context)
    return execute
