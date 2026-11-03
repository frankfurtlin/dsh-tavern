"""Complete a missing payment only when registered prices and a paid receipt agree."""
import re
from uuid import uuid5, NAMESPACE_URL


def continue_consumed_work(plan, parent, action):
    """A continuation cannot consume the same committed batch twice."""
    player=parent.state['playerId'];owner=parent.state['entities'][player]
    progress=' '.join(str(v) for v in owner.get('fields',{}).values())
    if not re.search('正在炼制|炼制中|正在制作|制作中',progress):return plan
    if not re.search('继续|这.?炉|本炉|收火|凝液|装瓶',action) or re.search('再炼一炉|另起一炉|重新开炉|新的一炉',action):return plan
    if not any(e.get('type')=='item' and e.get('source')=='game' for e in plan.get('additions',[])):return plan
    consumed={(c['itemId'],c['quantity']) for c in parent.changes if c.get('field')=='inventory' and c.get('fromId')==player and c.get('toId') is None and re.search('投入|炼制|制作',c.get('reason',''))}
    plan['transfers']=[t for t in plan.get('transfers',[]) if not (t.get('fromId')==player and t.get('toId') is None and (t.get('itemId'),t.get('quantity')) in consumed and owner.get('inventory',{}).get(t.get('itemId'),0)==0 and re.search('投入|炼制|制作',t.get('reason','')))]
    return plan


def normalize_receipts(plan, state, definition):
    """Canonicalize explicit receipts without fabricating a price or balance."""
    entities=state.get('entities',{});registered={e['id']:e for e in definition.get('entities',[])}
    kept=[];created={e['id']:e for e in plan.get('additions',[]) if e.get('type')=='item' and e.get('source')=='game'}
    balances = {key: dict(value.get('inventory', {})) for key, value in entities.items()}
    for transfer in plan.get('transfers',[]):
        item=registered.get(transfer.get('itemId'),{});reason=transfer.get('reason','')
        # A document is never the powder bought from its custodian. The receipt
        # explicitly creates a new sample; keep the original with its owner.
        if item.get('type')=='item' and re.search('凭据|账簿|文书',item.get('name','')) and re.search('购得|购买|支付|买下',reason) and re.search('药粉样品',reason):
            sample=uuid5(NAMESPACE_URL,'sample:'+transfer['itemId']+':'+str(transfer.get('toId'))+':'+reason).hex
            plan.setdefault('additions',[]).append({'id':sample,'name':'同批药粉样品','type':'item','source':'game','description':reason[:1000],'stackable':False,'knownBy':[state['playerId']]})
            transfer={**transfer,'itemId':sample,'fromId':None}
        sender,recipient=transfer.get('fromId'),transfer.get('toId');ident=transfer.get('itemId');quantity=transfer.get('quantity',0)
        if sender is None and recipient and ident not in created and item.get('type')=='item' and not item.get('stackable',False) and entities.get(recipient,{}).get('inventory',{}).get(ident,0)>=quantity and re.search('无重复转移|始终.{0,10}持有|随身保管|已交付|已经持有',reason):
            # An explicit possession confirmation is not a second creation.
            continue
        if ident in created and sender and not entities.get(sender,{}).get('inventory',{}).get(ident):
            transfer={**transfer,'fromId':None}
        if re.search('归还|退回|返还',reason) and sender and recipient and balances.get(sender,{}).get(ident,0)==0 and balances.get(recipient,{}).get(ident,0)>=quantity:
            continue
        kept.append(transfer)
        if sender:balances.setdefault(sender,{})[ident] = balances.get(sender,{}).get(ident,0)-quantity
        if recipient:balances.setdefault(recipient,{})[ident] = balances.get(recipient,{}).get(ident,0)+quantity
    plan['transfers']=kept
    operations=plan.get('resources',[]);removed=set()
    for debit in operations:
        if debit.get('operation')=='transfer' and debit.get('counterpartyId') in entities and type(entities[debit['counterpartyId']].get('fields',{}).get(debit['field'])) not in (int,float):
            debit.update(operation='spend',counterpartyId=None)
        if debit.get('operation')!='spend' or debit.get('counterpartyId'):continue
        matches=[credit for credit in operations if credit.get('operation')=='earn' and not credit.get('counterpartyId') and credit.get('entityId')!=debit.get('entityId') and credit.get('field')==debit.get('field') and credit.get('amount')==debit.get('amount') and re.search('收取|收到|收下|费用|价款',credit.get('reason',''))]
        if len(matches)!=1:continue
        credit=matches[0];recipient=credit.get('entityId')
        if recipient not in entities:continue
        removed.add(id(credit))
        if type(entities[recipient].get('fields',{}).get(debit['field'])) in (int,float):
            debit.update(operation='transfer',counterpartyId=recipient)
    plan['resources']=[operation for operation in operations if id(operation) not in removed]
    return plan


