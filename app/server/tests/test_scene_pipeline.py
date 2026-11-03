"""Queue/ownership/recovery contracts; these synthetic images are not visual QA."""
from copy import deepcopy
import io
from unittest.mock import patch
import pytest
from PIL import Image
from sqlalchemy import select,func
from app.db import SessionLocal
from app.errors import Problem
from app.scenes.models import SceneImage
from app.scenes.worker import process_one
from app.scenes.pipeline import validate_placements
from app.scenes.service import image_bytes,save_image
from app.world.models import World,WorldLine
from .test_scenes import scene_world,request,generated


def start(client,w):
    result=request(client,w)
    with SessionLocal() as db:
        row=db.get(SceneImage,result['id']);row.descriptor={**row.descriptor,'renderPipeline':1};db.commit()
    assert process_one()
    with SessionLocal() as db:
        parent=db.get(SceneImage,result['id']);child=db.get(SceneImage,parent.image['pipelineStageId'])
        assert child.descriptor['kind']=='scene-environment' and child.descriptor['sceneCast']==[]
        return result['id'],child.id


def slot(ident):
    return {'entityId':ident,'viewIndex':0,'bodyBoxPixels':[180,160,270,480],
            'groundPointPixels':[225,480],'pose':'standing','confidence':.95,
            'evidence':'Empty floor beside the actual bench, adult headroom below the lintel.'}


def test_unverified_painted_actor_is_not_reused_as_an_already_present_person(client,scene_world):
    from app.scenes.pipeline import advance
    w=scene_world;parent_id,base_id=start(client,w)
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);bad=db.get(SceneImage,base_id)
        cast=deepcopy(bad.descriptor['futureCast']);assert cast
        bad.descriptor={**bad.descriptor,'renderStage':'actor','sceneCast':cast,'futureCast':[]}
        data,metadata,request_id=generated();bad.image=save_image(bad,data,metadata,request_id)
        bad.image={**bad.image,'anchorStatus':'unavailable','bindingImageHash':bad.image['sha256'],
                   'composition':{'verified':True,'issues':[]},
                   'detectionIssues':[{'kind':'characters','id':cast[0]['entityId'],'reason':'body-evidence-unconfirmed'}]}
        bad.status='ready';parent.status='failed';db.commit()
    second=request(client,w,key='new-pipeline',regenerate=True)
    with SessionLocal() as db:
        parent=db.get(SceneImage,second['id']);parent.descriptor={**parent.descriptor,'renderPipeline':1}
        advance(db,parent,db.get(World,parent.world_id),db.get(WorldLine,parent.line_id));db.flush()
        stage=db.get(SceneImage,parent.image['pipelineStageId'])
        assert stage.descriptor['sceneCast']==[] and stage.descriptor['futureCast']
        assert stage.status=='queued' and not stage.image
        db.rollback()


def test_placement_cannot_override_explicit_current_seated_pose_with_standing():
    placement=slot('holmes')
    assert validate_placements({'placements':[placement]},[{'entityId':'holmes','expectedPose':'seated'}])==[]
    placement['pose']='seated'
    assert validate_placements({'placements':[placement]},[{'entityId':'holmes','expectedPose':'seated'}])==[placement]


@pytest.mark.parametrize('evidence_attempts',[0,2])
@pytest.mark.parametrize('unresolved',[False,True])
def test_exhausted_received_route_and_placement_evidence_can_create_a_bounded_new_environment(client,scene_world,evidence_attempts,unresolved):
    from app.models import Task
    from app.scenes.pipeline import advance
    parent_id,base_id=start(client,scene_world)
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);child=db.get(SceneImage,base_id)
        task=Task(owner_id=parent.owner_id,task_type='world_scene_binding',state='failed' if unresolved else 'succeeded',
            provider_state='unknown' if unresolved else 'completed',persistence_state='saved',payload={},checkpoint={},result={})
        db.add(task);db.flush()
        child.status='ready';child.image={'anchorStatus':'unavailable','bindingTaskId':task.id,
            'sha256':'a'*64,'bindingImageHash':'a'*64,
            'pointCorrectionNotes':[{'id':'unmeasured-route','viewIndex':0,
                'nearPixels':[256,350],'farPixels':[256,256],'throughPointPixels':[256,220],
                'actualPointObservations':{'confidence':.96,'unobstructed':False,'sameOpening':True,
                    'openingConfirmed':True,'crossedThreshold':True,'B':{'surface':'furniture','confidence':.96,
                    'evidence':'A thick beige doormat occupies the actual threshold walking segment.'}}}],
            'bindingEvidenceRetries':evidence_attempts,'composition':{'verified':True,'issues':[]},
            'missingExitIds':['unmeasured-route'],'detectionIssues':[
                {'kind':'exits','id':'unmeasured-route','reason':'passage-evidence-unconfirmed'},
                {'kind':'placements','id':None,'reason':'placement-evidence-unconfirmed'}]}
        from app.scenes.binding import queue_binding,inspection_context,placement_narrative
        from app.scenes.grounding import inspection_targets,VERSION
        from app.scenes.service import scene_view
        from app.world.models import WorldCheckpoint
        checkpoint=db.get(WorldCheckpoint,scene_world['cp'])
        context=inspection_context(child,inspection_targets(scene_view(checkpoint,parent.line_id)),
            narrative=placement_narrative(checkpoint))
        child.image={**child.image,'anchorVersion':VERSION,'bindingContext':context}
        with patch('app.scenes.binding.settings.ai_mode','live'),patch('app.world.jobs.create_job') as request_again:
            queue_binding(db,child)
            request_again.assert_not_called()
        advance(db,parent,db.get(World,parent.world_id),db.get(WorldLine,parent.line_id));db.flush()
        corrected=db.get(SceneImage,parent.image['pipelineStageId'])
        if unresolved:
            assert corrected.id==child.id and parent.status=='unknown'
        else:
            assert corrected.id!=child.id and corrected.status=='queued'
            assert corrected.descriptor['visualStageRepairs']==1 and corrected.descriptor['rebuildEnvironmentGuide'] is True
            hint=corrected.descriptor['correctionReasons']['groundChecks'][0]
            assert hint['sourceHash']=='a'*64 and hint['points'][0]['reportedCovering'] is True
            assert hint['points'][0]['panoramaPosition']==[.5,.5]
        assert child.image['bindingTaskId']==task.id and child.image['bindingEvidenceRetries']==evidence_attempts
        assert db.get(WorldLine,parent.line_id).head_checkpoint_id==scene_world['cp']
        db.rollback()


def test_pipeline_waits_for_inspection_and_never_delivers_an_empty_intermediate(client,scene_world):
    w=scene_world;parent_id,base_id=start(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier:
        assert process_one();assert supplier.call_count==1
        with SessionLocal() as db:
            child=db.get(SceneImage,base_id);child.image={**child.image,'anchorStatus':'queued'};db.commit()
        assert not process_one();assert supplier.call_count==1
    for suffix in ('','/image'):
        assert client.get('/api/world/v1/scenes/'+base_id+suffix).status_code==404
    public=client.get('/api/world/v1/scenes/'+parent_id).json()
    assert public['status']=='queued' and public['imageUrl'] is None
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);child=db.get(SceneImage,base_id)
        cast=child.descriptor['futureCast'];assert cast
        assert len(cast)==1  # Independent fixture contains one nearby NPC.
        child.image={**child.image,'anchorStatus':'complete','placementPlan':[slot(cast[0]['entityId'])]};db.commit()
    assert process_one()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);actor=db.get(SceneImage,parent.image['pipelineStageId'])
        assert actor.descriptor['renderStage']=='actor' and actor.descriptor['inputSceneId']==base_id
        assert parent.status=='queued';actor_id=actor.id
    value=io.BytesIO();Image.new('RGB',(512,512),'#bb9955').save(value,format='WEBP')
    with patch('app.scenes.worker.generate',return_value=(value.getvalue(),{'projection':'perspective-edit','width':512,'height':512,'mime':'image/webp'},'actor-request')) as supplier:
        assert process_one();assert supplier.call_count==1
    with SessionLocal() as db:
        actor=db.get(SceneImage,actor_id)
        assert actor.image['projection']=='equirectangular' and actor.image['cameraEdit']['viewIndex']==0
        actor.image={**actor.image,'anchorStatus':'complete','bindingImageHash':actor.image['sha256']};db.commit()
    with patch('app.scenes.worker.generate') as supplier:
        assert process_one();assert not process_one();supplier.assert_not_called()
    public=client.get('/api/world/v1/scenes/'+parent_id).json()
    assert public['status']=='ready' and public['image']['pipelinePhase']=='complete'
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);actor=db.get(SceneImage,actor_id)
        assert image_bytes(parent)==image_bytes(actor)
        assert db.get(WorldLine,w['line']).head_checkpoint_id==w['cp']
        assert parent.image['bindingImageHash']==parent.image['sha256']
        assert parent.descriptor['expectedPoses']==actor.descriptor.get('expectedPoses',{})
        assert parent.image['pipelineStageId']==actor.id


def test_unknown_paid_stage_stops_parent_and_refresh_cannot_resubmit(client,scene_world):
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',side_effect=Problem('SCENE_STATUS_UNKNOWN','Unknown')) as supplier:
        assert process_one();assert process_one();assert not process_one();assert supplier.call_count==1
    reopened=request(client,scene_world,'refresh-unknown-pipeline')
    assert reopened['id']==parent_id and reopened['status']=='unknown'
    with SessionLocal() as db:
        assert db.get(SceneImage,base_id).status=='unknown'
        assert db.scalar(select(func.count()).select_from(SceneImage))==2
        assert db.get(WorldLine,scene_world['line']).head_checkpoint_id==scene_world['cp']


@pytest.mark.parametrize('old_version,status,diagnostic,upgrade',[
    (4,'failed',{'requestSent':False,'responseReceived':False},True),
    (5,'failed',{},False),
    (4,'unknown',{'requestSent':True,'responseReceived':False},False),
    (4,'failed',{},False),
])
def test_old_known_unsent_public_failure_upgrades_once_without_replaying_original(client,scene_world,old_version,status,diagnostic,upgrade):
    parent_id,child_id=start(client,scene_world)
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);child=db.get(SceneImage,child_id)
        parent.descriptor={**parent.descriptor,'environmentGeometryVersion':old_version}
        parent.status=child.status=status
        parent.error_code=child.error_code='SCENE_CONNECTION_FAILED' if status=='failed' else 'SCENE_STATUS_UNKNOWN'
        child.image={**child.image,'failureDiagnostic':diagnostic};db.commit()
    with patch('app.scenes.worker.generate') as supplier:
        updated=request(client,scene_world,'upgrade-new-geometry')
        again=request(client,scene_world,'second-refresh-after-upgrade')
        supplier.assert_not_called()
    assert again['id']==updated['id']
    assert (updated['id']!=parent_id)==upgrade
    with SessionLocal() as db:
        assert db.get(SceneImage,parent_id).status==status and db.get(SceneImage,child_id).status==status
        assert db.scalar(select(func.count()).select_from(SceneImage))==2+int(upgrade)
        assert db.get(WorldLine,scene_world['line']).head_checkpoint_id==scene_world['cp']


def test_ready_public_picture_remains_reusable_across_geometry_upgrade(client,scene_world):
    original=request(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        row=db.get(SceneImage,original['id']);row.descriptor={**row.descriptor,'environmentGeometryVersion':4}
        original_bytes=image_bytes(row);db.commit()
    refreshed=request(client,scene_world,'ready-picture-after-upgrade')
    assert refreshed['id']==original['id']
    with SessionLocal() as db:assert image_bytes(db.get(SceneImage,original['id']))==original_bytes


@pytest.mark.parametrize('provider_state',['completed','unknown'])
def test_old_visual_failure_upgrade_requires_completed_owned_exact_image_inspection(client,scene_world,profile,provider_state):
    from app.models import Task
    from app.scenes.binding import queue_binding
    parent_id,child_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);child=db.get(SceneImage,child_id)
        parent.descriptor={**parent.descriptor,'environmentGeometryVersion':4}
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        task=db.get(Task,child.image['bindingTaskId']);task.provider_state=provider_state
        task.state='succeeded' if provider_state=='completed' else 'failed'
        parent.status='failed';parent.error_code='SCENE_VISUAL_UNCONFIRMED'
        child.image={**child.image,'anchorStatus':'unavailable','bindingImageHash':child.image['sha256']}
        db.commit()
    refreshed=request(client,scene_world,'old-visual-failure-upgrade')
    assert (refreshed['id']!=parent_id)==(provider_state=='completed')
    with SessionLocal() as db:
        assert db.get(SceneImage,parent_id).status=='failed'
        assert db.get(Task,task.id).provider_state==provider_state


