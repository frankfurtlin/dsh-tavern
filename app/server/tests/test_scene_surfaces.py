"""Reject the observed brick-wall far point without swallowing received art."""
import base64,io
from copy import deepcopy
from PIL import Image
import pytest
from app.scenes.surfaces import point_request,apply_surface,surface_valid
from app.scenes.passages import apply_review,shared_aperture,distinct_passages
from app.scenes.grounding import spatial_anchors_from_result
from app.scenes.inspection import panorama_coordinates


def candidate():
    # Actual v64 residential candidate: its far point was on brick masonry.
    answer={'id':'home','image':1,'matched':True,'continuous':True,'confidence':.9,'kind':'stairs-open',
            'thresholdBoxPixels':[194,274,268,374],'anchorPixels':[230,370],'beyondPointPixels':[230,330],
            'apertureBoxPixels':[194,274,268,350],'throughPointPixels':[230,310],
            'evidence':'Reported stone steps and visible far floor in the same opening.'}
    region={'home':{'singleImage':True,'viewIndex':1,'bounds':[0,0,512,512],'allowedKinds':['stairs-open'],'requiredOpeningProof':True}}
    return apply_review({}, {'routes':[answer]},region)['exits'][0]


def observation(surface='walkable-ground'):
    return {'A':{'surface':'walkable-ground','confidence':.95,'evidence':'Actual stone steps at the cyan ring centre.'},
            'B':{'surface':surface,'confidence':.95,'evidence':'Actual surface at the magenta ring centre.'},
            'C':{'surface':'walkable-ground','confidence':.95,'evidence':'Actual interior floor beyond the same threshold.'},
            'openingConfirmed':True,'crossedThreshold':True,'sameOpening':True,'unobstructed':True,'confidence':.95}


@pytest.mark.parametrize('surface',['wall','furniture','closed-door','unknown'])
def test_actual_point_rejection_blocks_the_v64_positive_wall_candidate(surface):
    raw=candidate();part=apply_surface(raw,observation(surface),'a'*64)
    assert raw['passageEvidence']['independentReview']['matched'] is True
    assert part['passageEvidence']['independentReview']['matched'] is False
    assert part['passageEvidence']['independentReview']['status']=='unconfirmed'
    measured=panorama_coordinates({'exits':[part]},(2048,1024))
    target={'exits':[{'id':'home','independentReviewRequired':True,'requiredSurfaceReview':True}]}
    assert spatial_anchors_from_result(measured,target,(2048,1024),require_evidence=True)=={}


def test_verified_point_proof_is_bound_to_both_exact_points_and_the_owned_view():
    part=apply_surface(candidate(),observation(),'a'*64);proof=part['passageEvidence']['surfaceReview']
    assert surface_valid(proof,[230,370],[230,330],1)
    assert not surface_valid(proof,[231,370],[230,330],1)
    assert not surface_valid(proof,[230,370],[230,331],1)
    assert not surface_valid(proof,[230,370],[230,330],0)
    for key in ['A','B','C','openingConfirmed','crossedThreshold','sameOpening','unobstructed','confidence']:
        absent=deepcopy(proof);absent['result'].pop(key)
        assert not surface_valid(absent,[230,370],[230,330],1)
    measured=panorama_coordinates({'exits':[part]},(2048,1024))
    target={'exits':[{'id':'home','independentReviewRequired':True,'requiredSurfaceReview':True}]}
    assert 'exits:home' in spatial_anchors_from_result(measured,target,(2048,1024),require_evidence=True)


def test_blind_point_request_uses_original_centres_and_never_supplies_route_desire():
    picture=Image.new('RGB',(512,512),'#654732')
    # A real grey stone ground spot and B brick wall; the exact original centre
    # pixels must survive crop enlargement and locator rings unchanged.
    picture.paste('#bab8ac',(200,350,260,390));out=io.BytesIO();picture.save(out,format='PNG')
    data=point_request(candidate(),out.getvalue())
    assert 'home' not in str({k:v for k,v in data.items() if k!='_sceneImages'})
    assert len(data['_sceneImages'])==5 and 'matched' not in data
    for index,colour in [(1,(186,184,172)),(2,(101,71,50))]:
        im=Image.open(io.BytesIO(base64.b64decode(data['_sceneImages'][index].split(',',1)[1])))
        x,y=data['points'][index-1]['centrePixels']
        assert im.format=='JPEG'
        assert max(abs(a-b) for a,b in zip(im.getpixel((round(x),round(y))),colour))<=3


