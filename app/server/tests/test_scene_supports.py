import io,json,base64
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image
import pytest
from app.errors import Problem
from app.scenes.supports import request,valid
from app.scenes.placements import inspect_placements
from app.scenes.binding import inspection_stages


@pytest.mark.parametrize('detail_confidence,accepted',[(.95,True),(.85,True),(.84,False),(.6,False)])
def test_low_confidence_support_gets_one_same_image_detail_without_relaxing_threshold(detail_confidence,accepted):
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='PNG');camera=out.getvalue();stages=[]
    sphere=io.BytesIO();Image.new('RGB',(2048,1024),'brown').save(sphere,format='PNG')
    def call(stage,prompt,data,limit):
        stages.append(stage)
        if stage.startswith('analysis:placement:'):
            return {'text':json.dumps({'placements':[proposal()] if data['camera']['viewIndex']==0 else []})}
        if stage.startswith('analysis:slot-detail:'):
            assert data['inspectionMode']=='proposed-body-support-detail' and data['_visionStream'] is False
            assert len(data['_sceneImages'])==1 and 'previous' not in data
            return {'text':json.dumps({**verdict(),'confidence':detail_confidence})}
        return {'text':json.dumps({**verdict(),'confidence':.82})}
    result=inspect_placements({'exits':[]},[{'entityId':'npc'}],[camera]*4,'',call,1000,
        independent=True,verify_support=True,single_context=True,clarify_support=True,panorama=sphere.getvalue())
    assert bool(result['placements']) is accepted
    assert stages.count('analysis:slot-detail:0:0')==1
    if accepted:assert result['placements']==[proposal()]


@pytest.mark.parametrize('field,value',[('footSurface','furniture'),('pelvisSupport','none'),('poseSupported',False),
    ('naturalAdultScale',False),('clearOfPassage',False),('pose','standing'),('confidence',.6)])
def test_known_negative_or_missing_support_is_not_upgraded_by_a_detail_call(field,value):
    from app.scenes.supports import needs_detail
    assert not needs_detail({'text':json.dumps({**verdict(),'confidence':.82,field:value})},'seated')


def test_unknown_support_detail_stops_without_replaying_the_paid_observation():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='PNG');stages=[]
    def call(stage,prompt,data,limit):
        stages.append(stage)
        if stage.startswith('analysis:placement:'):return {'text':json.dumps({'placements':[proposal()]})}
        if stage.startswith('analysis:slot-detail:'):raise Problem('PROVIDER_STATUS_UNKNOWN','unknown')
        return {'text':json.dumps({**verdict(),'confidence':.82})}
    with pytest.raises(Problem):inspect_placements({'exits':[]},[{'entityId':'npc'}],[out.getvalue()]*4,'',call,1000,
        independent=True,verify_support=True,single_context=True,clarify_support=True)
    assert stages==['analysis:placement:0','analysis:slot-check:0:0','analysis:slot-detail:0:0']


def test_support_detail_keeps_original_points_in_one_context_and_is_reserved_in_the_plan():
    from app.scenes.supports import detail_request
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='PNG')
    value=detail_request(proposal(),out.getvalue());assert len(value['_sceneImages'])==1
    im=Image.open(io.BytesIO(base64.b64decode(value['_sceneImages'][0].split(',',1)[1])))
    for point in value['points']:
        assert all(abs(a-b)<12 for a,b in zip(im.getpixel(tuple(round(p) for p in point['centrePixels'])),(165,42,42)))
    row=SimpleNamespace(descriptor={'futureCast':[{'entityId':'a'},{'entityId':'b'}],
        'placementInspectionVersion':2,'placementSupportVersion':1,'supportClarificationVersion':1})
    assert len(inspection_stages(row,{'exits':[]}))==31


def proposal():
    return {'entityId':'npc','viewIndex':0,'bodyBoxPixels':[180,290,270,470],
        'groundPointPixels':[225,470],'pose':'seated','confidence':.95,'evidence':'A proposed seated body on a visibly measured wooden bench.'}


def verdict():
    return {'pose':'seated','poseSupported':True,'naturalAdultScale':True,'clearOfPassage':True,
        'footSurface':'walkable-ground','pelvisSupport':'bench','confidence':.95,
        'evidence':'The actual wood seat under the proposed pelvis and stone floor under the shoes are visible.'}


