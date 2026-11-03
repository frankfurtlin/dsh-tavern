import io, json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image
import pytest
from app.errors import Problem
from app.scenes.heads import inspect_heads,proof_valid,crop_request
from app.scenes.grounding import binding_matches,VERSION
from app.scenes.composition import character_presentations
from app.scenes.recovery import retained_base
from app.domain import digest


def camera():
    output=io.BytesIO();Image.new('RGB',(512,512),'brown').save(output,format='JPEG')
    return output.getvalue()


def inputs():
    return {'characters':[{'entityId':'h','viewIndex':0,'visible':True,'headBoxPixels':[334,157,377,214]}]},[{'entityId':'h','name':'福尔摩斯'}],[camera()], 'a'*64


def result(value):return {'text':json.dumps(value),'finishReason':'stop'}

def confirmed_crop():
    return {'containsPhysicalHead':True,'fullHeadVisible':True,'identityMatched':True,'tightHeadBounds':True,
            'headBoxPixels':[58,58,212,259],'confidence':.95}


def test_corrected_camera_measurement_requires_its_real_crop_confirmation():
    parsed,cast,cameras,hash=inputs();requests=[]
    def call(stage,prompt,data,limit):
        requests.append(data)
        if data['inspectionMode']=='person-head':
            assert 'headBoxPixels' not in data['person']
            return result({'visible':True,'headBoxPixels':[334,224,377,280],'confidence':.95})
        assert data['inspectionMode']=='head-crop' and len(data['_sceneImages'])==1
        return result(confirmed_crop())
    corrected=inspect_heads(parsed,cast,cameras,hash,call,4096)['characters'][0]
    assert corrected['headBoxPixels']==pytest.approx([334.1465,224.13125,377.0183,280.034375],abs=.001)
    assert proof_valid(corrected['headReview'],hash) and len(requests)==2
    assert parsed['characters'][0]['headBoxPixels']==[334,157,377,214]


def test_wrong_bulk_camera_searches_actual_views_and_still_requires_the_real_head_crop():
    parsed,cast,cameras,image_hash=inputs();cameras=cameras*8;views=[];stages=[]
    parsed['characters'][0].update(bodyBoxPixels=[300,180,420,480],groundPointPixels=[350,475])
    def call(stage,prompt,data,limit):
        stages.append(stage);assert len(stage)<=30
        if data['inspectionMode']=='person-head':
            views.append(data['camera']['viewIndex'])
            return result({'visible':True,'headBoxPixels':[334,224,377,280],'confidence':.95} if views[-1]==3 else {'visible':False,'confidence':.05})
        return result(confirmed_crop())
    corrected=inspect_heads(parsed,cast,cameras,image_hash,call,4096,search_cameras=True)['characters'][0]
    assert views==[0,1,2,3] and len(stages)==5
    assert corrected['viewIndex']==3 and corrected['bodyViewIndex']==corrected['groundViewIndex']==0
    assert proof_valid(corrected['headReview'],image_hash)
    from app.scenes.binding import inspection_stages
    assert len(inspection_stages(SimpleNamespace(descriptor={'sceneCast':cast,'headInspectionVersion':2,'headCameraSearchVersion':1}),{'exits':[]}))==8


def test_absent_head_search_is_bounded_and_unknown_camera_is_never_replayed():
    parsed,cast,cameras,image_hash=inputs();cameras=cameras*8
    absent=Mock(return_value=result({'visible':False,'confidence':.05}))
    corrected=inspect_heads(parsed,cast,cameras,image_hash,absent,4096,search_cameras=True)['characters'][0]
    assert absent.call_count==4 and 'headReview' not in corrected
    unknown=Mock(side_effect=[result({'visible':False,'confidence':.05}),Problem('PROVIDER_STATUS_UNKNOWN','unknown')])
    with pytest.raises(Problem):inspect_heads(parsed,cast,cameras,image_hash,unknown,4096,search_cameras=True)
    assert unknown.call_count==2


