"""Full-source compilation, author review and real committed quest semantics."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from app.errors import Problem
from app.providers import MockAdapter
from app.scripts.compiler import compile_blueprint
from app.scripts.service import blocks_for, validate
from app.scripts.plot import advance, public_progress
from app.world.schemas import WorldRules
from app.world.work import prepare_work
from app.world.worker import process
from app.scenes.dialogue import structured_segments


def blueprint():
    return dict(title='失物',synopsis='医生追索失物并决定归还。',timeLabel='清晨',startPlaceId='home',recommendedCharacterId='doctor',opening='先问侦探。',
        people=[dict(id='doctor',name='医生',description='调查者',placeId='home'),dict(id='detective',name='侦探',description='提供线索',placeId='home',knows=['origin']),dict(id='witness',name='证人',description='稍后现身',placeId=None,knows=['truth'])],
        places=[dict(id='home',name='书房',description='通向街道的门。',neighbors=['street']),dict(id='street',name='街道',description='街道连接书房与市场。',neighbors=['market']),dict(id='market',name='市场',description='可返回街道。',neighbors=[])],
        items=[dict(id='gem',name='宝石',description='失物',ownerId='detective')],
        clues=[dict(id='origin',title='来历',text='物品来自市场。',knownBy=['detective']),dict(id='truth',title='真相',text='失物属于证人。',knownBy=['witness'])],
        scenes=[dict(id='s1',title='询问',summary='书房调查',placeId='home',characterIds=['doctor','detective'],sourceBlockIds=['S0001']),dict(id='s2',title='追查',summary='街道寻访',placeId='street',sourceBlockIds=['S0001']),dict(id='s3',title='相遇',summary='市场证人',placeId='market',characterIds=['witness'],sourceBlockIds=['S0001'])],
        objectives=[dict(id='o1',title='查明来历',description='询问侦探',requires=[],checks=[dict(kind='knowledge',loreId='origin')],sourceBlockIds=['S0001']),dict(id='o2',title='追查证人',description='市场询问',requires=['o1'],checks=[dict(kind='knowledge',loreId='truth')],sourceBlockIds=['S0001']),dict(id='o3',title='归还物品',description='拿到后交还',requires=['o2'],checks=[dict(kind='acquired',itemId='gem'),dict(kind='transfer',itemId='gem',toId='witness')],sourceBlockIds=['S0001'])],
        endings=[dict(id='return',title='归还',description='物归原主。',requires=['o3'],checks=[dict(kind='field',field='决定',value='归还')]),dict(id='report',title='报告',description='报备警方。',requires=['o3'],checks=[dict(kind='field',field='决定',value='报告')])])


def world_fixture():
    return SimpleNamespace(source={'blocks':blocks_for('一部完整故事。'*30)},definition={'rules':WorldRules().model_dump()})


def test_compiler_builds_closed_ledger_and_schedules_future_clue_holder():
    world=world_fixture();raw=blueprint()
    raw['people'][1]['name:']=raw['people'][1].pop('name')
    value,compiled=validate(compile_blueprint(raw,world.source['blocks']),world)
    assert compiled['initial']['detective']['inventory']=={'gem':1}
    assert compiled['initial']['doctor']['inventory']=={}
    assert compiled['initial']['witness']['location'] is None
    assert compiled['script']['castSchedule']==[{'entityId':'witness','placeId':'market','requires':['o1']}]
    assert compiled['rules']['routes']['market']==['street']
    assert compiled['initial']['doctor']['fields']['决定']==''
    assert value['definition']['entities'][0]['source']=='inferred'


def test_source_grounded_private_motive_survives_start_without_becoming_public_evidence():
    from app.world.state import initial_state, public_view
    world=world_fixture();raw=blueprint()
    quote='证人暗自决定隐瞒失物的真正来历。'
    world.source['blocks'][0]['text']+=quote
    raw['people'][2].update(hiddenGoal='隐瞒失物来历以免遭到追问',
        goalEvidence=[{'blockId':'S0001','quote':quote}])
    _,definition=validate(compile_blueprint(raw,world.source['blocks']),world)
    definition,state=initial_state(definition,'doctor')
    assert state['entities']['witness']['goal']=='隐瞒失物来历以免遭到追问'
    assert state['entities']['doctor']['known']==[]
    state['entities']['witness']['location']='home';state['discovered'].append('witness')
    public=public_view(state,definition)
    rendered=json.dumps(public,ensure_ascii=False)
    assert quote not in rendered and '隐瞒失物来历' not in rendered
    witness=next(e for e in public['entities'] if e['id']=='witness')
    assert witness['state']['goal']=='' and witness['evidence']==[]


@pytest.mark.parametrize('evidence',[[],[{'blockId':'missing','quote':'一部完整故事'}],
    [{'blockId':'S0001','quote':'来源中并不存在的动机证据'}]])
def test_private_motive_requires_exact_existing_source_evidence(evidence):
    world=world_fixture();raw=blueprint()
    raw['people'][1].update(hiddenGoal='查清失物来历',goalEvidence=evidence)
    with pytest.raises(Problem) as failed:compile_blueprint(raw,world.source['blocks'])
    assert failed.value.code=='SCRIPT_GOAL_EVIDENCE_INVALID'


def test_auto_completed_opening_remains_reviewable_but_cannot_be_published():
    from app.scripts.service import review_issues
    world=world_fixture();raw=blueprint();raw['people'][0]['knows']=['origin']
    value,compiled=validate(compile_blueprint(raw,world.source['blocks']),world)
    assert any('开场就已满足' in text for text in review_issues(value,compiled))
    with pytest.raises(Problem,match='开场'):
        validate(value,world,publication=True)
    raw=blueprint();raw['objectives'][1]['checks']=[dict(kind='visit',placeId='home')]
    value,compiled=validate(compile_blueprint(raw,world.source['blocks']),world)
    assert any('只检查开场地点' in text for text in review_issues(value,compiled))


@pytest.mark.parametrize('fault',['conflicting-item','ambiguous-ending','bad-reference','cycle'])
def test_invalid_story_cannot_be_published(fault):
    world=world_fixture();raw=blueprint()
    if fault=='conflicting-item':raw['objectives'][2]['checks'][0]['kind']='inventory'
    if fault=='ambiguous-ending':raw['endings'][1]['checks']=deepcopy(raw['endings'][0]['checks'])
    if fault=='bad-reference':raw['scenes'][0]['sourceBlockIds']=['missing-source']
    if fault=='cycle':raw['objectives'][0]['requires']=['o3']
    with pytest.raises(Problem):validate(compile_blueprint(raw,world.source['blocks']),world)


def test_item_handoff_and_distinct_ending_require_committed_evidence():
    raw=blueprint();world=world_fixture();_,definition=validate(compile_blueprint(raw,world.source['blocks']),world)
    state=dict(playerId='doctor',location='home',discovered=['home','doctor','detective'],entities=deepcopy(definition['initial']))
    advance(state,definition,[]);assert not state['plot']['completed']
    assert state['entities']['witness']['location'] is None
    state['entities']['doctor']['known']=['origin'];advance(state,definition,[])
    assert state['entities']['witness']['location']=='market'
    state['entities']['doctor']['known'].append('truth');advance(state,definition,[])
    advance(state,definition,[dict(field='inventory',itemId='gem',fromId='detective',toId='doctor',quantity=1)])
    assert not state['plot']['ending']
    advance(state,definition,[dict(field='inventory',itemId='gem',fromId='doctor',toId='witness',quantity=1)])
    assert state['plot']['completed']==['o1','o2','o3']
    assert not state['plot']['ending']
    state['entities']['doctor']['fields']['决定']='报告';advance(state,definition,[])
    assert public_progress(state,definition)['ending']['id']=='report'


def test_temporary_chapter_has_no_committed_changes_to_replay():
    parent=SimpleNamespace(id='candidate',state={'entities':{}},changes=None)
    assert prepare_work({},dict(playerId='doctor',entities={}),[parent])==({}, {})


def test_player_utterance_is_preserved_without_becoming_an_npc_bubble():
    parts=structured_segments('{"kind":"speech","speakerId":"doctor","text":"我来调查。"}',[],{'id':'doctor','name':'医生'})
    assert parts==[{'kind':'narration','text':'医生：“我来调查。”'}]


def test_full_script_api_analyzes_reviews_and_publishes_without_starting_play(client,reader,profile):
    calls=[]
    def stream(adapter,profile,prompt,data,limit,*,on_delta,cancel_check,**kwargs):
        calls.append(data)
        assert prompt.startswith('script-breakdown')
        assert ''.join(b['text'] for b in data['blocks'])=='完整故事。'*40
        text=json.dumps(blueprint(),ensure_ascii=False);cancel_check();on_delta(text)
        return dict(text=text,finishReason='stop',usage=dict(state='known',inputTokens=100,outputTokens=100),latencyMs=1)
    headers={'Idempotency-Key':'full-script'};body=dict(title='失物',text='完整故事。'*40)
    created=client.post('/api/world/v1/scripts',json=body,headers=headers)
    assert created.status_code==202,created.text
    assert client.post('/api/world/v1/scripts',json=body,headers=headers).json()==created.json()
    with patch.object(MockAdapter,'generate_stream',stream):process(created.json()['taskId'])
    url='/api/world/v1/worlds/'+created.json()['worldId']
    review=client.get(url+'/script').json()
    assert review['task']['state']=='succeeded',review
    assert review['processedBlocks']==review['blockCount'] and len(calls)==1
    assert 'raw' not in review and review['source']['blocks']
    published=client.post(url+'/script/publish',json=dict(expectedVersion=review['worldVersion']),headers={'Idempotency-Key':'publish-script'})
    assert published.status_code==200,published.text
    info=client.get(url).json()
    assert info['status']=='active' and info['recommendedCharacterId']=='doctor' and not info['activeLineId']
    assert client.post(url+'/script/publish',json=dict(expectedVersion=review['worldVersion']),headers={'Idempotency-Key':'stale-publish'}).status_code==409
    revision=client.post(url+'/script/revision',json={},headers={'Idempotency-Key':'revise-script'})
    assert revision.status_code==201,revision.text
    revised=client.get('/api/world/v1/worlds/'+revision.json()['worldId']+'/script').json()
    assert revised['status']=='review' and not revised['hasSaves']
    assert revised['value']==review['value'] and revised['source']==review['source']
    assert client.get(url).json()['status']=='active'
