"""Queue-time templates; legacy queues never invent historical versions."""
from pathlib import Path
from copy import deepcopy
from .domain import digest
from .errors import Problem
DIRECTORY=Path(__file__).parent/'prompts'
CONNECTION='connection-v1\n只回复连接正常。'
def freeze_prompts(directory=DIRECTORY):
    values={path.stem:path.read_text('utf-8') for path in sorted(directory.glob('*.txt'))}
    values['connection']=CONNECTION
    return {name:{'version':text.splitlines()[0],'sha256':digest(text),'text':text} for name,text in values.items()}
def frozen_prompt(task,name,directory=DIRECTORY):
    snapshot=(task.checkpoint or {}).get('executionPrompts')
    if snapshot is None:return CONNECTION if name=='connection' else (directory/(name+'.txt')).read_text('utf-8')
    value=snapshot.get(name) if isinstance(snapshot,dict) else None
    if (not isinstance(value,dict) or set(value)!={'version','sha256','text'} or not isinstance(value['text'],str)
        or not value['text'] or len(value['text'].encode())>128*1024 or value['version']!=value['text'].splitlines()[0]
        or digest(value['text'])!=value['sha256']):raise Problem('EXECUTION_CONFIG_INVALID','排队模板校验失败，未发送请求',409)
    return value['text']
