"""A handed-over document is the source, not the newly informed recipient."""
from copy import deepcopy
import pytest
from app.errors import Problem
from app.world.knowledge import normalize_knowledge
from app.world.state import apply_plan, initial_state
from .test_world import definition, plan


def handoff():
    d = definition()
    d['lore'][0].update(knownBy=['player'], entityIds=['letter'])
    d, state = initial_state(d, 'player')
    proposal = plan(
        transfers=[{'itemId':'letter','fromId':'player','toId':'npc','quantity':1,'reason':'呈交物证','visibleTo':['player']}],
        updates=[{'entityId':'npc','field':'known','value':['secret'],'reason':'当面查阅信件'}],
        events=[{'type':'knowledge_shared','entityId':'npc','loreIds':['secret'],
                 'description':'梅舟当面查阅信件，确认隐秘消息','visibleTo':['player']}])
    return d, state, proposal


def test_real_document_handoff_informs_recipient_atomically_and_keeps_input_immutable():
    d, state, proposal = handoff()
    before = deepcopy((d, state, proposal))
    final, _, changes, events = apply_plan(state, d, proposal, 'handoff')
    assert final['entities']['player']['inventory']['letter'] == 0
    assert final['entities']['npc']['inventory']['letter'] == 1
    assert 'secret' in final['entities']['npc']['known']
    assert events[0]['type'] == 'knowledge_observed' and events[0]['entityId'] == 'letter'
    assert set(events[0]['audienceIds']) == {'player', 'npc'}
    assert any(c['field'] == 'inventory' for c in changes)
    assert (d, state, proposal) == before


@pytest.mark.parametrize('missing', ['prior-knowledge','lore-link','transfer','held-document',
                                   'actual-recipient','known-update','private-audience','reading'])
def test_no_document_channel_is_inferred_without_all_explicit_grounding(missing):
    d, state, proposal = handoff()
    if missing == 'prior-knowledge':
        d['lore'][0]['knownBy'] = [];state['known'] = [];state['entities']['player']['known'] = []
    elif missing == 'lore-link':d['lore'][0]['entityIds'] = ['npc']
    elif missing == 'transfer':proposal['transfers'] = []
    elif missing == 'held-document':state['entities']['player']['inventory']['letter'] = 0
    elif missing == 'actual-recipient':proposal['transfers'][0]['toId'] = 'pier'
    elif missing == 'known-update':proposal['updates'] = []
    elif missing == 'private-audience':proposal['events'][0]['visibleTo'] = ['all']
    elif missing == 'reading':proposal['events'][0]['description'] = '梅舟打算明天查资料'
    original = deepcopy(proposal)
    assert normalize_knowledge(proposal, state, d)['events'][0]['type'] == 'knowledge_shared'
    with pytest.raises(Problem):apply_plan(state, d, proposal, 'unsafe')
    assert proposal == original


def test_future_handoff_cannot_grant_current_knowledge():
    d, state, proposal = handoff()
    proposal['events'][0]['dueMinute'] = 60
    assert normalize_knowledge(proposal, state, d)['events'][0]['type'] == 'knowledge_shared'
    with pytest.raises(Problem):apply_plan(state, d, proposal, 'future')


def test_known_source_sharing_keeps_original_semantics():
    d, state, proposal = handoff()
    proposal['events'][0].update(entityId='player',visibleTo=['npc'],description='顾安亲口告知梅舟消息')
    final, _, _, events = apply_plan(state, d, proposal, 'spoken')
    assert events[0]['type'] == 'knowledge_shared' and events[0]['entityId'] == 'player'
    assert 'secret' in final['entities']['npc']['known']
