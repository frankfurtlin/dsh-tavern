"""Resume only a proven unsent stage; retain and replay completed owned receipts."""
import json
from pathlib import Path
from sqlalchemy import select
from ..models import Task,Consent,ModelProfile,UsageRecord,now
from ..storage import private_path
from ..domain import digest
from ..provider_transport import NOT_SENT_CODES
from ..prose.policy import freeze

PROMPT_NAMES=('world_scene_binding','world_scene_passages','world_scene_surfaces','world_scene_surface_context','world_scene_heads','world_scene_bodies','world_scene_placements','world_scene_people','world_scene_people_inventory','world_scene_aperture','world_scene_supports','world_scene_artwork','world_scene_fixtures','world_scene_review')


def inspection_prompts():
    result={}
    for name in PROMPT_NAMES:
        text=(Path(__file__).parent.parent/'prompts'/f'{name}.txt').read_text('utf8')
        result[name]={'version':text.splitlines()[0],'sha256':digest(text),'text':text}
    return result


def upgrade_inspection(parent,child):
    from ..config import settings
    if settings.ai_mode!='real':return
    upgrades={key:1 for key in ('sceneReviewVersion','lightingInspectionVersion','peopleInventoryVersion','peopleClarificationVersion',
        'supportClarificationVersion','surfaceClarificationVersion','exitInspectionVersion','coordinateInspectionVersion',
        'apertureInspectionVersion','aperturePointVersion','apertureProposalVersion','surfaceContextVersion','cameraPixelsVersion',
        'placementSupportVersion','artworkReviewVersion','placementCameraVersion','coordinateCameraVersion','fixtureInspectionVersion',
        'headCameraSearchVersion','headCropPixelVersion','inspectionPerformanceVersion','actorSurroundingsVersion','inspectionReceiptVersion')}
    upgrades.update(readablePlacementVersion=2,independentSceneReadsVersion=0)
    upgrades['inspectionMaxCalls']=32
    child.descriptor={**child.descriptor,**upgrades}
    if parent.id!=child.id:parent.descriptor={**parent.descriptor,**upgrades}
    for row in (child,parent):
        facts=row.descriptor.get('visualFacts')
        if isinstance(facts,dict) and any(p.get('kind') in ('forbidden-live-animal','absent-transferred-item') for p in facts.get('requirements',[])):
            row.descriptor={**row.descriptor,'visualFacts':{**facts,'cameraInspectionVersion':1}}


def stopped_image_received(parent,child):
    """Only a stopped private operation with real, decryptable received pixels."""
    if (not child or parent.error_code!='SCENE_STOPPED' or parent.status!='failed'
            or child.status!='failed' or child.error_code!='SCENE_CONTEXT_CHANGED'
            or child.descriptor.get('renderStage') not in ('environment','environment-edit','actor')):return False
    try:
        from .service import saved_receipt
        _,data=saved_receipt(child)
    except Exception:
        try:
            from .characters import source_bytes
            data=source_bytes(child)
        except Exception:return False
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as picture:
            if picture.width*picture.height>32*1024*1024:return False
            picture.verify()
    except Exception:return False
    return True


