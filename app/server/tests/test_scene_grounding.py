"""Regression for missing cast and markers placed on an unrelated cauldron."""
from copy import deepcopy
from math import pi
from types import SimpleNamespace
from unittest.mock import patch
import json
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app.scenes.grounding import VERSION, inspection_targets, spatial_anchors_from_result, grounded_points, queue_visual_repair
from app.scenes.binding import anchors_from_result,queue_binding
from app.scenes.models import SceneImage,SceneProfile
from app.scenes.service import scene_view,receipt
from app.world.models import WorldLine,WorldCheckpoint
from app.models import Task
from .test_scenes import scene_world,request,generated


@pytest.mark.parametrize('reason',['missing-facts','foreign-feature','duplicate-feature','wall','counter','not-same-opening','uncertain','sky'])
def test_landmark_contract_rejects_positive_flags_without_same_opening_and_actual_ground(reason):
    from app.scenes.passages import apply_review,cue_requirements
    required=cue_requirements('Open tavern with amber windows and wooden barrels')
    answer={'id':'inn','image':1,'matched':True,'continuous':True,'confidence':.95,'kind':'door-open',
        'thresholdBoxPixels':[190,130,325,390],'anchorPixels':[256,380],'beyondPointPixels':[256,310],
        'evidence':'Actually open threshold into the visible furnished tavern interior.',
        'groundEvidence':{'nearSurface':'walkable-ground','farSurface':'walkable-ground','unobstructed':True,'continuation':'through-opening'},
        'cueFacts':[{'feature':name,'visible':True,'samePassage':True,'confidence':.95,'boxPixels':[190,130,325,390] if name.startswith('open-') else [160,180,205,320],
                    'evidence':'This measured landmark stands beside this exact open threshold.'} for name in required]}
    if reason=='missing-facts':answer.pop('cueFacts')
    if reason=='foreign-feature':answer['cueFacts'][0]['boxPixels']=[480,170,510,300]
    if reason=='duplicate-feature':answer['cueFacts'].append(deepcopy(answer['cueFacts'][0]))
    if reason=='wall':answer['groundEvidence']['continuation']='terminates-at-wall'
    if reason=='counter':answer['groundEvidence']['farSurface']='furniture'
    if reason=='not-same-opening':answer['cueFacts'][0]['samePassage']=False
    if reason=='uncertain':answer['cueFacts'][0]['confidence']=.7
    if reason=='sky':
        for fact in answer['cueFacts']:fact['boxPixels']=[180,1,230,25]
    region={'inn':{'singleImage':True,'viewIndex':0,'bounds':[0,0,512,512],'allowedKinds':['door-open'],'requiredCueFacts':required}}
    result=apply_review({}, {'routes':[answer]},region)['exits'][0]['passageEvidence']
    assert result['independentReview']['matched'] is False


@pytest.mark.parametrize('multiple',[False,True])
def test_measured_landmarks_and_ground_contract_survive_projection_and_bind(multiple):
    from app.scenes.passages import apply_review,cue_requirements
    from app.scenes.inspection import panorama_coordinates
    required=cue_requirements('Open tavern with amber windows and wooden barrels')
    answer={'id':'inn','image':1,'matched':True,'continuous':True,'confidence':.95,'kind':'door-open',
        'thresholdBoxPixels':[190,130,325,390],'anchorPixels':[256,380],'beyondPointPixels':[256,310],
        'evidence':'Actually open threshold into the visible furnished tavern interior.',
        'groundEvidence':{'nearSurface':'walkable-ground','farSurface':'walkable-ground','unobstructed':True,'continuation':'through-opening'},
        'cueFacts':[{'feature':name,'visible':True,'samePassage':True,'confidence':.95,'boxPixels':[190,130,325,390] if name.startswith('open-') else [160,180,205,320],
                    'evidence':'This measured landmark stands beside this exact open threshold.'} for name in required]}
    if multiple:
        for fact in deepcopy(answer['cueFacts']):
            if not fact['feature'].startswith('open-'):
                fact['boxPixels']=[320,180,365,320]
                answer['cueFacts'].append(fact)
    region={'inn':{'singleImage':True,'viewIndex':0,'bounds':[0,0,512,512],'allowedKinds':['door-open'],'requiredCueFacts':required}}
    measured=panorama_coordinates(apply_review({}, {'routes':[answer]},region),(2048,1024))
    targets={'exits':[{'id':'inn','visualCue':'Open tavern','requiredCueFacts':required,'independentReviewRequired':True,
                      'requiredReviewSource':'single-camera-measurement'}]}
    assert 'exits:inn' in spatial_anchors_from_result(measured,targets,(2048,1024),require_evidence=True)
    measured['exits'][0]['passageEvidence']['groundEvidence']['continuation']='terminates-at-wall'
    assert not spatial_anchors_from_result(measured,targets,(2048,1024),require_evidence=True)


@pytest.mark.parametrize('ground',[None,{'nearSurface':'walkable-ground','farSurface':'wall','unobstructed':True,'continuation':'open-ground'}])
def test_generic_production_route_requires_ground_even_without_named_landmarks(ground):
    from app.scenes.passages import apply_review,camera_review_requests
    target={'id':'road','visualCue':'Open road','allowedPassageKinds':['path-open'],
            'independentReviewRequired':True,'requiredCueFacts':[],'requiredGroundEvidence':True}
    data,regions=next(camera_review_requests([target],[b'actual camera bytes']))
    assert 'groundEvidence' in data['requiredRouteOutputKeys']
    assert data['responseShape']['routes'][0]['groundEvidence'] and data['groundContract']['required']
    answer={'id':'road','image':1,'matched':True,'continuous':True,'confidence':.95,'kind':'path-open',
            'thresholdBoxPixels':[190,130,325,390],'anchorPixels':[256,380],'beyondPointPixels':[256,310],
            'evidence':'Actual walking route, according to the measured observation.','cueFacts':[]}
    if ground is not None:answer['groundEvidence']=ground
    result=apply_review({}, {'routes':[answer]},regions)['exits'][0]['passageEvidence']['independentReview']
    assert result['matched'] is False
    assert result['status']==('unconfirmed' if ground is None else 'mismatch')


def revisited_room_measurements():
    # Actual completed detector receipt from an unchanged, manually reviewed
    # panorama. Keep these measurements, including its small bounding errors.
    common={'visible':True,'confidence':.95,'physicalPerson':True,'naturalScale':True,
            'scaleConfidence':.9,'groundConfidence':.9,
            'scaleEvidence':{'referenceKind':'furniture','viewIndex':4,'boxPixels':[230,275,415,505],
                             'depthRelation':'behind','description':'Measured furniture and adult proportions.'}}
    return {'characters':[
        {**deepcopy(common),'entityId':'holmes','viewIndex':0,'headBoxPixels':[203,232,246,284],
         'bodyViewIndex':4,'bodyBoxPixels':[176,107,266,345],'pose':'seated','groundViewIndex':4,
         'groundPointPixels':[219,340],'grounding':'fixture','supportingFixtureId':None,
         'supportingFixture':{'kind':'sofa','viewIndex':4,'boxPixels':[115,175,370,335],'confidence':.9}},
        {**deepcopy(common),'entityId':'peterson','viewIndex':1,'headBoxPixels':[230,168,273,216],
         'bodyViewIndex':1,'bodyBoxPixels':[196,170,300,477],'pose':'standing','groundViewIndex':5,
         'groundPointPixels':[247,293],'grounding':'floor','supportingFixtureId':None}
    ]}


