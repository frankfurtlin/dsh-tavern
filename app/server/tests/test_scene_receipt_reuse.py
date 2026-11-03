import json
import pytest
from unittest.mock import patch
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Task
from app.world.models import WorldLine,WorldTurn
from app.world.schemas import DirectorOutput
from app.world.worker import process
from app.storage import journal
from .test_scenes import scene_world
from .test_scene_interaction import executable_scene_source,body,provider,API


@pytest.mark.parametrize('code,provider_state,reused', [('WORLD_OUTPUT_INVALID','not_sent',True),('WORLD_OUTPUT_INCOMPLETE','completed',True),('WORLD_ENTITY_INVALID','completed',True),('WORLD_RELATION_INVALID','completed',True),('PROVIDER_CONNECTION_FAILED','not_sent',True),('PROVIDER_CONNECTION_FAILED','unknown',False),('PROVIDER_STATUS_UNKNOWN','unknown',False)])
def test_explicit_same_action_retry_reuses_received_director_without_another_analysis_call(client,scene_world,profile,code,provider_state,reused):
    w=scene_world;message='看一眼窗外'
    old=client.post(API+w['root']+'/actions',json=body(w,message),headers={'Idempotency-Key':'old-receipt'}).json()
    plan=DirectorOutput(title='窗外',elapsedMinutes=1,timeLabel='清晨',publicOutcome='你望向窗外。').model_dump()
    with SessionLocal() as db:
        task=db.get(Task,old['taskId']);task.state='failed';task.error_code=code;task.provider_state=provider_state
        task.checkpoint={**task.checkpoint,'calls':{'analysis:director:1':{'callId':'receipt-test'}}}
        db.get(WorldLine,w['line']).pending_task_id=None
        turn=db.scalar(select(WorldTurn).where(WorldTurn.task_id==task.id));turn.status='draft'
        if code=='WORLD_RELATION_INVALID':
            turn.plan={'1':{'plan':plan}}
            # The received transport can contain now-meaningless local IDs;
            # replay must use the durably resolved proposal instead.
            raw={**plan,'summary':'E999'}
        else:raw=plan
        journal(task.owner_id,task.id,'receipt-test',{'text':json.dumps(raw),'finishReason':'stop'})
        db.commit()
    request=body(w,message);request['turnId']='explicit-new-retry'
    response=client.post(API+w['root']+'/actions',json=request,headers={'Idempotency-Key':'new-receipt'})
    assert response.status_code==202,response.text
    calls=[];base=provider(plan,'你望向窗外，山道安静。')
    def counted(*args):calls.append(args[2]);return base(*args)
    with patch('app.world.worker.call_provider',side_effect=counted):process(response.json()['taskId'])
    assert client.get(API+'/tasks/'+response.json()['taskId']).json()['state']=='succeeded'
    assert calls==(['world_narrator','world_review'] if reused else ['world_director','world_narrator','world_review'])
