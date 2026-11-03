"""Undistorted camera views for examining the actual spherical image.

The sheet is an analysis input, never a replacement picture or a game sprite.
Every detected pixel maps back to the original panorama by the same projection.
"""
import io
from copy import deepcopy
from math import atan2, cos, isfinite, pi, sin, sqrt, tan
from PIL import Image, ImageDraw

SIZE = 512
FOV = 100
CENTRES = (0, pi / 2, pi, -pi / 2)
CAMERAS = tuple((yaw, 0) for yaw in CENTRES) + tuple((yaw, -35*pi/180) for yaw in CENTRES)


def sphere_pixel(index, x, y, width, height,*,camera_yaw=None):
    if type(index) is not int or not 0 <= index < len(CAMERAS):
        raise ValueError('Invalid camera view')
    if not all(type(v) in (int, float) and isfinite(v) and 0 <= v <= SIZE for v in (x, y)):
        raise ValueError('Invalid camera pixel')
    scale = tan(FOV * pi / 360)
    horizontal, vertical = (2 * x / SIZE - 1) * scale, (1 - 2 * y / SIZE) * scale
    yaw, pitch = CAMERAS[index]
    if camera_yaw is not None:
        if type(camera_yaw) not in (int,float) or not isfinite(camera_yaw):raise ValueError('Invalid camera heading')
        yaw=camera_yaw
    up = vertical*cos(pitch) + sin(pitch)
    forward = cos(pitch) - vertical*sin(pitch)
    longitude = yaw + atan2(horizontal, forward)
    latitude = atan2(up, sqrt(forward*forward + horizontal*horizontal))
    return (.5 + longitude / (2 * pi)) * width, (.5 - latitude / pi) * height


def camera_pixel(index, x, y, width, height,*,camera_yaw=None):
    """Map a measured spherical point into another rectilinear camera.

    A spherical bounding rectangle contains areas outside the original camera
    polygon. Contact containment must use this inverse projection, especially
    when the support and contact were measured in overlapping views.
    """
    if type(index) is not int or not 0<=index<len(CAMERAS):
        raise ValueError('Invalid camera view')
    if not all(type(v) in (int,float) and isfinite(v) for v in (x,y)) or not 0<=y<=height:
        raise ValueError('Invalid spherical pixel')
    longitude=(x/width-.5)*2*pi;latitude=(.5-y/height)*pi
    yaw,pitch=CAMERAS[index]
    if camera_yaw is not None:
        if type(camera_yaw) not in (int,float) or not isfinite(camera_yaw):raise ValueError('Invalid camera heading')
        yaw=camera_yaw
    horizontal=cos(latitude)*sin(longitude-yaw)
    vertical=sin(latitude);forward=cos(latitude)*cos(longitude-yaw)
    camera_vertical=vertical*cos(pitch)-forward*sin(pitch)
    camera_forward=vertical*sin(pitch)+forward*cos(pitch)
    if camera_forward<=1e-8:raise ValueError('Point behind camera')
    scale=tan(FOV*pi/360)
    return (1+horizontal/(camera_forward*scale))*SIZE/2,(1-camera_vertical/(camera_forward*scale))*SIZE/2


def camera_images(source, *, include_ground=False, labelled=True,camera_yaws=None,render_size=SIZE,view_indices=None):
    """Separate camera images make local pixel coordinates unambiguous."""
    if type(render_size) is not int or render_size not in (SIZE,1024):raise ValueError('Unsupported inspection size')
    indices=tuple(range(len(CAMERAS) if include_ground else 4)) if view_indices is None else tuple(view_indices)
    if any(type(i) is not int or not 0<=i<len(CAMERAS) for i in indices):raise ValueError('Unknown actual camera')
    with Image.open(io.BytesIO(source)) as image:
        original = image.convert('RGB')
    width, height = original.size
    wrapped = Image.new('RGB', (width * 3, height))
    for offset in range(3):
        wrapped.paste(original, (offset * width, 0))
    result=[]
    for index in indices:
        mesh = []
        scale=SIZE/render_size
        for y in range(0, render_size, 32):
            for x in range(0, render_size, 32):
                corners = [sphere_pixel(index, a*scale, b*scale, width, height,camera_yaw=(camera_yaws or {}).get(index))
                           for a, b in ((x, y), (x, y + 32), (x + 32, y + 32), (x + 32, y))]
                mesh.append(((x, y, x + 32, y + 32), tuple(v for a, b in corners for v in (a + width, b))))
        tile = wrapped.transform((render_size, render_size), Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)
        if labelled:
            draw = ImageDraw.Draw(tile)
            draw.rectangle((0, 0, 90, 22), fill='#15231f')
            draw.text((6, 5), f'VIEW {index}', fill='white')
        out=io.BytesIO();tile.save(out,format='JPEG',quality=92 if render_size>SIZE else 80);result.append(out.getvalue())
    return result