def test_revisit_small_camera_measurement_errors_do_not_remove_visible_people():
    from app.scenes.inspection import panorama_coordinates
    raw=revisited_room_measurements();original=deepcopy(raw)
    measured=panorama_coordinates(raw,(2048,1024))
    anchors=anchors_from_result(measured,[{'entityId':'holmes'},{'entityId':'peterson'}],(2048,1024),require_grounding=True)
    assert set(anchors)=={'holmes','peterson'}
    assert raw==original  # Tolerance never rewrites the measured pixels.
    assert anchors['holmes']['headBox'][1]==pytest.approx(measured['characters'][0]['headBoxPixels'][1]/1024)


@pytest.mark.parametrize('distance',[0,2,5,8,9,40])
def test_support_contact_tolerance_is_bounded_in_its_measured_camera(distance):
    from app.scenes.inspection import panorama_coordinates
    raw=revisited_room_measurements()
    raw['characters'][0]['groundPointPixels']=[219,335+distance]
    measured=panorama_coordinates(raw,(2048,1024))
    anchors=anchors_from_result(measured,[{'entityId':'holmes'},{'entityId':'peterson'}],(2048,1024),require_grounding=True)
    assert ('holmes' in anchors)==(distance<=8)


@pytest.mark.parametrize('error',['head-outside-body','contact-away-from-support'])
def test_revisit_tolerance_still_rejects_actual_contact_or_body_displacement(error):
    from app.scenes.inspection import panorama_coordinates
    raw=revisited_room_measurements()
    if error=='head-outside-body':
        raw['characters'][1]['headBoxPixels'][1]=130
        rejected='peterson'
    else:
        raw['characters'][0]['groundPointPixels']=[219,380]
        rejected='holmes'
    measured=panorama_coordinates(raw,(2048,1024))
    anchors=anchors_from_result(measured,[{'entityId':'holmes'},{'entityId':'peterson'}],(2048,1024),require_grounding=True)
    assert rejected not in anchors


def test_head_and_body_measured_in_adjacent_cameras_match_across_spherical_seam():
    from app.scenes.inspection import panorama_coordinates
    part={**revisited_room_measurements()['characters'][1],'entityId':'person',
          'viewIndex':2,'headBoxPixels':[450,190,485,225],
          'bodyViewIndex':3,'bodyBoxPixels':[26,175,90,460],
          'groundViewIndex':3,'groundPointPixels':[54,460]}
    part['scaleEvidence']={'referenceKind':'door','viewIndex':3,'boxPixels':[10,130,120,480],
                           'depthRelation':'same-depth','description':'The full door and grounded adult are visible.'}
    measured=panorama_coordinates({'characters':[part]},(2048,1024))
    anchors=anchors_from_result(measured,[{'entityId':'person'}],(2048,1024),require_grounding=True)
    assert set(anchors)=={'person'}


def test_measured_face_across_texture_seam_keeps_both_head_side_labels():
    from app.scenes.inspection import panorama_coordinates
    from app.scenes.composition import character_presentations
    part=deepcopy(revisited_room_measurements()['characters'][1])
    part.update(viewIndex=2,bodyViewIndex=2,groundViewIndex=6)
    measured=panorama_coordinates({'characters':[part]},(2048,1024))
    anchors=anchors_from_result(measured,[{'entityId':'peterson'}],(2048,1024),require_grounding=True)
    assert anchors['peterson']['headBox']==[]
    box=anchors['peterson']['unwrappedHeadBox'];assert box[0]<1<box[2]
    row=SimpleNamespace(status='ready',image={'sha256':'same','bindingImageHash':'same',
        'anchorVersion':VERSION,'characterAnchors':anchors})
    person=character_presentations(row,{'characters':[{'id':'peterson'}]})['peterson']
    assert person['mode']=='embedded' and person['labelYaw']<0<person['labelLeftYaw']
    assert person['labelPitch']==pytest.approx((.5-(box[1]+box[3])/2)*pi)


@pytest.mark.parametrize('problem',['adjacent-arch','wall-floor','missing','duplicate','wrong-kind','beyond-outside','no-depth'])
def test_independent_passage_review_rejects_false_landmarks_and_disconnected_ground(problem):
    from app.scenes.passages import apply_review
    part={'id':'inn','visible':True,'passable':True,'confidence':.95,'viewIndex':1,'anchorPixels':[260,370],
        'passageEvidence':{'kind':'path-open','viewIndex':1,'boxPixels':[200,150,330,390],
            'visualCueMatched':True,'cueEvidence':'The tavern door sits beside this open street arch.'}}
    region={'inn':{'viewIndex':1,'bounds':[100,50,430,445],'requiredKind':'door-open'}}
    answer={'id':'inn','matched':True,'continuous':True,'confidence':.95,'kind':'door-open',
        'thresholdBoxPixels':[120,100,230,330],'anchorPixels':[170,320],'beyondPointPixels':[170,250],
        'evidence':'Open tavern door has a continuous visible interior floor past the actual threshold.'}
    if problem=='adjacent-arch':answer['matched']=False
    if problem=='wall-floor':answer['continuous']=False
    if problem=='wrong-kind':answer['kind']='path-open'
    if problem=='beyond-outside':answer['beyondPointPixels']=[280,250]
    if problem=='no-depth':answer['beyondPointPixels']=[170,319]
    routes=[] if problem=='missing' else [answer,answer] if problem=='duplicate' else [answer]
    result=apply_review({'exits':[part]},{'routes':routes},region)
    evidence=result['exits'][0]['passageEvidence']
    assert evidence['independentReview']['matched'] is False
    assert evidence['independentReview']['status']==('mismatch' if problem in ('adjacent-arch','wall-floor') else 'unconfirmed')
    assert evidence['visualCueMatched'] is (False if problem in ('adjacent-arch','wall-floor') else True)
    assert result['exits'][0]['anchorPixels']==part['anchorPixels']


def test_independent_passage_review_uses_its_actual_threshold_measurements():
    from app.scenes.passages import apply_review
    from app.scenes.inspection import panorama_coordinates
    part={'id':'inn','visible':True,'passable':True,'confidence':.95,'viewIndex':1,'anchorPixels':[450,370],
        'passageEvidence':{'kind':'path-open','viewIndex':1,'boxPixels':[380,150,495,390]}}
    regions={'inn':{'viewIndex':1,'bounds':[100,50,512,445],'requiredKind':'door-open'}}
    answer={'id':'inn','matched':True,'continuous':True,'confidence':.95,'kind':'door-open',
        'thresholdBoxPixels':[120,100,230,330],'anchorPixels':[170,320],'beyondPointPixels':[170,250],
        'evidence':'Open tavern door has a continuous visible interior floor past the actual threshold.'}
    raw={'exits':[part]};result=apply_review(raw,{'routes':[answer]},regions)
    target={'id':'inn','visualCue':'Tavern door','requiredPassageKind':'door-open','independentReviewRequired':True}
    anchors=spatial_anchors_from_result(panorama_coordinates(result,(2048,1024)),{'exits':[target]},(2048,1024),require_evidence=True)
    assert anchors['exits:inn']['passageEvidence']['boxPixels']==[220,150,330,380]
    assert result['exits'][0]['anchorPixels']==[270,370] and raw['exits'][0]['anchorPixels']==[450,370]