def test_point_crops_reach_the_actual_provider_transport(monkeypatch):
    import json
    from .test_provider_transport import setup_transport,success
    from app.providers import ChatCompletionsAdapter
    async def respond(request,attempt):return success()
    profile,requests=setup_transport(monkeypatch,respond)
    image=io.BytesIO();Image.new('RGB',(512,512),'#bab8ac').save(image,format='JPEG')
    data=point_request(candidate(),image.getvalue());data.pop('_visionStream')
    ChatCompletionsAdapter().generate(profile,'Blind points',data,100)
    content=json.loads(requests[0].content)['messages'][1]['content']
    assert len(content)==6 and all(p['type']=='image_url' for p in content[1:])
    assert all(p['image_url']['url'].startswith('data:image/jpeg;base64,') for p in content[1:])
    assert 'home' not in content[0]['text']


def test_local_image_preflight_failure_is_known_unsent_and_never_dispatches(monkeypatch):
    from .test_provider_transport import setup_transport,success
    from app.providers import ChatCompletionsAdapter
    from app.provider_transport import NOT_SENT_CODES
    from app.errors import Problem
    async def respond(request,attempt):return success()
    profile,requests=setup_transport(monkeypatch,respond)
    with pytest.raises(Problem) as failed:
        ChatCompletionsAdapter().generate(profile,'Blind points',{'_sceneImages':['data:image/png;base64,AAAA']},100)
    assert failed.value.code in NOT_SENT_CODES
    assert failed.value.details=={'requestSent':False,'responseReceived':False} and requests==[]


def test_detailed_point_images_stay_inside_provider_byte_limits():
    image=Image.effect_noise((512,512),100).convert('RGB');out=io.BytesIO();image.save(out,format='PNG')
    data=point_request(candidate(),out.getvalue())
    assert all(len(p)<=350000 for p in data['_sceneImages'])


def test_actual_v66_stairs_beside_the_door_are_not_a_crossing():
    from app.scenes.surfaces import opening_geometry
    evidence={'apertureBoxPixels':[286,239,311,355],'throughPointPixels':[298,315]}
    assert not opening_geometry(evidence,[209,367],[209,340])
    # Keep a real interior-ground path as positive; no fixed point distance.
    assert opening_geometry({'apertureBoxPixels':[235,190,310,345],
                             'throughPointPixels':[273,320]},[273,373],[273,340])
    result=observation();result.update(openingConfirmed=False,crossedThreshold=False)
    assert not surface_valid(apply_surface(candidate(),result,'a'*64)['passageEvidence']['surfaceReview'],[230,370],[230,330],1)


def test_old_two_ground_points_cannot_restore_new_aperture_proof():
    part=apply_surface(candidate(),observation(),'a'*64);proof=part['passageEvidence']['surfaceReview']
    proof['source']='same-image-point-crops'
    assert not surface_valid(proof,[230,370],[230,330],1)


@pytest.mark.parametrize('kind',['door-open','path-open','stairs-open'])
def test_blind_surface_contract_distinguishes_a_road_from_a_doorway(kind):
    part=candidate();part['passageEvidence']['kind']=kind
    picture=io.BytesIO();Image.new('RGB',(512,512),'#bab8ac').save(picture,format='PNG')
    data=point_request(part,picture.getvalue())
    assert data['passageKind']==kind and 'home' not in str({k:v for k,v in data.items() if k!='_sceneImages'})
    if kind=='path-open':assert 'door frame, jamb, door leaf or material threshold is NOT required' in data['instruction']
    else:assert 'physically open doorway above any approach steps' in data['instruction']
    proof=apply_surface(part,observation(),'a'*64)['passageEvidence']['surfaceReview']
    assert surface_valid(proof,[230,370],[230,330],1,kind)
    other='path-open' if kind!='path-open' else 'door-open'
    assert not surface_valid(proof,[230,370],[230,330],1,other)
    for surface in ('wall','furniture','unknown','closed-door'):
        failed=apply_surface(part,observation(surface),'a'*64)['passageEvidence']['surfaceReview']
        assert not surface_valid(failed,[230,370],[230,330],1,kind)


