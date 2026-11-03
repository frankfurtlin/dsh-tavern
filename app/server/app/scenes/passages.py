"""Independent same-image route review, separate from whole-scene detection."""
import base64, io
from copy import deepcopy
from math import isfinite
from PIL import Image


def shared_aperture(left,right):
    """Compare received-pixel openings across overlapping real cameras."""
    from .inspection import sphere_pixel,camera_pixel
    def inside(source,destination):
        evidence=source.get('passageEvidence',{});other=destination.get('passageEvidence',{})
        point=evidence.get('throughPointPixels');box=other.get('apertureBoxPixels')
        if not isinstance(point,list) or len(point)!=2 or not isinstance(box,list) or len(box)!=4:return False
        try:
            sphere=sphere_pixel(evidence['viewIndex'],*point,2048,1024)
            x,y=camera_pixel(other['viewIndex'],*sphere,2048,1024)
            return box[0]-2<=x<=box[2]+2 and box[1]-2<=y<=box[3]+2
        except (ValueError,TypeError,KeyError):return False
    def coverage(source,destination):
        evidence=source.get('passageEvidence',{});other=destination.get('passageEvidence',{})
        box=evidence.get('apertureBoxPixels');target=other.get('apertureBoxPixels')
        if (not isinstance(box,list) or len(box)!=4 or not isinstance(target,list) or len(target)!=4
                or any(type(n) not in (int,float) or not isfinite(n) for n in box+target)
                or not box[0]<box[2] or not box[1]<box[3]):return 0
        covered=0
        try:
            # Reproject the actual aperture polygon, not its nonlinear spherical
            # bounding rectangle. A small edge-measurement difference must not
            # turn one physical door into two routes.
            for ix in range(7):
                for iy in range(7):
                    x=box[0]+(box[2]-box[0])*ix/6;y=box[1]+(box[3]-box[1])*iy/6
                    sphere=sphere_pixel(evidence['viewIndex'],x,y,2048,1024)
                    try:px,py=camera_pixel(other['viewIndex'],*sphere,2048,1024)
                    except ValueError:continue
                    covered+=target[0]-2<=px<=target[2]+2 and target[1]-2<=py<=target[3]+2
            return covered/49
        except (ValueError,TypeError,KeyError):return 0
    return (inside(left,right) and inside(right,left)
            or max(coverage(left,right),coverage(right,left))>=.85)


def distinct_passages(options):
    """Select measured alternatives jointly; never retain two names on one door.

    Bounded search favours complete actual coverage before confidence. The
    limit affects coverage only: every retained combination remains distinct.
    """
    keys=sorted(options,key=lambda key:(len(options[key]),key))
    best={};score=(-1,-1);steps=0
    def visit(index,chosen,confidence):
        nonlocal best,score,steps
        steps+=1
        if steps>20000 or len(chosen)+len(keys)-index<score[0]:return
        if index==len(keys):
            candidate=(len(chosen),confidence)
            if candidate>score:score=candidate;best=deepcopy(chosen)
            return
        key=keys[index]
        for part in sorted(options[key],key=lambda p:p['confidence'],reverse=True):
            if any(shared_aperture(part,prior) for prior in chosen.values()):continue
            chosen[key]=part;visit(index+1,chosen,confidence+part['confidence']);chosen.pop(key)
        visit(index+1,chosen,confidence)
    visit(0,{},0)
    return best


def cue_requirements(cue):
    cue=cue.lower()
    if 'tavern' in cue:return ['amber-windows','wooden-barrels','open-interior-threshold']
    if 'market' in cue:return ['canvas-awning','empty-market-stalls','open-market-aisle']
    if 'townhouse' in cue:
        return ['exterior-stone-steps','open-residential-threshold']+(['open-residential-interior'] if 'residential interior entrance hall' in cue else [])
    return []


