"""Explicit normalized XY contract; never guess units from old coordinates."""
from copy import deepcopy
import json
from math import isfinite,tan,pi

SYSTEM='normalized-1000-xy-v1'
PIXELS='pixels-xy-v1'
KEYS=frozenset(('headBoxPixels','bodyBoxPixels','groundPointPixels','anchorPixels','beyondPointPixels',
               'throughPointPixels','thresholdBoxPixels','apertureBoxPixels','boxPixels'))


def request(data,*,crop_pixels=False):
    value=deepcopy(data)
    size=data.get('imageSize',{}) if data.get('inspectionMode')=='head-crop' else {'width':512,'height':512}
    width,height=size.get('width',512),size.get('height',512)
    value['coordinateProtocol']={'system':SYSTEM,'outputRequired':'coordinateSystem: '+SYSTEM,
        'frameWidth':width,'frameHeight':height,
        'rule':'For ALL output point/box arrays, including keys ending Pixels, use normalized 0..1000 XY coordinates: left/top/right/bottom or x/y. x=1000 is the RIGHT image edge, y=1000 is the BOTTOM edge. These explicit output units override pixel units in examples. Input constraints keep their explicitly declared units and actual image bounds; convert your measured OUTPUT to normalized units. Never swap XY, infer missing evidence or copy requested positions. Include coordinateSystem at the OUTER JSON root.'}
    if crop_pixels and data.get('inspectionMode')=='head-crop':
        value['coordinateProtocol']={'system':PIXELS,'outputRequired':'coordinateSystem: '+PIXELS,
            'frameWidth':width,'frameHeight':height,
            'rule':f'Output headBoxPixels in ACTUAL CROP PIXELS: x=0..{width}, y=0..{height}. Do NOT normalize to 1000 or supply a second normalized box. Include coordinateSystem:"pixels-xy-v1" at the OUTER JSON root. No reference to the previous camera coordinates.'}
        value['measurement']='Independently inspect Image 1, the actual crop at imageSize. Verify the complete physical head, hair through chin, identity, and tight boundaries. Output headBoxPixels in these actual crop pixels ONLY, never normalized values or the full crop rectangle.'
        value['outputContract']={**value.get('outputContract',{}),'headBoxPixels':f'numeric [left,top,right,bottom] actual crop pixels, x<= {width}, y<= {height}'}
    if data.get('inspectionMode')=='person-head':
        value['outputContract']={**value.get('outputContract',{}),
            'headBoxPixels':'numeric [left,top,right,bottom] in normalized 0..1000 XY, tight actual complete head'}
    if data.get('inspectionMode')=='single-person':
        value['outputContract']={'coordinateSystem':SYSTEM,**value.get('outputContract',{})}
        value['requiredOutputKeys']=['coordinateSystem','entityId','visible','physicalPerson','confidence']
        value['measurementContract']=('Return the exact requested entityId and coordinateSystem at the OUTER JSON root. '
            'ALL point/box arrays use normalized 0..1000 XY, including nested scaleEvidence and supportingFixture. '
            'When visible, independently measure complete head/body and actual support. A scale reference must be '
            'real furniture or a doorway, never the person\'s own body rectangle. Use visible:false when uncertain. '
            'A missing coordinate tag or unmeasured reference cannot authorize an interaction.')
    if data.get('inspectionMode')=='single-camera' and isinstance(data.get('routes'),list):
        value['coordinateContract']=('ALL output boxes and points use finite normalized 0..1000 XY in this ONE actual image, '
            'following coordinateProtocol. Return image=1. Never use another camera or panorama coordinates. '
            'Every required cue needs its own measured box; repeated features need DISTINCT actual boxes, never duplicate measurements.')
        value['instruction']=('Exactly ONE attached image at the declared imageSize. ALL output coordinates are normalized '
            '0..1000 XY for this image. Return image=1 for all findings. Search ONLY these actual pixels. '
            'If a destination landmark is absent, report matched=false; other cameras are inspected separately.')
        # Preserve the exact original 512-frame physical distances. The wire
        # contract and coordinate arrays now use the same normalized units.
        for key in ('anchorContract','openingContract'):
            text=value.get(key)
            if isinstance(text,str):
                for pixels in (5,30,60):text=text.replace(f'{pixels} pixels',f'{pixels*1000/512:.10g} normalized units')
                value[key]='All distances below are normalized 0..1000 camera units. '+text
        for route in value.get('responseShape',{}).get('routes',[]):
            text=route.get('throughPointPixels')
            if isinstance(text,str):route['throughPointPixels']=text.replace('5px','9.765625 normalized units')
    if data.get('inspectionMode')=='actor-placement':
        # Give one unit system throughout this focused request. Conflicting
        # pixel examples made tagged replies retain pixel-sized body boxes.
        horizon=500+500*tan(data.get('camera',{}).get('pitchDegrees',0)*pi/180)/tan(50*pi/180)
        value['measurement']=f'Measure this ONE actual camera at the supplied pitch. All OUTPUT coordinates are normalized 0..1000 XY, including fields named Pixels. The eye-height horizon in THIS view is y={horizon:g}. Complete bodies must fit inside x/y=51..949, width 59..469, height 195..898. Return only placements for remaining exact futureCast IDs, or an empty array if none fit. Never copy a coordinate example.'
        value['poseAndFloorContract']=f'Standing adult head starts ABOVE y={horizon:g}; seated/kneeling adult head starts BELOW y={horizon-12:g}. Measure a real floor contact at the bottom of the proposed body, within 24 normalized units of its bottom. A seated pelvis needs an actual chair/bench while shoes rest on the floor. No person on a counter, shelf or chair seat. Do not overlap a doorway, existing person or another placement. Preserve the requested pose and actual adult scale.'
        if data.get('supportWillBeIndependentlyVerified'):
            value['poseAndFloorContract']='Measure actual adult scale/depth and the requested pose against visible furniture. There is no universal head-height cutoff: the original generated camera height and the seated adult height can vary. A separate actual-pixel review MUST confirm seat/floor and scale before painting. Feet must be on real visible floor, near the body bottom (within 24 normalized units). A seated pelvis needs an actual chair/bench/sofa, never a counter or shelf. Foreground furniture may naturally occlude part of the body when depth agrees. Do not block an actual open doorway or overlap another person.'
        heights=value.pop('readableBodyHeightPixels',None)
        if heights:
            value['readableCharacterContract']=(f'Choose an actual nearby supported position where the full standing body height is at least {heights["standing"]*1000/512:.10g}, '
                f'or seated/kneeling height at least {heights["posed"]*1000/512:.10g}, in normalized 0..1000 camera units. '
                'A tiny distant body through a doorway cannot support a readable face. Do not artificially enlarge a distant '
                'proposal; choose a physically supported nearer position, or return no proposal.')
        value['outputContract']={'coordinateSystem':SYSTEM,'placements':'JSON array of objects: entityId (exact requested string), viewIndex (this camera integer), bodyBoxPixels (numeric [left,top,right,bottom] normalized 0..1000), groundPointPixels (numeric [x,y] normalized 0..1000), pose, confidence (number 0.8..1), evidence (observed support and relative adult scale).'}
        value['outputSchema']={'type':'object','required':['coordinateSystem','placements'],
            'properties':{'coordinateSystem':{'const':SYSTEM},'placements':{'type':'array','items':{
                'type':'object','required':['entityId','viewIndex','bodyBoxPixels','groundPointPixels','pose','confidence','evidence'],
                'properties':{'entityId':{'enum':[p['entityId'] for p in data['futureCast']]},
                    'viewIndex':{'const':data['camera']['viewIndex']},
                    'bodyBoxPixels':{'type':'array','items':{'type':'number','minimum':51,'maximum':949},'minItems':4,'maxItems':4},
                    'groundPointPixels':{'type':'array','items':{'type':'number','minimum':0,'maximum':1000},'minItems':2,'maxItems':2},
                    'pose':{'enum':['standing','seated','kneeling']},'confidence':{'type':'number','minimum':.8,'maximum':1},'evidence':{'type':'string'}}}}}}
    return value


