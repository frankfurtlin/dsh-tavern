from copy import deepcopy
from unittest.mock import patch
import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Task
from app.scenes.models import SceneImage
from app.scenes.service import save_image
from app.scenes.scene_review import evaluate,apply,request as review_request
from app.scenes.binding import composition_from_result
from app.scenes.visual_brief import environment_text,environment_constraints
from app.scenes.worker import process_one
from .test_scenes import scene_world,request,generated,API


def observation(count=0,entities=None):
    entities=entities or [None]*count
    return {'projection':'equirectangular','spaceKind':'interior','cameraCovered':True,
            'visiblePeopleCount':count,'instances':[{'instanceId':'person-'+str(i),'entityId':ident,
                'headBox':[100+i*120,250,140+i*120,310],
                'evidence':'One actual physical person standing beside the table.'} for i,ident in enumerate(entities)],
            'referenceArtifacts':[],'finishedArtwork':True,'unpaintedGuideRegions':[],
            'confidence':.97,'evidence':'The actual ceiling encloses the observer; floor and walls extend around the full scene, with exterior light only through the windows.'}


def test_single_texture_review_never_mixes_rectilinear_cameras_or_future_cast():
    payload=review_request(b'actual-panorama',{'sceneCast':[],'futureCast':[{'entityId':'future'}]})
    assert len(payload['_sceneImages'])==1
    assert payload['expectedPhysicalPeople']==[] and 'futureCast' not in payload


@pytest.mark.parametrize('change', ['count','boolean-count','unknown-id','duplicate-instance','nan','bad-box','missing-evidence'])
def test_numeric_and_identity_contradictions_cannot_be_approved(change):
    value=observation(2,['a','b']);cast=[{'entityId':'a'},{'entityId':'b'}]
    if change=='count':value['visiblePeopleCount']=5
    elif change=='boolean-count':value['visiblePeopleCount']=True
    elif change=='unknown-id':value['instances'][0]['entityId']='invented'
    elif change=='duplicate-instance':value['instances'][1]['instanceId']=value['instances'][0]['instanceId']
    elif change=='nan':value['confidence']=float('nan')
    elif change=='bad-box':value['instances'][0]['headBox'][1]=-10
    else:value['evidence']=''
    assert evaluate(value,cast,{'spaceKind':'interior'})['verified'] is False


def test_two_physical_instances_of_one_npc_are_extra_people_even_when_id_matches():
    result=evaluate(observation(2,['npc','npc']),[{'entityId':'npc'}],{'spaceKind':'interior'})
    assert result['verified'] and 'unexpected-people' in result['issues']


def test_a_small_unidentified_face_does_not_prove_a_stranger_and_never_invents_identity():
    result=evaluate(observation(1,['null']),[{'entityId':'npc'}],{'spaceKind':'interior'})
    assert not result['issues'] and result['unidentifiedPeople']
    assert 'unexpected-people' in evaluate(observation(1,['null']),[],{'spaceKind':'interior'})['issues']


@pytest.mark.parametrize('seam',[False,True])
def test_repeated_detection_of_one_head_cannot_invent_two_people(seam):
    value=observation(2,['a','b'])
    value['instances'][0]['headBox']=[980,250,20,310] if seam else [100,250,140,310]
    value['instances'][1]['headBox']=[982,251,22,311] if seam else [101,251,141,311]
    assert not evaluate(value,[{'entityId':'a'},{'entityId':'b'}],{'spaceKind':'interior'})['verified']


def test_independent_recount_disagreement_cannot_be_hidden_by_a_positive_whole_texture_review():
    parsed=apply({'characters':[],'peopleRecount':{'visiblePeopleCount':2,'unexpectedPeople':['stranger']}},observation(),'abc')
    result=composition_from_result(parsed,[],{'sceneReviewVersion':1,'sceneImageHash':'abc','spaceKind':'interior'})
    assert 'people-count-unconfirmed' in result['issues']