def cue_facts_valid(answer,required,width=512,height=512,*,require_ground=False):
    ground=answer.get('groundEvidence')
    if (require_ground or required or ground is not None) and (not isinstance(ground,dict) or ground.get('nearSurface')!='walkable-ground'
            or ground.get('farSurface')!='walkable-ground' or ground.get('unobstructed') is not True
            or ground.get('continuation') not in ('through-opening','open-ground')):return False
    if not required:return True
    facts=answer.get('cueFacts');threshold=answer.get('thresholdBoxPixels')
    if (not isinstance(facts,list) or not isinstance(threshold,list) or len(threshold)!=4
            or any(type(n) not in (int,float) or not isfinite(n) for n in threshold)):return False
    for name in required:
        matches=[p for p in facts if isinstance(p,dict) and p.get('feature')==name]
        if not matches:return False
        # Two windows/barrel groups beside one real doorway are ordinary,
        # distinct evidence. Validate every instance; duplicated boxes or a
        # contradictory negative must not inflate a positive finding.
        measured=set()
        for fact in matches:
            box=fact.get('boxPixels')
            if (fact.get('visible') is not True or fact.get('samePassage') is not True
                or type(fact.get('confidence')) not in (int,float) or not .85<=fact['confidence']<=1
                or not isinstance(box,list) or len(box)!=4
                or any(type(n) not in (int,float) or not isfinite(n) for n in box)
                or not 0<=box[0]<box[2]<=width or not 0<=box[1]<box[3]<=height
                or not isinstance(fact.get('evidence'),str) or len(fact['evidence'].strip())<10):return False
            if tuple(box) in measured:return False
            measured.add(tuple(box))
            # A landmark elsewhere in the same picture cannot name this opening.
            if box[0]>threshold[2]+140 or box[2]<threshold[0]-140:return False
            if box[3]<threshold[1]-40 or box[1]>threshold[3]+40:return False
            if name.startswith('open-'):
                beyond=answer.get('beyondPointPixels')
                if (not isinstance(beyond,list) or len(beyond)!=2
                    or any(type(n) not in (int,float) or not isfinite(n) for n in beyond)
                    or not box[0]-8<=beyond[0]<=box[2]+8 or not box[1]-8<=beyond[1]<=box[3]+8):return False
    return True


def negative_ground_or_landmark(answer,required):
    ground=answer.get('groundEvidence')
    if isinstance(ground,dict) and (ground.get('farSurface') in ('wall','furniture')
            or ground.get('continuation') in ('terminates-at-wall','blocked') or ground.get('unobstructed') is False):return True
    facts=answer.get('cueFacts')
    return isinstance(facts,list) and any(isinstance(fact,dict) and fact.get('feature') in required
        and type(fact.get('confidence')) in (int,float) and .85<=fact['confidence']<=1
        and (fact.get('visible') is False or fact.get('samePassage') is False) for fact in facts)


def offset_facts(value,bounds):
    result=[]
    for fact in value if isinstance(value,list) else []:
        box=fact.get('boxPixels') if isinstance(fact,dict) else None
        if (not isinstance(box,list) or len(box)!=4
                or any(type(n) not in (int,float) or not isfinite(n) for n in box)):continue
        result.append({**deepcopy(fact),'boxPixels':[box[0]+bounds[0],box[1]+bounds[1],box[2]+bounds[0],box[3]+bounds[1]]})
    return result


def review_request(parsed,targets,cameras):
    routes=[];images=list(cameras[:4]);regions={}
    if len(images)!=4:return {'routes':[]},regions
    parts=parsed.get('exits',[]) if isinstance(parsed,dict) else []
    if not isinstance(parts,list):parts=[]
    for target in targets.get('exits',[]):
        if not target.get('independentReviewRequired'):continue
        part=next((p for p in parts if isinstance(p,dict) and p.get('id')==target['id']),{})
        evidence=part.get('passageEvidence',{});box=evidence.get('boxPixels') if isinstance(evidence,dict) else None
        view=part.get('viewIndex')
        candidate=(part.get('visible') is True and part.get('passable') is True
                and type(view) is int and 0<=view<len(cameras)
                and isinstance(box,list) and len(box)==4
                and all(type(v) in (int,float) and isfinite(v) and 0<=v<=512 for v in box)
                and box[0]<box[2] and box[1]<box[3])
        region={'allowedKinds':target.get('allowedPassageKinds') or [target.get('requiredPassageKind')],
                'cameraImages':[1,2,3,4]}
        route={'id':target['id'],'visualCue':target['visualCue'],
            'requiredPassageKind':target.get('requiredPassageKind'),
            'allowedPassageKinds':target.get('allowedPassageKinds'),'fullCameraImages':[1,2,3,4]}
        if candidate:
            # The full four views let the independent reviewer also find a
            # genuine passage that the first detector missed entirely.
            bounds=(max(0,int(box[0])-110),max(0,int(box[1])-80),min(512,int(box[2])+110),min(512,int(box[3])+55))
            with Image.open(io.BytesIO(cameras[view])) as im:
                crop=im.crop(bounds);out=io.BytesIO();crop.save(out,format='JPEG',quality=88)
            images.append(out.getvalue())
            route.update(cropImage=len(images),cropSize={'width':crop.width,'height':crop.height})
            region.update(viewIndex=view,bounds=list(bounds),cropImage=len(images))
        routes.append(route);regions[target['id']]=region
    return {'routes':routes,'_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(v).decode() for v in images],
            '_structured':True,'_visionStream':True},regions


