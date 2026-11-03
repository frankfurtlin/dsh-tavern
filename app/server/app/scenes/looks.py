from ..model_budget import image_budget_exceeded
"""World-owned opening portraits; no game line or checkpoint is required."""
from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.orm import defer
from ..domain import owned
from ..errors import Problem
from ..models import uid
from ..world.models import World, WorldLine
from ..world.service import world_result
from .models import CharacterLook, SceneImage, SceneProfile
from .characters import character_descriptor, character_key, art_receipt

PENDING=('queued','running','saving')

def library_image(row):
    return row.line_id is None and row.checkpoint_id is None and row.descriptor.get('kind')=='character' and row.descriptor.get('portraitLibrary') is True

def context_active(row,world,line):
    return bool(world and world.status=='active' and (library_image(row) or line and line.status=='active' and line.world_id==world.id))

def authorize_image(db,owner,row):
    world=owned(db,World,row.world_id,owner)
    if library_image(row):
        if world.status=='deleted':raise Problem('WORLD_NOT_FOUND','世界不存在',404)
        return world,None
    from ..world.service import require_line
    return require_line(db,owner,row.world_id,row.line_id)

def roster(db,world,include_current=False,line_id=None):
    cast=world_result(db,world,include_definition=True).get('characters',[])
    if include_current and (line_id or world.active_line_id):
        from ..world.models import WorldCheckpoint
        from ..world.state import public_view
        line=db.get(WorldLine,line_id or world.active_line_id)
        if line_id and (not line or line.owner_id!=world.owner_id or line.world_id!=world.id or line.status=='deleted'):
            raise Problem('WORLD_LINE_NOT_FOUND','世界线不存在',404)
        point=db.get(WorldCheckpoint,line.head_checkpoint_id) if line and line.owner_id==world.owner_id else None
        if point:
            visible=[e for e in public_view(point.state,point.definition)['entities'] if e['type']=='character']
            cast={e['id']:e for e in cast}|{e['id']:e for e in visible}
            return list(cast.values())
    return cast

def look_row(db,owner,world,entity):
    return db.scalar(select(CharacterLook).where(CharacterLook.owner_id==owner,CharacterLook.world_id==world,CharacterLook.entity_id==entity))

def selected_images(db,owner,world,entities):
    if not entities:return {}
    rows=db.execute(select(CharacterLook,SceneImage).join(SceneImage,CharacterLook.selected_scene_id==SceneImage.id).where(
        CharacterLook.owner_id==owner,CharacterLook.world_id==world,CharacterLook.entity_id.in_(entities),
        SceneImage.owner_id==owner,SceneImage.world_id==world,SceneImage.status=='ready'))
    result={look.entity_id:image for look,image in rows if image.descriptor.get('kind')=='character' and image.descriptor.get('entityId')==look.entity_id}
    from .artworks import selected_artworks
    return {**result,**selected_artworks(db,owner,world,entities)}

def library(db,owner,world_id,line_id=None):
    world=owned(db,World,world_id,owner)
    if world.status=='deleted':raise Problem('WORLD_NOT_FOUND','世界不存在',404)
    cast=roster(db,world,include_current=True,line_id=line_id)
    rows=list(db.scalars(select(SceneImage).where(SceneImage.owner_id==owner,SceneImage.world_id==world_id,
        SceneImage.descriptor['kind'].astext=='character').order_by(SceneImage.created_at.desc(),SceneImage.id.desc())))
    selected=selected_images(db,owner,world_id,[e['id'] for e in cast])
    choices={r.entity_id:r for r in db.scalars(select(CharacterLook).where(CharacterLook.owner_id==owner,CharacterLook.world_id==world_id))}
    from .models import CharacterArtwork
    from .artworks import receipt
    pairs=list(db.scalars(select(CharacterArtwork).options(defer(CharacterArtwork.full_image),defer(CharacterArtwork.avatar_image)).where(CharacterArtwork.owner_id==owner,CharacterArtwork.world_id==world_id).order_by(CharacterArtwork.created_at.desc(),CharacterArtwork.id.desc())))
    paired_sources={r.descriptor.get('sourceSceneId') for r in pairs}
    return {'worldId':world_id,'characters':[{
        'entityId':e['id'],'name':e['name'],'description':e.get('description',''),
        'version':choices[e['id']].version if e['id'] in choices else 0,
        'selectedId':selected[e['id']].id if e['id'] in selected else None,
        'versions':[receipt(row) for row in pairs if row.entity_id==e['id']]+[{**art_receipt(row),'createdAt':row.created_at.isoformat(),'appearance':row.descriptor.get('visualOverrides',{})}
                    for row in rows if row.descriptor.get('entityId')==e['id'] and row.id not in paired_sources]
    } for e in cast]}