def complete_priced_trade(plan, state, definition):
    player=state.get('playerId');entities=state.get('entities',{})
    if not player or player not in entities:return plan
    definitions={e['id']:e for e in definition.get('entities',[])}
    sales={}
    for transfer in plan.get('transfers',[]):
        seller=transfer.get('fromId');item=definitions.get(transfer.get('itemId'),{})
        if transfer.get('toId')!=player or seller==player or seller not in entities or item.get('type')!='item':continue
        sales.setdefault(seller,[]).append(transfer)
    for seller,transfers in sales.items():
        fields=entities[seller].get('fields',{});total=0
        for transfer in transfers:
            price=fields.get(definitions[transfer['itemId']]['name']+'单价');quantity=transfer.get('quantity')
            if type(price) not in (int,float) or price<0 or type(quantity) is not int or quantity<1:break
            total+=price*quantity
        else:
            if not 0<total<=1_000_000_000:continue
            for currency in ('金币','money','currency'):
                if currency not in fields or currency not in entities.get(player,{}).get('fields',{}):continue
                operations=plan.get('resources',[])
                debits=[op for op in operations if op.get('entityId')==player and op.get('field')==currency]
                credits=[op for op in operations if op.get('entityId')==seller and op.get('field')==currency]
                # A matched, conserved payment is a transfer, not an unearned
                # reward. Canonicalize only one exact pair for this real sale.
                if len(debits)==len(credits)==1 and debits[0].get('operation')=='spend' and credits[0].get('operation')=='earn' and debits[0].get('amount')==credits[0].get('amount')==total and not debits[0].get('counterpartyId') and not credits[0].get('counterpartyId'):
                    plan['resources']=[op for op in operations if op is not credits[0]]
                    debits[0].update(operation='transfer',counterpartyId=seller)
                    continue
                # Preserve negotiated prices and never add a second debit.
                if debits:continue
                amount=str(int(total)) if total==int(total) else str(total)
                receipt=None
                for event in plan.get('events',[]):
                    if event.get('type')!='交易' or event.get('entityId')!=seller:continue
                    text=event.get('description','')
                    if any(word in text for word in ('没有','尚未','还没','未收','若','如果','假如','赠送','免费','报价','拟')):continue
                    paid_amount=re.escape(amount)+r'\s*'+re.escape(currency)+r'(?![\d.])'
                    direct=r'(?:收下|收到|收取|收讫|付清|支付)\s*'+paid_amount
                    counted=r'(?:点清|清点|数清|数过)\s*'+paid_amount+r'[^。！？\n]{0,80}(?:收入|收进|收好|入账)'
                    if re.search(direct+'|'+counted,text):
                        receipt=text;break
                if receipt:
                    plan.setdefault('resources',[]).append({'entityId':player,'field':currency,'operation':'transfer',
                        'counterpartyId':seller,'amount':total,'reason':receipt[:400],'visibleTo':['player']})
    return plan
