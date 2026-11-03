"""Remeasure missing furniture contacts in one actual downward camera."""
import base64,json
from copy import deepcopy
from math import isfinite
from .inspection import panorama_coordinates
from .grounding import spatial_anchors_from_result


def inspect_fixtures(parsed,targets,cameras,image_size,call,limit):
    value=deepcopy(parsed);valid=spatial_anchors_from_result(panorama_coordinates(value,image_size),targets,image_size,require_evidence=True)
    found={key.split(':',1)[1] for key in valid if key.startswith('fixtures:')}
    desired={p['id']:p for p in targets['fixtures']}
    for index in range(4,8):
        remaining=[p for p in targets['fixtures'] if p['id'] not in found]
        if not remaining:break
        payload={'inspectionMode':'fixture-ground','camera':{'image':1,'viewIndex':index,'width':512,'height':512,'pitchDegrees':-35},
            'fixtures':remaining,'instruction':'Locate each requested ACTUAL fixture in this ONE received camera, then measure visible walkable FLOOR beside its base. Never put the contact on the countertop, bench seat, wooden panel, wall or a different object. A downward view exposes the nearby floor. Include exact ID/viewIndex, visible, anchorPixels [x,y], confidence and groundEvidence {surface:walkable-ground,evidence:actual visible floor and relationship to this fixture}. Return absent if the fixture or its adjacent floor is ambiguous. No invented objects or world changes.',
            'outputContract':{'fixtures':'array of exact requested IDs, visible boolean, this viewIndex integer, anchorPixels numeric [x,y], confidence number, groundEvidence object with surface and observed evidence'},
            '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(cameras[index]).decode()],
            '_structured':True,'_visionStream':True}
        result=call('analysis:fixture-view:'+str(index),'world_scene_fixtures',payload,limit)
        try:parts=json.loads(result.get('text','')).get('fixtures',[])
        except (ValueError,TypeError,AttributeError):parts=[]
        for part in parts if isinstance(parts,list) else []:
            if not isinstance(part,dict) or part.get('id') not in desired or part['id'] in found or part.get('viewIndex')!=index:continue
            proof=part.get('groundEvidence');confidence=part.get('confidence')
            if (not isinstance(proof,dict) or proof.get('surface')!='walkable-ground'
                    or not isinstance(proof.get('evidence'),str) or len(proof['evidence'].strip())<15
                    or type(confidence) not in (int,float) or not isfinite(confidence) or not .85<=confidence<=1):continue
            single=spatial_anchors_from_result(panorama_coordinates({'fixtures':[part]},image_size),
                {'fixtures':[desired[part['id']]],'exits':[]},image_size,require_evidence=True)
            if 'fixtures:'+part['id'] not in single:continue
            old=value.get('fixtures',[]);old=old if isinstance(old,list) else []
            value['fixtures']=[p for p in old if not isinstance(p,dict) or p.get('id')!=part['id']]+[
                {**part,'fixtureReview':{'version':1,'source':'single-camera-floor-contact','viewIndex':index}}]
            found.add(part['id'])
    return value