@pytest.mark.parametrize('blocked',['none','sent','context'])
def test_refresh_resumes_only_owned_unsent_connection_with_a_finite_budget(client,scene_world,blocked):
    from app.world.models import WorldCheckpoint
    from app.world.state import checkpoint_hash
    parent_id,child_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',side_effect=Problem('SCENE_CONNECTION_FAILED','Not sent',502,
            {'reason':'ConnectError','requestSent':blocked=='sent','responseReceived':False})):
        assert process_one();assert process_one()
    with SessionLocal() as db:
        if blocked=='context':
            old=db.get(WorldCheckpoint,scene_world['cp']);text=old.text+'\n同伴坐下了。'
            new=WorldCheckpoint(owner_id=old.owner_id,world_id=old.world_id,line_id=old.line_id,
                parent_checkpoint_id=old.id,sequence=old.sequence+1,title=old.title,text=text,
                state=deepcopy(old.state),definition=deepcopy(old.definition),memory=deepcopy(old.memory),
                content_hash=checkpoint_hash(old.state,old.definition,text,old.id))
            db.add(new);db.flush();db.get(WorldLine,old.line_id).head_checkpoint_id=new.id
            scene_world['cp']=new.id;db.commit()
    for attempt in range(3):
        refreshed=request(client,scene_world,'resume-connection-'+str(attempt))
        assert refreshed['id']==parent_id
        active=blocked=='none' and attempt<2
        assert refreshed['status']==('queued' if active else 'failed')
        with SessionLocal() as db:
            child=db.get(SceneImage,child_id)
            assert child.image.get('connectionRecoveryAttempts',0)==(min(attempt+1,2) if blocked=='none' else 0)
            assert db.scalar(select(func.count()).select_from(SceneImage))==2
        if active:
            with patch('app.scenes.worker.generate',side_effect=Problem('SCENE_CONNECTION_FAILED','Still not sent',502,
                    {'reason':'ConnectError','requestSent':False,'responseReceived':False})) as supplier:
                assert process_one();assert process_one();assert supplier.call_count==1
    with SessionLocal() as db:assert db.get(WorldLine,scene_world['line']).head_checkpoint_id==scene_world['cp']


def test_another_branch_cannot_resume_an_empty_cast_unsent_pipeline(client,scene_world):
    from app.world.models import WorldCheckpoint
    from app.world.state import checkpoint_hash
    from app.scenes.pipeline import resume_unsent_connection
    parent_id,child_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',side_effect=Problem('SCENE_CONNECTION_FAILED','Not sent',502,
            {'reason':'ConnectError','requestSent':False,'responseReceived':False})):
        assert process_one();assert process_one()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);parent.descriptor={**parent.descriptor,'sceneCast':[]}
        old=db.get(WorldCheckpoint,scene_world['cp'])
        other=WorldLine(owner_id=old.owner_id,world_id=old.world_id,name='另一个存档',status='active',head_checkpoint_id=old.id)
        db.add(other);db.flush()
        fork=WorldCheckpoint(owner_id=old.owner_id,world_id=old.world_id,line_id=other.id,
            parent_checkpoint_id=old.id,sequence=old.sequence+1,title=old.title,text=old.text,
            state=deepcopy(old.state),definition=deepcopy(old.definition),memory=deepcopy(old.memory),kind='fork',
            content_hash=checkpoint_hash(old.state,old.definition,old.text,old.id))
        db.add(fork);db.flush();other.head_checkpoint_id=fork.id
        assert not resume_unsent_connection(db,parent,other)
        db.commit();other_id=other.id;fork_id=fork.id
    other_world={**scene_world,'line':other_id,'cp':fork_id,'root':f"/worlds/{scene_world['world']}/lines/{other_id}"}
    refreshed=request(client,other_world,'other-branch-scene')
    assert refreshed['id']!=parent_id and refreshed['lineId']==other_id
    with SessionLocal() as db:
        assert db.get(SceneImage,parent_id).status=='failed'
        assert db.get(SceneImage,child_id).status=='failed'
        assert not db.get(SceneImage,child_id).image.get('connectionRecoveryAttempts')


@pytest.mark.parametrize('image_bad',[False,True])
def test_valid_empty_geometry_is_reinspected_without_repainting_but_never_published(client,scene_world,image_bad):
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        base=db.get(SceneImage,base_id);base.image={**base.image,'anchorStatus':'unavailable',
            'composition':{'verified':True,'issues':['unexpected-people'] if image_bad else []},
            'bindingImageHash':base.image['sha256'],
            'detectionIssues':[{'kind':'placements','id':None,'reason':'placement-evidence-unconfirmed'}],
            'missingCharacterIds':[],'missingExitIds':[],'missingFixtureIds':[]}
        db.commit()
    assert process_one()
    next_parent=request(client,scene_world,'new-placement-request',regenerate=True)
    assert next_parent['id']!=parent_id
    with SessionLocal() as db:
        row=db.get(SceneImage,next_parent['id']);row.descriptor={**row.descriptor,'renderPipeline':1};db.commit()
    with patch('app.scenes.worker.generate') as supplier:
        assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        row=db.get(SceneImage,next_parent['id']);child=db.get(SceneImage,row.image['pipelineStageId'])
        assert row.status=='queued' and child.id!=base_id
        if image_bad:assert child.status=='queued' and not child.image.get('reusedFromSceneId')
        else:
            assert child.status=='ready' and child.image['reusedFromSceneId']==base_id
            assert child.image.get('anchorStatus')!='complete'
            assert image_bytes(child)==image_bytes(db.get(SceneImage,base_id))
    public=client.get('/api/world/v1/scenes/'+next_parent['id']).json()
    assert public['status']=='queued' and public['imageUrl'] is None


@pytest.mark.parametrize('current_pose',['standing','seated'])
@pytest.mark.parametrize('old_contract',[False,True,'peopleInventoryVersion','peopleClarificationVersion','apertureProposalVersion','surfaceContextVersion','supportClarificationVersion','surfaceClarificationVersion'])
def test_ready_reference_with_old_pose_is_skipped_and_empty_owned_pixels_are_reused(client,scene_world,current_pose,old_contract):
    from app.models import uid
    from app.world.models import WorldCheckpoint
    from app.scenes.service import scene_view
    from app.world.state import checkpoint_hash
    from .test_scene_visits import append_fixture_visit
    w=scene_world
    with SessionLocal() as db:
        cp=db.get(WorldCheckpoint,w['cp']);npc=scene_view(cp)['characters'][0]
        cp.text+=npc['name']+'站着。'
        cp.content_hash=checkpoint_hash(cp.state,cp.definition,cp.text,cp.parent_checkpoint_id);db.commit()
    parent_id,base_id=start(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        base=db.get(SceneImage,base_id);base.image={**base.image,'anchorStatus':'complete'}
        person=base.descriptor['futureCast'][0]
        prior=SceneImage(id=uid(),owner_id=base.owner_id,world_id=base.world_id,line_id=base.line_id,
            checkpoint_id=base.checkpoint_id,profile_id=base.profile_id,profile_version=base.profile_version,
            scene_hash=uid(),generation=1,status='ready',
            descriptor={**deepcopy(base.descriptor),'sceneCast':[person],'futureCast':[],
                        'expectedPoses':{person['entityId']:'standing'}},image={})
        if old_contract is True:
            prior.descriptor={k:v for k,v in prior.descriptor.items() if k!='personInspectionVersion'}
        db.add(prior);db.flush();prior.image={**save_image(prior,image_bytes(base),
            {'width':base.image['width'],'height':base.image['height'],'mime':'image/webp'},'synthetic-reference'),
            'anchorStatus':'complete'}
        db.get(SceneImage,parent_id).status='failed'
        append_fixture_visit(db,w,cp.state['location'],npc['name']+('坐下了。' if current_pose=='seated' else '继续站着。'))
        prior_id=prior.id;db.commit()
    requested=request(client,w,'current-body-state',regenerate=True)
    with SessionLocal() as db:
        row=db.get(SceneImage,requested['id']);row.descriptor={**row.descriptor,'renderPipeline':1,
            **({old_contract:1} if isinstance(old_contract,str) else {})};db.commit()
    with patch('app.scenes.worker.generate') as supplier:
        assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        parent=db.get(SceneImage,requested['id']);source=db.get(SceneImage,parent.image['pipelineStageId'])
        assert source.image['reusedFromSceneId']==(prior_id if current_pose=='standing' and not old_contract else base_id)
        if current_pose=='seated' or old_contract:
            assert source.descriptor['sceneCast']==[]
            assert source.descriptor['futureCast'][0]['expectedPose']==current_pose


def test_private_inspection_connection_recovery_is_reachable_and_bounded(client,scene_world,profile):
    from app.models import Task
    from app.scenes.binding import queue_binding
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        db.commit()
    for attempt in range(3):
        with SessionLocal() as db:
            child=db.get(SceneImage,base_id);prior_id=child.image['bindingTaskId'];prior=db.get(Task,prior_id)
            prior.state='failed';prior.provider_state='not_sent';prior.error_code='PROVIDER_CONNECTION_FAILED'
            child.image={**child.image,'anchorStatus':'unavailable'};db.commit()
        with patch('app.scenes.binding.settings.ai_mode','live'):assert process_one()
        with SessionLocal() as db:
            child=db.get(SceneImage,base_id);parent=db.get(SceneImage,parent_id)
            if attempt<2:
                assert parent.status=='queued' and child.image['anchorStatus']=='queued'
                assert child.image['bindingTaskId']==prior_id
                assert child.image['bindingConnectionRetries']==attempt+1
            else:assert parent.status=='failed' and child.image['bindingTaskId']==prior_id


@pytest.mark.parametrize('invalid',[None,'unknown','profile','hash','context','version','cancelled','usage','narrative'])
def test_identical_private_scene_reuses_only_completed_owned_context_and_never_upgrades_missing_evidence(client,scene_world,profile,invalid):
    from app.models import Task,UsageRecord,uid
    from app.scenes.binding import queue_binding
    from app.scenes.grounding import VERSION
    from app.scenes.pipeline import stage
    parent_id,source_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        source=db.get(SceneImage,source_id)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,source)
        task=db.get(Task,source.image['bindingTaskId']);call_id=uid()
        task.state='succeeded';task.provider_state='completed'
        task.checkpoint={**task.checkpoint,'calls':{'analysis:scene-binding':{'callId':call_id}}}
        db.add(UsageRecord(owner_id=source.owner_id,task_id=task.id,provider_call_id=call_id,
            profile_id=task.payload['profileId'],consent_id=uid(),operation='analysis',
            reserved_input=100,reserved_output=100,input_tokens=10,output_tokens=10,
            status='intent' if invalid=='usage' else 'completed'))
        source.image={**source.image,'anchorStatus':'unavailable','anchorVersion':VERSION,
            'bindingImageHash':source.image['sha256'],'bindingEvidenceRetries':2,
            'composition':{'verified':True,'issues':[]},'missingExitIds':['still-missing'],
            'detectionIssues':[{'reason':'passage-evidence-unconfirmed'}]}
        descriptor=deepcopy(source.descriptor)
        if invalid=='unknown':task.state='failed';task.provider_state='unknown'
        if invalid=='cancelled':task.cancel_requested=True
        if invalid=='profile':source.profile_version+=1
        if invalid=='hash':source.image={**source.image,'bindingImageHash':'another-picture'}
        if invalid=='version':source.image={**source.image,'anchorVersion':VERSION-1}
        if invalid=='context':descriptor['description']='Another location with different evidence.'
        if invalid=='narrative':
            from app.world.models import WorldCheckpoint
            checkpoint=db.get(WorldCheckpoint,source.checkpoint_id)
            checkpoint.text='他已经坐在沙发上，双脚接触地面。'
        db.flush()
        with patch('app.scenes.binding.queue_binding') as inspect:
            clone=stage(db,db.get(SceneImage,parent_id),descriptor,received=source)
        assert inspect.call_count==(1 if invalid else 0)
        if not invalid:
            assert clone.image['anchorStatus']=='unavailable' and clone.image['missingExitIds']==['still-missing']
            assert clone.image['bindingEvidenceRetries']==2 and clone.image['bindingTaskId']==task.id
            assert clone.image['bindingCopiedSceneId']==source_id
            assert image_bytes(clone)==image_bytes(source)
        else:assert not clone.image.get('bindingCopiedSceneId')