def apply_review(parsed,review,regions):
    result=deepcopy(parsed) if isinstance(parsed,dict) else {}
    answers=review.get('routes',[]) if isinstance(review,dict) else []
    if not isinstance(answers,list):answers=[]
    parts=result.get('exits',[]) if isinstance(result,dict) else []
    if not isinstance(parts,list):parts=[]
    for ident,region in regions.items():
        part=next((p for p in parts if isinstance(p,dict) and p.get('id')==ident),None)
        if part is None:
            part={'id':ident,'visible':False,'passable':False,'confidence':0};parts.append(part)
        matches=[p for p in answers if isinstance(p,dict) and p.get('id')==ident]
        answer=matches[0] if len(matches)==1 else {}
        image=answer.get('image');valid_image=True
        if region.get('singleImage'):
            view=region['viewIndex'];bounds=region['bounds'];valid_image=type(image) is int and image==1
        elif region.get('cameraImages'):
            if type(image) is int and image in region['cameraImages']:
                view=image-1;bounds=[0,0,512,512]
            elif type(image) is int and image==region.get('cropImage'):
                view=region['viewIndex'];bounds=region['bounds']
            else:view=0;bounds=[0,0,512,512];valid_image=False
        else:view=region['viewIndex'];bounds=region['bounds']
        width,height=bounds[2]-bounds[0],bounds[3]-bounds[1]
        box=answer.get('thresholdBoxPixels');anchor=answer.get('anchorPixels');beyond=answer.get('beyondPointPixels')
        def point(value):return (isinstance(value,list) and len(value)==2 and all(type(v) in (int,float) and isfinite(v) for v in value) and 0<=value[0]<=width and 0<=value[1]<=height)
        valid=(valid_image and answer.get('matched') is True and answer.get('continuous') is True
            and type(answer.get('confidence')) in (int,float) and .85<=answer['confidence']<=1
            and answer.get('kind') in (region.get('allowedKinds') or [region.get('requiredKind')]) and point(anchor) and point(beyond)
            and isinstance(box,list) and len(box)==4 and point(box[:2]) and point(box[2:])
            and box[0]<box[2] and box[1]<box[3]
            and box[0]<=anchor[0]<=box[2] and box[1]+.4*(box[3]-box[1])<=anchor[1]<=min(height,box[3]+30)
            and box[0]<=beyond[0]<=box[2] and box[1]<=beyond[1]<anchor[1]-5
            and isinstance(answer.get('evidence'),str) and len(answer['evidence'].strip())>=10
            and cue_facts_valid(answer,region.get('requiredCueFacts',[]),width,height,
                                require_ground=region.get('requiredGroundEvidence',False)))
        if valid and region.get('requiredOpeningProof'):
            from .surfaces import opening_geometry
            valid=opening_geometry(answer,anchor,beyond)
        evidence=part.get('passageEvidence',{})
        if not isinstance(evidence,dict):evidence={}
        if not valid:
            observed_wrong=(valid_image and type(answer.get('confidence')) in (int,float) and .85<=answer['confidence']<=1
                and (answer.get('matched') is False or answer.get('continuous') is False
                     or negative_ground_or_landmark(answer,region.get('requiredCueFacts',[])))
                and isinstance(answer.get('evidence'),str) and len(answer['evidence'].strip())>=10)
            part['passageEvidence']={**evidence,
                **({'visualCueMatched':False} if observed_wrong else {}),
                'independentReview':{'matched':False,'continuous':False,
                    'status':'mismatch' if observed_wrong else 'unconfirmed',
                    'reason':answer.get('evidence','Independent measurement unavailable')}}
            continue
        part.update(visible=True,passable=True,confidence=answer['confidence'],viewIndex=view,
            anchorPixels=[anchor[0]+bounds[0],anchor[1]+bounds[1]])
        part['passageEvidence']={'kind':answer['kind'],'viewIndex':view,
            'boxPixels':[box[0]+bounds[0],box[1]+bounds[1],box[2]+bounds[0],box[3]+bounds[1]],
            'description':answer['evidence'],'visualCueMatched':True,'cueEvidence':answer['evidence'],
            'cueFacts':offset_facts(answer.get('cueFacts'),bounds),
            'groundEvidence':deepcopy(answer.get('groundEvidence')),
            **({'apertureBoxPixels':[answer['apertureBoxPixels'][0]+bounds[0],answer['apertureBoxPixels'][1]+bounds[1],
                                    answer['apertureBoxPixels'][2]+bounds[0],answer['apertureBoxPixels'][3]+bounds[1]],
                'throughPointPixels':[answer['throughPointPixels'][0]+bounds[0],answer['throughPointPixels'][1]+bounds[1]]}
               if region.get('requiredOpeningProof') else {}),
            'independentReview':{'matched':True,'continuous':True,'confidence':answer['confidence'],
                'beyondPointPixels':[beyond[0]+bounds[0],beyond[1]+bounds[1]],
                'source':'single-camera-measurement' if region.get('singleImage') else 'same-image-independent-crop'}}
    result['exits']=parts
    return result


