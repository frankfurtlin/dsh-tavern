"""P0: bounded parallel reads, physical story facts and scene-only stop."""
from contextvars import ContextVar
from copy import deepcopy
from threading import Barrier
from unittest.mock import Mock,patch
import io,json
import pytest
from PIL import Image
from app.errors import Problem
from app.scenes.parallel import ordered_reads
from app.scenes.facts import visual_facts,evaluate,prompt
from app.scenes.placements import inspect_placements
from .test_scenes import scene_world,request,API
from .test_scene_progressive import observation


def test_parallel_heads_keep_durable_ordinals_and_each_received_proof():
    from .test_scene_heads import inputs,result,confirmed_crop
    from app.scenes.heads import inspect_heads,proof_valid
    parsed,cast,cameras,image_hash=inputs()
    parsed['characters'].append({**deepcopy(parsed['characters'][0]),'entityId':'b'})
    cast.append({'entityId':'b','name':'彼得森'})
    barrier=Barrier(2);stages=[];progress=[]
    def call(stage,prompt,data,limit):
        stages.append(stage)
        if data['inspectionMode']=='person-head':
            barrier.wait(timeout=3)
            return result({'visible':True,'headBoxPixels':[334,224,377,280],'confidence':.95})
        return result(confirmed_crop())
    measured=inspect_heads(parsed,cast,cameras,image_hash,call,4096,parallel=True,on_progress=progress.append)
    assert set(stages)=={'analysis:head:0:0','analysis:head:1:0','analysis:head-crop:0:0','analysis:head-crop:1:0'}
    assert all(proof_valid(p.get('headReview'),image_hash) for p in measured['characters'])
    assert all(proof_valid(p.get('headReview'),image_hash) for p in progress[-1]['characters'])
    assert all('headReview' not in p for p in parsed['characters'])


def test_parallel_bodies_preserve_each_head_and_measure_each_physical_contact():
    from .test_scene_bodies import fixture,anchors
    from app.scenes.bodies import inspect_bodies
    parsed,cast,cameras,observed=fixture();second=deepcopy(parsed['characters'][0])
    second.update(entityId='second',viewIndex=1);second['headReview']['viewIndex']=1
    parsed['characters'].append(second);cast.append({'entityId':'second','name':'梅舟'})
    barrier=Barrier(2);stages=[];progress=[]
    def call(stage,prompt,data,limit):
        stages.append(stage);barrier.wait(timeout=3)
        assert data['person']['entityId']==('person' if data['camera']['viewIndex']==0 else 'second')
        return {'text':json.dumps(observed)}
    measured=inspect_bodies(parsed,cast,cameras,'a'*64,(2048,1024),call,4096,parallel=True,on_progress=progress.append)
    assert set(stages)=={'analysis:body:0:0','analysis:body:1:0'}
    assert set(anchors(measured,cast))=={'person','second'}
    assert set(anchors(progress[-1],cast))=={'person','second'}
    assert [p['headReview'] for p in measured['characters']]==[p['headReview'] for p in parsed['characters']]


def test_independent_people_search_uses_only_nonempty_blind_views_and_unique_stable_stages():
    from .test_scene_heads import camera
    from app.scenes.people import inspect_people
    cast=[{'entityId':'first','name':'唐禾'},{'entityId':'second','name':'梅舟'}]
    barrier=Barrier(2);calls=[]
    def call(stage,prompt,data,limit):
        calls.append(stage);barrier.wait(timeout=3)
        assert data['inspectionMode']=='single-person' and data['camera']['viewIndex']==2
        x=100 if data['person']['entityId']=='first' else 300
        return {'text':json.dumps({'entityId':data['person']['entityId'],'visible':True,'physicalPerson':True,
            'confidence':.95,'pose':'standing','headBoxPixels':[x,200,x+30,240]})}
    value=inspect_people({'characters':[]},cast,[camera()]*8,call,4096,view_indices=[2],recount=False,parallel=True)
    assert set(calls)=={'analysis:person:0:2','analysis:person:1:2'}
    assert [p['entityId'] for p in value['characters']]==['first','second']
    assert all(p['viewIndex']==2 for p in value['characters'])


@pytest.mark.parametrize('single_reads',[False,True])
def test_received_environment_preview_does_not_wait_for_slow_bulk_coordinates(client,scene_world,profile,single_reads):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time
    from app.db import SessionLocal
    from app.models import ModelProfile,Task,UsageRecord
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene
    from .test_scene_progressive import prepared_preview
    parent_id,child_id=prepared_preview(client,scene_world);blocked=Event();release=Event();calls=[]
    with patch('app.scenes.binding.inspection_targets',return_value={'characters':[],'fixtures':[],'exits':[]}):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child_id);row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'peopleInventoryVersion':1,'sceneReviewVersion':1,'inspectionPerformanceVersion':1,
                **({'independentSceneReadsVersion':1} if single_reads else {})}
            row.image={k:v for k,v in row.image.items() if k not in ('sceneReview','peopleInventory','composition')}
            parent=db.get(SceneImage,parent_id);parent.descriptor={**parent.descriptor,'peopleInventoryVersion':1}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();tid=row.image['bindingTaskId'];image_hash=row.image['sha256']
        def received(model,prompt,data,limit):
            mode=data.get('inspectionMode');calls.append(mode)
            if mode is None:
                assert not single_reads,'The redundant nine-image exchange must not be sent'
                blocked.set();assert release.wait(10)
                value={'characters':[],'fixtures':[],'exits':[]}
            elif mode=='single-panorama-composition-v1':value=observation()
            else:value={'count':0,'heads':[],'confidence':.98,'evidence':'No physical people in these original room pixels.'}
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received),ThreadPoolExecutor(max_workers=1) as pool:
            execution=pool.submit(bind_scene,tid)
            try:
                if not single_reads:assert blocked.wait(5)
                deadline=time.monotonic()+6;preview=None
                while time.monotonic()<deadline:
                    response=client.get(API+'/scenes/'+parent_id,params={'lineId':scene_world['line']})
                    assert response.status_code==200,response.text
                    preview=response.json().get('preview')
                    if preview:break
                    time.sleep(.03)
                assert preview and preview['readOnly'] and preview['sourceHash']==image_hash
                with SessionLocal() as db:
                    if not single_reads:assert db.get(Task,tid).state!='succeeded'
            finally:release.set();execution.result(timeout=10)
    assert len(calls)==(5 if single_reads else 6)


