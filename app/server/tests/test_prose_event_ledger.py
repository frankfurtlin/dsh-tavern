"""Mechanical event/quote/state contracts; these do not certify model semantics."""
from copy import deepcopy
import json
import pytest
from app.domain import digest, canonical
from app.prose.events import from_review, validate, fold


def proposal(kind, quote, change, subject='lin', certainty='narrated'):
    return {'kind':kind, 'subjectId':subject, 'certainty':certainty, 'confidence':850,
        'statement':quote, 'quote':quote, 'change':change}


def ledger(chapter, text, proposals, prior=()):
    return from_review({'text':json.dumps({'events':proposals}, ensure_ascii=False), 'finishReason':'stop'}, text, chapter,
        subjects={'lin','mei'}, prior_ids=prior)


def fixture():
    one = ledger(1, '🙂林渡把蓝信交给梅舟。梅舟从渡口走到南岸。', [
        proposal('item','🙂林渡把蓝信交给梅舟。',{'item':'蓝信','fromHolderId':'lin','toHolderId':'mei','location':'渡口'}),
        proposal('location','梅舟从渡口走到南岸。',{'from':'渡口','to':'南岸'},'mei'),
    ])
    two = ledger(2, '她决定查清旧信来历。失踪船夫仍未找到。', [
        proposal('motivation','她决定查清旧信来历。',{'goal':'查清旧信来历','state':'adopted'},'mei'),
        proposal('unresolved','失踪船夫仍未找到。',{'threadKey':'失踪船夫','state':'opened','detail':'船夫去向未明'},None),
    ], ['c1:e1','c1:e2'])
    three = ledger(3, '梅舟把蓝信交还林渡。林渡获知船夫去了北岸。', [
        proposal('item','梅舟把蓝信交还林渡。',{'item':'蓝信','fromHolderId':'mei','toHolderId':'lin','location':'南岸'},'mei'),
        proposal('knowledge','林渡获知船夫去了北岸。',{'fact':'船夫去了北岸','learnedByIds':['lin']}),
    ], ['c1:e1','c1:e2','c2:e1','c2:e2'])
    four = ledger(4, '这是他们相遇前一夜的事。也许林渡已经回到渡口。', [
        proposal('time','这是他们相遇前一夜的事。',{'label':'相遇前一夜','relation':'before','relativeTo':'c1:e1'},None),
        proposal('location','也许林渡已经回到渡口。',{'from':None,'to':'渡口'},'lin','inferred'),
    ], ['c1:e1','c1:e2','c2:e1','c2:e2','c3:e1','c3:e2'])
    five = ledger(5, '船夫归来，失踪之谜解开。她放弃追查旧信。', [
        proposal('unresolved','船夫归来，失踪之谜解开。',{'threadKey':'失踪船夫','state':'resolved','detail':'船夫亲自归来'},None),
        proposal('motivation','她放弃追查旧信。',{'goal':'查清旧信来历','state':'abandoned'},'mei'),
    ], ['c1:e1','c1:e2','c2:e1','c2:e2','c3:e1','c3:e2','c4:e1','c4:e2'])
    return [one, two, three, four, five]


def test_five_chapter_proposed_state_tracks_transfer_time_motive_knowledge_and_thread():
    ledgers = fixture()
    assert all(item['status']=='model-proposed' for item in ledgers)
    state = fold(ledgers)
    assert state['items']['蓝信'] == {'holderId':'lin','location':'南岸','eventId':'c3:e1'}
    assert state['locations']=={'mei':{'place':'南岸','eventId':'c1:e2'}}
    assert state['motivations']['mei']['state']=='abandoned'
    assert state['threads']['失踪船夫']['state']=='resolved'
    assert state['knowledge']=={'lin':[{'fact':'船夫去了北岸','eventId':'c3:e2'}]}
    assert state['timeRelations'][0]['relation']=='before' and not state['hints']
    assert len(state['eventOrder'])==10
    assert digest(canonical(state)) == digest(canonical(fold(deepcopy(ledgers))))


def test_utf16_quoted_evidence_and_source_hash_are_exact():
    value=fixture()[0]
    assert value['events'][0]['start']==0 and value['events'][0]['end']==12
    assert validate(value,'🙂林渡把蓝信交给梅舟。梅舟从渡口走到南岸。')==value
    with pytest.raises(ValueError): validate(value,'🙂林渡把蓝信交给梅舟。改动后的文字。')
    poisoned=deepcopy(value);poisoned['events'][0]['end']-=1
    with pytest.raises(ValueError):validate(poisoned)