def test_primary_detector_boolean_cannot_satisfy_independent_route_contract():
    target={'id':'inn','visualCue':'Tavern door','requiredPassageKind':'door-open','independentReviewRequired':True}
    part={'id':'inn','visible':True,'passable':True,'confidence':.95,'viewIndex':1,
        'anchorPixels':[1024,660],'cameraAnchorPixels':[270,370],
        'passageEvidence':{'kind':'door-open','viewIndex':1,'boxPixels':[220,150,330,380],
            'description':'An open door and walking surface.','visualCueMatched':True,'cueEvidence':'Supposed tavern entrance with amber windows.'}}
    assert spatial_anchors_from_result({'exits':[part]},{'exits':[target]},(2048,1024),require_evidence=True)=={}


@pytest.mark.parametrize('kind',['path-open','stairs-open','door-open'])
def test_actual_residential_path_or_stairs_are_accepted_without_substituting_tavern_door(kind):
    from app.scenes.passages import apply_review
    from app.scenes.inspection import panorama_coordinates
    view={'descriptor':{'exitRoutes':[{'id':'home','visualCue':'Open residential walking passage toward a townhouse entrance with exterior stone steps.'}]},
        'points':[],'exits':[{'id':'home','name':'寓所'}]}
    target=inspection_targets(view)['exits'][0]
    part={'id':'home','visible':True,'passable':True,'confidence':.95,'viewIndex':1,'anchorPixels':[270,370],
        'passageEvidence':{'kind':kind,'viewIndex':1,'boxPixels':[220,150,330,380]}}
    region={'home':{'singleImage':True,'viewIndex':1,'bounds':[100,50,430,445],'allowedKinds':target['allowedPassageKinds'],
                    'requiredCueFacts':target['requiredCueFacts'],'requiredGroundEvidence':True,'requiredOpeningProof':True}}
    answer={'id':'home','image':1,'matched':True,'continuous':True,'confidence':.95,'kind':kind,
        'thresholdBoxPixels':[120,100,230,330],'anchorPixels':[170,320],'beyondPointPixels':[170,250],
        'apertureBoxPixels':[120,100,230,280],'throughPointPixels':[170,220],
        'evidence':'Actual residential stone steps with visible connected near and far walking surface.',
        'groundEvidence':{'nearSurface':'walkable-ground','farSurface':'walkable-ground','unobstructed':True,'continuation':'through-opening'},
        'cueFacts':[{'feature':name,'visible':True,'samePassage':True,'confidence':.95,
                    'boxPixels':[120,100,230,330] if name.startswith('open-') else [120,240,230,330],
                    'evidence':'Measured exterior stone steps lead through this actual open residential threshold.'} for name in target['requiredCueFacts']]}
    measured=apply_review({'exits':[part]},{'routes':[answer]},region)
    from app.scenes.surfaces import apply_surface
    from .test_scene_surfaces import observation
    measured['exits'][0]=apply_surface(measured['exits'][0],observation(),'a'*64)
    parsed=panorama_coordinates(measured,(2048,1024))
    assert 'exits:home' in spatial_anchors_from_result(parsed,{'exits':[target]},(2048,1024),require_evidence=True)
    tavern={**target,'allowedPassageKinds':['door-open']}
    assert bool(spatial_anchors_from_result(parsed,{'exits':[tavern]},(2048,1024),require_evidence=True))==(kind=='door-open')


@pytest.mark.parametrize('observed',['interior','outdoor-lane','missing-interior','path-to-house'])
def test_residence_route_requires_measured_interior_not_just_steps_or_destination_name(observed):
    from app.scenes.layout import route_visual_cues
    from app.scenes.passages import apply_review
    exits=[{'id':'home','name':'贝克街寓所'},{'id':'market','name':'市场'}]
    cues=route_visual_cues(exits,'open-air street or market')
    target=inspection_targets({'descriptor':{'exitRoutes':[{'id':'home','visualCue':cues['home']}]},
                              'points':[],'exits':[exits[0]]})['exits'][0]
    required=target['requiredCueFacts']
    facts=[{'feature':name,'visible':True,'samePassage':True,'confidence':.95,
            'boxPixels':[120,100,230,280] if name.startswith('open-') else [120,240,230,330],
            'evidence':'Actual house entrance hall has enclosing interior walls and floor beyond its doorway.'}
           for name in required]
    if observed=='missing-interior':facts=[fact for fact in facts if fact['feature']!='open-residential-interior']
    if observed=='outdoor-lane':
        next(fact for fact in facts if fact['feature']=='open-residential-interior').update(
            visible=False,evidence='The actual opening reveals sky and an outdoor street, without any interior hall.')
    answer={'id':'home','image':1,'matched':True,'continuous':True,'confidence':.95,
            'kind':'path-open' if observed=='path-to-house' else 'stairs-open',
            'thresholdBoxPixels':[120,100,230,330],'anchorPixels':[170,320],
            'beyondPointPixels':[170,250],'apertureBoxPixels':[120,100,230,280],
            'throughPointPixels':[170,220],
            'groundEvidence':{'nearSurface':'walkable-ground','farSurface':'walkable-ground',
                              'unobstructed':True,'continuation':'through-opening'},
            'cueFacts':facts,'evidence':'Three actual stone steps reach this observed residential doorway and its far ground.'}
    region={'home':{'singleImage':True,'viewIndex':1,'bounds':[0,0,512,512],
                    'allowedKinds':target['allowedPassageKinds'],'requiredCueFacts':required,
                    'requiredGroundEvidence':True,'requiredOpeningProof':True}}
    result=apply_review({}, {'routes':[answer]},region)['exits'][0]['passageEvidence']['independentReview']
    assert result['matched']==(observed=='interior')
    assert result['continuous']==(observed=='interior')


def test_single_outdoor_exit_to_residence_still_requires_its_actual_interior():
    from app.scenes.layout import route_visual_cues
    from app.scenes.passages import cue_requirements
    cue=route_visual_cues([{'id':'home','name':'村民住宅'}],'open-air setting')['home']
    assert 'open-residential-interior' in cue_requirements(cue)


@pytest.mark.parametrize('name',['Warehouse','Greenhouse','Homeport','Winner Plaza','Marketplace Archive'])
def test_destination_substring_cannot_invent_a_house_or_tavern(name):
    from app.scenes.layout import route_visual_cues
    cue=route_visual_cues([{'id':'route','name':name}],'open-air setting')['route']
    assert cue=='Open walking passage.'


@pytest.mark.parametrize('name',['Home','Village House','Townhouse','Baker Street Apartment'])
def test_named_residential_words_still_receive_the_strict_interior_contract(name):
    from app.scenes.layout import route_visual_cues
    from app.scenes.passages import cue_requirements
    cue=route_visual_cues([{'id':'route','name':name}],'open-air setting')['route']
    assert 'open-residential-interior' in cue_requirements(cue)


@pytest.mark.parametrize('camera',list(range(8)))
def test_single_picture_review_cannot_choose_a_different_actual_camera(camera):
    from app.scenes.passages import apply_review,camera_review_requests
    target={'id':'market','visualCue':'Canvas market aisle','allowedPassageKinds':['path-open'],'independentReviewRequired':True}
    requests=list(camera_review_requests([target],[bytes([index]) for index in range(8)]))
    data,regions=requests[camera]
    assert len(data['_sceneImages'])==1 and 'cameraImages' not in regions['market']
    assert regions['market']['viewIndex']==camera
    answer={'id':'market','image':1,'matched':True,'continuous':True,'confidence':.95,'kind':'path-open',
        'thresholdBoxPixels':[120,100,330,380],'anchorPixels':[220,375],'beyondPointPixels':[220,300],
        'evidence':'An actual canvas-covered market aisle with clear near and far walking ground.'}
    result=apply_review({}, {'routes':[answer]},regions)['exits'][0]
    assert result['viewIndex']==camera and result['anchorPixels']==[220,375]
    assert result['passageEvidence']['independentReview']['source']=='single-camera-measurement'
    wrong=apply_review({}, {'routes':[{**answer,'image':2}]},regions)['exits'][0]
    assert wrong['passageEvidence']['independentReview']['status']=='unconfirmed'


