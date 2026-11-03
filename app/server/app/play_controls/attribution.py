"""Names and roles from committed, reader-visible receipts, never prose claims."""
from ..world.state import visible_to
import re


def requests_participation(message):
    """Answer direct actor questions from receipts; keep hypotheticals free."""
    hypothetical=bool(re.search(r'假如|假设|如果|想象|\b(if|imagine|suppose|hypothetical|would)\b',message,re.I))
    verify=bool(re.search(r'实际|记录|到底|是谁|核实|有无|\b(actually|really|records?|verify|who did)\b',message,re.I))
    actor=r'谁|哪位|亲手|亲自|经手|在场|目击|你本人|你做过|你去过|是不是你|是你|你当时|你曾|你把|你交|我们|咱们|\b(who|you|yourself|we)\b'
    action=r'交还|归还|送还|递交|交接|押送|说明|供词|结局|交给|善后|办理|做过|去过|\b(handed|handoff|return(?:ed)?|deliver(?:ed)?|escort(?:ed)?|witness(?:ed)?|statement|settlement)\b'
    return (not hypothetical or verify) and bool(re.search(actor,message,re.I)) and bool(re.search(action,message,re.I))


def participants(state, definition, character_id, events=()):
    registry = {e['id']: e for e in definition['entities']}
    player_id = state['playerId']
    def person(identity):
        return {'name': registry[identity]['name'], 'isCharacter': identity == character_id,
                'isPlayer': identity == player_id}
    def known(identity):
        # The explicitly visible receipt identifies its own participants even
        # when the player has not separately visited their entity card.
        return identity in registry
    transfers = []
    for receipt in state.get('plot', {}).get('transfers', []):
        if not visible_to(receipt.get('visibleTo', []), player_id): continue
        if not all(known(receipt.get(k)) for k in ('fromId', 'toId', 'itemId')): continue
        transfers.append({'from': person(receipt['fromId']), 'to': person(receipt['toId']),
                          'itemName': registry[receipt['itemId']]['name'], 'quantity': receipt['quantity']})
    decisions = []
    for receipt in state.get('plot', {}).get('transitionReceipts', []):
        if not any(visible_to(e.get('visibleTo', []), player_id) for e in receipt.get('effects', [])) or not known(receipt.get('authorityId')): continue
        # Authority is the person empowered to approve, not evidence that they
        # performed every resulting handoff, accompanied the player or witnessed it.
        transition = next((t for e in definition.get('script', {}).get('endings', [])
                           for t in e.get('transitions', []) if t['id'] == receipt['transitionId']), None)
        if transition:
            decisions.append({'authority': person(receipt['authorityId']), 'title': transition['title']})
    evidence_actions=[]
    for event in events:
        if event.type!='knowledge_shared' or not known(event.entity_id) or not visible_to(event.visible_to,player_id):continue
        evidence_actions.append({'actor':person(event.entity_id),'description':event.data.get('description') or event.data.get('text','')})
    return {'player': person(player_id), 'character': person(character_id), 'itemTransfers': transfers,
            'decisions': decisions,'evidenceActions':evidence_actions,
            'rule': 'Only from/to identify handoff participants. Authority only identifies approval. '
                    'Neither presence, outcome, a step title nor old chat proves personal participation. '
                    'The reader is outside the story and is not the player.'}


def verified_reply(source, message=''):
    """A rejected personal claim is replaced from receipts, never invented."""
    facts=source.get('participants',{})
    sentences=[]
    all_transfers=facts.get('itemTransfers',[])
    mentioned=[t for t in all_transfers if t['itemName'] in message]
    transfers=(mentioned or all_transfers)[-4:]
    for transfer in transfers:
        sentences.append(transfer['itemName']+'由'+transfer['from']['name']+'交给'+transfer['to']['name']+'。')
        if not transfer['from']['isCharacter'] and not transfer['to']['isCharacter']:
            sentences.append('这次交接不是由我亲手办理的，我也不能凭结果声称自己在场。')
    own=[a['description'] for a in facts.get('evidenceActions',[]) if a['actor']['isCharacter']]
    if own:sentences.append('我确实参与过的说明是：'+'；'.join(s.rstrip('。； ') for s in own[-2:])+'。')
    approved=[d['title'] for d in facts.get('decisions',[]) if d['authority']['isCharacter']]
    if approved:sentences.append('我作为批准者参与的是：'+'；'.join(approved)+'。这不等于我亲手完成了其他办理。')
    if not sentences:sentences.append('我不能把没有亲历依据的事情说成自己做过。我们可以谈谈想法，但不能凭一句话改写已经发生的事。')
    return '\n\n'.join(sentences)