def queue_look(db,owner,world_id,entity_id,appearance,regenerate=False,line_id=None):
    from .visual_style import visual_context
    from ..authoring.service import effective_definition
    world=owned(db,World,world_id,owner,lock=True)
    from ..authoring.trials import text_only
    text_only(world)
    if world.status!='active':raise Problem('WORLD_ARCHIVED','请先恢复这个世界',409)
    entity=next((e for e in roster(db,world,include_current=True,line_id=line_id) if e['id']==entity_id),None)
    if not entity:raise Problem('CHARACTER_NOT_FOUND','这个世界没有此角色',404)
    profile=db.scalar(select(SceneProfile).where(SceneProfile.owner_id==owner).with_for_update())
    if not profile or profile.status!='active':raise Problem('SCENE_NOT_CONFIGURED','请先配置图像模型',409)
    descriptor=character_descriptor(entity,world.name)
    overrides={key:value.strip() for key,value in appearance.items() if value.strip()}
    definition=effective_definition(db,world)
    if line_id:
        from ..world.models import WorldCheckpoint
        definition=db.get(WorldCheckpoint,db.get(WorldLine,line_id).head_checkpoint_id).definition
    descriptor.update(portraitLibrary=True,portraitVersion=4,visualOverrides=overrides,**visual_context(world,definition))
    if overrides:descriptor['appearance']=(descriptor.get('appearance','')+'\nExplicit visual customisation (takes priority over default appearance): '+
        '; '.join(key+': '+value for key,value in overrides.items()))
    key=character_key(descriptor,world_id,profile.version)
    # One pending portrait per role, including requests from another tab.
    pending=db.scalar(select(SceneImage).where(SceneImage.owner_id==owner,SceneImage.world_id==world_id,
        SceneImage.descriptor['kind'].astext=='character',SceneImage.descriptor['entityId'].astext==entity_id,
        SceneImage.status.in_(PENDING)).order_by(SceneImage.created_at.desc()).limit(1))
    if pending:return art_receipt(pending)
    previous=db.scalar(select(SceneImage).where(SceneImage.owner_id==owner,SceneImage.world_id==world_id,
        SceneImage.scene_hash==key).order_by(SceneImage.generation.desc()).limit(1))
    if previous and not regenerate:return art_receipt(previous)
    day=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    count=db.scalar(select(func.count()).select_from(SceneImage).where(SceneImage.owner_id==owner,SceneImage.created_at>=day))
    if image_budget_exceeded(profile,count):raise Problem('SCENE_DAILY_LIMIT','今天的出图次数已用完',429)
    if db.scalar(select(func.count()).select_from(SceneImage).where(SceneImage.owner_id==owner,SceneImage.status.in_(PENDING)))>=3:
        raise Problem('SCENE_QUEUE_FULL','请等当前图片生成完成',409)
    if not look_row(db,owner,world_id,entity_id):db.add(CharacterLook(owner_id=owner,world_id=world_id,entity_id=entity_id))
    row=SceneImage(id=uid(),owner_id=owner,world_id=world_id,line_id=None,checkpoint_id=None,
        profile_id=profile.id,profile_version=profile.version,scene_hash=key,generation=previous.generation+1 if previous else 1,
        status='queued',descriptor=descriptor,image={})
    db.add(row);db.flush();return art_receipt(row)

def select_look(db,owner,world_id,entity_id,scene_id,expected_version,crop=None,line_id=None):
    from .artworks import choose
    return choose(db,owner,world_id,entity_id,scene_id,expected_version,crop,line_id=line_id)



def adopt_first(db,row):
    if not library_image(row):return
    if row.descriptor.get('portraitVersion',0)>=4:return
    choice=look_row(db,row.owner_id,row.world_id,row.descriptor['entityId'])
    if choice and not choice.selected_scene_id:
        choice.selected_scene_id=row.id;choice.version+=1

def scene_looks(view,db,owner,world):
    visible=[e['id'] for e in view['characters'] if view.get('characterVisibility',{}).get(e['id'])=='visible']
    return selected_images(db,owner,world,visible)
