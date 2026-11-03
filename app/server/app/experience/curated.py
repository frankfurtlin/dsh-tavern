"""Reviewed library versions use the normal publication and personal-copy path."""
from copy import deepcopy
from fastapi import Depends, Request
from sqlalchemy import select, text
from ..db import get_db
from ..security import current_user
from ..models import uid
from ..domain import idempotent
from ..foundation.core import require_admin, config
from ..foundation.models import Config
from ..world.models import World, WorldLine
from ..world.schemas import CreateWorld
from ..world.service import create_world
from ..authoring.models import AuthorDraft, Publication
from ..authoring.service import default_value, compile_definition
from ..errors import Problem
from .api import router

SAMPLES = (
    ('blue-carbuncle', '蓝宝石案', '悬疑调查', '公版原作改编；三种结局及善后。'),
    ('original-mystery-v1', '钟楼失约', '原创悬疑', '从一封便笺开始，调查书店里的秘密。'),
    ('original-fantasy-v1', '雪关同行', '原创冒险', '带着路牌与同行者，共同决定雪关的旅程。'),
    ('douluo-forest-v1', '斗罗大陆·星斗试炼', '魂师冒险', '与唐三、小舞探索星斗大森林外围，辨认踪迹，完成试炼或安全撤回。'),
)


def complete_original(world, value):
    """Give curated originals an achievable short arc, not only a premise."""
    definition=value['definition']; people=[e for e in definition['entities'] if e['type']=='character']
    player,companion=people[:2]; item=next(e for e in definition['entities'] if e['type']=='item')
    clue,secret=definition['lore'][:2]; place=definition['location']; source=['AUTHOR-CONFIG']
    value['plot']={'synopsis':'弄清约定背后的隐情，再亲手完成一次有凭据的交接。',
        'recommendedCharacterId':player['id'],'opening':definition['description'],
        'scenes':[{'id':'meeting','title':'灯下相逢','summary':'在眼前的处境中确认约定。','placeId':place,
                   'characterIds':[p['id'] for p in people],'sourceBlockIds':source}],
        'objectives':[
            {'id':'understand','title':'听见另一半经过','description':'请对方说明尚未说出的缘由，不替对方作决定。','requires':[],
             'checks':[{'kind':'knowledge','loreId':secret['id']}],'sourceBlockIds':source},
            {'id':'handoff','title':'亲手交接','description':'了解缘由后，自愿把'+item['name']+'交给'+companion['name']+'。','requires':['understand'],
             'checks':[{'kind':'transfer','entityId':'player','itemId':item['id'],'toId':companion['id']}],'sourceBlockIds':source}],
        'endings':[{'id':'agreement-kept','title':'约定有了回音','description':'隐情已经说明，信物完成了正式交接；今后的选择仍属于两人。',
                    'selectionDescription':'决定完成约定后，仍需核对信物的实际交接结果。',
                    'requires':['understand','handoff'],'checks':[],
                    'settlementSteps':[{'id':'receipt','title':'交接确认','description':'信物确实在对方手中，玩家不再持有。',
                                        'checks':[{'kind':'inventory','entityId':companion['id'],'itemId':item['id']}]}]}],
        'conservedItemIds':[item['id']]}
    value['fixedActions']=[{'id':'observe','label':'检查约定','message':'我只查看手中的'+item['name']+'与已知的约定，不交出物品。','kind':'observe'},
                           {'id':'ask','label':'询问缘由','message':'我请'+companion['name']+'说明这次约定背后尚未说出的缘由，愿意先听完，不擅自行动。','kind':'dialogue'}]
    value['authorInstructions']+=' 这是一个可完成的短篇：知情人可以在玩家真诚询问时逐步自愿说明隐情；没有说出的隐情仍不能让玩家凭空知道。结局和交接必须以正式状态为依据。'


