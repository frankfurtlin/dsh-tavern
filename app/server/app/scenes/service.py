from ..model_budget import image_budget_exceeded
"""Scene views derive exclusively from the checkpoint's public perspective."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import base64
import json
import re
from sqlalchemy import select, func
from sqlalchemy.orm import object_session
from cryptography.fernet import Fernet
from ..config import settings
from ..domain import canonical, owned
from ..models import uid, now, Task
from ..errors import Problem
from ..storage import write_private, private_path
from ..world.models import WorldCheckpoint, WorldEvent, World, WorldTurn
from ..world.service import require_line, checked_checkpoint
from ..world.state import public_view, public_payload, execution_state, ancestry, apply_plan, checkpoint_hash
from .models import SceneImage, SceneProfile

from .layout import build_layout, view_geometry, hotspot_positions, VERSION
from .mentions import current_object_mention as visible_object_mention, absent_object_mention
from .interaction import scene_exits, public_presence
from .dialogue import conversation_segments
from .composition import character_visibility, character_presentations, scene_cast, inherited_visibility
from .cache import appearance, compatible, public_scene


def local_spatial_text(checkpoint,text):
    from .mentions import local_scene_text
    location=checkpoint.state.get('location');states=checkpoint.state.get('entities',{})
    away=[e['name'] for e in checkpoint.definition.get('entities',[]) if e.get('type')=='character'
          and states.get(e['id'],{}).get('location') not in (None,location)]
    present=[e['name'] for e in checkpoint.definition.get('entities',[]) if e.get('type')=='character'
             and states.get(e['id'],{}).get('location')==location]
    return local_scene_text(text,away,present)


def spatial_evidence(checkpoint):
    """Public changes since the last local walk, within one place/identity.

    Keeping a removal across ordinary turns prevents an old place description
    from bringing a fixture back. A later explicit reappearance supersedes it.
    """
    db=object_session(checkpoint);texts=[];cp=checkpoint;seen=set();bound={}
    for _ in range(128):
        if not cp or cp.id in seen:break
        seen.add(cp.id)
        if cp.owner_id!=checkpoint.owner_id or cp.world_id!=checkpoint.world_id or cp.state.get('location')!=checkpoint.state.get('location') or cp.state.get('playerId')!=checkpoint.state.get('playerId'):break
        texts.append(local_spatial_text(cp,public_payload(cp.text,checkpoint.state,checkpoint.definition)))
        spatial=cp.memory.get('sceneSpatialContext',{})
        if cp.kind=='scene_move' and spatial.get('location')==checkpoint.state.get('location') and spatial.get('playerId')==checkpoint.state.get('playerId'):
            bound=spatial;break
        cp=db.get(WorldCheckpoint,cp.parent_checkpoint_id) if db and cp.parent_checkpoint_id else None
    if len(seen)>=128 and cp is not None and not bound:
        # Without complete changes, authorize only recent positive evidence.
        bound={'evidenceTruncated':True}
    if not bound:
        spatial=checkpoint.memory.get('sceneSpatialContext',{})
        if spatial.get('location')==checkpoint.state.get('location') and spatial.get('playerId')==checkpoint.state.get('playerId'):bound=spatial
    # Leaving a place does not remove its furniture. On a later visit carry
    # only already-observed structural fixtures from this identity's own
    # ancestry. Keep this separate from current_text: historical NPC presence,
    # speech and portable items must never become current scene evidence.
    if db and cp and cp.state.get('location')!=checkpoint.state.get('location') and not bound.get('evidenceTruncated'):
        previous=[];base={};past=cp;past_seen=set()
        for _ in range(512):
            if not past or past.id in past_seen:break
            past_seen.add(past.id)
            if (past.owner_id!=checkpoint.owner_id or past.world_id!=checkpoint.world_id
                    or past.state.get('playerId')!=checkpoint.state.get('playerId')):break
            if past.state.get('location')==checkpoint.state.get('location'):
                previous.append(local_spatial_text(past,public_payload(past.text,checkpoint.state,checkpoint.definition)))
                context=past.memory.get('sceneSpatialContext',{})
                if (past.kind=='scene_move' and context.get('location')==checkpoint.state.get('location')
                        and context.get('playerId')==checkpoint.state.get('playerId')):
                    base=context;break
            past=db.get(WorldCheckpoint,past.parent_checkpoint_id) if past.parent_checkpoint_id else None
        truncated=len(past_seen)>=512 and past is not None and not base
        if previous and not truncated and not base.get('evidenceTruncated'):
            place=next((e for e in public_view(checkpoint.state,checkpoint.definition)['entities']
                        if e['id']==checkpoint.state.get('location')),None)
            if place:
                history=public_payload(base.get('text',''),checkpoint.state,checkpoint.definition)+'\n'+'\n'.join(reversed(previous))
                prior_layout=build_layout(place,[],history,current_text=history,
                    removed_fixtures=base.get('removedFixtures',[]),allow_background_evidence=True)
                nouns={'hearth':'壁炉','bench':'长凳','table':'木桌','window':'窗户','entrance':'入口',
                       'counter':'柜台','steps':'台阶','path':'通道'}
                # Retain the observed structure, not the old sentence's
                # occupants, dialogue or portable possessions. Region details
                # and inspection evidence are public at the current visit.
                fixture_text='\n'.join('此处有'+nouns.get(node.get('fixture'),node['name'])+'。'
                                       for node in prior_layout['nodes'][1:])
                bound={**bound,'location':checkpoint.state.get('location'),'playerId':checkpoint.state.get('playerId'),
                       'text':fixture_text,
                       'removedFixtures':prior_layout.get('removedFixtures',[])}
    return '\n'.join(reversed(texts)),bound


def scene_view(checkpoint,line_id=None):
    view=public_view(checkpoint.state,checkpoint.definition)
    entities=view['entities']; location=view['location']; player=view['playerId']
    place=next((e for e in entities if e['id']==location),None)
    if not place:
        raise Problem('SCENE_LOCATION_UNKNOWN','当前位置尚未被发现',409)
    self_entity=next(e for e in entities if e['id']==player)
    fields=self_entity.get('state',{}).get('fields',{})
    saved_point=fields.get('场景观察点','entry') if fields.get('场景地点')==location else 'entry'
    label=view['timeLabel']
    light=next((word for word in ['深夜','夜','傍晚','黄昏','清晨','早晨','午后','中午','黎明'] if word in label),'白天')
    environment={k:v for k,v in place.get('state',{}).get('fields',{}).items() if any(word in k.lower() for word in ['门','窗','火','雪','雨','天气','温度','照明','光线','环境','door','weather','fire','light','snow']) and isinstance(v,(str,int,float,bool))}
    inventory=self_entity.get('state',{}).get('inventory',{})
    characters=[e for e in entities if e['type']=='character' and e['id']!=player and e.get('nearby')
                and checkpoint.state.get('entities',{}).get(e['id'],{}).get('alive') is not False
                and checkpoint.state.get('entities',{}).get(e['id'],{}).get('location')==location]
    items=[e for e in entities if e['type']=='item' and (e.get('nearby') or inventory.get(e['id'],0)>0)]
    # Older narrative saves can describe visible objects before the ledger has
    # identified them. Expose these as observations, never invented inventory.
    text=local_spatial_text(checkpoint,public_payload(checkpoint.text,checkpoint.state,checkpoint.definition))
    current_text,spatial=spatial_evidence(checkpoint)
    presence=checkpoint.memory.get('scenePresence',{})
    bound_presence=presence if presence.get('location')==location and presence.get('playerId')==player else spatial
    absent_characters=bound_presence.get('absentCharacterIds',[]) if bound_presence.get('location')==location and bound_presence.get('playerId')==player else []
    # Older arrival receipts inherited the origin's visible roster, accidentally
    # hiding stationary NPCs at the destination. Derive arrival from ancestry
    # without rewriting saved checkpoints. Explicit narrative absence still wins.
    session=object_session(checkpoint);arrival=checkpoint
    if session:
        for _ in range(128):
            parent=session.get(WorldCheckpoint,arrival.parent_checkpoint_id) if arrival.parent_checkpoint_id else None
            if not parent or parent.owner_id!=checkpoint.owner_id or parent.world_id!=checkpoint.world_id:break
            if parent.state.get('location')!=location:
                absent_characters=[];break
            if arrival.kind not in ('scene_move','fork'):break
            arrival=parent
    characters=[e for e in characters if public_presence(e,current_text,e['id'] not in absent_characters and not spatial.get('evidenceTruncated',False))]
    surfaces=['铜路牌','铜牌','便笺','书信','旧书','账册','水壶','药材','钥匙','木箱','石碑','火炉','条凳','木桌','烛台','灯笼','窗户','房门','照片','底片']
    represented=[e['name'] for e in items]
    sentences=re.split(r'(?<=[。！？\n])',text)
    observed=checkpoint.memory.get('sceneObservations',{})
    if checkpoint.kind=='scene_move' and observed.get('location')==location and observed.get('playerId')==player:
        for item in public_payload(observed.get('items',[]),checkpoint.state,checkpoint.definition):
            if item.get('type')=='observation' and item.get('name') not in represented and not absent_object_mention(text,item.get('name','')):
                items.append(item);represented.append(item['name'])
    for name in surfaces:
        quote=next((part.strip() for part in sentences if visible_object_mention(part,name)),'')
        if quote and not any(name in present or present in name for present in represented):
            items.append({'id':'observation-'+sha256(name.encode()).hexdigest()[:16],'name':name,'type':'observation',
                          'description':quote[:240],'traits':'','source':'observed','evidence':[],'nearby':True})
            represented.append(name)
        if len(items)>=12:break
    if spatial.get('location')==location and spatial.get('playerId')==player:
        text=public_payload(spatial.get('text',''),checkpoint.state,checkpoint.definition)+'\n'+text
    layout=build_layout(place,items,text,current_text=current_text,removed_fixtures=spatial.get('removedFixtures',[]),allow_background_evidence=not spatial.get('evidenceTruncated',False))
    selected,pose=view_geometry(layout,saved_point);point=selected['id']
    points=[]
    for node in layout['nodes']:
        p=deepcopy(node)
        if p['id']!=point:
            from math import atan2
            p['yaw']=atan2(p['worldX']-pose['x'],-(p['worldZ']-pose['z']))
        points.append(p)
    exits=scene_exits(view,checkpoint,{**layout,'nodes':points})
    from .layout import space_kind
    from math import pi
    kind=space_kind(place)
    if kind!='interior' and len(exits)>1 and any(abs((a['yaw']-b['yaw']+pi)%(2*pi)-pi)<.7 for i,a in enumerate(exits) for b in exits[i+1:]):
        # Outdoor connections need distinct paths, not several labels on one door.
        for index,exit in enumerate(sorted(exits,key=lambda e:e['id'])):exit['yaw']=index*2*pi/len(exits)
    conversation=checkpoint.memory.get('sceneConversation',{})
    conversations=conversation_segments(public_payload(conversation.get('entries',[]),checkpoint.state,checkpoint.definition)) if conversation.get('playerId')==player else []
    # Received replies are presentation records, including uncertain drafts.
    # Reading them never accepts a turn or advances the world ledger.
    responses=[];arts={};db=object_session(checkpoint)
    if checkpoint.text and not any(e.get('checkpointId')==checkpoint.id for e in conversations):
        transcript=checkpoint.memory.get('sceneTranscript',{})
        responses.append({'id':checkpoint.id,'text':public_payload(checkpoint.text,checkpoint.state,checkpoint.definition),'draft':False,
            'segments':public_payload(transcript.get('segments',[]),checkpoint.state,checkpoint.definition) if transcript.get('checkpointId')==checkpoint.id else []})
    if db:
        from .characters import character_art_views
        world=db.get(World,checkpoint.world_id)
        if world:arts=character_art_views(db,checkpoint.owner_id,world,characters)
        parents=[checkpoint.id];ancestor=checkpoint
        # Local walks do not erase a received conversation at the same place.
        # Stop at the first actual story turn or identity/location change.
        while ancestor.kind=='scene_move' and ancestor.parent_checkpoint_id and len(parents)<128:
            parent=db.get(WorldCheckpoint,ancestor.parent_checkpoint_id)
            if not parent or parent.owner_id!=checkpoint.owner_id or parent.world_id!=checkpoint.world_id or parent.state.get('location')!=location or parent.state.get('playerId')!=player:break
            parents.append(parent.id);ancestor=parent
        rows=db.scalars(select(WorldTurn).where(WorldTurn.owner_id==checkpoint.owner_id,WorldTurn.world_id==checkpoint.world_id,
            WorldTurn.line_id==(line_id or checkpoint.line_id),WorldTurn.parent_checkpoint_id.in_(parents),
            WorldTurn.status.in_(['draft','candidate'])).order_by(WorldTurn.created_at).limit(20))
        for turn in rows:
            if not turn.draft_text:continue
            task=db.get(Task,turn.task_id)
            target=task.payload.get('selectedTarget',{}) if task and task.owner_id==checkpoint.owner_id else {}
            partner=target.get('entityId') if target.get('type')=='character' else None
            responses.append({'id':turn.id,'taskId':turn.task_id,'text':public_payload(turn.draft_text,checkpoint.state,checkpoint.definition),
                'draft':True,'partnerId':partner,'user':turn.action if partner else None})
    hotspots=hotspot_positions(layout,{**selected,'worldX':pose['x'],'worldZ':pose['z']},characters,items)
    descriptor={'placeId':location,'placeName':place['name'],'description':place.get('description',''),'environment':environment,
                'pointId':point,'pointLabel':selected['name'],'lighting':light,'layoutVersion':VERSION,'viewpoint':pose,
                'renderLayout':layout,'hotspots':hotspots}
    descriptor['observerName']=self_entity['name']
    from .facts import visual_facts
    facts=visual_facts(checkpoint,view,items,current_text)
    if facts:descriptor['visualFacts']=facts
    from .public_appearance import context as appearance_context
    public_appearance=appearance_context(checkpoint,characters)
    if public_appearance:descriptor['publicAppearanceContext']=public_appearance
    if point!='entry':descriptor['movementRenderVersion']=4
    descriptor['spaceKind']=kind
    from .layout import route_visual_cues,route_fixture_links
    from .composition import inherited_poses
    poses=inherited_poses(checkpoint,characters)
    if poses:descriptor['characterPoses']=poses
    cues=route_visual_cues(exits,kind)
    linked=route_fixture_links(exits,layout)
    descriptor['exitRoutes']=[{'id':e['id'],'portalId':e.get('portalId'),'name':e['name'],'yaw':e['yaw'],
                              **({'associatedFixtureIds':linked[e['id']]} if e['id'] in linked else {}),
                              **({'visualCue':cues[e['id']]} if e['id'] in cues else {})} for e in exits]
    result={'descriptor':descriptor,'pointId':point,'points':points,'exits':exits,'characters':characters,'items':items,
            'layout':layout,'hotspots':hotspots,'checkpointId':checkpoint.id,'timeLabel':label,'playerId':player,'conversations':conversations,
            'characterArt':arts,'responses':responses,
            'characterVisibility':inherited_visibility(checkpoint,characters)}
    if db:
        from .looks import scene_looks
        selected=scene_looks(result,db,checkpoint.owner_id,checkpoint.world_id)
        if selected:descriptor['characterLooks']={key:row.id for key,row in sorted(selected.items())}
    return result



def fingerprint(view,world_id,line_id,profile_version):
    return sha256(canonical({'world':world_id,'player':view['playerId'],
                            'profile':profile_version,'appearance':appearance(view)}).encode()).hexdigest()


def receipt(row,view=None):
    # Follow a bounded corrective image without losing the original receipt.
    session=object_session(row) if hasattr(row,'_sa_instance_state') else None
    repair=session.get(SceneImage,row.image['repairSceneId']) if session and row.image.get('repairSceneId') else None
    if repair and repair.owner_id==row.owner_id and repair.line_id==row.line_id and repair.world_id==row.world_id and repair.scene_hash==row.scene_hash and repair.descriptor.get('repairOfSceneId')==row.id and repair.status=='ready':
        return receipt(repair,view)
    result={'id':row.id,'worldId':row.world_id,'lineId':row.line_id,'status':row.status,
            'sceneHash':row.scene_hash,'generation':row.generation,'errorCode':row.error_code,
            'imageUrl':'/api/world/v1/scenes/'+row.id+'/image' if row.status=='ready' else None,
            'image':{k:v for k,v in row.image.items() if k!='file'},'descriptor':row.descriptor}
    from .lifecycle import progress
    result['progress']=progress(row)
    if row.status!='ready':
        from .status import recovery
        result['recovery']=recovery(row)
        if session and view and row.checkpoint_id==view.get('checkpointId'):
            from .progressive import metadata,invalid_preview_hashes
            preview=metadata(session,row)
            if preview:result['preview']=preview
            invalid=invalid_preview_hashes(session,row)
            if invalid:result['invalidPreviewHashes']=invalid
        if result['recovery'].get('responseRejected'):
            result['status']='failed'
            result['errorCode']='SCENE_VISION_REJECTED'
    if repair and repair.status in ('failed','unknown'):
        result['image']={**result['image'],'anchorStatus':'incomplete','repairError':repair.error_code}
    if view:
        result.update(view)
        # Hotspot coordinates belong to the image, independent of entity order.
        result['hotspots']={**view['hotspots'],**row.descriptor.get('hotspots',{})}
        # Characters stand on the current ground plane. Old saved panoramas
        # encoded their feet at the horizon; those coordinates must not win.
        for character in view['characters']:
            result['hotspots'][character['id']]=view['hotspots'][character['id']]
        result['layout']=row.descriptor.get('renderLayout',view['layout'])
        result['characterPresentation']=character_presentations(row,view)
        from .grounding import grounded_points
        result.update(grounded_points(row,view))
    return result


def queue_scene(db,owner,world_id,line_id,expected,regenerate=False):
    from .visual_style import visual_context
    world,line=require_line(db,owner,world_id,line_id,lock=True)
    from ..authoring.trials import text_only
    text_only(world)
    if world.status!='active' or line.status!='active':raise Problem('WORLD_ARCHIVED','请先恢复这条世界线',409)
    if line.head_checkpoint_id!=expected:raise Problem('WORLD_CHECKPOINT_CONFLICT','场景已变化，请刷新后重试',409)
    cp=checked_checkpoint(db,owner,world_id,expected)
    view=scene_view(cp,line_id)
    profile=db.scalar(select(SceneProfile).where(SceneProfile.owner_id==owner).with_for_update())
    if not profile or profile.status!='active':raise Problem('SCENE_NOT_CONFIGURED','请先配置场景图像模型',409)
    key=fingerprint(view,world_id,line_id,profile.version)
    previous=db.scalar(select(SceneImage).where(SceneImage.owner_id==owner,SceneImage.line_id==line_id,SceneImage.scene_hash==key).order_by(SceneImage.generation.desc()).limit(1))
    next_generation=previous.generation+1 if previous else 1
    if not previous and not regenerate:
        # A stricter review contract is not consent to pay for another image
        # after an unresolved or stopped request at this exact checkpoint.
        from .fact_upgrade import eligible as fact_upgrade_eligible
        candidates=db.scalars(select(SceneImage).where(SceneImage.owner_id==owner,SceneImage.world_id==world_id,
            SceneImage.line_id==line_id,SceneImage.checkpoint_id==expected,SceneImage.profile_id==profile.id,
            SceneImage.profile_version==profile.version,SceneImage.status.in_(('failed','unknown')))
            .order_by(SceneImage.created_at.desc()).limit(64))
        original=next((row for row in candidates if public_scene(row) and fact_upgrade_eligible(row,view)),None)
        if original:return receipt(original,view)
    if not previous and not regenerate:
        candidates=db.scalars(select(SceneImage).where(SceneImage.owner_id==owner,SceneImage.world_id==world_id,
            SceneImage.profile_version==profile.version,
            SceneImage.descriptor['placeId'].astext==view['descriptor']['placeId'],
            SceneImage.descriptor['pointId'].astext==view['pointId']).order_by(SceneImage.created_at.desc()).limit(100))
        previous=next((row for row in candidates if public_scene(row)
                       and (row.line_id==line_id or row.status=='ready') and compatible(row,view)),None)
    if previous and previous.status in ('failed','unknown') and previous.descriptor.get('repairOfSceneId'):
        parent=db.get(SceneImage,previous.descriptor['repairOfSceneId'])
        if (parent and parent.owner_id==owner and parent.world_id==world_id
                and parent.status=='ready' and compatible(parent,view)):
            previous=parent
    if previous and not regenerate:
        from .cache import known_failed_scene_upgrade
        if known_failed_scene_upgrade(db,previous,8):regenerate=True
        elif previous.profile_version==profile.version and compatible(previous,view):
            from .pipeline import resume_unsent_connection
            resume_unsent_connection(db,previous,line)
    if previous and (not regenerate or previous.status in ('queued','running','saving')):
        if previous.status=='ready' and previous.line_id!=line_id:
            # Each save owns a durable copy of received bytes. Deleting the
            # originating branch must not break a reused panorama elsewhere.
            old=previous
            resolved=receipt(old,view)
            if resolved['id']!=old.id:old=db.get(SceneImage,resolved['id'])
            # A pending inspection belongs to its existing task. Wait for that
            # receipt before creating an independent durable copy; copying its
            # queued status without its task would leave a permanent spinner.
            if resolved['image'].get('anchorStatus') in ('queued','repairing','unavailable'):
                from .binding import queue_binding
                queue_binding(db,old)
                return receipt(old,view)
            metadata,data=saved_receipt(old)
            copied=SceneImage(id=uid(),owner_id=owner,world_id=world_id,line_id=line_id,checkpoint_id=expected,
                profile_id=profile.id,profile_version=profile.version,scene_hash=key,generation=1,
                status='ready',descriptor={**deepcopy(old.descriptor),'reusedFromSceneId':old.id},image={})
            copied.image={**save_image(copied,data,{k:v for k,v in metadata.items() if k not in ('file','providerRequestId')},old.request_id),
                **{k:deepcopy(v) for k,v in resolved['image'].items() if k not in metadata and k not in ('bindingTaskId','repairSceneId','anchorError')}}
            db.add(copied);db.flush();previous=copied
        if previous.status=='ready':
            from .binding import queue_binding
            queue_binding(db,previous)
        return receipt(previous,view)
    from .cooldown import check_selected
    check_selected(db,owner)
    day=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    count=db.scalar(select(func.count()).select_from(SceneImage).where(SceneImage.owner_id==owner,SceneImage.created_at>=day))
    if image_budget_exceeded(profile,count):raise Problem('SCENE_DAILY_LIMIT','今天的场景出图次数已用完，仍可继续文字游玩',429)
    pending=db.scalar(select(func.count()).select_from(SceneImage).where(SceneImage.owner_id==owner,SceneImage.status.in_(['queued','running','saving'])))
    if pending>=3:raise Problem('SCENE_QUEUE_FULL','请等当前场景生成完成',409)
    references=db.scalars(select(SceneImage).where(SceneImage.owner_id==owner,SceneImage.world_id==world_id,
        SceneImage.profile_version==profile.version,SceneImage.status=='ready',
        SceneImage.descriptor['placeId'].astext==view['descriptor']['placeId'],
        SceneImage.descriptor['pointId'].astext==view['pointId']).order_by(SceneImage.created_at.desc()).limit(100))
    from .cache import usable_reference
    reference=next((row for row in references if usable_reference(row)),None)
    descriptor={**view['descriptor'], **visual_context(world,cp.definition),'sceneCast':scene_cast(view,world.name),'compositionVersion':3,'exitInspectionVersion':1 if settings.ai_mode=='real' else 0,'coordinateInspectionVersion':1 if settings.ai_mode=='real' else 0,'coordinateCameraVersion':1 if settings.ai_mode=='real' else 0,'apertureInspectionVersion':1 if settings.ai_mode=='real' else 0,'cameraPixelsVersion':1 if settings.ai_mode=='real' else 0,'fixtureInspectionVersion':1 if settings.ai_mode=='real' else 0,'artworkReviewVersion':1 if settings.ai_mode=='real' else 0,'bodyInspectionVersion':1,'identityInspectionVersion':1,'personInspectionVersion':1,'headCameraSearchVersion':1 if settings.ai_mode=='real' else 0,'headCropPixelVersion':1 if settings.ai_mode=='real' else 0,'placementInspectionVersion':2,'placementSupportVersion':1 if settings.ai_mode=='real' else 0,'placementCameraVersion':1 if settings.ai_mode=='real' else 0,'croppedPlacementGuideVersion':1,'actorMaskVersion':3,'environmentGeometryVersion':7,'renderPipeline':0 if settings.ai_mode=='mock' else 1,'appearance':appearance(view),
                **({'referenceSceneId':reference.id,'referenceMode':'edit-cast-and-state'} if reference else {})}
    descriptor.update(environmentGeometryVersion=11,visualBriefVersion=1)
    if settings.ai_mode=='real':descriptor.update(inspectionPerformanceVersion=1,inspectionMaxCalls=32,readablePlacementVersion=2,actorSurroundingsVersion=1,inspectionReceiptVersion=1)
    if settings.ai_mode=='real':descriptor.update(measurementRecoveryVersion=1,peopleCountConflictVersion=1,inventoryContinuityVersion=1,localEditResolutionVersion=1)
    if settings.ai_mode=='real':descriptor.update(measurementEfficiencyVersion=1,globalEvidenceRecoveryVersion=1,publicAppearanceContextVersion=1)
    if settings.ai_mode=='real':descriptor.update(sceneReviewVersion=1,peopleInventoryVersion=1,peopleClarificationVersion=1,lightingInspectionVersion=1,aperturePointVersion=1,apertureProposalVersion=1,surfaceContextVersion=1,supportClarificationVersion=1,surfaceClarificationVersion=1)
    for person in descriptor['sceneCast']:
        look_id=descriptor.get('characterLooks',{}).get(person['entityId'])
        from .models import CharacterArtwork
        look=(db.get(CharacterArtwork,look_id) or db.get(SceneImage,look_id)) if look_id else None
        if look and look.owner_id==owner and look.world_id==world_id and look.status=='ready' and look.descriptor.get('entityId')==person['entityId']:
            person['appearance']=look.descriptor.get('appearance',person.get('appearance',''))
            person['lookImageId']=look.id
    row=SceneImage(id=uid(),owner_id=owner,world_id=world_id,line_id=line_id,checkpoint_id=expected,
                   profile_id=profile.id,profile_version=profile.version,scene_hash=key,generation=next_generation,
                   status='queued',descriptor=descriptor,image={})
    db.add(row);db.flush()
    return receipt(row,view)


def save_image(row,data,metadata,request_id=None):
    name='scene-'+row.world_id+'-'+row.id+'.enc'
    payload={'version':1,'data':base64.b64encode(data).decode(),'metadata':{**metadata,'sha256':sha256(data).hexdigest(),'bytes':len(data),'providerRequestId':request_id}}
    cipher=Fernet(settings.provider_master_key.encode()).encrypt(canonical(payload).encode())
    write_private(row.owner_id,name,cipher)
    return {**payload['metadata'],'file':name}


def saved_receipt(row):
    name='scene-'+row.world_id+'-'+row.id+'.enc'
    value=json.loads(Fernet(settings.provider_master_key.encode()).decrypt(private_path(row.owner_id,name).read_bytes()))
    data=base64.b64decode(value['data'],validate=True)
    if value['version']!=1 or sha256(data).hexdigest()!=value['metadata']['sha256']:raise ValueError('Invalid scene receipt')
    return {**value['metadata'],'file':name},data


def image_bytes(row):
    from .models import CharacterArtwork
    if isinstance(row,CharacterArtwork):return row.full_image
    try:
        _,value=saved_receipt(row)
        if sha256(value).hexdigest()!=row.image['sha256']:raise ValueError()
        return value
    except Exception:raise Problem('SCENE_IMAGE_UNAVAILABLE','场景图片暂时不可用',503) from None


def move(db,owner,world_id,line_id,data):
    world,line=require_line(db,owner,world_id,line_id,lock=True,active=True)
    if world.status!='active':raise Problem('WORLD_ARCHIVED','请先恢复这个世界',409)
    if line.pending_task_id:raise Problem('WORLD_BUSY','请先等当前行动完成',409)
    if line.head_checkpoint_id!=data.expectedCheckpointId:raise Problem('WORLD_CHECKPOINT_CONFLICT','当前位置已变化，请刷新后重试',409)
    parent=checked_checkpoint(db,owner,world_id,line.head_checkpoint_id)
    view=scene_view(parent); base=execution_state(parent,ancestry(db,parent,owner))
    if base['ended'] or base['entities'][base['playerId']].get('alive') is False:raise Problem('WORLD_ENDED','当前身份无法移动',409)
    choices=view['points'] if data.targetKind=='point' else view['exits']
    target=next((p for p in choices if p['id']==data.targetId),None)
    if not target:raise Problem('SCENE_DESTINATION_UNKNOWN','这里还没有通往这个位置的路',409)
    if data.targetKind=='place':raise Problem('SCENE_TRAVEL_REQUIRED','请通过探索行动前往下一个地点',409)
    if data.targetKind=='point' and target['id']==view['pointId']:return {'worldId':world_id,'lineId':line_id,'checkpointId':parent.id}
    location=base['location'] if data.targetKind=='point' else target['id']
    point=target['id'] if data.targetKind=='point' else 'entry'
    text='你走到'+target['name']+'，停下脚步。'
    rules=parent.definition['rules']; costs=rules.get('actionCosts',{}).get('action',{})
    plan={'title':target['name'],'elapsedMinutes':max(1,costs.get('minutes',0)), 'timeLabel':base['timeLabel'], 'publicOutcome':text,
          'location':location, 'updates':[{'entityId':base['playerId'],'field':'field:'+key,'value':value,'visibleTo':['player'],'reason':text} for key,value in [('场景观察点',point),('场景地点',location)]],
          'resources':[{'entityId':base['playerId'],'field':k,'operation':'spend','amount':v,'reason':text,'visibleTo':['player']} for k,v in costs.items() if k!='minutes' and v],
          'options':deepcopy(parent.options) if location==base['location'] else []}
    checkpoint_id=uid()
    # Carry only still-valid, public fixture evidence from this location and
    # identity. A revoked fixture cannot reappear on the next local move.
    spatial_text='\n'.join(node.get('source',{}).get('quote','') for node in view['layout']['nodes'][1:])
    absent_characters=[e['id'] for e in public_view(parent.state,parent.definition)['entities'] if e['type']=='character' and e['id']!=base['playerId'] and e.get('nearby') and e['id'] not in {person['id'] for person in view['characters']}]
    state,definition,changes,events=apply_plan(base,parent.definition,plan,checkpoint_id,action_kind='action')
    cp=WorldCheckpoint(id=checkpoint_id,owner_id=owner,world_id=world_id,line_id=line_id,parent_checkpoint_id=parent.id,
        sequence=parent.sequence+1,title=target['name'],text=text,action=target['direction'],state=state,definition=definition,
        options=plan['options'],changes=changes,summary=text,memory={**deepcopy(parent.memory),'scenePresence':{'location':location,'playerId':base['playerId'],'absentCharacterIds':absent_characters if location==base['location'] else []},'sceneSpatialContext':{'location':location,'playerId':base['playerId'],'absentCharacterIds':absent_characters if location==base['location'] else [],'text':spatial_text if location==base['location'] else '','removedFixtures':view['layout'].get('removedFixtures',[]) if location==base['location'] else [],'evidenceTruncated':view['layout'].get('evidenceTruncated',False) if location==base['location'] else False},'sceneObservations':{'location':location,'playerId':base['playerId'],'items':[e for e in view['items'] if e['type']=='observation'] if location==base['location'] else []}},
        content_hash=checkpoint_hash(state,definition,text,parent.id),kind='scene_move')
    db.add(cp);db.flush()
    for event in events:
        db.add(WorldEvent(owner_id=owner,world_id=world_id,checkpoint_id=cp.id,entity_id=event.get('entityId'),type=event['type'],data=event,visible_to=event['visibleTo'],minute=event.get('minute',state['minute'])))
    line.head_checkpoint_id=cp.id;line.version+=1;world.active_line_id=line.id;world.updated_at=now()
    return {'worldId':world_id,'lineId':line_id,'checkpointId':cp.id}
