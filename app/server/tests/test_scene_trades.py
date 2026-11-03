from copy import deepcopy
import pytest
from app.scenes.interaction import normalize_plan
from app.world.state import initial_state,apply_plan
from .test_world import definition,plan


def sale():
    d,s=initial_state(definition(),'player')
    item=next(e for e in d['entities'] if e['id']=='letter');item['name']='药材';item['stackable']=True
    s['entities']['player']['inventory']={};s['entities']['npc']['inventory']={'letter':12}
    s['entities']['player']['fields']['金币']=300;s['entities']['npc']['fields'].update({'金币':600,'药材单价':80})
    d['rules']['actionCosts']={'action':{'金币':0}}
    p=plan(transfers=[{'itemId':'letter','fromId':'npc','toId':'player','quantity':3,'reason':'当面付款交付','visibleTo':['player']}],
           events=[{'type':'交易','entityId':'npc','description':'梅舟收下240金币，当场交割药材。','visibleTo':['player'],'dueMinute':None}])
    return d,s,p


def test_matching_registered_prices_complete_the_received_payment_once():
    d,s,p=sale();original=deepcopy(p)
    normalized=normalize_plan(p,s,d);assert normalized['resources'][0]['amount']==240
    assert normalize_plan(normalized,s,d)==normalized
    after,_,_,_=apply_plan(s,d,normalized,'sale',action_kind='action')
    assert after['entities']['player']['fields']['金币']==60
    assert after['entities']['npc']['fields']['金币']==840
    assert after['entities']['player']['inventory']['letter']==3
    assert p==original and s['entities']['player']['fields']['金币']==300


def test_counted_payment_requires_an_actual_receipt_not_just_counting():
    d,s,p=sale();p['events'][0]['description']='梅舟点清240金币收入钱匣，当面交割药材。'
    assert normalize_plan(p,s,d)['resources'][0]['amount']==240
    p['events'][0]['description']='梅舟点清240金币，尚未收进钱匣。'
    assert not normalize_plan(p,s,d).get('resources')
    p['events'][0]['description']='梅舟点清240金币，还给你等你决定。'
    assert not normalize_plan(p,s,d).get('resources')


@pytest.mark.parametrize('description',['梅舟报价240金币。','梅舟尚未收下240金币。','如果梅舟收下240金币，就交付药材。','梅舟收下200金币，已经议价。','梅舟免费赠送药材。'])
def test_quotes_hypothetical_unpaid_discounted_and_gift_receipts_never_infer_a_debit(description):
    d,s,p=sale();p['events'][0]['description']=description
    assert not normalize_plan(p,s,d).get('resources')


def test_existing_negotiated_payment_and_unknown_price_never_get_an_extra_charge():
    d,s,p=sale();p['resources']=[{'entityId':'player','field':'金币','operation':'transfer','counterpartyId':'npc','amount':200,'reason':'双方同意折扣','visibleTo':['player']}]
    assert normalize_plan(p,s,d)['resources']==p['resources']
    p['resources']=[];del s['entities']['npc']['fields']['药材单价']
    assert not normalize_plan(p,s,d).get('resources')
