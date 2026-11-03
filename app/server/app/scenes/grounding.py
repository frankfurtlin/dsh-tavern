from ..model_budget import image_budget_exceeded
"""Image evidence for spatial markers; generation coordinates are never proof."""
from copy import deepcopy
from math import isfinite, pi
from ..model_budget import call_budget_exceeded, input_budget_exceeded, output_budget_exceeded

VERSION = 28


def binding_matches(row):
    """Pixel evidence is meaningful only for the exact received image."""
    image = row.image
    from .binding import artwork_confirmed
    # One unconfirmed person's head cannot invalidate separately measured
    # walking floors. Each character checks its own proof at presentation.
    return (row.status == 'ready' and image.get('anchorVersion') == VERSION
            and isinstance(image.get('sha256'), str) and bool(image['sha256'])
            and image.get('bindingImageHash') == image['sha256']
            and (image.get('anchorStatus')!='complete' or artwork_confirmed(row)))


def inspection_targets(view,*,independent_interior=False):
    fixtures = [{'id': p['id'], 'name': p['name'], 'fixture': p.get('fixture'),
                 'evidence': p.get('source', {}).get('quote', '')[:240]}
                for p in view.get('points', []) if p['id'] != view.get('pointId')]
    # The current viewpoint is not a destination, but its doorway remains the
    # physical portal for an exit. Never lose its evidence by filtering points.
    portals={p['id']:p for p in view.get('points',[])}
    cues={e['id']:e['visualCue'] for e in view.get('descriptor',{}).get('exitRoutes',[]) if e.get('visualCue')}
    exits=[]
    for p in view.get('exits',[]):
        portal=portals.get(p.get('portalId'),{})
        cue=cues.get(p['id'],'')
        indoor=independent_interior and view.get('descriptor',{}).get('spaceKind')=='interior'
        if indoor and not cue:
            cue='An actually OPEN doorway or architectural opening toward '+p['name']+', with actual walking floor continuing through its threshold. A closed door, wall or furniture is not this passage.'
        from .passages import cue_requirements
        exits.append({'id':p['id'],'name':p['name'],'portalId':p.get('portalId'),
            'fixture':portal.get('fixture'),'portalName':portal.get('name'),
            'evidence':portal.get('source',{}).get('quote','')[:240],
            **({'visualCue':cue,'allowedPassageKinds':(['door-open','stairs-open'] if indoor else ['door-open'] if 'tavern' in cue.lower() else ['door-open','stairs-open'] if 'residential interior entrance hall' in cue.lower() else ['door-open','path-open','stairs-open']),
                'requiredCueFacts':cue_requirements(cue),'requiredGroundEvidence':True,'requiredSurfaceReview':True,
                'independentReviewRequired':True,'requiredReviewSource':'single-camera-measurement'} if cue else {})})
    return {'fixtures': fixtures, 'exits': exits}