def test_kindless_legacy_door_proof_does_not_certify_the_open_aisle_contract():
    proof=apply_surface(candidate(),observation(),'a'*64)['passageEvidence']['surfaceReview']
    proof['source']='same-image-opening-crops-v2';proof.pop('passageKind')
    assert surface_valid(proof,[230,370],[230,330],1,'door-open')
    assert surface_valid(proof,[230,370],[230,330],1,'stairs-open')
    assert not surface_valid(proof,[230,370],[230,330],1,'path-open')


def measured_door(ident,view,box,point,confidence=.95):
    return {'id':ident,'visible':True,'passable':True,'confidence':confidence,
            'passageEvidence':{'viewIndex':view,'apertureBoxPixels':box,'throughPointPixels':point,
                'independentReview':{'matched':True}}}


def test_same_real_door_in_horizontal_and_downward_camera_is_conflicting():
    from app.scenes.inspection import sphere_pixel,camera_pixel
    first=measured_door('inn',0,[220,185,320,350],[275,320])
    points=[camera_pixel(4,*sphere_pixel(0,x,y,2048,1024),2048,1024)
            for x,y in [(220,185),(320,350),(275,320)]]
    second=measured_door('home',4,[max(0,points[0][0]),max(0,points[0][1]),
                                  min(512,points[1][0]),min(512,points[1][1])],list(points[2]))
    assert shared_aperture(first,second) and shared_aperture(second,first)
    selected=distinct_passages({'inn':[first],'home':[second]})
    assert len(selected)==1
    alternative=measured_door('home',1,[240,210,310,345],[275,320],.90)
    selected=distinct_passages({'inn':[first],'home':[second,alternative]})
    assert selected['home']['passageEvidence']['viewIndex']==1 and len(selected)==2


def test_adjacent_real_doorways_are_distinct_without_marker_offsets():
    left=measured_door('left',0,[150,180,240,350],[215,310])
    right=measured_door('right',0,[260,180,350,350],[285,310])
    assert not shared_aperture(left,right)
    assert len(distinct_passages({'left':[left],'right':[right]}))==2


def test_same_aperture_cannot_escape_conflict_by_selecting_opposite_floor_edges():
    first=measured_door('inn',0,[200,150,300,350],[201,240])
    other=measured_door('home',0,[204,148,304,350],[303,260])
    assert shared_aperture(first,other)
    assert len(distinct_passages({'inn':[first],'home':[other]}))==1


def test_invalid_extra_route_cannot_poison_an_authorized_opening():
    part=apply_surface(candidate(),observation(),'a'*64)
    target={'exits':[{'id':'home','requiredSurfaceReview':True,'independentReviewRequired':True}]}
    unexpected=deepcopy(part);unexpected.update(id='extra-not-authorized',confidence=0)
    measured=panorama_coordinates({'exits':[part,unexpected]},(2048,1024))
    assert 'exits:home' in spatial_anchors_from_result(measured,target,(2048,1024),require_evidence=True)
    assert spatial_anchors_from_result({'exits':None},target,(2048,1024),require_evidence=True)=={}


@pytest.mark.parametrize('invalid_first',[False,True])
def test_invalid_same_id_duplicate_cannot_replace_a_validated_aperture(invalid_first):
    home=apply_surface(candidate(),observation(),'a'*64)
    inn=deepcopy(home);inn['id']='inn'
    invalid=deepcopy(home);invalid['confidence']=0
    invalid['passageEvidence']['apertureBoxPixels']=[20,20,70,90]
    invalid['passageEvidence']['throughPointPixels']=[45,60]
    parts=([invalid,home,inn] if invalid_first else [home,inn,invalid])
    target={'exits':[{'id':ident,'requiredSurfaceReview':True,'independentReviewRequired':True}
                      for ident in ('home','inn')]}
    measured=panorama_coordinates({'exits':parts},(2048,1024))
    assert spatial_anchors_from_result(measured,target,(2048,1024),require_evidence=True)=={}


def test_single_camera_surface_request_keeps_the_room_context_without_crop_planes():
    picture=io.BytesIO();Image.new('RGB',(512,512),'#bab8ac').save(picture,format='PNG')
    request=point_request(candidate(),picture.getvalue(),single_camera=True)
    assert len(request['_sceneImages'])==1
    assert request['inspectionMode']=='blind-single-camera-surface-points'
    assert request['points'][0]=={'label':'A','centrePixels':[230*1000/512,370*1000/512]}
    assert request['imageSize']=={'width':1000,'height':1000}
    assert 'home' not in str({k:v for k,v in request.items() if k!='_sceneImages'})
    received=Image.open(io.BytesIO(base64.b64decode(request['_sceneImages'][0].split(',',1)[1])))
    assert received.size==(1000,1000)


