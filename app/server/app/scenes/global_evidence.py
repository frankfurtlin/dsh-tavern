"""A bounded new observation of uncertain global evidence, never a verdict override."""
from copy import deepcopy
import json


def needs_detail(verdict):
    return (isinstance(verdict,dict) and bool(verdict.get('issues'))
            and set(verdict['issues'])<={'scene-review-unconfirmed','visual-facts-unconfirmed'})


def request(panorama,descriptor,cameras):
    from .scene_review import request as base
    import base64
    value=base(panorama,descriptor)
    value['inspectionMode']='single-panorama-context-detail-v1'
    value['imageRoles'] += [{'image':i+2,'role':'actual camera from the SAME panorama, for detail only','viewIndex':i} for i in range(min(4,len(cameras)))]
    value['instruction']=value['instruction'].replace(
        'Inspect only this ONE actual full spherical texture. No rectilinear camera images or identity-reference pictures are supplied.',
        'Inspect Image 1, the ONE actual full spherical texture, with its supplied same-image camera details. No identity-reference pictures are supplied.').replace(
        'a rectilinear camera: there are no such cameras here.',
        'a supplied rectilinear camera; only Image 1 owns the projection judgement.')
    value['_sceneImages'] += ['data:image/jpeg;base64,'+base64.b64encode(p).decode() for p in cameras[:4]]
    value['instruction'] += (' Image 1 is the complete panorama. Images 2..5 are four actual undistorted '
        'horizontal cameras from these SAME received pixels, supplied only to clarify enclosure, '
        'illumination, visible weather and physical story facts. Count unique physical people on Image 1 '
        'ONCE; overlapping camera appearances are the same people. Determine daylight/night/twilight '
        'from visible windows, open doors and actual illumination, independently of requested tags. '
        'Return the COMPLETE original contract. No previous verdict or desired answer is supplied. '
        'If evidence remains uncertain, report uncertainty; do not guess a successful result.')
    return value


def apply(original,answer,cast,expected,image_hash):
    from .scene_review import evaluate
    try:observed=json.loads(answer.get('text',''))
    except (TypeError,ValueError):observed={}
    if not isinstance(observed,dict):observed={}
    # The new complete reply still goes through every original numeric,
    # physical-count, weather, fact, projection and enclosure check.
    return observed,evaluate(observed,cast,expected),{
        'version':1,'imageHash':image_hash,'original':deepcopy(original),'observation':deepcopy(observed)}