def spatial_anchors_from_result(result, targets, image_size, *, require_evidence=False):
    anchors = {}
    route_regions=[]
    if not isinstance(result, dict):return anchors
    width, height = image_size
    from .passages import shared_aperture
    for kind in ('fixtures', 'exits'):
        allowed = {p['id'] for p in targets.get(kind, [])}
        target_by_id={p['id']:p for p in targets.get(kind,[])}
        parts = result.get(kind, [])
        if not isinstance(parts, list):continue
        for part in parts:
            if not isinstance(part, dict):continue
            ident, confidence = part.get('id'), part.get('confidence')
            pixel = part.get('anchorPixels')
            if not isinstance(ident, str) or ident not in allowed or kind+':'+ident in anchors:continue
            if part.get('visible') is not True or (kind == 'exits' and part.get('passable') is not True):continue
            if kind=='exits' and require_evidence:
                evidence=part.get('passageEvidence');local=part.get('cameraAnchorPixels')
                if not isinstance(evidence,dict) or evidence.get('kind') not in ('door-open','path-open','stairs-open'):continue
                box=evidence.get('boxPixels')
                if (type(evidence.get('viewIndex')) is not int or not 0<=evidence['viewIndex']<8
                        or evidence.get('viewIndex')!=part.get('viewIndex') or not isinstance(box,list) or len(box)!=4
                        or any(type(v) not in (int,float) or not isfinite(v) or not 0<=v<=512 for v in box)
                        or not box[0]<box[2] or not box[1]<box[3]
                        or not isinstance(local,list) or len(local)!=2
                        or any(type(v) not in (int,float) or not isfinite(v) for v in local)
                        or not isinstance(evidence.get('description'),str) or not evidence['description'].strip()):continue
                if not box[0]-8<=local[0]<=box[2]+8 or not box[1]+.4*(box[3]-box[1])<=local[1]<=min(512,box[3]+96):continue
                if target_by_id[ident].get('visualCue'):
                    cue=evidence.get('cueEvidence')
                    if (evidence.get('visualCueMatched') is not True or not isinstance(cue,str)
                            or len(cue.strip())<10):continue
                target=target_by_id[ident]
                if target.get('requiredSurfaceReview'):
                    from .surfaces import surface_valid
                    if not surface_valid(evidence.get('surfaceReview'),local,
                            evidence.get('independentReview',{}).get('beyondPointPixels'),evidence['viewIndex'],evidence.get('kind')):continue
                    proof=evidence['surfaceReview']
                    if (proof.get('apertureBoxPixels')!=evidence.get('apertureBoxPixels')
                            or proof.get('throughPointPixels')!=evidence.get('throughPointPixels')):continue
                from .passages import cue_facts_valid
                if not cue_facts_valid({'cueFacts':evidence.get('cueFacts'),'groundEvidence':evidence.get('groundEvidence'),
                                        'beyondPointPixels':evidence.get('independentReview',{}).get('beyondPointPixels'),
                                        'thresholdBoxPixels':box},target.get('requiredCueFacts',[]),
                                        require_ground=target.get('requiredGroundEvidence',False)):continue
                if target.get('requiredPassageKind') and evidence.get('kind')!=target['requiredPassageKind']:continue
                if target.get('allowedPassageKinds') and evidence.get('kind') not in target['allowedPassageKinds']:continue
                if target.get('independentReviewRequired'):
                    review=evidence.get('independentReview',{})
                    if target.get('requiredReviewSource') and review.get('source')!=target['requiredReviewSource']:continue
                    if (review.get('matched') is not True or review.get('continuous') is not True
                            or type(review.get('confidence')) not in (int,float)
                            or not .85<=review['confidence']<=1):continue
                    beyond=review.get('beyondPointPixels')
                    if (not isinstance(beyond,list) or len(beyond)!=2
                            or any(type(v) not in (int,float) or not isfinite(v) for v in beyond)
                            or not box[0]<=beyond[0]<=box[2] or not box[1]<=beyond[1]<local[1]-5):continue
            if type(confidence) not in (float, int) or not isfinite(confidence) or not .8 <= confidence <= 1:continue
            if not isinstance(pixel, list) or len(pixel) != 2:continue
            if not all(type(p) in (float, int) and isfinite(p) for p in pixel):continue
            x, y = pixel
            if not 0 <= x <= width or not 0 <= y <= height:continue
            # Ground targets cannot be in the sky, or at the distorted poles.
            # A nearby table's actual foot/contact can be below .92 latitude
            # while plainly visible in an undistorted downward camera. Do not
            # reject that measured surface solely for texture pole stretching.
            lower_limit=.98 if kind=='fixtures' and type(part.get('viewIndex')) is int and part['viewIndex'] in range(4,8) and part.get('cameraAnchorPixels') else .92
            if not .42 <= y / height <= lower_limit:continue
            if (kind=='exits' and require_evidence and target_by_id[ident].get('visualCue')
                    and not evidence.get('apertureBoxPixels')):
                from .inspection import region_pixels
                region=region_pixels(evidence['viewIndex'],box,width,height)
                area=(region[2]-region[0])*(region[3]-region[1])
                duplicate=False
                for prior,prior_x in route_regions:
                    prior_area=(prior[2]-prior[0])*(prior[3]-prior[1])
                    for shift in (-width,0,width):
                        overlap=max(0,min(region[2],prior[2]+shift)-max(region[0],prior[0]+shift))*max(0,min(region[3],prior[3])-max(region[1],prior[1]))
                        overlap_fraction=overlap/min(area,prior_area)
                        yaw_gap=min(abs(x-prior_x),width-abs(x-prior_x))/width*2*pi
                        if overlap_fraction>=.98 or (overlap_fraction>=.8 and yaw_gap<.1):duplicate=True
                if duplicate:continue
                route_regions.append((region,x))
            anchors[kind+':'+ident] = {'yaw': (x / width - .5) * 2 * pi,
                             'pitch': (.5 - y / height) * pi,
                             'verified': True, 'source': 'image-inspection', 'kind': kind}
            if kind=='exits' and require_evidence:
                anchors[kind+':'+ident]['passageEvidence']=deepcopy(evidence)
    if require_evidence:
        # Only fully validated, authorized measurements participate. A null
        # list or low-confidence extra detector ID cannot poison a real door.
        # Use the exact accepted evidence, not another raw record sharing its
        # ID. An invalid duplicate cannot replace a validated doorway here.
        routes={key.split(':',1)[1]:{'passageEvidence':value['passageEvidence']}
                for key,value in anchors.items() if key.startswith('exits:')}
        conflicts={ident for ident,p in routes.items() for other,q in routes.items()
                   if ident!=other and shared_aperture(p,q)}
        for ident in conflicts:anchors.pop('exits:'+ident,None)
    return anchors


