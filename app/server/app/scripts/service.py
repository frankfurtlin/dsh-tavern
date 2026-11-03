"""Author-facing analysis; the entire source stays frozen and attributable."""
from copy import deepcopy
from pathlib import Path
import json
import re
from ..domain import canonical, digest
from ..models import uid
from ..errors import Problem
from ..world.models import World
from ..world.jobs import create_job
from ..world.schemas import WorldRules
from ..world.state import prepared_definition
from .schemas import ScriptOutput

SAMPLE = {'id':'blue-carbuncle','title':'蓝宝石案','author':'阿瑟·柯南·道尔', 'year':1892,
          'sourceUrl':'https://www.gutenberg.org/ebooks/1661', 'license':'Public domain', 'language':'en',
          'description':'一顶旧帽、一只圣诞鹅和失窃宝石。完整短篇原文，拆成可探索的侦探故事。'}
SAMPLES=[SAMPLE,{'id':'hound-baskervilles','title':'巴斯克维尔的猎犬','author':'阿瑟·柯南·道尔','year':1902,
    'sourceUrl':'https://www.gutenberg.org/ebooks/2852','license':'Public domain','language':'en',
    'description':'完整十五章长篇。从伦敦到荒原，调查家族诅咒、隐瞒的身份和猎犬案。'}]


def create_prepared_sample(db, owner, sample_id):
    if sample_id != 'blue-carbuncle':raise Problem('SCRIPT_SAMPLE_NOT_FOUND','没有这个已校对示例',404)
    directory = Path(__file__).parent/'samples'
    text = (directory/'blue-carbuncle.txt').read_text('utf-8')
    value = json.loads((directory/'blue-carbuncle-script.json').read_text('utf-8'))
    blocks = blocks_for(text)
    world = World(id=uid(), owner_id=owner, name='蓝宝石案 · 校对版', source_title=SAMPLE['title'],
        source={'kind':'standalone','schemaVersion':2,'title':SAMPLE['title'],'summary':value['opening'],
                'blocks':blocks,'chapters':source_chapters(blocks,SAMPLE['title']),'referenceBlocks':[],'fullBookReference':False,'anchor':None,
                'contentHash':digest(text),'script':{'status':'review','version':2,'scale':'short',
                    'blockCount':len(blocks),'processedBlocks':len(blocks),'sourceCharacters':len(text),
                    'provenance':{**deepcopy(SAMPLE),'reviewedSample':True,'aftermath':'adaptation'},'value':value}},
        definition={'rules':WorldRules(genre='mystery',pace='short').model_dump(),'version':0}, status='preparing')
    value, definition = validate(value, world, publication=True)
    world.definition = definition;world.source['script']['value'] = value
    db.add(world);db.flush()
    from ..foundation.events import emit
    emit(db, owner, 'sample:' + world.id, 'fact.sample.entered', data={'worldId':world.id,'sampleId':sample_id})
    return {'worldId':world.id,'status':'review','preparedSample':True}


def blocks_for(text):
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text.replace('\r\n','\n')) if p.strip()]
    result = [];chapter='script-full';chapter_count=0
    for paragraph in paragraphs:
        # Contents entries lack the terminal period used by actual English
        # chapter headings. Preserve the dedication/contents as source blocks,
        # without falsely counting them as a chapter a second time.
        heading=re.match(r'^Chapter\s+(?:\d+|[IVXLCDM]+)\.?(?:\s*\n|\s*$)',paragraph,re.I)
        chinese=re.match(r'^第[一二三四五六七八九十百千万两零〇\d]+[章节回]',paragraph)
        if heading or chinese:
            chapter_count+=1;chapter=f'script-chapter-{chapter_count:03}'
        # No truncation: every character belongs to exactly one stable block.
        for offset in range(0, len(paragraph), 1800):
            part = paragraph[offset:offset+1800]
            result.append({'blockId':f'S{len(result)+1:04}', 'chapterId':chapter, 'text':part, 'source':'original'})
    return result


def groups_for(blocks):
    result, group, size = [], [], 0
    for block in blocks:
        count = len(canonical(block).encode())
        if group and size + count > 62000:result.append(group);group=[];size=0
        group.append(block);size += count
    if group:result.append(group)
    if len(result)>50:raise Problem('SCRIPT_SOURCE_LIMIT','文本超过本次可拆解范围，请按完整分卷拆解',422)
    return result


