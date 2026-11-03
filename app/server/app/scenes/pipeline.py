"""Durable environment -> inspected placement -> painted actors -> inspection.

Each submitted image owns a separate receipt. Private stages are never exposed
as a playable picture. Unknown requests stop the chain without resubmission.
"""
from copy import deepcopy
from hashlib import sha256
from math import isfinite
from sqlalchemy import select,func
from ..domain import canonical
from ..models import Task,uid
from ..world.models import WorldCheckpoint
from .models import SceneImage
from .service import image_bytes,save_image,scene_view
from .cache import compatible

VERSION=1
PRIVATE_KINDS=('scene-environment','scene-actor-edit','scene-environment-edit')


def rebuild_environment(descriptor, source_id):
    """A fresh spherical guide must never inherit a camera-edit contract."""
    result=deepcopy(descriptor)
    for key in ('environmentEdit','floorRepairPlan','environmentCameraIndex',
                'environmentCameraPitchVersion','remainingRoomEdits','remainingRouteEdits',
                'remainingEditCameras','environmentRoutePlan','routeRepairGuideVersion',
                'protectedPassages','environmentCleaned'):
        result.pop(key,None)
    result.update(kind='scene-environment',renderStage='environment',inputSceneId=source_id,
                  rebuildEnvironmentGuide=True)
    return result


def cleanup_views(image):
    """Edit only cameras whose independent inventory actually saw a person."""
    from .people_inventory import evaluate,needs_clarification
    proof=image.get('peopleInventory')
    if evaluate(proof,image.get('sha256')).get('verified'):
        views=[]
        for entry in proof['views']:
            observed=entry['observation']
            if needs_clarification(observed):observed=entry['clarification']['observation']
            if observed['count']>0:views.append(entry['viewIndex'])
        if views:return views
    return [0,1,2,3]


def is_parent(row):
    return row.descriptor.get('renderPipeline')==VERSION and row.descriptor.get('kind') not in PRIVATE_KINDS


def base_key(row):
    value=deepcopy(row.descriptor.get('appearance',{}))
    for key in ('cast','playerId','characterLooks'):value.pop(key,None)
    return sha256(canonical({'world':row.world_id,'profile':row.profile_version,'appearance':value,
                             'style':{k:row.descriptor.get(k) for k in ('genre','narrativeStyle','setting','visualSettingVersion','environmentGeometryVersion')}}).encode()).hexdigest()


def owned_stage(db,parent,ident):
    row=db.get(SceneImage,ident) if ident else None
    return row if row and row.owner_id==parent.owner_id and row.world_id==parent.world_id and row.line_id==parent.line_id and row.descriptor.get('pipelineParentId')==parent.id else None


def resume_unsent_connection(db,parent,line):
    """Resume the same owned reservation only when HTTP was never submitted."""
    if (not line or line.id!=parent.line_id or line.owner_id!=parent.owner_id
            or line.world_id!=parent.world_id):return False
    if parent.status!='failed' or parent.error_code!='SCENE_CONNECTION_FAILED':return False
    child=owned_stage(db,parent,parent.image.get('pipelineStageId'))
    if not child or child.status!='failed' or child.error_code!='SCENE_CONNECTION_FAILED':return False
    diagnostic=child.image.get('failureDiagnostic',{})
    if diagnostic.get('requestSent') is not False or diagnostic.get('responseReceived') is not False:return False
    attempts=child.image.get('connectionRecoveryAttempts',0)
    if type(attempts) is not int or not 0<=attempts<2:return False
    if not narrative_context_matches(db,parent,line):return False
    # The world/line/profile locks and current appearance check are held by
    # queue_scene. Dispatch still rechecks this frozen parent before HTTP.
    child.status='queued';child.error_code=None
    child.image={**child.image,'connectionRecoveryAttempts':attempts+1,
        'unsentConnectionFailures':[*child.image.get('unsentConnectionFailures',[]),deepcopy(diagnostic)][-2:]}
    parent.status='queued';parent.error_code=None
    parent.image={**parent.image,'pipelinePhase':child.descriptor['renderStage']}
    return True