@pytest.mark.parametrize('single_reads',[False,True])
def test_single_camera_routes_are_parallel_but_absence_never_becomes_a_verified_exit(client,scene_world,profile,single_reads):
    from threading import Lock
    from app.db import SessionLocal
    from app.models import ModelProfile,Task
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene
    from app.scenes.apertures import SYSTEM
    from .test_scene_apertures import TARGET
    from .test_scene_progressive import prepared_preview
    _,child_id=prepared_preview(client,scene_world);barrier=Barrier(4);lock=Lock();route_calls=[]
    with patch('app.scenes.binding.inspection_targets',return_value={'fixtures':[],'exits':[TARGET]}):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child_id);row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'peopleInventoryVersion':1,'sceneReviewVersion':1,'inspectionPerformanceVersion':1,
                'independentSceneReadsVersion':1 if single_reads else 0,'apertureInspectionVersion':1}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();tid=row.image['bindingTaskId']
        def received(model,prompt,data,limit):
            mode=data.get('inspectionMode')
            if mode is None:
                assert not single_reads
                value={'characters':[],'fixtures':[],'exits':[]}
            elif mode=='native-aperture':
                with lock:route_calls.append(mode);ordinal=len(route_calls)
                if ordinal<=4:barrier.wait(timeout=8)
                value={'coordinateSystem':SYSTEM,'routes':[{'id':'yard','matched':False,'kind':'door-open',
                    'box_2d':None,'confidence':.96,'evidence':'No physical open doorway is visible in this actual camera.'}]}
            elif mode=='single-panorama-composition-v1':value=observation()
            else:value={'count':0,'heads':[],'confidence':.98,'evidence':'No physical people in these original room pixels.'}
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):bind_scene(tid)
    assert len(route_calls)==8
    with SessionLocal() as db:
        row=db.get(SceneImage,child_id)
        assert row.image['anchorStatus']!='complete' and row.image['missingExitIds']==['yard']
        assert 'exits:yard' not in row.image['spatialAnchors']
        assert len(db.get(Task,tid).checkpoint['calls'])==(13 if single_reads else 14)


@pytest.mark.parametrize('pose,height',[('standing',152),('seated',110),('kneeling',110)])
@pytest.mark.parametrize('version,grounded,accepted',[(1,True,False),(2,True,True),(2,False,False)])
def test_size_planning_does_not_discard_feasible_slots_or_skip_actual_support(pose,height,version,grounded,accepted):
    from .test_scene_placement_review import camera,placement
    from .test_scene_supports import verdict
    from app.scenes.placements import readable_slot
    part=placement();top=190 if pose=='standing' else 280
    part.update(pose=pose,bodyBoxPixels=[180,top,250,top+height],groundPointPixels=[215,top+height-2])
    stages=[]
    def call(stage,prompt,data,limit):
        stages.append(stage)
        if stage.startswith('analysis:initial-slot:'):
            return {'text':json.dumps({**verdict(),'pose':pose,'poseSupported':grounded,'pelvisSupport':'chair' if pose=='seated' else 'none'})}
        return {'text':'{"placements":[]}'}
    value=inspect_placements({'placements':[part]},[{'entityId':'npc','expectedPose':pose}],[camera()]*4,'',call,1000,
        independent=True,verify_support=True,verify_initial=True,readable_version=version)
    assert bool(value['placements']) is accepted
    assert ('analysis:initial-slot:0' in stages)==(version==2)
    if accepted:assert value['placements']==[part]


@pytest.mark.parametrize('change',[None,'owner','unknown','cancelled','hash','context','prompts','profile',
    'usage-owner','usage-unknown','journal-hash','journal-scope','people-count','missing-journal','explicit','binding','binding-unknown'])
def test_received_blind_reads_require_settled_same_context_owned_receipts(tmp_path,change):
    from types import SimpleNamespace as N
    from app.domain import digest
    from app.scenes.received_reads import preflight
    context='frozen-context';image_hash='a'*64;owner='owner'
    inspection={'sceneId':'scene','imageHash':image_hash,'context':context,'version':1}
    metadata={'bindingInspection':inspection,'executionProfile':{'version':1},'executionPrompts':{'review':'v1'}}
    task=N(id='new',owner_id=owner,checkpoint={**deepcopy(metadata),'reuseInspectionTaskId':'old'})
    inventory={'version':1,'imageHash':image_hash,'views':[{'viewIndex':i,'observation':{
        'count':0,'heads':[],'confidence':.98,'evidence':'No physical person is visible in these original room pixels.'}} for i in range(4)]}
    stages=['analysis:scene-review',*[f'analysis:people-view:{i}' for i in range(4)]]
    if change in ('binding','binding-unknown'):stages.append('analysis:scene-binding')
    prior=N(id='old',owner_id=owner,task_type='world_scene_binding',state='succeeded',provider_state='completed',
        cancel_requested=False,result={},payload={'sceneId':'scene','worldId':'world','lineId':'line'},
        checkpoint={**deepcopy(metadata),'calls':{stage:{'callId':str(i)} for i,stage in enumerate(stages)},
            'completedSceneReads':{'sceneId':'scene','imageHash':image_hash,'context':context,
                'review':observation(),'inventory':inventory}})
    row=N(id='scene',owner_id=owner,world_id='world',line_id='line',image={'sha256':image_hash},
        descriptor={'inspectionReceiptVersion':1,'sceneReviewVersion':1,'peopleInventoryVersion':1,'spaceKind':'interior','sceneCast':[]})
    usages=[N(owner_id=owner,task_id='old',operation=stage,status='completed') for stage in stages]
    for i,stage in enumerate(stages):
        response={'text':'received original supplier response','finishReason':'stop'}
        if stage=='analysis:scene-binding':
            from app.scenes.coordinates import SYSTEM
            row.descriptor['coordinateInspectionVersion']=1
            response['text']=json.dumps({'coordinateSystem':SYSTEM,'fixtures':[{'boxPixels':[100,200,300,900]}]})
        journal={'taskId':'old','callId':str(i),'result':response,'hash':digest(json.dumps(response,ensure_ascii=False,sort_keys=True))}
        (tmp_path/f'journal-old-{i}.json').write_text(json.dumps(journal))
    if change=='owner':prior.owner_id='foreign'
    elif change=='unknown':prior.provider_state='unknown'
    elif change=='cancelled':prior.cancel_requested=True
    elif change=='hash':row.image['sha256']='b'*64
    elif change=='context':context='changed-context'
    elif change=='prompts':task.checkpoint['executionPrompts']={'review':'v2'}
    elif change=='profile':task.checkpoint['executionProfile']={'version':2}
    elif change=='usage-owner':usages[0].owner_id='foreign'
    elif change=='usage-unknown':usages[0].status='unknown'
    elif change in ('journal-hash','journal-scope'):
        path=tmp_path/'journal-old-0.json';value=json.loads(path.read_text());value['hash' if change=='journal-hash' else 'taskId']='wrong';path.write_text(json.dumps(value))
    elif change=='missing-journal':(tmp_path/'journal-old-0.json').unlink()
    elif change=='people-count':prior.checkpoint['completedSceneReads']['review']=observation(1)
    elif change=='explicit':task.checkpoint.pop('reuseInspectionTaskId')
    elif change=='binding-unknown':usages[-1].status='reserved'
    db=Mock();db.get.return_value=prior;db.scalar.side_effect=usages
    with patch('app.scenes.received_reads.private_path',side_effect=lambda own,name:tmp_path/name):
        actual=preflight(db,task,row,context)
    if change and change!='binding':assert actual=={}
    else:
        assert set(actual)==({'review',0,1,2,3,'binding'} if change=='binding' else {'review',0,1,2,3})
        assert json.loads(actual['review']['text'])==observation()
        if change=='binding':assert json.loads(actual['binding']['text'])['fixtures'][0]['boxPixels']==[51.2,102.4,153.6,460.8]


