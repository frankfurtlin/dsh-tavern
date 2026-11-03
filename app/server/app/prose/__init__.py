"""Reader prose protocol; legacy candidate and task policies remain separate."""

PROTOCOL = 'reader-prose-v1'

def is_prose(task):
    return task.task_type == 'story_flow' and task.payload.get('protocol') == PROTOCOL


def is_imported_history(task):
    return task.payload.get('importedHistory') is True or task.result.get('importedHistory') is True


def require_writable(task):
    if is_imported_history(task):
        from ..errors import Problem
        raise Problem('IMPORTED_HISTORY_READ_ONLY',
            '这是导入的历史写作；如需续写或重写，请使用当前账号模型新开写作', 409)
