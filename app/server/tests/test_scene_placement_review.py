import io,json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PIL import Image
from app.errors import Problem
from app.scenes.placements import inspect_placements
from app.scenes.binding import inspection_stages


def camera():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='JPEG');return out.getvalue()


def placement(view=0):
    return {'entityId':'npc','viewIndex':view,'bodyBoxPixels':[180,160,270,480],'groundPointPixels':[225,480],
            'pose':'standing','confidence':.95,'evidence':'Measured clear stone floor beside the wooden counter with adult headroom.'}


def test_remeasure_invalid_downward_placement_in_one_actual_horizontal_image():
    parsed={'placements':[placement(6)],'exits':[],'composition':{'verified':True}};stages=[]
    def call(stage,prompt,data,limit):
        stages.append(stage);assert len(data['_sceneImages'])==1
        assert prompt=='world_scene_placements'
        assert data['camera']['viewIndex']==0
        return {'text':json.dumps({'placements':[placement()]})}
    result=inspect_placements(parsed,[{'entityId':'npc','name':'店员'}],[camera()]*8,'店员站在柜台边。',call,16384)
    assert result['placements']==[placement()] and result['composition']==parsed['composition']
    assert stages==['analysis:placement:0'] and parsed['placements'][0]['viewIndex']==6


def test_absent_or_wrong_camera_proposal_never_becomes_a_placement():
    call=Mock(return_value={'text':json.dumps({'placements':[placement(6)]})})
    result=inspect_placements({'exits':[]},[{'entityId':'npc'}],[camera()]*8,'',call,16384)
    assert result['placements']==[] and call.call_count==4


def test_unknown_placement_request_is_not_replayed():
    call=Mock(side_effect=Problem('PROVIDER_STATUS_UNKNOWN','unknown'))
    with pytest.raises(Problem):inspect_placements({'exits':[]},[{'entityId':'npc'}],[camera()]*8,'',call,16384)
    assert call.call_count==1


def test_horizontal_placement_cannot_cover_a_verified_doorway():
    door={'visible':True,'passageEvidence':{'viewIndex':0,'boxPixels':[175,100,280,480]}}
    call=Mock(return_value={'text':json.dumps({'placements':[placement()]})})
    result=inspect_placements({'exits':[door]},[{'entityId':'npc'}],[camera()]*8,'',call,16384)
    assert result['placements']==[] and result['exits']==[door]


def test_binding_reserves_the_bounded_extra_views_before_dispatch():
    row=SimpleNamespace(descriptor={'futureCast':[{'entityId':'npc'}]})
    assert len(inspection_stages(row,{'exits':[]}))==5
    assert len(inspection_stages(SimpleNamespace(descriptor={}),{'exits':[]}))==1


def test_explicit_absence_with_placeholder_zero_boxes_keeps_a_missing_character():
    from app.scenes.binding import evaluate_inspection
    result={'composition':{'projection':'equirectangular','confidence':.95,'visiblePeopleCount':0,
                          'unexpectedPeople':[],'referenceArtifacts':[]},
            'characters':[{'entityId':'npc','viewIndex':0,'visible':False,'physicalPerson':False,
                           'headBoxPixels':[0,0,0,0],'bodyBoxPixels':[0,0,0,0]}],
            'fixtures':[],'exits':[]}
    checked=evaluate_inspection(result,[{'entityId':'npc'}],{'fixtures':[],'exits':[]},(2048,1024),{})
    assert checked['missingCharacterIds']==['npc'] and checked['characterAnchors']=={}
    assert checked['anchorStatus']=='incomplete'
    assert checked['detectionIssues']==[]


def test_actor_edit_keeps_authored_identity_description_with_generic_appearance():
    from app.scenes.provider import prompt_for
    prompt=prompt_for({'renderStage':'actor','paintedActor':{'name':'唐禾','description':'穿蓝色围裙的女性书店店员','appearance':'Preserve the public age, gender and occupation.'},'placement':placement()})
    assert '穿蓝色围裙的女性书店店员' in prompt


def test_actor_camera_recentres_measured_rays_without_moving_the_actual_person():
    from app.scenes.perspective import centred_actor_placement,edit_camera_yaw
    from app.scenes.inspection import sphere_pixel
    original=placement();original.update(bodyBoxPixels=[400,160,480,464],groundPointPixels=[440,464])
    centred=centred_actor_placement(original)
    before=sphere_pixel(0,*original['groundPointPixels'],2048,1024)
    after=sphere_pixel(0,*centred['groundPointPixels'],2048,1024,camera_yaw=centred['cameraYaw'])
    assert after==pytest.approx(before,abs=1e-7)
    assert 240<(centred['bodyBoxPixels'][0]+centred['bodyBoxPixels'][2])/2<272
    assert original['bodyBoxPixels']==[400,160,480,464]
    assert edit_camera_yaw({'renderStage':'actor','placement':centred},0)==centred['cameraYaw']