def test_sky_beyond_a_window_does_not_put_the_observer_outdoors():
    value=observation();value['visibleSkyBeyondWindow']=True
    assert not evaluate(value,[],{'spaceKind':'interior'})['issues']
    value.update(spaceKind='outdoor',cameraCovered=True)
    assert 'space-mismatch' in evaluate(value,[],{'spaceKind':'interior'})['issues']


def test_actual_daylight_or_snow_cannot_be_approved_as_the_rainy_night_bookshop():
    value={**observation(),'lighting':'daylight','exteriorWeather':'snowy'}
    expected={'spaceKind':'interior','lightingInspectionVersion':1,'lighting':'夜','description':'雨夜的旧城书店'}
    assert 'lighting-mismatch' in evaluate(value,[],expected)['issues']
    value.update(lighting='night',exteriorWeather='rainy')
    assert not evaluate(value,[],expected)['issues']
    value.pop('lighting');assert not evaluate(value,[],expected)['verified']


def test_local_camera_projection_misjudgment_cannot_override_the_bound_whole_texture_observation():
    parsed=apply({'composition':{'projection':'perspective'},'characters':[]},observation(),'abc')
    expected={'spaceKind':'interior','sceneReviewVersion':1,'sceneImageHash':'abc','artworkInspectionVersion':1}
    result=composition_from_result(parsed,[],expected)
    assert result['verified'] and not result['issues']
    parsed['sceneReview']['imageHash']='other-image'
    assert composition_from_result(parsed,[],expected)['verified'] is False


def test_actual_copied_identity_reference_findings_survive_while_local_projection_verdicts_do_not():
    parsed=apply({'composition':{'confidence':.97,'projection':'perspective',
        'referenceArtifacts':['Two pasted identity-reference faces on the newly edited wall.']},'characters':[]},observation(),'abc')
    result=composition_from_result(parsed,[],{'sceneReviewVersion':1,'sceneImageHash':'abc','spaceKind':'interior'})
    assert result['projection']=='equirectangular'
    assert 'reference-artifacts' in result['issues'] and 'projection-mismatch' not in result['issues']


def test_environment_brief_removes_player_and_companions_without_modifying_story_input():
    desc={'description':'木桌靠窗，两人正在谈话，陆遥看着闻青。窗外是雪山。','observerName':'陆遥',
          'futureCast':[{'name':'闻青'}],'spaceKind':'interior'}
    before=deepcopy(desc);text=environment_text(desc)
    assert '木桌靠窗' in text and '窗外是雪山' in text
    assert '两人' not in text and '陆遥' not in text and '闻青' not in text
    assert desc==before and 'INSIDE this enclosed room' in environment_constraints(desc)
    text=environment_text({'description':'雨夜的旧城书店即将打烊，一封便笺把两名陌生人留在灯下。'})
    assert '雨夜' in text and '两名陌生人' not in text
    assert 'Snow, mountains' not in environment_constraints(desc)
    retained={**desc,'sceneCast':[{'entityId':'retained'}]}
    assert 'EXACTLY 1 existing' in environment_constraints(retained) and 'ZERO PEOPLE' not in environment_constraints(retained)


def test_unknown_future_pose_does_not_invent_two_seated_people_and_clutter_the_floor():
    from app.scenes.visual_brief import staging_constraints
    descriptor={'futureCast':[{'entityId':'one'},{'entityId':'two'}]};original=deepcopy(descriptor)
    brief=staging_constraints(descriptor)
    assert 'No seated pose is required' in brief and 'Provide 2 clearly visible empty seats' not in brief
    assert 'DIFFERENT horizontal viewing' in brief and descriptor==original
    seated=staging_constraints({'futureCast':[{'entityId':'one','expectedPose':'seated'},{'entityId':'two'}]})
    assert 'Provide 1 clearly visible empty seats' in seated and 'No seated pose is required' not in seated