def test_body_camera_orders_search_without_promoting_an_unchecked_head():
    parsed,cast,cameras,image_hash=inputs();cameras=cameras*8;views=[]
    parsed['characters'][0].update(bodyViewIndex=7,groundViewIndex=7)
    def call(stage,prompt,data,limit):
        if data['inspectionMode']=='person-head':
            views.append(data['camera']['viewIndex']);return result({'visible':True,'headBoxPixels':[334,224,377,280],'confidence':.95})
        return result(confirmed_crop())
    corrected=inspect_heads(parsed,cast,cameras,image_hash,call,4096,search_cameras=True)['characters'][0]
    assert views==[3] and corrected['viewIndex']==3 and corrected['bodyViewIndex']==7
    assert proof_valid(corrected['headReview'],image_hash)


def test_wrong_identity_in_one_camera_searches_another_instead_of_repainting_the_world():
    parsed,cast,cameras,image_hash=inputs();cameras=cameras*8;views=[];crops=0
    def call(stage,prompt,data,limit):
        nonlocal crops
        if data['inspectionMode']=='person-head':
            views.append(data['camera']['viewIndex']);return result({'visible':True,'headBoxPixels':[334,224,377,280],'confidence':.95})
        crops+=1;return result({**confirmed_crop(),'identityMatched':crops>1})
    corrected=inspect_heads(parsed,cast,cameras,image_hash,call,4096,search_cameras=True)['characters'][0]
    assert views==[0,1] and crops==2 and corrected['viewIndex']==1
    assert proof_valid(corrected['headReview'],image_hash) and 'headIdentityMismatch' not in corrected


@pytest.mark.parametrize('failure',['containsPhysicalHead','fullHeadVisible','identityMatched'])
def test_background_partial_face_or_wrong_identity_cannot_publish_a_head(failure):
    parsed,cast,cameras,hash=inputs()
    def call(stage,prompt,data,limit):
        if data['inspectionMode']=='person-head':return result({'visible':True,'headBoxPixels':[334,224,377,280],'confidence':.95})
        checked=confirmed_crop()
        checked[failure]=False;return result(checked)
    corrected=inspect_heads(parsed,cast,cameras,hash,call,4096)['characters'][0]
    assert 'headReview' not in corrected


def test_unknown_head_localization_is_never_replayed():
    parsed,cast,cameras,hash=inputs();call=Mock(side_effect=Problem('PROVIDER_STATUS_UNKNOWN','unknown'))
    with pytest.raises(Problem):inspect_heads(parsed,cast,cameras,hash,call,4096)
    assert call.call_count==1


def test_downward_body_view_does_not_force_head_measurement_into_a_clipped_crop():
    parsed,cast,cameras,image_hash=inputs();cameras=cameras*8
    part=parsed['characters'][0];part.update(viewIndex=4,bodyBoxPixels=[300,40,400,480],groundPointPixels=[350,475])
    body=deepcopy(part['bodyBoxPixels']);ground=deepcopy(part['groundPointPixels']);views=[]
    def call(stage,prompt,data,limit):
        if data['inspectionMode']=='person-head':
            views.append(data['camera']['viewIndex'])
            return result({'visible':True,'headBoxPixels':[334,224,377,280],'confidence':.95})
        return result(confirmed_crop())
    corrected=inspect_heads(parsed,cast,cameras,image_hash,call,16384)['characters'][0]
    assert views==[0] and corrected['viewIndex']==0
    assert corrected['bodyViewIndex']==corrected['groundViewIndex']==4
    assert corrected['bodyBoxPixels']==body and corrected['groundPointPixels']==ground
    assert proof_valid(corrected['headReview'],image_hash)


def test_low_confidence_matching_crop_gets_wider_pixels_without_relaxing_final_proof():
    parsed,cast,cameras,image_hash=inputs();sizes=[];stages=[]
    def call(stage,prompt,data,limit):
        stages.append(stage)
        if data['inspectionMode']=='person-head':return result({'visible':True,'headBoxPixels':[334,224,377,280],'confidence':.95})
        sizes.append(data['imageSize']);checked=confirmed_crop()
        if len(sizes)==1:checked['confidence']=.82
        return result(checked)
    corrected=inspect_heads(parsed,cast,cameras,image_hash,call,16384)['characters'][0]
    assert len(stages)==3 and len(sizes)==2
    assert proof_valid(corrected['headReview'],image_hash) and corrected['headReview']['confidence']==.95
    assert corrected['headReview']['cropBoxPixels']==[302,192,410,313]


