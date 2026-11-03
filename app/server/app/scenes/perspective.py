"""Painted camera edits are reprojected into the same sphere, never UI sprites."""
import io
from math import atan2, cos, isfinite, pi, sin, sqrt, tan
from PIL import Image, ImageDraw
from .inspection import CAMERAS, FOV, SIZE, camera_images


def edit_camera_yaw(descriptor,index):
    """Centre private painting on failed routes; never use this as proof."""
    if descriptor.get('renderStage')=='actor' and 'cameraYaw' in descriptor.get('placement',{}):
        yaw=descriptor['placement']['cameraYaw']
        if type(yaw) not in (int,float) or not isfinite(yaw):raise ValueError('Invalid actor editing camera')
        return yaw
    if descriptor.get('routeRepairGuideVersion',0)<2:return None
    directions=[route.get('yaw') for route in descriptor.get('environmentRoutePlan',{}).get(str(index),[])]
    if len(directions)!=1 or any(type(v) not in (int,float) or not isfinite(v) for v in directions):raise ValueError('A centred repair requires exactly one route')
    return atan2(sin(directions[0]),cos(directions[0]))


def centred_actor_placement(placement):
    """Reframe measured rays, keeping the person's actual spherical position."""
    from copy import deepcopy
    from .inspection import sphere_pixel,camera_pixel
    value=deepcopy(placement);index=value['viewIndex'];box=value['bodyBoxPixels']
    centre=sphere_pixel(index,(box[0]+box[2])/2,256,2048,1024)
    yaw=(centre[0]/2048-.5)*2*pi
    points=[camera_pixel(index,*sphere_pixel(index,x,y,2048,1024),2048,1024,camera_yaw=yaw)
            for x in (box[0],(box[0]+box[2])/2,box[2]) for y in (box[1],box[3])]
    value.update(cameraYaw=yaw,measuredBodyBoxPixels=deepcopy(box),measuredGroundPointPixels=deepcopy(value['groundPointPixels']),
        bodyBoxPixels=[min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)],
        groundPointPixels=list(camera_pixel(index,*sphere_pixel(index,*value['groundPointPixels'],2048,1024),2048,1024,camera_yaw=yaw)))
    return value


def route_repair_boxes(descriptor, index):
    """Private generation masks, NEVER measurements of a finished picture."""
    if descriptor.get('environmentEdit')=='clear-floor':
        plan=descriptor.get('floorRepairPlan',{});boxes=plan.get('boxes')
        if (type(index) is not int or not 0<=index<8 or plan.get('version')!=1 or plan.get('viewIndex')!=index
                or descriptor.get('environmentCameraPitchVersion')!=1 or not isinstance(boxes,list) or not 1<=len(boxes)<=2):raise ValueError('Invalid floor edit plan')
        limit=.95 if plan.get('kind')=='space' else .65
        for box in boxes:
            if (not isinstance(box,list) or len(box)!=4 or any(type(n) not in (int,float) or not isfinite(n)
                    or not 0<=n<=SIZE for n in box) or not box[0]<box[2] or not box[1]<box[3]
                    or (box[2]-box[0])*(box[3]-box[1])>SIZE*SIZE*limit):raise ValueError('Invalid floor edit bounds')
        return [list(b) for b in boxes]
    if type(index) is not int or not 0<=index<4:raise ValueError('Invalid editing camera')
    pose=descriptor.get('viewpoint',{});cx,cz=pose.get('x',0),pose.get('z',0)
    yaw=edit_camera_yaw(descriptor,index)
    if yaw is None:yaw=CAMERAS[index][0]
    scale=tan(FOV*pi/360);boxes=[]
    from .guide import portal_geometry,guide_half_extent
    extent=guide_half_extent(descriptor)
    portals={p['id']:p for p in portal_geometry(descriptor)}
    for route in descriptor.get('environmentRoutePlan',{}).get(str(index),[]):
        direction=route.get('yaw')
        if type(direction) not in (int,float) or not isfinite(direction):continue
        tx,tz=sin(direction),-cos(direction)
        hx=((extent-cx)/tx if tx>1e-8 else (-extent-cx)/tx if tx< -1e-8 else float('inf'))
        hz=((extent-cz)/tz if tz>1e-8 else (-extent-cz)/tz if tz< -1e-8 else float('inf'))
        distance=min(hx,hz);px,pz=cx+tx*distance,cz+tz*distance
        if not isfinite(distance) or distance<=0:continue
        portal=portals.get(route.get('id'),{})
        span=portal.get('opening',.85)+.35
        points=[]
        for across in (-span,span):
            for inset in (0,1.5):
                x=px-tx*inset+(0 if hx<hz else across)
                z=pz-tz*inset+(across if hx<hz else 0)
                horizontal=(x-cx)*cos(yaw)+(z-cz)*sin(yaw)
                forward=(x-cx)*sin(yaw)-(z-cz)*cos(yaw)
                if forward<=0:continue
                for height in (0,4.5):
                    points.append((SIZE/2*(1+horizontal/forward/scale),SIZE/2*(1+(1.6-height)/forward/scale)))
        if not points:continue
        box=[max(26,round(min(p[0] for p in points))-16),
             max(26,round(min(p[1] for p in points))-16),
             min(486,round(max(p[0] for p in points))+16),
             min(486,round(max(p[1] for p in points))+16)]
        if box[0]<box[2] and box[1]<box[3]:boxes.append(box)
    return boxes