def test_standing_adult_cannot_be_placed_entirely_below_an_eye_height_horizon():
    invalid=placement();invalid['bodyBoxPixels']=[416,288,480,464];invalid['groundPointPixels']=[448,464]
    call=Mock(return_value={'text':json.dumps({'placements':[]})})
    checked=inspect_placements({'placements':[invalid],'exits':[]},[{'entityId':'npc'}],[camera()]*8,'',call,16384)
    assert checked['placements']==[] and call.call_count==4


def test_new_contract_rechecks_bulk_proposal_in_actual_single_camera():
    proposal={'placements':[placement()],'exits':[]}
    call=Mock(return_value={'text':json.dumps({'placements':[]})})
    checked=inspect_placements(proposal,[{'entityId':'npc'}], [camera()]*8,'',call,16384,independent=True)
    assert checked['placements']==[] and call.call_count==4


def test_new_camera_pose_contract_rejects_countertop_seating_without_forcing_camera_distance():
    from app.scenes.placements import natural_slot
    seated={**placement(),'pose':'seated'}
    assert not natural_slot(seated,strict=True)
    seated.update(bodyBoxPixels=[210,290,290,470],groundPointPixels=[250,470])
    assert natural_slot(seated,strict=True)
    seated.update(bodyBoxPixels=[210,260,270,410],groundPointPixels=[240,410])
    assert natural_slot(seated,strict=True)


def test_bounded_correction_remeasures_completed_malformed_reply_without_accepting_it():
    stages=[]
    bad=placement();bad['bodyBoxPixels']=[180,160,199,480]
    def call(stage,prompt,data,limit):
        stages.append(stage)
        if stage=='analysis:placement:0':return {'text':json.dumps({'placements':[bad]})}
        if stage=='analysis:slot-fix:0':
            assert len(stage)<=30
            assert data['rejectedProposal']==[bad]
            return {'text':json.dumps({'placements':[placement()]})}
        return {'text':'{"placements":[]}'}
    result=inspect_placements({'exits':[]},[{'entityId':'npc'}],[camera()]*8,'',call,16384,independent=True,correct_invalid=True)
    assert result['placements']==[placement()] and len(stages)==5
    row=SimpleNamespace(descriptor={'futureCast':[{'entityId':'npc'}],'placementInspectionVersion':2})
    assert len(inspection_stages(row,{'exits':[]}))==7


def test_downward_camera_keeps_seat_and_floor_inside_one_measured_view():
    proposal={**placement(4),'bodyBoxPixels':[180,150,270,360],
        'groundPointPixels':[225,360],'pose':'seated'}
    calls=[]
    def call(stage,prompt,data,limit):
        calls.append(stage);index=data['camera']['viewIndex']
        if index==4:
            assert data['camera']['pitchDegrees']==pytest.approx(-35)
            return {'text':json.dumps({'placements':[proposal]})}
        return {'text':'{"placements":[]}'}
    result=inspect_placements({'exits':[]},[{'entityId':'npc','expectedPose':'seated'}],
        [camera()]*8,'',call,1000,independent=True,downward=True)
    assert result['placements'][0]['viewIndex']==4 and result['placements'][0]['cameraPitchVersion']==1
    assert len(calls)==5
    from app.scenes.pipeline import validate_placements
    assert not validate_placements({'placements':[proposal]},[{'entityId':'npc'}])
    assert validate_placements({'placements':result['placements']},[{'entityId':'npc'}])


def test_downward_paint_is_projected_to_measured_ground_rays_not_the_horizontal_camera():
    from app.scenes.perspective import reproject_camera,centred_actor_placement
    from app.scenes.inspection import sphere_pixel
    source=io.BytesIO();Image.new('RGB',(512,256),'blue').save(source,format='PNG')
    paint=io.BytesIO();Image.new('RGB',(512,512),'red').save(paint,format='PNG')
    slot={**placement(4),'bodyBoxPixels':[180,150,270,360],
        'groundPointPixels':[225,360],'pose':'seated','cameraPitchVersion':1,'maskVersion':3}
    output,_=reproject_camera(source.getvalue(),paint.getvalue(),4,slot)
    picture=Image.open(io.BytesIO(output));actual=tuple(round(v) for v in sphere_pixel(4,225,255,512,256))
    wrong=tuple(round(v) for v in sphere_pixel(0,225,255,512,256))
    assert picture.getpixel(actual)[0]>220 and picture.getpixel(wrong)[2]>220
    centred=centred_actor_placement(slot)
    before=sphere_pixel(4,*slot['groundPointPixels'],2048,1024)
    after=sphere_pixel(4,*centred['groundPointPixels'],2048,1024,camera_yaw=centred['cameraYaw'])
    assert after==pytest.approx(before,abs=1e-7)


def test_private_pose_guide_uses_actual_measured_foot_contact_not_the_body_centre():
    from app.scenes.perspective import camera_reference
    source=io.BytesIO();Image.new('RGB',(1024,512),'blue').save(source,format='PNG')
    slot={**placement(),'pose':'seated','bodyBoxPixels':[180,280,280,470],'groundPointPixels':[275,465]}
    image=Image.open(io.BytesIO(camera_reference(source.getvalue(),0,slot)))
    assert image.getpixel((275,455))==(84,92,87)
    assert image.getpixel((198,455))[2]>240
