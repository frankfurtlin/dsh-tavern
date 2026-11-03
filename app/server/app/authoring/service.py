"""Account-owned author settings; new starts freeze them into their checkpoint."""
from copy import deepcopy
import base64, io
from PIL import Image
from sqlalchemy import select
from ..domain import owned, canonical
from ..models import now
from ..errors import Problem
from ..world.models import World
from ..world.schemas import PrepareOutput
from ..world.state import prepared_definition
from .models import AuthorDraft, Publication
from .schemas import AuthorValue, check_value


def default_value(world):
    definition=world.definition
    if not definition.get('entities'):raise Problem('AUTHOR_WORLD_NOT_READY','请先完成世界准备或剧本校对',409)
    raw={k:deepcopy(definition[k]) for k in PrepareOutput.model_fields if k in definition}
    script=definition.get('script',{})
    from .plot import from_world
    value={'plot':from_world(world),'title':world.name,'summary':(definition.get('description') or world.name)[:500],
        'genre':definition['rules'].get('genre','general'),'rules':definition['rules'],'definition':raw,
        'openings':[{'id':'default','title':'故事开场','text':script.get('opening') or definition.get('description') or '观察眼前的处境。',
                     'characterId':script.get('recommendedCharacterId'),'locationId':definition['location']}]}
    config=definition.get('authoring',{})
    value.update({key:deepcopy(config[key]) for key in ('openings','playerFields','statusFields','authorInstructions','fixedActions','suggestedModel','formTitle','statusTitle','statusTheme') if key in config})
    return AuthorValue.model_validate(value).model_dump()


def read_draft(db, world):
    row=db.get(AuthorDraft,world.id)
    value=deepcopy(row.value) if row else default_value(world)
    if row:
        from .plot import from_world
        if 'plot' not in value:value['plot']=from_world(world)
        value=AuthorValue.model_validate(value).model_dump()
    publication=db.scalar(select(Publication).where(Publication.world_id==world.id,Publication.owner_id==world.owner_id))
    return {'worldId':world.id,'version':row.version if row else 0,'value':value,
        'hasSaves':bool(world.active_line_id),'publication':publication_owner(publication) if publication else None}


def publication_owner(row):
    return {'id':row.id,'version':row.version,'state':row.state,'live':row.live,'note':row.note,
        'submittedAuthorVersion':row.submitted.get('authorVersion'), 'publishedAuthorVersion':row.approved.get('authorVersion'),
        'updatedAt':row.updated_at.isoformat()}