def independent_inspection(db,owner,world_id,line_id,scene_id,expected,source_scene_id=None):
    """Explicit new text operation on received pixels; retain old unknown calls."""
    from ..errors import Problem
    from ..domain import owned
    from ..world.service import require_line, checked_checkpoint
    from ..world.jobs import create_job, TERMINAL
    from ..world.models import WorldCheckpoint
    from .models import SceneImage,SceneProfile
    from .pipeline import is_parent,owned_stage,narrative_context_matches
    from .service import receipt,scene_view,image_bytes
    from .binding import inspection_context,placement_narrative,inspection_stages
    from .grounding import inspection_targets,VERSION
    from .cache import compatible
    world,line=require_line(db,owner,world_id,line_id,lock=True,active=True)
    if line.head_checkpoint_id!=expected:raise Problem('WORLD_CHECKPOINT_CONFLICT','世界已经前进，请检查当前画面',409)
    parent=owned(db,SceneImage,scene_id,owner,lock=True)
    if parent.world_id!=world_id or parent.line_id!=line_id:raise Problem('SCENE_NOT_FOUND','画面不属于当前世界线',404)
    child=owned_stage(db,parent,parent.image.get('pipelineStageId')) if is_parent(parent) else parent
    # Legacy completed parents sometimes omitted the final private-stage pointer.
    # Their own received, hash-verified final bytes can be reviewed directly;
    # no guessed private child or replacement image is needed.
    if child is None and is_parent(parent) and parent.status=='ready' and parent.image.get('file'):
        image_bytes(parent)
        child=parent
    checkpoint=checked_checkpoint(db,owner,world_id,expected)
    view=scene_view(checkpoint,line_id)
    from .cooldown import check_selected
    check_selected(db,owner)
    from .fact_upgrade import eligible as fact_upgrade_eligible,apply as apply_fact_upgrade
    parent_fact_upgrade=not compatible(parent,view) and fact_upgrade_eligible(parent,view)
    fact_upgrade=parent_fact_upgrade or bool(child and (child.descriptor.get('visualFacts') or {})!=(view['descriptor'].get('visualFacts') or {}))
    if not (compatible(parent,view) or parent_fact_upgrade) or is_parent(parent) and not narrative_context_matches(db,parent,line):raise Problem('SCENE_INSPECTION_UNAVAILABLE','当前没有可以独立校验的已收到图片',409)
    if (is_parent(parent) and parent.error_code=='SCENE_STOPPED' and child and child.status=='ready'
            and child.image.get('anchorStatus')=='complete' and not source_scene_id and not fact_upgrade):
        prior=db.get(Task,child.image.get('bindingTaskId')) if child.image.get('bindingTaskId') else None
        profile=db.get(SceneProfile,child.profile_id)
        if (profile and profile.status=='active' and profile.version==child.profile_version
                and (not prior or prior.state=='succeeded' and prior.provider_state=='completed')):
            # The image may have completed just before the stop click. Let the
            # ordinary guarded pipeline adopt that exact receipt, without POST.
            image_bytes(child);upgrade_inspection(parent,child)
            parent.status='queued';parent.error_code=None
            parent.image={**parent.image,'pipelinePhase':child.descriptor.get('renderStage','environment'),'lastStartedAt':now().isoformat()}
            return {**receipt(parent,view),'imageRegenerated':False,'receiptRecovered':True}
    if stopped_image_received(parent,child) and not source_scene_id:
        profile=db.get(SceneProfile,child.profile_id)
        if not profile or profile.status!='active' or profile.version!=child.profile_version:raise Problem('SCENE_CONTEXT_CHANGED','图像通道已变化，请重新打开当前场景',409)
        prior=db.get(Task,child.image.get('bindingTaskId')) if child.image.get('bindingTaskId') else None
        if prior and prior.state not in TERMINAL:raise Problem('SCENE_INSPECTION_RUNNING','当前画面校验仍在停止中，请稍后继续',409)
        apply_fact_upgrade(parent,child,view)
        upgrade_inspection(parent,child)
        child.descriptor={**child.descriptor,'stoppedImageRecovery':{'priorTaskId':prior.id if prior else None,'requestedAt':now().isoformat()}}
        child.image={k:v for k,v in child.image.items() if k not in ('bindingTaskId','anchorStatus','anchorError')}
        child.status='saving';child.error_code=None
        parent.status='queued';parent.error_code=None
        parent.image={**parent.image,'pipelinePhase':child.descriptor['renderStage'],'lastStartedAt':now().isoformat()}
        return {**receipt(parent,view),'imageRegenerated':False,'receiptRecovered':True}
    needs_source=(not child or child.status!='ready'
        or parent.status in ('failed','unknown') and child.image.get('anchorStatus')=='complete')
    if needs_source and is_parent(parent) and parent.status in ('failed','unknown'):
        from .retained import choices,environment
        if not source_scene_id:
            saved=choices(db,parent)
            if saved:source_scene_id=saved[0]['sceneId']
        if source_scene_id:child=environment(db,owned(db,SceneImage,source_scene_id,owner,lock=True))
    if not child or child.status!='ready':raise Problem('SCENE_INSPECTION_UNAVAILABLE','当前没有可以独立校验的已收到图片',409)
    original=db.get(Task,child.image['bindingTaskId']) if child.image.get('bindingTaskId') else None
    if original and original.state not in TERMINAL:raise Problem('SCENE_INSPECTION_RUNNING','当前画面校验仍在进行，请等待结果',409)
    from ..config import settings
    inventory_upgrade=settings.ai_mode=='real' and child.descriptor.get('peopleInventoryVersion')!=1
    if child.image.get('anchorStatus')=='complete' and not inventory_upgrade and not source_scene_id and not fact_upgrade:raise Problem('SCENE_INSPECTION_COMPLETE','画面已经完成校验，无需重复请求',409)
    profile=db.get(SceneProfile,child.profile_id)
    if not profile or profile.status!='active' or profile.version!=child.profile_version:raise Problem('SCENE_CONTEXT_CHANGED','图像通道已变化，请重新打开当前场景',409)
    if source_scene_id:
        from .retained import environment
        from .pipeline import stage,base_key
        source=owned(db,SceneImage,source_scene_id,owner,lock=True)
        source_child=environment(db,source)
        if (not is_parent(parent) or parent.status not in ('failed','unknown') or not source_child
                or source.world_id!=world_id or source.line_id!=line_id or source.checkpoint_id!=expected
                or source.status not in ('failed','unknown','ready') or not (compatible(source,view) or fact_upgrade_eligible(source,view))
                or not narrative_context_matches(db,source,line) or base_key(source)!=base_key(parent)
                or source.profile_id!=parent.profile_id or source.profile_version!=parent.profile_version):
            raise Problem('SCENE_INSPECTION_UNAVAILABLE','已保存图片不属于当前场景，未发起校验',409)
        image_bytes(source_child)
        from copy import deepcopy
        child=stage(db,parent,{**deepcopy(source_child.descriptor),'visualStageRepairs':0,
            'retainedSourceSceneId':source.id},received=source_child,inspect_received=False)
        original=None
    image_bytes(child)  # Verify retained bytes before reserving a new analysis.
    apply_fact_upgrade(parent,child,view)
    upgrade_inspection(parent,child)
    targets=inspection_targets(view,independent_interior=child.descriptor.get('exitInspectionVersion')==1)
    context=inspection_context(child,targets,narrative=placement_narrative(checkpoint))
    stages=inspection_stages(child,targets)
    task=create_job(db,owner,'world_scene_binding',{'worldId':world_id,'lineId':line_id,'sceneId':child.id,'purpose':'explicit-independent-image-review'},stages,max_calls=len(stages))
    task.checkpoint={**task.checkpoint,'bindingInspection':{'sceneId':child.id,'imageHash':child.image['sha256'],'context':context,'version':VERSION},
        'executionProfile':freeze(db.get(ModelProfile,task.payload['profileId'])),'executionPrompts':inspection_prompts(),
        'independentInspectionOfTaskId':original.id if original else None}
    # A user-requested independent review must inspect the pixels afresh. Reusing
    # a received but inconclusive composition/placement answer can otherwise
    # finish with zero calls and reproduce the same failure. The old receipts
    # remain intact; this separately consented job does not resume their calls.
    child.image={**child.image,'bindingTaskId':task.id,'anchorStatus':'queued','anchorError':None,
        'independentInspectionHistory':[*child.image.get('independentInspectionHistory',[]),{'taskId':original.id if original else None,'newTaskId':task.id,'imageHash':child.image['sha256']}],
        'anchorVersion':VERSION,'bindingContext':context,'bindingImageHash':child.image['sha256']}
    if parent.id!=child.id:
        parent.status='queued';parent.error_code=None;parent.image={**parent.image,'pipelinePhase':'independent-inspection','lastStartedAt':now().isoformat()}
    return {**receipt(parent,view),'inspectionTaskId':task.id,'imageRegenerated':False}