def test_context_receipt_requires_its_explicit_version_and_exact_observed_points():
    part=apply_surface(candidate(),observation(),'a'*64,single_camera=True)
    proof=part['passageEvidence']['surfaceReview']
    assert proof['source']=='same-image-opening-context-v4' and proof['contextVersion']==1
    assert surface_valid(proof,[230,370],[230,330],1,'stairs-open')
    assert not surface_valid(proof,[230,371],[230,330],1,'stairs-open')
    proof.pop('contextVersion')
    assert not surface_valid(proof,[230,370],[230,330],1,'stairs-open')


@pytest.mark.parametrize('surface',['wall','furniture','closed-door','unknown'])
def test_single_camera_context_never_overrides_actual_bad_surface(surface):
    part=apply_surface(candidate(),observation(surface),'a'*64,single_camera=True)
    assert part['passageEvidence']['independentReview']['matched'] is False
    assert not surface_valid(part['passageEvidence']['surfaceReview'],[230,370],[230,330],1,'stairs-open')


@pytest.mark.parametrize('field',['sameOpening','unobstructed','openingConfirmed','crossedThreshold'])
def test_single_camera_context_keeps_every_physical_crossing_gate(field):
    observed=observation();observed[field]=False
    part=apply_surface(candidate(),observed,'a'*64,single_camera=True)
    assert not surface_valid(part['passageEvidence']['surfaceReview'],[230,370],[230,330],1,'stairs-open')


@pytest.mark.parametrize('where',['overall','A','B','C'])
def test_supported_but_low_confidence_floor_requires_fresh_detail_above_existing_threshold(where):
    from app.scenes.surfaces import needs_detail
    original=observation()
    if where=='overall':original['confidence']=.82
    else:original[where]['confidence']=.82
    assert needs_detail(original)
    weak=apply_surface(candidate(),original,'a'*64,single_camera=True)
    assert not surface_valid(weak['passageEvidence']['surfaceReview'],[230,370],[230,330],1,'stairs-open')
    detailed=apply_surface(candidate(),observation(),'a'*64,single_camera=True)
    assert surface_valid(detailed['passageEvidence']['surfaceReview'],[230,370],[230,330],1,'stairs-open')
    assert detailed['passageEvidence']['surfaceReview']['source']=='same-image-opening-context-v4'


@pytest.mark.parametrize('field',['sameOpening','unobstructed','openingConfirmed','crossedThreshold'])
def test_rejected_crossing_cannot_be_promoted_by_surface_detail(field):
    from app.scenes.surfaces import needs_detail
    value=observation();value['confidence']=.82;value[field]=False
    assert not needs_detail(value)


@pytest.mark.parametrize('surface',['furniture','wall','closed-door','unknown'])
def test_surface_detail_does_not_turn_bad_or_hidden_ground_into_a_candidate(surface):
    from app.scenes.surfaces import needs_detail
    value=observation();value['confidence']=.82;value['B']['surface']=surface
    assert not needs_detail(value)


def test_surface_detail_keeps_original_points_in_one_context_with_an_explicit_call_bound():
    from app.scenes.surfaces import detail_request,needs_detail
    from app.scenes.binding import inspection_stages
    from types import SimpleNamespace
    picture=io.BytesIO();Image.new('RGB',(512,512),'#bab8ac').save(picture,format='PNG')
    data=detail_request(candidate(),picture.getvalue())
    assert data['inspectionMode']=='same-camera-surface-detail' and data['_visionStream'] is False
    assert len(data['_sceneImages'])==1
    image=Image.open(io.BytesIO(base64.b64decode(data['_sceneImages'][0].split(',',1)[1])))
    for point in data['points']:
        assert all(abs(a-b)<10 for a,b in zip(image.getpixel(tuple(round(n) for n in point['centrePixels'])),(186,184,172)))
    row=SimpleNamespace(descriptor={'surfaceClarificationVersion':1})
    assert len(inspection_stages(row,{'exits':[{'independentReviewRequired':True}]}))==25
    assert not needs_detail(observation())