def test_actor_correction_keeps_wrong_counts_and_detected_names_out_of_generation():
    from app.scenes.provider import prompt_for
    descriptor={'renderStage':'actor','worldTitle':'Test','worldGenre':'mystery',
        'paintedActor':{'entityId':'npc','name':'梅舟','description':'书店的年轻店员'},
        'placement':{'pose':'standing','bodyBoxPixels':[170,130,280,450],'groundPointPixels':[225,450]},
        'correctionReasons':{'composition':{'visiblePeopleCount':3},'detectorIssues':[{'name':'WrongIdentity','reason':'unexpected-people'}]}}
    value=prompt_for(descriptor)
    assert '梅舟' in value and 'EXACTLY ONE physical person' in value
    assert 'WrongIdentity' not in value and 'visiblePeopleCount' not in value and 'twins' not in value


def test_stale_parent_selection_cannot_fail_a_newly_queued_inspection(client,scene_world,profile):
    from app.db import SessionLocal
    from app.models import Task
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding
    from app.scenes.worker import process_one
    from .test_scene_pipeline import start
    from .test_scenes import generated
    parent_id,child_id=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,child_id)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        task_id=child.image['bindingTaskId'];db.commit()
    # Simulate a candidate selected BEFORE that commit but processed AFTER it.
    with patch('app.scenes.pipeline.candidate',side_effect=lambda db,excluded=():db.get(SceneImage,parent_id)), \
            patch('app.scenes.worker.generate',side_effect=AssertionError('No additional image request')):
        assert process_one()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);child=db.get(SceneImage,child_id);task=db.get(Task,task_id)
        assert parent.status=='queued' and parent.error_code is None
        assert parent.image['pipelineStageId']==child_id and child.image['anchorStatus']=='queued'
        assert task.state=='queued' and not task.cancel_requested and task.checkpoint['calls']=={}


def test_automatic_same_image_retry_reuses_five_settled_blind_reads_without_new_usage(client,scene_world,profile):
    from sqlalchemy import select,func
    from app.db import SessionLocal
    from app.models import ModelProfile,Task,UsageRecord
    from app.world.models import WorldLine
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene,evaluate_inspection
    from .test_scene_progressive import prepared_preview
    _,child_id=prepared_preview(client,scene_world);calls=[]
    with patch('app.scenes.binding.inspection_targets',return_value={'characters':[],'fixtures':[],'exits':[]}):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child_id);row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'peopleInventoryVersion':1,'sceneReviewVersion':1,'inspectionPerformanceVersion':1,'inspectionReceiptVersion':1}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();original=row.image['bindingTaskId'];image_hash=row.image['sha256']
        def received(model,prompt,data,limit):
            mode=data.get('inspectionMode');calls.append(mode)
            value=(observation() if mode=='single-panorama-composition-v1' else
                {'count':0,'heads':[],'confidence':.98,'evidence':'No physical people in these original room pixels.'}
                if mode=='single-camera-physical-head-inventory' else {'characters':[],'fixtures':[],'exits':[]})
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        def incomplete_contact(*args,**kwargs):
            result=evaluate_inspection(*args,**kwargs)
            result.update(anchorStatus='unavailable',detectionIssues=[{'kind':'characters','id':'npc','reason':'body-evidence-unconfirmed'}])
            return result
        with patch('app.worker.MockAdapter.generate',side_effect=received),patch('app.scenes.binding.evaluate_inspection',side_effect=incomplete_contact):bind_scene(original)
        with SessionLocal() as db:
            row=db.get(SceneImage,child_id);old=db.get(Task,original)
            assert old.checkpoint['completedSceneReads']['inventory']['imageHash']==image_hash
            assert 'verifiedImageInspection' not in old.checkpoint
            old_calls=deepcopy(old.checkpoint['calls'])
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();second=row.image['bindingTaskId'];assert second!=original
            assert db.get(Task,second).checkpoint['reuseInspectionTaskId']==original
        with patch('app.worker.MockAdapter.generate',side_effect=received):bind_scene(second)
    assert len(calls)==6 and calls.count('single-camera-physical-head-inventory')==4
    assert calls.count('single-panorama-composition-v1')==1
    with SessionLocal() as db:
        row=db.get(SceneImage,child_id)
        assert row.image['anchorStatus']=='complete' and row.image['sha256']==image_hash
        assert db.get(Task,original).checkpoint['calls']==old_calls
        assert len(db.get(Task,second).checkpoint['calls'])==0
        assert db.scalar(select(func.count()).select_from(UsageRecord))==6
        assert db.get(WorldLine,scene_world['line']).head_checkpoint_id==scene_world['cp']


