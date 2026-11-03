"""Actual ledger invariants behind the full-playthrough findings."""
import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from PIL import Image, ImageChops, ImageStat
from app.errors import Problem
from app.world.turn_contract import reference_wire, resolve_plan, travel_capability
from app.world.work import prepare_work
from app.scripts.plot import advance, public_progress
from app.scripts.service import blocks_for, groups_for
from app.scripts.schemas import ScriptInput
from app.scenes.dialogue import structured_segments
from app.scenes.trades import normalize_receipts
from app.scenes.provider import periodic_edges
from app.prose.chapter_number import chapter_title
from app.world.state import parse_json
from app.world.schemas import DirectorOutput


def test_departure_with_intervening_actions_hides_stale_roster_but_quoted_plans_do_not():
    from app.scenes.interaction import public_presence
    person={'name':'彼得森'}
    assert not public_presence(person,'彼得森应声把稿子收好，开门下楼，奔晚报编辑部而去。')
    assert public_presence(person,'彼得森说：“明天我开门下楼，去报社。”')
    assert public_presence(person,'昨天彼得森把稿子收好，开门下楼。')
    assert public_presence(person,'彼得森打算把稿子收好，开门下楼。')
    assert public_presence(person,'彼得森开门下楼。彼得森回到寓所。')


def test_scheduled_arrival_is_reported_to_narrator_once():
    state={'playerId':'p','location':'home','discovered':['home'],'entities':{'p':{},'guest':{'location':None}}}
    definition={'script':{'objectives':[],'endings':[],'castSchedule':[{'entityId':'guest','requires':[],'placeId':'home'}]}}
    changes=[];advance(state,definition,changes)
    assert state['entities']['guest']['location']=='home' and 'guest' in state['discovered']
    assert changes==[{'entityId':'guest','field':'location','before':None,'after':'home','reason':'调查推进后，此人已抵达当前地点；请在本轮正文呈现其到场，不能仍写成等待未来到来。','visibleTo':['p']}]
    changes=[];advance(state,definition,changes);assert changes==[]


def test_explicit_sharing_receipt_normalizes_only_existing_known_learned_ids():
    from app.world.knowledge import normalize_knowledge
    raw={'learned':['hat','future'], 'events':[{'type':'知识传授','entityId':'h','loreIds':[]}, {'type':'闲聊','entityId':'h','loreIds':[]}]}
    base={'entities':{'h':{'known':['hat']}}}
    result=normalize_knowledge(raw,base,{'lore':[]})
    assert result['events'][0]['type']=='knowledge_shared'
    assert result['events'][0]['loreIds']==['hat'] and result['events'][1]['loreIds']==[]
    assert raw['events'][0]['type']=='知识传授'


def test_script_clues_retrieve_across_source_languages_without_learning_automatically():
    from app.scripts.context import enrich_script
    lore={'id':'hat','title':'帽子','text':'从帽衬查明主人的情况','knownBy':['detective'],'evidence':[{'blockId':'S0001'}]}
    definition={'lore':[lore], 'script':{'objectives':[{'id':'investigate','requires':[], 'checks':[{'kind':'knowledge','loreId':'hat'}], 'sourceBlockIds':['S0001']}], 'endings':[]}}
    state={'playerId':'p','known':[], 'location':'home','entities':{'p':{'location':'home'},'detective':{'location':'home'}}}
    data={'lore':[]}
    enrich_script(data,definition,state,{'blocks':[{'blockId':'S0001','text':'An English hat clue.'},{'blockId':'S0002','text':'The final culprit confesses.'}]})
    assert data['lore']==[lore] and data['source']==[{'blockId':'S0001','text':'An English hat clue.'}]
    assert data['scriptGuide']['localClues'][0]['availableSources']==['detective']
    assert state['known']==[]


