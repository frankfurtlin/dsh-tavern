"""Actual failure classes: unresolved identity, false heads, redundant reads."""
from copy import deepcopy
from types import SimpleNamespace as N
from unittest.mock import Mock,patch
import base64,io,json
import pytest
import httpx
from PIL import Image
from app.domain import digest
from app.scenes.binding import evaluate_inspection,inspection_scene,inspection_context
from app.scenes.people_inventory import evaluate,request as inventory_request,candidate_boxes
from app.scenes.inventory_receipts import unchanged_views
from app.scenes.grounding import VERSION
from .test_scene_people_inventory import proof,head
from .test_scene_progressive import observation
from .test_scenes import scene_world


@pytest.mark.parametrize('current',[False,True])
def test_counted_person_with_unresolved_identity_needs_measurement_not_a_new_image(current):
    scene={'spaceKind':'interior',**({'measurementRecoveryVersion':1} if current else {})}
    parsed={'composition':{'projection':'equirectangular','confidence':.95,'spaceKind':'interior','openSky':False,
        'visiblePeopleCount':1,'unexpectedPeople':[],'referenceArtifacts':[]},
        'characters':[{'entityId':'npc','visible':False}],'fixtures':[],'exits':[]}
    before=deepcopy(parsed)
    result=evaluate_inspection(parsed,[{'entityId':'npc'}],{'fixtures':[],'exits':[]},(2048,1024),scene)
    assert result['anchorStatus']==('unavailable' if current else 'incomplete')
    assert result['missingCharacterIds']==['npc'] and result['characterAnchors']=={}
    assert bool(result['detectionIssues'])==current and parsed==before


@pytest.mark.parametrize('count',[0,2])
def test_missing_or_extra_physical_people_are_still_visual_failures(count):
    parsed={'sceneReview':{'version':1,'imageHash':'a'*64,'observation':observation(count)},
        'characters':[],'fixtures':[],'exits':[]}
    result=evaluate_inspection(parsed,[{'entityId':'npc'}],{'fixtures':[],'exits':[]},(2048,1024),
        {'spaceKind':'interior','measurementRecoveryVersion':1,'sceneReviewVersion':1,'sceneImageHash':'a'*64})
    assert result['anchorStatus']=='incomplete' and result['composition']['issues']
    assert not result['characterAnchors']


def conflict_detail(value,*,count=1,confidence=.95):
    entry=value['views'][3]
    entry['clarification']={'source':'same-camera-person-detail-v1','reason':'independent-scene-count-conflict-v1',
        'viewIndex':3,'candidateRegions':candidate_boxes(entry['observation']),
        'observation':{'count':count,'heads':[head([725,496,776,565])] if count==1 else entry['observation']['heads'],
            'confidence':confidence,'evidence':'Enlarged actual geometry distinguishes the hearth shadow from a physical head.'}}
    return value


@pytest.mark.parametrize('change',[None,'uncertain','camera','regions','source','hash','still-two','missing-view'])
def test_confident_but_conflicting_count_needs_bound_fresh_camera_detail(change):
    original=proof([head([725,496,776,565]),head([491,660,604,755])]);before=deepcopy(original)
    assert evaluate(original,'a'*64)=={'verified':True,'count':2}
    checked=conflict_detail(original)
    if change=='uncertain':checked['views'][3]['clarification']['observation']['confidence']=.84
    elif change=='camera':checked['views'][3]['clarification']['viewIndex']=2
    elif change=='regions':checked['views'][3]['clarification']['candidateRegions']=[]
    elif change=='source':checked['views'][3]['clarification']['source']='unbound-crop'
    elif change=='hash':checked['imageHash']='b'*64
    elif change=='still-two':conflict_detail(checked,count=2)
    elif change=='missing-view':checked['views'].pop(0)
    actual=evaluate(checked,'a'*64)
    if change=='still-two':assert actual=={'verified':True,'count':2}
    elif change:assert actual['verified'] is False
    else:assert actual=={'verified':True,'count':1}
    assert checked['views'][-1]['observation']==before['views'][-1]['observation']


def pixel_image():
    output=io.BytesIO();Image.new('RGB',(512,512),'#7d6c58').save(output,format='PNG');return output.getvalue()


def test_conflict_detail_contains_actual_context_and_no_requested_answer():
    value=inventory_request(pixel_image(),clarify=proof([head([725,496,776,565]),head([491,660,604,755])])['views'][3]['observation'])
    assert len(value['_sceneImages'])==3
    assert not {'cast','expectedCount','expectedIds','previousCount'}&set(value)
    assert 'No previous count, verdict or expected cast is provided' in value['instruction']
    for uri in value['_sceneImages']:
        picture=Image.open(io.BytesIO(base64.b64decode(uri.split(',',1)[1])))
        assert picture.size==(1000,1000)


