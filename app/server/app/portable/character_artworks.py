"""Typed, licensed image files and exact world-local role remapping."""
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
import io
from PIL import Image
from sqlalchemy import select
from ..scenes.models import CharacterArtwork,CharacterLook,SceneImage
from ..scenes.artworks import prepare_pair,MAX_IMAGE_BYTES
from ..scenes.service import image_bytes
from ..world.models import World,WorldCheckpoint
from .errors import ArchiveError

def collect(db,owner,selected,world_ids):
    # A removed author role or deleted branch must not prevent backing up the
    # remaining world. Keep art only for roles retained in settings or saves.
    roles={w.id:{e['id'] for e in w.definition.get('entities',[]) if e['type']=='character'} for w in selected.get(World,[])}
    for point in selected.get(WorldCheckpoint,[]):
        roles.setdefault(point.world_id,set()).update(e['id'] for e in point.definition.get('entities',[]) if e['type']=='character')
    artworks=list(db.scalars(select(CharacterArtwork).where(CharacterArtwork.owner_id==owner,CharacterArtwork.world_id.in_(world_ids))))
    owned_artworks={a.id:a for a in artworks}
    by_source={a.descriptor.get('sourceSceneId'):a.id for a in artworks}
    scenes=db.scalars(select(SceneImage).where(SceneImage.owner_id==owner,SceneImage.world_id.in_(world_ids),SceneImage.status=='ready',SceneImage.descriptor['kind'].astext=='character'))
    for row in scenes:
        if row.descriptor.get('entityId') not in roles.get(row.world_id,set()):continue
        if row.id in by_source:continue
        try:body,avatar,meta=prepare_pair(image_bytes(row))
        except Exception as error:raise ArchiveError('BACKUP_CHARACTER_IMAGE_INVALID') from error
        descriptor={k:deepcopy(row.descriptor[k]) for k in ('name','description','visualOverrides','appearance') if k in row.descriptor}
        artworks.append(SimpleNamespace(id=row.id,owner_id=owner,world_id=row.world_id,entity_id=row.descriptor['entityId'],source_kind='ai_generated',
            license_note='AI 生成角色形象；仅恢复已完成图片，不重新调用模型。',descriptor={**descriptor,'kind':'character','entityId':row.descriptor['entityId']},image=meta,full_image=body,avatar_image=avatar,created_at=row.created_at))
        by_source[row.id]=row.id
    choices=[]
    for row in db.scalars(select(CharacterLook).where(CharacterLook.owner_id==owner,CharacterLook.world_id.in_(world_ids))):
        if row.selected_artwork_id:
            art=owned_artworks.get(row.selected_artwork_id)
            if not art or art.world_id!=row.world_id or art.entity_id!=row.entity_id:raise ArchiveError('BACKUP_CHARACTER_ROLE_INVALID')
        if row.entity_id not in roles.get(row.world_id,set()):continue
        choices.append(SimpleNamespace(id=row.id,owner_id=owner,created_at=row.created_at,world_id=row.world_id,entity_id=row.entity_id,
            selected_artwork_id=row.selected_artwork_id or by_source.get(row.selected_scene_id),version=row.version))
    selected[CharacterArtwork]=[a for a in artworks if a.entity_id in roles.get(a.world_id,set())];selected[CharacterLook]=choices

def encode(writer,row):
    result={}
    for key,raw in [('full',row.full_image),('avatar',row.avatar_image)]:
        if not raw or len(raw)>MAX_IMAGE_BYTES:raise ArchiveError('BACKUP_CHARACTER_IMAGE_INVALID')
        digest=sha256(raw).hexdigest();path='assets/'+digest+'.webp';target=writer.root/path
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            writer.files.append({'path':path,'kind':'asset','mediaType':'image/webp','size':len(raw),'sha256':digest,'license':row.license_note})
        result[key]={'path':path,'sha256':digest}
    return result