def camera_review_requests(targets,cameras):
    """One real image per request: the server owns its camera identity.

    Suppliers mixed 1-based image numbers with the label printed on another
    camera. A single-camera response cannot bind pixels to a different view.
    Negative observations in one view are not a whole-panorama rejection.
    """
    for view,camera in enumerate(cameras[:8]):
        routes=[{k:target[k] for k in ('id','visualCue','allowedPassageKinds','requiredCueFacts','requiredGroundEvidence') if k in target}
                for target in targets if target.get('independentReviewRequired')]
        if not routes:return
        regions={target['id']:{'singleImage':True,'viewIndex':view,'bounds':[0,0,512,512],
                 'allowedKinds':target.get('allowedPassageKinds'),'requiredCueFacts':target.get('requiredCueFacts',[]),
                 'requiredOpeningProof':target.get('requiredSurfaceReview',False),
                 'requiredGroundEvidence':target.get('requiredGroundEvidence',False)} for target in targets
                 if target.get('independentReviewRequired')}
        yield {'inspectionMode':'single-camera','imageSize':{'width':512,'height':512},'routes':routes,
            'requiredRouteOutputKeys':['id','image','matched','continuous','confidence','kind','thresholdBoxPixels',
                                       'anchorPixels','beyondPointPixels','apertureBoxPixels','throughPointPixels','groundEvidence','cueFacts','evidence'],
            'groundContract':{'required':True,'nearSurface':['walkable-ground','furniture','wall','unknown'],
                              'farSurface':['walkable-ground','furniture','wall','unknown'],
                              'unobstructed':'boolean; false for a counter, stall, wall or closed door',
                              'continuation':['through-opening','open-ground','terminates-at-wall','blocked','unknown'],
                              'measurement':'Near is the anchorPixels surface; far is the beyondPointPixels surface. Describe actual pixels, not the desired destination.'},
            'coordinateContract':'ALL boxes and points are finite coordinates within 0..512 in this ONE image. Never coordinates from a different view or from a larger source image. Every required cue needs a measured box; repeated windows/barrel groups may each have a DISTINCT box. No duplicate measurements.',
            'anchorContract':'anchorPixels is AT THE MEASURED THRESHOLD, NOT the foreground at the observer feet. x must be inside thresholdBoxPixels left..right. y must be in its lower 60%, no more than 30 pixels below its bottom. For an aisle measure the near edge of that actual aisle, not the image bottom. beyondPointPixels must be inside this SAME measured opening and at least 5 pixels above anchorPixels, on actual visible far floor. Look carefully: a brick wall beside the doorway is NOT interior floor.',
            'openingContract':'For an indoor exit, measure the FIRST opening leaving the CURRENT room: the nearby enclosing door frame, not a distant gate or second door across the courtyard. apertureBoxPixels encloses the ENTIRE visible opening, from its top to the real floor threshold; never substitute a narrow patch of ground for the opening. thresholdBoxPixels may be a lower strip, but its smaller height is not the aperture height. A is on the approach side of THIS near doorway; B and C are on ground after crossing THIS same near doorway. Measure apertureBoxPixels tightly around the REAL empty doorway or walking corridor, separately from approach stairs. For path-open a visible open aisle is enough: no door frame or physical threshold is required. B and C are inside that same visible aisle past its near mouth. For door-open/stairs-open B and C must be floor after its real doorway threshold. C is at least 5 pixels above B, may be off-centre to avoid furniture, and must be visible ground, never a table or its unseen underlying floor. A approaches this same aperture at most 60 pixels below its bottom. Two stairs in front of a wall are NOT crossing an opening. Do not extend an aperture box to include a wall beside the real door. Distinct destination IDs require distinct real openings.',
            'responseShape':{'routes':[{'id':'supplied exact route ID','image':1,'matched':'boolean','continuous':'boolean',
                'confidence':'0..1','kind':'door-open/path-open/stairs-open','thresholdBoxPixels':'[left,top,right,bottom]',
                'anchorPixels':'[x,y] near floor AT the lower edge of thresholdBoxPixels, never observer foreground','beyondPointPixels':'[x,y] visible far floor inside threshold',
                'apertureBoxPixels':'[left,top,right,bottom] tight actual empty opening, excluding its approach stairs',
                'throughPointPixels':'[x,y] visible deeper ground AFTER crossing the same aperture, at least 5px above beyondPointPixels',
                'groundEvidence':{'nearSurface':'observed surface type','farSurface':'observed surface type',
                                  'unobstructed':'boolean','continuation':'observed continuation type'},
                'cueFacts':[{'feature':'exact requiredCueFacts entry','visible':'boolean','samePassage':'boolean',
                             'confidence':'0..1','boxPixels':'[left,top,right,bottom]','evidence':'actual feature observation'}],
                'evidence':'actual landmark and near/far ground observation'}]},
            'instruction':'Exactly ONE attached image. All coordinates are local to this 512x512 picture. Return image=1 for all findings. Search ONLY this actual picture. If a destination landmark is absent here report matched=false; it may exist in another camera, which the server checks separately.',
            '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(camera).decode()],
            '_structured':True,'_visionStream':True},regions