def test_one_absent_camera_does_not_discard_a_destination_found_in_another():
    from app.scenes.passages import apply_review,merge_camera_reviews
    target={'id':'market','visualCue':'Canvas market aisle','allowedPassageKinds':['path-open'],'independentReviewRequired':True}
    absent={'id':'market','image':1,'matched':False,'continuous':False,'confidence':.95,'evidence':'This actual camera contains only a brick wall, with no market landmark.'}
    present={**absent,'matched':True,'continuous':True,'kind':'path-open','thresholdBoxPixels':[120,100,330,380],
        'anchorPixels':[220,375],'beyondPointPixels':[220,300],'evidence':'Visible market awnings beside the same open aisle with continuous ground.'}
    findings=[apply_review({}, {'routes':[present if index==2 else absent]},
        {'market':{'singleImage':True,'viewIndex':index,'bounds':[0,0,512,512],'allowedKinds':['path-open']}}) for index in range(4)]
    raw={'composition':{'unexpectedPeople':['Actual stranger']},'characters':[],'exits':[]}
    merged=merge_camera_reviews(raw,findings,[target])
    assert merged['exits'][0]['viewIndex']==2 and merged['exits'][0]['visible'] is True
    assert merged['composition']==raw['composition'] and raw['exits']==[]
    only_absent=[findings[0]]*8
    assert merge_camera_reviews(raw,only_absent,[target])['exits'][0]['passageEvidence']['independentReview']['status']=='mismatch'
    assert merge_camera_reviews(raw,only_absent[:3],[target])['exits'][0]['passageEvidence']['independentReview']['status']=='unconfirmed'


def test_crop_review_request_contains_actual_pixels_and_no_prior_detector_verdict():
    from app.scenes.passages import review_request
    from PIL import Image
    from io import BytesIO
    im=Image.new('RGB',(512,512),'#695c47');out=BytesIO();im.save(out,format='JPEG')
    part={'id':'inn','visible':True,'passable':True,'confidence':.99,'viewIndex':1,
        'passageEvidence':{'boxPixels':[330,175,455,400],'visualCueMatched':True,
            'cueEvidence':'PREVIOUS CLAIM THAT SHOULD NOT BIAS REVIEW'}}
    target={'id':'inn','visualCue':'Actual tavern open doorway','independentReviewRequired':True,
        'allowedPassageKinds':['door-open']}
    data,regions=review_request({'exits':[part]},{'exits':[target]},[out.getvalue()]*8)
    assert len(data['_sceneImages'])==5 and 'PREVIOUS CLAIM' not in json.dumps(data)
    assert data['routes'][0]['cropSize']=={'width':292,'height':360}
    assert regions['inn']['bounds']==[220,95,512,455]
    assert data['routes'][0]['fullCameraImages']==[1,2,3,4] and data['routes'][0]['cropImage']==5


def test_independent_reviews_budget_one_exchange_per_actual_camera(client,scene_world,profile):
    from app.models import Consent
    initial=request(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);row.status='ready';row.image={'sha256':'received-pixels'}
        targets={'fixtures':[],'exits':[{'id':str(i),'independentReviewRequired':True} for i in range(9)]}
        with patch('app.scenes.binding.settings.ai_mode','live'),patch('app.scenes.binding.inspection_targets',return_value=targets):queue_binding(db,row)
        task=db.get(Task,row.image['bindingTaskId']);consent=db.get(Consent,task.payload['consentId'])
        assert consent.max_calls==81  # Full scene, eight owned views, and at most nine point confirmations per view.


def test_new_indoor_exit_contract_requires_actual_single_camera_door_and_ground():
    view={'points':[],'exits':[{'id':'yard','name':'庭院'}], 'descriptor':{'spaceKind':'interior'}}
    legacy=inspection_targets(view)['exits'][0]
    assert not legacy.get('independentReviewRequired')
    target=inspection_targets(view,independent_interior=True)['exits'][0]
    assert target['independentReviewRequired'] and target['requiredGroundEvidence'] and target['requiredSurfaceReview']
    assert target['allowedPassageKinds']==['door-open','stairs-open']


@pytest.mark.parametrize('value',[None,{},'not-an-array'])
def test_malformed_route_arrays_remain_unconfirmed_without_internal_failure(value):
    from app.scenes.passages import review_request,apply_review
    target={'id':'a','visualCue':'Actual doorway','independentReviewRequired':True}
    request,regions=review_request({'exits':value},{'exits':[target]},[])
    assert request['routes']==[] and regions=={}
    part={'id':'a','visible':True,'passable':True,'passageEvidence':{'visualCueMatched':True}}
    regions={'a':{'viewIndex':0,'bounds':[0,0,512,512],'allowedKinds':['door-open']}}
    result=apply_review({'exits':[part]},{'routes':value},regions)
    assert result['exits'][0]['passageEvidence']['independentReview']['status']=='unconfirmed'
    assert apply_review({'exits':value},{'routes':[]},regions)['exits'][0]['passageEvidence']['independentReview']['status']=='unconfirmed'


@pytest.mark.parametrize('image',[1,3,5,True,None])
def test_independent_full_view_can_measure_a_real_passage_missed_by_primary_detector(image):
    from app.scenes.passages import apply_review
    region={'inn':{'cameraImages':[1,2,3,4],'allowedKinds':['door-open']}}
    answer={'id':'inn','image':image,'matched':True,'continuous':True,'confidence':.95,'kind':'door-open',
        'thresholdBoxPixels':[190,130,325,390],'anchorPixels':[256,380],'beyondPointPixels':[256,310],
        'evidence':'Actual open tavern threshold with amber windows and continuous visible interior floor.'}
    result=apply_review({'exits':[{'id':'inn','visible':False,'passable':False}]},{'routes':[answer]},region)
    part=result['exits'][0]
    if type(image) is int and image in (1,3):
        assert part['visible'] and part['passable'] and part['viewIndex']==image-1
        assert part['anchorPixels']==[256,380]
    else:
        assert not part['visible'] and part['passageEvidence']['independentReview']['status']=='unconfirmed'


@pytest.mark.parametrize('image',[None,9,True])
def test_negative_independent_answer_without_a_real_image_is_unconfirmed(image):
    from app.scenes.passages import apply_review
    region={'inn':{'cameraImages':[1,2,3,4],'allowedKinds':['door-open']}}
    raw={'exits':[{'id':'inn','visible':True,'passable':True,'passageEvidence':{'visualCueMatched':True}}]}
    answer={'id':'inn','image':image,'matched':False,'continuous':False,'confidence':.95,
        'evidence':'The actual tavern doorway appears closed and blocks continuous passage.'}
    result=apply_review(raw,{'routes':[answer]},region)['exits'][0]['passageEvidence']
    assert result['visualCueMatched'] is True and result['independentReview']['status']=='unconfirmed'