def test_real_cast_copy_freezes_head_contract_before_receipt_reuse(client,scene_world,profile):
    from app.models import Task,UsageRecord,uid
    from app.scenes.binding import queue_binding
    from app.scenes.pipeline import stage
    parent_id,source_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        source=db.get(SceneImage,source_id)
        source.descriptor={**source.descriptor,'sceneCast':source.descriptor['futureCast'],
            'futureCast':[],'headInspectionVersion':2}
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,source)
        task=db.get(Task,source.image['bindingTaskId']);call_id=uid()
        task.state='succeeded';task.provider_state='completed'
        task.checkpoint={**task.checkpoint,'calls':{'analysis:scene-binding':{'callId':call_id}}}
        db.add(UsageRecord(owner_id=source.owner_id,task_id=task.id,provider_call_id=call_id,
            profile_id=task.payload['profileId'],consent_id=uid(),operation='analysis',
            reserved_input=100,reserved_output=100,input_tokens=10,output_tokens=10,status='completed'))
        source.image={**source.image,'anchorStatus':'incomplete','missingExitIds':['retained-missing']}
        descriptor=deepcopy(source.descriptor);descriptor.pop('headInspectionVersion');db.flush()
        with patch('app.scenes.binding.queue_binding') as supplier,patch('app.config.settings.ai_mode','real'):
            clone=stage(db,db.get(SceneImage,parent_id),descriptor,received=source)
        supplier.assert_not_called()
        assert clone.descriptor['headInspectionVersion']==2
        assert clone.image['bindingCopiedSceneId']==source_id
        assert clone.image['missingExitIds']==['retained-missing']
        assert image_bytes(clone)==image_bytes(source)


def test_changed_placement_story_cancels_before_sending_any_inspection(client,scene_world,profile):
    from app.models import Task
    from app.world.models import WorldCheckpoint
    from app.scenes.binding import queue_binding,bind_scene
    parent_id,source_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        source=db.get(SceneImage,source_id)
        assert source.descriptor['futureCast']
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,source)
        task_id=source.image['bindingTaskId']
        checkpoint=db.get(WorldCheckpoint,source.checkpoint_id)
        checkpoint.text='他已经坐在沙发上，双脚接触地面。'
        db.commit()
    with patch('app.worker.call_provider') as supplier:
        with pytest.raises(Problem) as error:bind_scene(task_id)
        assert error.value.code=='TASK_CANCELLED'
        supplier.assert_not_called()


def test_complete_placement_receipt_cannot_hide_changed_pose_context(client,scene_world,profile):
    from app.models import Task
    from app.world.models import WorldCheckpoint
    from app.scenes.binding import queue_binding,inspection_context,placement_narrative
    from app.scenes.grounding import inspection_targets
    from app.scenes.service import scene_view
    parent_id,source_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        source=db.get(SceneImage,source_id);checkpoint=db.get(WorldCheckpoint,source.checkpoint_id)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,source)
        old_task=db.get(Task,source.image['bindingTaskId']);old_task.state='succeeded';old_task.provider_state='completed'
        source.image={**source.image,'anchorStatus':'complete','placementPlan':[slot(source.descriptor['futureCast'][0]['entityId'])]}
        old_context=source.image['bindingContext']
        checkpoint.text='他已经坐在沙发上，双脚接触地面。'
        db.flush()
        targets=inspection_targets(scene_view(checkpoint,source.line_id))
        assert inspection_context(source,targets,narrative=placement_narrative(checkpoint))!=old_context
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,source)
        assert source.image['bindingTaskId']!=old_task.id and source.image['anchorStatus']=='queued'
        assert source.image['bindingContext']!=old_context


@pytest.mark.parametrize('unknown',[False,True])
@pytest.mark.parametrize('append_checkpoint',[False,True])
def test_pipeline_consuming_changed_pose_never_paints_a_stale_standing_actor(client,scene_world,profile,unknown,append_checkpoint):
    from app.models import Task
    from app.world.models import WorldCheckpoint
    from app.scenes.binding import queue_binding
    parent_id,source_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        source=db.get(SceneImage,source_id)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,source)
        old_task=db.get(Task,source.image['bindingTaskId']);task_id=old_task.id
        old_task.state='failed' if unknown else 'succeeded';old_task.provider_state='unknown' if unknown else 'completed'
        source.image={**source.image,'anchorStatus':'complete','placementPlan':[slot(source.descriptor['futureCast'][0]['entityId'])]}
        checkpoint=db.get(WorldCheckpoint,source.checkpoint_id)
        original_text=checkpoint.text
        if append_checkpoint:
            new=WorldCheckpoint(owner_id=checkpoint.owner_id,world_id=checkpoint.world_id,line_id=checkpoint.line_id,
                parent_checkpoint_id=checkpoint.id,sequence=checkpoint.sequence+1,title=checkpoint.title,
                text=checkpoint.text+'\n他已坐下，双脚接触地面。',state=deepcopy(checkpoint.state),
                definition=deepcopy(checkpoint.definition),content_hash='d'*64)
            db.add(new);db.flush()
            db.get(WorldLine,source.line_id).head_checkpoint_id=new.id
            current=new
        else:
            checkpoint.text+='\n他已坐下，双脚接触地面。';current=checkpoint
        from app.scenes.cache import compatible
        from app.scenes.service import scene_view
        assert compatible(db.get(SceneImage,parent_id),scene_view(current,source.line_id))
        db.commit()
    with patch('app.config.settings.ai_mode','live'):
        with patch('app.scenes.worker.generate') as supplier:
            assert process_one()
            supplier.assert_not_called()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);source=db.get(SceneImage,source_id)
        assert parent.image['pipelineStageId']==source_id
        assert db.scalar(select(func.count()).select_from(SceneImage).where(SceneImage.descriptor['renderStage'].astext=='actor'))==0
        assert source.image['bindingTaskId']==task_id
        assert parent.status=='failed' and parent.error_code=='SCENE_CONTEXT_CHANGED'
        if append_checkpoint:assert db.get(WorldCheckpoint,source.checkpoint_id).text==original_text


@pytest.mark.parametrize('two_actors',[False,True])
def test_new_checkpoint_after_first_actor_stops_next_actor_and_publication(client,scene_world,two_actors):
    from app.world.models import WorldCheckpoint
    from app.world.state import checkpoint_hash
    from app.scenes.service import save_image
    with SessionLocal() as db:
        checkpoint=db.get(WorldCheckpoint,scene_world['cp'])
        if two_actors:
            definition=deepcopy(checkpoint.definition);state=deepcopy(checkpoint.state)
            first=next(e for e in definition['entities'] if e['type']=='character' and e['id']!=state['playerId']
                       and state['entities'][e['id']].get('location')==state['location'] and e['id'] in state['discovered'])
            second={**deepcopy(first),'id':'peterson','name':'彼得森'}
            definition['entities'].append(second);state['entities']['peterson']=deepcopy(state['entities'][first['id']])
            state['discovered'].append('peterson')
            checkpoint.definition=definition;checkpoint.state=state
            db.get(World,scene_world['world']).definition=definition
        checkpoint.text+='\n同伴站着，彼得森也站着。'
        checkpoint.content_hash=checkpoint_hash(checkpoint.state,checkpoint.definition,checkpoint.text,checkpoint.parent_checkpoint_id)
        db.commit()
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        base=db.get(SceneImage,base_id);cast=base.descriptor['futureCast']
        assert len(cast)==(2 if two_actors else 1)
        placements=[slot(person['entityId']) for person in cast]
        if two_actors:
            placements[1].update(bodyBoxPixels=[345,180,425,480],groundPointPixels=[385,480])
        base.image={**base.image,'anchorStatus':'complete','placementPlan':placements};db.commit()
    assert process_one()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);actor=db.get(SceneImage,parent.image['pipelineStageId'])
        actor_id=actor.id;assert actor.descriptor['renderStage']=='actor' and actor.descriptor['futureCast']==[]
        picture,metadata,receipt=generated()
        actor.image=save_image(actor,picture,metadata,receipt)
        actor.image={**actor.image,'anchorStatus':'complete','bindingImageHash':actor.image['sha256']}
        actor.status='ready';old=db.get(WorldCheckpoint,parent.checkpoint_id)
        new=WorldCheckpoint(owner_id=old.owner_id,world_id=old.world_id,line_id=old.line_id,
            parent_checkpoint_id=old.id,sequence=old.sequence+1,title=old.title,
            text=old.text+'\n彼得森已坐下，同伴也坐下。',state=deepcopy(old.state),definition=deepcopy(old.definition),
            content_hash=checkpoint_hash(old.state,old.definition,old.text+'\n彼得森已坐下，同伴也坐下。',old.id))
        db.add(new);db.flush();db.get(WorldLine,old.line_id).head_checkpoint_id=new.id
        count=db.scalar(select(func.count()).select_from(SceneImage));db.commit()
    with patch('app.scenes.worker.generate') as supplier:
        assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id)
        assert parent.status=='failed' and parent.error_code=='SCENE_CONTEXT_CHANGED'
        assert parent.image['pipelineStageId']==actor_id and not parent.image.get('file')
        assert db.scalar(select(func.count()).select_from(SceneImage))==count


@pytest.mark.parametrize('change_during_dispatch',[False,True])
def test_queued_actor_rechecks_story_before_supplier_post(client,scene_world,change_during_dispatch):
    from app.world.models import WorldCheckpoint
    from app.world.state import checkpoint_hash
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        base=db.get(SceneImage,base_id)
        base.image={**base.image,'anchorStatus':'complete','placementPlan':[slot(base.descriptor['futureCast'][0]['entityId'])]};db.commit()
    assert process_one()
    with SessionLocal() as db:
        actor_id=db.get(SceneImage,parent_id).image['pipelineStageId']
    def change_story():
        with SessionLocal() as db:
            old=db.get(WorldCheckpoint,scene_world['cp']);text=old.text+'\n同伴坐下了。'
            new=WorldCheckpoint(owner_id=old.owner_id,world_id=old.world_id,line_id=old.line_id,
                parent_checkpoint_id=old.id,sequence=old.sequence+1,title=old.title,text=text,
                state=deepcopy(old.state),definition=deepcopy(old.definition),
                content_hash=checkpoint_hash(old.state,old.definition,text,old.id))
            db.add(new);db.flush();db.get(WorldLine,old.line_id).head_checkpoint_id=new.id;db.commit()
    if change_during_dispatch:
        # The supplier adapter calls this authorization immediately before
        # sending HTTP. A change after queue claim must still stop the POST.
        def dispatch(*args,authorize,**kwargs):
            change_story();authorize()
            pytest.fail('Obsolete actor was authorized for a paid POST')
        with patch('app.scenes.worker.generate',side_effect=dispatch):assert process_one()
    else:
        change_story()
        with patch('app.scenes.worker.generate') as supplier:
            assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        actor=db.get(SceneImage,actor_id)
        assert actor.status=='failed' and actor.error_code=='SCENE_CONTEXT_CHANGED'
        assert not actor.image.get('bindingTaskId') and not actor.image.get('file')
    assert process_one()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id)
        assert parent.status=='failed' and parent.error_code=='SCENE_CONTEXT_CHANGED'
        assert parent.image['pipelineStageId']==actor_id and not parent.image.get('file')


def test_private_route_camera_centres_an_oblique_exit_and_its_inverse_projection():
    from math import pi,atan2,sin,cos
    from app.scenes.perspective import edit_camera_yaw,route_repair_boxes,reproject_camera,camera_reference
    from app.scenes.inspection import sphere_pixel,camera_pixel
    descriptor={'routeRepairGuideVersion':2,'spaceKind':'open-air street or market','viewpoint':{'x':0,'z':0},
        'exitRoutes':[{'id':'house','yaw':2*pi/3,'visualCue':'Open townhouse with exterior stone steps.'}],
        'environmentRoutePlan':{'1':[{'id':'house','yaw':2*pi/3,'visualCue':'Open townhouse with exterior stone steps.'}]}}
    heading=edit_camera_yaw(descriptor,1);assert heading==pytest.approx(2*pi/3)
    boxes=route_repair_boxes(descriptor,1)
    assert len(boxes)==1 and boxes[0][0]<256<boxes[0][2]
    for yaw in (heading,-2*pi/3,pi-.01,-pi+.01):
        for x,y in ((26,26),(100,220),(256,310),(486,486)):
            sx,sy=sphere_pixel(1,x,y,2048,1024,camera_yaw=yaw)
            cx,cy=camera_pixel(1,sx,sy,2048,1024,camera_yaw=yaw)
            assert cx==pytest.approx(x) and cy==pytest.approx(y)
    base=io.BytesIO();Image.new('RGB',(2048,1024),'#123456').save(base,format='PNG')
    edit=io.BytesIO();Image.new('RGB',(512,512),'#aa8855').save(edit,format='PNG')
    picture,metadata=reproject_camera(base.getvalue(),edit.getvalue(),1,repair_boxes=boxes,camera_yaw=heading)
    result=Image.open(io.BytesIO(camera_reference(picture,1,camera_yaw=heading))).convert('RGB')
    assert result.getpixel((256,256))[0]>150
    assert metadata['cameraEdit']['yaw']==pytest.approx(heading)
    # The final inspector retains its fixed cardinal views. Its real measured
    # pixels must locate this off-axis exit, not inherit the guide centre 256.
    sx,sy=sphere_pixel(1,256,256,2048,1024,camera_yaw=heading)
    actual_x,_=camera_pixel(1,sx,sy,2048,1024)
    assert actual_x>370