def candidate(db,excluded=()):
    # A waiting parent must not starve its younger child at the front of the
    # image queue. The parent stays queued in the public receipt while waiting.
    offset=0
    while True:
        rows=list(db.scalars(select(SceneImage).where(SceneImage.status.in_(['queued','saving']),SceneImage.id.not_in(excluded)).order_by(SceneImage.created_at,SceneImage.id).offset(offset).limit(128)))
        for row in rows:
            if row.status=='saving' or not is_parent(row):return row
            child=owned_stage(db,row,row.image.get('pipelineStageId'))
            if not child or child.status in ('failed','unknown'):return row
            if child.status=='ready' and child.image.get('anchorStatus') not in ('queued','repairing'):return row
        if len(rows)<128:break
        offset+=128
    return None


def stage(db,parent,descriptor,*,received=None,inspect_received=True):
    # queue_binding applies this same contract to real cast inspections.
    # Freeze it before comparing a received receipt, so copying exact pixels
    # cannot accidentally create a differently hashed inspection and another POST.
    from ..config import settings
    if descriptor.get('renderStage')=='actor' and descriptor.get('actorSurroundingsVersion')==1:
        descriptor=deepcopy(descriptor)
        descriptor['placement']={**descriptor['placement'],'surroundingsVersion':1}
        source=db.get(SceneImage,descriptor.get('inputSceneId'))
        if source and source.owner_id==parent.owner_id and source.world_id==parent.world_id and source.line_id==parent.line_id:
            descriptor['protectedPassages']=[deepcopy(a['passageEvidence']) for a in (source.image.get('spatialAnchors') or {}).values()
                if a.get('kind')=='exits' and a.get('verified') is True and isinstance(a.get('passageEvidence'),dict)
                and a['passageEvidence'].get('surfaceReview',{}).get('imageHash')==source.image.get('sha256')]
    if settings.ai_mode=='real' and descriptor.get('sceneCast'):
        descriptor={**descriptor,'headInspectionVersion':2}
    ident=uid();index=parent.image.get('pipelineStep',0)+1
    row=SceneImage(id=ident,owner_id=parent.owner_id,world_id=parent.world_id,line_id=parent.line_id,
        checkpoint_id=parent.checkpoint_id,profile_id=parent.profile_id,profile_version=parent.profile_version,
        scene_hash=sha256((parent.id+':stage:'+str(index)).encode()).hexdigest(),generation=1,
        status='ready' if received else 'queued',descriptor={**descriptor,'pipelineParentId':parent.id,'pipelineStep':index},image={})
    row.descriptor={**row.descriptor,'resetComposition':False,'visualRepairAttempt':0}
    db.add(row);db.flush()
    parent.image={**parent.image,'pipelineStageId':ident,'pipelineStep':index,'pipelinePhase':descriptor['renderStage']}
    if received:
        row.image=save_image(row,image_bytes(received),{'width':received.image['width'],'height':received.image['height'],
            'mime':'image/webp','projection':'equirectangular','reusedFromSceneId':received.id},received.request_id)
        row.request_id=received.request_id
        if inspect_received:
            from .binding import queue_binding,reuse_completed_inspection
            if not reuse_completed_inspection(db,row,received):queue_binding(db,row)
    return row


