"""Hand-authored Douluo side adventure, with account-local state and IDs."""
KEY = 'douluo-forest-v1'
TITLE = '斗罗大陆·星斗试炼'
DESCRIPTION = '与唐三、小舞进入星斗大森林外围，辨认魂兽踪迹，完成一次有去有回的森林试炼。'
OPENING = (
    '晨雾还停在星斗大森林的树冠间。你叫林澈，是随队参加试炼的学员，武魂青藤，魂力十八级。'
    '这是一次勘察与协作训练，并非猎取魂环。\n\n'
    '营地中央，一张路线图被石块压在木桌上。赵无极站在桌边，指着通往溪谷的林道：'
    '“只查外围的标记。遇到不明魂兽就退回来，不许擅自进深林。”\n\n'
    '唐三蹲在营地出口旁，察看一截被折断的枝条。小舞站在他身侧，望向林道：'
    '“先弄清是谁留下的痕迹，别急着动手。”\n\n'
    '你腰间挂着唯一一枚铜制回营信标，行囊里收着一卷空白路线图。'
    '营地向前连着外围林道，林道再通向浅溪。要先问清安排，还是亲自检查入口的足迹？'
)
INSTRUCTIONS = (
    '《斗罗大陆》设定下原创的独立试炼支线，不复述原著章节，不强制重演原著事件。'
    '以林澈的当前身份和眼前可见事实推进。唐三谨慎分析、蓝银草用于探路与控制；'
    '小舞敏捷直率、尊重魂兽生命；赵无极负责营地与安全边界。人物保持独立判断，'
    '不得凭空泄露角色未公开的身世或秘密。本轮不是猎取魂环任务：不能自动获得魂环、'
    '魂骨、升级或击杀任何魂兽。魂力消耗、伤势、物品交接和位置移动必须进入正式状态。'
    '玩家初始持有唯一回营信标与路线图；NPC不能同时持有它们。'
    '通路是营地↔外围林道↔浅溪，两段移动不能跳过；离开的人不能隔空参与对话。'
    '允许观察、对话、谨慎探索和主动撤回；不得替玩家完成调查、上交或选择结局。'
)


