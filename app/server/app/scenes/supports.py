"""Check proposed support against untouched received pixels before painting."""
import base64,io,json
from math import isfinite
from PIL import Image,ImageDraw


def request(part,camera,*,single_camera=False):
    with Image.open(io.BytesIO(camera)) as raw:picture=raw.convert('RGB')
    left,top,right,bottom=part['bodyBoxPixels'];feet=part['groundPointPixels']
    pelvis=[(left+right)/2,top+(bottom-top)*.61]
    locations=([(pelvis,'#ffe45a','pelvis')] if part['pose']=='seated' else [])+[(feet,'#f24ebd','shoes')]
    if single_camera:
        from .coordinate_cameras import marked_camera
        return {'inspectionMode':'proposed-body-support-context','pose':part['pose'],
            'bodyBoxPixels':[n*1000/512 for n in part['bodyBoxPixels']],
            'points':[{'label':label,'centrePixels':[n*1000/512 for n in point]} for point,_,label in locations],
            'imageSize':{'width':1000,'height':1000},
            'instruction':'ONE actual complete camera image, with NO crops or other camera. No proposed person is painted yet. '
                'The cyan rectangle, yellow pelvis ring (seated only), and magenta shoe ring are hypotheses. Their exact centres '
                'retain original architecture pixels. Use the whole room geometry to distinguish horizontal floor, seat, '
                'vertical riser and furniture; texture orientation alone is not a surface plane. The shoe point must be '
                'actual visible walking floor, never a chair seat. A seated pelvis must have an actual chair/bench/sofa seat '
                'and visible floor at the shoes. A standing torso may naturally occlude background furniture and needs no '
                'pelvis seat. Judge adult scale against the actual furniture and depth. Only an actual doorway aperture '
                'blocked by this rectangle violates clearOfPassage; ordinary presence on some room floor is allowed. '
                'Raised hearth rims, fireplace stones, furnace platforms, plinths and structural ledges are not '
                'walking floor even when their top is horizontal stone. Check the exact point against the '
                'surrounding room floor height and continuity. '
                'Report uncertainty when the exact support cannot be seen; do not invent hidden floor or seat.',
            'requiredOutput':{'pose':part['pose'],'poseSupported':'boolean','naturalAdultScale':'boolean',
                'clearOfPassage':'boolean','footSurface':'walkable-ground or uncertain or furniture',
                'pelvisSupport':'bench or chair or sofa or none or uncertain','confidence':'number 0..1',
                'evidence':'actual whole-camera geometry at the exact marked shoe/pelvis points'},
            '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(marked_camera(camera,[(label,point,colour) for point,colour,label in locations],((left,top,right,bottom),))).decode()],
            '_structured':True,'_visionStream':True}
    context=picture.copy();draw=ImageDraw.Draw(context)
    draw.rectangle((left,top,right,bottom),outline='#00c9ef',width=2)
    images=[context];points=[]
    for point,colour,label in locations:
        x,y=point;draw.ellipse((x-5,y-5,x+5,y+5),outline=colour,width=2)
        bounds=(max(0,int(x)-45),max(0,int(y)-45),min(512,int(x)+46),min(512,int(y)+46))
        crop=picture.crop(bounds).resize((320,320),Image.Resampling.LANCZOS)
        cx=(x-bounds[0])*320/(bounds[2]-bounds[0]);cy=(y-bounds[1])*320/(bounds[3]-bounds[1])
        ImageDraw.Draw(crop).ellipse((cx-10,cy-10,cx+10,cy+10),outline=colour,width=2)
        points.append({'image':len(images)+1,'label':label,'centrePixels':[cx,cy]})
        images.append(crop)
    encoded=[]
    for image in images:
        output=io.BytesIO();image.save(output,format='JPEG',quality=92,subsampling=0)
        encoded.append('data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode())
    return {'inspectionMode':'proposed-body-support','pose':part['pose'],'pointCrops':points,
        'instruction':'NO proposed person is painted yet. Image 1 is the actual received camera; cyan body rectangle and coloured rings are HYPOTHESES, not evidence. Later images enlarge the untouched underlying pixels at the labelled pointCrops centres. The shoe centre MUST be real visible walking floor. For SEATED only, a yellow pelvis centre additionally needs an ACTUAL bench/chair/sofa seat; wall/floor/counter is not a seat. A STANDING pelvis requires NO seat or contact: a future body can naturally occlude a shelf, wall or counter behind it. Do not reject standing simply because background furniture would be behind the torso. Evaluate depth from actual shoe floor and adult scale against nearby furniture. Natural foreground occlusion is allowed when depth and support agree. clearOfPassage refers to an actual open doorway aperture being blocked, NOT all ordinary floor space: occupying part of a room aisle is normal person presence. Do not invent hidden supports. Reject ambiguous feet, wrong scale or an actual blocked doorway.',
        'requiredOutput':{'pose':part['pose'],'poseSupported':'boolean','naturalAdultScale':'boolean',
            'clearOfPassage':'boolean','footSurface':'walkable-ground or uncertain or furniture',
            'pelvisSupport':'bench or chair or sofa or none or uncertain','confidence':'number 0..1',
            'evidence':'describe actual support and floor pixels; no inference from the hypothesis'},
        '_sceneImages':encoded,'_structured':True,'_visionStream':True}


def valid(result,pose):
    try:value=json.loads(result.get('text',''))
    except (ValueError,TypeError):return False
    return (isinstance(value,dict) and value.get('pose')==pose
        and all(value.get(k) is True for k in ('poseSupported','naturalAdultScale','clearOfPassage'))
        and value.get('footSurface')=='walkable-ground'
        and (pose!='seated' or value.get('pelvisSupport') in ('bench','chair','sofa'))
        and type(value.get('confidence')) in (int,float) and isfinite(value['confidence']) and .85<=value['confidence']<=1
        and isinstance(value.get('evidence'),str) and len(value['evidence'].strip())>=15)


def needs_detail(result,pose):
    try:value=json.loads(result.get('text',''))
    except (ValueError,TypeError):return False
    return (isinstance(value,dict) and value.get('pose')==pose
        and all(value.get(k) is True for k in ('poseSupported','naturalAdultScale','clearOfPassage'))
        and value.get('footSurface')=='walkable-ground'
        and (pose!='seated' or value.get('pelvisSupport') in ('bench','chair','sofa'))
        and type(value.get('confidence')) in (int,float) and isfinite(value['confidence']) and .65<=value['confidence']<.85
        and isinstance(value.get('evidence'),str) and len(value['evidence'].strip())>=15)


def detail_request(part,camera):
    value=request(part,camera,single_camera=True)
    value.update(inspectionMode='proposed-body-support-detail',_visionStream=False)
    value['instruction']+=(' ONE finer-resolution rendering of this SAME actual camera comes from original '
        'panorama pixels. There are no separate crops or other camera views. Keep the whole-room geometry '
        'when inspecting the exact shoe/pelvis centres. No prior verdict or desired confidence is provided. '
        'Return EVERY requiredOutput field, and uncertainty for hidden or ambiguous support.')
    return value
