import io
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from PIL import Image
from app.scenes.room_repairs import plans,protected_people,apply_plan
from app.scenes.perspective import camera_reference,reproject_camera,route_repair_boxes
from app.scenes.inspection import sphere_pixel
from .test_scene_progressive import observation
from .test_scenes import scene_world


def frame():
    digest='a'*64
    desc={'renderPipeline':1,'renderStage':'environment','sceneReviewVersion':1,'peopleInventoryVersion':1,
        'spaceKind':'interior','sceneCast':[{'entityId':'holmes','name':'Holmes'}],
        'futureCast':[{'entityId':'peterson','expectedPose':'standing'}],
        'expectedPoses':{'holmes':'seated'},'exitRoutes':[{'id':'street_london','name':'伦敦街道','yaw':3.075}]}
    inventory={'version':1,'imageHash':digest,'views':[{'viewIndex':i,'observation':{
        'count':1 if i==2 else 0,'heads':[{'box':[400,400,470,490],
            'evidence':'One actual separate physical head and body.'}] if i==2 else [],
        'confidence':.95,'evidence':'Actual full camera examined for physical people.'}} for i in range(4)]}
    image={'sha256':digest,'bindingImageHash':digest,'sceneReview':{'version':1,'imageHash':digest,
        'observation':observation(1,['holmes'])},'peopleInventory':inventory,'missingExitIds':[],
        'missingCharacterIds':[],'placementPlan':[],'placementEvidenceExhausted':True,
        'characterAnchors':{'holmes':{'verified':True,'headReview':{'imageHash':digest},
            'bodyEvidence':{'naturalScale':True,'pose':'seated','scaleConfidence':.95,'groundConfidence':.95,
                'bodyBox':[1925,360,2025,730],'imageSize':[2048,1024]}}},
        'spatialAnchors':{'exits:street_london':{'kind':'exits','verified':True,'yaw':1.9}}}
    return desc,image


def test_empty_camera_edit_clears_space_without_repainting_existing_person_or_door():
    desc,image=frame();before=deepcopy((desc,image));actual=plans(desc,image)
    assert [p['kind'] for p in actual]==['space'] and actual[0]['viewIndex'] in (0,3)
    assert actual[0]['protectedPanoramaBoxes'][0][0]<1925/2048
    assert (desc,image)==before
    source=SimpleNamespace(id='received',image=image)
    updated=apply_plan(desc,actual[0],[],source)
    assert updated['inputSceneId']=='received' and updated['floorRepairPlan']['sourceHash']==image['sha256']
    assert updated['sceneCast']==desc['sceneCast'] and updated['clearedSpaceCameras']==[actual[0]['viewIndex']]


@pytest.mark.parametrize('invalid',['hash','extra-person','inventory','body','pose','scale','old-head'])
def test_unverified_people_never_authorize_room_edits(invalid):
    desc,image=frame()
    if invalid=='hash':image['bindingImageHash']='b'*64
    elif invalid=='extra-person':image['sceneReview']['observation']=observation(2,['holmes',None])
    elif invalid=='inventory':image['peopleInventory']['imageHash']='b'*64
    elif invalid=='body':image['characterAnchors']['holmes']['bodyEvidence']=None
    elif invalid=='pose':image['characterAnchors']['holmes']['bodyEvidence']['pose']='standing'
    elif invalid=='scale':image['characterAnchors']['holmes']['bodyEvidence']['scaleConfidence']=.84
    else:image['characterAnchors']['holmes']['headReview']['imageHash']='b'*64
    assert protected_people(desc,image) is None and plans(desc,image)==[]


def test_observed_threshold_and_empty_space_are_edited_cumulatively_from_new_received_bytes():
    desc,image=frame();image['missingExitIds']=['street_london']
    image['pointCorrectionNotes']=[{'id':'street_london','viewIndex':5,'nearPixels':[290,430],
        'farPixels':[290,380],'throughPointPixels':[290,330],'apertureBoxPixels':[220,20,360,410],
        'actualPointObservations':{'openingConfirmed':True,'crossedThreshold':True,'sameOpening':True,
            'unobstructed':False,'confidence':.96,'C':{'surface':'furniture','confidence':.96,
                'evidence':'A raised beige doormat blocks the marked walking path.'}}}]
    first,second=plans(desc,image)
    assert first['kind']=='threshold' and first['viewIndex']==5 and second['kind']=='space'
    one=apply_plan(desc,first,[second],SimpleNamespace(id='original',image=image))
    next_image={**image,'sha256':'b'*64};two=apply_plan(one,second,[],SimpleNamespace(id='edited-first',image=next_image))
    assert two['inputSceneId']=='edited-first' and two['floorRepairPlan']['sourceHash']=='b'*64
    assert two['floorRepairPlan']['protectedPanoramaBoxes']==first['protectedPanoramaBoxes']
    assert two['remainingRoomEdits']==[]