@pytest.mark.parametrize('change',[None,'explicit','owner','world','line','checkpoint','parent','image-profile',
    'vision-profile','prompts','facts','unknown','failed','cancelled','deleted','camera','usage-unknown',
    'journal-hash','journal-scope','missing-journal','changed-observation','copied-without-original-call'])
def test_camera_reuse_requires_owned_identical_pixels_and_original_metered_receipts(tmp_path,change):
    descriptor={'renderStage':'environment','pipelineParentId':'parent','sceneReviewVersion':1,
        'peopleInventoryVersion':1,'inventoryContinuityVersion':1,'spaceKind':'interior','sceneCast':[]}
    scope={'owner_id':'owner','world_id':'world','line_id':'line','checkpoint_id':'cp','profile_id':'image-profile','profile_version':1}
    source=N(id='source',status='ready',descriptor=deepcopy(descriptor),
        image={'sha256':'a'*64,'bindingImageHash':'a'*64,'anchorStatus':'complete','bindingTaskId':'old'},**scope)
    row=N(id='target',status='ready',descriptor={**deepcopy(descriptor),'renderStage':'actor','inputSceneId':'source',
        'headInspectionVersion':2,'sceneCast':[{'entityId':'new-person'}]},image={'sha256':'b'*64},**scope)
    targets={'fixtures':[],'exits':[]};context=inspection_context(source,targets,narrative='public story')
    inventory=proof();inventory['imageHash']='a'*64
    stages=['analysis:people-view:'+str(i) for i in range(4)]
    metadata={'executionProfile':{'model':'same-model','version':1},'executionPrompts':{'inventory':'frozen-v1'}}
    prior=N(id='old',owner_id='owner',task_type='world_scene_binding',state='succeeded',provider_state='completed',
        cancel_requested=False,result={},payload={'sceneId':'source','worldId':'world','lineId':'line'},
        checkpoint={**deepcopy(metadata),'calls':{stage:{'callId':str(i)} for i,stage in enumerate(stages)},
            'bindingInspection':{'sceneId':'source','imageHash':'a'*64,'context':context,'version':VERSION},
            'completedSceneReads':{'sceneId':'source','imageHash':'a'*64,'context':context,'review':observation(),
                'inventory':deepcopy(inventory)}})
    task=N(id='current',owner_id='owner',payload={'sceneId':'target','worldId':'world','lineId':'line'},checkpoint=deepcopy(metadata))
    usages={str(i):N(owner_id='owner',task_id='old',operation=stage,status='completed') for i,stage in enumerate(stages)}
    for i,stage in enumerate(stages):
        result={'text':json.dumps(inventory['views'][i]['observation']),'finishReason':'stop'}
        journal={'taskId':'old','callId':str(i),'result':result,'hash':digest(json.dumps(result,ensure_ascii=False,sort_keys=True))}
        (tmp_path/f'journal-old-{i}.json').write_text(json.dumps(journal))
    if change=='explicit':task.payload['purpose']='explicit-independent-image-review'
    elif change=='owner':row.owner_id='foreign'
    elif change in ('world','line','checkpoint'):setattr(source,change+'_id','foreign')
    elif change=='parent':source.descriptor['pipelineParentId']='another'
    elif change=='image-profile':source.profile_version=2
    elif change=='vision-profile':task.checkpoint['executionProfile']={'version':2}
    elif change=='prompts':task.checkpoint['executionPrompts']={'inventory':'changed'}
    elif change=='facts':row.descriptor['visualFacts']={'requirements':[{'id':'no-live-goose'}]}
    elif change=='unknown':prior.provider_state='unknown'
    elif change=='failed':prior.state='failed'
    elif change=='cancelled':prior.cancel_requested=True
    elif change=='deleted':prior.result={'deleted':True}
    elif change=='usage-unknown':usages['0'].status='unknown'
    elif change in ('journal-hash','journal-scope'):
        path=tmp_path/'journal-old-0.json';j=json.loads(path.read_text());j['hash' if change=='journal-hash' else 'taskId']='invalid';path.write_text(json.dumps(j))
    elif change=='missing-journal':(tmp_path/'journal-old-0.json').unlink()
    elif change=='changed-observation':prior.checkpoint['completedSceneReads']['inventory']['views'][0]['observation']['evidence']='Different, unjournalled observation.'
    elif change=='copied-without-original-call':prior.checkpoint['calls'].pop(stages[0])
    before=deepcopy(prior.checkpoint)
    records={'source':source,'old':prior,'cp':N(owner_id='owner',world_id='world')}
    db=Mock();db.get.side_effect=lambda model,ident:records.get(ident)
    db.scalar.side_effect=lambda stmt:usages.get(list(stmt.compile().params.values())[0])
    cameras=[b'actual camera '+str(i).encode() for i in range(8)];current=list(cameras)
    if change=='camera':current[0]=b'newly painted camera'
    with patch('app.scenes.binding.placement_narrative',return_value='public story'),\
            patch('app.scenes.service.scene_view',return_value={}),\
            patch('app.scenes.grounding.inspection_targets',return_value=targets),\
            patch('app.scenes.service.image_bytes',return_value=b'original texture'),\
            patch('app.scenes.binding.inspection_image',return_value=(b'bounded panorama',(2048,1024))),\
            patch('app.scenes.binding.inspection_cameras',return_value=cameras),\
            patch('app.scenes.inventory_receipts.private_path',side_effect=lambda owner,name:tmp_path/name):
        actual=unchanged_views(db,task,row,current)
    if change in ('camera','changed-observation','copied-without-original-call'):assert set(actual)=={1,2,3}
    elif change:assert actual=={}
    else:
        assert set(actual)=={0,1,2,3}
        assert actual[0]['continuity']['sourceTaskId']=='old'
        assert actual[0]['continuity']['sourceImageHash']=='a'*64
        assert actual[0]['continuity']['targetImageHash']=='b'*64
        assert evaluate({'version':1,'imageHash':'b'*64,'views':list(actual.values())},'b'*64)['verified']
    assert prior.checkpoint==before


