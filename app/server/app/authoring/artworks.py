"""Publish immutable image defaults; private reader changes never reach the author."""
from copy import deepcopy
from ..scenes.models import CharacterArtwork,CharacterLook
from ..scenes.artworks import create_pair
from ..scenes.looks import selected_images
from ..scenes.service import image_bytes
from ..models import uid

def freeze(db,world,definition):
    cast=[e['id'] for e in definition.get('entities',[]) if e['type']=='character']
    result={}
    for entity,art in selected_images(db,world.owner_id,world.id,cast).items():
        if not isinstance(art,CharacterArtwork):
            art=create_pair(db,world.owner_id,world,entity,image_bytes(art),None,'ai_generated',
                'AI 生成的角色形象，随作品公开使用。',{**art.descriptor,'sourceSceneId':art.id})
        result[entity]=art.id
    return result

def clone(db,origin,world,mapping):
    cast={e['id'] for e in world.definition.get('entities',[]) if e['type']=='character'}
    for entity,identity in mapping.items():
        art=db.get(CharacterArtwork,identity)
        if entity not in cast or not art or art.owner_id!=origin.owner_id or art.world_id!=origin.id or art.entity_id!=entity:continue
        descriptor=deepcopy(art.descriptor);descriptor.pop('sourceSceneId',None)
        pair=CharacterArtwork(id=uid(),owner_id=world.owner_id,world_id=world.id,entity_id=entity,source_kind=art.source_kind,
            license_note=art.license_note,descriptor=descriptor,image=deepcopy(art.image),full_image=art.full_image,avatar_image=art.avatar_image)
        db.add(pair);db.flush()
        db.add(CharacterLook(owner_id=world.owner_id,world_id=world.id,entity_id=entity,selected_artwork_id=pair.id,version=1))
    db.flush()