def response(result,data):
    try:value=json.loads(result.get('text',''))
    except (ValueError,TypeError):return result
    if not isinstance(value,dict):return result
    frame=data['coordinateProtocol'];tag=value.get('coordinateSystem');expected=frame['system']
    def convert(obj):
        if isinstance(obj,list):return [convert(v) for v in obj]
        if not isinstance(obj,dict):return obj
        output={}
        for key,item in obj.items():
            if key not in KEYS:output[key]=convert(item);continue
            if (tag!=expected or not isinstance(item,list) or len(item) not in (2,4)
                    or any(type(n) not in (int,float) or not isfinite(n)
                        or not 0<=n<=(1000 if expected==SYSTEM else frame['frameWidth'] if i%2==0 else frame['frameHeight']) for i,n in enumerate(item))):
                output[key]=None;continue
            output[key]=([n*(frame['frameWidth'] if i%2==0 else frame['frameHeight'])/1000 for i,n in enumerate(item)]
                if expected==SYSTEM else list(item))
        return output
    converted=convert(value)
    converted['coordinateSystem']=PIXELS if tag==expected else 'unconfirmed'
    converted['coordinateEvidence']={'sourceSystem':tag,'frameWidth':frame['frameWidth'],'frameHeight':frame['frameHeight'],'converted':tag==expected and expected==SYSTEM,'validatedUnits':tag==expected}
    return {**result,'text':json.dumps(converted,ensure_ascii=False)}
