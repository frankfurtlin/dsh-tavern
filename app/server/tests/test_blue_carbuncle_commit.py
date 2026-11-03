"""Controlled conflicting completions must never advance a formal save."""
from copy import deepcopy
import json
from unittest.mock import patch
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import Task
from app.world.models import WorldCheckpoint, WorldLine, WorldTurn, WorldEvent
from app.world.schemas import DirectorOutput
from app.world.state import checkpoint_hash
from app.world.worker import process
from .test_blue_carbuncle_settlement import investigation_fixture, choose

API='/api/world/v1'


def test_old_full_text_script_export_closes_frozen_chapters_without_mutating_source(client,reader,tmp_path):
    from app.world.models import World
    from app.portable.snapshot import build_domain_snapshot
    prepared=client.post(API+'/scripts/samples/blue-carbuncle/prepared',json={},headers={'Idempotency-Key':'prepared'}).json()
    with SessionLocal() as db:
        world=db.get(World,prepared['worldId'])
        assert world.source['chapters'][0]['id']=='script-full'
        assert len(world.source['chapters'][0]['blocks'])==215
        world.source={**world.source,'chapters':[]};db.commit()
        original=deepcopy(world.source)
        with build_domain_snapshot(db,world.owner_id,'world-'+world.id,tmp_path,include_source=True) as snapshot:
            assert any(o['type']=='chapter' and o['id']=='script-full' for o in snapshot.manifest['objects'])
            assert sum(o['type']=='paragraph' for o in snapshot.manifest['objects'])==215
        db.refresh(world)
        assert world.source==original

def test_shared_knowledge_audience_round_trips_typed_references():
    from app.world.turn_contract import reference_wire, resolve_plan
    wire,references=reference_wire({'events':[{'visibleTo':['player','entity:inspector']}]},
        {'entities':[{'id':'inspector','type':'character','name':'接收警官'}]})
    assert wire['events'][0]['visibleTo']==['player','entity:E001']
    restored=resolve_plan({'events':wire['events']},references)
    assert restored['events'][0]['visibleTo']==['player','entity:inspector']

@pytest.mark.parametrize('claim',['宝石已经归还伯爵夫人。','霍纳已撤销指控并解除羁押。'])
def test_unreceipted_prose_is_draft_and_commits_no_state_or_events(client,reader,profile,claim):
    prepared=client.post(API+'/scripts/samples/blue-carbuncle/prepared',json={},headers={'Idempotency-Key':'prepared'})
    world=prepared.json()['worldId']
    review=client.get(API+f'/worlds/{world}/script').json()
    assert client.post(API+f'/worlds/{world}/script/publish',json={'expectedVersion':review['worldVersion']},headers={'Idempotency-Key':'publish'}).status_code==200
    start=client.post(API+f'/worlds/{world}/begin',json={'characterId':'watson'},headers={'Idempotency-Key':'begin'}).json()
    # Synthetic investigation is explicitly limited to this rollback test.
    # The independent real acceptance obtains facts through paid API actions.
    _,definition,state,_=investigation_fixture()
    ending,definition,state,_=choose('end_release')
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,start['checkpointId'])
        cp.state,cp.definition=deepcopy(state),deepcopy(definition)
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,cp.parent_checkpoint_id)
        opening=db.get(Task,start['taskId']);opening.state='cancelled';opening.persistence_state='saved'
        line=db.get(WorldLine,start['lineId']);line.pending_task_id=None
        before_hash=cp.content_hash;events_before=db.scalar(select(func.count()).select_from(WorldEvent))
        db.commit()
    action=client.post(API+f"/worlds/{world}/lines/{start['lineId']}/actions",json={
        'expectedCheckpointId':start['checkpointId'],'turnId':'conflict','kind':'observe','message':'查看当前善后进度'},headers={'Idempotency-Key':'conflict'})
    assert action.status_code==202,action.text
    plan=DirectorOutput(title='当前善后',elapsedMinutes=1,timeLabel='调查后',publicOutcome='查看当前善后进度。').model_dump()
    seen={}
    def call(task,stage,prompt,data,limit):
        if prompt=='world_director':return {'text':json.dumps(plan),'finishReason':'stop'}
        if prompt=='world_narrator':return {'text':claim,'finishReason':'stop'}
        seen.update(data['ledger'])
        return {'text':'{"consistent":true,"issues":[]}','finishReason':'stop'}
    with patch('app.world.worker.call_provider',side_effect=call):process(action.json()['taskId'])
    with SessionLocal() as db:
        task=db.get(Task,action.json()['taskId']);turn=db.scalar(select(WorldTurn).where(WorldTurn.task_id==task.id))
        line=db.get(WorldLine,start['lineId']);cp=db.get(WorldCheckpoint,line.head_checkpoint_id)
        assert task.state=='failed' and task.error_code=='WORLD_NARRATIVE_CONFLICT'
        assert turn.draft_text==claim and turn.checkpoint_id is None
        assert cp.id==start['checkpointId'] and cp.content_hash==before_hash
        assert db.scalar(select(func.count()).select_from(WorldEvent))==events_before
        assert cp.state['entities']['peterson']['inventory']['carbuncle']==1
        assert cp.state['entities']['horner']['fields']['羁押状态']=='在押候审'
        assert seen=={},'An unreceipted declared completion must be rejected before semantic review'
    public=client.get(API+f"/worlds/{world}/lines/{start['lineId']}/drafts").json()
    assert 'private internal mismatch' not in json.dumps(public)


