from ..model_budget import unlimited_usage
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, SecretStr
from typing import Literal
from sqlalchemy import select
from ..db import get_db
from ..security import current_user
from ..domain import owned
from ..providers import encrypt_key
from ..errors import Problem
from ..world.api import mutate, available
from ..world.service import require_line, checked_checkpoint, create_turn
from ..world.schemas import ActionInput
from .models import SceneImage, SceneProfile
from .service import queue_scene, receipt, scene_view, image_bytes, move, fingerprint
from .provider import MODEL
from . import cleanup
from .cache import compatible
from ..assets.schemas import Crop

router=APIRouter(dependencies=[Depends(available)])

@router.get('/worlds/{world_id}/lines/{line_id}/portrait-scene')
def portrait_scene(world_id:str,line_id:str,user=Depends(current_user),db=Depends(get_db)):
    from .portrait_stage import presentation
    return presentation(db,user.id,world_id,line_id)


@router.get('/worlds/{world_id}/lines/{line_id}/portrait-scene/background')
def portrait_background(world_id:str,line_id:str,user=Depends(current_user),db=Depends(get_db)):
    from .portrait_stage import background_bytes
    return Response(background_bytes(db,user.id,world_id,line_id),media_type='image/webp',headers={'Cache-Control':'private, no-store'})

class Configuration(BaseModel):
    key:SecretStr=Field(min_length=20,max_length=500)
    dailyLimit:int=Field(default=50,ge=0,le=2147483647)

class SceneRequest(BaseModel):
    expectedCheckpointId:str=Field(pattern=r'^[a-f0-9]{32}$')
    regenerate:bool=False

class Movement(BaseModel):
    expectedCheckpointId:str=Field(pattern=r'^[a-f0-9]{32}$')
    targetKind:Literal['point','place']
    targetId:str=Field(min_length=1,max_length=80)


class IndependentInspection(SceneRequest):
    sceneId:str=Field(pattern=r'^[a-f0-9]{32}$')
    independent:Literal[True]
    sourceSceneId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')


class StopScene(SceneRequest):
    sceneId:str=Field(pattern=r'^[a-f0-9]{32}$')


@router.post('/worlds/{world_id}/lines/{line_id}/scene/stop')
def stop(world_id:str,line_id:str,data:StopScene,request:Request,user=Depends(current_user),db=Depends(get_db)):
    from .lifecycle import stop_scene
    def execute():
        row=stop_scene(db,user.id,world_id,line_id,data.sceneId,data.expectedCheckpointId)
        return receipt(row,scene_view(checked_checkpoint(db,user.id,world_id,data.expectedCheckpointId),line_id))
    return mutate(db,user,request,'scene-stop:'+line_id,data.model_dump(),execute)


@router.post('/worlds/{world_id}/lines/{line_id}/scene/inspect',status_code=202)
def inspect_received(world_id:str,line_id:str,data:IndependentInspection,request:Request,user=Depends(current_user),db=Depends(get_db)):
    from .recovery import independent_inspection
    return mutate(db,user,request,'scene-independent-inspection:'+line_id,data.model_dump(exclude_none=True),lambda:independent_inspection(db,user.id,world_id,line_id,data.sceneId,data.expectedCheckpointId,data.sourceSceneId))


@router.get('/scenes/{scene_id}/retained-image')
def retained_preview(scene_id:str,user=Depends(current_user),db=Depends(get_db)):
    from .retained import environment
    from .pipeline import narrative_context_matches
    row=owned(db,SceneImage,scene_id,user.id)
    world,line=require_line(db,user.id,row.world_id,row.line_id,active=True)
    if row.checkpoint_id!=line.head_checkpoint_id or not narrative_context_matches(db,row,line):
        raise Problem('SCENE_CONTEXT_CHANGED','已保存图片不属于当前时间点',409)
    child=environment(db,row)
    if not child:raise Problem('SCENE_NOT_FOUND','没有可预览的已保存底图',404)
    return Response(image_bytes(child),media_type='image/webp',headers={'Cache-Control':'private, no-store'})

class Activation(BaseModel):
    enabled:bool
    dailyLimit:int|None=Field(default=None,ge=0,le=2147483647)

class TargetAction(ActionInput):
    targetEntityId:str=Field(min_length=1,max_length=80)

class CharacterRequest(SceneRequest):
    entityId:str=Field(min_length=1,max_length=80)

