"""Settled same-image support reuse, without inventing a new supplier receipt."""
from copy import deepcopy
from types import SimpleNamespace as N
from unittest.mock import Mock,patch
import json
import pytest
from app.domain import digest
from app.scenes.placement_receipts import record,restore
from app.scenes.placements import inspect_placements
from .test_scene_supports import proposal,verdict
from .test_scene_placement_review import camera
from .test_scenes import scene_world


@pytest.mark.parametrize('change',[None,'owner','row-owner','world','line','scene','profile','prompts','context',
    'image','explicit','unknown','failed','cancelled','deleted','usage-unknown','usage-owner',
    'part','camera','journal-hash','journal-scope','observation','missing-journal','negative-support',
    'low-confidence','pose','malformed-snapshot','copied-proof','copied-proof-unknown'])
def test_support_reuse_requires_original_settled_observations_and_the_same_scene_contract(tmp_path,change):
    part=proposal();pixels=camera();proof={'text':json.dumps(verdict()),'finishReason':'stop'}
    stage='analysis:slot-check:0:0';context='context';image_hash='a'*64
    metadata={'bindingInspection':{'sceneId':'scene','imageHash':image_hash,'context':context,'version':1},
        'executionProfile':{'version':1},'executionPrompts':{'support':'v1'}}
    task=N(id='new',owner_id='owner',checkpoint={**deepcopy(metadata),'reusePlacementTaskId':'old'})
    saved=record('old',part,proof,stage,pixels)
    prior=N(id='old',owner_id='owner',task_type='world_scene_binding',state='succeeded',provider_state='completed',
        cancel_requested=False,result={},payload={'sceneId':'scene','worldId':'world','lineId':'line'},
        checkpoint={**deepcopy(metadata),'calls':{stage:{'callId':'call'}},
            'completedPlacementReads':{'version':1,'sceneId':'scene','imageHash':image_hash,'context':context,'proofs':[saved]}})
    row=N(id='scene',owner_id='owner',world_id='world',line_id='line',image={'sha256':image_hash},
        descriptor={'inspectionReceiptVersion':1,'readablePlacementVersion':2,'futureCast':[{'entityId':'npc'}]})
    usage=N(owner_id='owner',task_id='old',operation=stage,status='completed')
    if change=='owner':prior.owner_id='foreign'
    elif change=='row-owner':row.owner_id='foreign'
    elif change in ('world','line','scene'):prior.payload[{'world':'worldId','line':'lineId','scene':'sceneId'}[change]]='foreign'
    elif change=='profile':task.checkpoint['executionProfile']={'version':2}
    elif change=='prompts':task.checkpoint['executionPrompts']={'support':'v2'}
    elif change=='context':context='changed'
    elif change=='image':row.image['sha256']='b'*64
    elif change=='explicit':task.checkpoint.pop('reusePlacementTaskId')
    elif change=='unknown':prior.provider_state='unknown'
    elif change=='failed':prior.state='failed'
    elif change=='cancelled':prior.cancel_requested=True
    elif change=='deleted':prior.result={'deleted':True}
    elif change=='usage-unknown':usage.status='reserved'
    elif change=='usage-owner':usage.owner_id='foreign'
    elif change=='part':saved['part']['groundPointPixels'][0]+=20
    elif change=='camera':pixels=b'other received camera'
    elif change=='observation':saved['observationHash']='wrong'
    elif change in ('negative-support','low-confidence'):
        value=verdict();value['footSurface' if change=='negative-support' else 'confidence']='furniture' if change=='negative-support' else .84
        proof['text']=json.dumps(value);saved['observationHash']=digest(proof['text'])
    elif change=='pose':row.descriptor['futureCast'][0]['expectedPose']='standing'
    elif change=='malformed-snapshot':prior.checkpoint['completedPlacementReads']=None
    journal={'taskId':'old','callId':'call','result':proof,'hash':digest(json.dumps(proof,ensure_ascii=False,sort_keys=True))}
    if change=='journal-hash':journal['hash']='wrong'
    elif change=='journal-scope':journal['taskId']='foreign'
    path=tmp_path/'journal-old-call.json'
    if change!='missing-journal':path.write_text(json.dumps(journal))
    db=Mock();db.get.return_value=prior;db.scalar.return_value=usage
    if change in ('copied-proof','copied-proof-unknown'):
        latest=N(**deepcopy(prior.__dict__));latest.id='latest'
        latest.checkpoint['calls']={'analysis:scene-view:0':{'callId':'next'}}
        task.checkpoint['reusePlacementTaskId']='latest'
        next_proof={'text':'{"routes":[]}','finishReason':'stop'}
        (tmp_path/'journal-latest-next.json').write_text(json.dumps({'taskId':'latest','callId':'next','result':next_proof,
            'hash':digest(json.dumps(next_proof,ensure_ascii=False,sort_keys=True))}))
        db.get.side_effect=lambda model,ident:{'old':prior,'latest':latest}.get(ident)
        db.scalar.side_effect=lambda statement:(usage if list(statement.compile().params.values())==['call'] else
            N(owner_id='owner',task_id='latest',operation='analysis:scene-view:0',status='completed'))
        if change=='copied-proof-unknown':prior.provider_state='unknown'
    before=deepcopy(prior.checkpoint)
    with patch('app.scenes.placement_receipts.private_path',side_effect=lambda owner,name:tmp_path/name):parts,records=restore(db,task,row,context,[pixels]*8)
    if change and change!='copied-proof':assert parts==records==[]
    else:
        assert parts==[part] and records==[saved]
        provider=Mock(side_effect=AssertionError('An already verified support must not reserve another call'))
        result=inspect_placements({'exits':[]},[{'entityId':'npc'}],[pixels]*8,'',provider,1000,
            independent=True,verify_support=True,verified_placements=parts,readable_version=2)
        assert result['placements']==[part];provider.assert_not_called()
    assert prior.checkpoint==before