def test_head_contract_hides_old_or_other_image_coordinates():
    parsed,cast,cameras,hash=inputs();_,crop_hash,_=crop_request(cast[0],cameras[0],[334,224,377,280])
    proof={'version':1,'imageHash':hash,'cropHash':crop_hash,'viewIndex':0,'headBoxPixels':[334,224,377,280],
        'containsPhysicalHead':True,'fullHeadVisible':True,'identityMatched':True,'confidence':.95}
    row=SimpleNamespace(status='ready',descriptor={'headInspectionVersion':1,'sceneCast':cast},
        image={'sha256':hash,'bindingImageHash':hash,'anchorVersion':VERSION,'anchorStatus':'queued','characterAnchors':{}})
    view={'characters':[{'id':'h'}]}
    assert binding_matches(row)
    assert character_presentations(row,view)['h']['mode']=='offscreen'
    row.image['characterAnchors']={'h':{'headReview':proof,'verified':True,'source':'image-inspection','yaw':0,'pitch':0}}
    assert binding_matches(row)
    assert character_presentations(row,view)['h']['mode']=='embedded'
    row.image['sha256']=row.image['bindingImageHash']='b'*64
    assert character_presentations(row,view)['h']['mode']=='offscreen'


def test_a_later_unknown_preserves_completed_person_progress_and_uses_compact_stage_ids():
    parsed,cast,cameras,image_hash=inputs()
    other='f'*32
    cast.append({'entityId':other,'name':'彼得森'})
    parsed['characters'].append({'entityId':other,'viewIndex':0,'visible':True})
    snapshots=[];stages=[]
    def call(stage,prompt,data,limit):
        stages.append(stage)
        assert len(stage)<=30
        if data['person']['entityId']==other:
            raise Problem('PROVIDER_STATUS_UNKNOWN','unknown')
        if data['inspectionMode']=='person-head':
            return result({'visible':True,'headBoxPixels':[334,224,377,280],'confidence':.95})
        return result(confirmed_crop())
    with pytest.raises(Problem):
        inspect_heads(parsed,cast,cameras,image_hash,call,4096,on_progress=snapshots.append)
    assert len(stages)==3 and len(snapshots)==1
    assert proof_valid(snapshots[0]['characters'][0]['headReview'],image_hash)
    assert 'headReview' not in snapshots[0]['characters'][1]


@pytest.mark.parametrize('change',[None,'owner','image','context','usage','hash'])
def test_new_inspection_can_only_read_completed_base_receipt_in_the_same_scope(tmp_path,monkeypatch,change):
    original=SimpleNamespace(id='old',owner_id='owner',task_type='world_scene_binding',
        payload={'sceneId':'scene','worldId':'world','lineId':'line'},result={},cancel_requested=False,
        checkpoint={'bindingInspection':{'sceneId':'scene','imageHash':'a'*64,'context':'context'},
            'executionProfile':{'version':4},'executionPrompts':{'world_scene_binding':{'sha256':'fixed'}},
            'calls':{'analysis:scene-binding':{'callId':'completed-call'}}})
    task=SimpleNamespace(id='new',owner_id='owner',checkpoint={
        'inspectionBaseReceipt':{'taskId':'old','callId':'completed-call'},
        'executionProfile':{'version':4},'executionPrompts':{'world_scene_binding':{'sha256':'fixed'}}})
    row=SimpleNamespace(id='scene',world_id='world',line_id='line',image={'sha256':'a'*64})
    usage=SimpleNamespace(owner_id='owner',task_id='old',status='completed',operation='analysis:scene-binding')
    result_value={'text':'{"characters":[]}','finishReason':'stop'}
    saved={'taskId':'old','callId':'completed-call','result':result_value,
        'hash':digest(json.dumps(result_value,ensure_ascii=False,sort_keys=True))}
    if change=='owner':original.owner_id='other'
    if change=='image':row.image['sha256']='b'*64
    if change=='context':original.checkpoint['bindingInspection']['context']='other'
    if change=='usage':usage.status='intent'
    if change=='hash':saved['hash']='tampered'
    path=tmp_path/'journal.json';path.write_text(json.dumps(saved),'utf8')
    monkeypatch.setattr('app.scenes.recovery.private_path',lambda *_:path)
    db=SimpleNamespace(get=lambda *_:original,scalar=lambda *_:usage)
    if change:
        with pytest.raises(Problem):retained_base(db,task,row,'context')
    else:assert retained_base(db,task,row,'context')==result_value
