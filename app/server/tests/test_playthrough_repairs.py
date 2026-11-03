import json
import os
from pathlib import Path
from copy import deepcopy
import pytest
from app.world.context_budget import fit_context
from app.context_wire import wire_json
from app.scenes.interaction import normalize_plan
from app.scenes.binding import anchors_from_result
from app.scenes.composition import character_visibility, character_presentations
from app.scenes.layout import space_kind
from app.scenes.dialogue import scene_segments
from app.world.state import apply_plan


FIXTURES = Path(os.environ.get('PLAYTHROUGH_FIXTURE_DIR', '/qa'))
def cases():return json.loads((FIXTURES / 'regressions.json').read_text('utf8'))


def test_real_long_save_request_fits_without_losing_ledger_rules_or_obligations():
    data=cases()[2]['context'];prior=deepcopy(data)
    fitted=fit_context(data,30000)
    assert len(wire_json(fitted).encode())<=30500
    for key in ('rules','entityState','queuedEvents','action','actionRules','criticalFacts','placeRegistry'):
        assert fitted[key]==prior[key]
    assert data==prior
    data['recentScenes']=[{'text':'旧事'*2000} for _ in range(100)]
    assert len(wire_json(fit_context(data,30000)).encode())<=30500


def test_real_sixteenth_round_fits_with_director_prompt_and_preserves_authoritative_state():
    data=json.loads((FIXTURES / 'context-playthrough.json').read_text('utf8'));prior=deepcopy(data)
    fitted=fit_context(data,30000)
    from app.worker import PROMPTS
    prompt=(PROMPTS/'world_director.txt').read_text('utf8')
    from types import SimpleNamespace
    from app.completion_policy import input_ceiling
    assert len(wire_json(fitted).encode())+len(prompt.encode())+256<=input_ceiling(SimpleNamespace(context_window=1048576),2048,world=True)
    for key in ('rules','entityState','criticalFacts','action','actionRules','placeRegistry'):
        assert fitted[key]==prior[key]
    assert 'observed' not in fitted['state'] and data==prior


def test_review_wrapper_bounds_old_narration_without_removing_current_result_or_ledger():
    data={'scene':{'recentText':'旧正文'*10000,'conversation':[{'reply':'旧对话'*10000}],'outcome':'本轮实际交付。','rules':{'genre':'fantasy'}},'ledger':{'inventory':{'receipt':1}},'text':'本轮正文。'}
    fitted=fit_context(data,30000)
    assert len(wire_json(fitted).encode())<30000
    assert fitted['text']==data['text'] and fitted['ledger']==data['ledger']
    assert fitted['scene']['outcome']==data['scene']['outcome'] and fitted['scene']['rules']==data['scene']['rules']


def test_consecutive_chapter_prose_copy_cannot_displace_pending_decision_or_ledger():
    data={'lastCommittedTurn':{'summary':'约定到期，选择尚未完成。','text':'此前的可检索正文。'*2000},
        'state':{'queue':[{'id':'decision-1','requiresDecision':True}], 'inventory':{'letter':1}},
        'rules':{'noAutomaticDecisions':True},'action':'在需要选择时停下。'}
    before=deepcopy(data);fitted=fit_context(data,1200)
    assert len(wire_json(fitted).encode())<=1200
    assert fitted['lastCommittedTurn']=={'summary':data['lastCommittedTurn']['summary'],'text':''}
    for key in ('state','rules','action'):assert fitted[key]==data[key]
    assert data==before


@pytest.mark.parametrize('index',[0,1])
def test_real_failed_journey_and_payment_apply_atomically(index):
    case=cases()[index];original=deepcopy(case['plan'])
    plan=normalize_plan(case['plan'],case['state'],case['definition'])
    state,definition,changes,events=apply_plan(case['state'],case['definition'],plan,'test-fix',case['context']['source'],action_kind='action')
    assert case['plan']==original
    if index==0:
        assert next(e['name'] for e in definition['entities'] if e['id']==state['location'])=='萧家后山'
    else:
        payer=state['playerId'];seller=plan['resources'][0]['counterpartyId']
        assert state['entities'][payer]['fields']['金币']==60
        assert state['entities'][seller]['fields']['金币']==case['state']['entities'][seller]['fields']['金币']+240
        assert len(plan['resources'])==1
        assert sum(t['quantity'] for t in plan['transfers'])==6