def merge_camera_reviews(parsed,findings,targets,expected_views=8):
    """Use measured positive evidence, or a complete four-view absence.

    Never turn one camera's negative result into a missing route, nor accept a
    low-confidence measurement merely to finish generation.
    """
    result=deepcopy(parsed) if isinstance(parsed,dict) else {}
    parts=result.get('exits',[])
    if not isinstance(parts,list):parts=[]
    options={target['id']:[p for finding in findings for p in finding.get('exits',[])
              if isinstance(p,dict) and p.get('id')==target['id']
              and p.get('passageEvidence',{}).get('independentReview',{}).get('matched') is True]
             for target in targets}
    selected=distinct_passages(options)
    for target in targets:
        ident=target['id']
        observed=[p for finding in findings for p in finding.get('exits',[])
                  if isinstance(p,dict) and p.get('id')==ident]
        positive=[p for p in observed if p.get('passageEvidence',{}).get('independentReview',{}).get('matched') is True]
        # Prefer the clearest actual camera; overlap between adjacent views is
        # normal. The later geometry check still prevents shared route pins.
        chosen=selected.get(ident)
        part=next((p for p in parts if isinstance(p,dict) and p.get('id')==ident),None)
        if part is None:part={'id':ident,'visible':False,'passable':False};parts.append(part)
        if chosen:
            part.update(deepcopy(chosen));continue
        confirmed_missing=len(observed)==expected_views and all(p.get('passageEvidence',{}).get('independentReview',{}).get('status')=='mismatch' for p in observed)
        evidence=part.get('passageEvidence',{})
        if not isinstance(evidence,dict):evidence={}
        part['passageEvidence']={**evidence,
            **({'visualCueMatched':False} if confirmed_missing else {}),
            'independentReview':{'matched':False,'continuous':False,
                'status':'mismatch' if confirmed_missing else 'unconfirmed',
                'reason':'No matching open threshold measured across all actual horizontal and downward cameras' if confirmed_missing else 'A complete single-camera threshold measurement is still unavailable'}}
    result['exits']=parts
    return result
