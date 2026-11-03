"""Bounded measurements in actual horizontal views, never inferred coordinates."""
import base64,json
from math import tan,pi
from copy import deepcopy
from .heads import measured_camera
from .pipeline import validate_placements


def reserved_regions(found,passages,view):
    """Project existing measured reservations into this actual camera only."""
    from .inspection import sphere_pixel,camera_pixel
    sources=[('reserved-future-body',p.get('viewIndex'),p.get('bodyBoxPixels')) for p in found.values()]
    for passage in passages if isinstance(passages,list) else []:
        evidence=passage.get('passageEvidence',{}) if isinstance(passage,dict) else {}
        sources.append(('measured-passage',evidence.get('viewIndex'),evidence.get('boxPixels')))
    result=[]
    for kind,index,box in sources:
        if type(index) is not int or not 0<=index<8 or not isinstance(box,list) or len(box)!=4:continue
        points=[]
        for x in (box[0],(box[0]+box[2])/2,box[2]):
            for y in (box[1],(box[1]+box[3])/2,box[3]):
                try:points.append(camera_pixel(view,*sphere_pixel(index,x,y,2048,1024),2048,1024))
                except ValueError:continue
        if not points:continue
        left=max(0,min(p[0] for p in points));right=min(512,max(p[0] for p in points))
        top=max(0,min(p[1] for p in points));bottom=min(512,max(p[1] for p in points))
        if right-left<1 or bottom-top<1:continue
        result.append({'kind':kind,'bounds':[n*1000/512 for n in (left,top,right,bottom)]})
    return result


def natural_slot(part,*,strict=False):
    # The camera is at adult eye height. A standing adult cannot have its
    # entire head below that horizon; such a guide makes the editor recenter
    # and enlarge the actor outside the measured insertion mask.
    from .inspection import CAMERAS,FOV
    pitch=CAMERAS[part.get('viewIndex',0)][1] if part.get('cameraPitchVersion')==1 else 0
    horizon=256+256*tan(pitch)/tan(FOV*pi/360)
    if strict and part.get('pose') in ('seated','kneeling') and part['bodyBoxPixels'][1]<horizon-6:return False
    return part.get('pose')!='standing' or part['bodyBoxPixels'][1]<=horizon


def readable_slot(part,version=1):
    # A 100px adult can leave a face smaller than the independent head crop's
    # minimum. Reject that insertion proposal before paying to paint a body
    # which can never support a legible, verified character interaction.
    box=part['bodyBoxPixels']
    # Version 2 uses the guide's 13% standing / 17% posed head proportions to
    # retain a margin above the 12px crop minimum without rejecting medium
    # distance floor/seat proposals. Actual painted heads and support still
    # require the unchanged independent proofs; this is only render planning.
    minimum=(140 if part['pose']=='standing' else 100) if version==2 else (180 if part['pose']=='standing' else 140)
    return box[3]-box[1]>=minimum