def source_chapters(blocks, title):
    chapters = {}
    for block in blocks:
        entry = chapters.setdefault(block['chapterId'], {'id':block['chapterId'],
            'title':title if block['chapterId']=='script-full' else block['text'].splitlines()[0][:160], 'blocks':[]})
        entry['blocks'].append(block['blockId'])
    return list(chapters.values())


def validate_notes(value, blocks):
    """Only source-backed, explicitly covered batches count as analyzed."""
    allowed={b['blockId']:b['text'] for b in blocks}
    covered=value.get('analyzedBlockIds') if isinstance(value,dict) else None
    if (not isinstance(covered,list) or any(not isinstance(i,str) for i in covered)
            or len(covered)!=len(set(covered)) or set(covered)!=set(allowed)):
        raise Problem('SCRIPT_BATCH_COVERAGE_INVALID','这一批没有确认完整原文覆盖，已完成批次仍保留',422)
    entries=[]
    for key in ('characters','places','items','events','clues'):
        parts=value.get(key)
        if not isinstance(parts,list):raise Problem('SCRIPT_OUTPUT_INVALID','这一批拆解结构不完整',422)
        entries+=parts
    if value.get('ending') is not None:entries.append(value['ending'])
    if not entries:raise Problem('SCRIPT_OUTPUT_INVALID','这一批没有形成可核对的拆解内容',422)
    for entry in entries:
        ids=entry.get('sourceBlockIds') if isinstance(entry,dict) else None
        quote=entry.get('quote') if isinstance(entry,dict) else None
        if (not isinstance(ids,list) or not ids or any(not isinstance(i,str) or i not in allowed for i in ids)
                or not isinstance(quote,str) or not quote.strip()):
            raise Problem('SCRIPT_REFERENCE_INVALID','分批拆解缺少真实来源证据',422)
        evidence=re.sub(r'\s+','', '\n\n'.join(allowed[i] for i in ids))
        if re.sub(r'\s+','',quote) not in evidence:
            raise Problem('SCRIPT_REFERENCE_INVALID','分批拆解引用与原文不符，未计为已完成',422)
    return value


def create(db, owner, data):
    sample = next((s for s in SAMPLES if s['id']==data.sampleId),None)
    text = (Path(__file__).parent/'samples'/f'{sample["id"]}.txt').read_text('utf-8') if sample else data.text.strip()
    blocks = blocks_for(text)
    groups = groups_for(blocks)
    name = data.title.strip() or (sample['title'] if sample else '未命名剧本')
    long_source=len(text)>100000
    rules = WorldRules(genre=data.genre, pace='standard' if long_source else 'short').model_dump()
    source = {'kind':'standalone','schemaVersion':2,'title':name,'summary':'','blocks':blocks,
              'chapters':source_chapters(blocks,name), 'referenceBlocks':[], 'fullBookReference':False, 'anchor':None,
              'contentHash':digest(text), 'script':{'status':'queued', 'provenance':deepcopy(sample) if sample else {'kind':'user-text'},
                'blockCount':len(blocks),'processedBlocks':0,'sourceCharacters':len(text),
                'chapterCount':len({b['chapterId'] for b in blocks if b['chapterId']!='script-full'}),
                'scale':'novel' if long_source else 'short','version':1}}
    world = World(id=uid(), owner_id=owner, name=name, source_title=name, source=source,
                  definition={'rules':rules,'version':0}, status='preparing')
    db.add(world);db.flush()
    return enqueue(db, world)


def enqueue(db, world):
    groups = groups_for(world.source['blocks'])
    from ..models import Task
    prior=db.get(Task,world.prepare_task_id) if world.prepare_task_id else None
    complete=[]
    if (prior and prior.task_type=='world_script_breakdown' and prior.payload.get('worldId')==world.id
            and prior.checkpoint.get('scriptSourceHash')==world.source.get('contentHash')):
        for index,notes in enumerate(prior.checkpoint.get('scriptNotes',[])[:len(groups)]):
            try:validate_notes(notes,groups[index])
            except Problem:break
            complete.append(deepcopy(notes))
    count = 1 if len(groups)==1 else len(groups)-len(complete)+1
    task = create_job(db, world.owner_id, 'world_script_breakdown', {'worldId':world.id}, ['breakdown']*count)
    task.checkpoint={**task.checkpoint,'scriptSourceHash':world.source.get('contentHash'),'scriptNotes':complete}
    world.prepare_task_id=task.id
    world.status='preparing'
    world.source={**world.source, 'script':{**world.source['script'], 'status':'queued', 'taskId':task.id,
        'processedBlocks':sum(len(g) for g in groups[:len(complete)])}}
    return {'worldId':world.id,'taskId':task.id,'status':'preparing'}