def test_head_progress_cannot_erase_a_concurrent_paid_call_intent(client,scene_world,profile):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time
    from sqlalchemy import select,func
    from app.db import SessionLocal
    from app.models import Task,ModelProfile,UsageRecord
    from app.completion_policy import completion_limit
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,inspection_context
    from app.scenes.inspection_progress import save
    from app.worker import call_provider
    from .test_scene_progressive import prepared_preview
    _,child_id=prepared_preview(client,scene_world);entered=Event();release=Event();sent=Event();started=Event()
    with SessionLocal() as db:
        row=db.get(SceneImage,child_id)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
        db.commit();tid=row.image['bindingTaskId'];image_hash=row.image['sha256']
        limit=completion_limit(db.get(ModelProfile,profile['id']),'analysis',world=True)
    def hold_context(*args,**kwargs):
        entered.set();assert release.wait(8)
        return inspection_context(*args,**kwargs)
    def received(*args,**kwargs):
        sent.set();return {'text':'{}','finishReason':'stop','latencyMs':1,'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
    def reserve():
        started.set();return call_provider(tid,'analysis:concurrent-proof','world_scene_heads',{'_structured':True},limit)
    before=client.get(API+scene_world['root']).json()['head']
    with patch('app.scenes.binding.inspection_context',side_effect=hold_context),patch('app.worker.MockAdapter.generate',side_effect=received),ThreadPoolExecutor(max_workers=2) as pool:
        progress=pool.submit(save,tid,image_hash,{'characters':[]},{'characterAnchors':{}},{'fixtures':[],'exits':[]})
        try:
            assert entered.wait(8)
            request_future=pool.submit(reserve);assert started.wait(2)
            time.sleep(.2)
            assert not sent.is_set() and not request_future.done(),'Provider intent must wait for the atomic progress write'
        finally:release.set()
        progress.result(timeout=8);request_future.result(timeout=8)
    with SessionLocal() as db:
        task=db.get(Task,tid)
        assert task.checkpoint['headInspectionProgress']['sceneId']==child_id
        assert set(task.checkpoint['calls'])=={'analysis:concurrent-proof'}
        assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.task_id==tid))==1
    assert client.get(API+scene_world['root']).json()['head']==before


def test_parallel_reads_are_really_concurrent_and_preserve_lease_context():
    lease=ContextVar('inspection-test-lease');lease.set('current-lease')
    barrier=Barrier(4)
    def read(index):
        barrier.wait(timeout=2)
        return index,lease.get()
    assert ordered_reads(read,range(4))==[(n,'current-lease') for n in range(4)]


def test_parallel_failure_joins_received_siblings_and_retains_unknown_priority():
    settled=[];barrier=Barrier(3)
    def read(index):
        barrier.wait(timeout=2)
        if index==0:raise Problem('BUDGET_NOT_AUTHORIZED','known unsent')
        if index==1:raise Problem('PROVIDER_STATUS_UNKNOWN','unknown paid request')
        settled.append(index);return index
    with pytest.raises(Problem) as error:ordered_reads(read,range(3))
    assert error.value.code=='PROVIDER_STATUS_UNKNOWN' and settled==[2]


def test_unknown_batch_stops_not_yet_started_requests_instead_of_spending_more():
    calls=[]
    def read(index):
        calls.append(index)
        if index==0:raise Problem('PROVIDER_STATUS_UNKNOWN','Unknown paid request')
        return index
    with pytest.raises(Problem) as error:ordered_reads(read,range(8),workers=1)
    assert error.value.code=='PROVIDER_STATUS_UNKNOWN' and calls==[0]


@pytest.mark.parametrize('capacity',[4,8])
def test_global_visual_limit_bounds_multiple_concurrent_batches(monkeypatch,capacity):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock,BoundedSemaphore
    from app.scenes.parallel import visual_slot
    monkeypatch.setattr('app.scenes.parallel._slots',BoundedSemaphore(capacity))
    lock=Lock();barrier=Barrier(capacity);active=0;peak=0
    def read(index):
        nonlocal active,peak
        with visual_slot():
            with lock:active+=1;peak=max(peak,active)
            try:barrier.wait(timeout=8);return index
            finally:
                with lock:active-=1
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[pool.submit(ordered_reads,read,range(start,start+capacity),workers=capacity) for start in (0,capacity)]
        assert [f.result(timeout=12) for f in results]==[list(range(capacity)),list(range(capacity,capacity*2))]
    assert peak==capacity and active==0


@pytest.mark.parametrize('text,expected',[
    ('她剖开鹅嗉，从里面发现了蓝宝石。',True),
    ('鹅已经被宰杀。',True),('鸡已经煮熟。',True),
    ('她准备剖开鹅嗉。',False),('她没有剖开鹅嗉。',False),
    ('如果她剖开鹅嗉，可能发现东西。',False),('他带来一只活鹅。',False),
])
def test_completed_animal_transformation_excludes_intentions_and_negation(text,expected):
    value=visual_facts(None,{'entities':[]},[],text)
    assert bool(value)==expected


def test_absent_item_fact_comes_only_from_public_ledger_and_changes_cache():
    public={'entities':[{'id':'gem','name':'蓝宝石','type':'item'}]}
    assert visual_facts(None,public,public['entities'],'') is None
    facts=visual_facts(None,public,[],'')
    assert facts['requirements'][0]['id']=='absent-gem'
    assert '蓝宝石' in prompt({'visualFacts':facts})
    from .test_scene_consistency import scene
    from app.scenes.cache import appearance
    old=scene();new=deepcopy(old);new['descriptor']['visualFacts']=facts
    assert appearance(old)!=appearance(new)


def test_public_handover_to_a_present_character_is_not_an_absent_item():
    public={'entities':[{'id':'gem','name':'蓝宝石','type':'item','nearby':False},
        {'id':'countess','name':'伯爵夫人','type':'character','nearby':True}],
        'plot':{'confirmedHoldings':[{'itemName':'蓝宝石','holderName':'伯爵夫人'}]}}
    assert visual_facts(None,public,[],'') is None
    public['entities'][1]['nearby']=False
    assert visual_facts(None,public,[],'')['requirements'][0]['id']=='absent-gem'


