"""Real regressions: uncertain lighting, repeated layout scans, public attire."""
from copy import deepcopy
import json
from types import SimpleNamespace as N
from unittest.mock import patch
import pytest
from app.errors import Problem
from app.scenes.global_evidence import request as detail_request,apply as apply_detail,needs_detail
from app.scenes.public_appearance import excerpts,augment
from .test_scene_progressive import observation,prepared_preview
from .test_scenes import scene_world,API


@pytest.mark.parametrize('change',['daylight','night','uncertain','extra','fact','malformed'])
def test_global_detail_never_overrides_lighting_counts_facts_or_missing_measurements(change):
    original={**observation(),'lighting':'uncertain','exteriorWeather':'clear'};before=deepcopy(original)
    value={**observation(),'lighting':'daylight','exteriorWeather':'clear'}
    scene={'spaceKind':'interior','lighting':'清晨','lightingInspectionVersion':1}
    if change in ('night','uncertain'):value['lighting']=change
    if change=='extra':value.update(observation(1,['unknown']))
    if change=='fact':
        scene['visualFacts']={'requirements':[{'id':'no-live-goose'}]}
        value['visualFactChecks']=[{'id':'no-live-goose','consistent':False,'confidence':.98,'evidence':'A physical living goose stands beside the chair.'}]
    if change=='malformed':value.pop('cameraCovered')
    actual,verdict,proof=apply_detail(original,{'text':json.dumps(value)},[],scene,'a'*64)
    assert not verdict['issues'] if change=='daylight' else verdict['issues']
    if change in ('uncertain','malformed'):assert verdict['verified'] is False
    assert original==before and proof['original']==before and proof['imageHash']=='a'*64


def test_global_detail_uses_actual_same_image_cameras_without_a_requested_positive_answer():
    payload=detail_request(b'actual-panorama',{'sceneCast':[]},[b'camera-'+bytes([i]) for i in range(4)])
    assert len(payload['_sceneImages'])==5
    assert 'previousVerdict' not in payload and 'expectedCount' not in payload
    assert 'only Image 1 owns the projection judgement' in payload['instruction']
    assert needs_detail({'verified':False,'issues':['scene-review-unconfirmed']})
    assert not needs_detail({'verified':False,'issues':['scene-review-unconfirmed','visual-fact-mismatch']})
    assert not needs_detail({'verified':True,'issues':['unexpected-people']})


def test_public_appearance_keeps_current_clothes_and_foot_object_with_the_named_person():
    text='闻青。采药人。他穿着洗得发旧的青灰色短褐，袖口露出一截结实的小臂。一只竹编药篓搁在他脚边，系着绳，没有打开过。\n\n你摸了摸怀里的旧铜路牌。'
    people=[{'id':'npc','name':'闻青'},{'id':'other','name':'唐禾'}]
    value=excerpts(text,people)
    assert set(value)=={'npc'} and '青灰色短褐' in value['npc'] and '药篓' in value['npc']
    assert '铜路牌' not in value['npc']
    person={'entityId':'npc','description':'熟悉山道的采药人'}
    assert augment({'publicAppearanceContext':value},person)==person['description']
    assert '药篓' in augment({'publicAppearanceContextVersion':1,'publicAppearanceContext':value},person)


def test_distinct_named_sentences_in_one_paragraph_keep_their_own_current_props():
    value=excerpts('你推开门，脱下大衣。福尔摩斯陷在扶手椅里，手里举着一顶破毡帽。门房彼得森穿着制服。',
        [{'id':'h','name':'福尔摩斯'},{'id':'p','name':'彼得森'}])
    assert '毡帽' in value['h'] and '制服' not in value['h'] and '脱下大衣' not in value['h']
    assert '制服' in value['p'] and '毡帽' not in value['p']


@pytest.mark.parametrize('text',[
    '闻青说：“唐禾穿着蓝外套。”',
    '闻青说：“我父亲穿着蓝外套。”',
    '闻青回忆从前穿着蓝外套。',
    '闻青打算明天穿着蓝外套。',
    '闻青和唐禾站在屋内。他穿着蓝外套。',
])
def test_reported_historical_future_and_ambiguous_clothes_are_not_assigned(text):
    assert excerpts(text,[{'id':'a','name':'闻青'},{'id':'b','name':'唐禾'}])=={}


@pytest.mark.parametrize('outcome',['daylight','still-uncertain','extra','unknown'])
def test_queued_global_detail_is_bounded_metered_and_does_not_change_the_story(client,scene_world,profile,outcome):
    from app.db import SessionLocal
    from app.models import Task,ModelProfile,UsageRecord
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene
    from sqlalchemy import select
    w=scene_world;_,child=prepared_preview(client,w);before=client.get(API+w['root']).json()
    targets={'fixtures':[],'exits':[]};detail_calls=[]
    with patch('app.scenes.binding.inspection_targets',return_value=targets):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child)
            row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'globalEvidenceRecoveryVersion':1,'lightingInspectionVersion':1,'lighting':'清晨'}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();tid=row.image['bindingTaskId'];image_hash=row.image['sha256']
        def received(model,prompt,data,limit):
            mode=data.get('inspectionMode')
            if mode=='single-panorama-composition-v1':value={**observation(),'lighting':'uncertain','exteriorWeather':'clear'}
            elif mode=='single-panorama-context-detail-v1':
                detail_calls.append(data)
                if outcome=='unknown':raise Problem('PROVIDER_STATUS_UNKNOWN','Unresolved fresh review')
                value={**(observation(1,[None]) if outcome=='extra' else observation()),
                    'lighting':'uncertain' if outcome=='still-uncertain' else 'daylight','exteriorWeather':'clear'}
            else:value={'characters':[],'fixtures':[],'exits':[],'placements':[]}
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):
            if outcome=='unknown':
                with pytest.raises(Problem) as error:bind_scene(tid)
                assert error.value.code=='PROVIDER_STATUS_UNKNOWN'
            else:bind_scene(tid)
        assert len(detail_calls)==1 and len(detail_calls[0]['_sceneImages'])==5
        with SessionLocal() as db:
            row=db.get(SceneImage,child);task=db.get(Task,tid)
            assert row.image['sha256']==image_hash
            usages=list(db.scalars(select(UsageRecord).where(UsageRecord.task_id==tid)))
            assert sum(u.operation=='analysis:scene-review-detail' for u in usages)==1
            if outcome!='unknown':
                assert row.image['anchorStatus']==('complete' if outcome=='daylight' else 'unavailable' if outcome=='still-uncertain' else 'incomplete')
                assert row.image['globalEvidenceRecheck']['original']['lighting']=='uncertain'
                assert all(u.status=='completed' for u in usages)
    assert client.get(API+w['root']).json()==before