@router.post('/curated/publish')
def publish_curated(request: Request, user=Depends(current_user), db=Depends(get_db)):
    require_admin(db, user)
    def publish():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('story-reviewed-library'))"))
        existing = dict(config(db, 'reviewed-library'))
        for key, title, _, description in SAMPLES:
            prior=db.get(Publication,existing[key]) if existing.get(key) else None
            if prior and (key=='blue-carbuncle' or prior.approved.get('value',{}).get('plot')): continue
            if key == 'blue-carbuncle':
                from ..scripts.service import create_prepared_sample
                result = create_prepared_sample(db, user.id, key)
                world = db.get(World, result['worldId']); world.status = 'active'
            else:
                result = create_world(db, user.id, CreateWorld(templateId=key))
                world = db.get(World, result['worldId'])
            world.name = title
            value = default_value(world); value['title'] = title; value['summary'] = description
            value['introduction'] = description+' 每个玩家的存档独立保存。'
            if key == 'blue-carbuncle': value['coverTheme'] = 'blue'
            elif key == 'douluo-forest-v1':
                from ..world.douluo import configure_author_value
                configure_author_value(world, value)
            else: complete_original(world,value)
            draft = AuthorDraft(world_id=world.id, owner_id=user.id, version=1, value=value)
            db.add(draft)
            frozen = {'value': deepcopy(value), 'definition': compile_definition(world, value), 'authorVersion': 1,
                      'attribution': ('柯南·道尔《蓝宝石案》公版原作；产品改编善后。' if key == 'blue-carbuncle' else
                                      '《斗罗大陆》设定的原创互动支线；场景与对话为独立创作。' if key == 'douluo-forest-v1' else '故事之间原创互动模板。')}
            row = Publication(id=uid(), owner_id=user.id, world_id=world.id, submitted=frozen,
                              approved=deepcopy(frozen), state='published', live=True, reviewer_id=user.id,
                              note='已校对案例与原创模板；通过内置版本检查。')
            db.add(row); existing[key] = row.id
        row = db.get(Config, 'reviewed-library')
        if row: row.value = existing
        else: db.add(Config(key='reviewed-library', value=existing))
        db.flush()
        return {'published': len(existing)}
    return idempotent(db, user.id, 'experience:publish-curated', request.headers.get('idempotency-key'), {}, publish)


def personal(db, owner, key, publication):
    query = select(World).where(World.owner_id == owner, World.status == 'active')
    if key == 'blue-carbuncle':
        query = query.where((World.source['publicationId'].astext == publication) |
                            (World.source['script']['provenance']['id'].astext == key))
    else: query = query.where(World.source['publicationId'].astext == publication)
    return db.scalar(query.order_by(World.updated_at.desc(), World.id).limit(1))


@router.get('/curated')
def curated(user=Depends(current_user), db=Depends(get_db)):
    from ..authoring.api import live_query
    mapping = config(db, 'reviewed-library'); items = []
    for key, title, genre, description in SAMPLES:
        publication = mapping.get(key)
        live = db.scalar(live_query().where(Publication.id == publication)) if publication else None
        world = personal(db, user.id, key, publication or '')
        line = db.get(WorldLine, world.active_line_id) if world and world.active_line_id else None
        items.append({'key': key, 'title': title, 'genre': genre, 'description': description,
                      'publicationId': publication if live else None, 'available': bool(live or world),
                      'worldId': world.id if world else None, 'lineId': line.id if line and line.status == 'active' else None})
    return {'items': items}


@router.post('/curated/{key}/open')
def open_curated(key: str, request: Request, user=Depends(current_user), db=Depends(get_db)):
    if key not in {r[0] for r in SAMPLES}: raise Problem('RESOURCE_NOT_FOUND', '没有这个精选故事', 404)
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': 'curated:'+user.id+':'+key})
    publication = config(db, 'reviewed-library').get(key)
    existing = personal(db, user.id, key, publication or '')
    if existing:
        db.commit()
        line = db.get(WorldLine, existing.active_line_id) if existing.active_line_id else None
        return {'worldId': existing.id, 'lineId': line.id if line and line.status == 'active' else None, 'reused': True}
    if not publication: raise Problem('CURATED_NOT_PUBLISHED', '精选故事正在准备，请联系管理员或稍后再试', 409)
    from ..authoring.api import copy
    return copy(publication, request, user, db)