class LookAppearance(BaseModel):
    age:str=Field(default='',max_length=80)
    hair:str=Field(default='',max_length=180)
    clothing:str=Field(default='',max_length=250)
    temperament:str=Field(default='',max_length=180)

class LookRequest(BaseModel):
    entityId:str=Field(min_length=1,max_length=80)
    lineId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    appearance:LookAppearance=Field(default_factory=LookAppearance)
    regenerate:bool=False

class LookSelection(BaseModel):
    sceneId:str=Field(pattern=r'^[a-f0-9]{32}$')
    expectedVersion:int=Field(ge=0)
    crop:Crop|None=None
    lineId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')

@router.get('/worlds/{world_id}/character-looks')
def character_looks(world_id:str,lineId:str|None=None,user=Depends(current_user),db=Depends(get_db)):
    from .looks import library
    return library(db,user.id,world_id,line_id=lineId)

@router.post('/worlds/{world_id}/character-looks',status_code=202)
def generate_look(world_id:str,data:LookRequest,request:Request,user=Depends(current_user),db=Depends(get_db)):
    from .looks import queue_look
    return mutate(db,user,request,'character-look:'+world_id,data.model_dump(exclude_none=True),lambda:queue_look(db,user.id,world_id,data.entityId,data.appearance.model_dump(),data.regenerate,line_id=data.lineId))

@router.post('/worlds/{world_id}/character-looks/{entity_id}/selection')
def choose_look(world_id:str,entity_id:str,data:LookSelection,request:Request,user=Depends(current_user),db=Depends(get_db)):
    from .looks import select_look
    return mutate(db,user,request,'character-look-selection:'+world_id+':'+entity_id,data.model_dump(exclude_none=True),lambda:select_look(db,user.id,world_id,entity_id,data.sceneId,data.expectedVersion,data.crop.model_dump() if data.crop else None,line_id=data.lineId))


class LookUpload(BaseModel):
    entityId:str=Field(min_length=1,max_length=80)
    lineId:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    expectedVersion:int=Field(ge=0)
    imageDataUrl:str=Field(max_length=4*1024*1024+100)
    crop:Crop|None=None
    rightsConfirmed:bool=False
    licenseNote:str=Field(default='',max_length=1000)

@router.post('/worlds/{world_id}/character-looks/upload',status_code=201)
def upload_look(world_id:str,data:LookUpload,request:Request,user=Depends(current_user),db=Depends(get_db)):
    from .artworks import upload
    return mutate(db,user,request,'character-look-upload:'+world_id,data.model_dump(exclude_none=True),lambda:upload(db,user.id,world_id,data))

@router.get('/character-artworks/{artwork_id}/{variant}')
def artwork_image(artwork_id:str,variant:str,user=Depends(current_user),db=Depends(get_db)):
    from .models import CharacterArtwork
    from ..world.models import World
    row=owned(db,CharacterArtwork,artwork_id,user.id);world=owned(db,World,row.world_id,user.id)
    if world.status=='deleted' or variant not in ('avatar','image'):raise Problem('RESOURCE_NOT_FOUND','形象不存在',404)
    return Response(row.avatar_image if variant=='avatar' else row.full_image,media_type='image/webp',headers={'Cache-Control':'private, no-store'})

@router.get('/scenes/{scene_id}/avatar')
def candidate_avatar(scene_id:str,user=Depends(current_user),db=Depends(get_db)):
    from .looks import authorize_image
    from .artworks import prepare_pair
    row=owned(db,SceneImage,scene_id,user.id);authorize_image(db,user.id,row)
    if row.status!='ready' or row.descriptor.get('kind')!='character':raise Problem('SCENE_NOT_READY','角色形象尚未完成',409)
    _,avatar,_=prepare_pair(image_bytes(row))
    return Response(avatar,media_type='image/webp',headers={'Cache-Control':'private, no-store'})

class TravelAction(ActionInput):
    sceneExitId:str=Field(min_length=1,max_length=100)

@router.get('/scene-profile')
def profile(user=Depends(current_user),db=Depends(get_db)):
    row=db.scalar(select(SceneProfile).where(SceneProfile.owner_id==user.id))
    from ..foundation.default_models import managed_image
    return {'enabled':bool(row and row.status=='active'),'modelId':MODEL,'hasKey':bool(row and row.key_cipher),'unlimitedUsage':unlimited_usage(),'dailyLimit':0 if unlimited_usage() else row.daily_limit if row else 50,
            'source':'platform' if managed_image(db,row) else 'personal'}