def validate_placements(result,cast):
    parts=result.get('placements') if isinstance(result,dict) else None
    if not isinstance(parts,list):return []
    desired={p['entityId']:p for p in cast};allowed=set(desired);found={};regions=[]
    from .inspection import sphere_pixel, region_pixels
    for part in parts:
        if not isinstance(part,dict) or part.get('entityId') not in allowed or part['entityId'] in found:return []
        view=part.get('viewIndex');box=part.get('bodyBoxPixels');ground=part.get('groundPointPixels');confidence=part.get('confidence')
        if type(view) is not int or not 0<=view<(8 if part.get('cameraPitchVersion')==1 else 4) or part.get('pose') not in ('standing','seated','kneeling'):return []
        if desired[part['entityId']].get('expectedPose') and desired[part['entityId']]['expectedPose']!=part['pose']:return []
        if type(confidence) not in (int,float) or not isfinite(confidence) or not .8<=confidence<=1:return []
        if not isinstance(box,list) or len(box)!=4 or not isinstance(ground,list) or len(ground)!=2:return []
        if any(type(v) not in (int,float) or not isfinite(v) or not 0<=v<=512 for v in [*box,*ground]):return []
        # The camera edit tapers at its edges. Choose an overlapping camera
        # where the entire adult fits inside the untapered interior.
        if not 26<=box[0]<box[2]<=486 or not 26<=box[1]<box[3]<=486:return []
        if not 30<=box[2]-box[0]<=240 or not 100<=box[3]-box[1]<=460:return []
        if not box[0]<=ground[0]<=box[2] or abs(ground[1]-box[3])>12:return []
        if not isinstance(part.get('evidence'),str) or not part['evidence'].strip():return []
        gx,gy=sphere_pixel(view,*ground,2048,1024)
        if gy<.5*1024 or gy>.85*1024:return []
        left=sphere_pixel(view,box[0],256,2048,1024)[0];right=sphere_pixel(view,box[2],256,2048,1024)[0]
        if any(max(left,a+shift)<=min(right,b+shift)+8 for a,b in regions for shift in (-2048,0,2048)):return []
        passages=result.get('exits',[])
        if not isinstance(passages,list):return []
        for passage in passages:
            if not isinstance(passage,dict):return []
            evidence=passage.get('passageEvidence')
            if passage.get('visible') is not True or not isinstance(evidence,dict):continue
            pb=evidence.get('boxPixels');pv=evidence.get('viewIndex')
            if not isinstance(pb,list) or len(pb)!=4 or type(pv) is not int or not 0<=pv<8:return []
            if any(type(v) not in (int,float) or not isfinite(v) or not 0<=v<=512 for v in pb):return []
            if not pb[0]<pb[2] or not pb[1]<pb[3]:return []
            # Exit thresholds are often measured in a downward camera. Use
            # that camera's real rectangle/pitch, not a horizontal centre-line
            # or an invented position. Unwrapped longitudes retain seam overlap.
            a,_,b,_=region_pixels(pv,pb,2048,1024)
            if any(max(left,a+shift)<=min(right,b+shift)+8 for shift in (-2048,0,2048)):return []
        regions.append((left,right));found[part['entityId']]=deepcopy(part)
    return [found[p['entityId']] for p in cast] if set(found)==allowed else []


def route_edit_plan(descriptor, missing=None):
    from math import atan2,sin,cos
    from .inspection import CENTRES
    plan={}
    for route in descriptor.get('exitRoutes',[]):
        if missing is not None and route.get('id') not in missing:continue
        yaw=route.get('yaw');cue=route.get('visualCue')
        if type(yaw) not in (int,float) or not isfinite(yaw) or not isinstance(cue,str) or not cue.strip():continue
        index=min(range(4),key=lambda i:abs(atan2(sin(yaw-CENTRES[i]),cos(yaw-CENTRES[i]))))
        plan.setdefault(str(index),[]).append(deepcopy(route))
    return plan


def route_edit_steps(plan):
    # Even routes sharing a cardinal camera need separate centred edit views.
    # Three asymmetric exits can never all fit a single safe 100-degree frame.
    return [{'viewIndex':int(index),'route':deepcopy(route)}
            for index in sorted(plan,key=int) for route in plan[index]]


def narrative_context_matches(db,row,line):
    """An in-flight actor chain uses one frozen story, including before POST."""
    if not row.descriptor.get('sceneCast'):return True
    from .binding import narrative_hash,placement_narrative
    current=db.get(WorldCheckpoint,line.head_checkpoint_id) if line else None
    origin=db.get(WorldCheckpoint,row.checkpoint_id)
    if not current or not origin:return False
    if any(cp.owner_id!=row.owner_id or cp.world_id!=row.world_id or cp.line_id!=row.line_id for cp in (current,origin)):return False
    frozen=row.descriptor.get('pipelineNarrativeHash') or narrative_hash(placement_narrative(origin))
    return narrative_hash(placement_narrative(current))==frozen