def setting():
    people = ['learner', 'tangsan', 'xiaowu', 'zhaowuji']
    entities = [
        {'id': 'camp', 'name': '星斗外围营地', 'type': 'place',
         'description': '森林边缘的开阔营地，中央木桌压着路线图，三顶帐篷靠树排列。营地出口直接连着外围林道，清晨薄雾，地面干燥可行。'},
        {'id': 'trail', 'name': '外围林道', 'type': 'place',
         'description': '连接营地与浅溪的低矮林道，树干上留有旧路线刻痕，路旁有倒木和新鲜爪印；没有建筑或室内空间。'},
        {'id': 'creek', 'name': '林间浅溪', 'type': 'place',
         'description': '林道尽头的浅溪，水面清澈，圆石、蓝银草和一段倒木沿着溪岸分布；远处密林不属于本次可通行范围。'},
        {'id': 'learner', 'name': '林澈', 'type': 'character',
         'description': '参加外围试炼的学员，武魂青藤，十八级、一枚黄色魂环。深青色短外衣，黑发束起，腰间挂铜制回营信标。',
         'traits': '愿意学习，行动谨慎，不替同伴作决定', 'knownFacts': ['briefing']},
        {'id': 'tangsan', 'name': '唐三', 'type': 'character',
         'description': '史莱克学院学员，蓝银草武魂。蓝色便装，黑发，腰间系着收纳腰带。此刻在营地出口蹲着察看断枝。',
         'traits': '冷静细致，先观察再行动，重视同伴安全', 'knownFacts': ['briefing', 'safe_route']},
        {'id': 'xiaowu', 'name': '小舞', 'type': 'character',
         'description': '史莱克学院学员，柔骨兔武魂。粉色便装，长发束成蝎子辫。此刻站在唐三旁，留意林道的动静。',
         'traits': '直率敏捷，关心同伴，不赞成无意义地伤害魂兽', 'knownFacts': ['briefing']},
        {'id': 'zhaowuji', 'name': '赵无极', 'type': 'character',
         'description': '带队老师，身材魁梧，褐色练功服，双手空着，站在营地中央的木桌旁。武魂大力金刚熊。',
         'traits': '说话直接，留守营地照应队伍，安全优先', 'knownFacts': ['briefing', 'safe_route']},
        {'id': 'beacon', 'name': '铜制回营信标', 'type': 'item',
         'description': '林澈腰间的一枚铜制信标，标着营地集合位置。唯一一枚，撤回或完成勘察后应亲手交回带队老师。'},
        {'id': 'map', 'name': '试炼路线图', 'type': 'item',
         'description': '林澈行囊里的一卷空白路线图，只能记录实际到达并确认的地形和足迹，不能预知未探索区域。'},
    ]
    for entity in entities:
        entity.update(source='user', knownBy=people if entity['type'] != 'item' else ['learner'])
    initial = {key: {'alive': True, 'location': 'camp', 'inventory': {}, 'fields': {},
                     'known': ['briefing'], 'relations': {}} for key in people}
    initial['learner'].update(inventory={'beacon': 1, 'map': 1},
        fields={'武魂': '青藤', '魂力等级': 18, '魂环': '一枚黄色魂环', '体力': 100, '魂力余量': 100},
        relations={'tangsan': '同行学员，愿意向他请教', 'xiaowu': '同行学员', 'zhaowuji': '带队老师'})
    initial['tangsan']['known'].append('safe_route')
    initial['zhaowuji']['known'].append('safe_route')
    return {'title': TITLE, 'description': OPENING, 'timeLabel': '清晨，试炼开始前',
        'location': 'camp', 'entities': entities, 'initial': initial,
        'lore': [
            {'id': 'briefing', 'title': '外围试炼安排', 'text': '只沿营地、外围林道和浅溪开展勘察；不进入深林，不主动猎杀魂兽。若无法确认安全，可以带信标撤回并如实报告。',
             'entityIds': people + ['beacon'], 'knownBy': people, 'source': 'user', 'triggers': ['试炼', '任务', '信标']},
            {'id': 'safe_route', 'title': '断枝与溪边足迹', 'text': '林道断枝上的刮痕方向朝向浅溪；溪岸的两组爪印属于经过的不同小型魂兽，并非围攻队伍的迹象。先确认方向和数量，沿外侧通路观察，不追入密林。唐三可以在被询问时解释自己的观察方法。',
             'entityIds': ['tangsan', 'zhaowuji', 'trail', 'creek'], 'knownBy': ['tangsan', 'zhaowuji'],
             'source': 'user', 'triggers': ['断枝', '爪印', '判断', '观察方法']},
        ], 'unresolved': ['是否先请教唐三，再去溪边核对踪迹？', '什么时候撤回营地，怎样向老师报告？'],
        'narrativeStyle': '清晰、有画面感的魂师冒险；用可观察线索与明确状态推进，战斗不自动升级，保留玩家选择。'}


def configure(definition):
    """Attach only references belonging to this freshly allocated world."""
    by_name = {entity['name']: entity['id'] for entity in definition['entities']}
    camp, trail, creek = (by_name[n] for n in ('星斗外围营地', '外围林道', '林间浅溪'))
    if not definition['rules'].get('routes'):
        definition['rules']['routes'] = {camp: [trail], trail: [camp, creek], creek: [trail]}
    definition['rules']['customRules'] = (definition['rules'].get('customRules', '') +
        '\n试炼仅限营地、外围林道、浅溪；按实际通路移动。魂力、位置和物品交接以状态为准，'
        '不能凭空升级、获得魂环或同时持有唯一信标。不得自动替玩家调查、交接或选择结局。').strip()
    definition['initial'][by_name['林澈']]['fields']['author_trial_choice'] = '未决定'
    definition['authoring'] = {
        'openings': [{'id': 'camp', 'title': '清晨营地', 'text': OPENING,
                      'characterId': by_name['林澈'], 'locationId': camp}],
        'authorInstructions': INSTRUCTIONS,
        'fixedActions': [
            {'id': 'observe', 'label': '查看营地与林道入口', 'kind': 'observe', 'message': '我先观察营地、出口的断枝与脚印，不离开营地、不消耗魂力、不交出信标。'},
            {'id': 'ask', 'label': '向唐三请教', 'kind': 'dialogue', 'message': '我问唐三如何判断断枝与爪印的方向，先听完他的分析，不擅自行动。'},
            {'id': 'briefing', 'label': '确认试炼安排', 'kind': 'dialogue', 'message': '我向赵无极确认试炼的范围、撤回条件和信标交接要求。'},
        ],
        'statusFields': [{'id': 'author_trial_choice', 'label': '试炼选择', 'type': 'select',
                          'defaultValue': '未决定', 'options': ['未决定', '完成勘察', '主动撤回']}],
        'playerFields': [], 'statusTitle': '魂师状态', 'statusTheme': 'scroll',
        'formTitle': '进入星斗试炼', 'suggestedModel': '',
    }
    from ..authoring.schemas import CustomField
    definition['authoring']['statusFields'] = [CustomField.model_validate(field).model_dump()
        for field in definition['authoring']['statusFields']]
    return definition