@pytest.mark.parametrize('floor',['confirmed','contradicted','unknown'])
def test_hint_first_route_still_requires_independent_opening_and_floor_and_stops_only_when_proven(client,scene_world,profile,floor):
    from app.db import SessionLocal
    from app.models import ModelProfile
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene
    from .test_scene_apertures import TARGET,measured_answer
    w=scene_world;_,child=prepared_preview(client,w);before=client.get(API+w['root']).json();routes=[];floors=[]
    target={**TARGET,'name':'courtyard'}
    with patch('app.scenes.binding.inspection_targets',return_value={'fixtures':[],'exits':[target]}):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child)
            row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'measurementEfficiencyVersion':1,'inspectionPerformanceVersion':1,
                'apertureInspectionVersion':1,'surfaceContextVersion':0,'aperturePointVersion':0}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();tid=row.image['bindingTaskId'];image_hash=row.image['sha256']
        def received(model,prompt,data,limit):
            mode=data.get('inspectionMode')
            if mode=='single-panorama-composition-v1':value=observation()
            elif mode=='native-aperture':routes.append(data);value=measured_answer()
            elif mode=='blind-surface-points':
                floors.append(data)
                if floor=='unknown':raise Problem('PROVIDER_STATUS_UNKNOWN','Unresolved actual floor check')
                value={key:floor=='confirmed' for key in ('sameOpening','crossedThreshold','openingConfirmed','unobstructed')}
                value['confidence']=.96
                for label in ('A','B','C'):value[label]={'surface':'walkable-ground' if floor=='confirmed' else 'wall','confidence':.96,'evidence':'The actual marked point is independently observed on this physical surface.'}
            else:value={'characters':[],'fixtures':[],'placements':[],
                'exits':[{'id':'yard','visible':True,'passable':False,'viewIndex':6,
                    'passageEvidence':{'viewIndex':6,'kind':'door-open'}}]}
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):
            if floor=='unknown':
                with pytest.raises(Problem) as error:bind_scene(tid)
                assert error.value.code=='PROVIDER_STATUS_UNKNOWN'
            else:bind_scene(tid)
        assert floors and routes
        if floor in ('confirmed','unknown'):assert len(routes)==1
        else:assert len(routes)>1
        with SessionLocal() as db:
            row=db.get(SceneImage,child);assert row.image['sha256']==image_hash
            assert (row.image.get('anchorStatus')=='complete')==(floor=='confirmed')
    assert client.get(API+w['root']).json()==before


@pytest.mark.parametrize('version',[0,1])
def test_exhausted_complete_layout_search_does_not_repeat_the_same_pixels_in_new_scenes(client,scene_world,profile,version):
    from app.db import SessionLocal
    from app.models import Task,ModelProfile
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene
    from sqlalchemy import select,func
    w=scene_world;_,child=prepared_preview(client,w);before=client.get(API+w['root']).json()
    targets={'fixtures':[],'exits':[]};contracts=[]
    with patch('app.scenes.binding.inspection_targets',return_value=targets):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child)
            row.descriptor={**row.descriptor,'sceneCast':[],
                'futureCast':[{'entityId':'planned-person','name':'待入画人物','description':'An adult traveller'}],
                'coordinateInspectionVersion':1,'measurementEfficiencyVersion':version,
                'inspectionPerformanceVersion':1,'inspectionMaxCalls':32}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();tid=row.image['bindingTaskId'];image_hash=row.image['sha256']
        def received(model,prompt,data,limit):
            if data.get('placementContract'):contracts.append(deepcopy(data['placementContract']))
            value=(observation() if data.get('inspectionMode')=='single-panorama-composition-v1' else
                {'coordinateSystem':'normalized-1000-xy-v1','characters':[],'fixtures':[],'exits':[],'placements':[]})
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):bind_scene(tid)
        assert contracts
        if version:assert '51..949' in contracts[0]['bodyBoxPixels'] and '0..7' in contracts[0]['viewIndex']
        else:assert '26..486' in contracts[0]['bodyBoxPixels']
        with SessionLocal() as db:
            row=db.get(SceneImage,child);assert row.image['placementEvidenceExhausted'] is True
            assert row.image['anchorStatus']=='unavailable' and row.image['sha256']==image_hash
            count=db.scalar(select(func.count()).select_from(Task))
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.flush()
            assert db.scalar(select(func.count()).select_from(Task))==count+(0 if version else 1)
            assert (row.image['bindingTaskId']==tid)==bool(version)
            db.commit()
    assert client.get(API+w['root']).json()==before