def prepared_preview(client,w):
    parent=request(client,w)
    with SessionLocal() as db:
        row=db.get(SceneImage,parent['id']);row.descriptor={**row.descriptor,'renderPipeline':1,'sceneReviewVersion':1}
        db.commit()
    assert process_one()
    data,metadata,_=generated()
    with SessionLocal() as db:
        root=db.get(SceneImage,parent['id']);child=db.get(SceneImage,root.image['pipelineStageId'])
        child.status='ready';child.descriptor={**child.descriptor,'sceneReviewVersion':1,'spaceKind':'interior'}
        child.image=save_image(child,data,metadata,'received-fixture')
        child.image={**child.image,'anchorStatus':'queued','sceneReview':{
            'version':1,'imageHash':child.image['sha256'],'observation':observation()}}
        ident=child.id;db.commit()
    return parent['id'],ident


def test_preview_is_read_only_authenticated_and_has_no_gameplay_image_or_mainline_write(client,scene_world):
    w=scene_world;parent,child=prepared_preview(client,w)
    status=client.get(API+'/scenes/'+parent+'?lineId='+w['line']).json()
    assert status['status']=='queued' and status['imageUrl'] is None and status['preview']['readOnly'] is True
    preview=client.get(status['preview']['url']);assert preview.status_code==200 and preview.content
    assert client.get(API+'/scenes/'+child).status_code==404
    assert client.get(API+'/scenes/'+child+'/image').status_code==404
    assert client.get(API+'/scenes/'+parent+'/image').status_code==409
    headers=client.headers.copy();client.headers.pop('authorization',None)
    assert client.get(status['preview']['url']).status_code==401
    client.headers.update(headers)
    with SessionLocal() as db:
        from app.world.models import WorldLine
        assert db.get(WorldLine,w['line']).head_checkpoint_id==w['cp']


@pytest.mark.parametrize('change',['hash','bad-scene','archived-line','other-checkpoint','private-child'])
def test_preview_rejects_unreviewed_or_changed_scope(client,scene_world,change):
    w=scene_world;parent,child=prepared_preview(client,w)
    status=client.get(API+'/scenes/'+parent).json();url=status['preview']['url']
    with SessionLocal() as db:
        picture=db.get(SceneImage,child)
        if change=='hash':picture.image={**picture.image,'sceneReview':{**picture.image['sceneReview'],'imageHash':'wrong'}}
        elif change=='bad-scene':
            proof=deepcopy(picture.image['sceneReview']);proof['observation']['spaceKind']='outdoor';picture.image={**picture.image,'sceneReview':proof}
        elif change=='archived-line':
            from app.world.models import WorldLine
            db.get(WorldLine,w['line']).status='archived'
        elif change=='other-checkpoint':
            from app.models import uid
            from app.world.models import WorldLine
            db.get(WorldLine,w['line']).head_checkpoint_id=uid()
        else:url=url.replace(parent,child)
        db.commit()
    assert client.get(url).status_code in (404,409)


def test_in_flight_preview_read_survives_final_publication_without_exposing_private_child(client,scene_world):
    w=scene_world;parent,child=prepared_preview(client,w)
    status=client.get(API+'/scenes/'+parent).json();url=status['preview']['url']
    with SessionLocal() as db:
        db.get(SceneImage,parent).status='ready';db.commit()
    assert client.get(url).status_code==200
    assert client.get(API+'/scenes/'+child+'/image').status_code==404


def test_new_parent_cannot_preview_an_old_weaker_people_receipt(client,scene_world):
    w=scene_world;parent,child=prepared_preview(client,w)
    with SessionLocal() as db:
        root=db.get(SceneImage,parent);root.descriptor={**root.descriptor,'peopleInventoryVersion':1};db.commit()
    assert client.get(API+'/scenes/'+parent).json().get('preview') is None
    with SessionLocal() as db:
        row=db.get(SceneImage,child);row.descriptor={**row.descriptor,'peopleInventoryVersion':1}
        row.image={**row.image,'peopleInventory':{'version':1,'imageHash':row.image['sha256'],
            'views':[{'viewIndex':i,'observation':{'count':0,'heads':[],'confidence':.95,
                'evidence':'This actual physical camera contains no person or physical head.'}} for i in range(4)]}}
        db.commit()
    assert client.get(API+'/scenes/'+parent).json()['preview']['readOnly'] is True
    with SessionLocal() as db:
        row=db.get(SceneImage,child);row.image={**row.image,'peopleInventory':{**row.image['peopleInventory'],'imageHash':'wrong'}};db.commit()
    assert client.get(API+'/scenes/'+parent).json().get('preview') is None