def protected_route_boxes(descriptor,index):
    """Project actual verified openings into this camera, retaining their floor."""
    from .inspection import camera_pixel,sphere_pixel
    boxes=[]
    for evidence in descriptor.get('protectedPassages',[]):
        aperture=evidence.get('apertureBoxPixels');view=evidence.get('viewIndex')
        near=evidence.get('surfaceReview',{}).get('nearPixels')
        if (type(view) is not int or view not in range(8) or not isinstance(aperture,list)
                or len(aperture)!=4 or not isinstance(near,list) or len(near)!=2):continue
        if any(type(v) not in (int,float) or not isfinite(v) or not 0<=v<=SIZE for v in [*aperture,*near]):continue
        left,top,right,bottom=aperture
        left=max(0,min(left,near[0]-24));right=min(SIZE,max(right,near[0]+24))
        top=max(0,min(top,near[1]-24));bottom=min(SIZE,max(bottom,near[1]+24))
        points=[]
        for i in range(9):
            for j in range(9):
                px,py=sphere_pixel(view,left+(right-left)*i/8,top+(bottom-top)*j/8,2048,1024)
                try:point=camera_pixel(index,px,py,2048,1024,camera_yaw=edit_camera_yaw(descriptor,index))
                except ValueError:continue
                points.append(point)
        if not points:continue
        box=[max(0,min(p[0] for p in points)-8),max(0,min(p[1] for p in points)-8),
             min(SIZE,max(p[0] for p in points)+8),min(SIZE,max(p[1] for p in points)+8)]
        if box[0]<box[2] and box[1]<box[3]:boxes.append(box)
    return boxes


def route_repair_reference(actual, structural, boxes, protected=None):
    """Replace only a failed portal's private input geometry before painting."""
    picture=Image.open(io.BytesIO(actual)).convert('RGB')
    guide=Image.open(io.BytesIO(structural)).convert('RGB').resize(picture.size,Image.Resampling.LANCZOS)
    mask=Image.new('L',picture.size);pixels=mask.load()
    for left,top,right,bottom in boxes:
        for y in range(top,bottom):
            for x in range(left,right):
                margin=min(x-left,right-1-x,y-top,bottom-1-y)
                if not any(a<=x<=c and b<=y<=d for a,b,c,d in (protected or [])):
                    pixels[x,y]=max(pixels[x,y],round(255*min(1,margin/8)))
    picture.paste(guide,(0,0),mask)
    output=io.BytesIO();picture.save(output,format='PNG');return output.getvalue()


