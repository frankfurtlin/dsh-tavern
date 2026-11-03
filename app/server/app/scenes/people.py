"""Recover missed people by measuring one actual camera at a time."""
import base64,json
from copy import deepcopy
from math import isfinite
from .heads import measured_camera,box_valid


def parsed(result):
    try:value=json.loads(result.get('text',''))
    except (ValueError,TypeError):return {}
    return value if isinstance(value,dict) else {}


def same_head(left,right):
    """Actual camera head regions, including wrap-seam and overlapping views."""
    from .inspection import sphere_pixel
    def region(part):
        box=part.get('headBoxPixels');index=part.get('viewIndex')
        if not box_valid(box) or type(index) is not int or not 0<=index<8:return None
        points=[sphere_pixel(index,x,y,2048,1024) for x in (box[0],box[2]) for y in (box[1],box[3])]
        return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
    a,b=region(left),region(right)
    if a is None or b is None:return False
    for shift in (-2048,0,2048):
        overlap=max(0,min(a[2],b[2]+shift)-max(a[0],b[0]+shift))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        area=min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1]))
        if area>0 and overlap/area>=.7:return True
    return False


def inspect_people(value,cast,cameras,call,limit,*,retained_ids=(),view_indices=None,recount=True,parallel=False,stage_ordinals=None):
    if parallel and len(cast)>1 and not recount:
        from .parallel import person_reads
        ordinals={person['entityId']:index for index,person in enumerate(cast)}
        return person_reads(lambda parsed,people,progress:inspect_people(parsed,people,cameras,call,limit,
            retained_ids=retained_ids,view_indices=view_indices,recount=False,stage_ordinals=ordinals),value,cast)
    result=deepcopy(value);parts=result.get('characters',[])
    if not isinstance(parts,list):parts=[]
    parts=[p for p in parts if isinstance(p,dict)]
    desired={p['entityId']:p for p in cast}
    def pose_matches(part,person):
        return not person.get('expectedPose') or part.get('pose')==person['expectedPose']
    def claimed(part,ident):
        return any(other.get('entityId')!=ident and other.get('entityId') in desired
            and other.get('visible') is True and other.get('physicalPerson') is True
            and pose_matches(other,desired[other['entityId']]) and same_head(part,other) for other in parts)
    changed=False
    for ordinal,person in enumerate(cast):
        ordinal=(stage_ordinals or {}).get(person['entityId'],ordinal)
        old=next((p for p in parts if p.get('entityId')==person['entityId']),{})
        if person['entityId'] in retained_ids:continue
        if (old.get('visible') is True and old.get('physicalPerson') is True and pose_matches(old,person)
                and type(old.get('viewIndex')) is int and 0<=old['viewIndex']<8 and not claimed(old,person['entityId'])):continue
        indices=range(min(4,len(cameras))) if view_indices is None else view_indices
        for index in indices:
            camera=cameras[index]
            data={'inspectionMode':'single-person','person':{k:v for k,v in person.items() if k in ('entityId','name','description','appearance','expectedPose')},
                'identitySearch':'Current expectedPose is a search clue, never visual proof. Independently measure the actual pose. Do not report a standing person as a seated character simply because their clothing or face is similar. Return absent for a different physical person.',
                'camera':{'viewIndex':index,'width':512,'height':512},
                'outputContract':{'entityId':person['entityId'],'visible':'boolean','physicalPerson':'boolean','confidence':'0..1',
                    'headBoxPixels':'[left,top,right,bottom], actual complete head only',
                    'bodyBoxPixels':'actual body rectangle including head',
                    'pose':'standing/seated/kneeling','naturalScale':'boolean','scaleConfidence':'0..1',
                    'scaleEvidence':{'referenceKind':'door/furniture','viewIndex':index,'boxPixels':'actual reference rectangle','depthRelation':'same-depth/in-front/behind','description':'actual relative scale'},
                    'groundPointPixels':'actual shoe/floor contact, or actual seated supporting contact','grounding':'floor/fixture/floating','groundConfidence':'0..1',
                    'supportingFixture':'if fixture, {kind:chair/bench/counter/sofa,viewIndex,boxPixels,confidence}'},
                '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(measured_camera(camera)).decode()],
                '_structured':True,'_visionStream':True}
            part=parsed(call(f'analysis:person:{ordinal}:{index}','world_scene_people',data,limit))
            confidence=part.get('confidence')
            if (part.get('entityId')!=person['entityId'] or part.get('visible') is not True or part.get('physicalPerson') is not True
                    or not box_valid(part.get('headBoxPixels')) or type(confidence) not in (int,float) or not isfinite(confidence) or confidence<.85
                    or not pose_matches(part,person)):continue
            part.update(viewIndex=index,bodyViewIndex=index,groundViewIndex=index)
            if claimed(part,person['entityId']):continue
            parts=[p for p in parts if p.get('entityId')!=person['entityId']]+[part];changed=True;break
    result['characters']=parts
    if changed and recount:
        data={'inspectionMode':'composition-recount','expectedIds':[p['entityId'] for p in cast],
            'images':'Four actual horizontal cameras of the SAME sphere; count overlapping people once, exclude wall paintings.',
            'outputContract':{'visiblePeopleCount':'all physical people, integer','unexpectedPeople':'array of unmatched descriptions'},
            '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(c).decode() for c in cameras[:4]],'_structured':True,'_visionStream':True}
        count=parsed(call('analysis:person-recount','world_scene_people',data,limit))
        # A separate whole-cast observation supplies the count. Never infer
        # "no strangers" merely by summing individually matched people.
        observed=result.get('composition',{})
        observed.update({key:count.get(key) for key in ('visiblePeopleCount','unexpectedPeople')})
        result['composition']=observed
        result['peopleRecount']={key:count.get(key) for key in ('visiblePeopleCount','unexpectedPeople')}
    return result
