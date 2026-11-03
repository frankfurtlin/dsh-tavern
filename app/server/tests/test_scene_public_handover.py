"""Real public-ledger shape and retained pixels across stronger review rules."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import json
import pytest
from sqlalchemy import select,func
from app.db import SessionLocal
from app.models import Task
from app.world.models import WorldCheckpoint
from app.scenes.facts import visual_facts
from app.scenes.people_inventory import request as inventory_request,evaluate_facts
from app.scenes.models import SceneImage
from app.scenes.service import scene_view,image_bytes
from app.scenes.worker import process_one
from .test_scenes import scene_world,generated,API
from .test_scene_independent_review import failed_review
from .test_scene_heads import camera
from .test_scene_people_inventory import proof


def public_ledger():
    return {'entities':[{'id':'watson','name':'华生','type':'character','nearby':True},
        {'id':'inspector','name':'接收警官','type':'character','nearby':True}],
        'plot':{'confirmedHoldings':[{'itemName':'蓝宝石','holderName':'莫卡伯爵夫人',
            'description':'最近确认交接至莫卡伯爵夫人，后续未知变化不作推断。'}]}}


def facts():return visual_facts(None,public_ledger(),[],'')


def test_actual_ending_ledger_shape_keeps_handed_gem_absent_after_it_leaves_discovered_entities():
    public=public_ledger();before=deepcopy(public)
    checkpoint=SimpleNamespace(state={'privateInventory':'PRIVATE-NEVER-INSPECT'},definition={'privateItemId':'PRIVATE-ID'})
    value=visual_facts(checkpoint,public,[],'')
    assert value['requirements'][0]['name']=='蓝宝石' and value['requirements'][0]['kind']=='absent-transferred-item'
    assert value['cameraInspectionVersion']==1 and public==before
    assert 'PRIVATE' not in json.dumps(value)


@pytest.mark.parametrize('positive',['visible-item','present-holder','new-confirmed-holder'])
def test_current_public_positive_evidence_supersedes_a_historical_handover(positive):
    public=public_ledger();items=[]
    if positive=='visible-item':items=[{'id':'gem','name':'蓝宝石','type':'item'}]
    elif positive=='present-holder':public['entities'].append({'id':'countess','name':'莫卡伯爵夫人','nearby':True})
    else:public['plot']['confirmedHoldings']=[{'itemName':'蓝宝石','holderName':'华生'}]
    assert visual_facts(None,public,items,'') is None


def test_handover_constraints_are_deduplicated_and_never_invented_without_a_public_record():
    public=public_ledger();public['plot']['confirmedHoldings']*=2
    assert len(visual_facts(None,public,[],'')['requirements'])==1
    public['plot'].clear();assert visual_facts(None,public,[],'') is None


@pytest.mark.parametrize('defect',[None,'contradiction','missing','stale'])
def test_handed_item_uses_four_existing_blind_views_without_giving_them_an_expected_cast(defect):
    value=facts();payload=inventory_request(camera(),facts=value)
    text=json.dumps({k:v for k,v in payload.items() if k!='_sceneImages'},ensure_ascii=False)
    assert '蓝宝石' in text and '莫卡伯爵夫人' not in text and '华生' not in text
    assert len(payload['_sceneImages'])==1 and 'expectedPhysicalPeople' not in payload
    inventory=proof();ident=value['requirements'][0]['id']
    for view in inventory['views']:
        view['observation']['visualFactChecks']=[{'id':ident,'consistent':True,'confidence':.97,
            'evidence':'No physical gemstone is visible in this actual camera.'}]
    if defect=='contradiction':inventory['views'][3]['observation']['visualFactChecks'][0].update(consistent=False,evidence='An actual faceted gemstone is held by a visible physical hand.')
    elif defect=='missing':inventory['views'][3]['observation'].pop('visualFactChecks')
    elif defect=='stale':inventory['imageHash']='b'*64
    expected=[] if defect is None else ['visual-fact-mismatch'] if defect=='contradiction' else ['visual-facts-unconfirmed']
    assert evaluate_facts(inventory,value,'a'*64)==expected


@pytest.mark.parametrize('change',[None,'player','point','routes','checkpoint','weaken-facts'])
def test_explicit_fresh_fact_inspection_reuses_pixels_only_with_the_same_scene_and_preserves_original_task(client,scene_world,profile,change):
    w=scene_world;scene,old_id,_=failed_review(client,w)
    with SessionLocal() as db:
        row=db.get(SceneImage,scene['id']);cp=db.get(WorldCheckpoint,w['cp'])
        current=scene_view(cp,w['line']);upgraded=deepcopy(current);upgraded['descriptor']['visualFacts']=facts()
        if change=='weaken-facts':
            previous=visual_facts(None,{'entities':[]},[],'她剖开鹅嗉。')
            row.descriptor={**row.descriptor,'visualFacts':previous,'appearance':{**row.descriptor['appearance'],'visualFacts':previous}}
            db.commit()
        before_task=deepcopy({c.name:getattr(db.get(Task,old_id),c.name) for c in Task.__table__.columns})
        before_pixels=image_bytes(row);before_descriptor=deepcopy(row.descriptor)
    if change=='player':upgraded['playerId']='another-player'
    elif change=='point':upgraded['descriptor']['pointId']='another-point'
    elif change=='routes':upgraded['exits']=[*upgraded['exits'],{'id':'another-exit'}]
    elif change=='checkpoint':upgraded['checkpointId']='another-checkpoint'
    before=client.get(API+w['root']).json()
    with patch('app.scenes.service.scene_view',return_value=upgraded),patch('app.scenes.worker.generate') as supplier:
        response=client.post(API+w['root']+'/scene/inspect',headers={'Idempotency-Key':'upgrade-fact-contract'},
            json={'sceneId':scene['id'],'expectedCheckpointId':w['cp'],'independent':True})
        supplier.assert_not_called()
    assert response.status_code==(202 if change is None else 409),response.text
    with SessionLocal() as db:
        row=db.get(SceneImage,scene['id'])
        assert image_bytes(row)==before_pixels
        assert {c.name:getattr(db.get(Task,old_id),c.name) for c in Task.__table__.columns}==before_task
        if change is None:
            assert row.descriptor['visualFacts']==facts() and row.image['anchorStatus']=='queued'
            assert row.descriptor['inspectionFactHistory'][-1]['previousFacts']==before_descriptor.get('visualFacts')
            new=db.get(Task,response.json()['inspectionTaskId'])
            assert new.id!=old_id and not new.checkpoint.get('calls') and 'inspectionBaseReceipt' not in new.checkpoint
            frozen=new.checkpoint['executionPrompts']['world_scene_people_inventory']['text']
            assert 'physicalFacts' in frozen and 'visualFactChecks' in frozen
        else:assert row.descriptor==before_descriptor
    assert client.get(API+w['root']).json()==before


def test_frozen_camera_prompts_support_the_request_protocol_without_conflicting_legacy_pixel_limits():
    from app.scenes.recovery import inspection_prompts
    prompts=inspection_prompts()
    people=prompts['world_scene_people']['text'];placement=prompts['world_scene_placements']['text'];routes=prompts['world_scene_passages']['text']
    assert 'never 0..1000' not in people and 'ONE 512x512 image' not in people
    assert '512 by 512 camera' not in placement and 'declared camera.width/camera.height' in placement
    assert 'Coordinates and boxes must all stay within 0..512' not in routes
    assert all('normalized 0..1000' in text for text in (people,placement,routes))


@pytest.mark.parametrize('storage',['source','receipt'])
def test_stopped_received_pixels_recover_under_stronger_facts_without_repeating_the_image_post(client,scene_world,storage):
    from app.world.models import WorldCheckpoint
    from app.scenes.characters import save_source
    from app.scenes.service import save_image
    from .test_scene_pipeline import start
    w=scene_world;parent_id,child_id=start(client,w);pixels,metadata,request_id=generated()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);child=db.get(SceneImage,child_id)
        if storage=='source':save_source(child,pixels);child.image={'sourceReceived':True,'sourceRequestId':request_id}
        else:child.image=save_image(child,pixels,metadata,request_id)
        child.status='failed';child.error_code='SCENE_CONTEXT_CHANGED';parent.status='failed';parent.error_code='SCENE_STOPPED'
        current=scene_view(db.get(WorldCheckpoint,w['cp']),w['line']);upgraded=deepcopy(current);upgraded['descriptor']['visualFacts']=facts()
        db.commit()
    before=client.get(API+w['root']).json()
    with patch('app.scenes.service.scene_view',return_value=upgraded):
        response=client.post(API+w['root']+'/scene/inspect',headers={'Idempotency-Key':'stronger-facts-'+storage},
            json={'sceneId':parent_id,'expectedCheckpointId':w['cp'],'independent':True})
        assert response.status_code==202,response.text
        assert response.json()['imageRegenerated'] is False and response.json()['receiptRecovered']
        with patch('app.scenes.worker.generate') as supplier:assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:
        child=db.get(SceneImage,child_id)
        assert child.status=='ready'
        if storage=='source':
            from app.scenes.characters import source_bytes
            assert source_bytes(child)==pixels
        else:assert image_bytes(child)==pixels
        assert child.descriptor['visualFacts']==facts()
    assert client.get(API+w['root']).json()==before


@pytest.mark.parametrize('state',['unknown','failed'])
def test_opening_a_stricter_fact_contract_keeps_the_original_reservation_until_explicit_inspection(client,scene_world,profile,state):
    from .test_scene_failure_recovery import stopped
    w=scene_world;parent_id,child_id,task_id,_,pixels=stopped(client,w,{'reason':'timeout','stage':'analysis:original'})
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);parent.status=state
        if state=='failed':parent.error_code='SCENE_STOPPED'
        view=scene_view(db.get(WorldCheckpoint,w['cp']),w['line']);view['descriptor']['visualFacts']=facts()
        before_task=deepcopy({c.name:getattr(db.get(Task,task_id),c.name) for c in Task.__table__.columns})
        before_counts=(db.scalar(select(func.count()).select_from(SceneImage)),db.scalar(select(func.count()).select_from(Task)))
        db.commit()
    before=client.get(API+w['root']).json()
    with patch('app.scenes.service.scene_view',return_value=view),patch('app.scenes.worker.generate') as supplier:
        for key in ('first-open-after-fact-upgrade','second-tab-after-fact-upgrade'):
            result=client.post(API+w['root']+'/scene',headers={'Idempotency-Key':key},json={'expectedCheckpointId':w['cp'],'regenerate':False})
            assert result.status_code==202,result.text
            assert result.json()['id']==parent_id and result.json()['recovery']['canInspect']
        supplier.assert_not_called()
    with SessionLocal() as db:
        assert (db.scalar(select(func.count()).select_from(SceneImage)),db.scalar(select(func.count()).select_from(Task)))==before_counts
        assert {c.name:getattr(db.get(Task,task_id),c.name) for c in Task.__table__.columns}==before_task
        assert image_bytes(db.get(SceneImage,child_id))==pixels
        assert not db.get(SceneImage,parent_id).descriptor.get('inspectionFactHistory')
    assert client.get(API+w['root']).json()==before


@pytest.mark.parametrize('complete',[False,True])
def test_selected_old_pixels_receive_current_facts_even_when_parent_was_already_upgraded(client,scene_world,profile,complete):
    from .test_scene_failure_recovery import stopped
    w=scene_world;parent_id,child_id,task_id,_,pixels=stopped(client,w,{'reason':'timeout','stage':'analysis:original'})
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);child=db.get(SceneImage,child_id)
        view=scene_view(db.get(WorldCheckpoint,w['cp']),w['line']);view['descriptor']['visualFacts']=facts()
        parent.descriptor={**parent.descriptor,'visualFacts':facts(),
            'appearance':{**parent.descriptor['appearance'],'visualFacts':facts()}}
        if complete:
            parent.status='failed';parent.error_code='SCENE_STOPPED'
            child.image={**child.image,'anchorStatus':'complete'}
            task=db.get(Task,task_id);task.state='succeeded';task.provider_state='completed'
        original_descriptor=deepcopy(child.descriptor)
        original_task=deepcopy({c.name:getattr(db.get(Task,task_id),c.name) for c in Task.__table__.columns})
        db.commit()
    before=client.get(API+w['root']).json()
    body={'sceneId':parent_id,'expectedCheckpointId':w['cp'],'independent':True}
    if not complete:body['sourceSceneId']=parent_id
    with patch('app.scenes.service.scene_view',return_value=view),patch('app.scenes.worker.generate') as supplier:
        result=client.post(API+w['root']+'/scene/inspect',headers={'Idempotency-Key':'parent-already-upgraded'},json=body)
        assert result.status_code==202,result.text
        assert result.json().get('inspectionTaskId') and not result.json().get('receiptRecovered')
        supplier.assert_not_called()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);selected=db.get(SceneImage,parent.image['pipelineStageId'])
        assert selected.descriptor['visualFacts']==facts()
        assert selected.descriptor['appearance']['visualFacts']==facts()
        assert image_bytes(selected)==pixels and selected.image['anchorStatus']=='queued'
        assert 'sceneReview' not in selected.image and 'peopleInventory' not in selected.image
        assert {c.name:getattr(db.get(Task,task_id),c.name) for c in Task.__table__.columns}==original_task
        if not complete:assert db.get(SceneImage,child_id).descriptor==original_descriptor and selected.id!=child_id
    assert client.get(API+w['root']).json()==before