@pytest.mark.parametrize('index',[0,5])
def test_masked_camera_edit_preserves_actual_people_and_every_pixel_outside_region(index):
    source=Image.new('RGB',(1024,512),'#b9a783');data=io.BytesIO();source.save(data,format='PNG')
    edit=io.BytesIO();Image.new('RGB',(512,512),'#1746b4').save(edit,format='PNG')
    u,v=sphere_pixel(index,256,256,1024,512)
    protected=[[u/1024-.025,v/512-.025,u/1024+.025,v/512+.025]]
    result,metadata=reproject_camera(data.getvalue(),edit.getvalue(),index,repair_boxes=[[170,100,350,440]],
        allow_ground=True,protected_panorama_boxes=protected)
    actual=Image.open(io.BytesIO(result))
    assert actual.getpixel((int(u),int(v)))==source.getpixel((int(u),int(v)))
    assert actual.getpixel((2,2))==source.getpixel((2,2))
    px,py=sphere_pixel(index,205,330,1024,512)
    assert actual.getpixel((int(px),int(py)))!=source.getpixel((int(px),int(py)))
    assert metadata['cameraEdit']['backgroundPreserved'] is True
    assert Image.open(io.BytesIO(camera_reference(data.getvalue(),index,allow_ground=True))).size==(512,512)


def test_floor_mask_is_a_bounded_painting_region_not_an_unchecked_full_sphere():
    desc,image=frame();plan=plans(desc,image)[0];updated=apply_plan(desc,plan,[],SimpleNamespace(id='source',image=image))
    assert route_repair_boxes(updated,plan['viewIndex'])==plan['boxes']
    updated['floorRepairPlan']['boxes']=[[0,0,512,512]]
    with pytest.raises(ValueError):route_repair_boxes(updated,plan['viewIndex'])


def test_saved_environment_prefers_verified_person_and_door_over_bad_latest_painting():
    from app.scenes.retained import environment
    desc,image=frame();good=SimpleNamespace(descriptor=desc,image=image)
    bad=SimpleNamespace(descriptor=deepcopy(desc),image=deepcopy(image))
    bad.image['sceneReview']['observation']=observation(2,['holmes',None])
    old=SimpleNamespace(descriptor={'renderStage':'environment','sceneCast':[]},image={})
    parent=SimpleNamespace(id='parent',owner_id='owner',world_id='world',line_id='line',checkpoint_id='cp',
        profile_id='profile',profile_version=1,descriptor={'renderPipeline':1,'sceneCast':desc['sceneCast']})
    db=SimpleNamespace(scalars=lambda query:[old,good,bad])
    with patch('app.scenes.pipeline.is_parent',return_value=True):assert environment(db,parent) is good


@pytest.mark.parametrize('route',[{'id':'street_london','name':'伦敦街道','yaw':3.075},
    {'id':'uuid','name':'London street','yaw':3.075},{'id':'uuid','visualCue':'Open doorway to the street.','yaw':3.075}])
def test_actual_route_metadata_triggers_street_cue_and_keeps_observed_opening_in_place(route):
    from app.scenes.provider import prompt_for
    value={'renderStage':'environment','environmentGeometryVersion':11,'environmentInputMode':'actual-image',
        'placeName':'起居室','description':'炉火与敞开的门。','spaceKind':'interior','lighting':'清晨',
        'sceneCast':[{'name':'Holmes','appearance':'adult'}],'exitRoutes':[route],
        'correctionReasons':{'missingExits':[route['id']]}}
    text=prompt_for(value)
    assert 'street roadway visibly' in text and 'Existing observed openings define their positions' in text
    assert '"longitudeFraction"' not in text and '"yaw"' not in text
    value['environmentInputMode']='new-artwork'
    assert '"longitudeFraction"' in prompt_for(value)


