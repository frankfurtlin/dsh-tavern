"""Non-sensitive queue information; querying never starts another task."""
from sqlalchemy import select, func
from ..config import settings
from ..models import Task, TaskEvent, now


def capacity():
    return max(1, min(16, settings.world_task_concurrency))


def progress(db, task):
    terminal = task.state in ('succeeded', 'failed', 'cancelled')
    phase = task.result.get('phase', task.state)
    if task.persistence_state == 'recoverable': label = '正在恢复已收到的内容'
    elif task.state == 'queued': label = '正在排队'
    elif task.state == 'succeeded': label = '已完成并保存'
    elif task.state == 'cancelled': label = '已停止，已收到的内容保留'
    elif task.state == 'failed': label = '需要处理，可查看原任务'
    elif phase in ('validation', 'validating', 'reviewing'): label = '正在核验'
    elif task.provider_state == 'unknown': label = '供应商结果待核查，请勿重复发送'
    else: label = '正在生成与保存'
    position = None
    if task.state == 'queued' and task.task_type.startswith('world_'):
        position = 1 + db.scalar(select(func.count()).select_from(Task).where(
            Task.task_type.like('world_%'), Task.state == 'queued',
            (Task.created_at < task.created_at) | ((Task.created_at == task.created_at) & (Task.id < task.id))))
    finished = db.scalar(select(func.max(TaskEvent.created_at)).where(TaskEvent.task_id == task.id)) if terminal else None
    elapsed = max(0, int(((finished or now())-task.created_at).total_seconds())) if not terminal or finished else None
    return {'label': label, 'queued': task.state == 'queued', 'position': position,
            'capacity': capacity() if task.task_type.startswith('world_') else None,
            'elapsedSeconds': elapsed, 'terminal': terminal}
