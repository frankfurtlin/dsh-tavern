"""Actual child SIGKILL after the private fragment and before DB mirror."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import timedelta
from sqlalchemy import select
from app import worker
from app.config import settings
from app.db import SessionLocal
from app.models import Task, GenerationChapterVersion, UsageRecord, ModelProfile, now
from app.providers import MockAdapter
from .test_prose_generations import isolated_prose, start, get

CHILD = r'''
import json, os, time
from pathlib import Path
from app.config import settings
from app.db import engine
from app import worker
from app.prose import stream
from app.providers import MockAdapter,ChatCompletionsAdapter
assert settings.ai_mode=='mock' and engine.url.database=='reader_test'
def forbidden(*a,**kw):raise AssertionError('No external provider')
ChatCompletionsAdapter.generate=forbidden
ChatCompletionsAdapter.generate_stream=forbidden
def hold_mirror(ctx,value,**kw):
    path=Path(os.environ['PROSE_BARRIER'])
    assert not path.exists()
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as handle:
        json.dump({'taskId':ctx['taskId'],'generationId':ctx['generationId'],
            'durableText':stream.read(ctx)['text'],'snapshotVersion':value['snapshotVersion']},handle)
        handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,path)
    while True:time.sleep(.05)
stream.mirror=hold_mirror
def partial(self,p,prompt,data,limit,*,on_delta,**kw):
    on_delta('进程被终止前已收到😀。')
    raise AssertionError('The parent must kill before this returns')
MockAdapter.generate_stream=partial
claim=worker.claim('owned-prose-kill')
assert claim and str(claim)==os.environ['PROSE_TASK']
worker.process(claim)
'''


def test_sigkill_after_private_fragment_before_mirror_recovers_without_provider(client,reader,demo,profile,tmp_path,monkeypatch):
    assert SessionLocal.kw['bind'].url.database=='reader_test'
    receipt,_=start(client,reader,demo,profile)
    barrier=tmp_path/'durable-barrier.json'
    env={**os.environ,'PROSE_TASK':receipt['taskId'],'PROSE_BARRIER':str(barrier),'STORAGE_PATH':str(settings.storage_path)}
    log=tmp_path/'owned-child.log'
    with log.open('w',encoding='utf-8') as handle:
        child=subprocess.Popen([sys.executable,'-c',CHILD],env=env,stdout=handle,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+10
            while not barrier.exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            assert barrier.exists(),log.read_text('utf-8')
            observed=json.loads(barrier.read_text('utf-8'))
            assert observed['taskId']==receipt['taskId'] and observed['generationId']==receipt['generationId']
            with SessionLocal() as db:
                assert db.scalar(select(GenerationChapterVersion)).text==''
                usage=list(db.scalars(select(UsageRecord).where(UsageRecord.task_id==receipt['taskId'])))
                reserved=(db.get(ModelProfile,profile['id']).input_reserved,db.get(ModelProfile,profile['id']).output_reserved)
                old_calls={u.provider_call_id:u.status for u in usage}
                assert len(usage)==2 and sum(u.status=='intent' for u in usage)==1
            assert get(client,receipt)['chapters'][0]['text']==observed['durableText']
            child.kill();child.wait(timeout=5)
            assert child.returncode != 0 if os.name=='nt' else child.returncode == -signal.SIGKILL
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)
    def no_more(*a,**kw):raise AssertionError('Save recovery cannot call provider')
    monkeypatch.setattr(MockAdapter,'generate',no_more);monkeypatch.setattr(MockAdapter,'generate_stream',no_more)
    with SessionLocal() as db:
        task=db.get(Task,receipt['taskId']);task.lease_expires_at=now()-timedelta(seconds=1);db.commit()
    reclaimed=worker.claim('owned-prose-recovery')
    assert reclaimed and str(reclaimed)==receipt['taskId']
    worker.process(reclaimed)
    saved=get(client,receipt)
    assert saved['chapters'][0]['text']==observed['durableText']
    assert saved['chapters'][0]['durability']=='saved' and not saved['chapters'][0]['canAccept']
    with SessionLocal() as db:
        assert {u.provider_call_id:u.status for u in db.scalars(select(UsageRecord).where(UsageRecord.task_id==receipt['taskId']))}==old_calls
        assert (db.get(ModelProfile,profile['id']).input_reserved,db.get(ModelProfile,profile['id']).output_reserved)==reserved
    output=os.environ.get('PROSE_TEST_OBSERVATIONS')
    if output:
        folder=Path(output);folder.mkdir(parents=True,exist_ok=True)
        path=folder/'sigkill-recovery.json';assert not path.exists()
        path.write_text(json.dumps({'childReturnCode':child.returncode,'checkpointBeforeMirrorTextEmpty':True,
            'privateTextVisible':True,'reclaimedSameTask':True,'adapterCallsOnRecovery':0,'usageReservationsUnchanged':True,
            'oldCallIds':sorted(old_calls),'generationId':receipt['generationId'],'taskId':receipt['taskId']},indent=2),encoding='utf-8')