def inspection_prompt(task,name):
    from ..errors import Problem
    saved=task.checkpoint.get('executionPrompts',{}).get(name)
    if (name not in PROMPT_NAMES or not isinstance(saved,dict)
            or not isinstance(saved.get('text'),str) or not saved['text']
            or saved.get('sha256')!=digest(saved['text'])
            or saved.get('version')!=saved['text'].splitlines()[0]):
        raise Problem('EXECUTION_CONFIG_INVALID','场景校验模板无法确认，未发送请求',409)
    return saved['text']


def assert_execution(task,profile):
    from ..errors import Problem
    if task.checkpoint.get('executionProfile')!=freeze(profile):
        raise Problem('EXECUTION_CONFIG_INVALID','场景校验模型已变化，未发送请求',409)


def retained_base(db,task,row,context):
    """Read an explicitly selected, completed image-analysis receipt.

    A new independent inspection may retain received composition/body data;
    it never resumes the old task or sends its unresolved stage again.
    """
    reference=task.checkpoint.get('inspectionBaseReceipt')
    if reference is None:return None
    from ..errors import Problem
    from ..domain import digest
    def reject():raise Problem('JOURNAL_INVALID','已有画面测量回执无法确认，未发送请求')
    if not isinstance(reference,dict):reject()
    original=db.get(Task,reference.get('taskId'))
    if (not original or original.id==task.id or original.owner_id!=task.owner_id
            or original.task_type!='world_scene_binding'
            or original.payload.get('sceneId')!=row.id
            or original.payload.get('worldId')!=row.world_id
            or original.payload.get('lineId')!=row.line_id
            or original.result.get('deleted') or original.cancel_requested):reject()
    frozen=original.checkpoint.get('bindingInspection',{})
    if (frozen.get('sceneId')!=row.id or frozen.get('imageHash')!=row.image.get('sha256')
            or frozen.get('context')!=context
            or original.checkpoint.get('executionProfile')!=task.checkpoint.get('executionProfile')
            or original.checkpoint.get('executionPrompts',{}).get('world_scene_binding')
                !=task.checkpoint.get('executionPrompts',{}).get('world_scene_binding')):reject()
    call=original.checkpoint.get('calls',{}).get('analysis:scene-binding',{})
    if not call.get('callId') or reference.get('callId')!=call['callId']:reject()
    usage=db.scalar(select(UsageRecord).where(UsageRecord.provider_call_id==call['callId']))
    if (not usage or usage.owner_id!=task.owner_id or usage.task_id!=original.id
            or usage.status!='completed' or usage.operation!='analysis:scene-binding'):reject()
    path=private_path(task.owner_id,f'journal-{original.id}-{call["callId"]}.json')
    try:saved=json.loads(path.read_text('utf8'));result=saved['result']
    except (OSError,ValueError,KeyError,TypeError):reject()
    if (saved.get('taskId')!=original.id or saved.get('callId')!=call['callId']
            or saved.get('hash')!=digest(json.dumps(result,ensure_ascii=False,sort_keys=True))
            or result.get('finishReason') not in ('stop','completed') or result.get('notSent')):reject()
    return result