def validate(value, world, *, publication=False, authored=False):
    from ..authoring.plot import AuthoredScript
    result = (AuthoredScript if authored else ScriptOutput).model_validate(value).model_dump()
    definition = result['definition']
    # Synopsis is author-facing and includes the solution. Players enter via
    # the non-spoiling opening rather than the full analysis summary.
    definition['description']=result['opening']
    places = {e['id'] for e in definition['entities'] if e['type']=='place'}
    people = {e['id'] for e in definition['entities'] if e['type']=='character'}
    items = {e['id'] for e in definition['entities'] if e['type']=='item'}
    lore = {e['id'] for e in definition['lore']}
    blocks = {b['blockId'] for b in world.source['blocks']}
    objectives = {o['id'] for o in result['objectives']}
    if len(objectives)!=len(result['objectives']) or result['recommendedCharacterId'] not in people:raise Problem('SCRIPT_REFERENCE_INVALID','任务或主角引用无效',422)
    from .settlement_validation import validate_settlement
    validate_settlement(result, people=people, places=places, items=items, lore=lore, blocks=blocks)
    routes=result['routes']
    if any(a not in places or b not in places or a==b for a, paths in routes.items() for b in paths):raise Problem('SCRIPT_ROUTE_INVALID','路线必须连接两个真实地点',422)
    # A disclosed open route is reversible. Locks/one-way passages must be
    # represented in the world rules, not accidentally dropped return edges.
    for a, paths in list(routes.items()):
        for b in paths:
            routes.setdefault(b, [])
            if a not in routes[b]:routes[b].append(a)
    reachable={definition['location']}
    for _ in places:reachable |= {b for a in reachable for b in routes.get(a, [])}
    if reachable != places:raise Problem('SCRIPT_ROUTE_INVALID','有地点无法从开场抵达，请补全路线',422)
    for scene in result['scenes']:
        if scene['placeId'] not in places or not set(scene['characterIds']) <= people or not set(scene['sourceBlockIds']) <= blocks:raise Problem('SCRIPT_REFERENCE_INVALID','场景来源或人物引用无效',422)
    finished=set()
    for _ in objectives:
        for objective in result['objectives']:
            if not set(objective['requires']) <= objectives or objective['id'] in objective['requires'] or not set(objective['sourceBlockIds']) <= blocks:raise Problem('SCRIPT_REFERENCE_INVALID','任务依赖或来源引用无效',422)
            if set(objective['requires']) <= finished:finished.add(objective['id'])
            for c in objective['checks']:
                if c['entityId'] not in people|{'player'}:raise Problem('SCRIPT_REFERENCE_INVALID','任务人物引用无效',422)
                if (c['kind'] in ('visit','location') and c['placeId'] not in places
                        or c['kind'] in ('inventory','acquired','transfer') and c['itemId'] not in items
                        or c['kind']=='knowledge' and c['loreId'] not in lore
                        or c['kind']=='transfer' and c['toId'] not in people
                        or c['kind']=='field' and (not c['field'] or c['value'] is None)
                        or c['kind']=='transition'):
                    raise Problem('SCRIPT_REFERENCE_INVALID','任务验收条件无效；办理回执仅用于结局善后',422)
            for c in objective['checks']:
                if c['kind']=='inventory' and any(t['kind']=='transfer' and t['entityId']==c['entityId'] and t['itemId']==c['itemId'] for t in objective['checks']):
                    raise Problem('SCRIPT_CONDITION_CONFLICT','同一任务不能同时要求持有并交出物品，请将持有条件改为曾获得',422)
    if finished!=objectives:raise Problem('SCRIPT_DEPENDENCY_CYCLE','任务依赖存在循环',422)
    if any(not set(e['requires']) <= objectives or not set(e['sourceBlockIds']) <= blocks for e in result['endings']):raise Problem('SCRIPT_REFERENCE_INVALID','结局引用了不存在的任务或原文',422)
    if len(result['endings'])>1:
        signatures=[]
        for ending in result['endings']:
            if not ending['checks']:raise Problem('SCRIPT_ENDING_AMBIGUOUS','多个结局需要各自明确的选择条件',422)
            signature=canonical({'requires':sorted(ending['requires']),'checks':ending['checks']})
            if signature in signatures:raise Problem('SCRIPT_ENDING_AMBIGUOUS','不同结局不能使用相同条件',422)
            signatures.append(signature)
    for ending in result['endings']:
        for c in ending['checks']:
            if c['entityId'] not in people|{'player'} or c['kind'] not in ('field','knowledge') or c['kind']=='field' and (not c['field'] or c['value'] is None) or c['kind']=='knowledge' and c['loreId'] not in lore:raise Problem('SCRIPT_REFERENCE_INVALID','结局条件需要真实人物、线索和明确决定',422)
        if len(result['endings'])>1 and not any(c['kind']=='field' for c in ending['checks']):raise Problem('SCRIPT_ENDING_AMBIGUOUS','不同结局需要明确的决定条件',422)
    rules = {**world.definition['rules'], 'routes':routes}
    compiled = prepared_definition(definition, world.source['blocks'], rules)
    compiled['script'] = {k:deepcopy(result[k]) for k in ('scenes','objectives','endings','recommendedCharacterId','opening','conservedItemIds')}
    # A person who appears later in the source must not be standing in the
    # opening room. Schedule their first reachable appearance from the scene
    # cards and the prerequisites of their first essential clue.
    schedule=[]
    for person in people:
        if compiled['initial'].get(person,{}).get('location'):continue
        scenes=[s for s in result['scenes'] if person in s['characterIds']]
        held={l['id'] for l in definition['lore'] if person in l['knownBy']}
        goal=next((o for o in result['objectives'] if any(c['kind']=='knowledge' and c['loreId'] in held for c in o['checks'])),None)
        if goal and scenes:
            schedule.append({'entityId':person,'placeId':scenes[0]['placeId'],'requires':goal['requires']})
        elif goal:
            raise Problem('SCRIPT_CLUE_UNREACHABLE','关键线索的知情人没有可抵达的出场地点',422)
    compiled['script']['castSchedule']=schedule
    # Every quest field has a declared initial value. The director never needs
    # to invent unregistered progress attributes to make the ending reachable.
    for o in result['objectives']+result['endings']:
        for c in o['checks']:
            if c['kind']=='field':
                who=result['recommendedCharacterId'] if c['entityId']=='player' else c['entityId']
                fields=compiled['initial'].setdefault(who, {}).setdefault('fields', {})
                fields.setdefault(c['field'], '')
    issues = review_issues(result, compiled, world)
    if publication and issues:
        raise Problem('SCRIPT_OPENING_INVALID', '请先修正开场：' + '；'.join(issues), 422)
    return result, compiled