@pytest.mark.parametrize('patch', [
    {'subjectId':'future-person'}, {'certainty':'confirmed'}, {'confidence':True}, {'confidence':1001},
    {'quote':'不存在的引文'}, {'change':{'item':'蓝信','fromHolderId':'lin','toHolderId':'future-person','location':None}},
    {'change':{'item':'蓝信','fromHolderId':'lin','toHolderId':'mei','location':None,'hidden':'unknown'}},
])
def test_invalid_model_events_preserve_unavailable_metadata_without_creating_facts(patch):
    original=proposal('item','林渡把蓝信交给梅舟。',{'item':'蓝信','fromHolderId':'lin','toHolderId':'mei','location':None})
    original.update(patch);value=ledger(1,'林渡把蓝信交给梅舟。',[original])
    assert value['status']=='unavailable' and value['events']==[]


@pytest.mark.parametrize('reference', ['c1:e1','c2:e1','c0:e1','c1:e13','c99:e99'])
def test_event_references_cannot_guess_missing_self_forward_or_out_of_range(reference):
    value=ledger(1,'一夜之前。',[proposal('time','一夜之前。',{'label':'前夜','relation':'before','relativeTo':reference},None)])
    assert value['status']=='unavailable'


def test_transfer_and_travel_gaps_are_soft_and_inference_never_overwrites_narrated_state():
    one=fixture()[0]
    two=ledger(2,'林渡把蓝信交给梅舟。梅舟从北岸走到城中。',[
        proposal('item','林渡把蓝信交给梅舟。',{'item':'蓝信','fromHolderId':'lin','toHolderId':'mei','location':None}),
        proposal('location','梅舟从北岸走到城中。',{'from':'北岸','to':'城中'},'mei'),
    ],['c1:e1','c1:e2'])
    state=fold([one,two])
    assert [hint['code'] for hint in state['hints']]==['ITEM_TRANSFER_GAP','LOCATION_TRANSITION_GAP']
    assert all(hint['severity']=='soft' for hint in state['hints'])
    assert state['items']['蓝信']['holderId']=='mei'
    with pytest.raises(ValueError):fold([two,one])


def test_named_new_people_are_kept_separate_from_stable_known_identities():
    value=ledger(1,'另一个林渡走进灯下。林渡把旧别名告诉梅舟。',[
        proposal('character','另一个林渡走进灯下。',{'name':'林渡','aliases':[]},None),
        proposal('character','林渡把旧别名告诉梅舟。',{'name':'林渡','aliases':['夜渡']},'lin'),
    ])
    state=fold([value]);assert set(state['characters'])=={'c1:e1','lin'}
    assert state['characters']['c1:e1']['subjectId'] is None


def test_deep_or_duplicate_review_objects_return_unavailable_without_recursive_decoding():
    for raw in ['{"events":[],"events":[]}', '{"unknown":'+ '['*10000+'0'+']'*10000+',"events":[]}']:
        value=from_review({'text':raw,'finishReason':'stop'},'正文。',1)
        assert value['status']=='unavailable' and not value['events']


def test_explicit_flashback_transfer_keeps_current_owner_and_separate_history():
    one=fixture()[0]
    two=ledger(2,'相遇前一夜。林渡那时把蓝信藏进木箱。',[
        proposal('time','相遇前一夜。',{'label':'前夜','relation':'before','relativeTo':'c1:e1'},None),
        proposal('item','林渡那时把蓝信藏进木箱。',{'item':'蓝信','fromHolderId':'lin','toHolderId':'lin','location':'木箱'}),
    ],['c1:e1','c1:e2'])
    state=fold([one,two])
    assert state['items']['蓝信']['holderId']=='mei'
    assert state['historicalEvents']==['c2:e2'] and not state['hints']


def test_version_ledger_reference_closure_and_canonical_chapter_binding():
    from app.prose.events import validate_ledger
    values=fixture();value={'1':{str(i+1):v for i,v in enumerate(values)}}
    assert validate_ledger(value)==value
    with pytest.raises(ValueError):validate_ledger({'01':value['1']})
    with pytest.raises(ValueError):validate_ledger({'1':{'2':values[0]}})
    with pytest.raises(ValueError):validate_ledger({'1':{'4':values[3]}})