def test_independent_route_measurement_never_discards_negative_composition_evidence():
    from app.scenes.passages import apply_review
    from app.scenes.binding import composition_from_result
    raw={'composition':{'projection':'equirectangular','confidence':.95,'spaceKind':'outdoor','openSky':True,
        'visiblePeopleCount':0,'unexpectedPeople':['Two distant pedestrian silhouettes'],'referenceArtifacts':[]},'exits':[]}
    result=apply_review(raw,{'routes':[]},{'a':{'cameraImages':[1,2,3,4],'allowedKinds':['path-open']}})
    assert result['composition']==raw['composition']
    assert composition_from_result(result,[],{'spaceKind':'outdoor'})['issues']==['unexpected-people']


@pytest.mark.parametrize('view',range(8))
def test_camera_contact_inverse_preserves_measured_pixels_across_seam_and_ground_views(view):
    from app.scenes.inspection import sphere_pixel,camera_pixel
    for local in ((26,26),(256,256),(485,460),(100,375)):
        point=sphere_pixel(view,*local,2048,1024)
        for offset in (-2048,0,2048):
            assert camera_pixel(view,point[0]+offset,point[1],2048,1024)==pytest.approx(local)


@pytest.mark.parametrize('distance',[0,5,8,9,40])
def test_support_measured_in_another_camera_cannot_use_curved_envelope_as_contact(distance):
    from app.scenes.inspection import sphere_pixel,camera_pixel,panorama_coordinates
    contact=camera_pixel(7,*sphere_pixel(4,135,335+distance,2048,1024),2048,1024)
    part={**revisited_room_measurements()['characters'][0],'entityId':'person',
          'viewIndex':7,'headBoxPixels':[345,210,380,260],
          'bodyViewIndex':7,'bodyBoxPixels':[335,205,400,365],
          'groundViewIndex':7,'groundPointPixels':list(contact)}
    part['scaleEvidence']={'referenceKind':'furniture','viewIndex':7,'boxPixels':[290,205,425,370],
                           'depthRelation':'same-depth','description':'Actual sofa relative to the seated adult.'}
    measured=panorama_coordinates({'characters':[part]},(2048,1024))
    anchors=anchors_from_result(measured,[{'entityId':'person'}],(2048,1024),require_grounding=True)
    if distance>8:assert anchors=={}
    # This fixture checks support containment only; another body/contact
    # consistency check may reject an otherwise close synthetic measurement.


@pytest.mark.parametrize('cue_evidence',[None,{}, {'visualCueMatched':False,'cueEvidence':'Stone posts beside the path'}])
def test_multi_route_missing_actual_landmark_evidence_never_gets_picture_pin(cue_evidence):
    passage={'kind':'path-open','viewIndex':0,'boxPixels':[180,100,330,400],
             'description':'An open continuous walking path.',**(cue_evidence or {})}
    part={'id':'a','visible':True,'passable':True,'confidence':.95,'viewIndex':0,
          'anchorPixels':[1024,660],'cameraAnchorPixels':[256,380],'passageEvidence':passage}
    assert spatial_anchors_from_result({'exits':[part]}, {'exits':[{'id':'a','visualCue':'Stone posts.'}]}, (2048,1024),require_evidence=True)=={}


@pytest.mark.parametrize('top,nested,accepted',[(True,None,True),(False,None,False),(True,False,False),(False,True,False)])
def test_actual_top_level_cue_evidence_is_normalized_and_explicit_mismatch_wins(top,nested,accepted):
    from app.scenes.inspection import panorama_coordinates
    evidence={'kind':'path-open','viewIndex':0,'boxPixels':[180,100,330,400],
              'description':'A continuous open path with stone border posts.'}
    if nested is not None:evidence['visualCueMatched']=nested
    part={'id':'a','visible':True,'passable':True,'confidence':.95,'viewIndex':0,
          'anchorPixels':[256,380],'passageEvidence':evidence,'visualCueMatched':top,
          'cueEvidence':'Actual short stone posts on each side of the opening.'}
    raw={'exits':[part]};before=deepcopy(raw);measured=panorama_coordinates(raw,(2048,1024))
    anchors=spatial_anchors_from_result(measured,{'exits':[{'id':'a','visualCue':'Stone posts.'}]},(2048,1024),require_evidence=True)
    assert bool(anchors)==accepted and raw==before


def test_independent_routes_cannot_reuse_one_measured_passage_under_different_names():
    passage={'kind':'path-open','viewIndex':0,'boxPixels':[180,100,330,400],
             'description':'One open continuous walking path.','visualCueMatched':True,
             'cueEvidence':'Short stone border posts visibly flank the path.'}
    part={'visible':True,'passable':True,'confidence':.95,'viewIndex':0,
          'anchorPixels':[1024,660],'cameraAnchorPixels':[256,380],'passageEvidence':passage}
    targets={'exits':[{'id':ident,'visualCue':'A distinct natural landmark.'} for ident in ('a','b','c')]}
    result=spatial_anchors_from_result({'exits':[{**deepcopy(part),'id':ident} for ident in ('a','b','c')]},targets,(2048,1024),require_evidence=True)
    assert len(result)==1
    # A measured passage around the sphere in a different direction is separate.
    other={**deepcopy(part),'id':'b','viewIndex':1,'anchorPixels':[1536,660],
           'passageEvidence':{**passage,'viewIndex':1}}
    result=spatial_anchors_from_result({'exits':[{**part,'id':'a'},other]},targets,(2048,1024),require_evidence=True)
    assert len(result)==2


def test_confirmed_wrong_route_landmark_is_not_reinspected_as_missing_fields(client,scene_world,profile):
    from app.world.worker import process
    from app.scenes.service import save_image
    initial=request(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);data,metadata,request_id=generated()
        row.image=save_image(row,data,metadata,request_id);row.status='ready'
        row.descriptor={**row.descriptor,'sceneCast':[]}
        with patch('app.scenes.binding.settings.ai_mode','live'),\
             patch('app.scenes.binding.inspection_targets',return_value={'fixtures':[],'exits':[{'id':'a','visualCue':'Stone posts.'}]}):queue_binding(db,row)
        task_id=row.image['bindingTaskId'];db.commit()
    value={'characters':[],'fixtures':[],'composition':{'projection':'equirectangular','spaceKind':'interior',
           'openSky':False,'confidence':.95,'visiblePeopleCount':0,'unexpectedPeople':[],'referenceArtifacts':[],
           'finishedArtwork':True,'unpaintedGuideRegions':[]},
           'exits':[{'id':'a','visible':True,'passable':True,'confidence':.95,'viewIndex':0,'anchorPixels':[256,380],
                     'passageEvidence':{'kind':'path-open','viewIndex':0,'boxPixels':[180,100,330,400],
                     'description':'An open path without the required stone posts.','visualCueMatched':False,
                     'cueEvidence':'There are no stone posts at this opening.'}}]}
    with patch('app.scenes.binding.inspection_targets',return_value={'fixtures':[],'exits':[{'id':'a','visualCue':'Stone posts.'}]}),\
         patch('app.worker.call_provider',return_value={'text':json.dumps(value),'finishReason':'stop'}):process(task_id)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);task=db.get(Task,task_id);task.provider_state='completed'
        assert any(p['reason']=='passage-cue-mismatch' for p in row.image['detectionIssues'])
        with patch('app.scenes.binding.settings.ai_mode','live'),\
             patch('app.scenes.binding.inspection_targets',return_value={'fixtures':[],'exits':[{'id':'a','visualCue':'Stone posts.'}]}):queue_binding(db,row)
        assert row.image['bindingTaskId']==task_id