def configure_author_value(world, value):
    definition = value['definition'];ids = {e['name']: e['id'] for e in definition['entities']}
    lore = {e['title']: e['id'] for e in definition['lore']}
    source = ['AUTHOR-CONFIG']
    value.update(coverTheme='forest', tags=['斗罗大陆', '魂师冒险', '森林试炼'],
                 authorInstructions=INSTRUCTIONS,
                 introduction=DESCRIPTION+' 你扮演新学员林澈，从清晨营地出发；可以观察、对话、探索或选择撤回。每个账号保存自己的独立进度。')
    value['plot'] = {
        'synopsis': '在唐三、小舞与赵无极的陪伴下，查清外围踪迹，选择完成勘察或主动撤回，并亲手交还唯一回营信标。',
        'recommendedCharacterId': ids['林澈'], 'opening': OPENING,
        'scenes': [{'id': key, 'title': name, 'summary': text, 'placeId': ids[name],
                    'characterIds': [e['id'] for e in definition['entities'] if e['type'] == 'character'], 'sourceBlockIds': source}
                   for key, name, text in [('camp', '星斗外围营地', '确认安排、询问同伴，结束时返回这里交接。'),
                                           ('trail', '外围林道', '辨认断枝与爪印，不把推测当作事实。'),
                                           ('creek', '林间浅溪', '核对实际踪迹，决定是否返回。')]],
        'objectives': [
            {'id': 'understand', 'title': '学会辨认踪迹', 'description': '向知情人请教，理解断枝与爪印的判断依据。',
             'requires': [], 'checks': [{'kind': 'knowledge', 'loreId': lore['断枝与溪边足迹']}], 'sourceBlockIds': source},
            {'id': 'survey', 'title': '抵达浅溪勘察', 'description': '沿外围林道抵达浅溪，亲自核对沿途环境。',
             'requires': ['understand'], 'checks': [{'kind': 'visit', 'placeId': ids['林间浅溪']}], 'sourceBlockIds': source},
            {'id': 'handoff', 'title': '交还回营信标', 'description': '返回营地，如实报告并亲手把唯一信标交给赵无极。',
             'requires': [], 'checks': [{'kind': 'transfer', 'entityId': 'player', 'itemId': ids['铜制回营信标'], 'toId': ids['赵无极']}], 'sourceBlockIds': source},
        ],
        'endings': [
            {'id': 'survey-complete', 'title': '林间归途', 'description': '学会辨认踪迹，抵达溪边核对后安全返回；信标完成正式交接，试炼有了交代。',
             'selectionDescription': '勘察和交接实际完成后，由你明确选择“完成勘察”，再确认试炼结束。', 'requires': ['understand', 'survey', 'handoff'],
             'checks': [{'kind': 'field', 'entityId': 'player', 'field': 'author_trial_choice', 'value': '完成勘察'}],
             'settlementSteps': [{'id': 'returned', 'title': '营地确认', 'description': '人回到营地，信标确实交回老师手中。',
                  'checks': [{'kind': 'location', 'entityId': 'player', 'placeId': ids['星斗外围营地']},
                             {'kind': 'inventory', 'entityId': ids['赵无极'], 'itemId': ids['铜制回营信标']}]}]},
            {'id': 'safe-withdrawal', 'title': '谨慎也是答案', 'description': '你选择结束勘察并如实说明当前所知，返回营地交还信标；不把未完成的勘察说成成功。',
             'selectionDescription': '由你明确选择“主动撤回”；仍需亲自回营并交还信标。', 'requires': ['handoff'],
             'checks': [{'kind': 'field', 'entityId': 'player', 'field': 'author_trial_choice', 'value': '主动撤回'}],
             'settlementSteps': [{'id': 'returned', 'title': '撤回确认', 'description': '玩家安全回营，信标由赵无极保管，没有凭空完成探索或获得魂环。',
                  'checks': [{'kind': 'location', 'entityId': 'player', 'placeId': ids['星斗外围营地']},
                             {'kind': 'inventory', 'entityId': ids['赵无极'], 'itemId': ids['铜制回营信标']}]}]},
        ], 'conservedItemIds': [ids['铜制回营信标'], ids['试炼路线图']],
    }
    return value