@pytest.mark.parametrize('stage,current,size',[('actor',True,'1K'),('environment-edit',True,'1K'),
    ('environment',True,'2K'),('actor',False,'2K'),('environment-edit',False,'2K')])
def test_new_local_edit_requests_native_output_and_preserves_legacy_and_full_panorama(monkeypatch,stage,current,size):
    from .test_scene_transport import transport,descriptor
    from app.scenes.provider import generate,MODEL
    from app.providers import encrypt_key
    requests=[];source=pixel_image()
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(source).decode()}]})
    transport(monkeypatch,handle)
    value={**descriptor(),'renderStage':stage,'worldTitle':'Test','paintedActor':{'name':'NPC','description':'adult'},
        'placement':{'pose':'standing','bodyBoxPixels':[170,130,280,450],'groundPointPixels':[225,450]},
        **({'localEditResolutionVersion':1} if current else {})}
    before=deepcopy(value)
    image,metadata,_=generate(value,encrypt_key('ISOLATED-TEST-CREDENTIAL'),source)
    assert len(requests)==1 and requests[0]['model']==MODEL and requests[0]['size']==size
    assert requests[0]['ratio']==('21:9' if stage=='environment' else '1:1')
    if stage!='environment':assert metadata['width']==metadata['height']==1024
    assert value==before


@pytest.mark.parametrize('claimed',[False,True])
def test_real_task_camera_carry_saves_four_calls_and_keeps_original_rows_and_journals(client,scene_world,profile,claimed):
    from hashlib import sha256
    from sqlalchemy import select,func
    from app.db import SessionLocal
    from app.models import Task,ModelProfile,UsageRecord
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene
    from app.scenes.pipeline import stage
    from app.storage import private_path
    from app.task_lease import ClaimedTask
    from .test_scene_progressive import prepared_preview
    from .test_scenes import API
    w=scene_world;parent_id,source_id=prepared_preview(client,w);calls=[]
    before=client.get(API+w['root']).json();targets={'characters':[],'fixtures':[],'exits':[]}
    def received(model,prompt,data,limit):
        mode=data.get('inspectionMode');calls.append(mode)
        if mode=='single-panorama-composition-v1':value=observation()
        elif mode=='single-camera-physical-head-inventory':value={'count':0,'heads':[],'confidence':.98,'evidence':'No physical people in these original room pixels.'}
        else:value={'characters':[],'fixtures':[],'exits':[],'placements':[]}
        return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
            'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
    with patch('app.scenes.binding.inspection_targets',return_value=targets),\
            patch('app.scenes.grounding.inspection_targets',return_value=targets),\
            patch('app.worker.MockAdapter.generate',side_effect=received):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,source_id)
            row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'peopleInventoryVersion':1,'sceneReviewVersion':1,'inspectionPerformanceVersion':1,
                'inspectionReceiptVersion':1,'inventoryContinuityVersion':1}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();original=row.image['bindingTaskId']
        bind_scene(ClaimedTask(original,'source-lease') if claimed else original)
        with SessionLocal() as db:
            source=db.get(SceneImage,source_id);old=db.get(Task,original)
            assert source.image['anchorStatus']=='complete' and len(calls)==6
            original_fields=deepcopy({c.name:getattr(old,c.name) for c in Task.__table__.columns})
            original_usage=[{c.name:getattr(u,c.name) for c in UsageRecord.__table__.columns}
                for u in db.scalars(select(UsageRecord).where(UsageRecord.task_id==original).order_by(UsageRecord.id))]
            journals={c['callId']:sha256(private_path(w['owner'],f"journal-{original}-{c['callId']}.json").read_bytes()).hexdigest()
                for c in old.checkpoint['calls'].values()}
            target=stage(db,db.get(SceneImage,parent_id),{**source.descriptor,'kind':'scene-environment-edit',
                'renderStage':'environment-edit','inputSceneId':source.id},received=source,inspect_received=False)
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,target)
            db.commit();current=target.image['bindingTaskId'];target_id=target.id
        bind_scene(ClaimedTask(current,'target-lease') if claimed else current)
    with SessionLocal() as db:
        target=db.get(SceneImage,target_id);assert target.image['anchorStatus']=='complete'
        job=db.get(Task,current);assert set(job.checkpoint['calls'])=={'analysis:scene-review','analysis:scene-binding'}
        assert len(calls)==8 and db.scalar(select(func.count()).select_from(UsageRecord))==8
        assert len(target.image['peopleInventory']['views'])==4
        assert all(v['continuity']['sourceTaskId']==original for v in target.image['peopleInventory']['views'])
        assert {c.name:getattr(db.get(Task,original),c.name) for c in Task.__table__.columns}==original_fields
        assert [{c.name:getattr(u,c.name) for c in UsageRecord.__table__.columns}
            for u in db.scalars(select(UsageRecord).where(UsageRecord.task_id==original).order_by(UsageRecord.id))]==original_usage
        assert all(sha256(private_path(w['owner'],f'journal-{original}-{ident}.json').read_bytes()).hexdigest()==value for ident,value in journals.items())
    assert client.get(API+w['root']).json()==before


