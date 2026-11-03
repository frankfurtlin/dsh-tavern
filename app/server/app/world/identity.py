"""Player identity is an execution constraint, not a character to meet twice."""
import re
from ..errors import Problem


def actor_contract(state, definition, *, visible_entities=None):
    player = next(e for e in definition['entities'] if e['id'] == state['playerId'])
    entities = definition['entities'] if visible_entities is None else visible_entities
    npcs = [{'id': e['id'], 'name': e['name']} for e in entities
            if e['type'] == 'character' and e['id'] != player['id']
            and state['entities'].get(e['id'], {}).get('location') == state['location']
            and state['entities'].get(e['id'], {}).get('alive') is not False]
    return {'player': {'id': player['id'], 'name': player['name'], 'aliases': player.get('aliases', [])},
            'presentNPCs': npcs, 'perspective': 'second-person',
            'rule': '你、玩家、本次行动者始终是 player 指定的人物。原作主角不是默认玩家。'
                    'player 的人物资料描述你本人，不能再把同一人物写成迎接你、与你说话的另一个NPC。'
                    'presentNPCs 不含玩家本人；为空时此处没有其他在场人物。人物到场或离开须同步账本位置。'}


def identity_conflict(text, contract):
    """Reject explicit self encounters, while allowing self reference and thoughts.

    This intentionally does not infer every mention of the player's name to be
    an NPC; literary third person, quoted names, and reflection remain valid.
    The semantic reviewer checks less literal perspective mistakes as well.
    """
    player = contract['player']
    names = [player['name'], *player.get('aliases', [])]
    for name in filter(None, names):
        actor = re.escape(name)
        if re.search(r'【台词[：:]\s*' + actor + r'\s*】', text):
            return True
        if re.search(r'你(?:在[^。！？\n]{0,24})?(?:遇见|遇到|碰见|碰到|走近|朝着|向)\s*' + actor, text):
            return True
        if re.search(actor + r'[^。！？\n“”「」"]{0,65}(?:(?:朝|向|对|打量|招呼|看着|望着|等着|等待|等)\s*你|见你(?:停下|走来|到来|过来|走近))', text):
            return True
    return False


def check_identity(text, contract):
    if identity_conflict(text, contract):
        raise Problem('WORLD_PLAYER_IDENTITY_CONFLICT', '这次把玩家本人误写成了另一个角色，已保留正文供查看')


def check_plan_identity(plan, contract):
    for text in [plan.get('publicOutcome', ''), plan.get('summary', ''), *plan.get('critical', [])]:
        check_identity(text, contract)