def advance(db,row,world,line):
    current=db.get(WorldCheckpoint,line.head_checkpoint_id) if line else None
    if not current or not compatible(row,scene_view(current,line.id)):
        row.status='failed';row.error_code='SCENE_CONTEXT_CHANGED';return
    child=owned_stage(db,row,row.image.get('pipelineStageId'))
    if child and (child.descriptor.get('visualFacts') or {})!=(row.descriptor.get('visualFacts') or {}):
        row.status='failed';row.error_code='SCENE_FACTS_RECHECK_REQUIRED'
        row.image={**row.image,'pipelinePhase':'stopped'};return
    cast=row.descriptor.get('sceneCast',[])
    if cast:
        from .binding import narrative_hash,placement_narrative
        frozen=row.descriptor.get('pipelineNarrativeHash')
        if not frozen:
            origin=db.get(WorldCheckpoint,row.checkpoint_id)
            if not origin or origin.owner_id!=row.owner_id or origin.world_id!=row.world_id:
                row.status='failed';row.error_code='SCENE_CONTEXT_CHANGED';return
            frozen=narrative_hash(placement_narrative(origin))
            row.descriptor={**row.descriptor,'pipelineNarrativeHash':frozen}
        # One immutable story context governs the environment, EVERY actor,
        # and publication. Actor stages deliberately have futureCast=[], so
        # guarding only that field would miss changes between two actors.
        if not narrative_context_matches(db,row,line):
            row.status='failed';row.error_code='SCENE_CONTEXT_CHANGED'
            row.image={**row.image,'pipelinePhase':'stopped'};return
    if child and child.descriptor.get('futureCast') and child.descriptor.get('placementNarrativeHash'):
        from .binding import narrative_hash,placement_narrative
        # Real actions append a checkpoint; the immutable old checkpoint can
        # still match its receipt while the current story asks for a new pose.
        # Stop this owned pipeline rather than consuming its obsolete placement
        # or reassigning/replaying the old supplier request on another head.
        if narrative_hash(placement_narrative(current))!=child.descriptor['placementNarrativeHash']:
            row.status='failed';row.error_code='SCENE_CONTEXT_CHANGED'
            row.image={**row.image,'pipelinePhase':'stopped'};return
    if not child:
        key=base_key(row);required={p['entityId'] for p in cast};reference=None;base_cast=[]
        candidates=list(db.scalars(select(SceneImage).where(SceneImage.owner_id==row.owner_id,SceneImage.world_id==row.world_id,
            SceneImage.profile_version==row.profile_version,SceneImage.status=='ready',
            SceneImage.descriptor['placeId'].astext==row.descriptor['placeId']).order_by(SceneImage.created_at.desc()).limit(100)))
        candidates.sort(key=lambda p:len(p.descriptor.get('sceneCast',[])),reverse=True)
        for prior in candidates:
            prior_task=db.get(Task,prior.image.get('bindingTaskId')) if prior.image.get('bindingTaskId') else None
            # Copying pixels must not discard an unresolved inspection receipt
            # and thereby silently authorize the same paid inspection again.
            if prior.image.get('bindingTaskId') and (not prior_task or prior_task.state!='succeeded' or prior_task.provider_state!='completed'):continue
            evidence_only=(not prior.descriptor.get('sceneCast')
                and prior.descriptor.get('renderStage') in ('environment','environment-edit')
                and prior.image.get('anchorStatus')=='unavailable'
                and prior.image.get('composition',{}).get('verified') is True
                and not prior.image.get('composition',{}).get('issues')
                and prior.image.get('bindingImageHash')==prior.image.get('sha256')
                and prior.image.get('detectionIssues')
                and all(p.get('reason') in ('placement-evidence-unconfirmed','body-evidence-unconfirmed','passage-evidence-unconfirmed','surface-evidence-unconfirmed') for p in prior.image['detectionIssues'])
                and not prior.image.get('scaleFailureIds'))
            if not (prior.image.get('anchorStatus')=='complete' or evidence_only) or base_key(prior)!=key:continue
            prior_cast=prior.descriptor.get('sceneCast',[])
            if prior_cast and any(prior.descriptor.get(field)!=row.descriptor.get(field) for field in (
                    'sceneReviewVersion','peopleInventoryVersion','peopleClarificationVersion',
                    'aperturePointVersion','apertureProposalVersion','surfaceContextVersion','supportClarificationVersion','surfaceClarificationVersion')):
                # An older physical-person receipt cannot authorize actor reuse
                # under a stronger contract. Empty owned architecture can still
                # be measured for all current people without reusing old bodies.
                continue
            if prior_cast and prior.descriptor.get('identityInspectionVersion')!=row.descriptor.get('identityInspectionVersion'):continue
            if prior_cast and prior.descriptor.get('personInspectionVersion')!=row.descriptor.get('personInspectionVersion'):continue
            if not {p['entityId'] for p in prior_cast}<=required:continue
            desired={p['entityId']:p for p in cast}
            if any(any(person.get(field)!=desired[person['entityId']].get(field) for field in ('lookImageId','appearance')) for person in prior_cast):continue
            # A compatible empty background can still be reused, but a person
            # whose actual old body has a different posture must be repainted.
            if any(desired[person['entityId']].get('expectedPose')
                   and desired[person['entityId']]['expectedPose']!=(prior.descriptor.get('expectedPoses',{}).get(person['entityId'])
                       or prior.image.get('characterAnchors',{}).get(person['entityId'],{}).get('bodyEvidence',{}).get('pose'))
                   for person in prior_cast):continue
            reference=prior;base_cast=deepcopy(prior_cast);break
        remaining=[p for p in cast if p['entityId'] not in {c['entityId'] for c in base_cast}]
        descriptor={**deepcopy(row.descriptor),'kind':'scene-environment','renderStage':'environment','sceneCast':base_cast,
                    'futureCast':remaining,'baseKey':key,'referenceSceneId':None,'referenceMode':'',
                    'expectedPoses':deepcopy(reference.descriptor.get('expectedPoses',{})) if reference else {}}
        stage(db,row,descriptor,received=reference);return
    if child.status in ('failed','unknown'):
        row.status=child.status;row.error_code=child.error_code;row.image={**row.image,'pipelinePhase':'stopped'};return
    if child.status!='ready':return
    # A second image executor can have selected this parent immediately before
    # the first queued its evidence retry. Re-read eligibility after the world
    # lock: a queued/running inspection is pending work, never a failed review.
    # Without this guard the stale selection failed the parent and cancelled
    # the new task before it could send even one request.
    if child.image.get('anchorStatus') in ('queued','repairing'):return
    if child.descriptor.get('renderStage')=='environment-edit' and child.descriptor.get('remainingRoomEdits'):
        from .room_repairs import apply_plan
        first,*remaining=child.descriptor['remainingRoomEdits']
        stage(db,row,apply_plan(child.descriptor,first,remaining,child));return
    if child.descriptor.get('renderStage')=='environment-edit' and child.descriptor.get('remainingRouteEdits'):
        next_edit,*remaining=child.descriptor['remainingRouteEdits']
        index=next_edit['viewIndex']
        stage(db,row,{**deepcopy(child.descriptor),'inputSceneId':child.id,
            'environmentCameraIndex':index,'environmentRoutePlan':{str(index):[next_edit['route']]},
            'remainingRouteEdits':remaining})
        return
    if child.descriptor.get('renderStage')=='environment-edit' and child.descriptor.get('remainingEditCameras'):
        remaining=child.descriptor['remainingEditCameras']
        stage(db,row,{**deepcopy(child.descriptor),'inputSceneId':child.id,
            'environmentCameraIndex':remaining[0],'remainingEditCameras':remaining[1:]})
        return
    from ..config import settings
    from .grounding import binding_matches
    from .binding import placement_context_matches
    if settings.ai_mode!='mock' and (not binding_matches(child) or not placement_context_matches(db,child)):
        # A completed detector may be upgraded; an unresolved paid request
        # still cannot be repeated. Do not paint actors using an obsolete
        # placement contract even if its old receipt says "complete".
        from .binding import queue_binding
        queue_binding(db,child)
        if child.image.get('anchorStatus')=='queued':return
        if not binding_matches(child) or not placement_context_matches(db,child):
            prior=db.get(Task,child.image.get('bindingTaskId')) if child.image.get('bindingTaskId') else None
            unknown=prior and prior.provider_state in ('running','sent','unknown')
            from .status import rejected
            denied=rejected(prior)
            row.status='unknown' if unknown and not denied else 'failed'
            row.error_code='SCENE_VISION_REJECTED' if denied else prior.error_code if unknown else 'SCENE_VISUAL_UNCONFIRMED'
            row.image={**row.image,'pipelinePhase':'stopped'};return
    if child.image.get('anchorStatus')=='unavailable':
        from .binding import queue_binding
        queue_binding(db,child)
        if child.image.get('anchorStatus')=='queued':return
    if child.image.get('anchorStatus')!='complete':
        task=db.get(Task,child.image.get('bindingTaskId')) if child.image.get('bindingTaskId') else None
        unresolved=task and task.provider_state in ('running','sent','unknown') and task.state=='failed'
        # Correct a known, received visual failure in a new owned receipt. The
        # original paid request is never replayed, and partial art stays private.
        known_bad=(child.image.get('anchorStatus')=='incomplete'
                   or any(p.get('reason') in ('body-evidence-contradictory','passage-cue-mismatch') for p in child.image.get('detectionIssues',[]))
                    or any(p in ('unexpected-people','missing-people','reference-artifacts','projection-mismatch','space-mismatch','lighting-mismatch','pose-mismatch','identity-mismatch','unfinished-artwork','visual-fact-mismatch')
                          for p in child.image.get('composition',{}).get('issues',[])))
        # Exhausted successful measurements can leave a genuinely unmeasurable
        # doorway private forever. Permit a new local painting correction after
        # the finite evidence attempts, still requiring inspection of its new
        # pixels. An unresolved supplier call can NEVER authorize this branch.
        exhausted_measurements=(child.descriptor.get('measurementEfficiencyVersion')==1
            and child.image.get('placementEvidenceExhausted') is True
            and bool(child.descriptor.get('futureCast')) and not child.image.get('placementPlan'))
        exhausted_route_evidence=((child.image.get('bindingEvidenceRetries',0)>=2 or exhausted_measurements)
            and (bool(child.image.get('missingExitIds')) or child.descriptor.get('visualBriefVersion')==1
                 and bool(child.descriptor.get('futureCast')) and not child.image.get('placementPlan'))
            and child.image.get('composition',{}).get('verified') is True
            and not child.image.get('composition',{}).get('issues')
            and all(p.get('reason') in ('passage-evidence-unconfirmed','surface-evidence-unconfirmed','placement-evidence-unconfirmed')
                    for p in child.image.get('detectionIssues',[])))
        from .visual_brief import obstructed_threshold
        from .room_repairs import plans as room_repair_plans
        local_plans=room_repair_plans(child.descriptor,child.image)
        known_bad=known_bad or exhausted_route_evidence or obstructed_threshold(child.image) or bool(local_plans)
        attempts=child.descriptor.get('visualStageRepairs',0)
        if (not unresolved and task and task.state=='succeeded' and task.provider_state=='completed'
                and child.descriptor.get('environmentGeometryVersion',0)>=6
                and not child.descriptor.get('sceneCast') and not child.descriptor.get('environmentFinished')
                and not any(issue in child.image.get('composition',{}).get('issues',[])
                            for issue in ('projection-mismatch','reference-artifacts','space-mismatch','unexpected-people'))
                and 'unfinished-artwork' in child.image.get('composition',{}).get('issues',[])):
            # Material defects may extend across several views. Editing only a
            # failed doorway retains these defects outside its bounded ROI.
            # Finish actual camera pixels once; the final sphere still requires
            # independent image evidence before any actors or publication.
            stage(db,row,{**deepcopy(child.descriptor),'kind':'scene-environment-edit',
                'renderStage':'environment-edit','inputSceneId':child.id,
                'environmentCameraIndex':0,'remainingEditCameras':[1,2,3],'remainingRouteEdits':[],
                'environmentEdit':'finish-materials','environmentFinished':True,'routeRepairGuideVersion':0})
            return
        if (not unresolved and task and task.state=='succeeded' and task.provider_state=='completed'
                and not child.descriptor.get('sceneCast') and not child.descriptor.get('environmentCleaned')
                and not any(issue in child.image.get('composition',{}).get('issues',[])
                            for issue in ('projection-mismatch','reference-artifacts','space-mismatch','visual-fact-mismatch'))
                and 'unexpected-people' in child.image.get('composition',{}).get('issues',[])):
            # Repainting the entire sphere kept dropping correct entrances.
            # Clean its four actual camera views while preserving architecture;
            # only the final inspected sphere can become a playable image.
            cameras=cleanup_views(child.image) if row.descriptor.get('inspectionPerformanceVersion')==1 else [0,1,2,3]
            stage(db,row,{**deepcopy(child.descriptor),'kind':'scene-environment-edit',
                'renderStage':'environment-edit','inputSceneId':child.id,
                'environmentCameraIndex':cameras[0],'remainingEditCameras':cameras[1:],
                'environmentEdit':'remove-strangers','environmentCleaned':True})
            return
        if (not unresolved and known_bad and task and task.state=='succeeded' and task.provider_state=='completed' and attempts<2):
            from .visual_brief import ground_feedback
            composition=child.image.get('composition',{})
            if not composition.get('issues'):
                composition={k:v for k,v in composition.items() if k in ('verified','issues','visiblePeopleCount')}
            descriptor={**deepcopy(child.descriptor),'visualStageRepairs':attempts+1,
                'correctionReasons':{'groundChecks':ground_feedback(child.image,child.image.get('missingExitIds',[])),
                    'composition':composition,
                    'missingCharacters':child.image.get('missingCharacterIds',[]),
                    'missingExits':child.image.get('missingExitIds',[]),
                    'missingFixtures':child.image.get('missingFixtureIds',[]),
                    'detectorIssues':child.image.get('detectionIssues',[])}}
            if (not child.descriptor.get('sceneCast')
                    and 'visual-fact-mismatch' in composition.get('issues',[])):
                stage(db,row,rebuild_environment(descriptor,child.id));return
            if local_plans:
                from .room_repairs import apply_plan
                first,*remaining=local_plans
                stage(db,row,apply_plan(descriptor,first,remaining,child));return
            # Existing people cannot be repainted as part of a whole-room edit.
            # An unmeasurable old picture stays private until another saved base
            # is selected; actor repairs keep their own measured insertion mask.
            if child.descriptor.get('sceneCast') and child.descriptor.get('renderStage') in ('environment','environment-edit'):
                row.status='failed';row.error_code='SCENE_VISUAL_UNCONFIRMED';row.image={**row.image,'pipelinePhase':'stopped'};return
            plan=route_edit_plan(child.descriptor,set(child.image.get('missingExitIds',[])))
            if (not child.descriptor.get('sceneCast') and plan
                    and not any(issue in child.image.get('composition',{}).get('issues',[])
                                for issue in ('projection-mismatch','reference-artifacts','space-mismatch','lighting-mismatch'))
                    and (child.image.get('missingExitIds') or child.image.get('missingFixtureIds'))):
                first,*remaining=route_edit_steps(plan);index=first['viewIndex']
                descriptor.update(kind='scene-environment-edit',renderStage='environment-edit',inputSceneId=child.id,
                    environmentEdit='repair-passages',environmentRoutePlan={str(index):[first['route']]},
                    routeRepairGuideVersion=5,
                    protectedPassages=[deepcopy(anchor['passageEvidence']) for key,anchor in child.image.get('spatialAnchors',{}).items()
                        if key.startswith('exits:') and anchor.get('verified') is True and anchor.get('passageEvidence')
                        and key.split(':',1)[1] not in child.image.get('missingExitIds',[])],
                    environmentCameraIndex=index,remainingEditCameras=[],remainingRouteEdits=remaining)
                stage(db,row,descriptor);return
            if descriptor['renderStage']=='environment':
                descriptor['inputSceneId']=child.id
                # A perspective street cannot be made spherical by repeatedly
                # asking the editor to preserve that wrong camera. Start this
                # known-response correction from the structural sphere guide.
                descriptor['rebuildEnvironmentGuide']=(not child.descriptor.get('sceneCast') and
                    ('projection-mismatch' in child.image.get('composition',{}).get('issues',[])
                     or 'space-mismatch' in child.image.get('composition',{}).get('issues',[])
                     or 'lighting-mismatch' in child.image.get('composition',{}).get('issues',[])
                     or 'visual-fact-mismatch' in child.image.get('composition',{}).get('issues',[])
                     or bool(child.image.get('missingExitIds'))
                     or exhausted_route_evidence and bool(child.descriptor.get('futureCast')) and not child.image.get('placementPlan')
                     or any(p.get('reason')=='passage-cue-mismatch' for p in child.image.get('detectionIssues',[]))))
            elif descriptor['renderStage']=='environment-edit':
                descriptor=rebuild_environment(descriptor,child.id)
            stage(db,row,descriptor);return
        from .status import rejected
        denied=rejected(task)
        row.status='unknown' if unresolved and not denied else 'failed';row.error_code='SCENE_VISION_REJECTED' if denied else task.error_code if unresolved else 'SCENE_VISUAL_UNCONFIRMED'
        row.image={**row.image,'pipelinePhase':'stopped','visualIssues':child.image.get('detectionIssues',[]),
                   'composition':child.image.get('composition'),'missingCharacterIds':child.image.get('missingCharacterIds',[]),
                   'missingExitIds':child.image.get('missingExitIds',[])};return
    if child.descriptor['renderStage'] in ('environment','environment-edit'):
        placements=validate_placements({'placements':child.image.get('placementPlan'),
                                       'exits':child.image.get('placementPassages',[])},child.descriptor.get('futureCast',[]))
        if child.descriptor.get('futureCast') and not placements:
            row.status='failed';row.error_code='SCENE_PLACEMENT_UNCONFIRMED';return
        row.image={**row.image,'placementPlan':placements}
    painted={p['entityId'] for p in child.descriptor.get('sceneCast',[])}
    remaining=[p for p in cast if p['entityId'] not in painted]
    if not remaining:
        # Publish only an inspected final model-painted sphere. Neither blockout
        # pixels nor an uninspected private intermediate can become gameplay art.
        row.descriptor={**row.descriptor,'expectedPoses':deepcopy(child.descriptor.get('expectedPoses',{})),
                        **({'headInspectionVersion':child.descriptor['headInspectionVersion']}
                           if child.descriptor.get('headInspectionVersion') in (1,2) else {})}
        copied={k:deepcopy(v) for k,v in child.image.items() if k not in ('file','bindingTaskId','bindingConnectionRetries','placementPlan')}
        row.image={**save_image(row,image_bytes(child),{k:v for k,v in copied.items() if k not in ('sha256','bytes','providerRequestId')},child.request_id),
                   **copied,'pipelinePhase':'complete','pipelineStageId':child.id,'pipelineStep':row.image.get('pipelineStep',1)}
        row.request_id=child.request_id;row.status='ready';row.error_code=None;return
    person=remaining[0];placement=next((p for p in row.image.get('placementPlan',[]) if p['entityId']==person['entityId']),None)
    if not placement:
        row.status='failed';row.error_code='SCENE_PLACEMENT_UNCONFIRMED';return
    from .perspective import centred_actor_placement
    placement=centred_actor_placement(placement)
    if row.descriptor.get('actorMaskVersion') in (2,3): placement['maskVersion']=row.descriptor['actorMaskVersion']
    if placement.get('maskVersion')==3:
        from .perspective import actor_crop
        placement['editCropPixels']=actor_crop(placement)
        if row.descriptor.get('croppedPlacementGuideVersion')==1:placement['croppedGuideVersion']=1
        prior_task=db.get(Task,child.image.get('bindingTaskId')) if child.image.get('bindingTaskId') else None
        proof=(prior_task.checkpoint.get('verifiedImageInspection',{}) if prior_task and prior_task.owner_id==row.owner_id else {})
        old_size=proof.get('imageSize') if proof.get('imageHash')==child.image.get('sha256') else None
        placement['protectedPanoramaBoxes']=protected_actor_boxes(child.image.get('characterAnchors',{}),old_size)
    descriptor={**deepcopy(row.descriptor),'kind':'scene-actor-edit','renderStage':'actor','inputSceneId':child.id,
        'sceneCast':[*deepcopy(child.descriptor.get('sceneCast',[])),person], 'paintedActor':person,'placement':placement,
        'expectedPoses':{**child.descriptor.get('expectedPoses',{}),person['entityId']:placement['pose']},
        'futureCast':[],'referenceSceneId':None,'referenceMode':'','baseKey':base_key(row)}
    stage(db,row,descriptor)


def protected_actor_boxes(anchors,legacy_size=None):
    result=[]
    for anchor in anchors.values():
        body=anchor.get('bodyEvidence',{});box=body.get('bodyBox');size=body.get('imageSize') or legacy_size
        # Body rectangles use the bounded INSPECTION image, not the larger
        # received panorama. Dividing by output dimensions shifted the protected
        # region over another actor and erased that actor's neck/torso.
        if not isinstance(box,list) or len(box)!=4 or not isinstance(size,(list,tuple)) or len(size)!=2:continue
        if any(type(v) not in (int,float) or not isfinite(v) or v<=0 for v in size):continue
        result.append([box[0]/size[0],box[1]/size[1],box[2]/size[0],box[3]/size[1]])
    return result