def test_portraits_and_panorama_share_world_era_in_both_reference_paths():
    from app.scenes.visual_style import visual_context
    from app.scenes.characters import character_prompt
    from app.scenes.provider import prompt_for
    world=SimpleNamespace(name='蓝宝石案',definition={'rules':{'genre':'mystery','style':'维多利亚时代伦敦'},'entities':[{'type':'place','name':'贝克街','description':'London, 1892'}]})
    descriptor={**visual_context(world),'worldName':world.name,'name':'福尔摩斯','description':'英国侦探','placeName':'贝克街','pointLabel':'入口','lighting':'morning','portraitLibrary':True}
    portrait=character_prompt(descriptor)
    assert 'London, 1892' in portrait and '维多利亚时代伦敦' in portrait
    assert 'Unified painterly Chinese fantasy' not in portrait
    scene=prompt_for({**descriptor,'characterReferenceOrder':[{'name':'福尔摩斯','image':2}],'sceneCast':[{'name':'福尔摩斯'}]})
    assert 'London, 1892' in scene and '国风幻想插画笔触' not in scene


def test_known_director_trailer_does_not_discard_a_complete_response():
    text=json.dumps({'title':'火候','elapsedMinutes':2,'timeLabel':'清晨','publicOutcome':'火候稳定。'})
    assert parse_json(text+'parameter',DirectorOutput)['title']=='火候'
    for suffix in ('{"second":1}','random prose'):
        with pytest.raises(Problem):parse_json(text+suffix,DirectorOutput)


def test_empty_provider_placeholder_is_ignored_but_nonempty_invalid_changes_still_fail():
    base={'title':'选择','elapsedMinutes':2,'timeLabel':'夜晚','publicOutcome':'你作出了决定。'}
    plan=parse_json(json.dumps({**base,'updates':[{'entityId':'p','field:':None,'value':None,'reason':None,'visibleTo':None}]}),DirectorOutput)
    assert plan['updates']==[] and plan['warnings']
    with pytest.raises(Problem):parse_json(json.dumps({**base,'updates':[{'entityId':'p','field:':None,'value':'已完成','reason':None}]}),DirectorOutput)


def test_world_output_caps_are_reserved_separately_from_reader_defaults():
    from app.completion_policy import completion_policy
    profile=SimpleNamespace(base_url='https://opencode.ai/zen/go/v1',model_id='mimo-v2.6-flash')
    assert completion_policy(profile)['stages']['analysis']['totalCompletionTokens']==2048
    policy=completion_policy(profile,world=True)
    assert policy['stages']['analysis']['totalCompletionTokens']==4096
    assert policy['stages']['generation']['totalCompletionTokens']==4096
    assert policy['stages']['analysis']['thinking']=='disabled'


def test_round_references_preserve_typed_identity_without_fuzzy_matching():
    definition={'entities':[{'id':'opaque-player','type':'character','name':'调查员'},{'id':'opaque-item','type':'item','name':'宝石'}],'lore':[{'id':'secret'}]}
    wire,reverse=reference_wire({'state':{'playerId':'opaque-player','entities':{'opaque-player':{'inventory':{'opaque-item':1}}}},'known':['secret']},definition)
    plan=resolve_plan({'transfers':[{'itemId':wire['registry'][1]['id'],'fromId':wire['state']['playerId']}],'additions':[{'id':'NEW_document'}]},reverse)
    assert plan['transfers'][0]=={'itemId':'opaque-item','fromId':'opaque-player'}
    assert len(plan['additions'][0]['id'])==32
    assert resolve_plan({'itemId':'E999'},reverse)['itemId']=='E999'
    result=resolve_plan({'additions':[{'id':'E999','type':'item'}],'transfers':[{'itemId':'E999'}]},reverse)
    assert len(result['additions'][0]['id'])==32 and result['transfers'][0]['itemId']==result['additions'][0]['id']


def test_malformed_optional_relationship_preserves_core_action_without_guessing():
    from app.scenes.interaction import normalize_plan
    definition={'entities':[{'id':'p','type':'character'},{'id':'npc','type':'character'}],'lore':[],'rules':{}}
    state={'playerId':'p','entities':{'p':{'fields':{'决定':''}},'npc':{}}}
    plan={'updates':[{'entityId':'npc','field':'relation:陈028','value':'畏惧'}, {'entityId':'p','field':'决定','value':'调查'}],'learned':[]}
    result=normalize_plan(plan,state,definition)
    assert result['updates']==[{'entityId':'p','field':'field:决定','value':'调查'}]
    assert result['warnings'] and len(plan['updates'])==2