def test_visual_binding_uses_received_pixels_and_never_guessed_composition():
    from types import SimpleNamespace
    cast=[{'entityId':'a'}]
    result={'characters':[{'entityId':'a','visible':True,'confidence':.95,'headBox':[.3,.495,.315,.52]}, {'entityId':'foreign','visible':True,'confidence':1,'headBox':[.7,.4,.8,.5]}]}
    anchors=anchors_from_result(result,cast);assert set(anchors)=={'a'}
    oversized=deepcopy(result);oversized['characters'][0]['headBox']=[.3,.4,.35,.47]
    assert anchors_from_result(oversized,cast)=={}
    view={'characters':[{'id':'a'}],'characterVisibility':{'a':'visible'}}
    row=SimpleNamespace(status='ready',descriptor={'sceneCast':[{'entityId':'a','yaw':0,'pitch':0}]},image={})
    assert character_presentations(row,view)['a']['mode']=='offscreen'
    from app.scenes.grounding import VERSION
    row.image={'sha256':'received-fixture-image','bindingImageHash':'received-fixture-image','anchorVersion':VERSION,'characterAnchors':anchors}
    assert character_presentations(row,view)['a']['yaw']==anchors['a']['yaw']
    row.image['bindingImageHash']='different-image'
    assert character_presentations(row,view)['a']['mode']=='offscreen'
    view['characterVisibility']['a']='voice'
    assert character_presentations(row,view)['a']=={'mode':'voice'}


def test_outdoor_and_spirit_visibility_semantics():
    for name in ('萧家练武场','乌坦城街道','萧家后山','青山镇','魔兽山脉外围'):
        assert space_kind({'name':name})!='interior'
    spirit={'name':'药老','description':'纳戒中寄居的灵魂，偶尔显出投影'}
    assert character_visibility(spirit,'药老的声音从纳戒传来。')=='voice'
    assert character_visibility(spirit,'药老现出投影，站在一旁。')=='visible'
    assert character_visibility(spirit,'药老的投影浮在药鼎旁侧，袖手而立。')=='visible'
    assert character_visibility(spirit,'药老并未显出投影。')=='voice'


def test_structured_multiple_speakers_and_actions_keep_exact_ownership():
    text='【旁白】林掌柜抬头。【台词：merchant】小少爷要什么？【旁白】药老顿了顿。【台词：spirit】先看看药材。'
    parts=scene_segments(text,[{'id':'merchant','name':'林掌柜'},{'id':'spirit','name':'药老'}])
    assert [p['kind'] for p in parts]==['narration','speech','narration','speech']
    assert [p['speakerId'] for p in parts if p['kind']=='speech']==['merchant','spirit']
    assert all('顿了顿' not in p['text'] for p in parts if p['kind']=='speech')


def test_real_attached_spirit_movement_does_not_assign_the_spirit_update_to_its_ring():
    case=cases()[3]
    plan=normalize_plan(case['plan'],case['state'],case['definition'])
    drug=next(e['id'] for e in case['definition']['entities'] if e['name']=='药老')
    update=next(u for u in plan['updates'] if u['reason'].startswith('药老依附'))
    assert update['entityId']==drug
    assert update['value']==plan['location']


def test_same_committed_batch_finishes_without_consuming_ingredients_twice():
    from types import SimpleNamespace
    from app.scenes.trades import continue_consumed_work
    case=json.loads((FIXTURES / 'craft-playthrough.json').read_text('utf8'))
    parent=SimpleNamespace(state=case['state'],changes=case['changes'])
    plan=normalize_plan(case['plan'],case['state'],case['definition'])
    plan=continue_consumed_work(plan,parent,case['action'])
    state,definition,_,_=apply_plan(case['state'],case['definition'],plan,'crafted',[],action_kind='dialogue')
    player=state['entities'][state['playerId']]
    elixir=next(e for e in definition['entities'] if e['name']=='筑基灵液')
    assert player['inventory'][elixir['id']]==1 and player['fields']['炼药经验']==1
    assert all(player['inventory'][key]==0 for key in ('57c14c367ccf5a51b9fcc8a695bdc312','58d1fe5ea4955e169fdcfdb0a08cf36a','25fb38f686b2528392702a7a12d01345'))
    assert continue_consumed_work(deepcopy(case['plan']),parent,'另起一炉再炼一炉')['transfers']==case['plan']['transfers']


