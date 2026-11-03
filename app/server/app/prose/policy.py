"""Freeze actual encrypted credentials and finite chapter-specific limits."""
from types import SimpleNamespace
from ..completion_policy import is_mimo_go, input_ceiling
from ..errors import Problem

PROFILE_FIELDS = ('id', 'name', 'base_url', 'model_id', 'key_cipher', 'context_window',
    'output_parameter', 'supports_json', 'version')
LENGTHS = {'short': (800, 1200, 600, 4096), 'standard': (1500, 2500, 1200, 6144),
    'long': (2500, 4000, 2000, 8192)}

def freeze(profile):
    return {field: getattr(profile, field) for field in PROFILE_FIELDS}

def profile(value):
    return SimpleNamespace(**value)

def execution_profile(db, task, live):
    from . import is_prose
    if not is_prose(task):
        frozen = (task.checkpoint or {}).get('executionProfile')
        if frozen is None:
            # Legacy tasks have no recoverable pre-edit snapshot. Preserve their
            # existing consent/version checks; never invent historical settings.
            return live
        if not isinstance(frozen, dict) or set(frozen) != set(PROFILE_FIELDS) or frozen['id'] != live.id:
            raise Problem('EXECUTION_CONFIG_INVALID', '排队配置校验失败，未发送请求', 409)
        return profile(frozen)
    from sqlalchemy import select
    from ..models import GenerationBatch
    batch = db.scalar(select(GenerationBatch).where(GenerationBatch.task_id == task.id,
        GenerationBatch.owner_id == task.owner_id))
    if batch is None:
        raise Problem('TASK_CANCELLED', '写作内容已删除')
    return profile(batch.profile_snapshot)

def policy(frozen, chapter_length):
    thinking = 'disabled' if is_mimo_go(frozen) else None
    return {'version': 'prose-completion-v1', 'stages': {
        kind: {'totalCompletionTokens': limit, 'thinking': thinking}
        for kind, limit in {'analysis': 2048, 'generation': LENGTHS[chapter_length][3], 'validation': 2048}.items()}}

def budget(frozen, chapter_length, analyses, chapter_count):
    limits = policy(frozen, chapter_length)
    # One repair generation plus its necessary review is authorized for the
    # finite batch, rather than silently adding arbitrary retries.
    stages = ['analysis'] * analyses + ['generation', 'validation'] * chapter_count + ['generation', 'validation']
    caps = [limits['stages'][stage]['totalCompletionTokens'] for stage in stages]
    inputs = [input_ceiling(frozen, cap, world=True) for cap in caps]
    if any(cap <= 0 for cap in inputs):
        raise Problem('CONTEXT_BUDGET_EXCEEDED', '模型上下文不能容纳所选篇幅，未创建任务')
    return {'maxCalls': len(stages), 'inputTokens': sum(inputs), 'outputTokens': sum(caps),
        'completionPolicy': limits, 'maxRepairCalls': 1}
