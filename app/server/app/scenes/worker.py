"""One durable image request per reservation; ambiguous responses never retry."""
import threading
import logging
from sqlalchemy import select, update, case
from ..db import SessionLocal
from ..config import settings
from ..errors import Problem
from ..storage import private_path
from ..world.models import World, WorldLine
from .models import SceneImage, SceneProfile
from .service import save_image, image_bytes, saved_receipt
from .provider import generate
from .looks import context_active, adopt_first

stop=threading.Event()
thread=None
leader=None
execution_lock=threading.Lock()
executions={}

def joint_scene_reference(db,row,descriptor):
    """Explicit local edit input, never evidence of an accepted output."""
    world=db.get(World,row.world_id)
    source=db.get(SceneImage,descriptor.get('referenceSceneId'))
    if (not world or world.owner_id!=row.owner_id or world.source.get('local360Preview') is not True
            or descriptor.get('jointSceneReferenceVersion')!=1 or descriptor.get('renderPipeline')!=0
            or descriptor.get('renderStage') or descriptor.get('kind')=='character'
            or not source or source.owner_id!=row.owner_id or source.world_id!=row.world_id
            or source.line_id!=row.line_id or source.checkpoint_id!=row.checkpoint_id
            or source.profile_id!=row.profile_id or source.profile_version!=row.profile_version
            or source.status!='ready' or not source.image.get('file')
            or not source.image.get('sha256') or source.image['sha256']!=descriptor.get('jointReferenceHash')
            or source.descriptor.get('placeId')!=descriptor.get('placeId')):
        raise Problem('SCENE_REFERENCE_UNAVAILABLE','联合重绘的底图与当前场景不一致，尚未发送请求',409)
    # image_bytes below verifies the actual input. An inconclusive background
    # is only edit material; the new full panorama keeps every output review.
    return source


def pipeline_context_active(db,row,line):
    """Do not dispatch or inspect an obsolete private image stage."""
    if not row.descriptor.get('pipelineParentId'):return True
    from .pipeline import owned_stage,narrative_context_matches
    from .cache import compatible
    from .service import scene_view
    from ..world.models import WorldCheckpoint
    parent=db.get(SceneImage,row.descriptor['pipelineParentId'])
    current=db.get(WorldCheckpoint,line.head_checkpoint_id) if line else None
    return bool(parent and owned_stage(db,parent,row.id) is not None and parent.status=='queued'
                and parent.image.get('pipelineStageId')==row.id
                and current and compatible(parent,scene_view(current,line.id))
                and narrative_context_matches(db,parent,line))

def inspect_finished_stage(db,row):
    if row.descriptor.get('kind')=='character':return
    if row.descriptor.get('renderStage')=='environment-edit' and (row.descriptor.get('remainingEditCameras') or row.descriptor.get('remainingRouteEdits') or row.descriptor.get('remainingRoomEdits')):
        row.image={**row.image,'anchorStatus':'planned-edit'}
        return
    from .binding import queue_binding
    queue_binding(db,row)

def authorize_dispatch(ident):
    with SessionLocal() as db:
        row=db.get(SceneImage,ident)
        profile=db.get(SceneProfile,row.profile_id) if row else None
        world=db.get(World,row.world_id) if row else None
        line=db.get(WorldLine,row.line_id) if row and row.line_id else None
        from ..models import User
        owner=db.get(User,row.owner_id) if row else None
        if not owner or owner.status!='active':
            raise Problem('SCENE_CONTEXT_CHANGED','这次出图已停止',409)
        if not row or row.status!='running' or not profile or profile.status!='active' or profile.version!=row.profile_version or not context_active(row,world,line):
            raise Problem('SCENE_CONTEXT_CHANGED','这次出图已停止',409)
        if not pipeline_context_active(db,row,line):
            raise Problem('SCENE_CONTEXT_CHANGED','这次出图已停止',409)

