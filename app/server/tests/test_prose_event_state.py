"""Finite relationships, causal provenance and disjoint writing namespaces."""
from copy import deepcopy
import pytest
from app.prose.events import fold, fold_scoped
from .test_prose_event_ledger import ledger, proposal


def relationship_story():
    first = ledger(1, '林渡与梅舟结盟。结盟后两人同行。', [
        proposal('relationship', '林渡与梅舟结盟。', {'otherId': 'mei', 'relation': '盟友'}),
        proposal('event', '结盟后两人同行。', {'effect': '两人同行', 'causedBy': ['c1:e1']}),
    ])
    second = ledger(2, '也许林渡视梅舟为对手。结盟以前。两人曾经互相警惕。警惕使他们分头离开。', [
        proposal('relationship', '也许林渡视梅舟为对手。', {'otherId': 'mei', 'relation': '对手'}, certainty='inferred'),
        proposal('time', '结盟以前。', {'label': '结盟前', 'relation': 'before', 'relativeTo': 'c1:e1'}, None),
        proposal('relationship', '两人曾经互相警惕。', {'otherId': 'mei', 'relation': '警惕'}),
        proposal('event', '警惕使他们分头离开。', {'effect': '分头离开', 'causedBy': ['c2:e3']}),
    ], ['c1:e1', 'c1:e2'])
    third = ledger(3, '梅舟已经信任林渡。林渡明确解除同盟。', [
        proposal('relationship', '梅舟已经信任林渡。', {'otherId': 'lin', 'relation': '信任'}, 'mei'),
        proposal('relationship', '林渡明确解除同盟。', {'otherId': 'mei', 'relation': '解除同盟'}),
    ], ['c1:e1', 'c1:e2', 'c2:e1', 'c2:e2', 'c2:e3', 'c2:e4'])
    return [first, second, third]


def test_directed_relationships_do_not_assume_reciprocity_or_promote_inference_and_flashback():
    story = relationship_story()
    before = fold(story[:2])
    assert before['relationships'] == {'lin': {'mei': {'relation': '盟友', 'eventId': 'c1:e1'}}}
    assert before['historicalEvents'] == ['c2:e3', 'c2:e4']
    current = fold(story)
    assert current['relationships']['lin']['mei']['relation'] == '解除同盟'
    assert current['relationships']['mei']['lin']['relation'] == '信任'
    assert current['schema'] == 'prose-event-state-v2'


def test_causal_history_keeps_exact_backward_sources_and_story_frame_without_changing_input():
    story = relationship_story(); original = deepcopy(story)
    state = fold(story)
    assert state['causalEvents'] == [
        {'eventId': 'c1:e2', 'effect': '两人同行', 'causedBy': ['c1:e1'], 'temporalFrame': 'present'},
        {'eventId': 'c2:e4', 'effect': '分头离开', 'causedBy': ['c2:e3'], 'temporalFrame': 'past'},
    ]
    assert story == original


def test_scoped_batches_keep_local_ids_disjoint_and_detect_cross_batch_holder_gap():
    first = ledger(1, '林渡把信交给梅舟。', [proposal('item', '林渡把信交给梅舟。',
        {'item': '信', 'fromHolderId': 'lin', 'toHolderId': 'mei', 'location': None})])
    next_batch = ledger(1, '林渡把信放回箱子。', [proposal('item', '林渡把信放回箱子。',
        {'item': '信', 'fromHolderId': 'lin', 'toHolderId': 'lin', 'location': '箱子'})])
    state = fold_scoped([{'scope': 'batch-a@v1', 'ledgers': [first]}, {'scope': 'batch-b@v2', 'ledgers': [next_batch]}])
    assert state['eventOrder'] == ['batch-a@v1/c1:e1', 'batch-b@v2/c1:e1']
    assert state['items']['信']['eventId'] == 'batch-b@v2/c1:e1'
    assert state['hints'] == [{'code': 'ITEM_TRANSFER_GAP', 'eventId': 'batch-b@v2/c1:e1',
                              'previousEventId': 'batch-a@v1/c1:e1', 'severity': 'soft'}]


def test_namespace_qualifies_time_cause_and_unknown_identity_without_name_merging():
    story = relationship_story()
    state = fold_scoped([{'scope': 'source@1', 'ledgers': story}])
    assert state['timeRelations'][0]['relativeTo'] == 'source@1/c1:e1'
    assert state['causalEvents'][1]['causedBy'] == ['source@1/c2:e3']
    stranger = ledger(1, '另一个林渡出现。', [proposal('character', '另一个林渡出现。', {'name': '林渡', 'aliases': []}, None)])
    state = fold_scoped([{'scope': 'a', 'ledgers': [stranger]}, {'scope': 'b', 'ledgers': [stranger]}])
    assert set(state['characters']) == {'a/c1:e1', 'b/c1:e1'}


@pytest.mark.parametrize('groups', [None, [{'scope': ' ', 'ledgers': []}], [{'scope': True, 'ledgers': []}],
    [{'scope': 'a', 'ledgers': []}, {'scope': 'a', 'ledgers': []}], [{'scope': 'a', 'ledgers': {}, 'extra': 1}]])
def test_malformed_or_duplicate_namespace_groups_are_explicitly_rejected(groups):
    with pytest.raises(ValueError):
        fold_scoped(groups)