def test_generation_omits_historical_animal_props_but_independent_fact_check_stays_exact():
    from app.scenes.facts import character_description
    facts=visual_facts(None,{'entities':[]},[],'妻子剖开鹅嗉。')
    descriptor={'visualFacts':facts};person={'description':'一位诚实的制服门房，圣诞清晨拾得帽子和一只鹅。'}
    before=deepcopy(person)
    assert '制服门房' in character_description(descriptor,person)
    assert '鹅' not in character_description(descriptor,person) and person==before
    assert 'goose' not in prompt(descriptor) and '鹅' not in prompt(descriptor)
    assert facts['requirements'][0]['id']=='no-live-goose'
    assert evaluate({'visualFactChecks':[{'id':'no-live-goose','consistent':False,'confidence':.96,
        'evidence':'A live goose is standing beside the fireplace.'}]},facts)==['visual-fact-mismatch']


@pytest.mark.parametrize('change,issue',[
    ({'consistent':False},'visual-fact-mismatch'),
    ({'consistent':'true'},'visual-facts-unconfirmed'),
    ({'confidence':.84},'visual-facts-unconfirmed'),
    ({'confidence':float('nan')},'visual-facts-unconfirmed'),
    ({'id':'other'},'visual-facts-unconfirmed'),
    ({'evidence':''},'visual-facts-unconfirmed'),
])
def test_story_fact_observation_cannot_pass_with_contradiction_or_invalid_evidence(change,issue):
    facts=visual_facts(None,{'entities':[]},[],'她剖开鹅嗉。')
    check={'id':'no-live-goose','consistent':True,'confidence':.95,'evidence':'There is no live goose anywhere in the actual panorama.'}
    assert evaluate({'visualFactChecks':[{**check,**change}]},facts)==[issue]


def test_global_review_requires_fact_proof_and_preserves_the_actual_observation():
    from app.scenes.scene_review import request as review_request,evaluate as review
    facts=visual_facts(None,{'entities':[]},[],'她剖开鹅嗉。')
    scene={'spaceKind':'interior','visualFacts':facts};value=observation()
    assert 'visual-facts-unconfirmed' in review(value,[],scene)['issues']
    value['visualFactChecks']=[{'id':'no-live-goose','consistent':False,'confidence':.95,'evidence':'A living white goose stands beside the seated man.'}]
    original=deepcopy(value)
    assert 'visual-fact-mismatch' in review(value,[],scene)['issues']
    assert value==original
    assert 'visualFactChecks' in review_request(b'pixels',scene)['requiredOutputKeys']


@pytest.mark.parametrize('accepted',[True,False])
def test_initial_placement_requires_independent_actual_support_before_search(accepted):
    from .test_scene_placement_review import placement
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='JPEG')
    proposal=placement();stages=[]
    def call(stage,prompt,data,limit):
        stages.append(stage)
        if stage.startswith('analysis:initial-slot:'):
            return {'text':json.dumps({'pose':'standing','poseSupported':True,'naturalAdultScale':True,
                'clearOfPassage':True,'footSurface':'walkable-ground' if accepted else 'furniture',
                'pelvisSupport':'none','confidence':.96,'evidence':'The exact shoe contact is on actual unobstructed room floor.'})}
        return {'text':'{"placements":[]}'}
    result=inspect_placements({'placements':[proposal],'exits':[]},[{'entityId':'npc'}],
        [out.getvalue()]*8,'',call,1000,independent=True,verify_support=True,single_context=True,verify_initial=True)
    assert bool(result['placements'])==accepted
    assert stages[0]=='analysis:initial-slot:0'
    assert len(stages)==(1 if accepted else 5)


def test_scene_stop_is_idempotent_and_does_not_change_story_or_dispatch(client,scene_world):
    w=scene_world;scene=request(client,w)
    before=client.get(API+w['root']).json()
    body={'sceneId':scene['id'],'expectedCheckpointId':w['cp']}
    response=client.post(API+w['root']+'/scene/stop',headers={'Idempotency-Key':'scene-only-stop'},json=body)
    assert response.status_code==200,response.text
    assert response.json()['errorCode']=='SCENE_STOPPED'
    assert not response.json()['progress']['canStop']
    again=client.post(API+w['root']+'/scene/stop',headers={'Idempotency-Key':'scene-only-stop'},json=body)
    assert again.status_code==200
    assert client.get(API+w['root']).json()==before
    with patch('app.scenes.worker.generate') as supplier:
        from app.scenes.worker import process_one
        assert not process_one();supplier.assert_not_called()
    assert request(client,w,'reopen-stopped')['errorCode']=='SCENE_STOPPED'


def test_scene_stop_rejects_wrong_checkpoint_without_cancelling(client,scene_world):
    w=scene_world;scene=request(client,w)
    result=client.post(API+w['root']+'/scene/stop',headers={'Idempotency-Key':'wrong-head-stop'},
        json={'sceneId':scene['id'],'expectedCheckpointId':'0'*32})
    assert result.status_code==409
    assert client.get(API+'/scenes/'+scene['id']).json()['status']=='queued'


def test_late_received_image_cannot_queue_a_new_inspection_after_pipeline_stop(client,scene_world,monkeypatch):
    from app.db import SessionLocal
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding
    from app.config import settings
    from app.models import uid
    w=scene_world;scene=request(client,w)
    with SessionLocal() as db:
        parent=db.get(SceneImage,scene['id']);parent.descriptor={**parent.descriptor,'renderPipeline':1}
        child=SceneImage(id=uid(),owner_id=parent.owner_id,world_id=parent.world_id,line_id=parent.line_id,
            checkpoint_id=parent.checkpoint_id,profile_id=parent.profile_id,profile_version=parent.profile_version,
            scene_hash='late-'+uid(),generation=1,status='running',descriptor={**parent.descriptor,'pipelineParentId':parent.id,'kind':'scene-environment'},image={})
        db.add(child);parent.image={'pipelineStageId':child.id};ident=child.id;db.commit()
    result=client.post(API+w['root']+'/scene/stop',headers={'Idempotency-Key':'stop-inflight-image'},
        json={'sceneId':scene['id'],'expectedCheckpointId':w['cp']})
    assert result.status_code==200
    monkeypatch.setattr(settings,'ai_mode','real')
    with SessionLocal() as db,patch('app.world.jobs.create_job') as create_job:
        child=db.get(SceneImage,ident);child.status='ready';child.image={'sha256':'received-real-bytes'}
        queue_binding(db,child)
        create_job.assert_not_called()


