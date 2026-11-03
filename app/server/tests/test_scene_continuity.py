from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest
from app.scenes.continuity import received_parts,merge
from app.scenes.binding import inspection_context,inspection_image,inspection_cameras
from app.scenes.scene_review import apply
from app.scenes.heads import inspect_heads
from .test_scene_bodies import fixture
from .test_scene_progressive import observation
from .test_scene_surfaces import candidate,observation as floor_observation
from app.scenes.surfaces import apply_surface


def setup():
    parsed,cast,frames,body=fixture();parsed['characters'][0].update(body)
    parsed['characters'][0]['scaleEvidence']['viewIndex']=0
    parsed=apply(parsed,observation(1,['person']),'a'*64)
    parsed['fixtures']=[];parsed['exits']=[apply_surface(candidate(),floor_observation(),'a'*64)]
    targets={'characters':[],'fixtures':[],'exits':[{'id':'home','independentReviewRequired':True,'requiredSurfaceReview':True}]}
    scope={'owner_id':'owner','world_id':'world','line_id':'line','checkpoint_id':'cp','profile_id':'image-profile','profile_version':1}
    descriptor={'renderStage':'environment','pipelineParentId':'parent','sceneReviewVersion':1,'headInspectionVersion':2,
        'bodyInspectionVersion':1,'spaceKind':'interior','sceneCast':cast,'futureCast':[],'expectedPoses':{'person':'standing'}}
    source=SimpleNamespace(id='source',status='ready',descriptor=descriptor,
        image={'sha256':'a'*64,'bindingImageHash':'a'*64,'anchorStatus':'complete','bindingTaskId':'task'},**scope)
    target=SimpleNamespace(id='target',status='ready',descriptor={**deepcopy(descriptor),'renderStage':'actor','inputSceneId':'source',
        'sceneCast':[*deepcopy(cast),{'entityId':'new'}]},image={'sha256':'c'*64},**scope)
    raw,size=inspection_image(frames[0]);old=inspection_cameras(source,raw);new=list(old);new[7]=b'different-camera'
    saved={'sceneId':'source','imageHash':'a'*64,'imageSize':list(size),
        'context':inspection_context(source,targets),'result':parsed}
    task=SimpleNamespace(id='task',owner_id='owner',state='succeeded',provider_state='completed',cancel_requested=False,
        payload={'sceneId':'source','worldId':'world','lineId':'line'},checkpoint={'verifiedImageInspection':saved})
    cp=SimpleNamespace(id='cp',owner_id='owner',world_id='world')
    records={'source':source,'task':task,'cp':cp};db=SimpleNamespace(get=lambda cls,ident:records.get(ident))
    return db,source,target,targets,new,frames[0],task


def carry(values):
    db,source,target,targets,cameras,pixels,task=values
    with patch('app.scenes.service.image_bytes',return_value=pixels),patch('app.scenes.binding.placement_narrative',return_value='Public scene'):
        return received_parts(db,target,targets,cameras)


@pytest.mark.parametrize('changed',[None,'camera','native','unknown','target'])
def test_fixture_contact_reuses_only_completed_identical_camera_evidence(changed):
    values=setup();db,source,target,targets,cameras,pixels,task=values
    source.descriptor['inspectionPerformanceVersion']=1;target.descriptor['inspectionPerformanceVersion']=1
    targets['fixtures']=[{'id':'table','name':'桌边','fixture':'table'}]
    saved=task.checkpoint['verifiedImageInspection']
    saved['result']['fixtures']=[{'id':'table','visible':True,'confidence':.96,'viewIndex':2,'anchorPixels':[250,400],
        'groundEvidence':{'surface':'walkable-ground','evidence':'Visible walking floor immediately beside the actual table base.'},
        'fixtureReview':{'version':1,'source':'single-camera-floor-contact','viewIndex':2}}]
    saved['context']=inspection_context(source,targets)
    if changed=='camera':cameras[2]=b'changed-camera'
    elif changed=='unknown':task.provider_state='unknown'
    elif changed=='target':targets['fixtures'][0]['id']='different-table'
    original=deepcopy(saved['result']['fixtures'])
    def native(raw,index):return b'changed-detail' if changed=='native' and raw!=pixels and index==2 else b'same-detail'
    if changed=='native':
        with patch('app.scenes.service.image_bytes',side_effect=lambda row:pixels if row.id=='source' else b'new-texture'),\
                patch('app.scenes.inspection.detail_camera',side_effect=native),patch('app.scenes.binding.placement_narrative',return_value='Public scene'):
            result=received_parts(db,target,targets,cameras)
    else:result=carry(values)
    if changed:assert result.get('fixtures',[])==[]
    else:
        assert result['fixtures'][0]['id']=='table'
        assert result['fixtures'][0]['groundEvidence']==original[0]['groundEvidence']
        proof=result['fixtures'][0]['fixtureReview']['cameraContinuity']
        assert proof['sourceImageHash']=='a'*64 and proof['targetImageHash']=='c'*64
        assert merge({'fixtures':[]},result)['fixtures']==result['fixtures']
    assert saved['result']['fixtures']==original


