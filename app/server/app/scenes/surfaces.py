"""Blind confirmation of the exact near/far pixels, never desired route names."""
import base64
import io
from copy import deepcopy
from math import isfinite
from PIL import Image,ImageDraw

SOURCE='same-image-opening-crops-v3'
LEGACY_SOURCE='same-image-opening-crops-v2'
CONTEXT_SOURCE='same-image-opening-context-v4'


def opening_geometry(evidence,near,far):
    """A stair surface is not proof of crossing the doorway above it."""
    box=evidence.get('apertureBoxPixels');through=evidence.get('throughPointPixels')
    def point(p):
        return (isinstance(p,list) and len(p)==2 and all(type(n) in (int,float)
                and isfinite(n) and 0<=n<=512 for n in p))
    if (not isinstance(box,list) or len(box)!=4 or any(type(n) not in (int,float)
            or not isfinite(n) or not 0<=n<=512 for n in box)
            or not box[0]<box[2] or not box[1]<box[3]
            or not all(point(p) for p in (near,far,through))):return False
    # B and C are walking floor after the real aperture, not two steps before
    # a wall. A can be a stair immediately outside the same opening.
    return (box[0]-8<=near[0]<=box[2]+8 and box[1]<=near[1]<=box[3]+60
            and all(box[0]<=p[0]<=box[2] and box[1]<=p[1]<=box[3] for p in (far,through))
            and far[1]<near[1]-5 and through[1]<far[1]-5)


def _jpeg(picture):
    for quality in (92,85,75,65):
        output=io.BytesIO();picture.save(output,format='JPEG',quality=quality,subsampling=0)
        if len(output.getvalue())<=240000:return output.getvalue()
    from ..errors import Problem
    raise Problem('SCENE_IMAGE_INVALID','局部校验图片超出大小限制，未发送请求',422,
                  {'requestSent':False,'responseReceived':False})