@pytest.mark.parametrize('defect',[None,'fourth-camera','old-facts','missing-fourth-camera'])
def test_physical_fact_preview_requires_the_current_contract_and_all_four_cameras(client,scene_world,defect):
    from .test_scene_public_handover import facts
    w=scene_world;parent,child=prepared_preview(client,w);value=facts();fact_id=value['requirements'][0]['id']
    with SessionLocal() as db:
        root=db.get(SceneImage,parent);row=db.get(SceneImage,child);digest=row.image['sha256']
        root.descriptor={**root.descriptor,'visualFacts':value,'peopleInventoryVersion':1}
        row.descriptor={**row.descriptor,'visualFacts':value,'peopleInventoryVersion':1}
        positive={'id':fact_id,'consistent':True,'confidence':.97,'evidence':'No physical gemstone appears anywhere in these actual pixels.'}
        observed=observation();observed['visualFactChecks']=[deepcopy(positive)]
        views=[{'viewIndex':i,'observation':{'count':0,'heads':[],'confidence':.97,
            'evidence':'No actual physical person is depicted in this complete camera.',
            'visualFactChecks':[deepcopy(positive)]}} for i in range(4)]
        if defect=='fourth-camera':views[3]['observation']['visualFactChecks'][0]['consistent']=False
        elif defect=='missing-fourth-camera':views[3]['observation'].pop('visualFactChecks')
        elif defect=='old-facts':row.descriptor={k:v for k,v in row.descriptor.items() if k!='visualFacts'}
        row.image={**row.image,'sceneReview':{'version':1,'imageHash':digest,'observation':observed},
            'peopleInventory':{'version':1,'imageHash':digest,'views':views}}
        db.commit()
    before=client.get(API+w['root']).json()
    status=client.get(API+'/scenes/'+parent).json()
    assert bool(status.get('preview')) is (defect is None)
    assert (digest in status.get('invalidPreviewHashes',[])) is (defect is not None)
    if defect:
        assert client.get(API+'/scenes/'+parent+'/preview/'+digest).status_code in (404,409)
    assert client.get(API+w['root']).json()==before


@pytest.mark.parametrize('defect',['whole-panorama','later-coordinate-conflict'])
def test_latest_contradiction_revokes_older_preview_aliases_of_the_same_pixels(client,scene_world,defect):
    from app.scenes.pipeline import stage
    w=scene_world;parent,child=prepared_preview(client,w)
    old_url=client.get(API+'/scenes/'+parent).json()['preview']['url']
    with SessionLocal() as db:
        root=db.get(SceneImage,parent);old=db.get(SceneImage,child)
        replacement=stage(db,root,{**deepcopy(old.descriptor),'inputSceneId':old.id},received=old,inspect_received=False)
        old_proof=deepcopy(old.image['sceneReview'])
        if defect=='whole-panorama':old_proof['observation']['spaceKind']='outdoor'
        replacement.image={**replacement.image,'sceneReview':old_proof,'anchorStatus':'queued'}
        if defect=='later-coordinate-conflict':replacement.image={**replacement.image,'composition':{'verified':True,'issues':['reference-artifacts']}}
        digest=replacement.image['sha256'];db.commit()
    result=client.get(API+'/scenes/'+parent).json()
    assert result.get('preview') is None and digest in result['invalidPreviewHashes']
    assert client.get(old_url).status_code in (404,409)


