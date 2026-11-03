"""Image-only changes: never call a world action or mutate canon checkpoints."""
import base64,io
from copy import deepcopy
from hashlib import sha256
from PIL import Image,ImageOps
from sqlalchemy import select
from sqlalchemy.orm import defer
from ..domain import owned
from ..errors import Problem
from ..models import uid
from ..world.models import World
from .models import CharacterArtwork,CharacterLook,SceneImage

MAX_IMAGE_BYTES=3*1024*1024

def prepare_pair(data,crop=None):
    if not data or len(data)>MAX_IMAGE_BYTES:raise Problem('CHARACTER_IMAGE_LIMIT','图片需不超过 3 MiB',422)
    try:
        with Image.open(io.BytesIO(data)) as original:
            if original.format not in ('PNG','JPEG','WEBP') or getattr(original,'n_frames',1)!=1 or max(original.size)>8192 or original.width*original.height>16*1024*1024:raise ValueError()
            original.load();oriented=ImageOps.exif_transpose(original)
            image=oriented.convert('RGBA' if 'A' in oriented.getbands() or 'transparency' in oriented.info else 'RGB')
        if min(image.size)<32:raise ValueError()
        w,h=image.size
        if crop is None:
            side=max(1,min(w,int(h*.30)))
            crop={'x':max(0,(w-side)//2),'y':min(h-side,max(0,int(h*.03))),'width':side,'height':side}
        if set(crop)!={'x','y','width','height'} or any(type(v) is not int for v in crop.values()):raise ValueError()
        x,y,cw,ch=(crop[k] for k in ('x','y','width','height'))
        if min(x,y)<0 or cw!=ch or cw<1 or x+cw>w or y+ch>h:raise ValueError()
        full=io.BytesIO();image.save(full,format='WEBP',quality=94,method=4)
        face=image.crop((x,y,x+cw,y+ch)).resize((512,512),Image.Resampling.LANCZOS)
        avatar=io.BytesIO();face.save(avatar,format='WEBP',quality=90,method=4)
        body,head=full.getvalue(),avatar.getvalue()
        if len(body)>MAX_IMAGE_BYTES:raise ValueError()
        return body,head,{'width':w,'height':h,'mime':'image/webp','avatarCrop':crop,'sha256':sha256(body).hexdigest(),'avatarSha256':sha256(head).hexdigest(),'presentation':'portrait',
            'transparent':image.mode=='RGBA' and image.getchannel('A').getextrema()[0]<255}
    except (ValueError,OSError,Image.DecompressionBombError):raise Problem('CHARACTER_CROP_INVALID','图片或头像裁切无效，请选择图内的正方形区域',422)

def receipt(row):
    return {'id':row.id,'entityId':row.entity_id,'status':'ready','errorCode':None,
        'imageUrl':'/api/world/v1/character-artworks/'+row.id+'/image',
        'avatarUrl':'/api/world/v1/character-artworks/'+row.id+'/avatar',
        'width':row.image['width'],'height':row.image['height'],'avatarCrop':deepcopy(row.image['avatarCrop']),
        'sourceKind':row.source_kind,'createdAt':row.created_at.isoformat(),'appearance':deepcopy(row.descriptor.get('visualOverrides',{})),
        'transparent':row.image.get('transparent',False)}

def create_pair(db,owner,world,entity,data,crop,source_kind,license_note,descriptor):
    body,avatar,meta=prepare_pair(data,crop)
    safe={k:deepcopy(descriptor[k]) for k in ('name','description','visualOverrides','appearance','sourceSceneId') if k in descriptor}
    row=CharacterArtwork(id=uid(),owner_id=owner,world_id=world.id,entity_id=entity,source_kind=source_kind,
        license_note=license_note[:1000],descriptor={**safe,'kind':'character','entityId':entity},image=meta,full_image=body,avatar_image=avatar)
    db.add(row);db.flush();return row

def choose(db,owner,world_id,entity_id,identity,expected_version,crop=None,line_id=None):
    from .looks import roster,look_row
    world=owned(db,World,world_id,owner,lock=True)
    if world.status!='active':raise Problem('WORLD_ARCHIVED','请先恢复这个世界',409)
    if entity_id not in {e['id'] for e in roster(db,world,include_current=True,line_id=line_id)}:raise Problem('CHARACTER_NOT_FOUND','角色不存在',404)
    selection=look_row(db,owner,world_id,entity_id)
    if (selection.version if selection else 0)!=expected_version:raise Problem('CHARACTER_LOOK_CHANGED','形象已在另一页面修改，请刷新后选择',409)
    row=db.get(CharacterArtwork,identity)
    if row:
        if row.owner_id!=owner or row.world_id!=world_id or row.entity_id!=entity_id:raise Problem('CHARACTER_LOOK_INVALID','请选择这个角色的形象',404)
        if crop is not None and crop!=row.image['avatarCrop']:
            row=create_pair(db,owner,world,entity_id,row.full_image,crop,row.source_kind,row.license_note,row.descriptor)
    else:
        from .service import image_bytes
        scene=owned(db,SceneImage,identity,owner)
        if scene.world_id!=world_id or scene.status!='ready' or scene.descriptor.get('kind')!='character' or scene.descriptor.get('entityId')!=entity_id:raise Problem('CHARACTER_LOOK_INVALID','请选择这个角色已完成的形象',409)
        row=create_pair(db,owner,world,entity_id,image_bytes(scene),crop,'ai_generated','AI 生成的角色形象；生成来源与模型回执保留。',{**scene.descriptor,'sourceSceneId':scene.id})
    if not selection:
        selection=CharacterLook(owner_id=owner,world_id=world_id,entity_id=entity_id,version=0);db.add(selection)
    selection.selected_artwork_id=row.id
    scene_id=row.descriptor.get('sourceSceneId');scene=db.get(SceneImage,scene_id) if scene_id else None
    selection.selected_scene_id=scene.id if scene and scene.owner_id==owner and scene.world_id==world_id else None
    selection.version+=1;db.flush()
    return {'worldId':world_id,'entityId':entity_id,'selectedId':row.id,'version':selection.version,'artwork':receipt(row)}

def upload(db,owner,world_id,data):
    from .looks import roster,look_row
    world=owned(db,World,world_id,owner,lock=True)
    if world.status!='active':raise Problem('WORLD_ARCHIVED','请先恢复这个世界',409)
    person=next((e for e in roster(db,world,include_current=True,line_id=data.lineId) if e['id']==data.entityId),None)
    if not person:raise Problem('CHARACTER_NOT_FOUND','角色不存在',404)
    choice=look_row(db,owner,world_id,data.entityId)
    if (choice.version if choice else 0)!=data.expectedVersion:raise Problem('CHARACTER_LOOK_CHANGED','形象已在另一页面修改，请刷新后选择',409)
    if not data.rightsConfirmed or not data.licenseNote.strip():raise Problem('CHARACTER_RIGHTS_REQUIRED','请确认图片来源与使用权',422)
    prefix,sep,encoded=data.imageDataUrl.partition(',')
    if not sep or prefix not in ('data:image/png;base64','data:image/jpeg;base64','data:image/webp;base64'):raise Problem('CHARACTER_IMAGE_INVALID','请选择 PNG、JPEG 或 WebP 图片',422)
    try:raw=base64.b64decode(encoded,validate=True)
    except ValueError:raise Problem('CHARACTER_IMAGE_INVALID','图片数据无效',422)
    row=create_pair(db,owner,world,data.entityId,raw,data.crop.model_dump() if data.crop else None,'user_upload',data.licenseNote,person)
    return choose(db,owner,world_id,data.entityId,row.id,data.expectedVersion,line_id=data.lineId)

def selected_artworks(db,owner,world_id,entities):
    if not entities:return {}
    pairs=db.execute(select(CharacterLook,CharacterArtwork).options(defer(CharacterArtwork.full_image),defer(CharacterArtwork.avatar_image)).join(CharacterArtwork,CharacterLook.selected_artwork_id==CharacterArtwork.id).where(
        CharacterLook.owner_id==owner,CharacterLook.world_id==world_id,CharacterLook.entity_id.in_(entities),CharacterArtwork.owner_id==owner,CharacterArtwork.world_id==world_id))
    return {choice.entity_id:art for choice,art in pairs if art.entity_id==choice.entity_id}