def resume_inspection(db,row,task,context,version):
    def reject(reason):
        row.image={**row.image,'bindingRecoveryError':reason}
        return False
    task=db.scalar(select(Task).where(Task.id==task.id).with_for_update().execution_options(populate_existing=True))
    expected={'sceneId':row.id,'imageHash':row.image.get('sha256'),'context':context,'version':version}
    if (not task or task.owner_id!=row.owner_id or task.task_type!='world_scene_binding'
            or task.payload.get('sceneId')!=row.id or task.payload.get('worldId')!=row.world_id
            or task.payload.get('lineId')!=row.line_id or task.state!='failed'
            or task.provider_state!='not_sent' or task.error_code not in NOT_SENT_CODES
            or task.cancel_requested or task.result.get('deleted')
            or task.checkpoint.get('bindingInspection')!=expected):return reject('scope-or-image-changed')
    consent=db.get(Consent,task.payload.get('consentId'));profile=db.get(ModelProfile,task.payload.get('profileId'))
    if (not consent or not profile or consent.owner_id!=row.owner_id or consent.task_id!=task.id
            or consent.expires_at<=now() or profile.owner_id!=row.owner_id or profile.status!='active'
            or profile.version!=consent.profile_version or task.checkpoint.get('executionProfile')!=freeze(profile)):
        return reject('execution-authorization-changed')
    if task.checkpoint.get('executionPrompts')!=inspection_prompts():return reject('inspection-prompts-changed')
    calls=task.checkpoint.get('calls',{});unsent=[]
    for stage,call in calls.items():
        usage=db.scalar(select(UsageRecord).where(UsageRecord.provider_call_id==call.get('callId')))
        if (not usage or usage.owner_id!=row.owner_id or usage.task_id!=task.id
                or usage.consent_id!=consent.id or usage.operation!=stage):return reject('receipt-owner-mismatch')
        path=private_path(row.owner_id,f"journal-{task.id}-{call['callId']}.json")
        if not path.exists():return reject('receipt-missing')
        try:saved=json.loads(path.read_text('utf8'));result=saved['result']
        except (OSError,ValueError,KeyError,TypeError):return reject('receipt-unreadable')
        if saved.get('taskId')!=task.id or saved.get('callId')!=call['callId']:return reject('receipt-scope-mismatch')
        if saved.get('hash')!=digest(json.dumps(result,ensure_ascii=False,sort_keys=True)):return reject('receipt-hash-mismatch')
        if result.get('notSent'):
            if result['notSent']!=task.error_code or usage.status!='not_sent':return reject('unsent-receipt-unconfirmed')
            unsent.append(stage)
        elif usage.status!='completed' or result.get('finishReason') not in ('stop','completed'):
            return reject('completed-receipt-unconfirmed')
    # A task with no call intent has nothing to duplicate. Otherwise only the
    # one failed, fully settled unsent stage may receive another call ID.
    if calls and (len(unsent)!=1 or task.checkpoint.get('providerFailure',{}).get('stage')!=unsent[0]):return reject('failed-stage-unconfirmed')
    history=[*task.checkpoint.get('unsentStageHistory',[]),
        *[{'stage':stage,**calls[stage],'errorCode':task.error_code} for stage in unsent]]
    task.checkpoint={**task.checkpoint,'calls':{s:c for s,c in calls.items() if s not in unsent},
                     'unsentStageHistory':history}
    task.state='queued';task.error_code=None;task.provider_state='not_started'
    task.persistence_state='pending';task.result_outcome=None;task.lease_owner=None;task.lease_expires_at=None
    task.result={**task.result,'phase':'queued'}
    from ..worker import add_event
    add_event(db,task,'phase',{'phase':'queued','reason':'resume-confirmed-unsent-stage'})
    return True
