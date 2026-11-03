"""One full-texture observation owns global composition, never local cameras.

The raw observation is journalled by the existing metered task runner. Numerical
and identity contradictions cannot be overruled by a model's positive verdict.
"""
import base64
from copy import deepcopy
from math import isfinite


VERSION = 1


def request(panorama, descriptor):
    cast = descriptor.get('sceneCast', [])
    result = {
        'inspectionMode': 'single-panorama-composition-v1',
        'requiredOutputKeys':['projection','spaceKind','cameraCovered','lighting','exteriorWeather',
            'visiblePeopleCount','instances','referenceArtifacts','finishedArtwork','unpaintedGuideRegions','confidence','evidence'],
        'imageRoles': [{'image': 1, 'role': 'ONE complete longitude-latitude texture; NOT a perspective camera'}],
        'expectedScene': {k: descriptor.get(k) for k in ('placeName', 'description', 'spaceKind', 'lighting')},
        'expectedPhysicalPeople': [{k: p.get(k) for k in ('entityId', 'name', 'appearance')} for p in cast],
        'instruction': (
            'Inspect only this ONE actual full spherical texture. No rectilinear camera images or '
            'identity-reference pictures are supplied. Do not infer projection from the 2:1 ratio alone. '
            'Judge whether the room/environment surrounds the observer, with consistent wrapping '
            'architecture, sky/ceiling above and floor below. Do not demand curved straight lines in '
            'a rectilinear camera: there are no such cameras here. The observer is the unseen player; '
            'never count the requested people merely because they are named. Count EVERY physically '
            'depicted person, including unintended strangers, duplicate bodies and the player. '
            'Count distant physical people seen THROUGH windows or doors as well; their being '
            'outside does not exempt them from the inventory. '
            'Exclude wall paintings and reflections; deduplicate the SAME person cut at the wrap seam. '
            'List one instance per distinct physical person. Distinct duplicate bodies at different '
            'positions remain separate instances even if they depict the same identity. '
            'For each head give its bounding box on THIS panorama, in normalized 0..1000 coordinates '
            '[left,top,right,bottom]; left>right is allowed ONLY for a head crossing the wrap seam. '
            'Use only exact supplied entity IDs, or null for an unmatched person. '
            'A tiny or ambiguous face may be unidentifiable in the full texture: use null rather '
            'than inventing a match. Unidentified is not proof of a stranger; separate close-up '
            'identity checks own the final character assignment. '
            'Classify the OBSERVER position: interior means standing inside a roofed enclosed room. '
            'Seeing sky/courtyard through a window or door does not put the observer outdoors. '
            'Standing in an open courtyard or an unenclosed covered outdoor gallery is outdoor. '
            'cameraCovered refers to an actual roof/ceiling above the observer, not a distant building. '
            'Check finished materials and residual reference grids/columns/placeholders. Normal aged plaster, '
            'paint wear, plain finished walls and painterly brushwork are NOT unfinished guide geometry. '
            'Pasted face thumbnails, repeated portrait panels and identity reference sheets added '
            'to walls are referenceArtifacts, even though they are not physical people. '
            'Judge actual time-of-day illumination and visible exterior weather from the pixels, '
            'not the expected tags. Bright interior lamps do not make the exterior daytime. '
            'Include every required top-level field, including top-level evidence of at least 40 characters '
            'explaining the actual enclosure and projection, in addition to person evidence. '
            'Report observed evidence and uncertainty. Do not invent exits or coordinates for gameplay.'),
        'outputContract': {
            'projection': 'equirectangular/perspective/uncertain',
            'spaceKind': 'interior/outdoor/uncertain', 'cameraCovered': 'boolean',
            'lighting': 'daylight/night/twilight/uncertain',
            'exteriorWeather': 'rainy/snowy/clear/not-visible/uncertain',
            'visiblePeopleCount': 'integer, exactly the number of physical instances',
            'instances': [{'instanceId': 'unique string', 'entityId': 'exact supplied ID or null',
                           'headBox': 'normalized 0..1000 [left,top,right,bottom]',
                           'evidence': 'actual visible physical person, not a requested name'}],
            'referenceArtifacts': 'array of actual artifact descriptions; empty when absent',
            'finishedArtwork': 'boolean', 'unpaintedGuideRegions': 'array of actual unfinished surfaces',
            'confidence': 'number 0..1', 'evidence': 'specific observations about projection and observer enclosure'},
        '_sceneImages': ['data:image/jpeg;base64,'+base64.b64encode(panorama).decode()],
        '_structured': True, '_visionStream': True,
    }
    if descriptor.get('visualFacts'):
        result['visualFacts']=deepcopy(descriptor['visualFacts'])
        result['requiredOutputKeys'].append('visualFactChecks')
        result['outputContract']['visualFactChecks']=[{'id':'exact supplied fact id','consistent':'boolean, based only on actual pixels','confidence':'0..1','evidence':'specific actual image evidence; at least 15 characters'}]
        result['instruction']+=' Check EVERY current physical story fact against these actual pixels; return a visualFactChecks entry for each ID. Historical mentions do not justify a living animal or item already processed or absent. Report contradictions; never change story state.'
    return result


