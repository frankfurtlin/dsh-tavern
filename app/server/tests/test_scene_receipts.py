import json
import os
from pathlib import Path
from app.world.state import parse_json, apply_plan
from app.world.schemas import DirectorOutput
from app.scenes.interaction import normalize_plan


def receipt(index):return json.loads((Path(os.environ.get('PLAYTHROUGH_FIXTURE_DIR','/qa'))/'receipt-regressions.json').read_text('utf-8-sig'))[index]


def test_real_sample_delivery_never_transfers_the_source_document():
    case=receipt(0);plan=normalize_plan(parse_json(case['raw'],DirectorOutput),case['state'],case['definition'])
    document=next(e['id'] for e in case['definition']['entities'] if e['name']=='坊市进货凭据')
    assert not any(t['itemId']==document for t in plan['transfers'])
    sample=next(e for e in plan['additions'] if e['name']=='同批药粉样品')
    delivery=next(t for t in plan['transfers'] if t['itemId']==sample['id'])
    assert delivery['toId']==case['state']['playerId'] and delivery['fromId'] is None
    assert plan==normalize_plan(parse_json(case['raw'],DirectorOutput),case['state'],case['definition'])
    state,definition,_,_=apply_plan(case['state'],case['definition'],plan,'receipt-test',[],action_kind='dialogue')
    assert document not in state['entities'][state['playerId']]['inventory']
    assert state['entities'][state['playerId']]['inventory'][sample['id']]==1


def test_real_certificate_metadata_and_service_payment_are_canonical():
    case=receipt(1);plan=normalize_plan(parse_json(case['raw'],DirectorOutput),case['state'],case['definition'])
    assert all('entityIds' not in e for e in plan['additions'])
    assert len(plan['resources'])==1
    assert plan['resources'][0]['entityId']==case['state']['playerId'] and plan['resources'][0]['amount']==20
    assert not any(t['reason'].startswith('鉴定完毕，归还') for t in plan['transfers'])
    assert any(e['name']=='谷尼鉴定书' for e in plan['additions'])


def test_lore_misfiled_as_entity_moves_to_knowledge_channel_without_losing_content():
    raw={'title':'鉴定','elapsedMinutes':1,'timeLabel':'清晨','publicOutcome':'听到鉴定结论。','additions':[{'id':'knowledge','name':'结论','type':'lore','description':'鉴定师公开说明的结果。','source':'game','knownBy':['player']}],'learned':['knowledge']}
    result=parse_json(json.dumps(raw,ensure_ascii=False),DirectorOutput)
    assert result['additions']==[] and result['newLore'][0]['id']=='knowledge'
    assert result['newLore'][0]['text']=='鉴定师公开说明的结果。'


def test_delivery_misfiled_in_additions_uses_the_typed_transfer_channel():
    raw={'title':'交付','elapsedMinutes':1,'timeLabel':'清晨','publicOutcome':'交付鉴定书。','additions':[{'id':'doc','name':'鉴定书','type':'item','source':'game'},{'itemId':'doc','fromId':None,'toId':'p','quantity':1,'reason':'写好后交付','visibleTo':['player']}]}
    result=parse_json(json.dumps(raw,ensure_ascii=False),DirectorOutput)
    assert len(result['additions'])==1 and result['transfers'][0]['itemId']=='doc'


def test_possession_confirmation_does_not_create_a_second_unique_document():
    from app.scenes.trades import normalize_receipts
    state={'playerId':'p','entities':{'p':{'inventory':{'doc':1}}}}
    definitions={'entities':[{'id':'doc','type':'item','name':'抄件','stackable':False}]}
    plan={'transfers':[{'itemId':'doc','fromId':None,'toId':'p','quantity':1,'reason':'抄件始终由萧炎持有，无重复转移'}]}
    assert normalize_receipts(plan,state,definitions)['transfers']==[]


def test_existing_unique_items_without_possession_confirmation_still_reach_strict_validation():
    from app.scenes.trades import normalize_receipts
    state={'playerId':'p','entities':{'p':{'inventory':{'doc':1}}}}
    definitions={'entities':[{'id':'doc','type':'item','name':'抄件','stackable':False}]}
    transfer={'itemId':'doc','fromId':None,'toId':'p','quantity':1,'reason':'再复制一张给玩家'}
    assert normalize_receipts({'transfers':[transfer]},state,definitions)['transfers']==[transfer]


def test_newly_written_document_delivery_and_unknown_payee_balance_are_not_invented():
    state={'playerId':'p','entities':{'p':{'inventory':{},'fields':{'金币':57}},'npc':{'inventory':{},'fields':{}}}}
    plan={'additions':[{'id':'doc','type':'item','source':'game','name':'鉴定书'}],'transfers':[{'itemId':'doc','fromId':'npc','toId':'p','quantity':1,'reason':'当场写好并交付鉴定书'}],'resources':[{'entityId':'p','field':'金币','operation':'transfer','counterpartyId':'npc','amount':20,'reason':'鉴定费用'}]}
    from app.scenes.trades import normalize_receipts
    result=normalize_receipts(plan,state,{'entities':[]})
    assert result['transfers'][0]['fromId'] is None
    assert result['resources'][0]['operation']=='spend' and result['resources'][0]['counterpartyId'] is None
    assert state['entities']['npc']['fields']=={}


def test_real_received_certificate_proposal_can_commit_after_format_repair():
    case=json.loads((Path(os.environ.get('PLAYTHROUGH_FIXTURE_DIR','/qa'))/'certificate-regression.json').read_text('utf-8-sig'))
    plan=normalize_plan(parse_json(case['raw'],DirectorOutput),case['state'],case['definition'])
    state,definition,_,_=apply_plan(case['state'],case['definition'],plan,'certificate-test',[],action_kind='dialogue')
    player=state['entities'][state['playerId']]
    assert player['fields']['金币']==37
    certificate=next(e for e in definition['entities'] if e['name']=='谷尼鉴定书')
    assert player['inventory'][certificate['id']]==1
