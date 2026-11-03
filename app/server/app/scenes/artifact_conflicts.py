"""A coordinate extractor's wall-art suspicion needs a bound pixel recheck."""
from copy import deepcopy
from math import isfinite


def suspects(result):
    detail=result.get('composition') if isinstance(result,dict) else None
    if not isinstance(detail,dict):return []
    score=detail.get('confidence');items=detail.get('referenceArtifacts')
    if (type(score) not in (int,float) or not isfinite(score) or not .85<=score<=1
            or not isinstance(items,list) or not items or not all(isinstance(p,str) and p.strip() for p in items)):return []
    return items


def request(panorama,descriptor,items,*,cameras=None):
    from .scene_review import request as scene_request
    value=scene_request(panorama,descriptor)
    value.update(inspectionMode='single-panorama-artifact-conflict-v1',candidateReferenceArtifacts=deepcopy(items))
    value['instruction']+=(' An earlier observation reported the candidateReferenceArtifacts. '
        'These are UNCONFIRMED hypotheses, not known defects. Re-examine their actual pixels in this same original texture. '
        'Distinguish an intentional framed oil painting integrated into the room (wall perspective, frame, brushwork and lighting) '
        'from a pasted reference photograph/sheet, guide rectangle or UI overlay. A human face inside a wall picture alone '
        'does not establish reference residue. Describe the actual picture and its physical integration in evidence. '
        'Keep genuine copied reference panels in referenceArtifacts. If the distinction is uncertain report uncertainty, '
        'not a confident clearance. Return the entire original output contract, including every physical person.')
    if cameras:
        import base64,re
        indices=sorted({int(n) for item in items for n in re.findall(r'(?:view|视角|视图)\s*([0-3])\b',item,re.I)}) or list(range(min(4,len(cameras))))
        value['instruction']=value['instruction'].replace('No rectilinear camera images or identity-reference pictures are supplied.',
            'Image 1 alone defines global projection. Later images are higher-detail actual camera views from this SAME texture, never identity references or extra people.')
        value['instruction']=value['instruction'].replace('a rectilinear camera: there are no such cameras here.',
            'the rectilinear detail cameras; judge only Image 1 for projection.')
        value['instruction']+=' Use the detail views to inspect the actual brushwork, frame and wall integration. All physical person headBox coordinates still refer to Image 1. Do not count a detail image as a second person or classify the spherical texture from a perspective detail.'
        for index in indices:
            if index>=len(cameras):continue
            value['_sceneImages'].append('data:image/jpeg;base64,'+base64.b64encode(cameras[index]).decode())
            value['imageRoles'].append({'image':len(value['_sceneImages']),'viewIndex':index,'role':'Actual detail of the SAME current texture; only inspect wall-art integration, not projection or a new person.'})
    return value


def cleared(result,cast,expected):
    from .scene_review import evaluate
    proof=result.get('referenceArtifactRecheck')
    if (not isinstance(proof,dict) or proof.get('version')!=1 or not expected.get('sceneImageHash')
            or proof.get('imageHash')!=expected['sceneImageHash'] or proof.get('candidates')!=suspects(result)):return False
    observed=evaluate(proof.get('observation'),cast,expected)
    return observed['verified'] and not observed['issues']