def test_pipeline_cannot_publish_a_child_reviewed_against_older_physical_facts(client,scene_world):
    from app.scenes.pipeline import advance
    from app.world.models import World,WorldLine
    from .test_scene_public_handover import facts
    w=scene_world;parent,child=prepared_preview(client,w)
    before=client.get(API+w['root']).json()
    with SessionLocal() as db:
        root=db.get(SceneImage,parent);row=db.get(SceneImage,child)
        row.descriptor={**row.descriptor,'visualFacts':facts()}
        row.image={**row.image,'anchorStatus':'complete'}
        advance(db,root,db.get(World,w['world']),db.get(WorldLine,w['line']))
        assert root.status=='failed' and root.error_code=='SCENE_FACTS_RECHECK_REQUIRED'
        assert root.image.get('anchorStatus')!='complete'
        db.commit()
    assert client.get(API+w['root']).json()==before


@pytest.mark.parametrize('actual_people',[0,1])
def test_recovery_replaces_stale_empty_room_verdict_before_coordinate_work(client,scene_world,profile,actual_people):
    import json
    from app.models import ModelProfile
    from app.scenes.binding import queue_binding,bind_scene
    from app.errors import Problem
    w=scene_world;parent,child=prepared_preview(client,w)
    targets={'characters':[],'fixtures':[],'exits':[]}
    with patch('app.scenes.binding.inspection_targets',return_value=targets):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child)
            row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'peopleInventoryVersion':1,'sceneReviewVersion':1}
            row.image={**row.image,'composition':{'verified':False,'issues':['scene-review-unconfirmed']}}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();tid=row.image['bindingTaskId']
        calls=[]
        def received(model,prompt,data,limit):
            mode=data.get('inspectionMode');calls.append(mode)
            if mode=='single-panorama-composition-v1':value=observation()
            elif mode=='single-panorama-count-conflict-v1':value=observation(4)
            elif mode=='single-camera-physical-head-inventory':
                value={'count':actual_people,'heads':([{'box':[300,250,380,350],
                    'evidence':'An actual physical adult head beside the empty chair.'}] if actual_people else []),
                    'confidence':.95,'evidence':'The complete actual camera has been examined for physical bodies.'}
            else:
                status=client.get(API+'/scenes/'+parent+'?lineId='+w['line']).json()
                assert actual_people==0 and status['preview']['readOnly'] is True
                assert status['status']=='queued' and status['imageUrl'] is None
                assert status.get('image',{}).get('anchorStatus')!='complete'
                raise Problem('PROVIDER_CONNECTION_FAILED','Coordinate work deliberately stopped before sending',502)
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):
            if actual_people:
                bind_scene(tid)
            else:
                with pytest.raises(Problem):bind_scene(tid)
    assert calls[:5]==['single-panorama-composition-v1']+['single-camera-physical-head-inventory']*4
    status=client.get(API+'/scenes/'+parent+'?lineId='+w['line']).json()
    assert bool(status.get('preview')) is (actual_people==0)
    with SessionLocal() as db:
        from app.world.models import WorldLine
        assert db.get(WorldLine,w['line']).head_checkpoint_id==w['cp']



def received_floor_problem():
    return {'sha256':'a'*64,'bindingImageHash':'a'*64,'missingExitIds':['street'],'pointCorrectionNotes':[
        {'id':'street','viewIndex':0,'nearPixels':[256,350],'farPixels':[256,256],
         'throughPointPixels':[256,220],'actualPointObservations':{
            'confidence':.96,'unobstructed':False,'sameOpening':True,'openingConfirmed':True,'crossedThreshold':True,'B':{
            'surface':'furniture','confidence':.96,
            'evidence':'A thick beige doormat occupies the actual threshold walking segment.'}}}]}


def test_received_obstacle_feedback_uses_exact_spherical_location_without_changing_the_receipt():
    from app.scenes.visual_brief import ground_feedback
    source=received_floor_problem();before=deepcopy(source)
    actual=ground_feedback(source,['street'])
    assert actual[0]['routeId']=='street' and actual[0]['sourceHash']=='a'*64
    assert actual[0]['points'][0]['panoramaPosition']==[.5,.5]
    assert actual[0]['points'][0]['reportedCovering'] is True and source==before