def test_travel_cannot_spend_evidence_or_resolve_quests():
    result=travel_capability({'location':'b','updates':[{'field':'location'},{'field':'field:case_done'}],'transfers':[{'itemId':'gem'}],'learned':['culprit'],'events':[{'type':'arrival'},{'type':'quest_completed'}],'eventResolutions':[{'status':'fulfilled'}],'work':[]}, {'kind':'travel'},{'playerId':'p','location':'a'})
    assert result['location']=='b' and result['updates']==[{'field':'location'}]
    assert result['transfers']==result['learned']==result['eventResolutions']==[]
    assert result['events']==[{'type':'arrival'}]


def test_declared_exit_cannot_be_cancelled_by_an_unregistered_plot_hook():
    plan={'location':'a','updates':[], 'events':[{'type':'observation'}]}
    result=travel_capability(plan,{'kind':'travel','destinationId':'b'},{'playerId':'p','location':'a'})
    assert result['location']=='b' and result['summary']=='沿路线抵达目的地'
    assert plan['location']=='a'
    plan['events']=[{'type':'obstacle','description':'桥梁实际坍塌'}]
    assert travel_capability(plan,{'kind':'travel','destinationId':'b'},{'playerId':'p','location':'a'})['location']=='a'
    assert travel_capability({'location':'a'}, {'kind':'travel'}, {'playerId':'p','location':'a'})['location']=='a'


def test_future_clues_and_endings_do_not_leak_through_nearby_omniscient_npc():
    from app.scripts.context import enrich_script
    lore=[dict(id=key,title=key,text=key,knownBy=['h']) for key in ('current','future')]
    script={'objectives':[{'id':'o1','requires':[],'checks':[{'kind':'knowledge','loreId':'current'}]},
        {'id':'o2','requires':['o1'],'checks':[{'kind':'knowledge','loreId':'future'}]}],
        'endings':[{'id':'end','requires':['o2'],'description':'未来的结局'}]}
    state={'playerId':'p','known':[],'location':'home','entities':{'h':{'location':'home'}}}
    data={'lore':deepcopy(lore)}
    enrich_script(data,{'lore':lore,'script':script},state,{'blocks':[]})
    assert [l['id'] for l in data['lore']]==['current'] and data['scriptGuide']['endings']==[]
    state['plot']={'completed':['o1']}
    enrich_script(data,{'lore':lore,'script':script},state,{'blocks':[]})
    assert {l['id'] for l in data['lore']}=={'current','future'}


def test_work_survives_two_intervening_turns_and_consumes_once():
    base={'playerId':'p','entities':{'p':{'inventory':{'herb':3}}}}
    start={'transfers':[{'itemId':'herb','fromId':'p','toId':None,'quantity':3}],'work':[{'id':'batch','operation':'start','title':'第一炉','inputs':[{'itemId':'herb','quantity':3}],'outputs':[]}]}
    _,base['work']=prepare_work(start,base)
    for _ in range(2):_,base['work']=prepare_work({'transfers':[],'work':[]},base)
    finish={'transfers':[{'itemId':'herb','fromId':'p','toId':None,'quantity':3},{'itemId':'potion','fromId':None,'toId':'p','quantity':1}], 'work':[{'id':'batch','operation':'complete','title':'第一炉','inputs':[],'outputs':[{'itemId':'potion','quantity':1}]}]}
    plan,batches=prepare_work(finish,base)
    assert plan['transfers']==[finish['transfers'][1]] and batches['batch']['status']=='complete'
    with pytest.raises(Problem):prepare_work(finish,{**base,'work':batches})


def test_legacy_batch_recovery_uses_full_ancestry_not_last_progress_phrase():
    cp=SimpleNamespace(id='start',state={'entities':{'p':{'fields':{'phase':'正在炼制'}}}},changes=[{'field':'inventory','itemId':'h','quantity':2,'fromId':'p','toId':None,'reason':'投入药材'}])
    after=SimpleNamespace(id='later',state={'entities':{'p':{'fields':{'phase':'待最小火收汁出液','otherQuest':'已完成'}}}},changes=[])
    _,batches=prepare_work({}, {'playerId':'p','entities':{}},[cp,after])
    assert batches['start']['status']=='active'