def test_seated_support_needs_actual_seat_floor_scale_and_passage_evidence():
    good=verdict();assert valid({'text':json.dumps(good)},'seated')
    for field,value in [('pelvisSupport','none'),('footSurface','furniture'),('naturalAdultScale',False),
                        ('poseSupported',False),('clearOfPassage',False),('confidence',.7),('pose','standing')]:
        assert not valid({'text':json.dumps({**good,field:value})},'seated')


def test_support_request_uses_actual_unfilled_point_crops_not_painted_people():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='PNG')
    data=request(proposal(),out.getvalue());assert len(data['_sceneImages'])==3
    for uri,point in zip(data['_sceneImages'][1:],data['pointCrops']):
        im=Image.open(io.BytesIO(base64.b64decode(uri.split(',',1)[1])))
        # The centre is original brown, never replaced by the locating ring.
        centre=tuple(round(p) for p in point['centrePixels'])
        assert all(abs(a-b)<5 for a,b in zip(im.getpixel(centre),(165,42,42)))


def test_rejected_support_never_becomes_a_plan_and_unknown_support_is_not_replayed():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='JPEG');cameras=[out.getvalue()]*8
    stages=[]
    def call(stage,prompt,data,limit):
        stages.append(stage)
        if prompt=='world_scene_supports':return {'text':json.dumps({**verdict(),'pelvisSupport':'none'})}
        return {'text':json.dumps({'placements':[{**proposal(),'viewIndex':data['camera']['viewIndex']}]})}
    result=inspect_placements({'exits':[]},[{'entityId':'npc'}],cameras,'',call,1000,independent=True,verify_support=True)
    assert result['placements']==[] and len(stages)==8
    def unknown(stage,prompt,data,limit):
        if prompt=='world_scene_supports':raise Problem('PROVIDER_STATUS_UNKNOWN','unknown')
        return {'text':json.dumps({'placements':[proposal()]})}
    wrapped=Mock(side_effect=unknown)
    with pytest.raises(Problem):inspect_placements({'exits':[]},[{'entityId':'npc'}],cameras,'',wrapped,1000,independent=True,verify_support=True)
    assert wrapped.call_count==2
    row=SimpleNamespace(descriptor={'futureCast':[{'entityId':'a'},{'entityId':'b'}],
        'placementInspectionVersion':2,'placementSupportVersion':1})
    assert len(inspection_stages(row,{'exits':[]}))==19


def test_full_length_entity_ids_do_not_overflow_persisted_operation_names():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='JPEG')
    actor='a'*32;stages=[]
    def call(stage,prompt,data,limit):
        stages.append(stage);assert len(stage)<=30
        return {'text':json.dumps(verdict() if prompt=='world_scene_supports' else {'placements':[{**proposal(),'entityId':actor}]})}
    result=inspect_placements({'exits':[]},[{'entityId':actor}],[out.getvalue()]*8,'',call,1000,
        independent=True,verify_support=True)
    assert result['placements'][0]['entityId']==actor and stages[-1]=='analysis:slot-check:0:0'


def test_standing_checks_real_feet_without_inventing_a_seat_requirement():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='JPEG')
    data=request({**proposal(),'pose':'standing'},out.getvalue())
    assert len(data['_sceneImages'])==2 and data['pointCrops'][0]['label']=='shoes'
    assert valid({'text':json.dumps({**verdict(),'pose':'standing','pelvisSupport':'none'})},'standing')


def test_actual_seat_and_floor_review_replaces_an_assumed_camera_height_cutoff():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='JPEG')
    slot={**proposal(),'bodyBoxPixels':[180,240,270,440],'groundPointPixels':[225,440]}
    calls=[]
    def call(stage,prompt,data,limit):
        calls.append(stage)
        return {'text':json.dumps(verdict() if prompt=='world_scene_supports' else {'placements':[slot]})}
    result=inspect_placements({'exits':[]},[{'entityId':'npc'}],[out.getvalue()]*8,'',call,1000,
        independent=True,verify_support=True)
    assert result['placements']==[slot] and calls==['analysis:placement:0','analysis:slot-check:0:0']
    assert not valid({'text':json.dumps({**verdict(),'footSurface':'uncertain'})},'seated')