def inspect_placements(parsed,cast,cameras,narrative,call,limit,*,independent=False,correct_invalid=False,verify_support=False,downward=False,single_context=False,clarify_support=False,panorama=None,on_exhausted=None,verify_initial=False,readable_version=1,verified_placements=(),on_verified=None):
    initial=validate_placements(parsed,cast)
    if not cast or not independent and initial and all(natural_slot(p) for p in initial):return parsed
    found={};desired={p['entityId']:p for p in cast};rejected=[];support_rejections={};premeasured={}
    for part in verified_placements:
        entity=part.get('entityId')
        if entity not in desired or entity in found:continue
        candidates={**found,entity:part}
        checked=validate_placements({**parsed,'placements':list(candidates.values())},[p for p in cast if p['entityId'] in candidates])
        if checked and all(readable_slot(p,readable_version) for p in checked):found=candidates
    if len(found)==len(cast):return {**deepcopy(parsed),'placements':[found[p['entityId']] for p in cast]}
    if verify_initial and independent and verify_support and initial:
        from .supports import request as support_request,valid as support_valid
        from .parallel import ordered_reads
        def check_initial(entry):
            ordinal,part=entry;index=part['viewIndex']
            if part['entityId'] in found:return None
            if not readable_slot(part,readable_version):return None
            proof=call('analysis:initial-slot:'+str(ordinal),'world_scene_supports',
                       support_request(part,cameras[index],single_camera=single_context),limit)
            return (part,proof,'analysis:initial-slot:'+str(ordinal),index) if support_valid(proof,part['pose']) else None
        for measured in ordered_reads(check_initial,enumerate(initial)):
            if measured is not None:
                part,proof,stage,index=measured;found[part['entityId']]=part
                if on_verified is not None:on_verified(part,proof,stage,index)
        if len(found)==len(cast):return {**deepcopy(parsed),'placements':[found[p['entityId']] for p in cast]}
    # A completed but malformed proposal may be corrected in at most two
    # separately reserved calls. An absent proposal or unknown call is never
    # retried. The same geometric and real-image checks still apply.
    view_count=8 if downward else 4
    views=list(enumerate(cameras[:view_count]));attempt=0
    while views:
        index,camera=views.pop(0)
        remaining=[{k:p[k] for k in ('entityId','name','description','appearance','expectedPose') if k in p}
                   for p in cast if p['entityId'] not in found]
        from .inspection import CAMERAS
        data={'inspectionMode':'actor-placement','camera':{'image':1,'viewIndex':index,'width':512,'height':512,'pitchDegrees':CAMERAS[index][1]*180/pi},
            'futureCast':remaining,'publicNarrative':narrative,'requiredOutputKeys':['placements'],
            'supportWillBeIndependentlyVerified':verify_support,
            'measurement':'This ONE image is an actual HORIZONTAL camera view at adult eye height. Propose places for futureCast, who are NOT painted yet. Use the numbered 64-pixel grid to measure real free floor or supporting furniture. Standing people MUST have shoe contact on the actual walking floor, NEVER a bench, shelf or countertop; choose a different free floor area if furniture occupies the proposed region. Seated people need a real chair or bench. Describe the actual support under the exact proposed contact. Preserve any expectedPose. A standing adult head/body top must be ABOVE the horizon y=256. Every complete adult body must fit INSIDE x=26..486 and y=26..486, width=30..240, height=100..460. Ground point must lie inside body width and within 12 pixels of body bottom. Do not block any visible doorway, walking path, existing person or another proposed placement. Do not use an empty wall or floating space. Return an empty list if this camera has no suitable area. Never copy coordinates from another view.',
            'poseAndFloorContract':'Choose an actual free floor area at its REAL measured distance; do not force a particular distance from the camera. A standing adult head starts above y=256. A normally seated or kneeling adult head starts BELOW the eye-height horizon, y>=250; do not put a seated person on a high counter. For seated people the chair/bench supports the pelvis, while BOTH shoes contact the actual floor at the body bottom. groundPointPixels means this floor contact, NEVER a bench seat, countertop or shelf. If this camera has no such support area return no proposal.',
            'posePreference':'Preserve an explicit expectedPose or an explicit CURRENT pose in publicNarrative. Otherwise prefer standing on clearly visible free floor. Do not choose sitting or kneeling merely for style when the current story has not requested that pose; an unused armchair is not a reason to seat a character.',
            'outputContract':{'placements':[{'entityId':'one remaining futureCast id','viewIndex':index,
                'bodyBoxPixels':'[left,top,right,bottom] measured in this image','groundPointPixels':'[x,y] measured support contact',
                'pose':'standing/seated/kneeling','confidence':'actual confidence 0.8..1','evidence':'actual free support, adult scale and headroom'}]},
            '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(measured_camera(camera)).decode()],
            # This small one-camera measurement uses a complete JSON response.
            # The supplier's single-camera SSE sometimes terminates with DONE
            # but no finish receipt; never treat that partial exchange as proof.
            '_structured':True,'_visionStream':False}
        if single_context:
            data['cameraContextVersion']=1
            data['reservationsInThisCamera']={'coordinateSystem':'normalized-1000-xy-v1',
                'regions':reserved_regions(found,parsed.get('exits',[]),index),
                'instruction':'These are measured geometric reservations in THIS camera, not painted people. '
                    'The future bodies reserved in earlier cameras must remain separate. Choose a different horizontal '
                    'angular sector from each reserved-future-body and measured-passage, including overlapping views. '
                    'Do not draw these reservations or count them as existing physical people. Propose only the remaining IDs.'}
        if verify_initial:
            standing,posed=(140,100) if readable_version==2 else (180,140)
            data['readableBodyHeightPixels']={'standing':standing,'posed':posed}
            data['readableCharacterContract']=('Choose a real nearby floor/chair where the full standing body is at least '
                f'{standing} pixels high, or a seated/kneeling body at least {posed} pixels high in this actual 512px camera. '
                'A tiny distant body through a doorway cannot support a readable face. Do not enlarge a distant floor '
                'proposal artificially; choose a different physically supported nearer position, or return no proposal.')
        correction=attempt>=view_count
        if correction:
            data['rejectedProposal']=next(parts for view,parts in reversed(rejected) if view==index)
            if index in support_rejections:data['previousSupportChecks']=support_rejections[index]
            data['correctionInstruction']='The completed previous proposal did not satisfy the exact ID, body size/frame, requested pose, floor contact or non-overlap contract. Inspect these same received pixels again. Supply every required field, including the exact entityId. Correct the measurement only if real support exists; otherwise return an empty array. Do not infer a person or alter the image.'
        if verify_initial and not correction and index not in premeasured:
            # Independent camera proposals may run together. Later merging
            # still checks every physical overlap and verifies actual support;
            # a speculative proposal never becomes an accepted placement.
            from .parallel import ordered_reads
            def measure(entry):
                camera_index,picture=entry;payload=deepcopy(data)
                payload['camera'].update(viewIndex=camera_index,pitchDegrees=CAMERAS[camera_index][1]*180/pi)
                payload['outputContract']['placements'][0]['viewIndex']=camera_index
                payload['_sceneImages']=['data:image/jpeg;base64,'+base64.b64encode(measured_camera(picture)).decode()]
                if single_context:payload['reservationsInThisCamera']['regions']=reserved_regions(found,parsed.get('exits',[]),camera_index)
                return camera_index,call('analysis:placement:'+str(camera_index),'world_scene_placements',payload,limit)
            premeasured.update(ordered_reads(measure,[(index,camera),*views[:3]]))
        response=(premeasured[index] if index in premeasured and not correction else
            call('analysis:slot-fix:'+str(attempt-view_count) if correction else 'analysis:placement:'+str(index),'world_scene_placements',data,limit))
        attempt+=1
        try:parts=json.loads(response.get('text','')).get('placements',[])
        except (ValueError,TypeError,AttributeError):parts=[]
        if not isinstance(parts,list):continue
        before=len(found)
        for part in parts:
            if not isinstance(part,dict) or part.get('viewIndex')!=index or part.get('entityId') not in desired or part['entityId'] in found:continue
            if downward:part={**part,'cameraPitchVersion':1}
            candidates={**found,part['entityId']:part}
            proposal={**parsed,'placements':list(candidates.values())}
            validated=validate_placements(proposal,[p for p in cast if p['entityId'] in candidates])
            if (validated and (not verify_initial or all(readable_slot(p,readable_version) for p in validated))
                    and (verify_support or all(natural_slot(p,strict=independent) for p in validated))):
                if verify_support:
                    from .supports import request as support_request,valid as support_valid
                    support_stage='analysis:slot-check:'+str(attempt-1)+':'+str(list(desired).index(part['entityId']))
                    proof=call(support_stage,
                        'world_scene_supports',support_request(part,camera,single_camera=single_context),limit)
                    if clarify_support:
                        from .supports import needs_detail,detail_request
                        from .inspection import detail_camera
                        if needs_detail(proof,part['pose']):
                            support_stage='analysis:slot-detail:'+str(attempt-1)+':'+str(list(desired).index(part['entityId']))
                            proof=call(support_stage,
                                'world_scene_supports',detail_request(part,detail_camera(panorama,index) if panorama is not None else camera),limit)
                    if not support_valid(proof,part['pose']):
                        try:observed=json.loads(proof.get('text',''))
                        except (ValueError,TypeError):observed={}
                        support_rejections.setdefault(index,[]).append({'entityId':part['entityId'],'observation':observed})
                        continue
                    if on_verified is not None:on_verified(part,proof,support_stage,index)
                found=candidates
        if len(found)==len(cast):return {**deepcopy(parsed),'placements':[found[p['entityId']] for p in cast]}
        if parts and len(found)==before:rejected.append((index,parts))
        elif parts and any(p.get('entityId') not in found for p in parts if isinstance(p,dict)):rejected.append((index,parts))
        if not views and correct_invalid and attempt==view_count and rejected:
            # Spend bounded corrections on the clearest actual support, not
            # whichever poor camera happened to be visited last.
            selected=[]
            for person in cast:
                if person['entityId'] in found:continue
                ranked=[]
                for view,checks in support_rejections.items():
                    for check in checks:
                        if check['entityId']!=person['entityId']:continue
                        observed=check['observation']
                        if not isinstance(observed,dict):continue
                        score=(3*(observed.get('poseSupported') is True)+2*(observed.get('footSurface')=='walkable-ground')
                            +2*(observed.get('naturalAdultScale') is True)+(observed.get('clearOfPassage') is True)
                            +3*(person.get('expectedPose')=='seated' and observed.get('pelvisSupport') in ('chair','bench','sofa')))
                        confidence=observed.get('confidence');confidence=confidence if type(confidence) in (int,float) else 0
                        ranked.append((score,confidence,view))
                if ranked:
                    best=max(ranked)[2]
                    if best not in selected:selected.append(best)
            selected=list(dict.fromkeys([*selected,*(index for index,_ in reversed(rejected))]))[:2]
            views=[(index,cameras[index]) for index in selected]
    if on_exhausted is not None and attempt>=view_count:on_exhausted()
    return {**deepcopy(parsed),'placements':[]}