@pytest.mark.parametrize('invalid',['other-image','other-route','boolean-view','boolean-coordinate','nan-coordinate',
    'outside-coordinate','walkable-surface','nan-confidence'])
def test_correction_feedback_cannot_invent_a_mat_or_transfer_another_image_failure(invalid):
    from app.scenes.visual_brief import ground_feedback
    value=received_floor_problem();note=value['pointCorrectionNotes'][0]
    if invalid=='other-image':value['bindingImageHash']='b'*64
    elif invalid=='other-route':note['id']='other'
    elif invalid=='boolean-view':note['viewIndex']=True
    elif invalid=='boolean-coordinate':note['farPixels'][0]=True
    elif invalid=='nan-coordinate':note['farPixels'][0]=float('nan')
    elif invalid=='outside-coordinate':note['farPixels'][0]=513
    elif invalid=='walkable-surface':note['actualPointObservations']['B']['surface']='walkable-ground'
    else:note['actualPointObservations']['B']['confidence']=float('nan')
    assert ground_feedback(value,['street'])==[]


def test_repaint_prompt_receives_concrete_obstacle_and_keeps_people_room_and_story_items():
    from app.scenes.visual_brief import ground_feedback
    from app.scenes.provider import prompt_for
    value={'renderStage':'environment','environmentGeometryVersion':11,'visualBriefVersion':1,
        'environmentInputMode':'actual-image','placeName':'起居室','description':'炉火和敞开的前门。',
        'spaceKind':'interior','lighting':'清晨','sceneCast':[{'name':'福尔摩斯','appearance':'40岁英国男性'}],
        'exitRoutes':[{'id':'street','visualCue':'Open front doorway to the London street.'}],
        'futureCast':[{'entityId':'remaining','name':'来访者','expectedPose':'standing'}],
        'correctionReasons':{'groundChecks':ground_feedback(received_floor_problem(),['street']),
            'detectorIssues':[{'kind':'placements','reason':'placement-evidence-unconfirmed'}]}}
    prompt=prompt_for(value)
    assert 'thick beige doormat' in prompt and 'REMOVE the nonessential decorative doormat' in prompt
    assert 'Preserve EXACTLY these 1 existing physical people' in prompt and 'Preserve every named story fixture' in prompt
    assert 'Opposite facades stand ACROSS that roadway' in prompt and 'Keep the observer and existing people INSIDE' in prompt
    assert 'Rearrange only nonessential loose furnishings' in prompt and 'Do not paint the remaining people yet' in prompt
    value['correctionReasons']={}
    assert 'REMOVE the nonessential decorative doormat' not in prompt_for(value)


@pytest.mark.parametrize('invalid',['none','uncertain-overall','uncertain-covering','unconfirmed-opening',
    'another-opening','not-crossed','unobstructed','unrelated-furniture','old-image','already-confirmed-route'])
def test_known_obstacle_repair_requires_a_strong_same_opening_observation(invalid):
    from app.scenes.visual_brief import obstructed_threshold
    value=received_floor_problem();observed=value['pointCorrectionNotes'][0]['actualPointObservations']
    if invalid=='uncertain-overall':observed['confidence']=.84
    elif invalid=='uncertain-covering':observed['B']['confidence']=.84
    elif invalid=='unconfirmed-opening':observed['openingConfirmed']=False
    elif invalid=='another-opening':observed['sameOpening']=False
    elif invalid=='not-crossed':observed['crossedThreshold']=False
    elif invalid=='unobstructed':observed['unobstructed']=True
    elif invalid=='unrelated-furniture':observed['B']['evidence']='An ambiguous cabinet surface outside a hypothesized passage.'
    elif invalid=='old-image':value['bindingImageHash']='b'*64
    elif invalid=='already-confirmed-route':value['missingExitIds']=[]
    assert obstructed_threshold(value) is (invalid=='none')



def test_malformed_missing_route_list_cannot_be_used_as_repaint_instruction():
    from app.scenes.visual_brief import ground_feedback
    assert ground_feedback(received_floor_problem(),False)==[]
    assert ground_feedback(received_floor_problem(),'street')==[]