def test_bounded_correction_chooses_supported_floor_over_last_bad_furniture_view():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='JPEG');corrected=[]
    def call(stage,prompt,data,limit):
        if prompt=='world_scene_placements':
            if 'slot-fix' in stage:
                corrected.append(data['camera']['viewIndex'])
                assert data['previousSupportChecks'][0]['observation']['poseSupported']
            return {'text':json.dumps({'placements':[{**proposal(),'viewIndex':data['camera']['viewIndex']}]})}
        if stage=='analysis:slot-check:4:0':return {'text':json.dumps(verdict())}
        return {'text':json.dumps({**verdict(),'confidence':.75} if stage=='analysis:slot-check:0:0' else {**verdict(),'poseSupported':False,'pelvisSupport':'none','footSurface':'furniture','confidence':.99})}
    result=inspect_placements({'exits':[]},[{'entityId':'npc','expectedPose':'seated'}],[out.getvalue()]*8,'',call,1000,
        independent=True,verify_support=True,correct_invalid=True)
    assert result['placements']==[proposal()] and corrected==[0]


def test_single_context_support_uses_one_actual_camera_and_keeps_rejection_gates():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='PNG')
    data=request(proposal(),out.getvalue(),single_camera=True)
    assert len(data['_sceneImages'])==1 and data['inspectionMode']=='proposed-body-support-context'
    assert 'entityId' not in data and 'npc' not in str({k:v for k,v in data.items() if k!='_sceneImages'})
    assert data['points'][-1]['centrePixels']==[225*1000/512,470*1000/512]
    picture=Image.open(io.BytesIO(base64.b64decode(data['_sceneImages'][0].split(',',1)[1])))
    assert picture.size==(1000,1000)
    assert valid({'text':json.dumps(verdict())},'seated')
    for changed in ({'footSurface':'furniture'},{'pelvisSupport':'none'},{'poseSupported':False},{'confidence':.84}):
        assert not valid({'text':json.dumps({**verdict(),**changed})},'seated')


def test_new_placement_context_reaches_independent_support_without_extra_retries():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='PNG');calls=[]
    def call(stage,prompt,data,limit):
        calls.append(stage)
        if prompt=='world_scene_supports':
            assert data['inspectionMode']=='proposed-body-support-context' and len(data['_sceneImages'])==1
            return {'text':json.dumps(verdict())}
        assert data['cameraContextVersion']==1
        return {'text':json.dumps({'placements':[proposal()]})}
    result=inspect_placements({'exits':[]},[{'entityId':'npc'}],[out.getvalue()]*8,'',call,1000,
        independent=True,verify_support=True,single_context=True)
    assert result['placements']==[proposal()] and calls==['analysis:placement:0','analysis:slot-check:0:0']


def test_camera_reservations_project_prior_body_and_door_without_inventing_people():
    from app.scenes.placements import reserved_regions
    found={'npc':proposal()}
    passages=[{'passageEvidence':{'viewIndex':0,'boxPixels':[300,100,400,390]}}]
    measured=reserved_regions(found,passages,0)
    assert [r['kind'] for r in measured]==['reserved-future-body','measured-passage']
    assert measured[0]['bounds']==pytest.approx([n*1000/512 for n in proposal()['bodyBoxPixels']])
    assert reserved_regions(found,[],2)==[]


def test_next_camera_receives_an_already_verified_future_body_reservation():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='PNG');seen=[]
    first={**proposal(),'entityId':'one'}
    def call(stage,prompt,data,limit):
        if prompt=='world_scene_supports':return {'text':json.dumps(verdict())}
        seen.append(data)
        return {'text':json.dumps({'placements':[first] if data['camera']['viewIndex']==0 else []})}
    result=inspect_placements({'exits':[]},[{'entityId':'one'},{'entityId':'two'}],[out.getvalue()]*8,'',call,1000,
        independent=True,verify_support=True,single_context=True,downward=True)
    assert result['placements']==[]
    downward=next(d for d in seen if d['camera']['viewIndex']==4)
    assert [p['entityId'] for p in downward['futureCast']]==['two']
    assert any(r['kind']=='reserved-future-body' for r in downward['reservationsInThisCamera']['regions'])