def test_complete_unchanged_cameras_reuse_real_valid_geometry_without_copying_global_counts():
    values=setup();result=carry(values)
    assert [p['entityId'] for p in result['characters']]==['person'] and [p['id'] for p in result['exits']]==['home']
    head=result['characters'][0]['headReview'];assert head['imageHash']=='c'*64
    assert head['cameraContinuity']['sourceImageHash']=='a'*64
    assert head['cameraContinuity']['originalMeasurement']['headReview']['imageHash']=='a'*64
    surface=result['exits'][0]['passageEvidence']['surfaceReview'];assert surface['imageHash']=='c'*64
    parsed={'characters':[{'entityId':'person','visible':False},{'entityId':'new','visible':False}],
        'exits':[],'sceneReview':{'observation':{'visiblePeopleCount':2}}}
    updated=merge(parsed,result)
    assert updated['sceneReview']==parsed['sceneReview'] and len(updated['characters'])==2
    assert 'cameraContinuity' not in values[-1].checkpoint['verifiedImageInspection']['result']['characters'][0]['headReview']


@pytest.mark.parametrize('changed',['owner','world','line','cp','profile','version','parent','hash','unknown',
    'cancelled','context','old-rule','pose','appearance','invalid-source-geometry'])
def test_changed_context_or_unresolved_receipt_cannot_authorize_pixel_continuity(changed):
    values=setup();db,source,target,targets,cameras,pixels,task=values
    fields={'owner':'owner_id','world':'world_id','line':'line_id','cp':'checkpoint_id','profile':'profile_id','version':'profile_version'}
    if changed in fields:setattr(target,fields[changed],'other')
    elif changed=='parent':target.descriptor['pipelineParentId']='other'
    elif changed=='hash':source.image['bindingImageHash']='other'
    elif changed=='unknown':task.provider_state='unknown'
    elif changed=='cancelled':task.cancel_requested=True
    elif changed=='context':task.checkpoint['verifiedImageInspection']['context']='other'
    elif changed=='old-rule':target.descriptor['headInspectionVersion']=1
    elif changed=='pose':target.descriptor['expectedPoses']['person']='seated'
    elif changed=='appearance':target.descriptor['sceneCast'][0]['appearance']='A different identity.'
    elif changed=='invalid-source-geometry':task.checkpoint['verifiedImageInspection']['result']['characters'][0]['grounding']='floating'
    received=carry(values)
    assert received['characters']==[]
    if changed not in ('pose','appearance','old-rule'):assert received['exits']==[]
    elif changed=='old-rule':assert [p['id'] for p in received['exits']]==['home']


@pytest.mark.parametrize('view,characters,exits',[(0,0,1),(1,1,0),(7,1,1)])
def test_even_one_changed_relevant_camera_requires_new_measurement(view,characters,exits):
    values=setup();values[4][view]=b'changed-even-one-pixel'
    result=carry(values)
    assert len(result['characters'])==characters and len(result['exits'])==exits


def test_supplier_continuity_metadata_alone_cannot_skip_independent_head_inspection():
    values=setup();received=carry(values);part=received['characters'][0];frames=fixture()[2]
    allowed=Mock(side_effect=AssertionError('Already proved identical camera'))
    assert inspect_heads({'characters':[part]},values[2].descriptor['sceneCast'],frames,'c'*64,allowed,1000,
        retained_ids={'person'})['characters'][0]['headReview']['imageHash']=='c'*64
    denied=Mock(return_value={'text':'{"visible":false}'})
    inspect_heads({'characters':[part]},values[2].descriptor['sceneCast'],frames,'c'*64,denied,1000)
    assert denied.call_count==2


@pytest.mark.parametrize('changed_view,characters,exits',[(0,0,1),(1,1,0)])
def test_same_512_camera_cannot_hide_changed_original_resolution_detail(changed_view,characters,exits):
    values=setup();source_pixels=values[5]
    def pixels(row):return source_pixels if row.id=='source' else b'changed-native-texture'
    def detail(raw,index):return b'changed-detail' if raw!=source_pixels and index==changed_view else b'same-detail'
    with patch('app.scenes.service.image_bytes',side_effect=pixels),patch('app.scenes.inspection.detail_camera',side_effect=detail),\
            patch('app.scenes.binding.placement_narrative',return_value='Public scene'):
        received=received_parts(values[0],values[2],values[3],values[4])
    assert len(received['characters'])==characters and len(received['exits'])==exits