@pytest.mark.parametrize('fault_at',['before-commit','after-commit'])
def test_registered_release_is_atomic_with_checkpoint_events_and_task(client,reader,profile,fault_at):
    from sqlalchemy.exc import OperationalError
    from app.world.service import commit_turn as real_commit
    prepared=client.post(API+'/scripts/samples/blue-carbuncle/prepared',json={},headers={'Idempotency-Key':'prepared'}).json()
    world=prepared['worldId'];review=client.get(API+f'/worlds/{world}/script').json()
    assert client.post(API+f'/worlds/{world}/script/publish',json={'expectedVersion':review['worldVersion']},headers={'Idempotency-Key':'publish'}).status_code==200
    start=client.post(API+f'/worlds/{world}/begin',json={'characterId':'watson'},headers={'Idempotency-Key':'begin'}).json()
    _,definition,state,_=choose('end_release')
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,start['checkpointId']);cp.state,cp.definition=deepcopy(state),deepcopy(definition)
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,None)
        task=db.get(Task,start['taskId']);task.state='cancelled';task.persistence_state='saved'
        db.get(WorldLine,start['lineId']).pending_task_id=None;db.commit()
    action=client.post(API+f"/worlds/{world}/lines/{start['lineId']}/actions",json={
        'expectedCheckpointId':start['checkpointId'],'turnId':'release','kind':'action','message':'办理释放赖德，让赖德离开'},headers={'Idempotency-Key':'release'}).json()
    plan=DirectorOutput(title='释放办理',elapsedMinutes=5,timeLabel='调查后',publicOutcome='赖德获准离去。',transitionIds=['end_release_release']).model_dump()
    def call(task,stage,prompt,data,limit):
        return {'text':json.dumps(plan) if prompt=='world_director' else '赖德获准离去。' if prompt=='world_narrator' else '{"consistent":true,"issues":[]}', 'finishReason':'stop'}
    def crash(*args,**kwargs):
        if fault_at=='after-commit':real_commit(*args,**kwargs);args[0].commit()
        raise OperationalError('injected',{},Exception('synthetic connection loss'))
    with patch('app.world.worker.call_provider',side_effect=call),patch('app.world.worker.commit_turn',side_effect=crash):process(action['taskId'])
    with SessionLocal() as db:
        line=db.get(WorldLine,start['lineId']);cp=db.get(WorldCheckpoint,line.head_checkpoint_id);task=db.get(Task,action['taskId'])
        events=list(db.scalars(select(WorldEvent).where(WorldEvent.world_id==world)))
        if fault_at=='before-commit':
            assert cp.id==start['checkpointId'] and cp.state==state and not events
            assert task.state=='failed' and task.persistence_state=='recoverable'
        else:
            assert cp.id!=start['checkpointId'] and task.state=='succeeded'
            assert cp.state['entities']['ryder']['fields']['处置状态']=='获准离去'
            assert cp.state['plot']['resolution']['completedSteps']==['disposition']
            assert len(events)==1 and events[0].type=='script_transition'
            assert events[0].checkpoint_id==cp.id
            assert cp.state['plot']['transitionReceipts'][0]['checkpointId']==cp.id
            assert task.result['checkpointId']==cp.id and line.pending_task_id is None
    if fault_at=='after-commit':
        with patch('app.world.worker.call_provider',side_effect=AssertionError('Committed work must not call the provider again')):process(action['taskId'])