def validate_cover(value):
    if not value:return
    prefix,separator,data=value.partition(',')
    if prefix not in ('data:image/png;base64','data:image/jpeg;base64','data:image/webp;base64') or not separator:raise Problem('AUTHOR_COVER_INVALID','封面仅支持 PNG、JPEG 或 WebP',422)
    try:
        raw=base64.b64decode(data,validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('PNG','JPEG','WEBP') or image.width*image.height>16000000 or image.width<32 or image.height<32:raise ValueError()
            image.verify()
    except Exception:raise Problem('AUTHOR_COVER_INVALID','封面无法读取或尺寸无效',422)


def compile_definition(world,value):
    value=AuthorValue.model_validate(value).model_dump()
    definition=prepared_definition(value['definition'],world.source.get('blocks',[]),value['rules'],world.definition)
    authored_blocks=[]
    if value.get('plot'):
        from .plot import compile_plot
        definition,authored_blocks=compile_plot(world,value)
    elif world.definition.get('script'):
        # Keep the executable plot and its settlement ledger. Edited references
        # must still satisfy the same complete script validator.
        from ..scripts.service import validate
        script=deepcopy(world.source.get('script',{}).get('value'))
        if script:
            script['definition']=value['definition'];script['routes']=value['rules'].get('routes',script['routes'])
            _,compiled=validate(script,world,publication=True)
            definition['script']=compiled['script']
        else:definition['script']=deepcopy(world.definition['script'])
    places={e['id'] for e in definition['entities'] if e['type']=='place'}
    people={e['id'] for e in definition['entities'] if e['type']=='character'}
    for opening in value['openings']:
        if not opening['text'].strip() or not opening['title'].strip() or opening.get('characterId') and opening['characterId'] not in people or opening.get('locationId') and opening['locationId'] not in places:
            raise Problem('AUTHOR_OPENING_INVALID','开局需要有效的人物、地点和正文',422)
        if opening.get('characterId') and definition['initial'].get(opening['characterId'],{}).get('alive') is False:raise Problem('AUTHOR_OPENING_INVALID','不能用已死亡的人物开局',422)
    definition['authoring']={k:deepcopy(value.get(k,[] if k=='fixedActions' else '')) for k in ('openings','playerFields','statusFields','authorInstructions','fixedActions','suggestedModel','formTitle','statusTitle','statusTheme')}
    definition['authoring']['sourceBlocks']=authored_blocks
    return definition


def save_draft(db,world,data):
    row=db.scalar(select(AuthorDraft).where(AuthorDraft.world_id==world.id).with_for_update())
    if (row.version if row else 0)!=data.expectedVersion:raise Problem('VERSION_CONFLICT','作者配置已经更新，请载入最新版本；当前输入仍保留',409)
    value=data.value.model_dump();value['rules']['genre']=value['genre'];validate_cover(value['coverDataUrl']);compile_definition(world,value)
    if len(canonical(value).encode())>1600000:raise Problem('AUTHOR_CONFIG_TOO_LARGE','作者配置过大，请减少内容或缩小封面',422)
    if not row:row=AuthorDraft(world_id=world.id,owner_id=world.owner_id,version=1,value=value);db.add(row)
    else:row.version+=1;row.value=value;row.updated_at=now()
    if world.name!=value['title']:world.name=value['title'];world.version+=1
    world.updated_at=now()
    db.flush()
    return read_draft(db,world)


def effective_definition(db,world):
    row=db.get(AuthorDraft,world.id)
    return compile_definition(world,row.value) if row else deepcopy(world.definition)


def configure_start(definition,data):
    config=definition.get('authoring')
    if not config:
        if data.openingId or data.playerValues:raise Problem('AUTHOR_OPENING_INVALID','这个世界没有指定的开局配置',422)
        return definition,data.characterId,data.opening
    opening=next((o for o in config['openings'] if o['id']==data.openingId),None) if data.openingId else config['openings'][0]
    if not opening:raise Problem('AUTHOR_OPENING_INVALID','开局已经更新，请重新选择',422)
    definition=deepcopy(definition)
    if opening.get('locationId'):definition['location']=opening['locationId']
    if data.customName.strip() and any(data.customName.strip().casefold()==e.get('name','').strip().casefold() for e in definition.get('entities',[]) if e['type']=='character'):
        raise Problem('WORLD_IDENTITY_CONFLICT','这个名字属于已有故事人物，请直接选择该人物，或为自定义身份换一个名字',422)
    character=None if data.customName.strip() or data.customIdentity.strip() else data.characterId or opening.get('characterId')
    if character and opening.get('locationId'):
        definition['initial'].setdefault(character,{})['location']=opening['locationId']
    field_ids={f['id'] for f in config['playerFields']}
    if set(data.playerValues)-field_ids:raise Problem('AUTHOR_FIELD_INVALID','开局包含未知字段',422)
    values={}
    definition['authoring']['randomSeed']=__import__('secrets').token_hex(16)
    try:
        for field in config['playerFields']:
            if field['type']=='random':
                if field['id'] in data.playerValues:raise ValueError(field['label']+'由开局随机决定，不能手工指定')
                from hashlib import sha256
                value=field['options'][int(sha256((definition['authoring']['randomSeed']+field['id']).encode()).hexdigest(),16)%len(field['options'])]
            else:value=data.playerValues.get(field['id'],field['defaultValue'])
            check_value(field,value);values[field['id']]=value
    except ValueError as e:raise Problem('AUTHOR_FIELD_INVALID',str(e),422)
    definition['authoring']['playerValues']=values
    definition['authoring']['openingId']=opening['id']
    return definition,character,data.opening.strip() or opening['text']


def initialize_fields(definition,state):
    config=definition.get('authoring',{})
    player=state['playerId']
    state['entities'][player]['fields'].update(config.get('playerValues',{}))
    for field in config.get('statusFields',[]):
        for entity in definition['entities']:
            if entity['id']==player or field['scope']=='characters' and entity['type']=='character':
                default=field['defaultValue']
                if field['type']=='random':
                    from hashlib import sha256
                    seed=config.get('randomSeed','')+field['id']+entity['id']
                    default=field['options'][int(sha256(seed.encode()).hexdigest(),16)%len(field['options'])]
                # This runs only when creating a NEW root. The author's
                # configured initial value overrides an older template value;
                # existing checkpoints never pass through this initializer.
                state['entities'][entity['id']]['fields'][field['id']]=default
    validate_fields(definition,state)


def validate_fields(definition,state):
    config=definition.get('authoring',{})
    try:
        for field in config.get('playerFields',[]):
            check_value(field,state['entities'][state['playerId']].get('fields',{}).get(field['id']))
        for field in config.get('statusFields',[]):
            for entity in definition['entities']:
                if entity['id']==state['playerId'] or field['scope']=='characters' and entity['type']=='character':
                    check_value(field,state['entities'][entity['id']].get('fields',{}).get(field['id']))
    except ValueError as e:raise Problem('AUTHOR_FIELD_INVALID',str(e),422)
