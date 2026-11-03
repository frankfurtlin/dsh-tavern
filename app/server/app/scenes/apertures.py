"""Measured native YX opening boxes propose points; blind pixels must confirm."""
import base64,json
from copy import deepcopy
from math import isfinite

SYSTEM='normalized-1000-yx-v1'


def request(targets,camera,*,native_size=512,measure_points=False,rejected_points=()):
    if native_size==1000:
        from .coordinate_cameras import camera_image
        camera=camera_image(camera)
    payload={'inspectionMode':'native-aperture','coordinateSystem':SYSTEM,
        'routes':[{k:p[k] for k in ('id','name','visualCue','allowedPassageKinds') if k in p} for p in targets],
        'instruction':'Detect the NEAREST physical opening leaving this CURRENT interior room, not a second gate across the courtyard. Measure the entire open area inside its enclosing frame, top through floor threshold. Use native box_2d [ymin,xmin,ymax,xmax] normalized 0..1000. A floor patch, furniture gap, window, closed door or picture is not the requested opening. Return matched:false if uncertain. Do not propose walking points; a separate blind pixel check will test server-proposed points.',
        'outputSchema':{'type':'object','required':['coordinateSystem','routes'],
            'properties':{'coordinateSystem':{'const':SYSTEM},'routes':{'type':'array','items':{
                'type':'object','required':['id','matched','kind','box_2d','confidence','evidence'],
                'properties':{'id':{'enum':[p['id'] for p in targets]},'matched':{'type':'boolean'},
                    'kind':{'enum':['door-open','stairs-open']},
                    'box_2d':{'type':['array','null'],'items':{'type':'number','minimum':0,'maximum':1000},'minItems':4,'maxItems':4},
                    'confidence':{'type':'number','minimum':0,'maximum':1},'evidence':{'type':'string'}}}}}},
        '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(camera).decode()], '_structured':True,'_visionStream':True}
    if measure_points:
        payload['instruction']=payload['instruction'].replace('Do not propose walking points; a separate blind pixel check will test server-proposed points.',
            'Also MEASURE three actual walking-floor points in this SAME image, each [y,x] normalized 0..1000: '
            'near on visible approach floor BEFORE the threshold, beyond on visible floor AFTER the threshold, '
            'through deeper on that SAME connected floor. Do not place near on the vertical face of a raised sill, '
            'door jamb, wall or furniture. Horizontal walkable stair/threshold tops differ from vertical risers. '
            'Keep near at least 32 normalized units BELOW box_2d ymax (the threshold), away from its thin border; '
            'measure actual floor there, never invent floor if it is occluded. Keep beyond and through on actual '
            'floor inside the opening and separated in depth, rather than on threshold edges. '
            'All points must be visible inside this image, with near lower in the image than beyond and through. '
            'Do not infer hidden ground, ignore a railing or certify a path from the open door alone. '
            'Use walkPoints_yx:null when no such points can be measured. These are only hypotheses: a separate '
            'blind exchange must verify their exact original pixels and actual doorway crossing.')
        item=payload['outputSchema']['properties']['routes']['items']
        item['required'].append('walkPoints_yx')
        item['properties']['walkPoints_yx']={'type':['object','null'],'required':['near','beyond','through'],
            'properties':{name:{'type':'array','items':{'type':'number','minimum':0,'maximum':999},'minItems':2,'maxItems':2}
                          for name in ('near','beyond','through')}}
    if rejected_points:
        payload['rejectedSameImagePointMeasurements']=deepcopy(list(rejected_points))
        payload['correctionInstruction']='Earlier proposals failed geometric bounds or independent inspection of the SAME received pixels; inspect each recorded reason. Measure the actual visible approach floor, threshold and beyond floor anew. Do not copy rejected points or call a wall/vertical riser floor; do not assume the requested opening is traversable.'
    return payload


def candidates(result,targets,view,*,measure_points=False,fallback_on_geometry=False):
    if not isinstance(result,dict) or result.get('coordinateSystem')!=SYSTEM:return {'exits':[]}
    allowed={p['id']:p for p in targets};parts=[];rejected=[]
    for answer in result.get('routes',[]) if isinstance(result.get('routes'),list) else []:
        if not isinstance(answer,dict) or answer.get('id') not in allowed:continue
        confidence=answer.get('confidence');box=answer.get('box_2d');target=allowed[answer['id']]
        # A JSON-encoded numeric array has the same explicit units as an
        # ordinary array. Decode syntax only; never infer missing coordinate tags.
        if isinstance(box,str):
            try:box=json.loads(box)
            except (ValueError,TypeError):continue
        if (answer.get('matched') is not True or answer.get('kind') not in target.get('allowedPassageKinds',[])
                or type(confidence) not in (int,float) or not isfinite(confidence) or not .85<=confidence<=1
                or not isinstance(box,list) or len(box)!=4 or any(type(v) not in (int,float) or not isfinite(v) or not 0<=v<=1000 for v in box)
                or not isinstance(answer.get('evidence'),str) or len(answer['evidence'].strip())<10):continue
        top,left,bottom,right=[v*512/1000 for v in box]
        if right-left<24 or bottom-top<60:continue
        x=(left+right)/2;height=bottom-top
        near=[x,min(511,bottom+8)];far=[x,bottom-min(40,height*.10)];through=[x,bottom-min(80,height*.20)]
        aperture=[left,top,right,bottom]
        measured=answer.get('walkPoints_yx') if measure_points else None
        fallback=False
        if measured is not None:
            if (not isinstance(measured,dict) or set(measured)!={'near','beyond','through'}
                    or any(not isinstance(p,list) or len(p)!=2 or any(type(n) not in (int,float)
                        or not isfinite(n) or not 0<=n<1000 for n in p) for p in measured.values())):continue
            near,far,through=([measured[key][1]*512/1000,measured[key][0]*512/1000]
                              for key in ('near','beyond','through'))
            from .surfaces import opening_geometry
            if near[1]<bottom+12 or not opening_geometry({'apertureBoxPixels':aperture,'throughPointPixels':through},near,far):
                rejected.append({'id':answer['id'],'viewIndex':view,'nearPixels':near,'farPixels':far,
                    'throughPointPixels':through,'apertureBoxPixels':aperture,
                    'proposalRejection':'Point order, opening bounds or threshold margin failed. Approach must be below the threshold, with beyond and through inside this same opening in increasing depth. This is a geometric rejection, not a surface observation.'})
                if not fallback_on_geometry:continue
                fallback=True
        if fallback_on_geometry and (fallback or measured is None):
            # This is a NEW hypothesis from a measured opening, never a floor
            # verdict. The blind exact-pixel crossing check remains mandatory.
            # Do not clamp a cropped threshold into the frame or repair syntax.
            near=[x,bottom+16];far=[x,bottom-min(40,height*.10)];through=[x,bottom-min(80,height*.20)]
            from .surfaces import opening_geometry
            if (near[1]>=511 or not opening_geometry({'apertureBoxPixels':aperture,
                    'throughPointPixels':through},near,far)):continue
            fallback=True
        parts.append({'id':answer['id'],'visible':True,'passable':False,'confidence':confidence,
            'viewIndex':view,'anchorPixels':near,'requiresBlindApertureCheck':True,
            'passageEvidence':{'kind':answer['kind'],'viewIndex':view,'boxPixels':aperture,
                'apertureBoxPixels':aperture,'throughPointPixels':through,'description':answer['evidence'],
                'visualCueMatched':True,'cueEvidence':answer['evidence'],
                'pointProposal':{'source':'measured-opening-box-hypothesis-v1' if fallback else 'measured-native-opening-points' if measured is not None else 'measured-native-opening-box',
                                 'coordinateSystem':SYSTEM,'box_2d':box,
                                 **({'rejectedWalkPoints_yx' if fallback else 'walkPoints_yx':measured} if measured is not None else {})},
                'independentReview':{'matched':False,'continuous':False,'confidence':confidence,
                    'beyondPointPixels':far,'source':'aperture-candidate','status':'needs-blind-pixels'}}})
    return {'exits':parts,**({'rejectedPointProposals':rejected} if rejected else {})}