def evaluate(value, cast, expected_scene):
    invalid = {'verified': False, 'issues': ['scene-review-unconfirmed']}
    if not isinstance(value, dict):
        return invalid
    confidence = value.get('confidence');count = value.get('visiblePeopleCount')
    instances = value.get('instances');space = value.get('spaceKind');projection = value.get('projection')
    if (type(confidence) not in (int, float) or not isfinite(confidence) or not 0 <= confidence <= 1
            or type(count) is not int or not 0 <= count <= 40 or not isinstance(instances, list)
            or len(instances) != count or projection not in ('equirectangular', 'perspective', 'uncertain')
            or space not in ('interior', 'outdoor', 'uncertain') or type(value.get('cameraCovered')) is not bool
            or type(value.get('finishedArtwork')) is not bool
            or not isinstance(value.get('evidence'), str) or len(value['evidence'].strip()) < 40):
        return invalid
    for field in ('referenceArtifacts', 'unpaintedGuideRegions'):
        if not isinstance(value.get(field), list) or any(not isinstance(p, str) or not p.strip() for p in value[field]):
            return invalid
    allowed = {p['entityId'] for p in cast};ids = set();matched = [];extras = [];unidentified=[];boxes=[]
    for part in instances:
        if not isinstance(part, dict):return invalid
        ident = part.get('instanceId');entity = part.get('entityId');box = part.get('headBox')
        # A literal-null string cannot establish an identity. Treat it only as
        # an UNMATCHED body, which still fails the cast check and requires repair.
        # The untouched supplier response remains in its metered call journal.
        if entity=='null':entity=None
        if (not isinstance(ident, str) or not ident or len(ident) > 80 or ident in ids
                or entity is not None and (not isinstance(entity,str) or entity not in allowed)
                or not isinstance(part.get('evidence'), str) or len(part['evidence'].strip()) < 10
                or not isinstance(box, list) or len(box) != 4
                or any(type(n) not in (int, float) or not isfinite(n) or not 0 <= n <= 1000 for n in box)
                or not box[1] < box[3] or not 0 < (box[2]-box[0]) % 1000 < 400):
            return invalid
        ids.add(ident)
        left,top,right,bottom=box
        if right<left:right+=1000
        for old in boxes:
            for offset in (-1000,0,1000):
                overlap=max(0,min(right,old[2]+offset)-max(left,old[0]+offset))*max(0,min(bottom,old[3])-max(top,old[1]))
                area=min((right-left)*(bottom-top),(old[2]-old[0])*(old[3]-old[1]))
                if overlap>.7*area:return invalid
        boxes.append([left,top,right,bottom])
        if entity is None:unidentified.append(part['evidence'][:180])
        elif entity in matched:extras.append(part['evidence'][:180])
        else:matched.append(entity)
    issues = []
    from .facts import evaluate as evaluate_facts
    issues.extend(evaluate_facts(value,expected_scene.get('visualFacts')))
    verified = confidence >= .85 and projection != 'uncertain' and space != 'uncertain'
    if expected_scene.get('lightingInspectionVersion')==1:
        lighting=value.get('lighting');weather=value.get('exteriorWeather')
        if lighting not in ('daylight','night','twilight') or weather not in ('rainy','snowy','clear','not-visible','uncertain'):
            verified=False
        else:
            expected_light=expected_scene.get('lighting')
            if (expected_light in ('夜','深夜') and lighting!='night'
                    or expected_light in ('清晨','早晨','白天','午后','中午') and lighting=='night'):
                issues.append('lighting-mismatch')
            if '雨' in expected_scene.get('description','') and weather=='snowy':issues.append('lighting-mismatch')
    if not verified:issues.append('scene-review-unconfirmed')
    if projection == 'perspective':issues.append('projection-mismatch')
    if count>len(cast):extras.extend(unidentified)
    if extras or count > len(cast):issues.append('unexpected-people')
    if count < len(cast):issues.append('missing-people')
    if value['referenceArtifacts']:issues.append('reference-artifacts')
    if not value['finishedArtwork'] or value['unpaintedGuideRegions']:issues.append('unfinished-artwork')
    expected = expected_scene.get('spaceKind')
    if expected == 'interior':
        if space != 'interior' or value['cameraCovered'] is not True:issues.append('space-mismatch')
    elif expected and expected not in ('uncertain', 'interior') and space != 'outdoor':
        issues.append('space-mismatch')
    return {'verified': verified, 'projection': projection, 'confidence': confidence,
            'spaceKind': space, 'openSky': not value['cameraCovered'], 'visiblePeopleCount': count,
            'unexpectedPeople': extras, 'unidentifiedPeople':unidentified,'referenceArtifacts': value['referenceArtifacts'],
            'finishedArtwork': value['finishedArtwork'], 'unpaintedGuideRegions': value['unpaintedGuideRegions'],
            'issues': list(dict.fromkeys(issues))}


def apply(parsed, observation, image_hash):
    result = deepcopy(parsed) if isinstance(parsed, dict) else {}
    result['sceneReview'] = {'version': VERSION, 'imageHash': image_hash, 'observation': deepcopy(observation)}
    return result


def count_conflict_request(panorama,descriptor,observation):
    value=request(panorama,descriptor)
    value['inspectionMode']='single-panorama-count-conflict-v1'
    value['candidateHeadRegions']=[p.get('headBox') for p in observation.get('instances',[]) if isinstance(p,dict)]
    value['instruction']+=(' Independent observations disagreed on physical people in these SAME pixels. '
        'The candidateHeadRegions are hypotheses from the original texture, not confirmed people. '
        'Re-examine the entire room and each region, distinguishing a complete physical head/body from faces in framed '
        'wall art, reflection, hats and furniture. Neither observer is assumed correct. Do not use the expected cast '
        'size as the answer. Count every actual separate physical person, including extra people and duplicates. '
        'Return the complete original contract and describe the actual physical figures and excluded depictions in evidence.')
    return value
