from copy import deepcopy
from unittest.mock import patch
import asyncio,json
import httpx
import pytest
from app.errors import Problem
from app.scenes import admission
from app.scenes.candidate_scope import scope
from app.scenes.people_inventory import request,evaluate_facts
from app.scenes.facts import visual_facts
from app.scenes.binding import composition_from_result
from .test_scene_people_inventory import proof
from .test_scene_heads import camera
from .test_scene_progressive import observation


@pytest.fixture
def paced(monkeypatch):
    clock=[100.0];waits=[]
    monkeypatch.setattr(admission.settings,'ai_mode','real')
    monkeypatch.setattr(admission.settings,'scene_vision_start_interval_seconds',1.25)
    monkeypatch.setattr(admission,'monotonic',lambda:clock[0])
    monkeypatch.setattr(admission,'_next_start',0.0);monkeypatch.setattr(admission,'_cooldowns',{})
    def wait(timeout):waits.append(timeout);clock[0]+=timeout
    monkeypatch.setattr(admission._condition,'wait',wait)
    assert not admission._waiting
    yield clock,waits
    assert not admission._waiting


def test_pacing_and_received_rate_limit_delay_new_intents_without_replaying_the_failed_call(paced):
    clock,waits=paced;sent=[]
    admission.wait_for_start(lambda:None);sent.append(clock[0])
    admission.wait_for_start(lambda:None);sent.append(clock[0])
    assert 1.25<=sent[1]-sent[0]<=1.36
    error=Problem('PROVIDER_FAILED','busy',502,{'httpStatus':429,'retryAfterSeconds':60})
    admission.observe_failure(error)
    assert len(sent)==2
    with pytest.raises(Problem) as failure:admission.wait_for_start(lambda:None)
    assert failure.value.code=='SCENE_VISION_COOLDOWN' and len(sent)==2
    assert failure.value.details['retryAfterSeconds']>=59
    assert max(waits)<=.1


def test_unknown_response_never_becomes_a_new_or_replayed_intent(paced):
    clock,_=paced
    admission.observe_failure(Problem('PROVIDER_STATUS_UNKNOWN','timeout',502,{'reason':'timeout'}))
    assert not admission._cooldowns
    assert admission._next_start==0 and clock[0]==100


def test_cancelling_a_waiter_removes_its_ticket_without_sending_or_blocking_later_work(paced):
    clock,_=paced
    admission.observe_failure(Problem('PROVIDER_FAILED','busy',502,{'httpStatus':429}))
    checks=[]
    def cancelled():checks.append(1);raise Problem('TASK_CANCELLED','stopped',409)
    with pytest.raises(Problem,match='stopped'):admission.wait_for_start(cancelled)
    assert checks==[1] and not admission._waiting and admission._next_start==0
    with pytest.raises(Problem) as failure:admission.wait_for_start(lambda:None)
    assert failure.value.code=='SCENE_VISION_COOLDOWN' and clock[0]==100
    admission._cooldowns.clear();admission.wait_for_start(lambda:None)


@pytest.mark.parametrize('status,category',[(429,'rate_limit'),(402,'quota'),(400,'unspecified')])
def test_plain_supplier_rejections_have_fixed_categories_and_bounded_retry_after(status,category):
    from app.provider_rejection import diagnostics
    response=httpx.Response(status,content=b'non-json rejection',headers={'retry-after':'90'})
    result=asyncio.run(diagnostics(response,credential='private-key'))
    assert result['rejectionCategory']==category
    assert result.get('retryAfterSeconds')==(90 if status==429 else None)
    response=httpx.Response(429,content=b'',headers={'retry-after':'9'*7000})
    assert 'retryAfterSeconds' not in asyncio.run(diagnostics(response))


def test_coarse_unknown_hints_cannot_create_a_target_or_erase_a_known_or_malformed_proposal():
    original={'characters':[{'entityId':'stranger','visible':True}],
        'fixtures':[{'id':'known','visible':True,'confidence':.1},{'id':'invented'},{'bad':'missing-id'}],
        'exits':[{'id':'exit-main-door','visible':True,'passable':False}]}
    unchanged=deepcopy(original)
    result=scope(original,{'fixtures':[{'id':'known'}],'exits':[{'id':'required-real-exit'}]})
    assert original==unchanged and result['characters']==original['characters']
    assert result['fixtures']==[original['fixtures'][0],original['fixtures'][2]]
    assert result['exits']==[] and {p['id'] for p in result['ignoredCoarseHints']}=={'invented','exit-main-door'}


def facts_and_inventory():
    facts=visual_facts(None,{'entities':[]},[],'彼得森的妻子剖开鹅嗉。')
    inventory=proof()
    for view in inventory['views']:
        view['observation']['visualFactChecks']=[{'id':'no-live-goose','consistent':True,'confidence':.96,
            'evidence':'The actual camera shows no living or intact food animal.'}]
    return facts,inventory


@pytest.mark.parametrize('text,processed',[
    ('我妻子把鹅嗉剖开，就在里面发现了这东西。',True),
    ('她把那只鹅的嗉囊剖开，里面滚出了一颗石头。',True),
    ('她准备把鹅嗉剖开。',False),('如果把鹅嗉剖开，也许能发现东西。',False),
    ('她没有把鹅嗉剖开。',False),('请把鹅嗉剖开。',False),
    ('她计划把鹅嗉剖开。',False),('不要把鹅嗉剖开。',False),
    ('她要把鹅嗉剖开。',False),('她会把鹅嗉剖开。',False),
    ('她明天把鹅嗉剖开。',False),('她要宰杀鹅。',False),
])
def test_real_saved_disposal_wording_is_a_fact_but_intentions_and_negation_are_not(text,processed):
    result=visual_facts(None,{'entities':[]},[],text)
    assert bool(result)==processed
    if processed:assert result['requirements'][0]['id']=='no-live-goose' and result['cameraInspectionVersion']==1


def test_food_animal_fact_uses_the_existing_four_blind_cameras_without_leaking_cast_or_story_quotes():
    facts,inventory=facts_and_inventory();data=request(camera(),facts=facts)
    assert len(data['_sceneImages'])==1 and 'visualFactChecks' in data['outputContract']
    text=json.dumps({k:v for k,v in data.items() if k!='_sceneImages'},ensure_ascii=False)
    assert '彼得森' not in text and 'expectedPhysicalPeople' not in text
    assert evaluate_facts(inventory,facts,'a'*64)==[]


@pytest.mark.parametrize('defect',['contradiction','missing','low-confidence','stale'])
def test_one_bad_camera_cannot_be_overruled_by_a_positive_whole_panorama_fact_review(defect):
    facts,inventory=facts_and_inventory();check=inventory['views'][3]['observation']['visualFactChecks'][0]
    expected='visual-facts-unconfirmed'
    if defect=='contradiction':check.update(consistent=False,evidence='A living goose is physically standing on the actual walking floor.');expected='visual-fact-mismatch'
    elif defect=='missing':inventory['views'][3]['observation'].pop('visualFactChecks')
    elif defect=='low-confidence':check['confidence']=.5
    else:inventory['imageHash']='b'*64
    whole=observation();whole['visualFactChecks']=deepcopy(facts_and_inventory()[1]['views'][0]['observation']['visualFactChecks'])
    result=composition_from_result({'characters':[],'sceneReview':{'version':1,'imageHash':'a'*64,'observation':whole},
        'peopleInventory':inventory},[],{'sceneReviewVersion':1,'peopleInventoryVersion':1,'sceneImageHash':'a'*64,
            'spaceKind':'interior','visualFacts':facts})
    assert expected in result['issues']