def process_one():
    # Only this process owns the DB advisory leader lock. A receipt captured
    # mid-request is durable, but is not a crashed operation to recover while
    # its original executor is still encoding/projecting it.
    token=object()
    try:return _process_one(token)
    finally:
        with execution_lock:
            for ident in [ident for ident,owner in executions.items() if owner is token]:executions.pop(ident)


def _process_one(token):
    with SessionLocal() as db:
        from .pipeline import candidate as next_candidate,is_parent,advance
        with execution_lock:excluded=tuple(executions)
        candidate=next_candidate(db,excluded=excluded)
        if not candidate:return False
        candidate_id,world_id,owner_id=candidate.id,candidate.world_id,candidate.owner_id
        world=db.scalar(select(World).where(World.id==world_id,World.owner_id==owner_id).with_for_update())
        # Another executor may have advanced this same parent while this one
        # waited for the world lock. PostgreSQL's row lock alone does not refresh
        # SQLAlchemy's identity map. Discard all speculative queue observations
        # before deciding whether to create a child or dispatch a paid request.
        db.expire_all()
        row=db.scalar(select(SceneImage).where(SceneImage.id==candidate_id,SceneImage.status.in_(['queued','saving'])).with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if not row:return False
        with execution_lock:
            if row.id in executions:return False
            executions[row.id]=token
        profile=db.get(SceneProfile,row.profile_id); world=db.get(World,row.world_id);line=db.get(WorldLine,row.line_id) if row.line_id else None
        if row.status=='saving':
            try:
                try:metadata,picture=saved_receipt(row)
                except Exception:
                    if row.descriptor.get('renderStage') not in ('actor','environment','environment-edit'):raise
                    from .characters import source_bytes
                    if row.descriptor['renderStage'] in ('actor','environment-edit'):
                        from .perspective import encode_camera
                        picture,metadata=encode_camera(source_bytes(row))
                    else:
                        from .provider import encode_panorama
                        picture,metadata=encode_panorama(source_bytes(row),reference_yaw=row.image.get('sourceReferenceYaw',0))
                    metadata={**metadata,'providerRequestId':row.image.get('sourceRequestId')}
                if row.descriptor.get('renderStage') in ('actor','environment-edit') and metadata.get('projection')=='perspective-edit':
                    from .perspective import reproject_camera
                    source=db.get(SceneImage,row.descriptor['inputSceneId'])
                    if not source or source.owner_id!=row.owner_id or source.world_id!=row.world_id:raise ValueError('Invalid source receipt')
                    if row.descriptor.get('environmentEdit')=='clear-floor' and row.descriptor['floorRepairPlan'].get('sourceHash')!=source.image.get('sha256'):raise ValueError('Floor source changed')
                    placement=row.descriptor.get('placement') if row.descriptor['renderStage']=='actor' else None
                    index=placement['viewIndex'] if placement else row.descriptor['environmentCameraIndex']
                    from .perspective import route_repair_boxes,protected_route_boxes,edit_camera_yaw
                    boxes=route_repair_boxes(row.descriptor,index) if row.descriptor.get('routeRepairGuideVersion',0)>=1 else None
                    protected=protected_route_boxes(row.descriptor,index) if boxes is not None or placement and placement.get('surroundingsVersion')==1 else None
                    picture,metadata=reproject_camera(image_bytes(source),picture,index,placement,repair_boxes=boxes,protected_boxes=protected,camera_yaw=edit_camera_yaw(row.descriptor,index),allow_ground=row.descriptor.get('environmentCameraPitchVersion')==1,protected_panorama_boxes=row.descriptor.get('floorRepairPlan',{}).get('protectedPanoramaBoxes',[]))
                    metadata=save_image(row,picture,metadata,row.request_id or metadata.get('providerRequestId'))
            except Exception:
                attempts=row.image.get('projectionRecoveryAttempts',0)+1
                row.image={**row.image,'projectionRecoveryAttempts':attempts}
                if attempts>=2:row.status='failed';row.error_code='SCENE_RECEIPT_INVALID'
                db.commit();return True
            row.image=metadata;row.request_id=metadata.get('providerRequestId')
            from ..foundation.wallet import settle_scene
            settle_scene(db,row,paid=True)
            row.status='ready' if context_active(row,world,line) and pipeline_context_active(db,row,line) else 'failed'
            row.error_code=None if row.status=='ready' else 'SCENE_CONTEXT_CHANGED'
            if row.status=='ready':inspect_finished_stage(db,row)
            if row.status=='ready':adopt_first(db,row)
            db.commit()
            return True
        if not profile or profile.status!='active' or profile.version!=row.profile_version or not context_active(row,world,line) or not pipeline_context_active(db,row,line):
            row.status='failed';row.error_code='SCENE_CONTEXT_CHANGED';db.commit();return True
        if is_parent(row):
            advance(db,row,world,line);db.commit();return True
        row.status='running'; ident=row.id;owner=row.owner_id;world_id=row.world_id; descriptor=row.descriptor; cipher=profile.key_cipher
        # Reference only an owned, previously saved panorama of this location.
        if descriptor.get('jointSceneReferenceVersion')==1:
            try:reference_row=joint_scene_reference(db,row,descriptor)
            except Problem as error:
                # Validation happens before reservation/HTTP. Do not keep
                # reclaiming an invalid queued correction forever.
                row.status='failed';row.error_code=error.code
                row.image={**row.image,'failureDiagnostic':{'requestSent':False,'responseReceived':False}}
                db.commit();return True
        elif descriptor.get('kind')=='character':
            # This provider interprets image references as an edit, copying the
            # scenery into the sprite. Pass scene lighting as text instead.
            reference_row=None
        elif descriptor.get('renderStage')=='environment':
            reference_row=db.get(SceneImage,descriptor.get('inputSceneId')) if descriptor.get('visualStageRepairs') and not descriptor.get('rebuildEnvironmentGuide') else None
            if reference_row and (reference_row.owner_id!=owner or reference_row.world_id!=world_id
                    or reference_row.line_id!=row.line_id or reference_row.status!='ready'
                    or reference_row.descriptor.get('pipelineParentId')!=descriptor.get('pipelineParentId')):
                row.status='failed';row.error_code='SCENE_REFERENCE_UNAVAILABLE';db.commit();return True
        elif descriptor.get('renderStage') in ('actor','environment-edit'):
            reference_row=db.get(SceneImage,descriptor.get('inputSceneId'))
            if (not reference_row or reference_row.owner_id!=owner or reference_row.world_id!=world_id
                    or reference_row.line_id!=row.line_id or reference_row.status!='ready'
                    or reference_row.descriptor.get('pipelineParentId')!=descriptor.get('pipelineParentId')
                    or (descriptor['renderStage']=='actor' and reference_row.image.get('anchorStatus')!='complete')):
                row.status='failed';row.error_code='SCENE_REFERENCE_UNAVAILABLE';db.commit();return True
        else:
            from .cache import usable_reference
            reference_row=db.scalar(select(SceneImage).where(SceneImage.id==descriptor.get('referenceSceneId'),
                SceneImage.owner_id==owner,SceneImage.world_id==world_id,
                SceneImage.profile_version==row.profile_version,SceneImage.status=='ready',
                SceneImage.descriptor['placeId'].astext==descriptor['placeId'])) if descriptor.get('referenceSceneId') else None
            if reference_row and not usable_reference(reference_row):reference_row=None
            if not reference_row:
                references=db.scalars(select(SceneImage).where(SceneImage.owner_id==owner,SceneImage.world_id==world_id,SceneImage.profile_version==row.profile_version,SceneImage.status=='ready',SceneImage.descriptor['placeId'].astext==descriptor['placeId'],SceneImage.descriptor['layoutVersion'].astext==str(descriptor.get('layoutVersion',0))).order_by(case((SceneImage.descriptor['pointId'].astext=='entry',0),else_=1),SceneImage.created_at).limit(100))
                reference_row=next((candidate for candidate in references if usable_reference(candidate)),None)
        reference=None
        if (reference_row and descriptor.get('jointSceneReferenceVersion')!=1
                and descriptor.get('renderStage') not in ('actor','environment','environment-edit')
                and not usable_reference(reference_row)):reference_row=None
        if reference_row:
            try:reference=image_bytes(reference_row)
            except Problem:pass
        if descriptor.get('environmentEdit')=='clear-floor' and (not reference_row
                or descriptor.get('floorRepairPlan',{}).get('sourceHash')!=reference_row.image.get('sha256')):
            row.status='failed';row.error_code='SCENE_REFERENCE_UNAVAILABLE';db.commit();return True
        sphere_source=None;architecture_reference=None
        if descriptor.get('renderStage') in ('actor','environment-edit'):
            if not reference:
                row.status='failed';row.error_code='SCENE_REFERENCE_UNAVAILABLE';db.commit();return True
            from .perspective import camera_reference,route_repair_reference,route_repair_boxes,protected_route_boxes,edit_camera_yaw
            sphere_source=reference
            placement=descriptor.get('placement') if descriptor['renderStage']=='actor' else None
            index=placement['viewIndex'] if placement else descriptor['environmentCameraIndex']
            heading=edit_camera_yaw(descriptor,index)
            reference=camera_reference(reference,index,placement,camera_yaw=heading,allow_ground=descriptor.get('environmentCameraPitchVersion')==1)
            if placement:descriptor={**descriptor,'usesPlacementGuide':(placement.get('maskVersion')!=3 or placement.get('croppedGuideVersion')==1) and placement['pose'] in ('standing','seated','kneeling')}
            elif descriptor.get('environmentEdit')=='repair-passages':
                if descriptor.get('routeRepairGuideVersion',0)>=5:
                    # Editing the actual failed opening avoids two competing
                    # image geometries and copying private guide blocks into
                    # the finished art. The existing ROI/protection masks still
                    # constrain publication; actual new pixels require proof.
                    pass
                else:
                    from .guide import scene_guide
                    import io
                    buffer=io.BytesIO();scene_guide(descriptor).save(buffer,format='PNG')
                    structural=camera_reference(buffer.getvalue(),index,camera_yaw=heading)
                if 1<=descriptor.get('routeRepairGuideVersion',0)<5:
                    # The surrounding pixels already carry the original style.
                    # A second unpatched copy invites the image editor to copy
                    # the blocked opening back over the new private geometry.
                    reference=route_repair_reference(reference,structural,route_repair_boxes(descriptor,index),protected_route_boxes(descriptor,index))
                    if descriptor.get('routeRepairGuideVersion',0)>=4:
                        # The patched guide has geometry but no material or
                        # brushwork. Keep the ORIGINAL camera as a separate
                        # style reference; never mistake guide colours for the
                        # finished architecture to preserve.
                        architecture_reference=camera_reference(sphere_source,index,camera_yaw=heading)
                elif descriptor.get('routeRepairGuideVersion',0)<1:architecture_reference=structural
        if descriptor.get('resetComposition'):reference=None
        if descriptor.get('visualRepairAttempt') and reference_row and reference_row.image.get('characterAnchors'):
            from .binding import anchors_from_result
            cast_ids={p['entityId'] for p in descriptor.get('sceneCast',[])}
            raw=[{'entityId':ident,'visible':True,'confidence':1,'headBox':anchor.get('headBox')} for ident,anchor in reference_row.image['characterAnchors'].items() if ident in cast_ids]
            if len(anchors_from_result({'characters':raw},descriptor.get('sceneCast',[])))<len(raw):reference=None
        character_references=[];reference_order=[]
        if descriptor.get('kind')!='character':
            requested_looks=([descriptor['paintedActor']] if descriptor.get('renderStage')=='actor'
                             else [] if descriptor.get('renderStage') in ('environment','environment-edit') else descriptor.get('sceneCast',[]))
            for person in requested_looks:
                look_id=person.get('lookImageId') or descriptor.get('characterLooks',{}).get(person['entityId'])
                from .models import CharacterArtwork
                look=(db.get(CharacterArtwork,look_id) or db.get(SceneImage,look_id)) if look_id else None
                if look and look.owner_id==owner and look.world_id==world_id and look.status=='ready' and look.descriptor.get('kind')=='character' and look.descriptor.get('entityId')==person['entityId']:
                    person['appearance']=look.descriptor.get('appearance',person.get('appearance',''))
                    try:picture=image_bytes(look)
                    except Problem:continue  # Retain appearance text if an old reference file is unavailable.
                    character_references.append(picture)
                    # Image 1 is always the panorama or physical scale guide.
                    reference_order.append({'image':len(character_references)+1,'entityId':person['entityId'],'name':person['name']})
            descriptor={**descriptor,'characterReferenceOrder':reference_order,'hasPanoramaReference':bool(reference)}
        row.descriptor=descriptor
        db.commit()
    saved=None
    try:
        with SessionLocal() as billing_db:
            from ..foundation.wallet import reserve_scene
            billing_row=billing_db.get(SceneImage,ident)
            if billing_row:reserve_scene(billing_db,billing_row)
            billing_db.commit()
        def capture_source(data,request_id=None,reference_yaw=0):
            from .characters import save_source
            with SessionLocal() as db:
                db.execute(select(World).where(World.id==world_id,World.owner_id==owner).with_for_update())
                current=db.get(SceneImage,ident)
                if current:
                    save_source(current,data)
                    from ..foundation.wallet import settle_scene
                    settle_scene(db,current,paid=True)
                    if current.descriptor.get('renderStage') in ('actor','environment','environment-edit'):
                        current.image={**current.image,'sourceReceived':True,'sourceRequestId':request_id,'sourceReferenceYaw':reference_yaw}
                        current.request_id=request_id;current.status='saving'
                    db.commit()
        options={'capture':capture_source} if descriptor.get('kind')=='character' or descriptor.get('renderStage') in ('actor','environment','environment-edit') else {}
        if character_references:options['character_references']=character_references
        if architecture_reference:options['architecture_reference']=architecture_reference
        data,metadata,request_id=generate(descriptor,cipher,reference,authorize=lambda:authorize_dispatch(ident),**options)
        with SessionLocal() as billing_db:
            from ..foundation.wallet import settle_scene
            billing_row=billing_db.get(SceneImage,ident)
            if billing_row:settle_scene(billing_db,billing_row,paid=True)
            billing_db.commit()
        if descriptor.get('renderStage') in ('actor','environment-edit'):
            # Journal the received paid camera edit before local projection.
            # A restart resumes projection from these bytes, never the POST.
            with SessionLocal() as db:
                row=db.get(SceneImage,ident)
                row.image=save_image(row,data,metadata,request_id);row.request_id=request_id;row.status='saving';db.commit()
            from .perspective import reproject_camera
            placement=descriptor.get('placement') if descriptor['renderStage']=='actor' else None
            index=placement['viewIndex'] if placement else descriptor['environmentCameraIndex']
            from .perspective import route_repair_boxes,protected_route_boxes,edit_camera_yaw
            boxes=route_repair_boxes(descriptor,index) if descriptor.get('routeRepairGuideVersion',0)>=1 else None
            protected=protected_route_boxes(descriptor,index) if boxes is not None or placement and placement.get('surroundingsVersion')==1 else None
            data,metadata=reproject_camera(sphere_source,data,index,placement,repair_boxes=boxes,protected_boxes=protected,camera_yaw=edit_camera_yaw(descriptor,index),allow_ground=descriptor.get('environmentCameraPitchVersion')==1,protected_panorama_boxes=descriptor.get('floorRepairPlan',{}).get('protectedPanoramaBoxes',[]))
        with SessionLocal() as db:
            # The world lock linearizes publication against deletion/archiving.
            world=db.scalar(select(World).where(World.id==world_id,World.owner_id==owner).with_for_update())
            row=db.get(SceneImage,ident);line=db.get(WorldLine,row.line_id) if row and row.line_id else None
            if not row:return True
            if not context_active(row,world,line) or not pipeline_context_active(db,row,line):
                row.status='failed';row.error_code='SCENE_CONTEXT_CHANGED';db.commit();return True
            saved=save_image(row,data,metadata,request_id);row.image=saved;row.status='ready';row.request_id=request_id
            inspect_finished_stage(db,row)
            adopt_first(db,row)
            db.commit()
            if descriptor.get('kind')=='character':
                from .characters import source_name
                private_path(owner,source_name(row)).unlink(missing_ok=True)
    except Exception as error:
        code=error.code if isinstance(error,Problem) else 'SCENE_STATUS_UNKNOWN'
        with SessionLocal() as db:
            row=db.get(SceneImage,ident)
            if row:
                from ..foundation.wallet import settle_scene
                details=error.details if isinstance(error,Problem) and isinstance(error.details,dict) else {}
                settle_scene(db,row,not_sent=details.get('requestSent') is False or code in ('SCENE_CONTEXT_CHANGED','SCENE_HOST_DISABLED','INSUFFICIENT_CREDITS'))
            if row and row.status=='running':
                details=error.details if isinstance(error,Problem) and isinstance(error.details,dict) else {}
                row.image={**row.image,'failureDiagnostic':{k:details[k] for k in ('reason','requestSent','responseReceived') if k in details}}
                try:saved,_=saved_receipt(row)
                except Exception:saved=None
                row.status='saving' if saved else 'unknown' if code=='SCENE_STATUS_UNKNOWN' else 'failed';row.error_code='SCENE_SAVE_PENDING' if saved else code;db.commit()
        logging.getLogger('reader').warning('scene=%s result=%s',ident,code)
    finally:cipher=None;reference=None
    return True

def loop():
    from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
    # Each request retains the existing row claim, world lock and dispatch
    # check. One slow supplier image need not block every other world.
    capacity=max(1,min(4,settings.scene_image_concurrency))
    with ThreadPoolExecutor(max_workers=capacity) as pool:
        pending=set()
        while not stop.is_set():
            while len(pending)<capacity and not stop.is_set():pending.add(pool.submit(process_one))
            done,pending=wait(pending,timeout=.5,return_when=FIRST_COMPLETED)
            idle=True
            for future in done:
                try:idle=not future.result() and idle
                except Exception:logging.getLogger('reader').warning('scene queue temporarily unavailable')
            if done and idle:stop.wait(.5)

def start():
    global thread,leader
    if not settings.interactive_world_enabled:return
    from .migrate import migrate
    migrate()
    if settings.ai_mode=='mock':return
    from ..db import engine
    from sqlalchemy import text
    leader=engine.connect()
    if not leader.scalar(text('SELECT pg_try_advisory_lock(82370220)')):
        leader.close();leader=None;return
    leader.commit()
    with SessionLocal() as db:
        for row in db.scalars(select(SceneImage).where(SceneImage.status=='running').with_for_update()):
            try:saved_receipt(row);row.status='saving';row.error_code='SCENE_SAVE_PENDING'
            except Exception:
                try:
                    if row.descriptor.get('renderStage') not in ('actor','environment','environment-edit'):raise ValueError('No stage receipt')
                    from .characters import source_bytes
                    source_bytes(row);row.status='saving';row.error_code='SCENE_SAVE_PENDING'
                except Exception:row.status='unknown';row.error_code='SCENE_STATUS_UNKNOWN'
        db.commit()
    stop.clear();thread=threading.Thread(target=loop,name='scene-image-worker',daemon=True);thread.start()

def shutdown():
    stop.set()
    if thread:thread.join(timeout=1)
    if leader and (not thread or not thread.is_alive()):leader.close()
