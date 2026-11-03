"""Reuse measured parts only when every involved actual camera is identical."""
from copy import deepcopy
from hashlib import sha256


def received_parts(db,row,targets,cameras):
    from ..models import Task
    from ..world.models import WorldCheckpoint
    from .models import SceneImage
    from .service import image_bytes
    from .binding import (inspection_scene,inspection_context,inspection_image,inspection_cameras,
                          evaluate_inspection,placement_narrative)
    empty={'characters':[],'exits':[]}
    if row.descriptor.get('renderStage') not in ('actor','environment-edit'):return empty
    source=db.get(SceneImage,row.descriptor.get('inputSceneId'))
    if not source or not row.descriptor.get('pipelineParentId'):return empty
    if (any(getattr(source,k)!=getattr(row,k) for k in ('owner_id','world_id','line_id','checkpoint_id','profile_id','profile_version'))
            or source.status!='ready' or source.descriptor.get('pipelineParentId')!=row.descriptor['pipelineParentId']
            or source.image.get('anchorStatus')!='complete' or source.image.get('bindingImageHash')!=source.image.get('sha256')):return empty
    previous=inspection_scene(source);current=inspection_scene(row)
    before={k:v for k,v in previous.items() if k!='sceneImageHash'}
    after={k:v for k,v in current.items() if k!='sceneImageHash'}
    same_people_contract=before==after
    # Adding the first actor activates its head/identity contract. That cannot
    # invalidate an independently measured doorway in identical actual pixels.
    # People still require their complete original contract; exits retain all
    # global/fact/lighting/route/surface checks and BOTH camera resolutions.
    people_keys={'headInspectionVersion','bodyInspectionVersion','identityInspectionVersion',
        'personInspectionVersion','headCameraSearchVersion','headCropPixelVersion'}
    if {k:v for k,v in before.items() if k not in people_keys}!={k:v for k,v in after.items() if k not in people_keys}:return empty
    task=db.get(Task,source.image.get('bindingTaskId'))
    if (not task or task.owner_id!=row.owner_id or task.state!='succeeded' or task.provider_state!='completed'
            or task.cancel_requested or task.payload.get('sceneId')!=source.id
            or task.payload.get('worldId')!=row.world_id or task.payload.get('lineId')!=row.line_id):return empty
    cp=db.get(WorldCheckpoint,row.checkpoint_id)
    if not cp or cp.owner_id!=row.owner_id or cp.world_id!=row.world_id:return empty
    saved=task.checkpoint.get('verifiedImageInspection',{})
    if (saved.get('sceneId')!=source.id or saved.get('imageHash')!=source.image['sha256']
            or saved.get('context')!=inspection_context(source,targets,narrative=placement_narrative(cp))):return empty
    original=saved.get('result');size=saved.get('imageSize')
    if (not isinstance(original,dict) or any(not isinstance(original.get(k),list) for k in ('characters','exits'))
            or not isinstance(size,list) or len(size)!=2):return empty
    measured=evaluate_inspection(original,source.descriptor.get('sceneCast',[]),targets,size,previous,
        future_cast=source.descriptor.get('futureCast',[]),expected_poses=source.descriptor.get('expectedPoses',{}))
    if measured['anchorStatus']!='complete':return empty
    source_pixels=image_bytes(source)
    raw,actual_size=inspection_image(source_pixels)
    if list(actual_size)!=size:return empty
    old=inspection_cameras(source,raw)
    equal={i:sha256(a).hexdigest() for i,(a,b) in enumerate(zip(old,cameras)) if a==b}
    if not equal:return empty
    # Ground/support detail requests sample 1024 pixels directly from the
    # original texture. Equality of a compressed 512 camera alone cannot
    # authorize those finer observations. Check BOTH evidence resolutions.
    from .inspection import detail_camera
    target_pixels=image_bytes(row);native={}
    def native_equal(index):
        if index not in native:
            a=detail_camera(source_pixels,index);b=detail_camera(target_pixels,index)
            native[index]=sha256(a).hexdigest() if a==b else None
        return native[index] is not None
    lineage={'version':1,'sourceSceneId':source.id,'sourceTaskId':task.id,
        'sourceImageHash':source.image['sha256'],'targetImageHash':row.image['sha256']}
    allowed={p['entityId']:p for p in row.descriptor.get('sceneCast',[])}
    prior={p['entityId']:p for p in source.descriptor.get('sceneCast',[])}
    def views(part):
        result=[part.get('viewIndex')]
        for key in ('bodyViewIndex','groundViewIndex'):
            if key in part:result.append(part[key])
        for key in ('headReview','bodyReview','scaleEvidence','supportingFixture','passageEvidence','fixtureReview'):
            item=part.get(key)
            if isinstance(item,dict):result.append(item.get('viewIndex'))
        return set(result)
    kinds=('characters','exits','fixtures') if row.descriptor.get('inspectionPerformanceVersion')==1 else ('characters','exits')
    if 'fixtures' in kinds:empty['fixtures']=[]
    for kind in kinds:
        for part in original.get(kind,[]) if isinstance(original.get(kind),list) else []:
            if not isinstance(part,dict) or part.get('visible') is not True:continue
            ident=part.get('entityId') if kind=='characters' else part.get('id')
            if kind=='characters':
                if not same_people_contract:continue
                if ident not in measured['characterAnchors'] or ident not in allowed or ident not in prior:continue
                if any(allowed[ident].get(k)!=prior[ident].get(k) for k in ('name','description','appearance','lookImageId','presentation')):continue
                pose=row.descriptor.get('expectedPoses',{}).get(ident) or allowed[ident].get('expectedPose')
                if pose and part.get('pose')!=pose:continue
            elif kind+':'+str(ident) not in measured['spatialAnchors']:continue
            involved=views(part)
            if any(type(i) is not int or i not in equal for i in involved):continue
            if not all(native_equal(i) for i in involved):continue
            copied=deepcopy(part);proof={**lineage,'cameraSha256':{str(i):equal[i] for i in sorted(involved)},
                'nativeCameraSha256':{str(i):native[i] for i in sorted(involved)},'originalMeasurement':deepcopy(part)}
            if kind=='characters':
                from .heads import proof_valid
                if not proof_valid(copied.get('headReview'),source.image['sha256']):continue
                copied['headReview']={**copied['headReview'],'imageHash':row.image['sha256'],'cameraContinuity':proof}
                if isinstance(copied.get('bodyReview'),dict):copied['bodyReview']['imageHash']=row.image['sha256']
            elif kind=='exits':
                surface=copied.get('passageEvidence',{}).get('surfaceReview')
                if not isinstance(surface,dict) or surface.get('imageHash')!=source.image['sha256']:continue
                if surface.get('viewIndex') not in involved:continue
                surface.update(imageHash=row.image['sha256'],cameraContinuity=proof)
            else:
                prior_review=copied.get('fixtureReview')
                copied['fixtureReview']={**(prior_review if isinstance(prior_review,dict) else {}),'version':1,'viewIndex':copied['viewIndex'],
                    'imageHash':row.image['sha256'],'cameraContinuity':proof}
            empty[kind].append(copied)
    return empty


def merge(parsed,received):
    result=deepcopy(parsed)
    for kind,key in (('characters','entityId'),('exits','id'),('fixtures','id')):
        if kind not in received:continue
        replaced={p[key] for p in received[kind]}
        parts=result.get(kind,[])
        result[kind]=[p for p in parts if isinstance(p,dict) and p.get(key) not in replaced] if isinstance(parts,list) else []
        result[kind]+=deepcopy(received[kind])
    return result