@pytest.mark.parametrize('outcome',['empty','success','unknown'])
def test_placement_exhaustion_is_recorded_by_the_measurer_only_after_received_full_search(outcome):
    import json
    from app.scenes.placements import inspect_placements
    from app.errors import Problem
    from .test_scene_supports import proposal,verdict
    picture=io.BytesIO();Image.new('RGB',(512,512),'brown').save(picture,format='PNG');events=[];calls=[]
    def call(stage,prompt,data,limit):
        calls.append(stage)
        if outcome=='unknown':raise Problem('PROVIDER_STATUS_UNKNOWN','Unknown')
        if prompt=='world_scene_supports':return {'text':json.dumps(verdict())}
        return {'text':json.dumps({'placements':[proposal()] if outcome=='success' else []})}
    args=({'placements':[],'placementEvidenceExhausted':True},[{'entityId':'npc'}],[picture.getvalue()]*4,'',call,1000)
    if outcome=='unknown':
        with pytest.raises(Problem):inspect_placements(*args,independent=True,verify_support=True,on_exhausted=lambda:events.append('exhausted'))
    else:inspect_placements(*args,independent=True,verify_support=True,on_exhausted=lambda:events.append('exhausted'))
    assert events==(['exhausted'] if outcome=='empty' else [])
    if outcome=='empty':assert len(calls)==4


@pytest.mark.parametrize('recovery',[False,True])
def test_paid_local_camera_is_journaled_and_chained_without_repeating_generation(client,scene_world,recovery):
    from app.db import SessionLocal
    from app.scenes.models import SceneImage
    from app.scenes.pipeline import stage
    from app.scenes.worker import process_one
    from app.scenes.service import save_image,image_bytes
    from app.world.models import WorldLine
    from .test_scene_pipeline import start
    from .test_scenes import generated
    parent_id,base_id=start(client,scene_world)
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);base=db.get(SceneImage,base_id)
        data,metadata,request_id=generated();base.image=save_image(base,data,metadata,request_id);base.status='ready'
        plan={'version':1,'kind':'space','viewIndex':0,'boxes':[[8,8,504,504]],'protectedPanoramaBoxes':[]}
        second={**plan,'viewIndex':1}
        edit=stage(db,parent,apply_plan(base.descriptor,plan,[second],base));edit_id=edit.id;db.commit()
    picture=io.BytesIO();Image.new('RGB',(512,512),'#1746b4').save(picture,format='PNG')
    reply=(picture.getvalue(),{'projection':'perspective-edit','width':512,'height':512,'mime':'image/png'},'paid-local-edit')
    from app.scenes.perspective import reproject_camera as project
    with patch('app.scenes.worker.generate',return_value=reply) as supplier,patch('app.scenes.binding.queue_binding') as inspect:
        if recovery:
            with patch('app.scenes.perspective.reproject_camera',side_effect=RuntimeError('Local projection interrupted')):assert process_one()
            with SessionLocal() as db:assert db.get(SceneImage,edit_id).status=='saving'
            assert process_one()
        else:assert process_one()
        assert supplier.call_count==1;inspect.assert_not_called()
        with SessionLocal() as db:
            saved=db.get(SceneImage,edit_id);first_hash=saved.image['sha256'];first_pixels=image_bytes(saved)
            assert saved.status=='ready' and saved.image['anchorStatus']=='planned-edit'
        assert process_one()  # Queue next local edit against the received first edit.
        with SessionLocal() as db:
            parent=db.get(SceneImage,parent_id);next_edit=db.get(SceneImage,parent.image['pipelineStageId'])
            assert next_edit.descriptor['inputSceneId']==edit_id
            assert next_edit.descriptor['floorRepairPlan']['sourceHash']==first_hash
        with patch('app.scenes.perspective.reproject_camera',wraps=project) as projection:assert process_one()
        assert projection.call_args.args[0]==first_pixels and supplier.call_count==2
        assert inspect.call_count==1  # Only the final cumulative panorama is independently inspected.
        with SessionLocal() as db:
            assert db.get(WorldLine,scene_world['line']).head_checkpoint_id==scene_world['cp']


def test_stale_local_camera_hash_stops_before_paid_dispatch(client,scene_world):
    from app.db import SessionLocal
    from app.scenes.models import SceneImage
    from app.scenes.pipeline import stage
    from app.scenes.worker import process_one
    from app.scenes.service import save_image
    from .test_scene_pipeline import start
    from .test_scenes import generated
    parent_id,base_id=start(client,scene_world)
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);base=db.get(SceneImage,base_id)
        data,metadata,request_id=generated();base.image=save_image(base,data,metadata,request_id);base.status='ready'
        plan={'version':1,'kind':'space','viewIndex':0,'boxes':[[8,8,504,504]],'protectedPanoramaBoxes':[]}
        descriptor=apply_plan(base.descriptor,plan,[],base);descriptor['floorRepairPlan']['sourceHash']='stale'
        edit=stage(db,parent,descriptor);ident=edit.id;db.commit()
    with patch('app.scenes.worker.generate') as supplier:
        assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        assert db.get(SceneImage,ident).error_code=='SCENE_REFERENCE_UNAVAILABLE'