@pytest.mark.parametrize('offset',[0,3.141592653589793])
def test_asymmetric_dense_routes_split_into_centred_safe_frames_across_texture_seam(offset):
    from app.scenes.pipeline import route_edit_plan,route_edit_steps
    from app.scenes.perspective import edit_camera_yaw,route_repair_boxes
    from app.scenes.provider import prompt_for
    from math import pi
    routes=[{'id':str(i),'yaw':yaw+offset,'visualCue':'Open townhouse with exterior stone steps.'}
            for i,yaw in enumerate((-.78,.60,.78))]
    descriptor={'routeRepairGuideVersion':2,'spaceKind':'open-air street or market','viewpoint':{'x':0,'z':0},
        'renderStage':'environment-edit','environmentEdit':'repair-passages','exitRoutes':routes}
    plan=route_edit_plan(descriptor);assert len(plan)==1
    steps=route_edit_steps(plan);assert len(steps)==3
    for step in steps:
        index=step['viewIndex'];value={**descriptor,'environmentCameraIndex':index,
            'environmentRoutePlan':{str(index):[step['route']]}}
        heading=edit_camera_yaw(value,index)
        from math import atan2,sin,cos
        assert abs(atan2(sin(step['route']['yaw']-heading),cos(step['route']['yaw']-heading)))<1e-9
        boxes=route_repair_boxes(value,index)
        assert boxes and all(26<=box[0]<256<box[2]<=486 for box in boxes)
        assert '"openingCentreX": 256' in prompt_for(value)


@pytest.mark.parametrize('inspection',['queued','complete'])
def test_fork_never_selects_private_stage_that_copied_parent_appearance(client,scene_world,inspection):
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id);child.image={**child.image,'anchorStatus':inspection};db.commit()
    w=scene_world
    fork=client.post('/api/world/v1/worlds/'+w['world']+'/fork',json={'checkpointId':w['cp'],'mode':'continue','name':'分步生成时的分支'},headers={'Idempotency-Key':'pipeline-fork'})
    assert fork.status_code==200,fork.text
    line=fork.json();branch={**w,'line':line['id'],'cp':line['headCheckpointId'],'root':f"/worlds/{w['world']}/lines/{line['id']}"}
    result=request(client,branch,'pipeline-fork-scene')
    assert result['id'] not in (parent_id,base_id) and result['status']=='queued'
    assert result['lineId']==branch['line']
    assert not result['descriptor'].get('pipelineParentId')
    with SessionLocal() as db:
        assert db.get(SceneImage,parent_id).status=='queued'
        assert db.get(SceneImage,base_id).image['anchorStatus']==inspection


def test_raw_received_actor_survives_crash_before_encode_without_second_post(client,scene_world):
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id);cast=child.descriptor['futureCast']
        child.image={**child.image,'anchorStatus':'complete','placementPlan':[slot(cast[0]['entityId'])]};db.commit()
    assert process_one()
    received=io.BytesIO();Image.new('RGB',(512,512),'#bb9955').save(received,format='PNG')
    def crashed(*args,**kwargs):
        kwargs['capture'](received.getvalue(),'received-actor-request')
        raise SystemExit('Simulated crash after paid receipt journal')
    with patch('app.scenes.worker.generate',side_effect=crashed) as supplier:
        with pytest.raises(SystemExit):process_one()
        assert supplier.call_count==1
    with SessionLocal() as db:
        child=db.get(SceneImage,db.get(SceneImage,parent_id).image['pipelineStageId'])
        assert child.status=='saving' and child.image['sourceReceived']
        actor_id=child.id
    with patch('app.scenes.worker.generate') as supplier:
        assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        child=db.get(SceneImage,actor_id)
        assert child.status=='ready' and child.image['projection']=='equirectangular'
        assert child.request_id=='received-actor-request'


def test_raw_received_environment_survives_crash_without_second_post(client,scene_world):
    parent_id,base_id=start(client,scene_world)
    data=generated()[0]
    def crashed(*args,**kwargs):
        kwargs['capture'](data,'received-environment-request',0)
        raise SystemExit('Simulated crash after environment receipt journal')
    with patch('app.scenes.worker.generate',side_effect=crashed) as supplier:
        with pytest.raises(SystemExit):process_one()
        assert supplier.call_count==1
    with patch('app.scenes.worker.generate') as supplier:
        assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id)
        assert child.status=='ready' and child.image['projection']=='equirectangular'
        assert child.request_id=='received-environment-request'
        assert db.get(SceneImage,parent_id).status=='queued'


def test_placement_plan_never_accepts_collision_wall_ground_or_foreign_identity():
    cast=[{'entityId':'a'}];good=slot('a')
    assert validate_placements({'placements':[good]},cast)
    for changes in ({'entityId':'foreign'},{'groundPointPixels':[225,100]},
                    {'bodyBoxPixels':[180,160,900,480]},{'viewIndex':4},{'confidence':.79},
                    {'evidence':''},{'bodyBoxPixels':[180,160,270,180]}):
        assert not validate_placements({'placements':[{**good,**changes}]},cast)
    assert not validate_placements({'placements':[good,slot('b')]},[*cast,{'entityId':'b'}])
    # The same physical position seen in two overlapping cameras still collides.
    other={**slot('b'),'viewIndex':1,'bodyBoxPixels':[0,160,50,480],'groundPointPixels':[25,480]}
    first={**good,'bodyBoxPixels':[450,160,510,480],'groundPointPixels':[480,480]}
    assert not validate_placements({'placements':[first,other]},[*cast,{'entityId':'b'}])
    # Rectilinear pixels map nonlinearly at the edge; a centre/radius estimate
    # incorrectly lets these overlapping camera rectangles pass.
    edge_a={**good,'bodyBoxPixels':[0,160,240,480],'groundPointPixels':[120,480]}
    edge_b={**slot('b'),'bodyBoxPixels':[230,160,270,480],'groundPointPixels':[250,480]}
    assert not validate_placements({'placements':[edge_a,edge_b]},[*cast,{'entityId':'b'}])


def test_camera_reprojection_keeps_unseen_directions_and_wraps_the_same_camera():
    from app.scenes.perspective import reproject_camera
    base=io.BytesIO();Image.new('RGB',(1024,512),'#123456').save(base,format='PNG')
    edit=io.BytesIO();Image.new('RGB',(512,512),'#aa8855').save(edit,format='PNG')
    for index,centre in ((0,512),(1,768),(2,0),(3,256)):
        data,metadata=reproject_camera(base.getvalue(),edit.getvalue(),index)
        image=Image.open(io.BytesIO(data)).convert('RGB')
        assert metadata['projection']=='equirectangular'
        assert abs(image.getpixel((centre,256))[0]-170)<6
        assert image.getpixel(((centre+512)%1024,256))==(18,52,86)
        assert image.getpixel((centre,0))==(18,52,86)