@pytest.mark.parametrize('outcome',['shadow','physical-person','unknown'])
def test_live_task_count_conflict_requires_a_fresh_blind_detail_and_preserves_original(client,scene_world,profile,outcome):
    from threading import Lock
    from app.db import SessionLocal
    from app.models import Task,ModelProfile
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene
    from app.errors import Problem
    from .test_scene_progressive import prepared_preview
    from .test_scenes import API
    w=scene_world;_,child_id=prepared_preview(client,w)
    before=client.get(API+w['root']).json();lock=Lock();inventory_calls=0;details=[]
    targets={'characters':[],'fixtures':[],'exits':[]}
    with patch('app.scenes.binding.inspection_targets',return_value=targets):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child_id)
            row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'peopleInventoryVersion':1,'sceneReviewVersion':1,'inspectionPerformanceVersion':1,
                'peopleClarificationVersion':1,'peopleCountConflictVersion':1,'inspectionReceiptVersion':1}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();tid=row.image['bindingTaskId']
        def received(model,prompt,data,limit):
            nonlocal inventory_calls
            mode=data.get('inspectionMode')
            if mode in ('single-panorama-composition-v1','single-panorama-count-conflict-v1'):value=observation()
            elif mode=='single-camera-physical-head-inventory':
                with lock:ordinal=inventory_calls;inventory_calls+=1
                value={'count':1 if ordinal==0 else 0,'heads':[head([491,660,604,755])] if ordinal==0 else [],
                    'confidence':.95,'evidence':'Independent inspection of the actual room pixels and a dark hearth shape.'}
            elif mode=='same-camera-person-detail':
                details.append(data)
                assert not {'cast','expectedCount','expectedIds'}&set(data)
                if outcome=='unknown':raise Problem('PROVIDER_STATUS_UNKNOWN','unknown')
                present=outcome=='physical-person'
                value={'count':int(present),'heads':[head([491,660,604,755])] if present else [],'confidence':.95,
                    'evidence':'Actual enlarged context distinguishes a physical figure from the hearth shadow.'}
            else:value={'characters':[],'fixtures':[],'exits':[],'placements':[]}
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):
            if outcome=='unknown':
                with pytest.raises(Problem) as error:bind_scene(tid)
                assert error.value.code=='PROVIDER_STATUS_UNKNOWN'
            else:bind_scene(tid)
    assert inventory_calls==4 and len(details)==1
    with SessionLocal() as db:
        row=db.get(SceneImage,child_id);task=db.get(Task,tid)
        assert len([s for s in task.checkpoint['calls'] if s.startswith('analysis:people-conflict:')])==1
        if outcome!='unknown':
            assert row.image['anchorStatus']==('complete' if outcome=='shadow' else 'incomplete')
            original=task.checkpoint.get('completedSceneReads')
            if outcome=='shadow':
                assert original and any(v['observation']['count']==1 and v['clarification']['observation']['count']==0
                    for v in original['inventory']['views'] if v.get('clarification'))
            else:assert 'unexpected-people' in row.image['composition']['issues']
    assert client.get(API+w['root']).json()==before
