"""One budgeted visual inspection binds actors to received panorama pixels."""
import base64, io, json
from math import pi, isfinite
from PIL import Image
from sqlalchemy import select
from ..db import SessionLocal
from ..models import Task, ModelProfile, now
from ..errors import Problem
from ..config import settings
from ..world.models import World, WorldLine
from .models import SceneImage
from .grounding import VERSION, inspection_targets, spatial_anchors_from_result, queue_visual_repair


def inspection_image(source):
    """Bound base64 input even for a highly detailed forest panorama."""
    with Image.open(io.BytesIO(source)) as original:
        for width in (2048,1536,1280,1024):
            im=original.convert('RGB');im.thumbnail((width,width//2))
            for quality in (75,65,55,45):
                output=io.BytesIO();im.save(output,format='JPEG',quality=quality)
                if len(output.getvalue())<=240000:return output.getvalue(),im.size
    raise Problem('SCENE_IMAGE_LIMIT','人物校验图片无法压缩到可用范围，未发送请求',422)


def placement_narrative(checkpoint):
    """The exact public snippet used for future actor placement, never secrets."""
    from ..world.state import public_payload
    return public_payload(checkpoint.text,checkpoint.state,checkpoint.definition)[-3000:]


def narrative_hash(text):
    from hashlib import sha256
    return sha256(text.encode()).hexdigest()


def inspection_scene(row):
    """One frozen artwork contract for generation, reuse and crash recovery."""
    descriptor=getattr(row,'descriptor',{})
    value={k:descriptor.get(k) for k in ('placeName','description','spaceKind','lighting')}
    if descriptor.get('sceneReviewVersion')==1:
        value.update(sceneReviewVersion=1,sceneImageHash=(getattr(row,'image',None) or {}).get('sha256'))
    if descriptor.get('lightingInspectionVersion')==1:value['lightingInspectionVersion']=1
    if descriptor.get('peopleInventoryVersion')==1:value['peopleInventoryVersion']=1
    if descriptor.get('peopleClarificationVersion')==1:value['peopleClarificationVersion']=1
    if descriptor.get('environmentGeometryVersion',0)>=4 or descriptor.get('routeRepairGuideVersion',0)>=4:
        value['artworkInspectionVersion']=1
    if descriptor.get('headInspectionVersion') in (1,2):
        value['headInspectionVersion']=descriptor['headInspectionVersion']
    if descriptor.get('bodyInspectionVersion') == 1:
        value['bodyInspectionVersion'] = 1
    if descriptor.get('identityInspectionVersion') == 1:
        value['identityInspectionVersion'] = 1
    if descriptor.get('personInspectionVersion') == 1 and descriptor.get('headInspectionVersion') in (1,2):
        value['personInspectionVersion'] = 1
    if descriptor.get('placementInspectionVersion') in (1,2):
        value['placementInspectionVersion'] = descriptor['placementInspectionVersion']
    if descriptor.get('exitInspectionVersion')==1:value['exitInspectionVersion']=1
    if descriptor.get('coordinateInspectionVersion')==1:value['coordinateInspectionVersion']=1
    if descriptor.get('apertureInspectionVersion')==1:value['apertureInspectionVersion']=1
    if descriptor.get('aperturePointVersion')==1:value['aperturePointVersion']=1
    if descriptor.get('apertureProposalVersion')==1:value['apertureProposalVersion']=1
    if descriptor.get('surfaceContextVersion')==1:value['surfaceContextVersion']=1
    if descriptor.get('supportClarificationVersion')==1:value['supportClarificationVersion']=1
    if descriptor.get('surfaceClarificationVersion')==1:value['surfaceClarificationVersion']=1
    if descriptor.get('cameraPixelsVersion')==1:value['cameraPixelsVersion']=1
    if descriptor.get('placementSupportVersion')==1:value['placementSupportVersion']=1
    if descriptor.get('artworkReviewVersion')==1:value['artworkReviewVersion']=1
    if descriptor.get('placementCameraVersion')==1:value['placementCameraVersion']=1
    if descriptor.get('coordinateCameraVersion')==1:value['coordinateCameraVersion']=1
    if descriptor.get('fixtureInspectionVersion')==1:value['fixtureInspectionVersion']=1
    if descriptor.get('headCameraSearchVersion')==1:value['headCameraSearchVersion']=1
    if descriptor.get('headCropPixelVersion')==1:value['headCropPixelVersion']=1
    if descriptor.get('visualFacts'):value['visualFacts']=descriptor['visualFacts']
    if descriptor.get('publicAppearanceContextVersion')==1:
        value['publicAppearanceContextVersion']=1
        value['publicAppearanceContext']=descriptor.get('publicAppearanceContext',{})
    if descriptor.get('readablePlacementVersion') in (1,2):value['readablePlacementVersion']=descriptor['readablePlacementVersion']
    if descriptor.get('inspectionReceiptVersion')==1:value['inspectionReceiptVersion']=1
    if descriptor.get('independentSceneReadsVersion')==1:value['independentSceneReadsVersion']=1
    for key in ('measurementRecoveryVersion','peopleCountConflictVersion','inventoryContinuityVersion','measurementEfficiencyVersion','globalEvidenceRecoveryVersion'):
        if descriptor.get(key)==1:value[key]=1
    return value


def artwork_confirmed(row):
    if inspection_scene(row).get('artworkInspectionVersion')!=1:return True
    observed=row.image.get('composition',{})
    return (observed.get('verified') is True and observed.get('finishedArtwork') is True
            and observed.get('unpaintedGuideRegions')==[]
            and not any(issue in ('artwork-unconfirmed','unfinished-artwork') for issue in observed.get('issues',[])))


def inspection_context(row,targets,*,narrative=None):
    from hashlib import sha256
    from ..domain import canonical
    value={'targets':targets,'cast':row.descriptor.get('sceneCast',[]),
        'futureCast':row.descriptor.get('futureCast',[]),'expectedPoses':row.descriptor.get('expectedPoses',{}),
        'scene':inspection_scene(row)}
    if row.descriptor.get('futureCast'):
        value['placementInspectionVersion']=2
        value['placementNarrativeHash']=(narrative_hash(narrative) if narrative is not None
                                         else row.descriptor.get('placementNarrativeHash'))
    return sha256(canonical(value).encode()).hexdigest()


def placement_context_matches(db,row):
    """A saved pose is usable only with its actual frozen public narrative."""
    if not row.descriptor.get('futureCast'):return True
    from ..world.models import WorldCheckpoint
    from .service import scene_view
    checkpoint=db.get(WorldCheckpoint,row.checkpoint_id)
    if not checkpoint or checkpoint.owner_id!=row.owner_id or checkpoint.world_id!=row.world_id:return False
    narrative=placement_narrative(checkpoint)
    targets=inspection_targets(scene_view(checkpoint,row.line_id),independent_interior=row.descriptor.get('exitInspectionVersion')==1)
    return (row.descriptor.get('placementNarrativeHash')==narrative_hash(narrative)
            and row.image.get('bindingContext')==inspection_context(row,targets,narrative=narrative))


def restore_verified_inspection(db,row):
    """Revalidate a successful exact-image receipt locally, without a new call.

    Receipts predating the stored image hash cannot authorize this recovery.
    Scope, cast, route evidence and all current geometry checks still apply.
    """
    from ..world.models import WorldCheckpoint
    from .service import scene_view
    checkpoint=db.get(WorldCheckpoint,row.checkpoint_id)
    if not checkpoint or checkpoint.owner_id!=row.owner_id or checkpoint.world_id!=row.world_id:return False
    targets=inspection_targets(scene_view(checkpoint,row.line_id),independent_interior=row.descriptor.get('exitInspectionVersion')==1);narrative=placement_narrative(checkpoint)
    context=inspection_context(row,targets,narrative=narrative)
    candidates=[row]
    if not row.descriptor.get('pipelineParentId'):
        candidates+=list(db.scalars(select(SceneImage).where(SceneImage.owner_id==row.owner_id,
            SceneImage.world_id==row.world_id,SceneImage.line_id==row.line_id,
            SceneImage.descriptor['pipelineParentId'].astext==row.id,
            SceneImage.image['sha256'].astext==row.image.get('sha256'),SceneImage.status=='ready')))
    candidate_ids={};contexts={}
    for candidate in candidates:
        candidate_context=context
        if candidate.id!=row.id and not row.descriptor.get('expectedPoses'):
            # Published legacy receipts omitted the last private edit's frozen
            # poses. Only that SAME owned final image may supply its pose check;
            # cast, current targets and all other scene context remain public-row
            # inputs, and the saved hash/context must still match exactly.
            frozen=SceneImage(descriptor={**row.descriptor,
                'expectedPoses':candidate.descriptor.get('expectedPoses',{})},image=row.image)
            candidate_context=inspection_context(frozen,targets,narrative=narrative)
        if candidate.descriptor.get('futureCast'):
            candidate_checkpoint=db.get(WorldCheckpoint,candidate.checkpoint_id)
            if (not candidate_checkpoint or candidate_checkpoint.owner_id!=row.owner_id
                    or candidate_checkpoint.world_id!=row.world_id
                    or narrative_hash(placement_narrative(candidate_checkpoint))!=candidate.descriptor.get('placementNarrativeHash')):continue
        if inspection_context(candidate,targets)==candidate_context:
            candidate_ids[candidate.id]=candidate;contexts[candidate.id]=candidate_context
    tasks=db.scalars(select(Task).where(Task.owner_id==row.owner_id,Task.task_type=='world_scene_binding',
        Task.payload['sceneId'].astext.in_(candidate_ids),Task.payload['worldId'].astext==row.world_id,
        Task.payload['lineId'].astext==row.line_id,Task.state=='succeeded',Task.provider_state=='completed',
        Task.cancel_requested.is_(False)).order_by(Task.created_at.desc()).limit(8))
    for task in tasks:
        saved=task.checkpoint.get('verifiedImageInspection',{})
        if (saved.get('sceneId') not in candidate_ids or saved.get('sceneId')!=task.payload.get('sceneId')
                or saved.get('imageHash')!=row.image.get('sha256')
                or saved.get('context')!=contexts.get(saved.get('sceneId'))):continue
        size=saved.get('imageSize')
        if not isinstance(size,list) or len(size)!=2 or any(type(n) is not int or n<=0 for n in size):continue
        measured=evaluate_inspection(saved.get('result'),row.descriptor.get('sceneCast',[]),targets,size,
            inspection_scene(row),
            future_cast=row.descriptor.get('futureCast',[]),
            expected_poses=candidate_ids[saved['sceneId']].descriptor.get('expectedPoses',{}))
        if measured['anchorStatus']!='complete':continue
        row.image={**row.image,**measured,'anchorVersion':VERSION,'bindingImageHash':row.image['sha256'],
                   'anchorError':None,'bindingReusedTaskId':task.id,'bindingContext':context}
        if row.descriptor.get('sceneReviewVersion')==1:
            row.image={**row.image,'sceneReview':saved['result']['sceneReview']}
        if row.descriptor.get('futureCast'):
            row.descriptor={**row.descriptor,'placementNarrativeHash':narrative_hash(narrative)}
        return True
    return False


def reuse_completed_inspection(db,row,source):
    """Carry a same-context private receipt with identical pixels, without POST.

    Failed visual coverage stays failed. This never upgrades evidence, changes
    an unknown receipt, or publishes an intermediate image.
    """
    from ..world.models import WorldCheckpoint
    from ..models import UsageRecord
    from .service import scene_view
    if (row.owner_id!=source.owner_id or row.world_id!=source.world_id or row.line_id!=source.line_id
            or row.profile_id!=source.profile_id or row.profile_version!=source.profile_version
            or source.status!='ready' or source.image.get('anchorVersion')!=VERSION
            or source.image.get('anchorStatus') not in ('complete','incomplete','unavailable')
            or row.image.get('sha256')!=source.image.get('sha256')
            or source.image.get('bindingImageHash')!=row.image.get('sha256')):return False
    task=db.get(Task,source.image.get('bindingTaskId'))
    if not task or task.owner_id!=row.owner_id or task.state!='succeeded' or task.provider_state!='completed' or task.cancel_requested:return False
    if task.payload.get('worldId')!=row.world_id or task.payload.get('lineId')!=row.line_id:return False
    original=db.get(SceneImage,task.payload.get('sceneId'))
    checkpoint=db.get(WorldCheckpoint,row.checkpoint_id)
    if (not original or original.owner_id!=row.owner_id or original.world_id!=row.world_id
            or original.line_id!=row.line_id or original.image.get('sha256')!=row.image.get('sha256')
            or not checkpoint or checkpoint.owner_id!=row.owner_id or checkpoint.world_id!=row.world_id):return False
    targets=inspection_targets(scene_view(checkpoint,row.line_id),independent_interior=row.descriptor.get('exitInspectionVersion')==1)
    frozen=task.checkpoint.get('bindingInspection',{})
    narrative=placement_narrative(checkpoint)
    context=inspection_context(row,targets,narrative=narrative)
    if frozen!={'sceneId':original.id,'imageHash':row.image['sha256'],'context':context,'version':VERSION}:return False
    if inspection_context(source,targets)!=context or inspection_context(original,targets)!=context:return False
    if source.image.get('anchorStatus')=='complete' and not artwork_confirmed(source):return False
    for candidate in (source,original):
        if not candidate.descriptor.get('futureCast'):continue
        candidate_checkpoint=db.get(WorldCheckpoint,candidate.checkpoint_id)
        if (not candidate_checkpoint or candidate_checkpoint.owner_id!=row.owner_id
                or candidate_checkpoint.world_id!=row.world_id
                or narrative_hash(placement_narrative(candidate_checkpoint))!=candidate.descriptor.get('placementNarrativeHash')):return False
    calls=task.checkpoint.get('calls',{})
    if not calls:return False
    for call in calls.values():
        usage=db.scalar(select(UsageRecord).where(UsageRecord.task_id==task.id,UsageRecord.provider_call_id==call.get('callId')))
        if not usage or usage.status!='completed':return False
    keys=('anchorVersion','bindingImageHash','anchorStatus','anchorError','bindingCoverage',
          'characterAnchors','spatialAnchors','missingCharacterIds','missingExitIds','missingFixtureIds',
          'scaleFailureIds','composition','detectionIssues','placementPlan','placementPassages',
          'pointCorrectionNotes','bindingEvidenceRetries','bindingConnectionRetries','sceneReview','peopleInventory')
    from copy import deepcopy
    row.image={**row.image,**{k:deepcopy(source.image[k]) for k in keys if k in source.image},
               'bindingTaskId':task.id,'bindingCopiedSceneId':original.id,'bindingContext':context}
    if row.descriptor.get('futureCast'):
        row.descriptor={**row.descriptor,'placementNarrativeHash':narrative_hash(narrative)}
    return True


def inspection_stages(row,targets):
    review_count=sum(p.get('independentReviewRequired') is True for p in targets['exits'])
    head_calls=(7 if row.descriptor.get('headCameraSearchVersion')==1 else 4)*len(row.descriptor.get('sceneCast',[])) if row.descriptor.get('headInspectionVersion') in (1,2) else 0
    placement_calls=(8 if row.descriptor.get('placementCameraVersion')==1 else 4) if row.descriptor.get('futureCast') else 0
    if placement_calls and row.descriptor.get('placementInspectionVersion')==2:placement_calls+=2
    if placement_calls and row.descriptor.get('placementSupportVersion')==1:placement_calls*=1+len(row.descriptor['futureCast'])*(2 if row.descriptor.get('supportClarificationVersion')==1 else 1)
    body_calls=2*len(row.descriptor.get('sceneCast',[])) if head_calls and row.descriptor.get('bodyInspectionVersion') == 1 else 0
    people_calls=4*len(row.descriptor.get('sceneCast',[]))+1 if head_calls and row.descriptor.get('personInspectionVersion')==1 and row.descriptor.get('sceneCast') else 0
    artwork_calls=1 if row.descriptor.get('artworkReviewVersion')==1 else 0
    fixture_calls=4 if row.descriptor.get('fixtureInspectionVersion')==1 and targets.get('fixtures') else 0
    scene_calls=(3 if row.descriptor.get('sceneReviewVersion')==1 else 0)+(4 if row.descriptor.get('peopleInventoryVersion')==1 else 0)+(4 if row.descriptor.get('peopleClarificationVersion')==1 else 0)+(4 if row.descriptor.get('peopleCountConflictVersion')==1 else 0)
    surface_detail_calls=8*review_count if row.descriptor.get('surfaceClarificationVersion')==1 else 0
    count=(9+8*review_count if review_count else 1)+surface_detail_calls+head_calls+placement_calls+body_calls+people_calls+artwork_calls+fixture_calls+scene_calls
    if row.descriptor.get('inspectionPerformanceVersion')==1:count=min(count,row.descriptor.get('inspectionMaxCalls',32))
    return ['analysis']*count


def queue_binding(db, row):
    if settings.ai_mode=='mock' or row.status!='ready':return
    if row.descriptor.get('pipelineParentId'):
        parent=db.get(SceneImage,row.descriptor['pipelineParentId'])
        if (not parent or parent.owner_id!=row.owner_id or parent.world_id!=row.world_id
                or parent.line_id!=row.line_id or parent.status!='queued'
                or parent.image.get('pipelineStageId')!=row.id):return
    # Status polling and scene requests can arrive together. Lock the picture,
    # then inspect its latest task receipt before reserving another call.
    db.flush()
    row=db.scalar(select(SceneImage).where(SceneImage.id==row.id).with_for_update().execution_options(populate_existing=True))
    from ..world.models import WorldCheckpoint
    from .service import scene_view
    checkpoint=db.get(WorldCheckpoint,row.checkpoint_id)
    if not checkpoint or checkpoint.owner_id!=row.owner_id or checkpoint.world_id!=row.world_id:return
    targets=inspection_targets(scene_view(checkpoint,row.line_id),independent_interior=row.descriptor.get('exitInspectionVersion')==1);narrative=placement_narrative(checkpoint)
    context=inspection_context(row,targets,narrative=narrative)
    prior=db.get(Task,row.image.get('bindingTaskId')) if row.image.get('bindingTaskId') else None
    if prior and prior.state not in ('succeeded','failed','cancelled'):return
    # A version upgrade is not permission to duplicate an unresolved request
    # against the very same pixels.
    if (prior and row.image.get('bindingImageHash')==row.image.get('sha256')
            and prior.provider_state in ('running','sent','unknown')):return
    if settings.ai_mode=='real' and row.descriptor.get('renderPipeline')==1 and row.descriptor.get('sceneCast'):
        # Upgrade only the inspection contract, retaining all paid image bytes
        # and old task receipts. Unresolved prior calls were excluded above.
        row.descriptor={**row.descriptor,'headInspectionVersion':2}
        context=inspection_context(row,targets,narrative=narrative)
    if (prior and row.image.get('bindingImageHash')==row.image.get('sha256')
            and prior.provider_state=='not_sent'
            and prior.checkpoint.get('bindingInspection',{}).get('context')!=context):
        # A local unsent failure can follow paid, received stages. A changed
        # contract may not throw those receipts away and start the task again.
        row.image={**row.image,'bindingRecoveryError':'scene-context-changed'}
        return
    same_picture=(row.image.get('anchorVersion')==VERSION
                  and row.image.get('bindingImageHash')==row.image.get('sha256')
                  and (not row.descriptor.get('futureCast') and inspection_scene(row).get('artworkInspectionVersion')!=1
                       or row.image.get('bindingContext')==context))
    if same_picture and row.image.get('anchorStatus')=='complete' and artwork_confirmed(row):return
    if restore_verified_inspection(db,row):return
    if same_picture and row.image.get('anchorStatus')=='complete' and not artwork_confirmed(row):
        composition=composition_from_result({'composition':row.image.get('composition',{})},
            row.descriptor.get('sceneCast',[]),inspection_scene(row))
        row.image={**row.image,'anchorStatus':'incomplete' if composition['verified'] else 'unavailable',
                   'composition':composition}
    recovery=(same_picture and row.image.get('anchorStatus')=='unavailable'
              and prior and prior.state=='failed' and prior.provider_state=='not_sent'
              and prior.error_code in ('PROVIDER_CONNECTION_FAILED','SCENE_IMAGE_INVALID'))
    # A received but incomplete detector answer may be inspected again without
    # paying to redraw valid pixels. Never repeat an unresolved provider call.
    from .visual_brief import obstructed_threshold
    from .room_repairs import plans as room_repair_plans
    evidence_recovery=(same_picture and row.image.get('anchorStatus')=='unavailable'
        and not (row.descriptor.get('measurementEfficiencyVersion')==1 and row.image.get('placementEvidenceExhausted') is True)
        and not obstructed_threshold(row.image) and not room_repair_plans(row.descriptor,row.image)
        and prior and prior.state=='succeeded' and prior.provider_state=='completed'
        and not row.image.get('scaleFailureIds')
        and all(p.get('reason') in ('placement-evidence-unconfirmed','body-evidence-unconfirmed','passage-evidence-unconfirmed','surface-evidence-unconfirmed')
                for p in row.image.get('detectionIssues',[]))
        and all(p in ('composition-unconfirmed','people-count-unconfirmed','space-unconfirmed','artwork-unconfirmed','scene-review-unconfirmed')
                for p in row.image.get('composition',{}).get('issues',[])))
    evidence_attempts=row.image.get('bindingEvidenceRetries',0) if same_picture else 0
    attempts=row.image.get('bindingConnectionRetries',0) if same_picture else 0
    if same_picture and not recovery and not evidence_recovery:return
    if recovery and attempts>=2:return
    if evidence_recovery and evidence_attempts>=2:return
    from ..world.jobs import create_job
    if recovery:
        from .recovery import resume_inspection
        if not resume_inspection(db,row,prior,context,VERSION):return
        row.image={**row.image,'anchorStatus':'queued','bindingConnectionRetries':attempts+1,'anchorError':None}
        return
    review_count=sum(p.get('independentReviewRequired') is True for p in targets['exits'])
    # One image per exchange prevents the supplier from interchanging camera
    # labels and image numbers. All four directions are checked independently.
    # At most one blind near/far confirmation per candidate and actual camera.
    # Actual calls stop once a measured destination is found. The reservation
    # covers the worst case, without granting unbounded retries.
    stages=inspection_stages(row,targets)
    try:
        with db.begin_nested():
            task=create_job(db,row.owner_id,'world_scene_binding',{'worldId':row.world_id,'lineId':row.line_id,'sceneId':row.id},stages,max_calls=len(stages))
            from .recovery import inspection_prompts
            from ..prose.policy import freeze
            task.checkpoint={**task.checkpoint,'bindingInspection':{'sceneId':row.id,
                'imageHash':row.image.get('sha256'),'context':context,'version':VERSION},
                'executionProfile':freeze(db.get(ModelProfile,task.payload['profileId'])),
                'executionPrompts':inspection_prompts()}
            if evidence_recovery and row.descriptor.get('inspectionReceiptVersion')==1:
                task.checkpoint={**task.checkpoint,'reuseInspectionTaskId':prior.checkpoint.get('reuseInspectionTaskId') or prior.id,
                    'reusePlacementTaskId':prior.id}
            if row.descriptor.get('futureCast'):
                row.descriptor={**row.descriptor,'placementNarrativeHash':narrative_hash(narrative)}
            row.image={**row.image,'bindingTaskId':task.id,'anchorStatus':'queued','anchorVersion':VERSION,
                       'bindingContext':context,
                       'bindingImageHash':row.image.get('sha256'),'bindingConnectionRetries':attempts+1 if recovery else attempts,
                       'bindingEvidenceRetries':evidence_attempts+1 if evidence_recovery else evidence_attempts,
                       'anchorError':None,'characterAnchors':{},'spatialAnchors':{}}
    except Problem as error:
        row.image={**row.image,'anchorStatus':'unavailable','anchorError':error.code,'anchorVersion':VERSION,'characterAnchors':{},'spatialAnchors':{}}


def anchors_from_result(result, cast, image_size=None, *, require_grounding=False, verified_fixtures=()):
    allowed={p['entityId'] for p in cast};anchors={};centres=[]
    parts=result.get('characters',[]) if isinstance(result,dict) else []
    if not isinstance(parts,list):return anchors
    for part in parts:
        if not isinstance(part,dict):continue
        ident=part.get('entityId');box=part.get('headBox');confidence=part.get('confidence',0)
        pixels=part.get('headBoxPixels')
        if image_size and isinstance(pixels,list) and len(pixels)==4 and all(type(v) in (int,float) and isfinite(v) for v in pixels):
            w,h=image_size
            box=[pixels[0]/w,pixels[1]/h,pixels[2]/w,pixels[3]/h]
        if not isinstance(ident,str) or ident not in allowed or ident in anchors or part.get('visible') is not True or not isinstance(box,list) or len(box)!=4:continue
        spherical = part.get('sphericalView') is True
        if not all(type(v) in (int,float) and isfinite(v) for v in box):continue
        if not (0 <= box[1] <= 1 and 0 <= box[3] <= 1):continue
        if not spherical and not (0 <= box[0] <= 1 and 0 <= box[2] <= 1):continue
        x1,y1,x2,y2=box
        if not x1<x2 or not y1<y2 or type(confidence) not in (int,float) or not isfinite(confidence) or not .8<=confidence<=1:continue
        if require_grounding:
            # A near adult or a seated observer need not have a tiny head on
            # the horizon. Require actual body/furniture and contact evidence
            # instead of accepting a face merely because its pixels are small.
            natural=part.get('naturalScale');scale=part.get('scaleConfidence')
            ground=part.get('groundPointPixels');ground_confidence=part.get('groundConfidence')
            support=part.get('grounding')
            if (body_evidence_issue(part) or part.get('physicalPerson') is not True
                    or natural is not True or type(scale) not in (int,float) or not isfinite(scale) or not .8<=scale<=1
                    or type(ground_confidence) not in (int,float) or not isfinite(ground_confidence) or not .8<=ground_confidence<=1
                    or support not in ('floor','fixture') or not image_size
                    or not isinstance(ground,list) or len(ground)!=2
                    or any(type(v) not in (int,float) or not isfinite(v) for v in ground)):
                continue
            if support=='fixture':
                if part.get('supportingFixtureId') is not None and part.get('supportingFixtureId') not in verified_fixtures:continue
                measured=part.get('supportingFixture')
                if not isinstance(measured,dict) or measured.get('kind') not in ('chair','bench','counter','sofa'):continue
                confidence_support=measured.get('confidence');sb=measured.get('panoramaBoxPixels')
                if (type(confidence_support) not in (int,float) or not isfinite(confidence_support) or not .8<=confidence_support<=1
                        or not isinstance(sb,list) or len(sb)!=4):continue
                # Vision rectangles have the same bounded eight-pixel
                # measurement tolerance as head/body containment below. Apply
                # it in the measured camera, then project it to the sphere;
                # never move the actual contact or published anchor.
                local=measured.get('boxPixels');view=measured.get('viewIndex')
                if (type(view) is int and view in range(8) and isinstance(local,list) and len(local)==4
                        and all(type(v) in (int,float) and isfinite(v) and 0<=v<=512 for v in local)
                        and local[0]<local[2] and local[1]<local[3]):
                    from .inspection import camera_pixel
                    try:cx,cy=camera_pixel(view,*ground,*image_size)
                    except ValueError:continue
                    # Always judge contact in the actual support camera. An
                    # axis-aligned panorama envelope includes curved corners
                    # that can be forty pixels beyond the real support edge.
                    if not (max(0,local[0]-8)-1e-6<=cx<=min(512,local[2]+8)+1e-6
                            and max(0,local[1]-8)-1e-6<=cy<=min(512,local[3]+8)+1e-6):continue
                else:continue
                # Adjacent cameras can use opposite texture seam copies.
                # Check the bounded equivalent longitudes, never move pixels.
                # The inverse projection already handles equivalent seam
                # copies; no separate panorama-envelope gate is appropriate.
            gx,gy=ground[0]/image_size[0],ground[1]/image_size[1]
            if not 0<=gx<=1 or not (.5 if support=='floor' else .43)<=gy<=.98:continue
            # The contact must belong below this body, not a different floor
            # across the room. Occluded bodies require a measured solid fixture.
            if min(abs(gx-((x1+x2)/2)%1),1-abs(gx-((x1+x2)/2)%1))>.08 or gy<=y2:continue
            body=part.get('bodyBoxPixels')
            if not isinstance(body,list) or len(body)!=4:continue
            bx1,by1,bx2,by2=body[0]/image_size[0],body[1]/image_size[1],body[2]/image_size[0],body[3]/image_size[1]
            head_centre=(x1+x2)/2
            if not any(bx1-.02<=head_centre+offset<=bx2+.02 for offset in (-1,0,1)) or y2>by2:continue
            # Same-camera head/body containment was already checked with
            # eight-pixel tolerance by body_evidence_issue. A second exact
            # comparison after nonlinear projection rejected valid two-pixel
            # measurement differences when revisiting unchanged pictures.
            if gy<by1 or gy>by2+.04:continue
            if x2-x1>.12 or y2-y1>.14 or y2<.20:continue
            # Evaluate anatomy in its undistorted camera, not in a stretched
            # latitude texture. A cropped face is not a physical adult body.
            head_camera=part.get('cameraHeadBoxPixels');body_camera=part.get('cameraBodyBoxPixels')
            if part.get('bodyViewIndex',part.get('viewIndex'))==part.get('viewIndex'):
                if not head_camera or not body_camera:continue
                fraction=(head_camera[3]-head_camera[1])/(body_camera[3]-body_camera[1])
                if not .045<=fraction<=.48:continue
        elif x2-x1>.035 or y2-y1>.035 or abs((.5-(y1+y2)/2)*pi)>.18:continue
        u,v=((x1+x2)/2)%1,y2
        if any(min(abs(u-x),1-abs(u-x))<.025 and abs(v-y)<.05 for x,y in centres):continue
        centres.append((u,v))
        anchors[ident]={'yaw':(u-.5)*2*pi,'pitch':(.5-v)*pi,
                        'headPitch':(.5-y1)*pi,'headBox':box if 0<=x1<x2<=1 else [],
                        'unwrappedHeadBox':box,
                        'verified':True,'source':'image-inspection'}
        if isinstance(part.get('headReview'),dict):
            anchors[ident]['headReview']=part['headReview']
        if require_grounding:
            anchors[ident]['bodyEvidence']={'grounding':support,'naturalScale':True,
                'scaleConfidence':scale,'groundConfidence':ground_confidence,
                'groundPoint':[gx,gy],'supportingFixtureId':part.get('supportingFixtureId'),
                'pose':part['pose'],'bodyBox':body,'imageSize':list(image_size),'scaleEvidence':part['scaleEvidence']}
    return anchors


def body_evidence_issue(part):
    """Distinguish missing detector evidence from an observed bad image."""
    if type(part.get('physicalPerson')) is not bool or type(part.get('naturalScale')) is not bool:
        return 'body-evidence-unconfirmed'
    if not part['physicalPerson'] or not part['naturalScale'] or part.get('grounding')=='floating':
        return None  # A known visual failure may be repaired; do not invent a body.
    if part.get('pose') not in ('standing','seated','kneeling') or part.get('grounding') not in ('floor','fixture'):
        return 'body-evidence-unconfirmed'
    if part.get('grounding')=='fixture':
        support=part.get('supportingFixture')
        if (not isinstance(support,dict) or support.get('kind') not in ('chair','bench','counter','sofa')
                or not isinstance(support.get('boxPixels'),list) or len(support['boxPixels'])!=4):
            return 'body-evidence-unconfirmed'
    for field in ('scaleConfidence','groundConfidence'):
        value=part.get(field)
        if type(value) not in (int,float) or not isfinite(value) or not 0<=value<=1:
            return 'body-evidence-unconfirmed'
    evidence=part.get('scaleEvidence')
    if (not isinstance(evidence,dict) or evidence.get('referenceKind') not in ('door','furniture')
            or evidence.get('depthRelation') not in ('same-depth','in-front','behind')
            or not isinstance(evidence.get('description'),str) or not evidence['description'].strip()):
        return 'body-evidence-unconfirmed'
    # The detector must supply both actual rectangles, not only natural=true.
    for box in (part.get('cameraBodyBoxPixels'),evidence.get('boxPixels')):
        if (not isinstance(box,list) or len(box)!=4
                or any(type(v) not in (int,float) or not isfinite(v) or not 0<=v<=512 for v in box)
                or not box[0]<box[2] or not box[1]<box[3]):return 'body-evidence-unconfirmed'
    if type(evidence.get('viewIndex')) is not int or not 0<=evidence['viewIndex']<8:
        return 'body-evidence-unconfirmed'
    body=part['cameraBodyBoxPixels'];head=part.get('cameraHeadBoxPixels')
    body_view=part.get('bodyViewIndex',part.get('viewIndex'))
    if body_view==part.get('viewIndex') and isinstance(head,list) and len(head)==4:
        if not (body[0]-8<=head[0]<head[2]<=body[2]+8 and body[1]-8<=head[1]<head[3]<=body[3]+8):
            return 'body-evidence-contradictory'
    reference=evidence['boxPixels']
    # Only compare the same camera at the same depth. Perspective and a seated
    # or occluded body cannot be judged by a fixed miniature-head threshold.
    if (part['pose']=='standing' and evidence['referenceKind']=='door'
            and evidence['depthRelation']=='same-depth' and evidence['viewIndex']==body_view):
        if reference[1]<=2 or reference[3]>=510:return 'body-evidence-unconfirmed'
        if reference[3]-reference[1]>=96 and (body[3]-body[1])/(reference[3]-reference[1])>1.6:
            return 'body-evidence-contradictory'
    if not isinstance(part.get('groundPointPixels'),list) or len(part['groundPointPixels'])!=2:
        return 'body-evidence-unconfirmed'
    return None


def composition_from_result(result, cast, expected_scene=None):
    """Expected-person matching cannot account for extra people or a bad lens."""
    if (expected_scene or {}).get('sceneReviewVersion')==1:
        from .scene_review import evaluate
        review=result.get('sceneReview',{}) if isinstance(result,dict) else {}
        if (not isinstance(review,dict) or review.get('version')!=1 or not expected_scene.get('sceneImageHash')
                or review.get('imageHash')!=expected_scene['sceneImageHash']):
            return {'verified':False,'issues':['scene-review-unconfirmed']}
        observed=evaluate(review.get('observation'),cast,expected_scene)
        if not observed['verified']:return observed
        legacy_scene={k:v for k,v in expected_scene.items() if k!='sceneReviewVersion'}
        measured=composition_from_result({**result,'composition':observed},cast,legacy_scene)
        measured['issues']=list(dict.fromkeys([*observed['issues'],*measured['issues']]))
        if expected_scene.get('peopleInventoryVersion')==1:
            from .people_inventory import evaluate as evaluate_inventory,evaluate_facts as evaluate_camera_facts
            inventory=evaluate_inventory(result.get('peopleInventory'),expected_scene['sceneImageHash'])
            fact_issues=evaluate_camera_facts(result.get('peopleInventory'),expected_scene.get('visualFacts'),expected_scene['sceneImageHash'])
            measured['issues']=list(dict.fromkeys([*measured['issues'],*fact_issues]))
            if 'visual-facts-unconfirmed' in fact_issues:measured['verified']=False
            if not inventory['verified']:
                measured['verified']=False;measured['issues'].append('people-count-unconfirmed')
            elif inventory['count']>len(cast):
                measured['issues'].append('unexpected-people')
                measured['visiblePeopleCount']=inventory['count']
            elif inventory['count']!=len(cast) or inventory['count']!=observed['visiblePeopleCount']:
                measured['verified']=False;measured['issues'].append('people-count-unconfirmed')
        # A secondary coordinate exchange can mistake an ordinary framed oil
        # painting for a reference panel. Preserve the finding until a separate
        # full-texture exchange resolves this exact hypothesis on these bytes.
        # Missing, stale, malformed or uncertain rechecks cannot clear it.
        from .artifact_conflicts import suspects,cleared
        artifacts=suspects(result)
        if artifacts and not cleared(result,cast,expected_scene):
            measured['referenceArtifacts']=list(dict.fromkeys([*measured.get('referenceArtifacts',[]),*artifacts]))
            measured['issues']=list(dict.fromkeys([*measured['issues'],'reference-artifacts']))
        parts=result.get('characters',[])
        if not isinstance(parts,list):return {'verified':False,'issues':['people-count-unconfirmed']}
        physical=[p for p in parts if isinstance(p,dict)
                  and p.get('visible') is True and p.get('physicalPerson') is True]
        if any(p.get('entityId') not in {c['entityId'] for c in cast} for p in physical):
            measured['verified']=False;measured['issues'].append('people-count-unconfirmed')
        recount=result.get('peopleRecount')
        if isinstance(recount,dict) and (recount.get('visiblePeopleCount')!=observed['visiblePeopleCount']
                or recount.get('unexpectedPeople')):
            measured['verified']=False;measured['issues'].append('people-count-unconfirmed')
        return measured
    observation=result.get('composition') if isinstance(result,dict) else None
    if not isinstance(observation,dict):return {'verified':False,'issues':['composition-unconfirmed']}
    projection=observation.get('projection');confidence=observation.get('confidence')
    count=observation.get('visiblePeopleCount');extras=observation.get('unexpectedPeople')
    # Some vision responses wrap each actual stranger description in an
    # object. Preserve that observed failure instead of masking it as format.
    if isinstance(extras,list):
        extras=[p.get('description') if isinstance(p,dict) else p for p in extras]
    if (projection not in ('equirectangular','perspective','uncertain')
            or type(confidence) not in (int,float) or not isfinite(confidence) or not 0<=confidence<=1
            or type(count) is not int or not 0<=count<=40
            or not isinstance(extras,list) or any(not isinstance(p,str) or not p.strip() for p in extras)):
        return {'verified':False,'issues':['composition-unconfirmed']}
    verified=confidence>=.8 and projection!='uncertain'
    issues=[]
    if not verified:issues.append('composition-unconfirmed')
    elif projection!='equirectangular':issues.append('projection-mismatch')
    if extras or count>len(cast):issues.append('unexpected-people')
    artifacts=observation.get('referenceArtifacts')
    if isinstance(artifacts,list):
        artifacts=[p.get('description') if isinstance(p,dict) else p for p in artifacts]
    if not isinstance(artifacts,list) or any(not isinstance(p,str) for p in artifacts):
        verified=False;issues.append('composition-unconfirmed')
    elif artifacts:issues.append('reference-artifacts')
    finished=observation.get('finishedArtwork');unfinished=observation.get('unpaintedGuideRegions')
    if (expected_scene or {}).get('artworkInspectionVersion')==1:
        if type(finished) is not bool or not isinstance(unfinished,list) or any(not isinstance(p,str) or not p.strip() for p in unfinished):
            verified=False;issues.append('artwork-unconfirmed')
        elif not finished or unfinished:issues.append('unfinished-artwork')
    characters=result.get('characters')
    if isinstance(characters,list):
        physical=[p for p in characters if isinstance(p,dict) and p.get('visible') is True and p.get('physicalPerson') is True]
        identities=[p.get('entityId') for p in physical]
        if count<len(physical) or len(set(identities))!=len(identities):
            verified=False;issues.append('people-count-unconfirmed')
    expected=(expected_scene or {}).get('spaceKind')
    if expected in ('open-air courtyard','open-air street or market','outdoor natural landscape','open-air setting'):
        expected='outdoor'
    space=observation.get('spaceKind');sky=observation.get('openSky')
    if expected in ('interior','outdoor','outdoors','exterior'):
        if space not in ('interior','outdoor','uncertain') or type(sky) is not bool:
            verified=False;issues.append('space-unconfirmed')
        elif space=='uncertain':
            verified=False;issues.append('space-unconfirmed')
        elif (expected=='interior' and (space!='interior' or sky)) or (expected!='interior' and space!='outdoor'):
            issues.append('space-mismatch')
    return {'verified':verified,'projection':projection,'confidence':confidence,
            'spaceKind':space,'openSky':sky,'visiblePeopleCount':count,'unexpectedPeople':[p[:180] for p in extras[:40]],
            'referenceArtifacts':artifacts,'finishedArtwork':finished,'unpaintedGuideRegions':unfinished,'issues':issues}


def evaluate_inspection(parsed,cast,targets,image_size,expected_scene,*,future_cast=(),expected_poses=None):
    expected_poses=expected_poses or {}
    composition=composition_from_result(parsed,cast,expected_scene)
    valid_inspection=(isinstance(parsed,dict) and isinstance(parsed.get('characters'),list)
                      and composition['verified'])
    from .inspection import panorama_coordinates
    parsed = panorama_coordinates(parsed, image_size)
    if not isinstance(parsed,dict):parsed={}
    rejected=[{'kind':kind,'id':part.get('entityId') if kind=='characters' else part.get('id'),
               'reason':part['rejectionReason']}
              for kind in ('characters','fixtures','exits')
              for part in (parsed[kind] if isinstance(parsed.get(kind),list) else [])
              if isinstance(part,dict) and part.get('rejectionReason')]
    if expected_scene.get('sceneReviewVersion')==1:
        for kind in ('fixtures','exits'):
            allowed={p['id'] for p in targets[kind]}
            parts=parsed.get(kind,[])
            if isinstance(parts,list) and any(not isinstance(p,dict) or p.get('id') not in allowed for p in parts):
                rejected.append({'kind':kind,'id':None,'reason':'unlisted-target-id'})
    if rejected:valid_inspection=False
    spatial=spatial_anchors_from_result(parsed,targets,image_size,require_evidence=True)
    cue_targets={p['id'] for p in targets['exits'] if p.get('visualCue')}
    for part in parsed.get('exits',[]) if isinstance(parsed.get('exits'),list) else []:
        if (isinstance(part,dict) and isinstance(part.get('passageEvidence'),dict)
                and part['passageEvidence'].get('independentReview',{}).get('status')=='unconfirmed'):
            valid_inspection=False
            rejected.append({'kind':'exits','id':part.get('id'),'reason':'passage-evidence-unconfirmed'})
        if (isinstance(part,dict) and part.get('id') in cue_targets
                and part.get('visible') is True and part.get('passable') is True
                and isinstance(part.get('passageEvidence'),dict)
                and part['passageEvidence'].get('visualCueMatched') is False):
            valid_inspection=False
            rejected.append({'kind':'exits','id':part.get('id'),'reason':'passage-cue-mismatch'})
        if (isinstance(part,dict) and part.get('visible') is True and part.get('passable') is True
                and not part.get('passageEvidence')):
            valid_inspection=False
            rejected.append({'kind':'exits','id':part.get('id'),'reason':'passage-evidence-unconfirmed'})
    verified_fixtures={key.split(':',1)[1] for key in spatial if key.startswith('fixtures:')}
    anchors=anchors_from_result(parsed,cast,image_size,require_grounding=True,verified_fixtures=verified_fixtures)
    characters=parsed['characters'] if isinstance(parsed.get('characters'),list) else []
    if expected_scene.get('headInspectionVersion') in (1,2):
        from .heads import proof_valid
        for p in characters:
            if (isinstance(p,dict) and p.get('entityId') in {c['entityId'] for c in cast}
                    and p.get('visible') is True and (not proof_valid(p.get('headReview'),version=expected_scene['headInspectionVersion'])
                        or p['headReview'].get('viewIndex')!=p.get('viewIndex')
                        or p['headReview'].get('headBoxPixels')!=p.get('cameraHeadBoxPixels'))):
                anchors.pop(p['entityId'],None);valid_inspection=False
                rejected.append({'kind':'characters','id':p['entityId'],'reason':'body-evidence-unconfirmed',
                    'detail':'Head pixels have not passed an independent single-camera localization and actual crop check.'})
            if isinstance(p,dict) and p.get('headIdentityMismatch') is True:
                composition['issues'].append('identity-mismatch')
    if any(p.get('visible') is True and p.get('entityId') in expected_poses and p.get('pose')!=expected_poses[p['entityId']]
           for p in characters if isinstance(p,dict)):
        composition['issues'].append('pose-mismatch')
    scale_failures=[p['entityId'] for p in characters if isinstance(p,dict)
                    and p.get('visible') is True and p.get('entityId') not in anchors
                    and isinstance(p.get('headBoxPixels'),list) and len(p['headBoxPixels'])==4
                    and p.get('entityId') in {c['entityId'] for c in cast}
                    and all(type(v) in (int,float) and isfinite(v) for v in p['headBoxPixels'])
                    and (p.get('naturalScale') is False
                         or p['headBoxPixels'][2]-p['headBoxPixels'][0]>.12*image_size[0]
                         or p['headBoxPixels'][3]-p['headBoxPixels'][1]>.14*image_size[1])]
    incomplete_body=[p for p in characters if isinstance(p,dict) and p.get('visible') is True and body_evidence_issue(p)]
    if incomplete_body:
        valid_inspection=False
        rejected.extend({'kind':'characters','id':p.get('entityId'),'reason':body_evidence_issue(p)} for p in incomplete_body)
    # A detected natural physical adult with inconsistent contact measurements
    # is incomplete detector evidence, not proof that the paid image is wrong.
    # Reinspection must resolve this; never invent an anchor or redraw blindly.
    for p in characters:
        if (isinstance(p,dict) and p.get('entityId') not in anchors
                and p.get('entityId') not in scale_failures and p not in incomplete_body
                and p.get('visible') is True and p.get('physicalPerson') is True and p.get('naturalScale') is True):
            valid_inspection=False
            rejected.append({'kind':'characters','id':p.get('entityId'),'reason':'body-evidence-unconfirmed',
                'detail':'Measured contact/head/body/support do not agree. Re-measure in the clearest view. If feet visibly touch floor use grounding=floor; a shoe contact is not inside a sofa support box.'})
    for kind in ('fixtures','exits'):
        for p in parsed.get(kind,[]) if isinstance(parsed.get(kind),list) else []:
            if (isinstance(p,dict) and not any(issue['id']==p.get('id') and issue['reason']=='passage-cue-mismatch' for issue in rejected)
                    and p.get('visible') is True and type(p.get('confidence')) in (int,float)
                    and p['confidence']>=.8 and (kind!='exits' or p.get('passable') is True)
                    and kind+':'+str(p.get('id')) not in spatial):
                valid_inspection=False
                rejected.append({'kind':kind,'id':p.get('id'),'reason':'passage-evidence-unconfirmed' if kind=='exits' else 'surface-evidence-unconfirmed',
                    'detail':'Re-measure actual walking floor near the object/passage. Anchor must be away from distorted nadir; passage anchor inside actual opening width, near threshold.'})
    missing=[p['entityId'] for p in cast if p['entityId'] not in anchors]
    if (expected_scene.get('measurementRecoveryVersion')==1 and missing and composition.get('verified')
            and not composition['issues'] and composition.get('visiblePeopleCount')==len(cast)):
        # A complete blind count with unresolved identity/coordinates is NOT
        # evidence that the painting is wrong. Resolve it on the same pixels
        # through the existing bounded evidence recovery, never an auto-redraw.
        for ident in missing:
            if ident in scale_failures or any(p.get('id')==ident and p.get('reason')=='body-evidence-contradictory' for p in rejected):continue
            valid_inspection=False
            if not any(p.get('id')==ident and p.get('reason')=='body-evidence-unconfirmed' for p in rejected):
                rejected.append({'kind':'characters','id':ident,'reason':'body-evidence-unconfirmed',
                    'detail':'Physical people were counted, but this identity and its coordinates remain unconfirmed on the received image.'})
    missing_exits = [p['id'] for p in targets['exits'] if 'exits:'+p['id'] not in spatial]
    missing_fixtures = [p['id'] for p in targets['fixtures'] if 'fixtures:'+p['id'] not in spatial]
    placements=[]
    if future_cast:
        from .pipeline import validate_placements
        placements=validate_placements(parsed,future_cast)
        if not placements:
            valid_inspection=False;rejected.append({'kind':'placements','id':None,'reason':'placement-evidence-unconfirmed'})
    return {'characterAnchors':anchors,'spatialAnchors':spatial,
            'anchorStatus':'unavailable' if not valid_inspection else 'incomplete' if missing or missing_exits or missing_fixtures or composition['issues'] else 'complete',
            'missingCharacterIds':missing,'missingExitIds':missing_exits,'missingFixtureIds':missing_fixtures,
            'scaleFailureIds':scale_failures,'composition':composition,'detectionIssues':rejected,
            'placementPlan':placements,
            **({'peopleInventory':parsed['peopleInventory']} if isinstance(parsed.get('peopleInventory'),dict) else {}),
            'placementPassages':[{'visible':True,'passageEvidence':p['passageEvidence']}
                for p in (parsed['exits'] if isinstance(parsed.get('exits'),list) else []) if isinstance(p,dict)
                and 'exits:'+str(p.get('id')) in spatial and p.get('passageEvidence')],
            'bindingCoverage':{'characters':len(anchors),'exits':len(targets['exits'])-len(missing_exits),'fixtures':len(targets['fixtures'])-len(missing_fixtures)}}


def inspection_cameras(row,source):
    from .inspection import camera_images
    # View IDs live in request metadata. Painted VIEW banners in the analysis
    # input were mistaken for dark guide slabs, although absent from the scene.
    return camera_images(source,include_ground=True,labelled=row.descriptor.get('cameraPixelsVersion')!=1)


def bind_scene(task_id):
    from ..worker import call_provider, assert_task_active, add_event
    from ..world.jobs import lock_task_world
    from ..completion_policy import completion_limit
    from .service import image_bytes
    from .service import scene_view
    from ..world.models import WorldCheckpoint
    with SessionLocal() as db:
        lock_task_world(db,task_id);task=db.get(Task,task_id);assert_task_active(db,task)
        row=db.get(SceneImage,task.payload['sceneId']);world=db.get(World,task.payload['worldId']);line=db.get(WorldLine,task.payload['lineId'])
        if not row or not world or not line or row.owner_id!=task.owner_id or row.world_id!=world.id or row.line_id!=line.id or world.status!='active' or line.status!='active' or row.status!='ready':raise Problem('TASK_CANCELLED','场景已变化')
        if row.descriptor.get('pipelineParentId'):
            from .worker import pipeline_context_active
            if not pipeline_context_active(db,row,line):raise Problem('TASK_CANCELLED','场景管线已停止或变化')
        from .cooldown import hydrate
        cooldown_scope=hydrate(db,task.checkpoint.get('executionProfile') or db.get(ModelProfile,task.payload['profileId']))
        poses=row.descriptor.get('expectedPoses',{})
        cast=[{**p,**({'expectedPose':poses[p['entityId']]} if p['entityId'] in poses else {})}
              for p in row.descriptor.get('sceneCast',[])];source=image_bytes(row)
        if row.descriptor.get('publicAppearanceContextVersion')==1:
            from .facts import character_description
            cast=[{**p,'description':character_description(row.descriptor,p)} for p in cast]
        checkpoint=db.get(WorldCheckpoint,row.checkpoint_id)
        view=scene_view(checkpoint,line.id)
        targets=inspection_targets(view,independent_interior=row.descriptor.get('exitInspectionVersion')==1)
        narrative=placement_narrative(checkpoint)
        frozen=task.checkpoint.get('bindingInspection')
        if frozen is not None and frozen!={'sceneId':row.id,'imageHash':row.image.get('sha256'),
                'context':inspection_context(row,targets,narrative=narrative),'version':VERSION}:
            raise Problem('TASK_CANCELLED','校验画面或场景已变化，未重复请求')
        if row.descriptor.get('futureCast') and row.descriptor.get('placementNarrativeHash')!=narrative_hash(narrative):
            raise Problem('TASK_CANCELLED','角色姿态上下文已变化，未重复请求')
        image_data,image_size=inspection_image(source)
        from .inspection import camera_images, panorama_coordinates, SIZE, FOV
        cameras = inspection_cameras(row,image_data)
        inspected_hash = row.image.get('sha256')
        # Generation guidance is not visual evidence. Withhold all requested
        # positions so the inspector must locate the actual painted person.
        public_cast=[{k:p[k] for k in ('entityId','name','description','appearance','presentation') if k in p} for p in cast]
        for person in public_cast:
            if person['entityId'] in row.descriptor.get('expectedPoses',{}):
                person['expectedPose']=row.descriptor['expectedPoses'][person['entityId']]
        expected_scene=inspection_scene(row)
        from .recovery import retained_base
        received_base=retained_base(db,task,row,inspection_context(row,targets,narrative=narrative))
        from .received_reads import preflight as received_reads
        reused_preflight=received_reads(db,task,row,inspection_context(row,targets,narrative=narrative))
        from .inventory_receipts import unchanged_views
        for index,receipt in unchanged_views(db,task,row,cameras).items():
            reused_preflight.setdefault(index,receipt)
        from .placement_receipts import restore as restore_placements
        received_placements,placement_proofs=restore_placements(db,task,row,inspection_context(row,targets,narrative=narrative),cameras)
        data={'cast':public_cast,**targets,'expectedScene':expected_scene,'imageSize':{'width':image_size[0],'height':image_size[1]},
              'cameraViews':{'count':8,'width':SIZE,'height':SIZE,'horizontalFov':FOV,
                             'images':[{'image':index+2,'viewIndex':index,'pitchDegrees':0 if index<4 else -35} for index in range(8)]},
              '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(value).decode() for value in [image_data,*cameras]],
              '_structured':True,'_visionStream':True}
        data['bodyEvidenceContract']={'pose':['standing','seated','kneeling'],
            'scaleConfidence':'REQUIRED number 0..1; actual visual confidence, never omit',
            'grounding':['floor','fixture','floating'],
            'scaleEvidence':{'referenceKind':['door','furniture'],'viewIndex':'integer 0..7',
                'boxPixels':'actual complete visible reference rectangle [left,top,right,bottom] in this camera',
                'depthRelation':['same-depth','in-front','behind'],
                'depthMeaning':'PERSON relative to REFERENCE: in-front means person closer to camera than reference; behind means person farther.',
                'description':'observed adult proportions and relative depth'},
            'fixtureSupport':'For grounding=fixture ALWAYS include measured supportingFixture {kind:chair/bench/counter/sofa, viewIndex:0..7, boxPixels:[left,top,right,bottom],confidence:0.8..1}; actual contact inside this box. supportingFixtureId is optional; never substitute an unrelated table ID. If shoe/floor contact visible, use floor.'}
        data['referenceIdentityContract']={'suppliedCharacterReferences':row.descriptor.get('characterReferenceOrder',[]),
            'rule':'An arbitrary decorative human portrait is not proof of a copied CHARACTER reference. With zero supplied character references do not invent reference-face identity. Still reject real pasted cutouts, duplicated physical people and neutral placeholders.'}
        if expected_scene.get('identityInspectionVersion')==1:
            from .service import image_bytes
            references=[]
            for person in cast:
                look=db.get(SceneImage,person.get('lookImageId')) if person.get('lookImageId') else None
                if not look or look.owner_id!=row.owner_id or look.world_id!=row.world_id or look.status!='ready':continue
                reference,_=inspection_image(image_bytes(look))
                references.append({'image':len(data['_sceneImages'])+1,'entityId':person['entityId'],'name':person['name']})
                data['_sceneImages'].append('data:image/jpeg;base64,'+base64.b64encode(reference).decode())
            data['referenceIdentityContract']={'suppliedCharacterReferences':references,
                'rule':'Images 1..9 are scene views. Later images listed here are actual owned IDENTITY REFERENCES, never scene cameras. Compare physical identity and any claimed copied wall face against these exact references. A pre-existing decorative painting is not a reference artifact merely because it contains a human face. Reject actual copied reference frames/backgrounds, duplicate physical people, and unfinished neutral human placeholders. Do not ignore an actual copied face when the reference supports the match.'}
        if expected_scene.get('artworkInspectionVersion')==1:
            data['artworkContract']={'requiredKeys':['finishedArtwork','unpaintedGuideRegions'],
                'rule':'Inspect the actual camera pixels for residual architectural blockouts: blank white/gray cuboids or flat placeholder panels interrupting otherwise painted masonry, timber, paving or fabric. Report finishedArtwork:false and describe their actual view and surface. A naturally smooth painted wall alone is not a blockout; use material/style discontinuity and visibly unfinished geometry. Never accept the private generation geometry as finished artwork.'}
        if row.descriptor.get('renderStage') in ('environment','environment-edit'):
            data['futureCast']=[{k:p[k] for k in ('entityId','name','description','appearance','expectedPose') if k in p}
                                for p in row.descriptor.get('futureCast',[])]
            data['publicNarrative']=narrative
            data['inspectionPurpose']='Inspect current environment AND propose every futureCast placement; futureCast is not current people.'
            data['requiredOutputKeys']=['composition','characters','fixtures','exits','placements']
            data['placementContract']={'requiredIds':[p['entityId'] for p in data['futureCast']],
                'viewIndex':'integer 0..3; choose camera with entire person and support inside untapered interior',
                'bodyBoxPixels':'[left,top,right,bottom]; ALL coordinates 26..486; width 30..240, height 100..460',
                'groundPointPixels':'at bottom of proposed body, visible support on actual image',
                'pose':'standing, seated or kneeling; follow explicit publicNarrative',
                'confidence':'0.8..1','evidence':'describe real free floor/chair and relative adult scale; do not overlap another proposed person'}
            if row.descriptor.get('measurementEfficiencyVersion')==1:
                data['placementContract'].update(
                    coordinateSystem='normalized-1000-xy-v1',
                    viewIndex='integer 0..7; choose an actual supplied camera with entire body and physical support visible',
                    bodyBoxPixels='OUTPUT normalized 0..1000 XY: all body bounds 51..949; width 59..469; standing height 273.4375..898; seated/kneeling height 195.3125..898. These are normalized units, NOT 512 camera pixels.',
                    groundPointPixels='OUTPUT normalized 0..1000 XY, on actual walking floor, inside body width, within 23.4375 normalized units of body bottom; a seated pelvis has separate real chair support',
                    measurement='Measure real adult scale at this camera pitch. Do not enlarge a distant location to meet the minimum size. Preserve explicit current pose. Proposals are independently checked before painting.')
        data['passageContract']={'required':'Every passable exit needs passageEvidence {kind:door-open/path-open/stairs-open,viewIndex:0..7,boxPixels:[left,top,right,bottom],description:actual open passage and floor continuity}. For exits with visualCue also include visualCueMatched:true and cueEvidence:actual matching natural landmark, at least10characters. Each such route requires a separate measured passage. Closed door is NOT door-open. Measure anchorPixels on walking floor at this opening, not another door or object.'}
        # Put the complete measurement instruction in the actual user request,
        # including the first inspection. Real first-pass MiMo vision exchanges
        # stalled at supplier heartbeats; the same pixels with this instruction
        # returned a complete, metered result. This supplies no coordinates or
        # fictitious prior findings and never relaxes the acceptance checks.
        data['inspectionReminder']='Inspect these EXACT received pixels. Supply every required field. Do not infer missing evidence from the requested placement; choose the clearest camera.'
        if row.image.get('bindingEvidenceRetries'):
            data['previousMissingEvidence']=row.image.get('detectionIssues',[])
            data['inspectionReminder']='Reinspect these EXACT same received pixels. Supply every required field. Do not infer missing evidence from the requested placement; choose the clearest camera.'
        profile=db.get(ModelProfile,task.payload['profileId']);limit=completion_limit(profile,'analysis',world=True)
        head_aliases=task.checkpoint.get('headStageAliases',{})
    def raw_vision_call(stage,prompt,payload,bound):
        if prompt=='world_scene_review':return call_provider(task_id,stage,prompt,payload,bound)
        if prompt=='world_scene_people_inventory':return call_provider(task_id,stage,prompt,payload,bound)
        if prompt=='world_scene_surface_context':return call_provider(task_id,stage,prompt,payload,bound)
        if prompt=='world_scene_surfaces' and payload.get('inspectionMode')=='same-camera-surface-detail':return call_provider(task_id,stage,prompt,payload,bound)
        if prompt=='world_scene_supports' and payload.get('inspectionMode') in ('proposed-body-support-context','proposed-body-support-detail'):return call_provider(task_id,stage,prompt,payload,bound)
        if prompt=='world_scene_aperture':return call_provider(task_id,stage,prompt,payload,bound)
        if expected_scene.get('coordinateInspectionVersion')!=1:return call_provider(task_id,stage,prompt,payload,bound)
        from .coordinates import request as coordinate_request,response as coordinate_response
        if expected_scene.get('coordinateCameraVersion')==1:
            from .coordinate_cameras import prepare,wire_protocol
            payload=prepare(payload,cameras)
        wire=coordinate_request(payload,crop_pixels=expected_scene.get('headCropPixelVersion')==1)
        # call_provider journals the untouched supplier reply. Interpretation is
        # a reproducible unit conversion only after an explicit matching tag.
        transmitted=wire_protocol(wire) if expected_scene.get('coordinateCameraVersion')==1 else wire
        return coordinate_response(call_provider(task_id,stage,prompt,transmitted,bound),wire)
    def vision_call(stage,prompt,payload,bound):
        if settings.ai_mode!='real' and row.descriptor.get('inspectionPerformanceVersion')!=1:return raw_vision_call(stage,prompt,payload,bound)
        from .parallel import visual_slot
        from ..worker import check_cancel
        try:
            with visual_slot(lambda:check_cancel(task_id),cooldown_scope):return raw_vision_call(stage,prompt,payload,bound)
        except Problem as error:
            if error.code=='SCENE_VISION_COOLDOWN':
                from .cooldown import record_deferral
                record_deferral(task_id,error.details)
            raise
    def inspect_inventory(entry):
        index,camera=entry
        answer=vision_call('analysis:people-view:'+str(index),'world_scene_people_inventory',inventory_request(camera,facts=row.descriptor.get('visualFacts')),limit)
        try:observed=json.loads(answer.get('text',''))
        except (ValueError,TypeError):observed={}
        result={'viewIndex':index,'observation':observed}
        if expected_scene.get('peopleClarificationVersion')==1 and needs_clarification(observed):
            detail=vision_call('analysis:people-check:'+str(index),'world_scene_people_inventory',inventory_request(camera,clarify=observed,facts=row.descriptor.get('visualFacts')),limit)
            try:detail_observation=json.loads(detail.get('text',''))
            except (ValueError,TypeError):detail_observation={}
            result['clarification']={'source':'same-camera-person-detail-v1','viewIndex':index,
                'candidateRegions':candidate_boxes(observed),'observation':detail_observation}
        return result
    preflight=reused_preflight
    # Actual identity-reference comparisons retain their complete legacy
    # exchange. Unreferenced default actors and empty environments already have
    # dedicated independent people/head/body, fixture, route and support reads.
    single_reads=(row.descriptor.get('independentSceneReadsVersion')==1
        and not any(p.get('lookImageId') for p in cast) and not row.descriptor.get('characterReferenceOrder'))
    if (row.descriptor.get('inspectionPerformanceVersion')==1
            and expected_scene.get('sceneReviewVersion')==1 and expected_scene.get('peopleInventoryVersion')==1):
        from .parallel import ordered_reads
        from .scene_review import request as review_request
        from .people_inventory import request as inventory_request,needs_clarification,candidate_boxes
        entries=[key for key in ['review',0,1,2,3] if key not in preflight]+([] if received_base or single_reads or 'binding' in preflight else ['binding'])
        from threading import Lock
        preview_reads=dict(preflight);preview_lock=Lock();preview_sent=False
        def early_environment_preview(key,value):
            nonlocal preview_sent
            if cast or key=='binding':return
            with preview_lock:
                preview_reads[key]=value
                if preview_sent or not all(k in preview_reads for k in ['review',0,1,2,3]):return
                from .progressive import publish_received_environment
                publish_received_environment(task_id,inspected_hash,preview_reads)
                preview_sent=True
        def read_actual_pixels(key):
            if key=='review':value=vision_call('analysis:scene-review','world_scene_review',review_request(image_data,row.descriptor),limit)
            elif key=='binding':value=vision_call('analysis:scene-binding','world_scene_binding',data,limit)
            else:value=inspect_inventory((key,cameras[key]))
            early_environment_preview(key,value)
            return key,value
        # These observations use the same frozen pixels and are independent.
        # Preflight changes scheduling only: a bad global review still rejects
        # ALL coordinates; four blind counts and full support/head gates remain.
        preflight.update(ordered_reads(read_actual_pixels,entries,workers=6))
    scene_observation=None
    if expected_scene.get('sceneReviewVersion')==1:
        from .scene_review import request as review_request,evaluate as evaluate_review
        observation=preflight.get('review') or vision_call('analysis:scene-review','world_scene_review',review_request(image_data,row.descriptor),limit)
        try:scene_observation=json.loads(observation.get('text',''))
        except (ValueError,TypeError):scene_observation={}
        if not isinstance(scene_observation,dict):scene_observation={}
        measured_review=evaluate_review(scene_observation,cast,expected_scene)
        global_evidence_recheck=None
        if row.descriptor.get('globalEvidenceRecoveryVersion')==1:
            from .global_evidence import needs_detail,request as global_detail_request,apply as apply_global_detail
            if needs_detail(measured_review):
                answer=vision_call('analysis:scene-review-detail','world_scene_review',
                    global_detail_request(image_data,row.descriptor,cameras),limit)
                scene_observation,measured_review,global_evidence_recheck=apply_global_detail(
                    scene_observation,answer,cast,expected_scene,inspected_hash)
        global_artifact_recheck=None
        if (row.descriptor.get('inspectionPerformanceVersion')==1
                and 'reference-artifacts' in measured_review['issues']
                and not set(measured_review['issues'])-{'reference-artifacts','scene-review-unconfirmed'}):
            # One bounded re-observation of the SAME pixels can distinguish
            # integrated framed art from a pasted reference sheet. The original
            # supplier reply remains journalled; neither requested cast size nor
            # an operator verdict can clear it. Four blind views still follow.
            from .artifact_conflicts import request as artifact_request
            answer=vision_call('analysis:artifact-global','world_scene_review',
                artifact_request(image_data,row.descriptor,scene_observation['referenceArtifacts'],
                    cameras=cameras[:4] if row.descriptor.get('actorSurroundingsVersion')==1 else None),limit)
            try:rechecked=json.loads(answer.get('text',''))
            except (ValueError,TypeError):rechecked={}
            global_artifact_recheck={'version':1,'imageHash':inspected_hash,
                'original':scene_observation,'observation':rechecked}
            scene_observation=rechecked if isinstance(rechecked,dict) else {}
            measured_review=evaluate_review(scene_observation,cast,expected_scene)
        with SessionLocal() as progress_db:
            lock_task_world(progress_db,task_id)
            progress_task=progress_db.get(Task,task_id);assert_task_active(progress_db,progress_task)
            picture=progress_db.get(SceneImage,progress_task.payload['sceneId'])
            if (not picture or picture.owner_id!=progress_task.owner_id
                    or picture.image.get('bindingTaskId')!=task_id or picture.image.get('sha256')!=inspected_hash):
                raise Problem('TASK_CANCELLED','校验画面已变化')
            picture.image={**picture.image,'sceneReview':{'version':1,'imageHash':inspected_hash,
                'observation':scene_observation,'previewEligible':measured_review['verified'] and not measured_review['issues']},
                **({'globalArtifactRecheck':global_artifact_recheck} if global_artifact_recheck else {})}
            progress_db.commit()
    inventory=None;count_recheck=None
    if (expected_scene.get('peopleInventoryVersion')==1 and measured_review['verified']
            and not set(measured_review['issues'])-{'unexpected-people','missing-people'}):
        from .people_inventory import request as inventory_request,needs_clarification,candidate_boxes
        if preflight:
            views=[preflight[index] for index in range(4)]
        elif row.descriptor.get('inspectionPerformanceVersion')==1:
            from .parallel import ordered_reads
            views=ordered_reads(inspect_inventory,enumerate(cameras[:4]))
        else:views=[inspect_inventory(entry) for entry in enumerate(cameras[:4])]
        inventory={'version':1,'imageHash':inspected_hash,'views':views}
        from .people_inventory import evaluate as inventory_evaluate
        blind_count=inventory_evaluate(inventory,inspected_hash)
        if blind_count['verified'] and blind_count['count']!=scene_observation.get('visiblePeopleCount'):
            from .scene_review import count_conflict_request
            answer=vision_call('analysis:count-conflict','world_scene_review',count_conflict_request(image_data,row.descriptor,scene_observation),limit)
            try:rechecked=json.loads(answer.get('text',''))
            except (ValueError,TypeError):rechecked={}
            count_recheck={'version':1,'imageHash':inspected_hash,'original':scene_observation,
                'independentInventory':inventory,'observation':rechecked}
            scene_observation=rechecked if isinstance(rechecked,dict) else {}
            if (expected_scene.get('peopleCountConflictVersion')==1
                    and evaluate_review(scene_observation,cast,expected_scene).get('verified')
                    and not set(evaluate_review(scene_observation,cast,expected_scene).get('issues',[]))-{'unexpected-people','missing-people'}
                    and blind_count['count']!=scene_observation.get('visiblePeopleCount')):
                # One fresh, blind same-camera detail can resolve a shadow
                # mistaken for a second head. Neither cast nor desired count is
                # sent. Retain the original conflicting observation and require
                # agreement of ALL four cameras with the whole-scene review.
                from .parallel import ordered_reads
                from .people_inventory import observed_view
                from copy import deepcopy
                count_recheck['independentInventory']=deepcopy(inventory)
                def recheck_count(entry):
                    index=entry['viewIndex'];original=entry['observation']
                    answer=vision_call('analysis:people-conflict:'+str(index),'world_scene_people_inventory',
                        inventory_request(cameras[index],clarify=original,facts=row.descriptor.get('visualFacts')),limit)
                    try:detail=json.loads(answer.get('text',''))
                    except (ValueError,TypeError):detail={}
                    return index,{'source':'same-camera-person-detail-v1','viewIndex':index,
                        'reason':'independent-scene-count-conflict-v1','candidateRegions':candidate_boxes(original),
                        'observation':detail,
                        **({'previousClarification':entry['clarification']} if entry.get('clarification') else {})}
                nonempty=[entry for entry in views if (observed_view(entry) or {}).get('count',0)>0]
                for index,detail in ordered_reads(recheck_count,nonempty):views[index]['clarification']=detail
        measured_review=composition_from_result({'characters':[],
            'sceneReview':{'version':1,'imageHash':inspected_hash,'observation':scene_observation},
            'peopleInventory':inventory},cast,expected_scene)
        with SessionLocal() as progress_db:
            lock_task_world(progress_db,task_id)
            progress_task=progress_db.get(Task,task_id);assert_task_active(progress_db,progress_task)
            picture=progress_db.get(SceneImage,progress_task.payload['sceneId'])
            if not picture or picture.owner_id!=progress_task.owner_id or picture.image.get('bindingTaskId')!=task_id or picture.image.get('sha256')!=inspected_hash:raise Problem('TASK_CANCELLED','校验画面已变化')
            # A fresh empty-room review supersedes an earlier failed review of
            # these same pixels. Publish its current global verdict together
            # with the complete four-camera inventory, before slower placement
            # measurements. Actor stages still require their full inspection.
            picture.image={**picture.image,'peopleInventory':inventory,
                'sceneReview':{'version':1,'imageHash':inspected_hash,'observation':scene_observation,
                    'previewEligible':measured_review['verified'] and not measured_review['issues']},
                **({'sceneCountRecheck':count_recheck} if count_recheck else {}),
                **({'composition':measured_review} if not cast else {})}
            progress_db.commit()
    # A confirmed bad global picture needs correction before coordinate calls.
    # A malformed observation stays unconfirmed instead of becoming a pass.
    skip_measurements=(scene_observation is not None and
                       (not measured_review['verified'] or bool(measured_review['issues'])))
    result=({'text':'{"characters":[],"fixtures":[],"exits":[],"placements":[]}'} if skip_measurements or single_reads and not received_base else
            received_base or preflight.get('binding') or vision_call('analysis:scene-binding','world_scene_binding',data,limit))
    try:parsed=json.loads(result.get('text',''))
    except (ValueError,TypeError):parsed={}
    if row.descriptor.get('inspectionPerformanceVersion')==1:
        from .candidate_scope import scope
        parsed=scope(parsed,targets)
    if scene_observation is not None:
        from .scene_review import apply as apply_scene_review
        parsed=apply_scene_review(parsed,scene_observation,inspected_hash)
        if global_evidence_recheck is not None:parsed['globalEvidenceRecheck']=global_evidence_recheck
    if inventory is not None:parsed['peopleInventory']=inventory
    if count_recheck is not None:parsed['sceneCountRecheck']=count_recheck
    if scene_observation is not None and measured_review['verified'] and not measured_review['issues']:
        from .artifact_conflicts import suspects,request as artifact_request
        artifacts=suspects(parsed)
        if artifacts:
            answer=vision_call('analysis:artifact-conflict','world_scene_review',artifact_request(image_data,row.descriptor,artifacts,
                cameras=cameras[:4] if row.descriptor.get('actorSurroundingsVersion')==1 else None),limit)
            try:rechecked=json.loads(answer.get('text',''))
            except (ValueError,TypeError):rechecked={}
            parsed['referenceArtifactRecheck']={'version':1,'imageHash':inspected_hash,'candidates':artifacts,'observation':rechecked}
    first_composition=composition_from_result(parsed,cast,expected_scene)
    retained={'characters':[],'exits':[]}
    if first_composition.get('verified') and not first_composition.get('issues'):
        from .continuity import received_parts,merge as merge_received
        with SessionLocal() as evidence_db:retained=received_parts(evidence_db,row,targets,cameras)
        parsed=merge_received(parsed,retained)
    retained_people={p['entityId'] for p in retained['characters']}
    retained_exits={p['id'] for p in retained['exits']}
    if (expected_scene.get('artworkReviewVersion')==1 and expected_scene.get('sceneReviewVersion')!=1
            and any(p in first_composition.get('issues',[]) for p in ('unfinished-artwork','projection-mismatch'))):
        from .artwork import request as artwork_request,apply as apply_artwork
        review=vision_call('analysis:art-review','world_scene_artwork',artwork_request(parsed,image_data,cameras),limit)
        parsed=apply_artwork(parsed,review)
    from .passages import camera_review_requests,apply_review,merge_camera_reviews
    required=[p for p in targets['exits'] if p.get('independentReviewRequired') and p['id'] not in retained_exits]
    composition_failure=composition_from_result(parsed,cast,expected_scene)
    if expected_scene.get('sceneReviewVersion')==1 and not composition_failure.get('verified'):
        required=[]
    if (composition_failure.get('confidence',0)>=.8
            and composition_failure.get('projection')!='uncertain'
            and any(issue in ('unexpected-people','projection-mismatch','reference-artifacts','space-mismatch','lighting-mismatch','unfinished-artwork','visual-fact-mismatch') for issue in composition_failure['issues'])):
        # Measuring eight route views cannot repair a known incorrect picture.
        # Preserve the received failure and let the bounded painting correction
        # run before spending on independent coordinates for these bad pixels.
        required=[]
    findings=[];surface_index=0;surface_failures=[]
    review_views=list(enumerate(camera_review_requests(required,cameras)))
    route_reads={}
    if (row.descriptor.get('inspectionPerformanceVersion')==1 and required
            and row.descriptor.get('measurementEfficiencyVersion')!=1):
        # All four blind opening searches use independent actual cameras. The
        # usual floor/threshold checks below still own every accepted route.
        from .parallel import ordered_reads
        def read_route(entry):
            index,(review_data,regions)=entry
            notes=[p for p in row.image.get('pointCorrectionNotes',[]) if p.get('viewIndex')==index]
            native=expected_scene.get('apertureInspectionVersion')==1 and view['descriptor'].get('spaceKind')=='interior'
            if native:
                from .apertures import request as aperture_request
                review_data=aperture_request([p for p in required if p['id'] in regions],cameras[index],
                    native_size=1000 if expected_scene.get('coordinateCameraVersion')==1 else 512,
                    measure_points=expected_scene.get('aperturePointVersion')==1,rejected_points=notes)
            elif notes:
                review_data={**review_data,'rejectedSameImagePointMeasurements':notes,
                    'correctionInstruction':'Re-locate the actual opening and its walking floor in these same pixels. Do not repeat the rejected wall, furniture or uncertain points.'}
            return index,vision_call('analysis:scene-view:'+str(index),
                'world_scene_aperture' if native else 'world_scene_passages',review_data,limit)
        suggested=[p.get('passageEvidence',{}).get('viewIndex') for p in parsed.get('exits',[]) if isinstance(p,dict)]
        review_views.sort(key=lambda pair: (pair[0] not in suggested,pair[0]))
        route_reads=dict(ordered_reads(read_route,review_views[:4]))
    elif row.descriptor.get('measurementEfficiencyVersion')==1 and required:
        # Coarse coordinates are hints only. One independently observed
        # doorway PLUS its actual floor proof can settle that destination;
        # unrelated cameras need no new paid search once every exit is proven.
        suggested=[p.get('passageEvidence',{}).get('viewIndex',p.get('viewIndex'))
                   for p in parsed.get('exits',[]) if isinstance(p,dict)]
        review_views.sort(key=lambda pair:(pair[0] not in suggested,pair[0]))
    for index,(review_data,regions) in review_views:
        if index>=4:
            provisional=merge_camera_reviews(parsed,findings,required)
            remaining={p['id'] for p in provisional.get('exits',[])
                if p['id'] in regions and p.get('passageEvidence',{}).get('independentReview',{}).get('matched') is not True}
            if row.descriptor.get('measurementEfficiencyVersion')==1:
                proven={p.get('id') for finding in findings for p in finding.get('exits',[])
                        if p.get('passageEvidence',{}).get('independentReview',{}).get('matched') is True
                        and p.get('passageEvidence',{}).get('surfaceReview',{}).get('imageHash')==inspected_hash}
                remaining={p['id'] for p in required if p['id'] in regions and p['id'] not in proven}
            if not remaining:break
            # Far routes already measured in a horizontal image need no extra
            # call; downward views cover a close threshold below that frame.
            review_data['routes']=[p for p in review_data['routes'] if p['id'] in remaining]
            regions={ident:region for ident,region in regions.items() if ident in remaining}
        notes=[p for p in row.image.get('pointCorrectionNotes',[]) if p.get('viewIndex')==index]
        if notes:
            review_data['rejectedSameImagePointMeasurements']=notes
            review_data['correctionInstruction']='These EXACT previous points failed blind inspection of the actual pixels. Re-locate the real doorway/aisle on this same image. Do not copy those wall/furniture/uncertain points or label them floor. Return absent if no genuine opening can be measured.'
        native=expected_scene.get('apertureInspectionVersion')==1 and view['descriptor'].get('spaceKind')=='interior'
        if native:
            from .apertures import request as aperture_request
            review_data=aperture_request([p for p in required if p['id'] in regions],cameras[index],
                native_size=1000 if expected_scene.get('coordinateCameraVersion')==1 else 512,
                measure_points=expected_scene.get('aperturePointVersion')==1,rejected_points=notes)
        reviewed=(route_reads[index] if index in route_reads else vision_call('analysis:scene-view:'+str(index),
            'world_scene_aperture' if native else 'world_scene_passages',review_data,limit))
        try:review=json.loads(reviewed.get('text',''))
        except (ValueError,TypeError):review={}
        if native:
            from .apertures import candidates
            finding=candidates(review,[p for p in required if p['id'] in regions],index,
                               measure_points=expected_scene.get('aperturePointVersion')==1,
                               fallback_on_geometry=expected_scene.get('apertureProposalVersion')==1)
            surface_failures.extend(finding.get('rejectedPointProposals',[]))
        else:finding=apply_review({},review,regions)
        from .surfaces import point_request,apply_surface
        checked=[]
        for part in finding.get('exits',[]):
            if ((part.get('passageEvidence',{}).get('independentReview',{}).get('matched') is True or part.get('requiresBlindApertureCheck') is True)
                    and next((p for p in required if p['id']==part['id']),{}).get('requiredSurfaceReview')):
                single_context=expected_scene.get('surfaceContextVersion')==1
                confirmation=vision_call('analysis:scene-ground:'+str(surface_index),
                    'world_scene_surface_context' if single_context else 'world_scene_surfaces',
                    point_request(part,cameras[index],single_camera=single_context),limit)
                surface_index+=1
                try:surface=json.loads(confirmation.get('text',''))
                except (ValueError,TypeError):surface={}
                detailed=False
                if expected_scene.get('surfaceClarificationVersion')==1:
                    from .surfaces import needs_detail,detail_request
                    from .inspection import detail_camera
                    if needs_detail(surface):
                        clarification=vision_call('analysis:ground-detail:'+str(surface_index-1),
                            'world_scene_surface_context',detail_request(part,detail_camera(source,index)),limit)
                        try:surface=json.loads(clarification.get('text',''))
                        except (ValueError,TypeError):surface={}
                        detailed=True
                part=apply_surface(part,surface,inspected_hash,single_camera=single_context or detailed)
                if part['passageEvidence']['independentReview'].get('matched') is not True:
                    proof=part['passageEvidence']['surfaceReview']
                    surface_failures.append({'id':part['id'],'viewIndex':index,'passageKind':part['passageEvidence']['kind'],'nearPixels':proof['nearPixels'],
                        'farPixels':proof['farPixels'],'throughPointPixels':proof['throughPointPixels'],
                        'apertureBoxPixels':proof['apertureBoxPixels'],'actualPointObservations':proof['result']})
            checked.append(part)
        finding['exits']=checked;findings.append(finding)
        if row.descriptor.get('inspectionPerformanceVersion')==1:
            provisional=merge_camera_reviews(parsed,findings,required)
            confirmed={p['id'] for p in provisional.get('exits',[])
                if p.get('passageEvidence',{}).get('independentReview',{}).get('matched') is True}
            if all(p['id'] in confirmed for p in required):break
    if required:parsed=merge_camera_reviews(parsed,findings,required)
    if expected_scene.get('fixtureInspectionVersion')==1 and composition_failure.get('verified') is True and not composition_failure.get('issues'):
        from .fixtures import inspect_fixtures
        parsed=inspect_fixtures(parsed,targets,cameras,image_size,
            lambda stage,prompt,payload,limit:vision_call(stage,prompt,payload,limit),limit)
    placement_exhausted=False
    if data.get('futureCast') and composition_failure.get('verified') is True and not composition_failure.get('issues'):
        def record_exhaustion():
            nonlocal placement_exhausted
            placement_exhausted=True
        from .placements import inspect_placements
        def record_placement(part,proof,stage,index):
            from .placement_receipts import record
            placement_proofs.append(record(task_id,part,proof,stage,cameras[index]))
        parsed=inspect_placements(parsed,data['futureCast'],cameras,narrative,
            lambda stage,prompt,payload,limit:vision_call(stage,prompt,payload,limit),limit,
            independent=expected_scene.get('placementInspectionVersion') in (1,2),
            correct_invalid=expected_scene.get('placementInspectionVersion')==2,
            verify_support=expected_scene.get('placementSupportVersion')==1,
            downward=expected_scene.get('placementCameraVersion')==1,
            single_context=expected_scene.get('surfaceContextVersion')==1,
            clarify_support=expected_scene.get('supportClarificationVersion')==1,panorama=source,on_exhausted=record_exhaustion,
            verify_initial=row.descriptor.get('inspectionPerformanceVersion')==1,
            readable_version=row.descriptor.get('readablePlacementVersion',1),
            verified_placements=received_placements,on_verified=record_placement)
    if expected_scene.get('personInspectionVersion')==1 and cast and composition_failure.get('verified') is True and not composition_failure.get('issues'):
        from .people import inspect_people
        indices=None
        if single_reads and inventory:
            from .people_inventory import needs_clarification
            indices=[entry['viewIndex'] for entry in inventory['views']
                if (entry['clarification']['observation'] if needs_clarification(entry['observation']) else entry['observation'])['count']>0]
        parsed=inspect_people(parsed,public_cast,cameras,
            lambda stage,prompt,payload,limit:vision_call(stage,prompt,payload,limit),limit,retained_ids=retained_people,
            view_indices=indices,recount=not single_reads,parallel=single_reads)
    if expected_scene.get('headInspectionVersion') in (1,2) and cast and composition_failure.get('verified') is True and not composition_failure.get('issues'):
        from .heads import inspect_heads
        def save_head_progress(progress):
            # A later supplier timeout must not erase complete independent
            # heads or walking floors already measured from these same bytes.
            measured=evaluate_inspection(progress,cast,targets,image_size,expected_scene,
                future_cast=data.get('futureCast',[]),expected_poses=row.descriptor.get('expectedPoses',{}))
            from .inspection_progress import save
            save(task_id,inspected_hash,progress,measured,targets)
        save_head_progress(parsed)
        parsed=inspect_heads(parsed,cast,cameras,inspected_hash,
            lambda stage,prompt,payload,limit:vision_call(head_aliases.get(stage,stage),prompt,payload,limit),limit,
            on_progress=save_head_progress,search_cameras=expected_scene.get('headCameraSearchVersion')==1,retained_ids=retained_people,
            parallel=row.descriptor.get('inspectionPerformanceVersion')==1)
        if expected_scene.get('bodyInspectionVersion') == 1:
            from .bodies import inspect_bodies
            current_measurement=evaluate_inspection(parsed,cast,targets,image_size,expected_scene)
            parsed=inspect_bodies(parsed,cast,cameras,inspected_hash,image_size,
                lambda stage,prompt,payload,limit:vision_call(stage,prompt,payload,limit),limit,
                verified_fixtures={key.split(':',1)[1] for key in current_measurement['spatialAnchors'] if key.startswith('fixtures:')},
                on_progress=save_head_progress,parallel=row.descriptor.get('inspectionPerformanceVersion')==1)
    measured=evaluate_inspection(parsed,cast,targets,image_size,expected_scene,
        future_cast=data.get('futureCast',[]),expected_poses=row.descriptor.get('expectedPoses',{}))
    anchors=measured['characterAnchors'];missing=measured['missingCharacterIds'];missing_exits=measured['missingExitIds']
    composition=measured['composition'];valid_inspection=measured['anchorStatus']!='unavailable'
    with SessionLocal() as db:
        lock_task_world(db,task_id);task=db.get(Task,task_id);assert_task_active(db,task)
        row=db.get(SceneImage,task.payload['sceneId'])
        current_line=db.get(WorldLine,task.payload['lineId'])
        if (row and row.owner_id==task.owner_id and row.image.get('bindingTaskId')==task.id
                and row.image.get('sha256') == inspected_hash):
            if (row.descriptor.get('inspectionReceiptVersion')==1 and inventory
                    and first_composition.get('verified') is True and not first_composition.get('issues')):
                from copy import deepcopy
                task.checkpoint={**task.checkpoint,'completedSceneReads':{'sceneId':row.id,'imageHash':inspected_hash,
                    'context':inspection_context(row,targets,narrative=narrative),
                    'review':deepcopy(scene_observation),'inventory':deepcopy(inventory)},
                    'completedPlacementReads':{'version':1,'sceneId':row.id,'imageHash':inspected_hash,
                        'context':inspection_context(row,targets,narrative=narrative),'proofs':deepcopy(placement_proofs)}}
            row.image={**row.image,**measured,'pointCorrectionNotes':surface_failures,'placementEvidenceExhausted':placement_exhausted,
                **({'globalEvidenceRecheck':parsed['globalEvidenceRecheck']} if 'globalEvidenceRecheck' in parsed else {}),
                **({'artworkSecondReview':parsed['artworkSecondReview']} if 'artworkSecondReview' in parsed else {})}
            if measured['anchorStatus']=='complete':
                from copy import deepcopy
                task.checkpoint={**task.checkpoint,'verifiedImageInspection':{
                    'sceneId':row.id,'imageHash':inspected_hash,'imageSize':list(image_size),
                    'context':inspection_context(row,targets),'result':deepcopy(parsed)}}
            # Never correct an obsolete scene after the player has moved on.
            from .cache import compatible
            current=(db.get(WorldCheckpoint,current_line.head_checkpoint_id)
                     if current_line and current_line.owner_id==task.owner_id
                     and current_line.world_id==row.world_id and current_line.status=='active' else None)
            if (valid_inspection and not row.descriptor.get('pipelineParentId')
                    and current and compatible(row,scene_view(current,current_line.id))):
                repair=queue_visual_repair(db,row,view,missing,missing_exits,composition['issues'])
                if repair:row.image={**row.image,'repairSceneId':repair,'anchorStatus':'repairing'}
        task.state='succeeded';task.persistence_state='saved';task.result_outcome='complete';task.result={**task.result,'phase':'complete','anchorCount':len(anchors)}
        add_event(db,task,'terminal',{'state':'succeeded'});db.commit()