def point_request(part,camera,*,single_camera=False):
    evidence=part['passageEvidence']
    kind=evidence.get('kind','door-open')
    if kind not in ('door-open','path-open','stairs-open'):raise ValueError('Unknown actual passage kind')
    near=part['anchorPixels'];far=evidence['independentReview']['beyondPointPixels']
    through=evidence['throughPointPixels'];aperture=evidence['apertureBoxPixels']
    with Image.open(io.BytesIO(camera)) as original:
        original.load();picture=original.convert('RGB')
    geometry=('For path-open, the aperture is the visible mouth/cross-section of a continuous walking aisle or road. A door frame, jamb, door leaf or material threshold is NOT required. openingConfirmed means the actually visible walking corridor is open; crossedThreshold means B is along the corridor past its measured mouth, with C deeper along that SAME corridor. Confirm all three exact centres on connected walkable ground, without a wall, stall or furniture blocking the route.'
              if kind=='path-open' else
              'For door-open or stairs-open, confirm the actual physically open doorway above any approach steps. B and C must be walking floor after the same real doorway threshold, never two exterior steps ending at a wall or another door beside the stairs.')
    if single_camera:
        from .coordinate_cameras import marked_camera
        return {'inspectionMode':'blind-single-camera-surface-points','passageKind':kind,
            'imageRoles':[{'image':1,'role':'ONE actual camera image with hypothesis rings A/B/C and a candidate aperture rectangle; no other camera or crop'}],
            'points':[{'label':label,'centrePixels':[n*1000/512 for n in point]} for label,point in [('A',near),('B',far),('C',through)]],
            'imageSize':{'width':1000,'height':1000},
            'instruction':'Inspect this ONE actual camera only. Ring centres are original received pixels, not painted ground. '
                'Use the surrounding room geometry to distinguish horizontal walking floor from vertical walls, door leaves, '
                'risers and furniture. Vertical wood grain alone does not establish a wall. The rings and rectangle are '
                'hypotheses, never proof. '+geometry+' Unobstructed refers to the actual marked A-to-B-to-C segment through '
                'the same opening: objects beside that segment or farther beyond C are not obstructions to this segment. '
                'Never accept a barrier that actually crosses the marked segment or invent a hidden way around it. '
                'Judge exact centre pixels; report unknown and low confidence for ambiguity. No destination name, previous '
                'verdict, person identities or desired result is provided. Return A/B/C surface, confidence and observed '
                'evidence, plus openingConfirmed/crossedThreshold/sameOpening/unobstructed and overall confidence/evidence.',
            '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(marked_camera(camera,[(label,point,colour) for label,point,colour in [('A',near,'#00c9ef'),('B',far,'#f24ebd'),('C',through,'#ffe45a')]],(aperture,),box_colour='#ffe45a')).decode()],
            '_structured':True,'_visionStream':True}
    images=[];details=[]
    context=picture.copy();draw=ImageDraw.Draw(context)
    for label,point,colour in [('A',near,'#00c9ef'),('B',far,'#f24ebd'),('C',through,'#ffe45a')]:
        x,y=point;draw.ellipse((x-6,y-6,x+6,y+6),outline=colour,width=2)
        draw.text((x+9,y-10),label,fill=colour)
        # The centre remains original received pixels. A circle is a locator,
        # not a painted substitute for the surface being tested.
        bounds=(max(0,int(x)-40),max(0,int(y)-40),min(512,int(x)+41),min(512,int(y)+41))
        crop=picture.crop(bounds).resize((324,324),Image.Resampling.LANCZOS)
        centre=((x-bounds[0])*324/(bounds[2]-bounds[0]),(y-bounds[1])*324/(bounds[3]-bounds[1]))
        mark=ImageDraw.Draw(crop);cx,cy=centre
        mark.ellipse((cx-14,cy-14,cx+14,cy+14),outline=colour,width=3)
        images.append(_jpeg(crop))
        details.append({'image':len(images)+1,'label':label,'centrePixels':list(centre)})
    draw.rectangle(aperture,outline='#ffe45a',width=2)
    bounds=(max(0,int(aperture[0])-16),max(0,int(aperture[1])-16),
            min(512,int(aperture[2])+17),min(512,int(aperture[3])+17))
    images.append(_jpeg(picture.crop(bounds).resize((400,400),Image.Resampling.LANCZOS)))
    context_bytes=_jpeg(context)
    return {'inspectionMode':'blind-surface-points','passageKind':kind,'points':details,
        'apertureBoxPixels':aperture,
        'instruction':'Image 1 is the actual camera with rings A, B and C and a yellow candidate opening rectangle. Images 2, 3 and 4 enlarge ONLY their exact original point pixels. Image 5 enlarges that candidate opening. The rectangle and passageKind are hypotheses, NOT proof. Classify exact centres independently using the full camera to establish the surface plane: vertical wood grain in a crop alone does not establish a wall, because floorboards can run vertically in the picture. Horizontal walkable threshold or stair tops differ from vertical risers; never accept a vertical face as floor. '+geometry+' Do not infer destination, requested landmark or prior verdict. Return openingConfirmed, crossedThreshold, sameOpening, unobstructed, A/B/C surface evidence and confidence.',
        '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(v).decode() for v in [context_bytes,*images]],
        '_structured':True,'_visionStream':True}


def needs_detail(value):
    if (not isinstance(value,dict) or any(value.get(k) is not True
            for k in ('sameOpening','unobstructed','openingConfirmed','crossedThreshold'))):return False
    confidences=[value.get('confidence')]
    for label in ('A','B','C'):
        point=value.get(label)
        if (not isinstance(point,dict) or point.get('surface')!='walkable-ground'
                or not isinstance(point.get('evidence'),str) or len(point['evidence'].strip())<10):return False
        confidences.append(point.get('confidence'))
    return (all(type(n) in (int,float) and isfinite(n) and .65<=n<=1 for n in confidences)
        and any(n<.85 for n in confidences))