def test_refunded_item_does_not_disappear_from_ordered_receipts():
    state={'playerId':'p','entities':{'p':{'inventory':{'token':1}},'place':{'inventory':{}}}}
    transfers=[{'itemId':'token','fromId':'p','toId':'place','quantity':1,'reason':'恢复生命代价'},{'itemId':'token','fromId':'place','toId':'p','quantity':1,'reason':'原物返还'}]
    assert normalize_receipts({'transfers':deepcopy(transfers)},state,{'entities':[{'id':'token','type':'item','name':'护符'}]})['transfers']==transfers


def test_ending_requires_actual_receipts_and_committed_knowledge():
    objectives=[{'id':'discover','requires':[],'title':'追查','description':'查明来源','checks':[{'kind':'knowledge','entityId':'player','loreId':'origin'}]}, {'id':'return','requires':['discover'],'title':'归还','description':'交还物证','checks':[{'kind':'transfer','entityId':'player','itemId':'gem','toId':'owner','quantity':1}]}]
    definition={'script':{'objectives':objectives,'endings':[{'id':'solved','title':'结案','description':'证物归还','requires':['discover','return']}]}}
    state={'playerId':'p','entities':{'p':{'known':[]}},'discovered':[]}
    advance(state,definition,[]);assert not state['plot']['completed']
    state['entities']['p']['known']=['origin'];advance(state,definition,[])
    assert state['plot']['completed']==['discover'] and not state['plot']['ending']
    advance(state,definition,[{'field':'inventory','itemId':'gem','fromId':'p','toId':'owner','quantity':1}])
    assert public_progress(state,definition)['ending']['id']=='solved'


def test_full_source_blocks_never_truncate_long_paragraphs():
    text='开始'*2100+'\n\n'+'结局'*3000
    blocks=blocks_for(text)
    assert ''.join(b['text'] for b in blocks)==text.replace('\n\n','')
    assert sum(len(g) for g in groups_for(blocks))==len(blocks)
    assert len({b['blockId'] for b in blocks})==len(blocks)


@pytest.mark.parametrize('raw', ['{"kind":"speech","speakerId":"ghost","text":"你好"}', '{"kind":"speech","speakerId":"p","text":'])
def test_invalid_or_incomplete_speaker_is_not_committed(raw):
    with pytest.raises(Problem):structured_segments(raw,[{'id':'npc','name':'侦探'}])


def test_structured_speech_and_stage_directions_stay_separate():
    value='\n'.join(json.dumps(v,ensure_ascii=False) for v in [{'kind':'narration','text':'他抬头，没有开口。'},{'kind':'speech','speakerId':'npc','text':'你从哪里来？'},{'kind':'narration','text':'风声掠过窗边。'}])
    segments=structured_segments(value,[{'id':'npc','name':'侦探'}])
    assert [p['kind'] for p in segments]==['narration','speech','narration']
    assert segments[1]['speakerName']=='侦探'


@pytest.mark.parametrize('title', ['第1章 归来','第一章 归来','续写 · 第 1 章 归来'])
def test_absolute_chapter_title_keeps_subtitle(title):
    assert '第 3 章' in chapter_title(title,3) and chapter_title(title,3).endswith('归来')


def test_panorama_wrap_continuity_does_not_change_centre_pixels():
    image=Image.new('RGB',(800,400),'red');image.paste('blue',(400,0,800,400))
    middle=image.getpixel((400,200));image,quality=periodic_edges(image)
    delta=ImageStat.Stat(ImageChops.difference(image.crop((0,0,1,400)),image.crop((799,0,800,400)))).mean
    assert max(delta)==0 and image.getpixel((400,200))==middle and quality['wrapDeltaBefore']>0


@pytest.mark.parametrize('value',[{}, {'text':'short'}, {'sampleId':'blue-carbuncle','text':'text'}])
def test_incomplete_or_ambiguous_script_input_rejected(value):
    with pytest.raises(ValueError):ScriptInput.model_validate(value)