def actor_crop(placement):
    """Actual camera crop around a measured slot; no detached body transforms."""
    left,top,right,bottom=placement['bodyBoxPixels']
    side=min(460,max(140,round((bottom-top)*1.35),round((right-left)*1.5)))
    x=max(4,min(508-side,round((left+right-side)/2)))
    y=max(4,min(508-side,round((top+bottom-side)/2)))
    return [x,y,x+side,y+side]


def camera_reference(source, index, placement=None,*,camera_yaw=None,allow_ground=False):
    if type(index) is not int or not 0<=index<(8 if allow_ground or placement and placement.get('cameraPitchVersion')==1 else 4):raise ValueError('Invalid editing camera')
    picture=Image.open(io.BytesIO(camera_images(source,include_ground=index>=4,labelled=False,camera_yaws={index:camera_yaw})[index])).convert('RGB')
    if placement and (placement.get('maskVersion')!=3 or placement.get('croppedGuideVersion')==1) and placement.get('pose') in ('standing','seated','kneeling'):
        # Private neutral anatomy guide at the measured empty space, never a
        # gameplay sprite. The provider repaints it using the actor reference.
        left,top,right,bottom=placement['bodyBoxPixels']
        width,height=right-left,bottom-top;centre=(left+right)/2
        leg_width=max(2,round(min(width*.12,height*.08)))
        measured_x,measured_y=placement['groundPointPixels']
        foot_y=measured_y-max(2,leg_width/2+1)
        separation=min(width*.24,max(6,height*.06))
        if measured_x>centre+width*.1:feet=(measured_x-separation,measured_x)
        elif measured_x<centre-width*.1:feet=(measured_x,measured_x+separation)
        else:feet=(measured_x-separation/2,measured_x+separation/2)
        draw=ImageDraw.Draw(picture)
        posed=placement['pose']!='standing'
        head=height*(.17 if posed else .13);shoulders=top+head*1.6;hip=top+height*(.61 if posed else .60)
        head_width=min(head*.68,width*.8)
        draw.ellipse((centre-head_width/2,top,centre+head_width/2,top+head),fill='#a99580')
        # One connected anatomical silhouette. A detached floating head above
        # the torso invited the editor to preserve it AND invent another head.
        neck=max(2,head_width*.30)
        draw.rectangle((centre-neck/2,top+head*.85,centre+neck/2,shoulders+1),fill='#a99580')
        draw.polygon([(centre-width*.24,shoulders),(centre+width*.24,shoulders),
                      (centre+width*.19,hip),(centre-width*.19,hip)],fill='#66716c')
        draw.line([(centre-width*.24,shoulders),(centre-width*.37,top+height*.58)],fill='#66716c',width=max(2,round(width*.10)))
        draw.line([(centre+width*.24,shoulders),(centre+width*.37,top+height*.58)],fill='#66716c',width=max(2,round(width*.10)))
        if placement['pose']=='seated':
            # Head, torso and bent knees all remain inside the same measured
            # adult region. A chair-only guide lets the editor paint the head
            # above the insertion mask, which is then irreversibly clipped.
            for side,foot_x in zip((-1,1),feet):
                hip_x=centre+side*width*.12
                knee=((hip_x+foot_x)/2+side*width*.08,top+height*.77)
                draw.line([(hip_x,hip),knee,(foot_x,foot_y)],fill='#545c57',width=leg_width)
        elif placement['pose']=='kneeling':
            for side,foot_x in zip((-1,1),feet):
                draw.line([(centre+side*width*.12,hip),(centre+side*width*.29,top+height*.87),
                           (foot_x,foot_y)],fill='#545c57',width=leg_width)
        else:
            draw.line([(centre-width*.12,hip),(feet[0],foot_y)],fill='#545c57',width=leg_width)
            draw.line([(centre+width*.12,hip),(feet[1],foot_y)],fill='#545c57',width=leg_width)
    if placement and placement.get('editCropPixels'):
        picture=picture.crop(tuple(placement['editCropPixels'])).resize((512,512),Image.Resampling.LANCZOS)
    output=io.BytesIO();picture.save(output,format='PNG');return output.getvalue()


