import io, json
from copy import deepcopy
from unittest.mock import Mock
import pytest
from PIL import Image
from app.errors import Problem
from app.scenes.bodies import inspect_bodies
from app.scenes.binding import anchors_from_result
from app.scenes.inspection import panorama_coordinates


def fixture():
    image=io.BytesIO(); Image.new('RGB',(512,512),'tan').save(image,format='JPEG')
    proof={'version':2,'imageHash':'a'*64,'cropHash':'b'*64,'viewIndex':0,
           'containsPhysicalHead':True,'fullHeadVisible':True,'identityMatched':True,
           'tightHeadBounds':True,'confidence':.95,'headBoxPixels':[220,200,244,240],
           'cropBoxPixels':[204,184,260,256],'cropImageSize':[56,72],'cropHeadBoxPixels':[16,16,40,56]}
    part={'entityId':'person','visible':True,'confidence':.95,'viewIndex':0,
          'headBoxPixels':proof['headBoxPixels'],'headReview':proof,
          'bodyBoxPixels':[210,190,260,410],'groundPointPixels':[10,10]}
    observation={'physicalPerson':True,'naturalScale':True,'pose':'standing',
                 'scaleConfidence':.95,'bodyBoxPixels':[210,190,260,410],
                 'scaleEvidence':{'referenceKind':'furniture','boxPixels':[320,280,430,410],
                                  'depthRelation':'same-depth','description':'Actual counter beside person.'},
                 'grounding':'floor','groundPointPixels':[232,410],'groundConfidence':.95}
    return {'characters':[part]},[{'entityId':'person','name':'唐禾'}],[image.getvalue()]*8,observation


def anchors(value,cast):
    return anchors_from_result(panorama_coordinates(value,(2048,1024)),cast,(2048,1024),require_grounding=True)


def test_conflicting_contact_is_remeasured_in_one_actual_image_without_moving_head():
    parsed,cast,cameras,observed=fixture(); requests=[]
    def call(stage,prompt,data,limit):
        requests.append(data); return {'text':json.dumps(observed)}
    value=inspect_bodies(parsed,cast,cameras,'a'*64,(2048,1024),call,4096)
    assert anchors(value,cast) and len(requests)==1
    assert len(requests[0]['_sceneImages'])==1
    assert value['characters'][0]['headReview']==parsed['characters'][0]['headReview']
    # Once valid, polling/recovery needs no new body request.
    again=inspect_bodies(value,cast,cameras,'a'*64,(2048,1024),Mock(side_effect=AssertionError('duplicate')),4096)
    assert again==value


def test_erased_feet_remain_a_real_visual_failure_not_a_manufactured_floor_contact():
    parsed,cast,cameras,observed=fixture(); observed['grounding']='floating'
    call=Mock(return_value={'text':json.dumps(observed)})
    value=inspect_bodies(parsed,cast,cameras,'a'*64,(2048,1024),call,4096)
    assert not anchors(value,cast) and call.call_count==1


def test_unknown_body_request_is_not_retried_and_wrong_image_head_cannot_authorize_it():
    parsed,cast,cameras,observed=fixture(); call=Mock(side_effect=Problem('PROVIDER_STATUS_UNKNOWN','unknown'))
    with pytest.raises(Problem): inspect_bodies(parsed,cast,cameras,'a'*64,(2048,1024),call,4096)
    assert call.call_count==1
    call.reset_mock()
    assert inspect_bodies(parsed,cast,cameras,'c'*64,(2048,1024),call,4096)==parsed
    assert call.call_count==0


def test_actor_crop_retains_complete_received_head_instead_of_clipping_to_old_slot():
    from app.scenes.perspective import camera_reference,reproject_camera,actor_crop
    from app.scenes.inspection import sphere_pixel
    source=io.BytesIO();Image.new('RGB',(1024,512),'black').save(source,format='PNG')
    placement={'bodyBoxPixels':[220,290,295,460],'groundPointPixels':[258,458],'pose':'seated','maskVersion':3}
    placement['editCropPixels']=actor_crop(placement)
    edited=io.BytesIO();Image.new('RGB',(512,512),'white').save(edited,format='PNG')
    reference=camera_reference(source.getvalue(),0,placement)
    assert Image.open(io.BytesIO(reference)).size==(512,512)
    output,_=reproject_camera(source.getvalue(),edited.getvalue(),0,placement)
    picture=Image.open(io.BytesIO(output));crop=placement['editCropPixels']
    # A face above the old top=290 survives within the crop, while the other
    # side of the sphere keeps the original environment pixels.
    x,y=sphere_pixel(0,258,crop[1]+25,1024,512)
    assert picture.getpixel((int(x)%1024,int(y)))[0]>200
    assert picture.getpixel((0,256))==(0,0,0)


def test_existing_actor_protection_uses_its_inspected_image_dimensions():
    from app.scenes.pipeline import protected_actor_boxes
    anchors={'first':{'bodyEvidence':{'bodyBox':[1024,256,1536,768],'imageSize':[2048,1024]}}}
    # The received panorama can be 3136 wide; its size must never shift the
    # protected first actor over the next actor's actual neck and torso.
    assert protected_actor_boxes(anchors,[3136,1568])==[[.5,.25,.75,.75]]
    anchors['first']['bodyEvidence'].pop('imageSize')
    assert protected_actor_boxes(anchors,[2048,1024])==[[.5,.25,.75,.75]]
    assert protected_actor_boxes(anchors) == []
    assert protected_actor_boxes(anchors,[0,1024]) == []


def test_cropped_actor_reference_has_no_unpainted_mannequin_pixels():
    from app.scenes.perspective import camera_reference,actor_crop
    source=io.BytesIO();Image.new('RGB',(1024,512),(31,46,59)).save(source,format='PNG')
    placement={'bodyBoxPixels':[220,190,290,420],'groundPointPixels':[258,418],
               'pose':'standing','maskVersion':3}
    placement['editCropPixels']=actor_crop(placement)
    picture=Image.open(io.BytesIO(camera_reference(source.getvalue(),0,placement))).convert('RGB')
    assert len(set(picture.getdata()))==1


def test_explicit_new_cropped_guide_is_private_and_large_enough_to_replace():
    from app.scenes.perspective import camera_reference,actor_crop
    source=io.BytesIO();Image.new('RGB',(1024,512),(31,46,59)).save(source,format='PNG')
    placement={'bodyBoxPixels':[210,290,290,470],'groundPointPixels':[250,470],
               'pose':'seated','maskVersion':3,'croppedGuideVersion':1}
    placement['editCropPixels']=actor_crop(placement)
    reference=Image.open(io.BytesIO(camera_reference(source.getvalue(),0,placement))).convert('RGB')
    assert reference.size==(512,512) and len(set(reference.getdata()))>20
    # Input geometry only; the original sphere remains untouched.
    assert len(set(Image.open(io.BytesIO(source.getvalue())).getdata()))==1


def test_new_composition_cameras_do_not_paint_view_labels_over_actual_scene_pixels():
    from types import SimpleNamespace
    from app.scenes.binding import inspection_cameras
    source=io.BytesIO();Image.new('RGB',(1024,512),(31,46,59)).save(source,format='PNG')
    frames=inspection_cameras(SimpleNamespace(descriptor={'cameraPixelsVersion':1}),source.getvalue())
    assert len(frames)==8
    for frame in frames:
        picture=Image.open(io.BytesIO(frame)).convert('RGB')
        assert picture.getpixel((10,10))==picture.getpixel((300,300))
