"""Locate a painted head in one camera and confirm its actual pixel crop."""
import base64
from copy import deepcopy
from hashlib import sha256
import io, json
from math import isfinite
from PIL import Image, ImageDraw

VERSION = 2


def box_valid(box):
    return (isinstance(box,list) and len(box)==4
        and all(type(v) in (int,float) and isfinite(v) and 0<=v<=512 for v in box)
        and 12<=box[2]-box[0]<=220 and 12<=box[3]-box[1]<=240)


def proof_valid(proof,image_hash=None,*,version=VERSION):
    valid=(isinstance(proof,dict) and proof.get('version')==version
        and isinstance(proof.get('imageHash'),str) and len(proof['imageHash'])==64
        and (image_hash is None or proof['imageHash']==image_hash)
        and isinstance(proof.get('cropHash'),str) and len(proof['cropHash'])==64
        and type(proof.get('viewIndex')) is int and 0<=proof['viewIndex']<8
        and box_valid(proof.get('headBoxPixels'))
        and proof.get('containsPhysicalHead') is True and proof.get('fullHeadVisible') is True
        and proof.get('identityMatched') is True
        and type(proof.get('confidence')) in (int,float) and isfinite(proof['confidence'])
        and .85<=proof['confidence']<=1)
    if not valid or version==1:return valid
    crop_box=proof.get('cropBoxPixels');crop_head=proof.get('cropHeadBoxPixels');size=proof.get('cropImageSize')
    if (proof.get('tightHeadBounds') is not True
            or not box_valid(crop_box) or not isinstance(crop_head,list) or len(crop_head)!=4
            or not isinstance(size,list) or len(size)!=2
            or any(type(v) is not int or not 20<=v<=320 for v in size)
            or any(type(v) not in (int,float) or not isfinite(v) for v in crop_head)
            or not 0<=crop_head[0]<crop_head[2]<=size[0]
            or not 0<=crop_head[1]<crop_head[3]<=size[1]):return False
    mapped=[crop_box[0]+crop_head[0]*(crop_box[2]-crop_box[0])/size[0],
            crop_box[1]+crop_head[1]*(crop_box[3]-crop_box[1])/size[1],
            crop_box[0]+crop_head[2]*(crop_box[2]-crop_box[0])/size[0],
            crop_box[1]+crop_head[3]*(crop_box[3]-crop_box[1])/size[1]]
    return all(abs(a-b)<.001 for a,b in zip(mapped,proof['headBoxPixels']))


def person_context(person):
    return {k:person[k] for k in ('entityId','name','description','appearance','expectedPose') if k in person}


def measured_camera(camera):
    with Image.open(io.BytesIO(camera)) as source:
        measured=source.convert('RGB')
    draw=ImageDraw.Draw(measured)
    for position in range(64,512,64):
        draw.line((position,0,position,511),fill='#c1b496',width=1)
        draw.line((0,position,511,position),fill='#c1b496',width=1)
        draw.rectangle((position+1,24,position+22,35),fill='#172b26')
        draw.text((position+2,24),str(position),fill='white')
        draw.rectangle((2,position+1,25,position+12),fill='#172b26')
        draw.text((3,position+1),str(position),fill='white')
    output=io.BytesIO();measured.save(output,format='JPEG',quality=90)
    return output.getvalue()


def locate_request(person,index,camera):
    # No proposed/detected coordinates: locate afresh in this ONE actual view.
    return {'inspectionMode':'person-head','person':person_context(person),
        'camera':{'image':1,'viewIndex':index,'width':512,'height':512},
        'measurement':'Locate the physical person matching person, NOT a decorative portrait. Measure the complete head including hair and chin, excluding shoulders. The thin numbered grid marks EXACT pixel coordinates every 64 pixels: x increases right and y increases downward from the top-left. Read the grid to locate the head, then interpolate its tight bounds. The grid is an analysis overlay, not scenery. Use pixels of this one supplied image. Never copy coordinates from another camera.',
        'outputContract':{'visible':'boolean','headBoxPixels':'[left,top,right,bottom] within 0..512, tight complete head','confidence':'number 0..1'},
        '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(measured_camera(camera)).decode()],
        '_structured':True,'_visionStream':True}


def crop_request(person,camera,box,*,padding=16):
    left,top,right,bottom=box
    crop_box=[max(0,int(left)-padding),max(0,int(top)-padding),min(512,int(right)+padding+1),min(512,int(bottom)+padding+1)]
    with Image.open(io.BytesIO(camera)) as image:
        crop=image.crop(tuple(crop_box))
        scale=320/max(crop.size)
        crop=crop.resize(tuple(round(v*scale) for v in crop.size),Image.Resampling.LANCZOS)
        out=io.BytesIO();crop.save(out,format='JPEG',quality=90)
    data=out.getvalue()
    return {'inspectionMode':'head-crop','person':person_context(person),
        'imageSize':{'width':crop.width,'height':crop.height},
        'measurement':'Blindly inspect this actual crop. Does it contain the COMPLETE physical head (hair through chin) matching this person? A wall, railing, torso, portrait, or partly missing face is false. Do not assume the previous detector is correct. Independently measure tight headBoxPixels in THIS cropped image: hair/hat through chin only, with no shoulders or empty background margins. Pixel origin is this crop top-left; x increases right and y increases down. These crop-local bounds will refine the coarse detector, so do not return the entire crop.',
        'outputContract':{'containsPhysicalHead':'boolean','fullHeadVisible':'boolean','identityMatched':'boolean','tightHeadBounds':'boolean','headBoxPixels':'[left,top,right,bottom] tight head bounds in this crop imageSize','confidence':'number 0..1'},
        '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(data).decode()],
        '_structured':True,'_visionStream':True},sha256(data).hexdigest(),crop_box