@router.put('/scene-profile')
def configure(data:Configuration,user=Depends(current_user),db=Depends(get_db)):
    # Serialize key changes against queue reservation without exposing credentials.
    from ..models import User
    db.execute(select(User).where(User.id==user.id).with_for_update())
    row=db.scalar(select(SceneProfile).where(SceneProfile.owner_id==user.id).with_for_update())
    if not row:row=SceneProfile(owner_id=user.id,key_cipher=encrypt_key(data.key.get_secret_value()),version=1,daily_limit=data.dailyLimit,status='active');db.add(row)
    else:row.key_cipher=encrypt_key(data.key.get_secret_value());row.version+=1;row.daily_limit=data.dailyLimit;row.status='active'
    from ..foundation.models import ModelGrant
    grant=db.get(ModelGrant,user.id)
    if grant and grant.image_profile_id==row.id:grant.image_profile_id=None
    db.commit()
    return {'enabled':True,'modelId':MODEL,'hasKey':True,'dailyLimit':row.daily_limit}

@router.patch('/scene-profile')
def activate(data:Activation,user=Depends(current_user),db=Depends(get_db)):
    row=db.scalar(select(SceneProfile).where(SceneProfile.owner_id==user.id).with_for_update())
    if not row:raise Problem('SCENE_NOT_CONFIGURED','请先配置图像通道',409)
    status='active' if data.enabled else 'disabled'
    if row.status!=status:row.version+=1
    row.status=status
    if data.dailyLimit is not None:row.daily_limit=data.dailyLimit
    if not data.enabled:
        from sqlalchemy import update
        db.execute(update(SceneImage).where(SceneImage.owner_id==user.id,SceneImage.status=='queued').values(status='failed',error_code='SCENE_DISABLED'))
    db.commit()
    return {'enabled':data.enabled,'modelId':MODEL,'hasKey':True,'dailyLimit':row.daily_limit}

@router.post('/worlds/{world_id}/lines/{line_id}/scene',status_code=202)
def request_scene(world_id:str,line_id:str,data:SceneRequest,request:Request,user=Depends(current_user),db=Depends(get_db)):
    return mutate(db,user,request,'scene:'+line_id,data.model_dump(),lambda:queue_scene(db,user.id,world_id,line_id,data.expectedCheckpointId,data.regenerate))

@router.get('/scenes/{scene_id}')
def status(scene_id:str,lineId:str|None=None,user=Depends(current_user),db=Depends(get_db)):
    row=owned(db,SceneImage,scene_id,user.id)
    if row.descriptor.get('pipelineParentId'):raise Problem('SCENE_NOT_FOUND','场景不存在',404)
    from .looks import authorize_image
    world,line=authorize_image(db,user.id,row)
    if row.descriptor.get('kind')=='character':
        from .characters import art_receipt
        return art_receipt(row)
    # Cached images may serve another save in this owned world. Compare with
    # the requesting save, because the original save may have moved elsewhere.
    if lineId:
        world,line=require_line(db,user.id,row.world_id,lineId)
    cp=checked_checkpoint(db,user.id,row.world_id,line.head_checkpoint_id)
    view=scene_view(cp,line.id)
    if not compatible(row,view):
        raise Problem('SCENE_CONTEXT_CHANGED','所在场景已变化，请刷新当前位置',409)
    if row.status=='ready' and row.image.get('anchorStatus')=='unavailable':
        from .binding import queue_binding
        queue_binding(db,row)
        db.commit()
    return receipt(row,view)

@router.post('/worlds/{world_id}/lines/{line_id}/scene/characters',status_code=202)
def request_character(world_id:str,line_id:str,data:CharacterRequest,request:Request,user=Depends(current_user),db=Depends(get_db)):
    from .characters import queue_character
    return mutate(db,user,request,'scene-character:'+line_id,data.model_dump(),lambda:queue_character(db,user.id,world_id,line_id,data.expectedCheckpointId,data.entityId,data.regenerate))

@router.get('/scenes/{scene_id}/image')
def image(scene_id:str,user=Depends(current_user),db=Depends(get_db)):
    from .looks import authorize_image
    row=owned(db,SceneImage,scene_id,user.id);authorize_image(db,user.id,row)
    if row.descriptor.get('pipelineParentId'):raise Problem('SCENE_NOT_FOUND','场景不存在',404)
    if row.status!='ready':raise Problem('SCENE_NOT_READY','场景图片还未准备完成',409)
    return Response(image_bytes(row),media_type='image/webp',headers={'Cache-Control':'private, no-store'})