def grounded_points(row, view):
    """Keep all destinations available, exposing only measured picture positions."""
    anchors = row.image.get('spatialAnchors', {}) if binding_matches(row) else {}
    result = {}
    for kind in ('points', 'exits'):
        result[kind] = []
        for original in view.get(kind, []):
            point = deepcopy(original)
            expected_kind = 'fixtures' if kind == 'points' else 'exits'
            anchor = anchors.get(expected_kind+':'+point['id'], {})
            yaw, pitch = anchor.get('yaw'), anchor.get('pitch')
            verified = (row.status == 'ready' and anchor.get('verified') is True
                        and anchor.get('source') == 'image-inspection' and anchor.get('kind') == expected_kind
                        and type(yaw) in (float, int) and type(pitch) in (float, int)
                        and isfinite(yaw) and isfinite(pitch) and -pi/2 <= pitch <= pi/2)
            point['imageBound'] = bool(verified)
            if verified:point.update(yaw=yaw, pitch=pitch)
            result[kind].append(point)
    return result


def queue_visual_repair(db, row, view, missing, missing_exits=(), composition_issues=()):
    """One known-response correction per image chain, within existing limits."""
    from datetime import datetime, timezone
    from sqlalchemy import select, func
    from ..models import uid
    from .models import SceneImage, SceneProfile
    from .composition import scene_cast
    from ..world.models import World
    if not (missing or missing_exits or composition_issues) or row.descriptor.get('visualRepairAttempt', 0) >= 1 or row.image.get('repairSceneId'):return None
    profile = db.get(SceneProfile, row.profile_id)
    if not profile or profile.status != 'active' or profile.version != row.profile_version:return None
    # Do not pay for a corrective picture if its required inspection has no
    # authorized capacity. Keep the first image and all navigation available.
    from ..story_flow import default_profile
    from ..completion_policy import input_ceiling,completion_limit
    from ..errors import Problem
    try:model=default_profile(db,row.owner_id)
    except Problem:return None
    limit=completion_limit(model,'analysis',world=True)
    if call_budget_exceeded(model,1) or input_budget_exceeded(model,input_ceiling(model,limit,world=True)) or output_budget_exceeded(model,limit):return None
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    count = db.scalar(select(func.count()).select_from(SceneImage).where(SceneImage.owner_id == row.owner_id, SceneImage.created_at >= day))
    pending = db.scalar(select(func.count()).select_from(SceneImage).where(SceneImage.owner_id == row.owner_id, SceneImage.status.in_(['queued', 'running', 'saving'])))
    if image_budget_exceeded(profile,count) or pending >= 3:return None
    world = db.get(World, row.world_id)
    generation = db.scalar(select(func.max(SceneImage.generation)).where(SceneImage.owner_id == row.owner_id, SceneImage.line_id == row.line_id, SceneImage.scene_hash == row.scene_hash)) or row.generation
    descriptor = {**row.descriptor, 'sceneCast': scene_cast(view, world.name),
                  'observerName': view['descriptor'].get('observerName', ''),
                  'resetComposition': bool(row.image.get('scaleFailureIds') or 'projection-mismatch' in composition_issues),
                  'compositionIssues':list(composition_issues),
                  'visualRepairAttempt': 1, 'repairOfSceneId': row.id,
                  'missingCharacterIds': missing, 'missingExitIds': list(missing_exits), 'referenceSceneId': row.id,
                  'referenceMode': 'edit-cast-and-state'}
    frozen_cast={person['entityId']:person for person in row.descriptor.get('sceneCast',[])}
    for person in descriptor['sceneCast']:
        frozen=frozen_cast.get(person['entityId'],{})
        for field in ('appearance','lookImageId'):
            if field in frozen:person[field]=frozen[field]
    if descriptor.get('jointSceneReferenceVersion')==1:
        from .joint import correction_descriptor
        descriptor=correction_descriptor(descriptor,row.image.get('sha256'))
        # Preserve the explicit current posture and placement rather than
        # reconstructing them from an opening excerpt during correction.
        descriptor['sceneCast']=[dict(person) for person in row.descriptor['sceneCast']]
    corrected = SceneImage(id=uid(), owner_id=row.owner_id, world_id=row.world_id,
                           line_id=row.line_id, checkpoint_id=row.checkpoint_id,
                           profile_id=row.profile_id, profile_version=row.profile_version,
                           scene_hash=row.scene_hash, generation=generation+1,
                           status='queued', descriptor=descriptor, image={})
    db.add(corrected);db.flush()
    return corrected.id