def detail_request(part,camera):
    value=point_request(part,camera,single_camera=True)
    value.update(inspectionMode='same-camera-surface-detail',_visionStream=False)
    value['instruction']+=(' This is ONE finer-resolution rendering of the SAME camera from original panorama pixels, '
        'not a crop sheet or another viewpoint. Exact A/B/C hypotheses and the complete room remain in one frame. '
        'No previous verdict or desired confidence is supplied. Inspect the exact centres in their whole-room '
        'context; do not infer hidden floor. Return the COMPLETE A/B/C and all four crossing booleans, '
        'confidence and observed evidence, including uncertainty where actual surfaces remain unclear.')
    return value


def surface_valid(proof,near,far,view,passage_kind=None):
    if not isinstance(proof,dict):return False
    if proof.get('source')==LEGACY_SOURCE:
        # Old door receipts already required a real doorway crossing. A
        # kind-less legacy check cannot certify the new open-aisle contract.
        if passage_kind not in (None,'door-open','stairs-open'):return False
    elif proof.get('source') in (SOURCE,CONTEXT_SOURCE):
        if proof.get('source')==CONTEXT_SOURCE and proof.get('contextVersion')!=1:return False
        if proof.get('passageKind') not in ('door-open','path-open','stairs-open'):return False
        if passage_kind is not None and proof['passageKind']!=passage_kind:return False
    else:return False
    if proof.get('viewIndex')!=view or proof.get('nearPixels')!=near or proof.get('farPixels')!=far:return False
    if not opening_geometry(proof,near,far):return False
    result=proof.get('result')
    if not isinstance(result,dict) or any(result.get(k) is not True
            for k in ('sameOpening','unobstructed','openingConfirmed','crossedThreshold')):return False
    def confident(value):return type(value) in (int,float) and isfinite(value) and .85<=value<=1
    if not confident(result.get('confidence')):return False
    for label in ('A','B','C'):
        point=result.get(label)
        if (not isinstance(point,dict) or point.get('surface')!='walkable-ground'
                or not confident(point.get('confidence')) or not isinstance(point.get('evidence'),str)
                or len(point['evidence'].strip())<10):return False
    return True


def apply_surface(part,result,image_hash,*,single_camera=False):
    measured=deepcopy(part);evidence=measured['passageEvidence'];review=evidence['independentReview']
    proof={'source':CONTEXT_SOURCE if single_camera else SOURCE,
           **({'contextVersion':1} if single_camera else {}),'viewIndex':evidence['viewIndex'],'nearPixels':deepcopy(measured['anchorPixels']),
           'passageKind':evidence.get('kind'),
           'farPixels':deepcopy(review['beyondPointPixels']),'imageHash':image_hash,
           'apertureBoxPixels':deepcopy(evidence.get('apertureBoxPixels')),
           'throughPointPixels':deepcopy(evidence.get('throughPointPixels')),
           'result':deepcopy(result) if isinstance(result,dict) else {}}
    evidence['surfaceReview']=proof
    valid=surface_valid(proof,measured['anchorPixels'],review['beyondPointPixels'],evidence['viewIndex'],evidence.get('kind'))
    if measured.pop('requiresBlindApertureCheck',False) and valid:
        # Coordinates proposed from an observed frame are NOT floor evidence.
        # Only this independent actual-pixel verdict grants passage use.
        review.update(matched=True,continuous=True,status='confirmed',source='single-camera-measurement',
            confidence=min(review['confidence'],result['confidence']))
        measured['passable']=True
        evidence['groundEvidence']={'nearSurface':result['A']['surface'],'farSurface':result['B']['surface'],
            'unobstructed':result['unobstructed'],'continuation':'through-opening'}
    if not valid:
        # A misplaced point does not prove the painting itself lacks a route.
        # Re-measure the same received pixels rather than repainting good art.
        review.update(matched=False,continuous=False,status='unconfirmed',reason='Actual opening and three ground crops did not confirm crossing the same unobstructed threshold')
    return measured