def picture_view():
    return {'pointId':'entry','points':[{'id':'entry','name':'山台','yaw':0,'pitch':0},
            {'id':'bench','name':'条凳旁','fixture':'bench','yaw':-2,'pitch':-.1,'source':{'quote':'石台旁有长椅。'}},
            {'id':'path','name':'道路','fixture':'path','yaw':0,'pitch':-.1}],
            'exits':[{'id':'yard','name':'练武场','portalId':'path','yaw':0,'pitch':-.1}]}


def test_positions_are_measured_from_picture_not_requested_layout():
    view=picture_view();targets=inspection_targets(view)
    assert all('yaw' not in p and 'pitch' not in p for group in targets.values() for p in group)
    result={'fixtures':[{'id':'bench','visible':True,'confidence':.95,'anchorPixels':[750,350]}],
            'exits':[{'id':'yard','visible':True,'passable':True,'confidence':.9,'anchorPixels':[600,325]}]}
    anchors=spatial_anchors_from_result(result,targets,(1000,500))
    row=SimpleNamespace(status='ready',image={'sha256':'pic','bindingImageHash':'pic','anchorVersion':VERSION,'spatialAnchors':anchors})
    original=deepcopy(view);actual=grounded_points(row,view)
    bench=actual['points'][1];exit=actual['exits'][0]
    assert bench['imageBound'] and bench['yaw']==pytest.approx(pi/2) and bench['pitch']==pytest.approx(-.2*pi)
    assert exit['imageBound'] and exit['yaw']==pytest.approx(.2*pi)
    assert actual['points'][2]['imageBound'] is False and view==original


@pytest.mark.parametrize('change',[{'id':'foreign'},{'visible':False},{'passable':False},{'confidence':.4},
    {'confidence':True},{'anchorPixels':[float('nan'),300]},{'anchorPixels':[1100,300]},
    {'anchorPixels':[500,100]},{'anchorPixels':[500,490]},{'anchorPixels':['500',300]}])
def test_ambiguous_blocked_or_invalid_routes_never_get_picture_markers(change):
    part={'id':'yard','visible':True,'passable':True,'confidence':.95,'anchorPixels':[600,325],**change}
    assert spatial_anchors_from_result({'exits':[part]},inspection_targets(picture_view()),(1000,500))=={}


def test_legacy_coordinates_cannot_place_markers_but_route_remains_available():
    row=SimpleNamespace(status='ready',image={'anchorVersion':2,'spatialAnchors':{'yard':{'yaw':0,'pitch':0,'verified':True}}})
    result=grounded_points(row,picture_view())
    assert result['exits'][0]['id']=='yard' and result['exits'][0]['imageBound'] is False
    assert all(not p['imageBound'] for p in result['points'])


def test_real_low_confidence_bench_is_not_marked_on_the_adjacent_prop():
    response={'fixtures':[{'id':'bench','visible':True,'confidence':.7,'anchorPixels':[620,430]}]}
    assert spatial_anchors_from_result(response,inspection_targets(picture_view()),(1024,512))=={}


def test_name_is_beside_the_head_using_existing_measured_pixels():
    from app.scenes.composition import character_presentations
    row=SimpleNamespace(status='ready',image={'sha256':'pic','bindingImageHash':'pic','anchorVersion':VERSION,'characterAnchors':{'boy':{'yaw':.39,'pitch':.12,'headBox':[.55,.38,.58,.46],'verified':True,'source':'image-inspection'}}})
    value=character_presentations(row,{'characters':[{'id':'boy'}],'characterVisibility':{'boy':'visible'}})
    assert value['boy']['pitch']==pytest.approx(.12*pi)
    assert value['boy']['labelYaw']==pytest.approx(.08*2*pi)
    assert value['boy']['labelPitch']==pytest.approx(.08*pi)


@pytest.mark.parametrize('response',[[],{'characters':None},{'characters':[None,{'entityId':[]}]}])
def test_malformed_character_inspection_does_not_invent_an_anchor(response):
    assert anchors_from_result(response,[{'entityId':'boy'}],(1000,500))=={}


def test_missing_character_correction_is_bounded_and_preserves_story(client,scene_world,profile):
    w=scene_world;initial=request(client,w)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);row.status='ready';row.image={'sha256':'original-picture'}
        cp=db.get(WorldCheckpoint,w['cp']);view=scene_view(cp)
        old_state=deepcopy(cp.state);old_head=db.get(WorldLine,w['line']).head_checkpoint_id
        missing=[e['id'] for e in view['characters']]
        child_id=queue_visual_repair(db,row,view,missing);assert child_id
        row.image={**row.image,'repairSceneId':child_id}
        assert queue_visual_repair(db,row,view,missing) is None
        child=db.get(SceneImage,child_id)
        assert child.descriptor['referenceSceneId']==row.id and child.descriptor['visualRepairAttempt']==1
        assert queue_visual_repair(db,child,view,missing) is None
        assert db.scalar(select(func.count()).select_from(SceneImage))==2
        assert cp.state==old_state and db.get(WorldLine,w['line']).head_checkpoint_id==old_head
        child.status='ready';child.image={'anchorVersion':VERSION,'characterAnchors':{},'spatialAnchors':{}}
        assert receipt(row,view)['id']==child.id
        child.status='unknown';child.error_code='SCENE_STATUS_UNKNOWN'
        result=receipt(row,view)
        assert result['id']==row.id and result['image']['anchorStatus']=='incomplete'
        assert result['image']['repairError']=='SCENE_STATUS_UNKNOWN'
        db.commit()
    reopened=request(client,w,'reopen-after-failed-visual-repair')
    assert reopened['status']=='ready' and reopened['id']==initial['id']
    assert reopened['image']['repairError']=='SCENE_STATUS_UNKNOWN'
    fresh=request(client,w,'fresh-after-failed-visual-repair',True)
    assert fresh['generation']==initial['generation']+2
    assert fresh['id'] not in (initial['id'],child_id)


def test_repair_respects_existing_daily_limit(client,scene_world,profile):
    initial=request(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);row.status='ready'
        db.get(SceneProfile,row.profile_id).daily_limit=1
        assert queue_visual_repair(db,row,scene_view(db.get(WorldCheckpoint,scene_world['cp'])),['missing']) is None


def test_upgrade_binding_of_old_received_picture_only_once(client,scene_world,profile):
    initial=request(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);row.status='ready';row.image={'anchorVersion':2,'anchorStatus':'complete'}
        with patch('app.scenes.binding.settings.ai_mode','live'):
            queue_binding(db,row);task_id=row.image['bindingTaskId'];queue_binding(db,row)
        assert row.image['anchorVersion']==VERSION
        assert row.image['bindingTaskId']==task_id and db.get(Task,task_id)
        assert db.scalar(select(func.count()).select_from(Task).where(Task.task_type=='world_scene_binding'))==1


