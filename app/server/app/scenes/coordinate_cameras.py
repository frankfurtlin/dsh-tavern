"""Make camera pixels and normalized output share one 1000-unit scale."""
import base64,io
from copy import deepcopy
from PIL import Image,ImageDraw


def camera_image(source,*,subsampling=2):
    with Image.open(io.BytesIO(source)) as raw:picture=raw.convert('RGB').resize((1000,1000),Image.Resampling.LANCZOS)
    for quality in (85,75,65,50,35):
        output=io.BytesIO();picture.save(output,format='JPEG',quality=quality,subsampling=subsampling)
        if output.tell()<=240000:return output.getvalue()
    raise ValueError('Camera image exceeds the bounded inspection input')


def marked_camera(source,points,boxes=(),*,box_colour='#00c9ef'):
    """Overlay logical 512-unit hypotheses without resampling the source first."""
    with Image.open(io.BytesIO(source)) as raw:picture=raw.convert('RGB')
    if picture.width!=picture.height or picture.width not in (512,1024):raise ValueError('Unknown camera frame')
    original=picture.copy()
    scale=picture.width/512;draw=ImageDraw.Draw(picture)
    for box in boxes:draw.rectangle([n*scale for n in box],outline=box_colour,width=round(2*scale))
    for label,point,colour in points:
        x,y=(n*scale for n in point);radius=6*scale
        draw.ellipse((x-radius,y-radius,x+radius,y+radius),outline=colour,width=round(2*scale))
        draw.text((x+9*scale,y-10*scale),label,fill=colour)
    # The shoe point commonly lies ON the proposed body's bottom edge. A
    # rectangle must never paint over the source floor being inspected.
    for _,point,_ in points:
        x,y=(round(n*scale) for n in point);radius=round(3*scale)
        bounds=(x-radius,y-radius,x+radius+1,y+radius+1)
        patch=original.crop(bounds);mask=Image.new('L',patch.size)
        ImageDraw.Draw(mask).ellipse((0,0,patch.width-1,patch.height-1),fill=255)
        picture.paste(patch,bounds[:2],mask)
    out=io.BytesIO();picture.save(out,format='PNG')
    return camera_image(out.getvalue(),subsampling=0)


def prepare(data,cameras):
    value=deepcopy(data);mode=value.get('inspectionMode');camera=value.get('camera',{})
    uri=lambda source:'data:image/jpeg;base64,'+base64.b64encode(camera_image(source)).decode()
    if mode in ('actor-placement','person-head','person-body','single-person','fixture-ground') and type(camera.get('viewIndex')) is int and 0<=camera['viewIndex']<len(cameras):
        index=camera['viewIndex'];value['_sceneImages']=[uri(cameras[index]),*value['_sceneImages'][1:]]
        value['camera']={**camera,'width':1000,'height':1000}
        if mode=='actor-placement' and value.get('cameraContextVersion')==1:
            from math import tan,pi
            from .inspection import FOV
            pitch=camera.get('pitchDegrees',0)
            horizon=(256+256*tan(pitch*pi/180)/tan(FOV*pi/360))*1000/512
            value['measurement']=(f'This ONE actual 1000x1000 rectilinear camera is pitched {pitch:g} degrees; '
                f'its eye-height horizon is y={horizon:.1f}, NOT automatically the image centre. There is no numbered grid. '
                'Measure normalized 0..1000 XY coordinates from these actual pixels. Future people are not painted yet. '
                'Choose real free shoe-floor contact and, for seated people, a real empty chair/bench/sofa seat. '
                'Every returned proposal MUST contain an exact remaining entityId and this viewIndex. '
                'Complete body must fit inside x/y=51..949, width=59..469, height=196..898; shoe point lies inside '
                'body width; place its y coordinate AT the body bottom on the actual shoe-floor contact. '
                'Do not overlap another proposed body or block a real doorway. '
                'Return no proposal for this camera when the full body, actual floor or seat is not visible.')
            value['poseAndFloorContract']=(f'Use the actual camera horizon y={horizon:.1f} and nearby furniture to judge adult '
                'scale and depth. Preserve any expectedPose and public narrative. A seated pelvis rests on an actual empty '
                'seat, while BOTH shoes contact visible walking floor at body bottom; never measure shoe contact on the '
                'seat cushion. A standing torso may occlude background furniture if its shoes have actual floor. '
                'Do not invent hidden floor or a seat. Check the exact marked shoe and pelvis points after proposing.')
        if mode=='person-head':
            value['measurement']='Locate the actual complete head, including hair and chin, excluding shoulders, in this ONE 1000x1000 camera. There is no numbered grid. Output numeric normalized 0..1000 XY bounds; do not copy another view or a requested position. Report absent when uncertain.'
            value['outputContract']={**value.get('outputContract',{}),'headBoxPixels':'numeric [left,top,right,bottom] in normalized 0..1000 XY, tight actual complete head'}
        if isinstance(value.get('verifiedHead'),dict):
            value['verifiedHead']={**value['verifiedHead'],
                'headBoxPixels':[n*1000/512 for n in value['verifiedHead']['headBoxPixels']]}
        if isinstance(value.get('rejectedProposal'),list):
            value['rejectedProposal']=[{k:([n*1000/512 for n in v] if k in ('bodyBoxPixels','groundPointPixels') and isinstance(v,list) else v)
                for k,v in part.items()} for part in value['rejectedProposal'] if isinstance(part,dict)]
    elif not mode and isinstance(value.get('cameraViews'),dict):
        value['_sceneImages']=[value['_sceneImages'][0],*[uri(source) for source in cameras],*value['_sceneImages'][9:]]
        value['cameraViews']={**value['cameraViews'],'width':1000,'height':1000}
        value.pop('placementContract',None) # Dedicated single-camera measurements govern actual placement.
    return value


def wire_protocol(data):
    # Keep the decoder's original 512-pixel return frame private. The provider
    # sees actual 1000-pixel cameras and normalized output, never mixed units.
    value=deepcopy(data)
    if value.get('inspectionMode')=='single-camera' and isinstance(value.get('routes'),list):
        value['coordinateProtocol'].pop('frameWidth',None);value['coordinateProtocol'].pop('frameHeight',None)
        value['coordinateProtocol']['inputCameraSize']=value.get('imageSize',{}).get('width',512)
    elif value.get('inspectionMode') in ('actor-placement','person-head','person-body','single-person','fixture-ground') or value.get('cameraViews'):
        value['coordinateProtocol'].pop('frameWidth',None);value['coordinateProtocol'].pop('frameHeight',None)
        value['coordinateProtocol']['inputCameraSize']=1000
    return value
