"""Painting plans only: bounded camera edits never authorize gameplay."""
from math import isfinite,pi
from .inspection import sphere_pixel,FOV


def protected_people(descriptor,image):
    from .scene_review import evaluate
    from .people_inventory import evaluate as inventory
    cast=descriptor.get('sceneCast',[]);digest=image.get('sha256');review=image.get('sceneReview',{})
    if (not isinstance(review,dict) or not digest or image.get('bindingImageHash')!=digest or review.get('imageHash')!=digest
            or review.get('version')!=1 or descriptor.get('peopleInventoryVersion')!=1):return None
    observed=evaluate(review.get('observation'),cast,descriptor)
    if not observed['verified'] or observed['issues'] or inventory(image.get('peopleInventory'),digest)!={'verified':True,'count':len(cast)}:return None
    boxes=[]
    for person in cast:
        anchor=image.get('characterAnchors',{}).get(person['entityId'],{});body=anchor.get('bodyEvidence',{})
        if not isinstance(body,dict):return None
        box=body.get('bodyBox');size=body.get('imageSize')
        if (not isinstance(body,dict) or anchor.get('verified') is not True or anchor.get('headReview',{}).get('imageHash')!=digest
                or body.get('naturalScale') is not True or not isinstance(box,list) or len(box)!=4
                or not isinstance(size,list) or len(size)!=2 or any(type(n) not in (int,float)
                    or not isfinite(n) for n in [*box,*size]) or min(size)<=0):return None
        if any(type(body.get(k)) not in (int,float) or not isfinite(body[k]) or not .85<=body[k]<=1
                for k in ('scaleConfidence','groundConfidence')):return None
        pose=descriptor.get('expectedPoses',{}).get(person['entityId']) or person.get('expectedPose')
        if pose and body.get('pose')!=pose:return None
        bounds=[box[0]/size[0],box[1]/size[1],box[2]/size[0],box[3]/size[1]]
        if not 0<bounds[2]-bounds[0]<.5 or not 0<=bounds[1]<bounds[3]<=1:return None
        boxes.append([bounds[0]-.006,max(0,bounds[1]-.006),bounds[2]+.006,min(1,bounds[3]+.012)])
    return boxes


def plans(descriptor,image):
    """Preserve measured people; edit an observed threshold and/or empty view."""
    from .visual_brief import ground_feedback
    protected=protected_people(descriptor,image)
    if protected is None:return []
    digest=image['sha256'];result=[];missing=image.get('missingExitIds',[])
    if not isinstance(missing,list):return []
    notes=image.get('pointCorrectionNotes',[])
    options=[]
    for note in notes if isinstance(notes,list) else []:
        if not isinstance(note,dict) or note.get('id') not in missing:continue
        observed=note.get('actualPointObservations',{});box=note.get('apertureBoxPixels');view=note.get('viewIndex')
        if (not isinstance(observed,dict) or any(observed.get(k) is not True for k in
                ('openingConfirmed','crossedThreshold','sameOpening')) or type(view) is not int or not 0<=view<8
                or not isinstance(box,list) or len(box)!=4 or any(type(n) not in (int,float)
                    or not isfinite(n) or not 0<=n<=512 for n in box) or not box[0]<box[2] or not box[1]<box[3]):continue
        hints=ground_feedback({**image,'pointCorrectionNotes':[note]},missing)
        if not any(p['reportedCovering'] for h in hints for p in h['points']):continue
        points=[note.get(k) for k in ('nearPixels','farPixels','throughPointPixels')]
        if any(not isinstance(p,list) or len(p)!=2 or any(type(n) not in (int,float)
                or not isfinite(n) or not 0<=n<=512 for n in p) for p in points):continue
        bounds=[max(8,box[0]-16),max(8,box[1]-12),min(504,box[2]+16),min(504,max(box[3],*[p[1] for p in points])+32)]
        if (bounds[2]-bounds[0])*(bounds[3]-bounds[1])>512*512*.65:continue
        opening=sphere_pixel(view,(box[0]+box[2])/2,(box[1]+box[3])/2,2048,1024)[0]/2048%1
        target=next((r for r in descriptor.get('exitRoutes',[]) if r['id']==note['id']),{})
        plan={'version':1,'kind':'threshold','sourceHash':digest,'viewIndex':view,'boxes':[bounds],
              'protectedPanoramaBoxes':protected,'routeId':note['id'],
              'destination':target.get('name') or target.get('visualCue') or note['id'],'openingLongitude':opening}
        options.append((view>=4,plan))
    if options:result.append(max(options,key=lambda p:p[0])[1])
    if (image.get('placementEvidenceExhausted') is True and descriptor.get('futureCast')
            and not image.get('placementPlan') and (not missing or result)):
        occupied=[((b[0]+b[2])/2%1,(b[2]-b[0])/2) for b in protected]
        for anchor in image.get('spatialAnchors',{}).values():
            if anchor.get('kind')=='exits' and anchor.get('verified') is True:
                yaw=anchor.get('yaw')
                if type(yaw) in (int,float) and isfinite(yaw):occupied.append(((.5+yaw/(2*pi))%1,.06))
        occupied.extend((p['openingLongitude'],.06) for p in result)
        used=descriptor.get('clearedSpaceCameras',[]);candidates=[]
        if not isinstance(used,list):return result
        for index,centre in enumerate((.5,.75,0,.25)):
            if index in used:continue
            clearance=min((abs((centre-c+.5)%1-.5)-radius for c,radius in occupied),default=.5)
            if clearance>FOV/720+.015:candidates.append((clearance,-index))
        if candidates:
            index=-max(candidates)[1]
            result.append({'version':1,'kind':'space','sourceHash':digest,'viewIndex':index,'boxes':[[8,8,504,504]],
                'protectedPanoramaBoxes':protected,'seats':sum(p.get('expectedPose')=='seated' for p in descriptor['futureCast'])})
    return result


def apply_plan(descriptor,plan,remaining,source):
    from copy import deepcopy
    value={**deepcopy(descriptor),'kind':'scene-environment-edit','renderStage':'environment-edit',
        'environmentEdit':'clear-floor','environmentCameraIndex':plan['viewIndex'],'environmentCameraPitchVersion':1,
        'routeRepairGuideVersion':1,'floorRepairPlan':{**deepcopy(plan),'sourceHash':source.image['sha256']},
        'remainingRoomEdits':deepcopy(remaining),'inputSceneId':source.id,'remainingEditCameras':[],'remainingRouteEdits':[]}
    if plan['kind']=='space':value['clearedSpaceCameras']=[*descriptor.get('clearedSpaceCameras',[]),plan['viewIndex']]
    value['protectedPassages']=[a['passageEvidence'] for a in source.image.get('spatialAnchors',{}).values()
        if a.get('kind')=='exits' and a.get('verified') is True and isinstance(a.get('passageEvidence'),dict)
        and a['passageEvidence'].get('surfaceReview',{}).get('imageHash')==source.image['sha256']]
    return value
