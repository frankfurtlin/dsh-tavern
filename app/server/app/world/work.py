"""Durable crafting batches; consumption is recorded once across any number of turns."""
from copy import deepcopy
import re
from ..errors import Problem


def prepare_work(plan, base, path=()):
    result = deepcopy(plan)
    batches = deepcopy(base.get('work', {}))
    player = base['playerId']
    # Legacy saves recorded consumption in immutable checkpoints. Recover the
    # active batch from the entire ancestry, not only the immediately prior turn.
    if not batches:
        for cp in path:
            progress = ' '.join(str(v) for v in cp.state['entities'].get(player, {}).get('fields', {}).values())
            inputs = [dict(itemId=c['itemId'], quantity=c['quantity']) for c in (cp.changes or [])
                      if c.get('field') == 'inventory' and c.get('fromId') == player and c.get('toId') is None and re.search('投入|炼制|制作', c.get('reason', ''))]
            if inputs and re.search('正在炼制|炼制中|正在制作|制作中', progress):
                batches = {cp.id: {'id': cp.id, 'ownerId': player, 'title': '进行中的制作', 'status': 'active', 'inputs': inputs, 'outputs': []}}
            elif any(c.get('field') == 'inventory' and c.get('toId') == player and re.search('炼成|成品|装瓶|制作完成', c.get('reason', '')) for c in (cp.changes or [])):
                for batch in batches.values():batch['status'] = 'complete'
    operations = result.get('work', [])
    products = {e['id'] for e in result.get('additions', []) if e['type'] == 'item' and e['source'] == 'game'}
    active = [b for b in batches.values() if b['ownerId'] == player and b['status'] == 'active']
    if not operations and len(active) == 1 and products:
        batch = active[0]
        # Exact input tuple and an existing committed batch authorize reuse.
        # No zero-inventory heuristic may turn a new craft into free products.
        consumed = {(v['itemId'], v['quantity']) for v in batch['inputs']}
        repeated = [t for t in result.get('transfers', []) if t.get('fromId') == player and t.get('toId') is None and (t['itemId'], t['quantity']) in consumed and re.search('投入|炼制|制作', t['reason'])]
        if repeated:
            result['transfers'] = [t for t in result['transfers'] if t not in repeated]
            operations = [{'id': batch['id'], 'operation': 'complete', 'title': batch['title'], 'inputs': [], 'outputs': [{'itemId': t['itemId'], 'quantity': t['quantity']} for t in result['transfers'] if t['itemId'] in products and t.get('toId') == player]}]
            result['work'] = operations
    for operation in operations:
        ident, kind = operation['id'], operation['operation']
        existing = batches.get(ident)
        if kind == 'start':
            if existing:raise Problem('WORLD_WORK_INVALID', '这批制作已开始，请继续原批次')
            if not operation['inputs']:raise Problem('WORLD_WORK_INVALID', '制作需要明确投入材料')
            for item in operation['inputs']:
                if not any(t['itemId'] == item['itemId'] and t.get('fromId') == player and t.get('toId') is None and t['quantity'] == item['quantity'] for t in result['transfers']):
                    raise Problem('WORLD_WORK_INVALID', '材料投入需要实际消耗记录')
            batches[ident] = {**operation, 'ownerId': player, 'status': 'active'}
        else:
            if not existing or existing['ownerId'] != player or existing['status'] != 'active':raise Problem('WORLD_WORK_INVALID', '没有可继续的制作批次')
            if operation['inputs']:raise Problem('WORLD_WORK_INVALID', '同一批次不能再次投入已消耗的材料')
            consumed = {(i['itemId'], i['quantity']) for i in existing['inputs']}
            result['transfers'] = [t for t in result['transfers'] if not (t.get('fromId') == player and t.get('toId') is None and (t['itemId'], t['quantity']) in consumed)]
            if kind == 'complete':
                if not operation['outputs']:raise Problem('WORLD_WORK_INVALID', '成品需要实际入账')
                for item in operation['outputs']:
                    if not any(t['itemId'] == item['itemId'] and t.get('toId') == player and t['quantity'] == item['quantity'] for t in result['transfers']):raise Problem('WORLD_WORK_INVALID', '成品尚未交付到背包')
            existing.update(status='complete' if kind == 'complete' else 'cancelled' if kind == 'cancel' else 'active', outputs=operation['outputs'])
    return result, batches