def completed_bound_picture(client,w,geometry_version=4):
    from app.scenes.binding import inspection_context
    from app.scenes.service import save_image
    from app.world.worker import process
    initial=request(client,w)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);data,metadata,request_id=generated()
        row.descriptor={**row.descriptor,'environmentGeometryVersion':geometry_version}
        row.image=save_image(row,data,metadata,request_id);row.status='ready'
        targets=inspection_targets(scene_view(db.get(WorldCheckpoint,w['cp'])))
        part=revisited_room_measurements()['characters'][1]
        raw={'characters':[{**deepcopy(part),'entityId':p['entityId']} for p in row.descriptor['sceneCast']],
             'composition':{'projection':'equirectangular','confidence':.95,'spaceKind':'interior','openSky':False,
                            'visiblePeopleCount':len(row.descriptor['sceneCast']),'unexpectedPeople':[],'referenceArtifacts':[],
                            'finishedArtwork':True,'unpaintedGuideRegions':[]},
             'fixtures':[{'id':p['id'],'visible':True,'confidence':.95,'viewIndex':0,'anchorPixels':[100,430]} for p in targets['fixtures']],
             'exits':[{'id':p['id'],'visible':True,'passable':True,'confidence':.95,'viewIndex':2,'anchorPixels':[175,430],
                       'passageEvidence':{'kind':'door-open','viewIndex':2,'boxPixels':[100,125,235,435],
                                          'description':'The actual open passage continues across the visible floor.'}} for p in targets['exits']]}
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
        task_id=row.image['bindingTaskId'];db.commit()
    with patch('app.worker.call_provider',return_value={'text':json.dumps(raw),'finishReason':'stop'}):process(task_id)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);task=db.get(Task,task_id)
        assert row.image['anchorStatus']=='complete'
        assert task.checkpoint['verifiedImageInspection']['result']==raw
        task.provider_state='completed';db.commit()
    return initial['id'],task_id


def test_artwork_contract_upgrade_cannot_restore_old_complete_exact_pixel_receipt(client,scene_world,profile):
    from app.scenes.binding import restore_verified_inspection,inspection_context
    from app.scenes.grounding import binding_matches
    ident,task_id=completed_bound_picture(client,scene_world,geometry_version=3)
    with SessionLocal() as db:
        row=db.get(SceneImage,ident);task=db.get(Task,task_id);original=deepcopy(task.checkpoint)
        saved=deepcopy(original);saved['verifiedImageInspection']['result']['composition'].pop('finishedArtwork')
        saved['verifiedImageInspection']['result']['composition'].pop('unpaintedGuideRegions')
        task.checkpoint=saved
        row.descriptor={**row.descriptor,'environmentGeometryVersion':4}
        targets=inspection_targets(scene_view(db.get(WorldCheckpoint,row.checkpoint_id),row.line_id))
        assert inspection_context(row,targets)!=original['verifiedImageInspection']['context']
        row.image={**row.image,'composition':{k:v for k,v in row.image['composition'].items()
                   if k not in ('finishedArtwork','unpaintedGuideRegions')}}
        assert not binding_matches(row)
        assert not restore_verified_inspection(db,row)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
        assert row.image['anchorStatus']=='queued' and row.image['bindingTaskId']!=task_id
        assert task.checkpoint==saved  # Original paid proof is not rewritten.


def test_missing_artwork_measurement_retries_same_pixels_twice_without_repainting(client,scene_world,profile):
    initial=request(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);row.status='ready';row.image={'sha256':'same-received-pixels'}
        with patch('app.scenes.binding.settings.ai_mode','live'):
            queue_binding(db,row)
            identifiers=[row.image['bindingTaskId']]
            for retry in range(3):
                task=db.get(Task,row.image['bindingTaskId']);task.state='succeeded';task.provider_state='completed'
                row.image={**row.image,'anchorStatus':'unavailable','detectionIssues':[],
                    'composition':{'verified':False,'issues':['artwork-unconfirmed']}}
                queue_binding(db,row)
                if retry<2:
                    assert row.image['bindingTaskId'] not in identifiers
                    identifiers.append(row.image['bindingTaskId'])
                    assert row.image['bindingEvidenceRetries']==retry+1
                else:assert row.image['bindingTaskId']==identifiers[-1]
        assert len(identifiers)==3 and db.scalar(select(func.count()).select_from(SceneImage))==1
        assert row.image['sha256']=='same-received-pixels'


def test_exact_image_verified_receipt_is_revalidated_without_a_paid_call(client,scene_world,profile):
    ident,task_id=completed_bound_picture(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,ident);old=deepcopy(row.image);count=db.scalar(select(func.count()).select_from(Task))
        row.image={**row.image,'anchorVersion':VERSION-1,'anchorStatus':'unavailable','characterAnchors':{},'spatialAnchors':{}}
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
        assert row.image['anchorStatus']=='complete' and row.image['bindingReusedTaskId']==task_id
        assert row.image['characterAnchors']==old['characterAnchors'] and row.image['sha256']==old['sha256']
        assert db.scalar(select(func.count()).select_from(Task))==count


@pytest.mark.parametrize('mismatch',[None,'parent','hash','context'])
@pytest.mark.parametrize('scene_review',[False,True])
def test_published_picture_reuses_only_its_owned_exact_final_private_inspection(client,scene_world,profile,mismatch,scene_review):
    from app.models import uid
    from app.scenes.binding import restore_verified_inspection,inspection_context
    ident,task_id=completed_bound_picture(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,ident);task=db.get(Task,task_id)
        if scene_review:row.descriptor={**row.descriptor,'sceneReviewVersion':1}
        descriptor={**row.descriptor,'pipelineParentId':ident,'expectedPoses':{'peterson':'standing'}}
        child=SceneImage(id=uid(),owner_id=row.owner_id,world_id=row.world_id,line_id=row.line_id,
            checkpoint_id=row.checkpoint_id,profile_id=row.profile_id,profile_version=row.profile_version,
            scene_hash=row.scene_hash,generation=row.generation+1,status='ready',descriptor=descriptor,image=deepcopy(row.image))
        targets=inspection_targets(scene_view(db.get(WorldCheckpoint,row.checkpoint_id),row.line_id))
        saved=deepcopy(task.checkpoint);proof=saved['verifiedImageInspection']
        if scene_review:
            from .test_scene_progressive import observation
            proof['result']['sceneReview']={'version':1,'imageHash':row.image['sha256'],
                'observation':observation(len(row.descriptor['sceneCast']),[p['entityId'] for p in row.descriptor['sceneCast']])}
        proof.update(sceneId=child.id,context=inspection_context(child,targets))
        task.checkpoint=saved;task.payload={**task.payload,'sceneId':child.id}
        if mismatch=='parent':child.descriptor={**descriptor,'pipelineParentId':'unrelated-parent'}
        if mismatch=='hash':child.image={**child.image,'sha256':'different-pixels'}
        if mismatch=='context':child.descriptor={**descriptor,'description':'another scene'}
        db.add(child);db.flush()
        row.image={**row.image,'anchorVersion':VERSION-1,'bindingTaskId':None,'anchorStatus':'unavailable','characterAnchors':{},'spatialAnchors':{}}
        assert restore_verified_inspection(db,row) is (mismatch is None)
        if mismatch is None:assert row.image['bindingReusedTaskId']==task_id and row.image['anchorStatus']=='complete'


@pytest.mark.parametrize('mismatch',['pixels','context','result','identity','unknown'])
def test_verified_receipt_cannot_authorize_other_pixels_context_or_unresolved_requests(client,scene_world,profile,mismatch):
    from app.scenes.binding import restore_verified_inspection
    ident,task_id=completed_bound_picture(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,ident);task=db.get(Task,task_id);saved=deepcopy(task.checkpoint)
        if mismatch=='pixels':saved['verifiedImageInspection']['imageHash']='different-image'
        elif mismatch=='context':saved['verifiedImageInspection']['context']='different-cast-or-targets'
        elif mismatch=='result':saved['verifiedImageInspection']['result']['characters'][0]['grounding']='floating'
        elif mismatch=='identity':task.payload={**task.payload,'sceneId':'foreign-scene'}
        elif mismatch=='unknown':task.provider_state='unknown'
        task.checkpoint=saved
        row.image={**row.image,'anchorVersion':VERSION-1,'anchorStatus':'unavailable','characterAnchors':{},'spatialAnchors':{}}
        assert restore_verified_inspection(db,row) is False
        if mismatch=='unknown':
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            assert row.image['bindingTaskId']==task_id and row.image['anchorStatus']=='unavailable'