def test_failed_route_private_input_preserves_every_pixel_outside_repair_region():
    from app.scenes.perspective import route_repair_reference,route_repair_boxes
    from app.scenes.pipeline import route_edit_plan
    from math import pi
    descriptor={'viewpoint':{'x':0,'z':0},'exitRoutes':[
        {'id':'inn','yaw':0,'visualCue':'tavern'},
        {'id':'house','yaw':2*pi/3,'visualCue':'townhouse'},
        {'id':'market','yaw':4*pi/3,'visualCue':'market'}]}
    descriptor['environmentRoutePlan']=route_edit_plan(descriptor,{'house'})
    boxes=route_repair_boxes(descriptor,1)
    assert len(boxes)==1 and 26<=boxes[0][0]<380<boxes[0][2]<=486
    assert route_repair_boxes(descriptor,0)==[] and route_repair_boxes(descriptor,3)==[]
    actual=io.BytesIO();Image.new('RGB',(512,512),'#123456').save(actual,format='PNG')
    guide=io.BytesIO();Image.new('RGB',(512,512),'#aa8855').save(guide,format='PNG')
    picture=Image.open(io.BytesIO(route_repair_reference(actual.getvalue(),guide.getvalue(),boxes))).convert('RGB')
    left,top,right,bottom=boxes[0]
    assert picture.getpixel(((left+right)//2,(top+bottom)//2))==(170,136,85)
    assert picture.getpixel((left-1,(top+bottom)//2))==(18,52,86)
    assert picture.getpixel((100,250))==(18,52,86)


def test_failed_route_paint_cannot_overwrite_another_correct_opening():
    from app.scenes.perspective import reproject_camera
    from app.scenes.inspection import sphere_pixel
    base=io.BytesIO();Image.new('RGB',(1024,512),'#123456').save(base,format='PNG')
    edit=io.BytesIO();Image.new('RGB',(512,512),'#aa8855').save(edit,format='PNG')
    data,metadata=reproject_camera(base.getvalue(),edit.getvalue(),0,repair_boxes=[[150,140,280,360]])
    result=Image.open(io.BytesIO(data)).convert('RGB')
    def pixel(x,y):
        px,py=sphere_pixel(0,x,y,1024,512)
        return result.getpixel((round(px)%1024,round(py)))
    assert pixel(220,250)==(170,136,85)
    for x,y in ((350,300),(120,220),(200,100),(200,390)):
        assert pixel(x,y)==(18,52,86)
    assert metadata['cameraEdit']['backgroundPreserved'] is True
    assert metadata['cameraEdit']['repairBoxes']==[[150,140,280,360]]


@pytest.mark.parametrize('source_view',[0,4])
def test_adjacent_correct_opening_is_protected_even_when_failed_route_mask_overlaps(source_view):
    from app.scenes.perspective import route_repair_boxes,protected_route_boxes,reproject_camera,route_repair_reference
    from app.scenes.pipeline import route_edit_plan
    from app.scenes.inspection import sphere_pixel,camera_pixel
    from math import pi
    descriptor={'spaceKind':'open-air street or market','viewpoint':{'x':0,'z':0},'exitRoutes':[
        {'id':'bad','yaw':0,'visualCue':'townhouse'},
        {'id':'good','yaw':.3,'visualCue':'tavern'}]}
    descriptor['environmentRoutePlan']=route_edit_plan(descriptor,{'bad'})
    # This is accepted actual-camera evidence, independent of generation yaw.
    corners=[]
    for x,y in ((300,230),(300,340),(346,230),(346,340)):
        px,py=sphere_pixel(0,x,y,2048,1024)
        corners.append(camera_pixel(source_view,px,py,2048,1024))
    box=[min(p[0] for p in corners),min(p[1] for p in corners),max(p[0] for p in corners),max(p[1] for p in corners)]
    nx,ny=sphere_pixel(0,322,340,2048,1024)
    near=list(camera_pixel(source_view,nx,ny,2048,1024))
    descriptor['protectedPassages']=[{'viewIndex':source_view,'apertureBoxPixels':box,'surfaceReview':{'nearPixels':near}}]
    boxes=route_repair_boxes(descriptor,0);protected=protected_route_boxes(descriptor,0)
    assert len(boxes)==len(protected)==1
    assert boxes[0][2]<350  # Same-wall neighbour constrains the guide width.
    base=io.BytesIO();Image.new('RGB',(1024,512),'#123456').save(base,format='PNG')
    edit=io.BytesIO();Image.new('RGB',(512,512),'#aa8855').save(edit,format='PNG')
    data,_=reproject_camera(base.getvalue(),edit.getvalue(),0,repair_boxes=boxes,protected_boxes=protected)
    result=Image.open(io.BytesIO(data)).convert('RGB')
    for x,y in ((322,256),(302,260),(343,260),(322,345)):
        px,py=sphere_pixel(0,x,y,1024,512)
        assert result.getpixel((round(px)%1024,round(py)))==(18,52,86)
    actual=io.BytesIO();Image.new('RGB',(512,512),'#123456').save(actual,format='PNG')
    private=Image.open(io.BytesIO(route_repair_reference(actual.getvalue(),edit.getvalue(),boxes,protected))).convert('RGB')
    assert private.getpixel((322,256))==(18,52,86)
    assert private.getpixel((256,256))==(170,136,85)


def test_correct_near_floor_outside_door_box_survives_neighbour_camera_repair():
    from app.scenes.perspective import protected_route_boxes,route_repair_boxes,reproject_camera
    from app.scenes.inspection import sphere_pixel,camera_pixel
    from app.scenes.pipeline import route_edit_plan
    descriptor={'spaceKind':'open-air street or market','exitRoutes':[
        {'id':'bad','yaw':.95,'visualCue':'townhouse'},
        {'id':'good','yaw':.70,'visualCue':'tavern'}],
        'protectedPassages':[{'viewIndex':0,'apertureBoxPixels':[410,220,455,300],
            'surfaceReview':{'nearPixels':[463,310]}}]}
    descriptor['environmentRoutePlan']=route_edit_plan(descriptor,{'bad'})
    protected=protected_route_boxes(descriptor,1)
    px,py=sphere_pixel(0,463,310,2048,1024)
    local=camera_pixel(1,px,py,2048,1024)
    assert any(a<=local[0]<=c and b<=local[1]<=d for a,b,c,d in protected)
    source=io.BytesIO();Image.new('RGB',(2048,1024),'#123456').save(source,format='PNG')
    edited=io.BytesIO();Image.new('RGB',(512,512),'#aa8855').save(edited,format='PNG')
    data,_=reproject_camera(source.getvalue(),edited.getvalue(),1,
        repair_boxes=route_repair_boxes(descriptor,1),protected_boxes=protected)
    result=Image.open(io.BytesIO(data)).convert('RGB')
    assert result.getpixel((round(px)%2048,round(py)))==(18,52,86)


def test_supplier_background_changes_cannot_escape_measured_actor_region():
    from app.scenes.perspective import reproject_camera
    from app.scenes.inspection import sphere_pixel
    base=io.BytesIO();Image.new('RGB',(1024,512),'#123456').save(base,format='PNG')
    edit=io.BytesIO();Image.new('RGB',(512,512),'#aa8855').save(edit,format='PNG')
    data,metadata=reproject_camera(base.getvalue(),edit.getvalue(),0,slot('a'))
    result=Image.open(io.BytesIO(data)).convert('RGB')
    def observed(x,y):
        px,py=sphere_pixel(0,x,y,1024,512)
        return result.getpixel((round(px)%1024,round(py)))
    assert observed(225,300)==(170,136,85)
    # An untrusted edit changes every pixel, including a neighbouring doorway
    # or wall portrait. Every outside sample remains the original exact RGB.
    for x,y in ((80,180),(330,180),(110,450),(400,300),(256,80)):
        assert observed(x,y)==(18,52,86)
    assert metadata['cameraEdit']['backgroundPreserved'] is True


def test_private_anatomy_guide_does_not_modify_surrounding_room():
    from app.scenes.perspective import camera_reference
    from PIL import ImageChops
    base=io.BytesIO();Image.new('RGB',(1024,512),'#123456').save(base,format='PNG')
    before=Image.open(io.BytesIO(camera_reference(base.getvalue(),0))).convert('RGB')
    after=Image.open(io.BytesIO(camera_reference(base.getvalue(),0,slot('a')))).convert('RGB')
    box=ImageChops.difference(before,after).getbbox()
    assert box and 180<=box[0]<box[2]<=270 and 160<=box[1]<box[3]<=486


@pytest.mark.parametrize('pose',['seated','kneeling'])
@pytest.mark.parametrize('view',[0,2])
def test_nonstanding_guide_includes_head_inside_the_measured_edit_region(pose,view):
    from app.scenes.perspective import camera_reference
    from PIL import ImageChops
    base=io.BytesIO();Image.new('RGB',(1024,512),'#123456').save(base,format='PNG')
    placement={**slot('a'),'viewIndex':view,'pose':pose,'bodyBoxPixels':[318,305,470,486],
               'groundPointPixels':[390,486]}
    before=Image.open(io.BytesIO(camera_reference(base.getvalue(),view))).convert('RGB')
    after=Image.open(io.BytesIO(camera_reference(base.getvalue(),view,placement))).convert('RGB')
    changed=ImageChops.difference(before,after).getbbox()
    assert changed and 318<=changed[0]<changed[2]<=471 and 305<=changed[1]<changed[3]<=487
    assert after.getpixel((394,310))==(169,149,128)  # The full head fits above the shoulders.


@pytest.mark.parametrize('view',[0,1,2,3])
def test_narrowest_tallest_private_guide_stays_inside_measured_empty_space(view):
    from app.scenes.perspective import camera_reference
    from PIL import ImageChops
    base=io.BytesIO();Image.new('RGB',(1024,512),'#123456').save(base,format='PNG')
    placement={**slot('a'),'viewIndex':view,'bodyBoxPixels':[26,26,56,486],
               'groundPointPixels':[41,486]}
    assert validate_placements({'placements':[placement]},[{'entityId':'a'}])
    before=Image.open(io.BytesIO(camera_reference(base.getvalue(),view))).convert('RGB')
    after=Image.open(io.BytesIO(camera_reference(base.getvalue(),view,placement))).convert('RGB')
    box=ImageChops.difference(before,after).getbbox()
    assert box and 26<=box[0]<box[2]<=57 and 26<=box[1]<box[3]<=487


@pytest.mark.parametrize('failure',['projection','missing-route','wrong-destination','unfinished'])
@pytest.mark.parametrize('geometry_version',[5,6])
def test_known_environment_failure_uses_a_new_owned_correction_without_replaying_received_image(client,scene_world,profile,failure,geometry_version):
    from app.models import Task
    from app.scenes.binding import queue_binding
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id)
        child.descriptor={**child.descriptor,'environmentGeometryVersion':geometry_version}
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        t=db.get(Task,child.image['bindingTaskId']);t.state='succeeded';t.provider_state='completed'
        child.image={**child.image,'anchorStatus':'incomplete',
                     'composition':{'verified':True,'issues':['projection-mismatch','unexpected-people'] if failure=='projection' else ['unfinished-artwork'] if failure=='unfinished' else [],
                                    'finishedArtwork':False if failure=='unfinished' else True,'unpaintedGuideRegions':['White blockout facade.'] if failure=='unfinished' else []},
                     'missingExitIds':['market'] if failure=='missing-route' else [],
                     'detectionIssues':[{'kind':'exits','id':'market','reason':'passage-cue-mismatch'}] if failure=='wrong-destination' else []}
        db.commit()
    with patch('app.scenes.binding.settings.ai_mode','live'):assert process_one()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);corrected=db.get(SceneImage,parent.image['pipelineStageId'])
        if failure=='unfinished' and geometry_version==6:
            assert corrected.id!=base_id and corrected.descriptor['environmentEdit']=='finish-materials'
            assert corrected.descriptor['inputSceneId']==base_id and corrected.descriptor['environmentFinished'] is True
            assert corrected.descriptor['remainingEditCameras']==[1,2,3]
            assert parent.status=='queued' and db.get(SceneImage,base_id).status=='ready'
            return  # Four actual-camera dispatch/crash/inspection checks above cover the material-only chain.
        assert corrected.id!=base_id and corrected.descriptor['rebuildEnvironmentGuide'] is (failure!='unfinished')
        assert corrected.descriptor['visualStageRepairs']==1
    with patch('app.scenes.worker.generate',return_value=generated()) as supplier:
        assert process_one()
        assert supplier.call_count==1
        assert (supplier.call_args.args[2] is None) is (failure!='unfinished')
        assert supplier.call_args.args[0]['rebuildEnvironmentGuide'] is (failure!='unfinished')


def test_empty_environment_prompt_omits_crowd_clause_without_changing_source_description():
    from app.scenes.provider import prompt_for
    value={'renderStage':'environment','placeName':'街道','description':'煤气灯街道，行人呼出白气。路边有砖房。',
           'spaceKind':'open-air street or market','lighting':'清晨','sceneCast':[],
           'exitRoutes':[{'id':'inn','name':'酒馆','yaw':0,'visualCue':'Amber windows and wooden barrels.'}]}
    prompt=prompt_for(value)
    assert '行人呼出白气' not in prompt and '路边有砖房' in prompt and 'EXACT PEOPLE COUNT: 0' in prompt
    assert 'Amber windows and wooden barrels' in prompt and '行人呼出白气' in value['description']


def test_outdoor_spherical_guide_contains_sky_facades_and_each_separate_open_path():
    from app.scenes.guide import scene_guide
    from math import pi
    picture=scene_guide({'spaceKind':'open-air street or market',
                         'exitRoutes':[{'yaw':0},{'yaw':2*pi/3},{'yaw':4*pi/3}]})
    assert picture.getpixel((picture.width//2,0))==(181,205,219)
    for yaw in (0,2*pi/3,4*pi/3):
        x=round((.5+yaw/(2*pi))*picture.width)%picture.width
        assert picture.getpixel((x,picture.height//2))==(171,169,151)
    assert picture.getpixel((picture.width*7//12,picture.height//2))!=(171,169,151)


def test_route_linked_stairs_do_not_create_a_second_orphaned_fixture():
    from app.scenes.guide import scene_guide,_portal_landmarks
    descriptor={'spaceKind':'open-air street or market','exitRoutes':[
        {'id':'home','portalId':'stairs','yaw':0,'visualCue':'Open townhouse with exterior stone steps.'}]}
    actual_guide=scene_guide(descriptor)
    linked=scene_guide({**descriptor,'renderLayout':{'nodes':[
        {'id':'stairs','fixture':'steps','worldX':3,'worldZ':2}]}})
    assert linked.tobytes()==actual_guide.tobytes()
    independent=scene_guide({**descriptor,'renderLayout':{'nodes':[
        {'id':'unrelated-stairs','fixture':'steps','worldX':3,'worldZ':2}]}})
    assert independent.tobytes()!=actual_guide.tobytes()
    treads=_portal_landmarks({('z',-6):[('z',0,-6,'residential',1.6)]},0,0)
    assert len(treads)==3 and len({step[6] for step in treads})==3
    # The highest tread meets this doorway; all others approach the same width.
    assert [round(step[4],2) for step in treads]==[.54,.36,.18]
    assert all(step[0]<0<step[3] for step in treads)


def test_new_geometry_contract_cannot_reuse_an_old_failed_stair_picture():
    from app.scenes.pipeline import base_key
    old=SceneImage(world_id='world',profile_version=1,descriptor={'appearance':{'placeId':'street'}})
    new=SceneImage(world_id='world',profile_version=1,descriptor={'appearance':{'placeId':'street'},'environmentGeometryVersion':2})
    assert base_key(old)!=base_key(new)
    assert base_key(new)==base_key(SceneImage(world_id='world',profile_version=1,descriptor=deepcopy(new.descriptor)))


def test_source_named_stair_joins_its_entrance_when_production_node_ids_differ():
    from app.scenes.layout import route_fixture_links
    from app.scenes.guide import scene_guide
    from math import pi
    routes=[{'id':'baker_st','name':'返回贝克街寓所','portalId':'area-60f2151ccba0303d','yaw':2*pi/3,
             'visualCue':'Open townhouse entrance with exterior stone steps.'},
            {'id':'market','name':'考文特花园市场','portalId':'area-60f2151ccba0303d','yaw':4*pi/3,
             'visualCue':'Open market aisle through canvas awnings.'}]
    stairs={'id':'area-d09dcd9f3fb1c362','fixture':'steps','worldX':-1.5,'worldZ':2.6,
            'source':{'quote':'你推开门，走下贝克街寓所的台阶，寒气立刻裹住你的脸。'}}
    layout={'nodes':[{'id':'area-60f2151ccba0303d','fixture':'entrance','worldX':.2,'worldZ':3},stairs]}
    assert route_fixture_links(routes,layout)=={'baker_st':[stairs['id']]}
    descriptor={'spaceKind':'open-air street or market','exitRoutes':routes,'renderLayout':layout}
    # One source-backed stair belongs below its own portal; the generic
    # waypoint must not draw a second stair leading straight into a wall.
    without={**descriptor,'renderLayout':{'nodes':[layout['nodes'][0]]}}
    assert scene_guide(descriptor).tobytes()==scene_guide(without).tobytes()
    unrelated=deepcopy(descriptor);unrelated['renderLayout']['nodes'][1]['source']['quote']='花园远端有一段台阶。'
    assert route_fixture_links(routes,unrelated['renderLayout'])=={}
    assert scene_guide(unrelated).tobytes()!=scene_guide(without).tobytes()
    ambiguous=deepcopy(routes);ambiguous.append({**routes[0],'id':'another-house'})
    assert route_fixture_links(ambiguous,layout)=={}


@pytest.mark.parametrize('pose',[{'x':0,'z':0},{'x':4.5,'z':-3.8}])
def test_local_stair_correction_is_legible_and_camera_remains_inside_its_private_geometry(pose):
    from math import pi
    from app.scenes.guide import guide_half_extent,portal_geometry
    from app.scenes.perspective import route_repair_boxes
    from app.scenes.provider import prompt_for
    route={'id':'home','yaw':2*pi/3,'visualCue':'Open townhouse entrance with exterior stone steps.'}
    descriptor={'routeRepairGuideVersion':3,'spaceKind':'open-air street or market','viewpoint':pose,
        'exitRoutes':[route],'environmentRoutePlan':{'1':[route]},'environmentCameraIndex':1,
        'renderStage':'environment-edit','environmentEdit':'repair-passages'}
    extent=guide_half_extent(descriptor)
    assert extent>max(abs(pose['x']),abs(pose['z']))
    portal=portal_geometry(descriptor)[0]
    assert min(abs(abs(portal['x'])-extent),abs(abs(portal['z'])-extent))<1e-8
    box=route_repair_boxes(descriptor,1)[0]
    assert 26<=box[0]<256<box[2]<=486 and box[3]-box[1]>250
    prompt=prompt_for(descriptor)
    assert 'THREE distinct stone steps DIRECTLY below this SAME open doorway' in prompt
    assert 'NOT sky or another exterior street' in prompt


def test_environment_generation_receives_known_structural_fixtures_including_missing_steps():
    from app.scenes.provider import prompt_for
    descriptor={'renderStage':'environment','placeName':'街道','description':'结霜街道。',
        'spaceKind':'open-air street or market','lighting':'清晨','sceneCast':[],
        'renderLayout':{'nodes':[{'id':'entry','name':'街道'},
            {'id':'steps','name':'台阶旁','fixture':'steps','worldX':-1.5,'worldZ':2.6}]}}
    prompt=prompt_for(descriptor)
    assert 'MANDATORY visible physical fixtures' in prompt and '"fixture": "steps"' in prompt
    assert 'distinct treads and risers' in prompt and '"worldX": -1.5' in prompt


@pytest.mark.parametrize('cue,expected,absent',[
    ('Open townhouse threshold with exterior stone steps.','real exterior stone treads','amber windows'),
    ('Open market aisle with canvas awnings.','unobstructed aisle','amber windows'),
    ('Open tavern doorway with amber windows.','warm furnished tavern interior','residential threshold')])
def test_local_passage_edit_requests_only_landmarks_in_its_actual_camera(cue,expected,absent):
    from app.scenes.provider import prompt_for
    descriptor={'renderStage':'environment-edit','environmentEdit':'repair-passages',
                'environmentCameraIndex':0,'environmentRoutePlan':{'0':[{'yaw':0,'visualCue':cue}]}}
    prompt=prompt_for(descriptor)
    assert expected in prompt and absent not in prompt
    assert 'ZERO PEOPLE' in prompt and 'Image 1 camera' in prompt


def test_route_guide_has_receding_floor_and_landmarks_around_unblocked_thresholds():
    from app.scenes.guide import scene_guide
    from app.scenes.inspection import camera_images
    from PIL import Image
    from io import BytesIO
    from math import pi
    routes=[{'yaw':0,'visualCue':'Open tavern entrance with amber windows and barrels.'},
            {'yaw':pi/2,'visualCue':'Open market aisle with canvas awnings.'},
            {'yaw':pi,'visualCue':'Open townhouse entrance with steps.'}]
    picture=scene_guide({'spaceKind':'open-air street or market','exitRoutes':routes})
    out=BytesIO();picture.save(out,format='PNG')
    views=[Image.open(BytesIO(v)) for v in camera_images(out.getvalue(),labelled=False)]
    for index in range(3):
        # Horizon facade and lower receding pavement are visibly different:
        # the former single blue rectangle gave no depth or open floor.
        assert views[index].getpixel((256,256))!=views[index].getpixel((256,335))
    tavern_colours=views[0].getcolors(512*512)
    market_colours=views[1].getcolors(512*512)
    # Camera transport is JPEG, so compare a small colour neighbourhood.
    def near_count(colours,target):return sum(count for count,colour in colours if max(abs(a-b) for a,b in zip(colour,target))<10)
    assert near_count(tavern_colours,(226,166,72))>150
    assert near_count(market_colours,(188,151,91))>150


@pytest.mark.parametrize('count',[3,8,16,24])
def test_dense_hub_keeps_separate_open_thresholds_in_private_guide(count):
    from app.scenes.guide import scene_guide
    from math import pi
    picture=scene_guide({'spaceKind':'open-air street or market','exitRoutes':[{'yaw':2*pi*i/count} for i in range(count)]})
    # Sample the underlying ray grid once per column; Lanczos ringing at
    # resized boundaries must not count as an extra physical doorway.
    # Above eye level the actual opening shows its distant facade OR sky;
    # floor grid and distant window seams do not fragment one passage.
    row=[min(max(abs(a-b) for a,b in zip(picture.getpixel((round((x+.5)*picture.width/1024)%picture.width,round(232*picture.height/512))),colour)) for colour in ((171,169,151),(181,205,219)))<5 for x in range(1024)]
    runs=sum(flag and not row[index-1] for index,flag in enumerate(row))
    assert runs==count


@pytest.mark.parametrize('count',[3,8,16,24])
def test_market_landmark_solids_never_cross_any_neighbouring_approach(count):
    from app.scenes.guide import _portal_landmarks
    from math import sin,cos,pi
    portals=[]
    for yaw in [-.1,0,.1] if count==3 else [2*pi*i/count for i in range(count)]:
        tx,tz=sin(yaw),-cos(yaw);distance=6/max(abs(tx),abs(tz))
        portals.append(('x' if abs(tx)>abs(tz) else 'z',tx*distance,tz*distance,'market'))
    grouped={}
    for axis,px,pz,motif in portals:
        key=(axis,round(px if axis=='x' else pz))
        gaps=[abs((pz-qz) if axis=='x' else (px-qx)) for qa,qx,qz,_ in portals if qa==axis and round(qx if axis=='x' else qz)==key[1] and (qx,qz)!=(px,pz)]
        grouped.setdefault(key,[]).append((axis,px,pz,motif,min(1.6,min(gaps,default=100)*.42)))
    solids=[b for b in _portal_landmarks(grouped,0,0) if b[1]<2]
    # Independently sample the walking centre and both sides at tabletop
    # height. The earlier upper-doorway pixel test missed blocked lower paths.
    for group in grouped.values():
        for _,px,pz,_,opening in group:
            length=(px*px+pz*pz)**.5
            for i in range(1,121):
                for offset in (-.8,0,.8):
                    x=px*i/120-pz/length*opening*offset
                    z=pz*i/120+px/length*opening*offset
                    assert not any(b[0]<x<b[3] and b[2]<z<b[5] and b[1]<.78<b[4] for b in solids)


def test_nine_separate_inspection_images_reach_the_actual_vision_wire(monkeypatch):
    import json
    from app.providers import ChatCompletionsAdapter
    from .test_provider_transport import setup_transport,success
    async def respond(request,attempt):return success()
    profile,requests=setup_transport(monkeypatch,respond)
    images=['data:image/jpeg;base64,eA==']*9
    ChatCompletionsAdapter().generate(profile,'Inspection',{'_sceneImages':images,'cameraViews':{'count':8}},32)
    body=json.loads(requests[0].content)
    content=body['messages'][1]['content']
    assert len([p for p in content if p['type']=='image_url'])==9
    assert '_sceneImages' not in content[0]['text'] and 'cameraViews' in content[0]['text']
    with pytest.raises(Problem) as rejected:
        ChatCompletionsAdapter().generate(profile,'Inspection',{'_sceneImages':[*images,*images[:3]]},32)
    assert rejected.value.code=='SCENE_IMAGE_INVALID' and len(requests)==1


def test_environment_stage_does_not_inherit_costume_or_identity_instructions():
    from app.scenes.provider import prompt_for
    value={'renderStage':'environment','placeName':'起居室','description':'炉火、报纸、楼梯。',
           'spaceKind':'interior','lighting':'清晨','narrativeStyle':'19世纪末英国伦敦；英国人物穿绅士礼帽与女仆裙装。',
           'sceneCast':[],'futureCast':[{'entityId':'a','name':'福尔摩斯'}]}
    prompt=prompt_for(value)
    assert 'EXACTLY ZERO PEOPLE' in prompt and '19世纪末英国伦敦' in prompt
    assert '女仆裙装' not in prompt and 'Preserve each character' not in prompt


@pytest.mark.parametrize('version',[4,5])
def test_route_repair_uses_original_materials_and_the_actual_world_period(version):
    from app.scenes.provider import prompt_for
    value={'renderStage':'environment-edit','environmentEdit':'repair-passages',
        'routeRepairGuideVersion':version,'environmentCameraIndex':0,
        'environmentRoutePlan':{'0':[{'yaw':0,'visualCue':'Open townhouse threshold with stone steps.'}]},
        'narrativeStyle':'中国玄幻乌坦城；角色穿着传统服饰。'}
    prompt=prompt_for(value)
    assert '中国玄幻乌坦城' in prompt and 'world and period' in prompt
    assert 'Victorian' not in prompt and '角色穿着传统服饰' not in prompt
    if version==4:
        assert 'Image 2 is the ORIGINAL camera painting' in prompt
        assert 'INSIDE the corrected patch preserve only its opening/floor geometry' in prompt
    else:
        assert 'ONE actual scene image' in prompt and 'Image 2' not in prompt
        assert 'REPLACE any wrong outside street' in prompt
        assert 'Correction region in the INPUT 512x512 image' in prompt
    assert 'Keep Image 1 camera, perspective, painting style, materials' not in prompt


def test_actor_edit_does_not_request_world_bystander_costumes():
    from app.scenes.provider import prompt_for
    value={'renderStage':'actor','narrativeStyle':'19世纪末英国伦敦；英国人物穿绅士礼帽与女仆裙装。',
           'paintedActor':{'name':'福尔摩斯','appearance':'40岁英国男性，灰色男士便装'},'placement':slot('holmes')}
    prompt=prompt_for(value)
    assert 'EXACTLY ONE physical person' in prompt and '灰色男士便装' in prompt
    assert '女仆裙装' not in prompt and 'Preserve each character' not in prompt


def test_geometry_repair_preserves_retained_people_without_empty_environment_instruction():
    from app.scenes.provider import prompt_for
    value={'renderStage':'environment','placeName':'起居室','description':'炉火、报纸、楼梯。',
           'spaceKind':'interior','lighting':'清晨','sceneCast':[{'name':'福尔摩斯','appearance':'40岁英国男性'}],
           'correctionReasons':{'missingExits':['street']}}
    prompt=prompt_for(value)
    assert 'Preserve EXACTLY these 1 existing physical people' in prompt
    assert 'EXACTLY ZERO PEOPLE' not in prompt and 'EMPTY' not in prompt and 'No human subject' not in prompt


@pytest.mark.parametrize('expected',['open-air courtyard','open-air street or market','outdoor natural landscape','open-air setting'])
def test_every_outdoor_subtype_rejects_an_actual_indoor_picture(expected):
    from app.scenes.binding import composition_from_result
    report={'characters':[],'composition':{'projection':'equirectangular','confidence':.95,
        'spaceKind':'interior','openSky':False,'visiblePeopleCount':0,'unexpectedPeople':[],'referenceArtifacts':[]}}
    assert 'space-mismatch' in composition_from_result(report,[],{'spaceKind':expected})['issues']
    assert 'space-mismatch' not in composition_from_result(report,[],{'spaceKind':'unspecified setting'})['issues']


def test_exit_requires_actual_open_passage_and_anchor_on_its_floor():
    from app.scenes.inspection import panorama_coordinates
    from app.scenes.grounding import spatial_anchors_from_result
    part={'id':'street','visible':True,'passable':True,'confidence':.95,'viewIndex':2,'anchorPixels':[200,410],
          'passageEvidence':{'kind':'door-open','viewIndex':2,'boxPixels':[150,150,250,390],'description':'Open doorway and continuous visible floor.'}}
    targets={'exits':[{'id':'street'}]}
    def anchors(p):return spatial_anchors_from_result(panorama_coordinates({'exits':[p]},(2048,1024)),targets,(2048,1024),require_evidence=True)
    assert 'exits:street' in anchors(part)
    for changes in ({'passageEvidence':None},{'passageEvidence':{**part['passageEvidence'],'kind':'door-closed'}},
                    {'anchorPixels':[420,410]},{'anchorPixels':[200,160]},
                    {'passageEvidence':{**part['passageEvidence'],'viewIndex':0}}):
        assert not anchors({**part,**changes})


def test_camera_edge_placement_is_rejected_before_paying_for_a_cutoff_actor():
    assert not validate_placements({'placements':[{**slot('a'),'bodyBoxPixels':[440,300,512,470],'groundPointPixels':[470,465]}]},[{'entityId':'a'}])


def test_actual_downward_table_contact_is_not_rejected_for_panorama_pole_distortion():
    from app.scenes.inspection import panorama_coordinates
    from app.scenes.grounding import spatial_anchors_from_result
    value=panorama_coordinates({'fixtures':[{'id':'table','visible':True,'confidence':.95,'viewIndex':4,'anchorPixels':[300,470]}]},(2048,1024))
    assert value['fixtures'][0]['anchorPixels'][1]>1024*.92
    assert 'fixtures:table' in spatial_anchors_from_result(value,{'fixtures':[{'id':'table'}]},(2048,1024),require_evidence=True)
    # A panorama-space guess without a real camera observation remains invalid.
    part=value['fixtures'][0];part.pop('cameraAnchorPixels')
    assert not spatial_anchors_from_result(value,{'fixtures':[{'id':'table'}]},(2048,1024),require_evidence=True)


def test_proposed_person_cannot_block_an_actual_open_passage():
    good=slot('a');passage={'visible':True,'passageEvidence':{'viewIndex':0,'boxPixels':[170,130,290,490]}}
    assert not validate_placements({'placements':[good],'exits':[passage]},[{'entityId':'a'}])


@pytest.mark.parametrize('body_view,passage_view',[(0,4),(1,5),(2,6),(3,7)])
def test_downward_exit_measurement_excludes_people_across_the_spherical_seam(body_view,passage_view):
    person={**slot('a'),'viewIndex':body_view}
    passage={'visible':True,'passable':True,'passageEvidence':{
        'viewIndex':passage_view,'boxPixels':[170,130,280,470]}}
    assert not validate_placements({'placements':[person],'exits':[passage]},[{'entityId':'a'}])
    # A passage on the other side of the same sphere is genuinely clear.
    opposite={**passage,'passageEvidence':{**passage['passageEvidence'],'viewIndex':4+(passage_view-4+2)%4}}
    assert validate_placements({'placements':[person],'exits':[opposite]},[{'entityId':'a'}])


@pytest.mark.parametrize('unexpected_people',[False,True])
@pytest.mark.parametrize('render_stage',['environment','environment-edit'])
def test_single_picture_inspection_completes_all_camera_stages_through_usage_database(client,scene_world,profile,unexpected_people,render_stage):
    import json
    from app.models import Task,ModelProfile,UsageRecord
    from app.scenes.binding import queue_binding,bind_scene
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    targets={'characters':[],'fixtures':[],'exits':[{'id':'route','visualCue':'Open market aisle with a canvas awning.',
        'allowedPassageKinds':['path-open'],'independentReviewRequired':True,'requiredReviewSource':'single-camera-measurement'}]}
    with SessionLocal() as db:
        p=db.get(ModelProfile,profile['id']);p.context_window=200000;db.commit()
    with patch('app.scenes.binding.inspection_targets',return_value=targets):
        with SessionLocal() as db:
            child=db.get(SceneImage,base_id)
            child.descriptor={**child.descriptor,'renderStage':render_stage}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
            db.commit();tid=child.image['bindingTaskId']
        calls=[]
        def received(profile,prompt,data,limit):
            calls.append(data)
            if not data.get('inspectionMode'):
                assert data['futureCast'] and data['placementContract']['requiredIds']
                assert 'placements' in data['requiredOutputKeys']
                assert 'publicNarrative' in data
            result={'composition':{'projection':'equirectangular','confidence':.95,'spaceKind':'interior','visiblePeopleCount':0,'unexpectedPeople':[],'referenceArtifacts':[],
                'finishedArtwork':True,'unpaintedGuideRegions':[]},'characters':[],'fixtures':[],'exits':[]}
            if unexpected_people:result['composition'].update(visiblePeopleCount=1,unexpectedPeople=['An unrequested woman by the door.'])
            if data.get('inspectionMode')=='single-camera':
                result={'routes':[{'id':'route','image':1,'matched':False,'continuous':False,'confidence':.95,'evidence':'No canvas awning or market aisle exists in this actual image.'}]}
            return {'text':json.dumps(result),'finishReason':'stop','latencyMs':1,'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):bind_scene(tid)
    with SessionLocal() as db:
        task=db.get(Task,tid);assert task.state=='succeeded' and task.provider_state=='completed'
        usage=list(db.scalars(select(UsageRecord).where(UsageRecord.task_id==tid)))
        assert len(usage)==len(calls)==(1 if unexpected_people else 9)
        assert {r.operation for r in usage}==({'analysis:scene-binding'} if unexpected_people else {'analysis:scene-binding',*[f'analysis:scene-view:{i}' for i in range(8)]})
        assert db.get(SceneImage,base_id).image['anchorStatus']!='complete'


def test_candidate_wall_point_is_checked_through_durable_usage_database(client,scene_world,profile):
    import json
    from app.models import Task,ModelProfile,UsageRecord
    from app.scenes.binding import queue_binding,bind_scene
    from .test_scene_surfaces import observation
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    targets={'characters':[],'fixtures':[],'exits':[{'id':'secret_destination','visualCue':'Open path',
        'allowedPassageKinds':['path-open'],'independentReviewRequired':True,
        'requiredReviewSource':'single-camera-measurement','requiredGroundEvidence':True,'requiredSurfaceReview':True}]}
    with SessionLocal() as db:
        db.get(ModelProfile,profile['id']).context_window=200000;db.commit()
    with patch('app.scenes.binding.inspection_targets',return_value=targets):
        with SessionLocal() as db:
            child=db.get(SceneImage,base_id)
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
            db.commit();tid=child.image['bindingTaskId']
        calls=[];views=[]
        def received(profile,prompt,data,limit):
            calls.append(data)
            if data.get('inspectionMode')=='blind-surface-points':
                assert 'secret_destination' not in json.dumps({k:v for k,v in data.items() if k!='_sceneImages'})
                assert len(data['_sceneImages'])==5
                result=observation('wall')
            elif data.get('inspectionMode')=='single-camera':
                views.append(data);first=len(views)==1
                result={'routes':[{'id':'secret_destination','image':1,'matched':first,'continuous':first,
                    'confidence':.95,'kind':'path-open','thresholdBoxPixels':[180,130,325,390],
                    'anchorPixels':[250,380],'beyondPointPixels':[250,310],
                    'apertureBoxPixels':[180,130,325,350],'throughPointPixels':[250,290],
                    'groundEvidence':{'nearSurface':'walkable-ground','farSurface':'walkable-ground',
                                      'unobstructed':True,'continuation':'through-opening'},'cueFacts':[],
                    'evidence':'Measured near and far walking surfaces reported in this exact opening.'}]}
            else:result={'composition':{'projection':'equirectangular','confidence':.95,'spaceKind':'interior',
                'visiblePeopleCount':0,'unexpectedPeople':[],'referenceArtifacts':[]},'characters':[],'fixtures':[],'exits':[]}
            return {'text':json.dumps(result),'finishReason':'stop','latencyMs':1,'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):bind_scene(tid)
    with SessionLocal() as db:
        task=db.get(Task,tid);child=db.get(SceneImage,base_id)
        usage=list(db.scalars(select(UsageRecord).where(UsageRecord.task_id==tid)))
        assert task.state=='succeeded' and task.provider_state=='completed'
        assert len(usage)==len(calls)==10
        assert 'analysis:scene-ground:0' in {r.operation for r in usage}
        assert child.image['missingExitIds']==['secret_destination']
        assert child.image['pointCorrectionNotes'][0]['actualPointObservations']['B']['surface']=='wall'
        assert not task.checkpoint.get('verifiedImageInspection')


@pytest.mark.parametrize('stop',['resume','unknown','corrupt-receipt','changed-scene','expired','cancelled','profile-changed','prompt-changed'])
def test_paid_inspection_receipts_survive_unsent_stage_recovery(client,scene_world,profile,stop):
    from datetime import timedelta
    from app.models import Task,ModelProfile,UsageRecord,Consent,now
    from app.scenes.binding import queue_binding
    from app.worker import call_provider
    from app.storage import private_path
    from app.completion_policy import completion_limit
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    targets={'characters':[],'fixtures':[],'exits':[{'id':'exit','independentReviewRequired':True}]}
    with patch('app.scenes.binding.inspection_targets',return_value=targets):
        with SessionLocal() as db:
            child=db.get(SceneImage,base_id)
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
            db.commit();tid=child.image['bindingTaskId']
            task=db.get(Task,tid);limit=completion_limit(db.get(ModelProfile,task.payload['profileId']),'analysis',world=True)
        received={'text':'{"measured":"received"}','finishReason':'stop','latencyMs':1,
                  'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        stages=[('analysis:scene-binding','world_scene_binding'),('analysis:scene-view:0','world_scene_passages')]
        with patch('app.worker.MockAdapter.generate',return_value=received) as supplier:
            for stage,prompt in stages:assert call_provider(tid,stage,prompt,{'_structured':True},limit)==received
            assert supplier.call_count==2
        failure='PROVIDER_STATUS_UNKNOWN' if stop=='unknown' else 'PROVIDER_CONNECTION_FAILED'
        with patch('app.worker.MockAdapter.generate',side_effect=Problem(failure,'fixture',502,{'requestSent':False} if stop!='unknown' else {})):
            with pytest.raises(Problem):call_provider(tid,'analysis:scene-ground:0','world_scene_surfaces',{'_structured':True},limit)
        with SessionLocal() as db:
            task=db.get(Task,tid);task.state='failed';task.error_code=failure
            child=db.get(SceneImage,base_id);child.image={**child.image,'anchorStatus':'unavailable'}
            if stop=='corrupt-receipt':
                call=task.checkpoint['calls'][stages[0][0]]['callId']
                private_path(task.owner_id,f'journal-{tid}-{call}.json').write_text('{"hash":"invalid","result":{}}','utf8')
            if stop=='changed-scene':child.descriptor={**child.descriptor,'lighting':'a different actual scene'}
            if stop=='expired':db.get(Consent,task.payload['consentId']).expires_at=now()-timedelta(seconds=1)
            if stop=='cancelled':task.cancel_requested=True
            if stop=='profile-changed':db.get(ModelProfile,task.payload['profileId']).version+=1
            if stop=='prompt-changed':
                task.checkpoint={**task.checkpoint,'executionPrompts':{}}
            db.commit()
        with SessionLocal() as db:
            child=db.get(SceneImage,base_id)
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
            db.commit();task=db.get(Task,tid)
            assert child.image['bindingTaskId']==tid
            if stop!='resume':assert task.state=='failed';return
            assert task.state=='queued',child.image.get('bindingRecoveryError')
            assert len(task.checkpoint['unsentStageHistory'])==1
        with patch('app.worker.MockAdapter.generate',return_value=received) as supplier:
            for stage,prompt in stages:assert call_provider(tid,stage,prompt,{'_structured':True},limit)==received
            assert supplier.call_count==0
            call_provider(tid,'analysis:scene-ground:0','world_scene_surfaces',{'_structured':True},limit)
            assert supplier.call_count==1
        with SessionLocal() as db:
            usage=list(db.scalars(select(UsageRecord).where(UsageRecord.task_id==tid)))
            assert len(usage)==4 and sorted(r.status for r in usage)==['completed']*3+['not_sent']
            assert db.get(Consent,db.get(Task,tid).payload['consentId']).calls_reserved==3


def test_received_detector_recovery_preserves_both_bounds_when_failures_alternate(client,scene_world,profile):
    from app.models import Task,ModelProfile
    from app.scenes.binding import queue_binding
    from app.worker import call_provider
    from app.completion_policy import completion_limit
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        db.commit()
    for attempt in range(2):
        with SessionLocal() as db:
            child=db.get(SceneImage,base_id);t=db.get(Task,child.image['bindingTaskId']);tid=t.id
            limit=completion_limit(db.get(ModelProfile,t.payload['profileId']),'analysis',world=True)
        received={'text':'{"characters":[]}','finishReason':'stop','latencyMs':1,
                  'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',return_value=received):
            call_provider(tid,'analysis:scene-binding','world_scene_binding',{'_structured':True},limit)
        with SessionLocal() as db:
            t=db.get(Task,tid);assert t.provider_state=='completed'
            t.state='succeeded';child=db.get(SceneImage,base_id)
            child.image={**child.image,'anchorStatus':'unavailable','composition':{'verified':True,'issues':[]},
                'detectionIssues':[{'reason':'body-evidence-unconfirmed'}]};db.commit()
        with patch('app.scenes.binding.settings.ai_mode','live'):assert process_one()
        with SessionLocal() as db:
            child=db.get(SceneImage,base_id);assert child.image['bindingEvidenceRetries']==attempt+1
            t=db.get(Task,child.image['bindingTaskId']);retry_id=t.id
        with patch('app.worker.MockAdapter.generate',side_effect=Problem('PROVIDER_CONNECTION_FAILED','unsent',502,{'requestSent':False})):
            with pytest.raises(Problem):call_provider(retry_id,'analysis:scene-view:0','world_scene_passages',{'_structured':True},limit)
        with SessionLocal() as db:
            t=db.get(Task,retry_id);t.state='failed';t.error_code='PROVIDER_CONNECTION_FAILED'
            child=db.get(SceneImage,base_id);child.image={**child.image,'anchorStatus':'unavailable'};db.commit()
        with patch('app.scenes.binding.settings.ai_mode','live'):assert process_one()
        with SessionLocal() as db:
            child=db.get(SceneImage,base_id)
            assert child.image['bindingConnectionRetries']==attempt+1
            assert child.image['bindingEvidenceRetries']==attempt+1
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id);tid=child.image['bindingTaskId'];t=db.get(Task,tid)
        t.state='succeeded';t.provider_state='completed';child.image={**child.image,'anchorStatus':'unavailable'};db.commit()
    with patch('app.scenes.binding.settings.ai_mode','live'):assert process_one()
    with SessionLocal() as db:
        assert db.get(SceneImage,base_id).image['bindingTaskId']==tid
        assert db.get(SceneImage,parent_id).status=='failed'


def test_old_contract_cannot_resend_an_unresolved_running_detector(client,scene_world,profile):
    from app.models import Task
    from app.scenes.binding import queue_binding
    from app.scenes.grounding import VERSION
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        db.commit();tid=child.image['bindingTaskId']
        t=db.get(Task,tid);t.state='failed';t.provider_state='running';t.error_code='PERSISTENCE_UNAVAILABLE'
        child.image={**child.image,'anchorVersion':VERSION-1,'anchorStatus':'unavailable'};db.commit()
    with patch('app.scenes.binding.settings.ai_mode','live'):assert process_one()
    with SessionLocal() as db:
        assert db.get(SceneImage,base_id).image['bindingTaskId']==tid
        assert db.get(SceneImage,parent_id).status=='unknown'


def test_completed_old_placement_contract_is_reinspected_before_a_paid_actor(client,scene_world,profile):
    from app.models import Task
    from app.scenes.binding import queue_binding
    from app.scenes.grounding import VERSION
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        tid=child.image['bindingTaskId'];prior=db.get(Task,tid)
        prior.state='succeeded';prior.provider_state='completed'
        child.image={**child.image,'anchorStatus':'complete','anchorVersion':VERSION-1,
            'placementPlan':[slot(child.descriptor['futureCast'][0]['entityId'])]}
        db.commit()
    with patch('app.scenes.worker.settings.ai_mode','live'),patch('app.scenes.worker.generate') as supplier:
        assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id);parent=db.get(SceneImage,parent_id)
        assert child.image['anchorStatus']=='queued' and child.image['bindingTaskId']!=tid
        assert child.image['anchorVersion']==VERSION and parent.image['pipelineStageId']==base_id


def test_saved_placement_recheck_keeps_actual_exit_evidence(client,scene_world):
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id);ident=child.descriptor['futureCast'][0]['entityId']
        child.image={**child.image,'anchorStatus':'complete','placementPlan':[slot(ident)],
            'placementPassages':[{'visible':True,'passageEvidence':{'viewIndex':4,'boxPixels':[170,130,280,470]}}]}
        db.commit()
    with patch('app.scenes.worker.generate') as supplier:
        assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id)
        assert parent.status=='failed' and parent.error_code=='SCENE_PLACEMENT_UNCONFIRMED'
        assert parent.image['pipelineStageId']==base_id


def test_new_parent_never_discards_unknown_inspection_receipt_when_copying_pixels(client,scene_world,profile):
    from app.models import Task
    from app.scenes.binding import queue_binding
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        db.commit();t=db.get(Task,child.image['bindingTaskId'])
        t.state='failed';t.provider_state='unknown';t.error_code='PROVIDER_STATUS_UNKNOWN'
        child.image={**child.image,'anchorStatus':'unavailable','composition':{'verified':True,'issues':[]},
            'detectionIssues':[{'reason':'placement-evidence-unconfirmed'}],
            'bindingImageHash':child.image['sha256'],'missingExitIds':[],'missingFixtureIds':[]};db.commit()
    assert process_one()
    new=request(client,scene_world,'independent-parent-after-unknown',regenerate=True)
    with SessionLocal() as db:
        row=db.get(SceneImage,new['id']);row.descriptor={**row.descriptor,'renderPipeline':1};db.commit()
    assert process_one()
    with SessionLocal() as db:
        row=db.get(SceneImage,new['id']);child=db.get(SceneImage,row.image['pipelineStageId'])
        assert child.status=='queued' and not child.image.get('reusedFromSceneId')
        assert not child.image.get('bindingTaskId')


def test_completed_environment_edit_with_only_missing_measurement_reuses_pixels_then_reinspects(client,scene_world,profile):
    from app.models import Task
    from app.scenes.binding import queue_binding
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id)
        child.descriptor={**child.descriptor,'renderStage':'environment-edit'}
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        task=db.get(Task,child.image['bindingTaskId']);task.state='succeeded';task.provider_state='completed'
        child.image={**child.image,'anchorStatus':'unavailable','composition':{'verified':True,'issues':[]},
            'detectionIssues':[{'reason':'passage-evidence-unconfirmed'}],
            'bindingImageHash':child.image['sha256'],'missingExitIds':['road'],'missingFixtureIds':[]}
        db.get(SceneImage,parent_id).status='failed';db.commit()
        original_hash=child.image['sha256']
    new=request(client,scene_world,'new-independent-point-recheck',regenerate=True)
    with SessionLocal() as db:
        row=db.get(SceneImage,new['id']);row.descriptor={**row.descriptor,'renderPipeline':1};db.commit()
    with patch('app.scenes.worker.generate') as supplier,patch('app.scenes.binding.settings.ai_mode','live'):
        assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        row=db.get(SceneImage,new['id']);child=db.get(SceneImage,row.image['pipelineStageId'])
        assert child.status=='ready' and child.image['reusedFromSceneId']==base_id
        assert child.image['sha256']==original_hash and child.image['anchorStatus']=='queued'
        assert row.status=='queued'  # No unverified public picture is published.


def start_cleanup(client,w,issue='unexpected-people'):
    from app.models import Task
    from app.scenes.binding import queue_binding
    parent_id,base_id=start(client,w)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        task=db.get(Task,child.image['bindingTaskId']);task.state='succeeded';task.provider_state='completed'
        child.descriptor={**child.descriptor,'environmentGeometryVersion':6}
        child.image={**child.image,'anchorStatus':'incomplete','composition':{'verified':True,'issues':[issue]}}
        db.commit()
    assert process_one()
    return parent_id,base_id


@pytest.mark.parametrize('issue',['unexpected-people','unfinished-artwork'])
def test_cleanup_preserves_owned_architecture_and_inspects_only_final_sphere(client,scene_world,profile,issue):
    parent_id,base_id=start_cleanup(client,scene_world,issue)
    previous=base_id
    paid=io.BytesIO();Image.new('RGB',(512,512),'#ab9955').save(paid,format='PNG')
    value=(paid.getvalue(),{'width':512,'height':512,'mime':'image/webp','projection':'perspective-edit'},'cleanup-receipt')
    for index in range(4):
        with SessionLocal() as db:
            parent=db.get(SceneImage,parent_id);child=db.get(SceneImage,parent.image['pipelineStageId'])
            ident=child.id
            assert child.descriptor['renderStage']=='environment-edit'
            assert child.descriptor['environmentCameraIndex']==index
            assert child.descriptor['inputSceneId']==previous
        with patch('app.scenes.worker.generate',return_value=value) as supplier,patch('app.scenes.binding.queue_binding') as inspect:
            assert process_one();assert supplier.call_count==1
            descriptor,_,reference=supplier.call_args.args
            assert Image.open(io.BytesIO(reference)).size==(512,512)
            assert descriptor['characterReferenceOrder']==[] and 'character_references' not in supplier.call_args.kwargs
            assert 'architecture_reference' not in supplier.call_args.kwargs
            if issue=='unfinished-artwork':
                from app.scenes.provider import prompt_for
                assert descriptor['environmentEdit']=='finish-materials'
                prompt=prompt_for(descriptor)
                assert 'ONE actual camera image' in prompt and 'Preserve every real opening' in prompt
            assert inspect.call_count==(1 if index==3 else 0)
        with SessionLocal() as db:
            child=db.get(SceneImage,ident);assert child.image['projection']=='equirectangular'
            if index<3:assert child.image['anchorStatus']=='planned-edit'
            else:child.image={**child.image,'anchorStatus':'queued'};db.commit()
            assert db.get(SceneImage,parent_id).status=='queued'
        assert client.get('/api/world/v1/scenes/'+ident+'/image').status_code==404
        if index<3:assert process_one()
        else:assert not process_one()  # Final inspection must finish before any publication or actor.
        previous=ident


def test_cleanup_paid_receipt_resumes_projection_without_second_post(client,scene_world,profile):
    parent_id,_=start_cleanup(client,scene_world)
    paid=io.BytesIO();Image.new('RGB',(512,512),'#ab9955').save(paid,format='PNG')
    def crashed(*args,**kwargs):
        kwargs['capture'](paid.getvalue(),'received-cleanup-request')
        raise SystemExit('Simulated crash after paid environment edit')
    with patch('app.scenes.worker.generate',side_effect=crashed) as supplier:
        with pytest.raises(SystemExit):process_one()
        assert supplier.call_count==1
    with patch('app.scenes.worker.generate') as supplier,patch('app.scenes.binding.queue_binding') as inspect:
        assert process_one();supplier.assert_not_called();inspect.assert_not_called()
    with SessionLocal() as db:
        child=db.get(SceneImage,db.get(SceneImage,parent_id).image['pipelineStageId'])
        assert child.status=='ready' and child.image['projection']=='equirectangular'
        assert child.request_id=='received-cleanup-request' and child.image['anchorStatus']=='planned-edit'


@pytest.mark.parametrize('reason',['unknown','failed','context'])
def test_cleanup_stops_without_resubmitting_or_continuing_after_failed_context(client,scene_world,profile,reason):
    parent_id,_=start_cleanup(client,scene_world)
    with SessionLocal() as db:
        child=db.get(SceneImage,db.get(SceneImage,parent_id).image['pipelineStageId'])
        if reason=='context':
            world=db.get(World,scene_world['world'])
            world.status='archived'
        else:child.status=reason;child.error_code='SCENE_STATUS_UNKNOWN' if reason=='unknown' else 'SCENE_PROVIDER_FAILED'
        db.commit()
    with patch('app.scenes.worker.generate') as supplier:
        assert process_one()
        if reason=='context':assert process_one()  # Stop both queued private child and its parent.
        assert not process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        assert db.get(SceneImage,parent_id).status==('failed' if reason=='context' else reason)
        assert db.scalar(select(func.count()).select_from(SceneImage))==3


@pytest.mark.parametrize('evidence_only',[False,True])
@pytest.mark.parametrize('crash',[False,True])
def test_missing_outdoor_landmarks_receive_local_route_geometry_without_whole_sphere_repaint(client,scene_world,profile,evidence_only,crash):
    from app.models import Task
    from app.scenes.binding import queue_binding
    from math import pi
    parent_id,base_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base_id)
        child.descriptor={**child.descriptor,'exitRoutes':[
            {'id':'inn','yaw':0,'visualCue':'Open tavern doorway with amber windows and barrels.'},
            {'id':'house','yaw':2*pi/3,'visualCue':'Open townhouse threshold with exterior stone steps.'},
            {'id':'market','yaw':4*pi/3,'visualCue':'Open market aisle through canvas awnings and empty stalls.'}]}
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        task=db.get(Task,child.image['bindingTaskId']);task.state='succeeded';task.provider_state='completed'
        child.image={**child.image,'anchorStatus':'unavailable' if evidence_only else 'incomplete',
            'bindingEvidenceRetries':2 if evidence_only else 0,'composition':{'verified':True,'issues':[]},
            'missingExitIds':['market'],'detectionIssues':[{'kind':'exits','id':'market',
                'reason':'passage-evidence-unconfirmed' if evidence_only else 'passage-cue-mismatch'}]}
        db.commit()
    assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,db.get(SceneImage,parent_id).image['pipelineStageId'])
        assert child.descriptor['renderStage']=='environment-edit'
        assert child.descriptor['environmentEdit']=='repair-passages' and child.descriptor['environmentCameraIndex']==3
        assert child.descriptor['remainingEditCameras']==[]
        assert list(child.descriptor['environmentRoutePlan'])==['3']
        assert child.descriptor['visualStageRepairs']==1 and child.descriptor['inputSceneId']==base_id
    paid=io.BytesIO();Image.new('RGB',(512,512),'#ab9955').save(paid,format='PNG')
    value=(paid.getvalue(),{'width':512,'height':512,'mime':'image/webp','projection':'perspective-edit'},'received-local-architecture')
    def received_then_crashed(*args,**kwargs):
        kwargs['capture'](paid.getvalue(),'received-local-architecture')
        raise SystemExit('Simulated crash after paid architectural receipt')
    with patch('app.scenes.binding.settings.ai_mode','live'), patch('app.scenes.worker.generate',side_effect=received_then_crashed if crash else None,return_value=value) as supplier:
        if crash:
            with pytest.raises(SystemExit):process_one()
        assert process_one()
        assert supplier.call_count==1
        assert Image.open(io.BytesIO(supplier.call_args.args[2])).size==(512,512)
        from app.scenes.perspective import camera_reference
        with SessionLocal() as db:
            original=camera_reference(image_bytes(db.get(SceneImage,base_id)),3,camera_yaw=-2*pi/3)
        assert 'architecture_reference' not in supplier.call_args.kwargs
        assert supplier.call_args.args[2]==original  # Actual pixels only; no private geometry or competing picture.
    with SessionLocal() as db:
        child=db.get(SceneImage,db.get(SceneImage,parent_id).image['pipelineStageId'])
        assert child.image['anchorStatus']=='queued' and db.get(SceneImage,parent_id).status=='queued'
        assert child.image['cameraEdit']['yaw']==pytest.approx(-2*pi/3)