def test_real_actor_directions_are_neutral_even_when_provider_uses_a_speech_tag():
    from app.scenes.dialogue import separate_stage_directions
    text='他摆了摆手，目光落回摊开的货单。厅外日头正盛，木梁的影子挪过桌角。'
    assert separate_stage_directions([{'kind':'speech','speakerName':'萧战','text':text}])==[{'kind':'narration','text':text}]
    assert character_visibility({'name':'药老','description':'纳戒里的灵魂'},'药老指点你收火。',default='visible')=='visible'


def test_distant_quoted_roads_do_not_create_a_local_viewpoint():
    from app.scenes.layout import build_layout
    place={'id':'hill','name':'后山','description':'松林围绕石台。'}
    layout=build_layout(place,[],'药老说：“明天去青山镇，街道通往外围山道。”')
    assert len(layout['nodes'])==1
    assert len(build_layout(place,[],'一条山道沿石台通向山下。')['nodes'])==2


def test_drug_master_visual_identity_has_explicit_age_gender_and_beard():
    from app.scenes.characters import character_descriptor
    art=character_descriptor({'id':'spirit','name':'药老','description':'纳戒中的炼药师灵魂'},'斗破苍穹')
    assert 'OLD MALE' in art['appearance'] and 'LONG WHITE BEARD' in art['appearance']
    assert character_visibility({'name':'药老','description':'纳戒里的灵魂'},'药老的投影显了出来，袖手立在石上。')=='visible'


def test_detailed_panorama_inspection_always_fits_its_text_and_image_budget():
    import io,random
    from PIL import Image
    from app.scenes.binding import inspection_image
    source=Image.frombytes('RGB',(1536,768),random.Random(42).randbytes(1536*768*3))
    raw=io.BytesIO();source.save(raw,format='PNG')
    data,size=inspection_image(raw.getvalue())
    assert len(data)<=240000 and size[0]>=1024
    with Image.open(io.BytesIO(data)) as received:assert received.size==size


def test_actual_round24_keeps_mandatory_facts_inside_finite_model_window():
    from types import SimpleNamespace
    from app.completion_policy import input_ceiling
    from app.worker import PROMPTS
    data=json.loads((FIXTURES / 'context-round24.json').read_text('utf8'));fitted=fit_context(data,30000)
    size=len(wire_json(fitted).encode())+len((PROMPTS/'world_director.txt').read_bytes())+256
    assert 48000<size<=input_ceiling(SimpleNamespace(context_window=1048576),2048,world=True)<=96000
    for key in ('rules','entityState','criticalFacts','placeRegistry','action'):
        assert fitted[key]==data[key]
    assert fitted['state']['unresolved']==data['state']['unresolved']


def test_committed_spirit_arrival_reason_authorizes_projection_but_negation_does_not():
    spirit={'name':'药老','description':'寄居纳戒的灵魂'}
    assert character_visibility(spirit,'药老依附纳戒随萧炎返回后山，投影在石台显形。')=='visible'
    assert character_visibility(spirit,'药老的投影没有显形。')=='voice'


def test_inline_pointing_action_is_not_a_spoken_utterance():
    from app.scenes.dialogue import separate_stage_directions
    parts=separate_stage_directions([{'kind':'speech','speakerName':'药老','text':'第三。药老指了指你腰间纳戒，那瓶灵液一次没碰。'}])
    assert [p['kind'] for p in parts]==['speech','narration','speech']
    assert parts[1]['text']=='药老指了指你腰间纳戒，'