@pytest.mark.parametrize('anchor_version', [VERSION,VERSION-1])
@pytest.mark.parametrize('provider_state,error', [('unknown','PROVIDER_CONNECTION_FAILED'),
    ('sent','PROVIDER_CONNECTION_FAILED'),('completed','PROVIDER_CONNECTION_FAILED'),
    ('not_sent','MODEL_BUDGET_EXHAUSTED')])
def test_visual_inspection_never_repeats_a_sent_or_ambiguous_request(client,scene_world,profile,provider_state,error,anchor_version):
    from datetime import timedelta
    from app.models import now
    initial=request(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);row.status='ready';row.image={'sha256':'same-pixels'}
        with patch('app.scenes.binding.settings.ai_mode','live'):
            queue_binding(db,row);prior=db.get(Task,row.image['bindingTaskId'])
            prior.state='failed';prior.provider_state=provider_state;prior.error_code=error
            prior.created_at=now()-timedelta(seconds=10)
            row.image={**row.image,'anchorStatus':'unavailable','anchorVersion':anchor_version}
            queue_binding(db,row)
        if provider_state in ('unknown','sent') or anchor_version==VERSION:assert row.image['bindingTaskId']==prior.id
        else:return  # A confirmed completed old-version inspection can be upgraded.
        assert row.image['sha256']=='same-pixels'
        assert db.scalar(select(func.count()).select_from(Task).where(Task.task_type=='world_scene_binding'))==1


def test_visual_connection_recovery_is_bounded_and_keeps_the_received_picture(client,scene_world,profile):
    from datetime import timedelta
    from app.models import now
    initial=request(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);row.status='ready';row.image={'sha256':'same-pixels'}
        with patch('app.scenes.binding.settings.ai_mode','live'):
            queue_binding(db,row)
            for attempt in range(3):
                prior=db.get(Task,row.image['bindingTaskId'])
                prior.state='failed';prior.provider_state='not_sent';prior.error_code='PROVIDER_CONNECTION_FAILED'
                prior.created_at=now()-timedelta(seconds=10)
                row.image={**row.image,'anchorStatus':'unavailable'}
                queue_binding(db,row)
                if attempt<2:
                    assert row.image['bindingTaskId']==prior.id
                    assert row.image['bindingConnectionRetries']==attempt+1
                    task_id=row.image['bindingTaskId'];queue_binding(db,row)
                    assert row.image['bindingTaskId']==task_id
                else:assert row.image['bindingTaskId']==prior.id
        assert row.image['sha256']=='same-pixels'
        assert db.scalar(select(func.count()).select_from(Task).where(Task.task_type=='world_scene_binding'))==1
        assert db.scalar(select(func.count()).select_from(SceneImage))==1


def test_no_corrective_picture_when_inspection_budget_is_exhausted(client,scene_world,profile):
    from app.models import ModelProfile
    initial=request(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);row.status='ready'
        model=db.get(ModelProfile,profile['id']);model.calls_reserved=model.call_budget
        assert queue_visual_repair(db,row,scene_view(db.get(WorldCheckpoint,scene_world['cp'])),['missing']) is None
        assert db.scalar(select(func.count()).select_from(SceneImage))==1


@pytest.mark.parametrize('response,expected', [('[]','unavailable'),('{"characters":[]}','unavailable'),('{"characters":[],"composition":{"projection":"equirectangular","spaceKind":"interior","openSky":false,"confidence":0.95,"visiblePeopleCount":0,"unexpectedPeople":[],"referenceArtifacts":[],"finishedArtwork":true,"unpaintedGuideRegions":[]}}','repairing')])
def test_binding_distinguishes_missing_cast_from_invalid_inspection(client,scene_world,profile,response,expected):
    from app.world.worker import process
    from app.scenes.service import save_image
    initial=request(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);data,metadata,request_id=generated()
        row.image=save_image(row,data,metadata,request_id);row.status='ready'
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
        task_id=row.image['bindingTaskId'];db.commit()
    with patch('app.worker.call_provider',return_value={'text':response,'finishReason':'stop'}):process(task_id)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);assert row.image['anchorStatus']==expected
        assert db.get(Task,task_id).state=='succeeded'
        assert bool(row.image.get('repairSceneId'))==(expected=='repairing')
        assert db.get(WorldLine,scene_world['line']).head_checkpoint_id==scene_world['cp']


def test_observer_identity_does_not_hide_a_visible_novel_protagonist():
    from app.scenes.provider import prompt_for
    from app.scenes.characters import character_descriptor
    boy={'id':'boy','name':'萧炎','description':'萧家少年。'}
    art=character_descriptor(boy,'斗破苍穹 · 乌坦城风起')
    assert 'young Chinese MALE' in art['appearance']
    prompt=prompt_for({'description':'山台','pointLabel':'山台','placeName':'后山','lighting':'清晨',
                       'observerName':'药老','sceneCast':[{'entityId':'boy','name':'萧炎'}],
                       'visualRepairAttempt':1,'missingCharacterIds':['boy']})
    assert 'viewpoint observer is 药老' in prompt and 'REQUIRED visible people count: 1' in prompt and 'VISUAL CORRECTION' in prompt


def test_inspection_does_not_repair_a_scene_after_player_left_during_model_call(client,scene_world,profile):
    from app.world.worker import process
    from app.scenes.service import save_image
    from app.models import uid
    w=scene_world;initial=request(client,w)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id']);data,metadata,request_id=generated()
        row.image=save_image(row,data,metadata,request_id);row.status='ready'
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
        task_id=row.image['bindingTaskId'];db.commit()
    def reply(*args,**kwargs):
        with SessionLocal() as db:
            previous=db.get(WorldCheckpoint,w['cp']);state=deepcopy(previous.state);state['timeLabel']='深夜'
            fresh=WorldCheckpoint(id=uid(),owner_id=previous.owner_id,world_id=previous.world_id,line_id=previous.line_id,
                parent_checkpoint_id=previous.id,sequence=previous.sequence+1,title='夜晚新状态',text='',action='',
                state=state,definition=deepcopy(previous.definition),options=[],changes=[],summary='',memory={},content_hash='a'*64,kind='turn')
            db.add(fresh);db.flush();db.get(WorldLine,w['line']).head_checkpoint_id=fresh.id;db.commit()
        return {'text':'{"characters":[],"fixtures":[],"exits":[],"composition":{"projection":"equirectangular","spaceKind":"interior","openSky":false,"confidence":0.95,"visiblePeopleCount":0,"unexpectedPeople":[],"referenceArtifacts":[],"finishedArtwork":true,"unpaintedGuideRegions":[]}}','finishReason':'stop'}
    with patch('app.worker.call_provider',side_effect=reply):process(task_id)
    with SessionLocal() as db:
        row=db.get(SceneImage,initial['id'])
        assert row.image['anchorStatus']=='incomplete' and not row.image.get('repairSceneId')
        assert db.scalar(select(func.count()).select_from(SceneImage))==1