@pytest.mark.parametrize('storage',['source','receipt'])
def test_explicit_resume_recovers_stopped_received_pixels_without_another_image_post(client,scene_world,storage):
    from app.db import SessionLocal
    from app.scenes.models import SceneImage
    from app.scenes.characters import save_source
    from app.scenes.service import save_image
    from app.scenes.worker import process_one
    from .test_scene_pipeline import start
    from .test_scenes import generated
    parent_id,child_id=start(client,scene_world);picture,metadata,request_id=generated()
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);child=db.get(SceneImage,child_id)
        if storage=='source':save_source(child,picture);child.image={'sourceReceived':True,'sourceRequestId':request_id}
        else:child.image=save_image(child,picture,metadata,request_id)
        child.status='failed';child.error_code='SCENE_CONTEXT_CHANGED'
        parent.status='failed';parent.error_code='SCENE_STOPPED';db.commit()
    before=client.get(API+scene_world['root']).json()
    receipt=client.get(API+'/scenes/'+parent_id+'?lineId='+scene_world['line']).json()
    assert receipt['recovery']['canInspect'] and receipt['recovery']['imageRetained']
    body={'sceneId':parent_id,'expectedCheckpointId':scene_world['cp'],'independent':True}
    response=client.post(API+scene_world['root']+'/scene/inspect',json=body,headers={'Idempotency-Key':'resume-captured-'+storage})
    assert response.status_code==202,response.text
    assert response.json()['receiptRecovered'] and response.json()['imageRegenerated'] is False
    with patch('app.scenes.worker.generate') as supplier:
        assert process_one();supplier.assert_not_called()
    with SessionLocal() as db:assert db.get(SceneImage,child_id).status=='ready'
    assert client.get(API+scene_world['root']).json()==before


def test_resume_uses_owned_earlier_environment_and_blocks_a_late_superseded_actor(client,scene_world):
    from app.db import SessionLocal
    from app.scenes.models import SceneImage
    from app.scenes.pipeline import stage
    from app.scenes.worker import pipeline_context_active
    from app.world.models import WorldLine
    from .test_scene_progressive import prepared_preview
    parent_id,environment_id=prepared_preview(client,scene_world)
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);environment=db.get(SceneImage,environment_id);original_hash=environment.image['sha256']
        actor=stage(db,parent,{**environment.descriptor,'kind':'scene-actor-edit','renderStage':'actor','inputSceneId':environment_id})
        actor.status='failed';actor.error_code='SCENE_CONTEXT_CHANGED';actor_id=actor.id
        parent.status='failed';parent.error_code='SCENE_STOPPED';db.commit()
    before=client.get(API+scene_world['root']).json()
    receipt=client.get(API+'/scenes/'+parent_id+'?lineId='+scene_world['line']).json()
    assert receipt['recovery']['canInspect'] and receipt['recovery']['savedImages'][0]['sceneId']==parent_id
    response=client.post(API+scene_world['root']+'/scene/inspect',headers={'Idempotency-Key':'resume-earlier-owned-environment'},
        json={'sceneId':parent_id,'expectedCheckpointId':scene_world['cp'],'independent':True})
    assert response.status_code==202,response.text
    assert response.json()['imageRegenerated'] is False
    with SessionLocal() as db:
        parent=db.get(SceneImage,parent_id);child=db.get(SceneImage,parent.image['pipelineStageId'])
        assert child.id not in (actor_id,environment_id) and child.image['sha256']==original_hash
        assert not pipeline_context_active(db,db.get(SceneImage,actor_id),db.get(WorldLine,scene_world['line']))
    assert client.get(API+scene_world['root']).json()==before


@pytest.mark.parametrize('workers',[2,3])
def test_parallel_image_executors_do_not_dispatch_the_same_paid_reservation(client,scene_world,workers):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from .test_scenes import generated
    from app.scenes.worker import process_one
    scene=request(client,scene_world);entered=Event();release=Event()
    def receive(*args,**kwargs):
        entered.set();assert release.wait(8)
        return generated()
    with patch('app.scenes.worker.generate',side_effect=receive) as supplier,ThreadPoolExecutor(max_workers=workers) as pool:
        first=pool.submit(process_one)
        try:
            assert entered.wait(8)
            others=[pool.submit(process_one) for _ in range(workers-1)]
            assert all(future.result(timeout=8) is False for future in others)
        finally:release.set()
        assert first.result(timeout=8) is True
        supplier.assert_called_once()
    assert client.get(API+'/scenes/'+scene['id']).json()['status']=='ready'


@pytest.mark.parametrize('workers',[2,3])
def test_receipt_captured_by_a_running_executor_is_not_recovered_concurrently(client,scene_world,profile,workers):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import select,func
    from app.db import SessionLocal
    from app.models import Task
    from app.scenes.models import SceneImage
    from app.scenes.worker import process_one,executions
    from .test_scene_pipeline import start
    from .test_scenes import generated
    _,child=start(client,scene_world);entered=Event();release=Event()
    data,metadata,receipt=generated()
    def receive(*args,**kwargs):
        kwargs['capture'](data,receipt);entered.set();assert release.wait(12)
        return data,metadata,receipt
    with patch('app.scenes.worker.generate',side_effect=receive),patch('app.scenes.binding.settings.ai_mode','live'),ThreadPoolExecutor(max_workers=workers) as pool:
        first=pool.submit(process_one)
        try:
            assert entered.wait(12)
            with SessionLocal() as db:assert db.get(SceneImage,child).status=='saving'
            others=[pool.submit(process_one) for _ in range(workers-1)]
            assert all(future.result(timeout=8) is False for future in others)
        finally:release.set()
        assert first.result(timeout=12)
    assert not executions
    with SessionLocal() as db:
        row=db.get(SceneImage,child);assert row.status=='ready' and row.image['anchorStatus']=='queued'
        assert db.scalar(select(func.count()).select_from(Task).where(Task.task_type=='world_scene_binding'))==1
        assert db.get(Task,row.image['bindingTaskId']).payload['sceneId']==child