def decode(record,root,files):
    blobs=record.get('artworkBlobs')
    if not isinstance(blobs,dict) or set(blobs)!={'full','avatar'}:raise ArchiveError('BACKUP_CHARACTER_IMAGE_INVALID')
    raw={}
    for key,blob in blobs.items():
        if not isinstance(blob,dict) or set(blob)!={'path','sha256'}:raise ArchiveError('BACKUP_CHARACTER_IMAGE_INVALID')
        entry=files.get(blob['path'])
        if not entry or entry['kind']!='asset' or entry['mediaType']!='image/webp' or entry['sha256']!=blob['sha256'] or entry['size']>MAX_IMAGE_BYTES:raise ArchiveError('BACKUP_CHARACTER_IMAGE_INVALID')
        raw[key]=(root/blob['path']).read_bytes()
        if sha256(raw[key]).hexdigest()!=blob['sha256']:raise ArchiveError('BACKUP_CHARACTER_IMAGE_INVALID')
    meta=record['fields']['image'];descriptor=record['fields']['descriptor']
    if record['fields']['source_kind'] not in ('ai_generated','user_upload') or not record['fields']['license_note'].strip():raise ArchiveError('BACKUP_CHARACTER_IMAGE_INVALID')
    if not isinstance(meta,dict) or not isinstance(descriptor,dict) or descriptor.get('kind')!='character' or descriptor.get('entityId')!=record['fields']['entity_id']:raise ArchiveError('BACKUP_CHARACTER_IMAGE_INVALID')
    if meta.get('sha256')!=blobs['full']['sha256'] or meta.get('avatarSha256')!=blobs['avatar']['sha256']:raise ArchiveError('BACKUP_CHARACTER_IMAGE_INVALID')
    try:
        with Image.open(io.BytesIO(raw['full'])) as image:
            if image.format!='WEBP' or getattr(image,'n_frames',1)!=1 or min(image.size)<32 or max(image.size)>8192 or image.width*image.height>16*1024*1024 or image.size!=(meta['width'],meta['height']):raise ValueError()
            image.verify()
        with Image.open(io.BytesIO(raw['avatar'])) as image:
            if image.format!='WEBP' or getattr(image,'n_frames',1)!=1 or image.size!=(512,512):raise ValueError()
            image.verify()
        crop=meta['avatarCrop'];x,y,w,h=(crop[k] for k in ('x','y','width','height'))
        if set(crop)!={'x','y','width','height'} or any(type(v) is not int for v in crop.values()) or min(x,y)<0 or w!=h or w<1 or x+w>meta['width'] or y+h>meta['height']:raise ValueError()
    except (ValueError,KeyError,TypeError,OSError):raise ArchiveError('BACKUP_CHARACTER_IMAGE_INVALID')
    return {'full_image':raw['full'],'avatar_image':raw['avatar']}

def validate(rows):
    for row in rows.get('character_artwork',{}).values():
        world=rows.get('world',{}).get(row.world_id)
        ids={e['id'] for e in world.definition.get('entities',[]) if e['type']=='character'} if world else set()
        for point in rows.get('checkpoint',{}).values():
            if point.world_id==row.world_id:ids.update(e['id'] for e in point.definition.get('entities',[]) if e['type']=='character')
        if row.entity_id not in ids or row.descriptor.get('entityId')!=row.entity_id:raise ArchiveError('BACKUP_CHARACTER_ROLE_INVALID')
    for choice in rows.get('character_look',{}).values():
        if type(choice.version) is not int or not 0<=choice.version<2147483647:raise ArchiveError('BACKUP_CHARACTER_ROLE_INVALID')
        if not choice.selected_artwork_id:continue
        art=rows.get('character_artwork',{}).get(choice.selected_artwork_id)
        if not art or art.world_id!=choice.world_id or art.entity_id!=choice.entity_id:raise ArchiveError('BACKUP_CHARACTER_ROLE_INVALID')
