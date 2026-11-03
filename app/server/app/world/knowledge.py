"""Canonicalise explicit sharing receipts; never infer learning from prose."""
from copy import deepcopy
import re


def document_channel(event, plan, base, definition):
    """Canonicalise a recipient's explicit reading of delivered evidence.

    Some directors label the recipient's confirmation as outgoing sharing.
    Correct only a real, lore-linked handoff from the already informed player;
    never grant knowledge based on a narrative mention or a future delivery.
    The ordinary ledger/contact validators still validate the entire proposal.
    """
    player = base.get('playerId')
    recipient = event.get('entityId')
    lore_ids = set(event.get('loreIds', []))
    entities = base.get('entities', {})
    registry = {e['id']: e for e in definition.get('entities', [])}
    lore = {l['id']: l for l in definition.get('lore', [])}
    if (not player or recipient == player or not lore_ids or
            registry.get(recipient, {}).get('type') != 'character' or
            event.get('dueMinute') is not None or event.get('requiresDecision') or
            not re.search(r'查阅|阅读|读过|核对|核查', event.get('description', '')) or
            not set(event.get('visibleTo', [])) <= {'player', player, 'entity:' + player}):
        return
    def known(person):
        values = set(entities.get(person, {}).get('known', []))
        if person == player:values.update(base.get('known', []))
        values.update(key for key, row in lore.items() if person in row.get('knownBy', []) or
                      'all' in row.get('knownBy', []) or person == player and 'player' in row.get('knownBy', []))
        return values
    if lore_ids <= known(recipient) or not lore_ids <= known(player):return
    explicit = {key for update in plan.get('updates', []) if update.get('entityId') == recipient and
                update.get('field') == 'known' and isinstance(update.get('value'), list) for key in update['value']}
    if not lore_ids <= explicit:return
    delivered = {t.get('itemId') for t in plan.get('transfers', []) if t.get('fromId') == player and
                 t.get('toId') == recipient and type(t.get('quantity')) is int and t['quantity'] > 0 and
                 entities.get(player, {}).get('inventory', {}).get(t.get('itemId'), 0) >= t['quantity'] and
                 registry.get(t.get('itemId'), {}).get('type') == 'item'}
    for key in lore_ids:
        delivered &= set(lore.get(key, {}).get('entityIds', []))
    if len(delivered) != 1:return
    event['type'] = 'knowledge_observed'
    event['entityId'] = next(iter(delivered))
    event['visibleTo'] = list(dict.fromkeys([*event.get('visibleTo', []), 'entity:' + recipient]))


def normalize_knowledge(plan, base, definition):
    plan = deepcopy(plan)
    aliases = {'知识传授':'knowledge_shared', '知识分享':'knowledge_shared',
               '传授知识':'knowledge_shared', '分享知识':'knowledge_shared',
               'knowledge_share':'knowledge_shared'}
    for event in plan.get('events', []):
        event['type'] = aliases.get(event['type'], event['type'])
        if event['type'] != 'knowledge_shared':continue
        if event.get('loreIds'):
            document_channel(event, plan, base, definition)
            continue
        source = event.get('entityId')
        known = set(base.get('entities', {}).get(source, {}).get('known', []))
        known.update(l['id'] for l in definition.get('lore', []) if source in l.get('knownBy', []))
        # Both statements must already be explicit in the model plan: a
        # sharing event from a known source AND learning this exact lore ID.
        # The normal validator still checks contact and audience afterwards.
        event['loreIds'] = [key for key in plan.get('learned', []) if key in known]
    return plan