def detail_camera(source,index):
    """Sample finer original panorama pixels, preserving the exact camera rays."""
    return camera_images(source,labelled=False,render_size=1024,view_indices=(index,))[0]


def contact_sheet(source):
    """Contact sheet for offline visual review, not model coordinate input."""
    sheet = Image.new('RGB', (SIZE * 2, SIZE * 2))
    for index,data in enumerate(camera_images(source)):
        with Image.open(io.BytesIO(data)) as tile:
            sheet.paste(tile, ((index % 2) * SIZE, (index // 2) * SIZE))
    out = io.BytesIO()
    for quality in (80, 65, 50, 35):
        out.seek(0); out.truncate()
        sheet.save(out, format='JPEG', quality=quality)
        if len(out.getvalue()) <= 240000:
            return out.getvalue()
    raise ValueError('Inspection sheet too large')


def region_pixels(view,box,width,height):
    # Latitude is nonlinear across a rectilinear camera. Its extrema may be
    # at the optical axis INSIDE an edge, not at the four corners.
    xs=(box[0],box[2],max(box[0],min(SIZE/2,box[2])))
    points=[sphere_pixel(view,x,y,width,height) for x in xs for y in (box[1],box[3])]
    return [min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points)]


def panorama_coordinates(result, image_size):
    """Convert explicit camera-local pixels without accepting guessed views."""
    if not isinstance(result, dict):
        return result
    converted = deepcopy(result)
    width, height = image_size
    for kind in ('characters', 'fixtures', 'exits'):
        parts = converted.get(kind)
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or 'viewIndex' not in part:
                continue
            # Explicit absence has no physical coordinates to project. Supplier
            # placeholder zero boxes must not turn a proven missing person into
            # a malformed measurement that blocks the bounded painting repair.
            if part.get('visible') is False:continue
            if kind=='exits' and isinstance(part.get('passageEvidence'),dict):
                evidence=part['passageEvidence']
                # Some complete supplier receipts put the measured cue beside
                # passageEvidence instead of inside it. Normalize that shape
                # without adding evidence or overriding an explicit mismatch.
                if part.get('visualCueMatched') is False or evidence.get('visualCueMatched') is False:
                    evidence['visualCueMatched']=False
                elif 'visualCueMatched' not in evidence and type(part.get('visualCueMatched')) is bool:
                    evidence['visualCueMatched']=part['visualCueMatched']
                if not isinstance(evidence.get('cueEvidence'),str) and isinstance(part.get('cueEvidence'),str):
                    evidence['cueEvidence']=part['cueEvidence']
            try:
                if kind == 'characters':
                    box = part['headBoxPixels']
                    if not isinstance(box, list) or len(box) != 4 or not box[0] < box[2] or not box[1] < box[3]:
                        raise ValueError('Invalid head box')
                    part['cameraHeadBoxPixels'] = list(box)
                    # Unwrapped coordinates retain a face straddling the seam.
                    part['headBoxPixels'] = region_pixels(part['viewIndex'],box,width,height)
                    part['sphericalView'] = True
                    contact=part.get('groundPointPixels')
                    if contact is not None:
                        if not isinstance(contact,list) or len(contact)!=2:raise ValueError('Invalid contact pixel')
                        part['cameraGroundPointPixels']=list(contact)
                        gx,gy=sphere_pixel(part.get('groundViewIndex',part['viewIndex']),*contact,width,height)
                        part['groundPointPixels']=[gx%width,gy]
                    body=part.get('bodyBoxPixels')
                    if body is not None:
                        if not isinstance(body,list) or len(body)!=4 or not body[0]<body[2] or not body[1]<body[3]:
                            raise ValueError('Invalid body box')
                        body_view=part.get('bodyViewIndex',part['viewIndex'])
                        part['cameraBodyBoxPixels']=list(body)
                        part['bodyBoxPixels']=region_pixels(body_view,body,width,height)
                    support=part.get('supportingFixture')
                    if support is not None:
                        sb=support['boxPixels'];sv=support['viewIndex']
                        if not isinstance(sb,list) or len(sb)!=4 or not sb[0]<sb[2] or not sb[1]<sb[3]:raise ValueError('Invalid support box')
                        support['panoramaBoxPixels']=region_pixels(sv,sb,width,height)
                else:
                    pixel = part['anchorPixels']
                    if not isinstance(pixel, list) or len(pixel) != 2:
                        raise ValueError('Invalid ground pixel')
                    part['cameraAnchorPixels']=list(pixel)
                    x, y = sphere_pixel(part['viewIndex'], *pixel, width, height)
                    part['anchorPixels'] = [x % width, y]
            except (ValueError, TypeError, KeyError, IndexError):
                part['rejectionReason']='pixel-space-out-of-range'
                part['visible'] = False
                part['confidence'] = 0
    return converted