@pytest.mark.parametrize('pose',['standing','seated','kneeling'])
def test_private_anatomy_guide_has_one_connected_head_and_torso(pose):
    from app.scenes.perspective import camera_reference
    from .test_scene_placement_review import placement
    source=io.BytesIO();Image.new('RGB',(1024,512),'black').save(source,format='PNG')
    slot=placement();slot['pose']=pose
    picture=Image.open(io.BytesIO(camera_reference(source.getvalue(),0,slot))).convert('RGB')
    # Flood the actual private guide pixels. A floating head is a separate
    # connected component and is the ambiguity observed in the real failure.
    colours={(169,149,128),(102,113,108),(84,92,87)}
    remaining={(x,y) for y in range(512) for x in range(512) if picture.getpixel((x,y)) in colours}
    components=0
    while remaining:
        components+=1;queue=[remaining.pop()]
        while queue:
            x,y=queue.pop()
            for point in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if point in remaining:remaining.remove(point);queue.append(point)
    assert components==1


@pytest.mark.parametrize('protect_passage',[False,True])
def test_actor_crop_cannot_redesign_wall_art_outside_body_bay_or_a_verified_passage(protect_passage):
    from app.scenes.perspective import reproject_camera,actor_crop
    from app.scenes.inspection import sphere_pixel
    from .test_scene_placement_review import placement
    original=(18,102,188);painted=(239,34,0)
    source=io.BytesIO();Image.new('RGB',(1024,512),original).save(source,format='PNG')
    edit=io.BytesIO();Image.new('RGB',(512,512),painted).save(edit,format='PNG')
    slot={**placement(),'maskVersion':3,'surroundingsVersion':1};slot['editCropPixels']=actor_crop(slot)
    data,_=reproject_camera(source.getvalue(),edit.getvalue(),0,slot,
        protected_boxes=[[205,280,245,320]] if protect_passage else [])
    actual=Image.open(io.BytesIO(data)).convert('RGB')
    def pixel(x,y):
        a,b=sphere_pixel(0,x,y,1024,512);return actual.getpixel((round(a)%1024,round(b)))
    assert pixel(320,260)==original  # Wall inside context crop, outside actor bay.
    assert pixel(225,300)==(original if protect_passage else painted)
    assert pixel(205,360)==painted


def test_wall_art_recheck_gets_actual_detail_views_without_redefining_projection_or_people():
    from app.scenes.artifact_conflicts import request
    value=request(b'whole-texture',{},['Small framed portrait in VIEW 2.'],cameras=[b'actual-camera']*4)
    assert len(value['_sceneImages'])==2
    assert value['imageRoles'][1]['viewIndex']==2
    assert 'Image 1 alone defines global projection' in value['instruction']
    assert 'there are no such cameras here' not in value['instruction']


def test_first_actor_contract_keeps_unchanged_exit_proof_but_never_upgrades_old_head_evidence():
    from .test_scene_continuity import setup,carry
    from app.scenes.binding import inspection_context
    values=setup();_,source,_,targets,_,_,task=values
    source.descriptor.pop('headInspectionVersion')
    task.checkpoint['verifiedImageInspection']['context']=inspection_context(source,targets)
    received=carry(values)
    assert received['characters']==[] and [p['id'] for p in received['exits']]==['home']
    values[2].descriptor['exitInspectionVersion']=1
    assert carry(values)=={'characters':[],'exits':[]}


def test_concurrent_parent_claims_refresh_the_locked_parent_before_creating_children(client,scene_world):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.scenes.models import SceneImage
    from app.scenes.pipeline import candidate
    from app.scenes.worker import process_one
    parent=request(client,scene_world)
    with SessionLocal() as db:
        row=db.get(SceneImage,parent['id']);row.descriptor={**row.descriptor,'renderPipeline':1};db.commit()
    barrier=Barrier(2)
    def observed(db,**kwargs):
        result=candidate(db,**kwargs);assert result.id==parent['id'];barrier.wait(timeout=8);return result
    with patch('app.scenes.pipeline.candidate',side_effect=observed),patch('app.scenes.worker.generate') as supplier,ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(process_one) for _ in range(2)]
        assert all(f.result(timeout=12) for f in futures)
        supplier.assert_not_called()
    with SessionLocal() as db:
        children=list(db.scalars(select(SceneImage).where(SceneImage.descriptor['pipelineParentId'].astext==parent['id'])))
        assert len(children)==1
        assert db.get(SceneImage,parent['id']).image['pipelineStageId']==children[0].id


def test_full_environment_rebuild_discards_every_local_edit_contract():
    from app.scenes.pipeline import rebuild_environment
    source={'kind':'scene-environment-edit','renderStage':'environment-edit',
        'environmentEdit':'clear-floor','floorRepairPlan':{'sourceHash':'old'},
        'environmentCameraIndex':4,'environmentCameraPitchVersion':1,
        'remainingRoomEdits':[{}],'remainingRouteEdits':[{}],'remainingEditCameras':[1],
        'environmentRoutePlan':{},'routeRepairGuideVersion':5,'protectedPassages':[{}],
        'environmentCleaned':True,'visualFacts':{'version':1},'futureCast':[{'entityId':'npc'}]}
    original=deepcopy(source);result=rebuild_environment(source,'last-received')
    assert source==original
    assert result=={'kind':'scene-environment','renderStage':'environment','inputSceneId':'last-received',
        'rebuildEnvironmentGuide':True,'visualFacts':{'version':1},'futureCast':[{'entityId':'npc'}]}


def test_old_tiny_placement_receipt_cannot_authorize_current_readable_placement():
    from types import SimpleNamespace
    from app.scenes.binding import inspection_context
    old=SimpleNamespace(descriptor={'futureCast':[{'entityId':'npc'}]},image={})
    new=SimpleNamespace(descriptor={**old.descriptor,'readablePlacementVersion':1},image={})
    assert inspection_context(old,{})!=inspection_context(new,{})