def encode_camera(source):
    with Image.open(io.BytesIO(source)) as im:
        if im.format not in ('PNG','JPEG','WEBP') or im.width>8192 or im.height>8192 or im.width*im.height>32*1024*1024:
            raise ValueError('Invalid painted camera')
        im.load();im=im.convert('RGB');im=im.resize((1024,1024),Image.Resampling.LANCZOS)
        out=io.BytesIO();im.save(out,format='WEBP',quality=95)
        return out.getvalue(),{'width':1024,'height':1024,'mime':'image/webp','projection':'perspective-edit'}


def reproject_camera(source, edited, index, placement=None, repair_boxes=None, protected_boxes=None,*,camera_yaw=None,allow_ground=False,protected_panorama_boxes=()):
    """Use the inverse of the inspection camera, with a narrow edge taper."""
    if type(index) is not int or not 0<=index<(8 if allow_ground or placement and placement.get('cameraPitchVersion')==1 else 4):raise ValueError('Invalid editing camera')
    base=Image.open(io.BytesIO(source)).convert('RGB')
    view=Image.open(io.BytesIO(edited)).convert('RGB')
    if placement and placement.get('editCropPixels'):
        # Restore the entire painted camera crop at its original rays. Asking
        # for a tiny person in a full camera caused the editor to enlarge it;
        # clipping that full response to the old slot then erased heads/feet.
        # Crop-local painting bounds scale naturally and retain the whole body.
        crop=placement['editCropPixels']
        if crop!=actor_crop(placement):raise ValueError('Invalid actor crop')
        restored=Image.open(io.BytesIO(camera_images(source,include_ground=index>=4,labelled=False,camera_yaws={index:camera_yaw})[index])).convert('RGB')
        restored.paste(view.resize((crop[2]-crop[0],crop[3]-crop[1]),Image.Resampling.LANCZOS),(crop[0],crop[1]))
        view=restored
    width,height=base.size;yaw,pitch=CAMERAS[index];scale=tan(FOV*pi/360)
    if camera_yaw is not None:
        if type(camera_yaw) not in (int,float) or not isfinite(camera_yaw):raise ValueError('Invalid editing heading')
        yaw=camera_yaw
    def coordinates(x,y):
        longitude=(x/width-.5)*2*pi-yaw;latitude=(.5-y/height)*pi
        horizontal=sin(longitude)*cos(latitude)
        up=sin(latitude)*cos(pitch)-cos(longitude)*cos(latitude)*sin(pitch)
        forward=cos(longitude)*cos(latitude)*cos(pitch)+sin(latitude)*sin(pitch)
        if forward<=.01:return None
        u,v=horizontal/forward/scale,-up/forward/scale
        return ((u+1)*view.width/2,(v+1)*view.height/2,u,v)
    mesh=[]
    for y in range(0,height,16):
        for x in range(0,width,16):
            right,bottom=min(width,x+16),min(height,y+16)
            corners=[coordinates(a,b) for a,b in ((x,y),(x,bottom),(right,bottom),(right,y))]
            if all(corners) and all(abs(c[2])<1.3 and abs(c[3])<1.3 for c in corners):
                mesh.append(((x,y,right,bottom),tuple(v for c in corners for v in c[:2])))
    projected=view.transform(base.size,Image.Transform.MESH,mesh,Image.Resampling.BICUBIC)
    mw,mh=max(1,width//4),max(1,height//4);mask=Image.new('L',(mw,mh));pixels=mask.load()
    for y in range(mh):
        for x in range(mw):
            c=coordinates((x+.5)*width/mw,(y+.5)*height/mh)
            if c:
                margin=1-max(abs(c[2]),abs(c[3]))
                pixels[x,y]=round(255*max(0,min(1,margin/.10)))
    mask=mask.resize(base.size,Image.Resampling.BILINEAR)
    if placement:
        # The supplier cannot alter a wall picture, door or another person
        # outside the measured insertion region, even if its response does.
        left,top,right,bottom=placement['bodyBoxPixels']
        # Keep a bounded strip for new edits so contact shadows and shoes are
        # not erased during projection. Final pixels still require inspection.
        preserve=placement.get('surroundingsVersion')==1
        extra_foot=min(32,max(10,(bottom-top)*.10)) if placement.get('maskVersion')==2 or preserve else 10
        if placement.get('editCropPixels'):
            a,b,c,d=placement['editCropPixels']
            if preserve:
                # Keep wall art and architecture outside the actual body bay.
                # The wide crop provides context to the model; it is not
                # permission to copy a new face into every nearby wall frame.
                side=min(28,max(12,(right-left)*.25));head=min(28,max(12,(bottom-top)*.08))
                left,top,right,bottom=max(a+6,left-side),max(b+6,top-head),min(c-6,right+side),min(d-10,bottom)
            else:left,top,right,bottom=a+6,b+6,c-6,d-10
        pixels=mask.load()
        for y in range(height):
            for x in range(width):
                if not pixels[x,y]:continue
                c=coordinates(x+.5,y+.5)
                px,py=(c[0]*SIZE/view.width,c[1]*SIZE/view.height) if c else (-1,-1)
                margin=min(px-(left-6),(right+6)-px,py-(top-6),(bottom+extra_foot)-py)
                pixels[x,y]=round(pixels[x,y]*max(0,min(1,margin/6)))
                if preserve and any(a<=px<=c and b<=py<=d for a,b,c,d in (protected_boxes or [])):pixels[x,y]=0
                if any(a+shift<=x/width<=c+shift and b<=y/height<=d
                       for a,b,c,d in placement.get('protectedPanoramaBoxes',[]) for shift in (-1,0,1)):
                    pixels[x,y]=0
    elif repair_boxes is not None:
        # Even an editor that redesigns the whole input cannot modify a correct
        # neighbouring passage. Only painted pixels in the failed-route mask
        # enter this sphere; the private structural guide is never published.
        pixels=mask.load()
        for y in range(height):
            for x in range(width):
                if not pixels[x,y]:continue
                c=coordinates(x+.5,y+.5)
                px,py=(c[0]*SIZE/view.width,c[1]*SIZE/view.height) if c else (-1,-1)
                margin=max((min(px-left,right-px,py-top,bottom-py) for left,top,right,bottom in repair_boxes),default=-1)
                protected=(any(a<=px<=c and b<=py<=d for a,b,c,d in (protected_boxes or []))
                    or any(a+shift<=x/width<=c+shift and b<=y/height<=d
                        for a,b,c,d in protected_panorama_boxes for shift in (-1,0,1)))
                pixels[x,y]=0 if protected else round(pixels[x,y]*max(0,min(1,margin/8)))
    base.paste(projected,(0,0),mask)
    output=io.BytesIO();base.save(output,format='WEBP',lossless=True,quality=95)
    return output.getvalue(),{'width':width,'height':height,'mime':'image/webp',
        'projection':'equirectangular','cameraEdit':{'viewIndex':index,'fov':FOV,'edgeTaper':.10,
            **({'yaw':camera_yaw} if camera_yaw is not None else {}),
            **({'placementBox':placement['bodyBoxPixels'],'backgroundPreserved':True} if placement else {}),
            **({'repairBoxes':repair_boxes,'backgroundPreserved':True} if repair_boxes is not None else {})}}