@router.get('/scenes/{scene_id}/preview/{source_hash}')
def preview_image(scene_id:str,source_hash:str,lineId:str|None=None,user=Depends(current_user),db=Depends(get_db)):
    from .looks import authorize_image
    from .progressive import preview_stage,pixels
    parent=owned(db,SceneImage,scene_id,user.id)
    if parent.descriptor.get('pipelineParentId'):raise Problem('SCENE_NOT_FOUND','场景不存在',404)
    world,line=authorize_image(db,user.id,parent)
    if not line or lineId and lineId!=line.id:raise Problem('SCENE_CONTEXT_CHANGED','所在场景已变化',409)
    if line.status!='active' or world.status!='active' or parent.checkpoint_id!=line.head_checkpoint_id:
        raise Problem('SCENE_CONTEXT_CHANGED','所在场景已变化',409)
    row=preview_stage(db,parent,source_hash)
    if not row or row.image.get('sha256')!=source_hash:raise Problem('SCENE_NOT_READY','场景预览暂不可用',409)
    return Response(pixels(row),media_type='image/webp',headers={'Cache-Control':'private, no-store'})

@router.post('/worlds/{world_id}/lines/{line_id}/scene/move')
def movement(world_id:str,line_id:str,data:Movement,request:Request,user=Depends(current_user),db=Depends(get_db)):
    return mutate(db,user,request,'scene-move:'+line_id,data.model_dump(),lambda:move(db,user.id,world_id,line_id,data))

@router.post('/worlds/{world_id}/lines/{line_id}/scene/actions',status_code=202)
def targeted_action(world_id:str,line_id:str,data:TargetAction,request:Request,user=Depends(current_user),db=Depends(get_db)):
    def action():
        world,line=require_line(db,user.id,world_id,line_id,lock=True,active=True)
        cp=checked_checkpoint(db,user.id,world_id,line.head_checkpoint_id)
        view=scene_view(cp)
        target=next((e for e in view['characters']+view['items'] if e['id']==data.targetEntityId),None)
        if not target:raise Problem('SCENE_TARGET_UNAVAILABLE','这个人物或物品已不在当前场景中',409)
        value=create_turn(db,user.id,world,line,data)
        from ..models import Task
        task=db.get(Task,value['taskId'])
        selected={'entityId':target['id'],'name':target['name'],'type':target['type']}
        if target['type']=='observation':selected={'observationId':target['id'],'name':target['name'],'type':'observedObject','description':target['description']}
        task.payload={**task.payload,'selectedTarget':selected}
        if target['type']=='character' and data.kind=='dialogue':
            task.payload={**task.payload,'interactionMode':'dialogue'}
            task.result={**task.result,'interactionMode':'dialogue','partnerId':target['id'],'message':data.message}
        return value
    return mutate(db,user,request,'scene-action:'+line_id,data.model_dump(),action)

@router.post('/worlds/{world_id}/lines/{line_id}/scene/travel',status_code=202)
def travel(world_id:str,line_id:str,data:TravelAction,request:Request,user=Depends(current_user),db=Depends(get_db)):
    def action():
        world,line=require_line(db,user.id,world_id,line_id,lock=True,active=True)
        if line.head_checkpoint_id!=data.expectedCheckpointId:
            raise Problem('WORLD_CHECKPOINT_CONFLICT','当前位置已变化，请刷新后重试',409)
        cp=checked_checkpoint(db,user.id,world_id,line.head_checkpoint_id)
        view=scene_view(cp)
        target=next((e for e in view['exits'] if e['id']==data.sceneExitId),None)
        if not target:raise Problem('SCENE_DESTINATION_UNKNOWN','当前没有这个出口',409)
        if data.kind!='action':raise Problem('SCENE_TRAVEL_REQUIRED','探索出口需要行动回合',422)
        value=create_turn(db,user.id,world,line,data)
        from ..models import Task
        task=db.get(Task,value['taskId'])
        task.payload={**task.payload,'sceneIntent':{'kind':'travel','origin':view['descriptor']['placeId'],
            'destinationId':target['id'] if target['kind']=='place' else None,
            'direction':target['direction'],'doorEvidence':target.get('source',{})}}
        return value
    return mutate(db,user,request,'scene-travel:'+line_id,data.model_dump(),action)