def test_reused_support_cannot_authorize_a_body_over_a_newly_confirmed_doorway():
    part=proposal();door={'visible':True,'passageEvidence':{'viewIndex':0,'boxPixels':part['bodyBoxPixels']}}
    provider=Mock(return_value={'text':'{"placements":[]}'})
    result=inspect_placements({'exits':[door]},[{'entityId':'npc'}],[camera()]*4,'',provider,1000,
        independent=True,verify_support=True,verified_placements=[part],readable_version=2)
    assert result['placements']==[] and provider.call_count==4


@pytest.mark.parametrize('expected_pose',[None,'seated'])
def test_default_pose_preference_preserves_an_explicit_story_pose(expected_pose):
    person={'entityId':'npc',**({'expectedPose':expected_pose} if expected_pose else {})};seen=[]
    def provider(stage,prompt,data,limit):
        seen.append(data)
        assert data['futureCast'][0]==person
        assert 'explicit CURRENT pose' in data['posePreference'] and 'Otherwise prefer standing' in data['posePreference']
        return {'text':'{"placements":[]}'}
    result=inspect_placements({'exits':[]},[person],[camera()]*4,'明确要求坐在椅子上。',provider,1000,independent=True)
    assert result['placements']==[] and len(seen)==4


@pytest.mark.parametrize('claimed',[False,True])
def test_automatic_retry_keeps_metered_support_receipts_and_does_not_repeat_the_valid_placement(client,scene_world,profile,claimed):
    from hashlib import sha256
    from sqlalchemy import select,func
    from app.db import SessionLocal
    from app.models import Task,ModelProfile,UsageRecord
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene,evaluate_inspection
    from app.storage import private_path
    from .test_scene_progressive import prepared_preview,observation
    from .test_scene_placement_review import placement
    from .test_scenes import API
    w=scene_world;_,child_id=prepared_preview(client,w);calls=[]
    before=client.get(API+w['root']).json()
    targets={'characters':[],'fixtures':[],'exits':[]}
    with patch('app.scenes.binding.inspection_targets',return_value=targets):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child_id)
            row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[{'entityId':'npc','name':'店员'}],
                'peopleInventoryVersion':1,'sceneReviewVersion':1,'inspectionPerformanceVersion':1,
                'inspectionReceiptVersion':1,'placementInspectionVersion':2,'placementSupportVersion':1,
                'surfaceContextVersion':1,'readablePlacementVersion':2}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();original=row.image['bindingTaskId']
        def received(model,prompt,data,limit):
            mode=data.get('inspectionMode');calls.append(mode)
            if mode=='single-panorama-composition-v1':value=observation()
            elif mode=='single-camera-physical-head-inventory':value={'count':0,'heads':[],'confidence':.98,'evidence':'No physical people in these original room pixels.'}
            elif mode=='actor-placement':value={'placements':[placement(data['camera']['viewIndex'])] if data['camera']['viewIndex']==0 else []}
            elif mode=='proposed-body-support-context':value={**verdict(),'pose':'standing','pelvisSupport':'none'}
            else:value={'characters':[],'fixtures':[],'exits':[],'placements':[]}
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        def incomplete(*args,**kwargs):
            value=evaluate_inspection(*args,**kwargs)
            value.update(anchorStatus='unavailable',detectionIssues=[{'kind':'characters','id':'npc','reason':'body-evidence-unconfirmed'}])
            return value
        from app.task_lease import ClaimedTask
        execution_id=ClaimedTask(original,'original-lease') if claimed else original
        with patch('app.worker.MockAdapter.generate',side_effect=received),patch('app.scenes.binding.evaluate_inspection',side_effect=incomplete):bind_scene(execution_id)
        with SessionLocal() as db:
            row=db.get(SceneImage,child_id);old=db.get(Task,original)
            assert len(old.checkpoint['completedPlacementReads']['proofs'])==1
            assert type(old.checkpoint['completedPlacementReads']['proofs'][0]['taskId']) is str
            old_fields=deepcopy({c.name:getattr(old,c.name) for c in Task.__table__.columns})
            old_usage=db.scalar(select(func.count()).select_from(UsageRecord))
            journals={call['callId']:sha256(private_path(w['owner'],f"journal-{original}-{call['callId']}.json").read_bytes()).hexdigest()
                for call in old.checkpoint['calls'].values()}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();second=row.image['bindingTaskId']
            assert second!=original and db.get(Task,second).checkpoint['reusePlacementTaskId']==original
        completed_calls=len(calls)
        with patch('app.worker.MockAdapter.generate',side_effect=AssertionError('No additional paid observation is needed')):bind_scene(ClaimedTask(second,'retry-lease') if claimed else second)
    with SessionLocal() as db:
        row=db.get(SceneImage,child_id)
        assert row.image['anchorStatus']=='complete' and row.image['placementPlan'][0]['entityId']=='npc'
        assert {c.name:getattr(db.get(Task,original),c.name) for c in Task.__table__.columns}==old_fields
        assert db.get(Task,second).checkpoint['calls']=={}
        assert db.scalar(select(func.count()).select_from(UsageRecord))==old_usage==completed_calls
        assert all(sha256(private_path(w['owner'],f'journal-{original}-{ident}.json').read_bytes()).hexdigest()==value for ident,value in journals.items())
    assert client.get(API+w['root']).json()==before