def review_issues(value, compiled, world=None):
    """Keep editable analysis, but never publish a pre-completed opening."""
    from .plot import advance
    player = value['recommendedCharacterId']
    state = {'playerId':player, 'location':compiled['location'],
             'discovered':[compiled['location']], 'entities':deepcopy(compiled['initial'])}
    advance(state, compiled, [])
    completed = set(state['plot']['completed'])
    issues = ['任务「' + o['title'] + '」在开场就已满足，请修改开场知识或验收条件'
              for o in value['objectives'] if o['id'] in completed]
    issues += ['任务「' + o['title'] + '」只检查开场地点，请改为实际调查或行动的凭据'
               for o in value['objectives'] if o['id'] not in completed and
               all(c['kind']=='visit' and c['placeId']==compiled['location'] for c in o['checks'])]
    if world and world.source.get('script',{}).get('scale')=='novel':
        chapter_for={b['blockId']:b['chapterId'] for b in world.source['blocks']}
        chapters=set(chapter_for.values())-{'script-full'}
        if not chapters:issues.append('长篇章节未识别，请先补全章节边界再发布')
        covered={chapter_for[b] for scene in value['scenes'] for b in scene['sourceBlockIds'] if b in chapter_for}
        missing=sorted(chapters-covered)
        if missing:issues.append('长篇场景尚未覆盖 '+str(len(missing))+' 章：'+', '.join(missing))
        if len(value['scenes'])<12:issues.append('完整长篇至少需要十二个可游玩场景，当前只有 '+str(len(value['scenes']))+' 个')
        if len(value['objectives'])<8:issues.append('完整长篇至少需要八个递进任务，当前只有 '+str(len(value['objectives']))+' 个')
    return issues


def view(world):
    script=world.source.get('script')
    if not script:raise Problem('SCRIPT_NOT_FOUND','这个世界没有拆解记录',404)
    issues=[]
    if script.get('value'):
        value,compiled=validate(script['value'],world)
        issues=review_issues(value,compiled,world)
    return {'worldId':world.id,'name':world.name,'worldVersion':world.version,'worldStatus':world.status,'hasSaves':bool(world.active_line_id), 'reviewIssues':issues, **{k:deepcopy(v) for k,v in script.items() if k!='raw'},
            'source':{'blocks':world.source['blocks'],'title':world.source_title}}