def test_cleanup_uses_only_independently_observed_camera_and_falls_back_if_unproven():
    from app.scenes.pipeline import cleanup_views
    proof={'version':1,'imageHash':'actual','views':[{'viewIndex':index,'observation':{
        'count':1 if index==2 else 0,'heads':[{'box':[200,200,250,260],
        'evidence':'A distinct physical head and torso are visible outside the window.'}] if index==2 else [],
        'confidence':.96,'evidence':'These actual room pixels contain the observed physical figures only.'}} for index in range(4)]}
    assert cleanup_views({'sha256':'actual','peopleInventory':proof})==[2]
    assert cleanup_views({'sha256':'other','peopleInventory':proof})==[0,1,2,3]
    proof['views'][2]['observation']['confidence']=.6
    assert cleanup_views({'sha256':'actual','peopleInventory':proof})==[0,1,2,3]


def test_story_contradiction_prioritizes_fresh_environment_over_unrelated_local_edits(client,scene_world,profile):
    from app.db import SessionLocal
    from app.models import Task
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding
    from app.scenes.worker import process_one
    from .test_scene_pipeline import start
    from .test_scenes import generated
    parent,base=start(client,scene_world)
    with patch('app.scenes.worker.generate',return_value=generated()):assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,base)
        with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,child)
        task=db.get(Task,child.image['bindingTaskId']);task.state='succeeded';task.provider_state='completed'
        child.descriptor={**child.descriptor,'environmentGeometryVersion':6}
        child.image={**child.image,'anchorStatus':'unavailable','composition':{
            'verified':True,'issues':['visual-fact-mismatch','unexpected-people']}}
        db.commit()
    assert process_one()
    with SessionLocal() as db:
        child=db.get(SceneImage,db.get(SceneImage,parent).image['pipelineStageId'])
        assert child.descriptor['renderStage']=='environment' and child.descriptor['rebuildEnvironmentGuide']
        assert 'environmentEdit' not in child.descriptor
        assert child.descriptor['visualStageRepairs']==1 and child.status=='queued'


@pytest.mark.parametrize('extra_exit',[False,True])
def test_real_metered_preflight_runs_independent_observations_concurrently(client,scene_world,profile,extra_exit):
    from threading import Lock
    from app.db import SessionLocal
    from app.models import ModelProfile
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene
    from .test_scene_progressive import prepared_preview
    _,child=prepared_preview(client,scene_world);barrier=Barrier(4);lock=Lock();calls=[]
    with patch('app.scenes.binding.inspection_targets',return_value={'characters':[],'fixtures':[],'exits':[]}):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child);row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'peopleInventoryVersion':1,'sceneReviewVersion':1,'inspectionPerformanceVersion':1}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();tid=row.image['bindingTaskId']
        def received(model,prompt,data,limit):
            with lock:calls.append(data.get('inspectionMode'));ordinal=len(calls)
            if ordinal<=4:barrier.wait(timeout=8)
            mode=data.get('inspectionMode')
            if mode=='single-panorama-composition-v1':value=observation()
            elif mode=='single-camera-physical-head-inventory':
                value={'count':0,'heads':[],'confidence':.98,'evidence':'No physical people in these original room pixels.'}
            else:value={'characters':[],'fixtures':[],'exits':[{'id':'exit-main-door','visible':True,'passable':False}] if extra_exit else []}
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):bind_scene(tid)
    assert len(calls)==6 and calls.count('single-camera-physical-head-inventory')==4
    with SessionLocal() as db:
        row=db.get(SceneImage,child);assert row.image['anchorStatus']=='complete'


def test_tiny_distant_initial_slot_is_rejected_before_painting_or_support_call():
    from .test_scene_placement_review import camera,placement
    candidate=placement();candidate['bodyBoxPixels']=[180,250,220,355];candidate['groundPointPixels']=[200,355]
    stages=[]
    def call(stage,prompt,data,limit):
        stages.append(stage)
        assert '180 pixels' in data['readableCharacterContract']
        return {'text':'{"placements":[]}'}
    result=inspect_placements({'placements':[candidate]},[{'entityId':'npc'}],[camera()]*8,'',call,1000,
        independent=True,verify_support=True,verify_initial=True)
    assert result['placements']==[]
    assert len(stages)==4 and all(s.startswith('analysis:placement:') for s in stages)


@pytest.mark.parametrize('outcome',['clear','low-confidence-clear','artifact','malformed','unknown'])
def test_global_wall_art_recheck_is_bounded_and_never_hides_bad_or_unknown_receipts(client,scene_world,profile,outcome):
    from app.db import SessionLocal
    from app.models import ModelProfile,Task
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene
    from app.world.models import WorldLine
    from .test_scene_progressive import prepared_preview
    _,child=prepared_preview(client,scene_world);calls=[]
    with patch('app.scenes.binding.inspection_targets',return_value={'characters':[],'fixtures':[],'exits':[]}):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child);row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'peopleInventoryVersion':1,'sceneReviewVersion':1,'inspectionPerformanceVersion':1}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();tid=row.image['bindingTaskId'];digest=row.image['sha256']
        def received(model,prompt,data,limit):
            mode=data.get('inspectionMode');calls.append(mode)
            value=observation()
            if mode=='single-panorama-composition-v1':
                value['referenceArtifacts']=['Framed wall oil painting.']
                if outcome=='low-confidence-clear':value['confidence']=.8
            elif mode=='single-panorama-artifact-conflict-v1':
                if outcome=='unknown':raise Problem('PROVIDER_STATUS_UNKNOWN','Unknown paid response',502)
                if outcome=='artifact':value['referenceArtifacts']=['Actual pasted reference sheet with labels.']
                elif outcome=='malformed':value={}
            elif mode=='single-camera-physical-head-inventory':
                value={'count':0,'heads':[],'confidence':.98,'evidence':'No actual physical person in these room pixels.'}
            elif mode is None:value={'characters':[],'fixtures':[],'exits':[]}
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):
            if outcome=='unknown':
                with pytest.raises(Problem):bind_scene(tid)
            else:bind_scene(tid)
    assert calls.count('single-panorama-artifact-conflict-v1')==1
    with SessionLocal() as db:
        row=db.get(SceneImage,child)
        assert row.image['sha256']==digest
        assert db.get(WorldLine,scene_world['line']).head_checkpoint_id==scene_world['cp']
        if outcome in ('clear','low-confidence-clear'):
            assert not row.image['composition']['issues']
            assert calls.count('single-camera-physical-head-inventory')==4
            assert row.image['globalArtifactRecheck']['original']['referenceArtifacts']
        else:assert row.image.get('anchorStatus')!='complete'