def parse(result):
    try:value=json.loads(result.get('text',''))
    except (ValueError,TypeError):return {}
    return value if isinstance(value,dict) else {}


def inspect_heads(parsed,cast,cameras,image_hash,call,limit,*,on_progress=None,search_cameras=False,retained_ids=(),parallel=False):
    if parallel and len(cast)>1:
        from .parallel import person_reads
        return person_reads(lambda value,people,progress:inspect_heads(value,people,cameras,image_hash,call,limit,
            on_progress=progress,search_cameras=search_cameras,retained_ids=retained_ids),parsed,cast,on_progress=on_progress)
    value=deepcopy(parsed)
    if not isinstance(value,dict):return value
    people={p['entityId']:p for p in cast}
    for ordinal,part in enumerate(value.get('characters',[])):
        if not isinstance(part,dict) or part.get('visible') is not True:continue
        ident=part.get('entityId');index=part.get('viewIndex')
        if ident not in people or type(index) is not int or not 0<=index<len(cameras):continue
        if ident in retained_ids and proof_valid(part.get('headReview'),image_hash):continue
        original_index=index
        # Downward views often show the feet well but cut the head at the top.
        # Locate a complete head in the corresponding horizontal camera. Body
        # and ground measurements retain their original camera coordinates.
        if search_cameras and type(part.get('bodyViewIndex')) is int and 0<=part['bodyViewIndex']<len(cameras):
            # A visible body's camera is a useful search order, never proof of
            # a head. This avoids searching the reported empty/head-only view
            # first when the bulk result already measured the body elsewhere.
            index=part['bodyViewIndex']
        if index>=4:index=index%4
        part.pop('headReview',None)
        part.pop('headIdentityMismatch',None)
        retry_hint=None;searched=set();search_count=0
        # Two bounded independent attempts, each with its own complete receipt.
        # Unknown requests propagate out; they are never replayed here.
        for attempt in range(2):
            if retry_hint:found=retry_hint
            else:
                searched.add(index)
                found=parse(call('analysis:head:'+str(ordinal)+':'+str(attempt),
                    'world_scene_heads',locate_request(people[ident],index,cameras[index]),limit))
            box=found.get('headBoxPixels')
            confidence=found.get('confidence')
            if search_cameras and (found.get('visible') is not True or not box_valid(box)
                    or type(confidence) not in (int,float) or confidence<.85):
                # A bulk answer can name the wrong camera while the actual
                # person is plainly visible elsewhere. Search actual cameras
                # instead of repeating absence in the same empty wall view.
                for other in (p for p in range(min(4,len(cameras))) if p not in searched):
                    searched.add(other)
                    candidate=parse(call(f'analysis:head-search:{ordinal}:{search_count}','world_scene_heads',
                        locate_request(people[ident],other,cameras[other]),limit))
                    search_count+=1
                    candidate_confidence=candidate.get('confidence')
                    if (candidate.get('visible') is True and box_valid(candidate.get('headBoxPixels'))
                            and type(candidate_confidence) in (int,float) and candidate_confidence>=.85):
                        index=other;found=candidate;box=found['headBoxPixels'];confidence=candidate_confidence;break
                else:break
            if (found.get('visible') is not True or not box_valid(box)
                    or type(confidence) not in (int,float) or not isfinite(confidence) or confidence<.85):continue
            request,crop_hash,crop_box=crop_request(people[ident],cameras[index],box,padding=32 if retry_hint else 16)
            checked=parse(call('analysis:head-crop:'+str(ordinal)+':'+str(attempt),
                'world_scene_heads',request,limit))
            if (checked.get('containsPhysicalHead') is True and checked.get('fullHeadVisible') is True
                    and checked.get('identityMatched') is False
                    and type(checked.get('confidence')) in (int,float) and .85<=checked['confidence']<=1):
                part['headIdentityMismatch']=True
            tight=checked.get('headBoxPixels');size=request['imageSize']
            if not isinstance(tight,list) or len(tight)!=4 or any(type(v) not in (int,float) or not isfinite(v) for v in tight):continue
            refined=[crop_box[0]+tight[0]*(crop_box[2]-crop_box[0])/size['width'],
                     crop_box[1]+tight[1]*(crop_box[3]-crop_box[1])/size['height'],
                     crop_box[0]+tight[2]*(crop_box[2]-crop_box[0])/size['width'],
                     crop_box[1]+tight[3]*(crop_box[3]-crop_box[1])/size['height']]
            proof={**checked,'version':VERSION,'imageHash':image_hash,'cropHash':crop_hash,
                'viewIndex':index,'headBoxPixels':refined,'cropBoxPixels':crop_box,
                'cropHeadBoxPixels':tight,'cropImageSize':[size['width'],size['height']]}
            if not proof_valid(proof,image_hash):
                # A received crop can confirm the matching face but cut its
                # edge. Use the remaining bounded attempt on a wider actual
                # crop; keep the full visibility, tight bounds and confidence
                # gates unchanged. No coordinates are promoted from this hint.
                if checked.get('containsPhysicalHead') is True and checked.get('identityMatched') is True:retry_hint=found
                elif search_cameras:
                    remaining=[p for p in range(min(4,len(cameras))) if p not in searched]
                    if not remaining:break
                    index=remaining[0];retry_hint=None
                continue
            part.pop('headIdentityMismatch',None)
            if index!=original_index:
                if part.get('bodyBoxPixels') is not None:part.setdefault('bodyViewIndex',original_index)
                if part.get('groundPointPixels') is not None:part.setdefault('groundViewIndex',original_index)
                part['viewIndex']=index
            part['headBoxPixels']=refined;part['headReview']=proof
            part.pop('headIdentityMismatch',None)
            if on_progress:on_progress(deepcopy(value))
            break
    return value
