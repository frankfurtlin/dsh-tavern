"""Read-only portrait dialogue presentation; never submit image jobs or turns."""
from copy import deepcopy
from math import isfinite,pi
from sqlalchemy.orm import defer
from ..domain import digest,canonical
from ..world.service import require_line,checked_checkpoint
from .models import SceneImage,CharacterArtwork
from .service import scene_view,image_bytes
from .looks import selected_images

EXPRESSIONS=('neutral','smile','serious','surprised')


def background_signature(view):
    from .cache import appearance
    value=appearance(view)
    for key in ('cast','characterLooks','playerId'):value.pop(key,None)
    return digest(canonical(value))


def background_basis(view):
    from .cache import appearance
    value=appearance(view)
    for key in ('cast','characterLooks','playerId'):value.pop(key,None)
    if view['descriptor'].get('spaceKind')!='interior':
        # Outdoor prose can call the same trail an entrance, exit or path.
        # These inferred navigation nodes are not new buildings/furniture;
        # actual graph IDs and hash-bound authored route positions prevail.
        value['routes']=[{'id':route['id']} for route in value['routes']]
        navigation_fixture=lambda kind:kind in ('entrance','path') or kind.startswith('path-')
        value['fixtures']=[p for p in value['fixtures'] if not navigation_fixture(p.get('fixture') or '')]
        value['removedFixtures']=[key for key in value['removedFixtures'] if not navigation_fixture(key)]
    return value


def compatible_visit(configured,view):
    """Reuse an explicitly shared authored environment, never stale new props.

    Missing prose mentions do not remove old furniture. New/moved fixtures,
    an explicit removal, changed light, visual facts or routes invalidate it.
    """
    saved=configured.get('visitBasis')
    if configured.get('shareWithinWorld') is not True or not isinstance(saved,dict):return False
    wanted=background_basis(view)
    structural=('fixtures','removedFixtures')
    if {k:v for k,v in saved.items() if k not in structural}!={k:v for k,v in wanted.items() if k not in structural}:return False
    fixtures={p['id']:p for p in saved.get('fixtures',[])}
    if any(fixtures.get(p['id'])!=p for p in wanted.get('fixtures',[])):return False
    removed=set(wanted.get('removedFixtures',[]))
    return not any(p.get('fixture') in removed or p['id'] in removed for p in fixtures.values())


def context(db,owner,world_id,line_id):
    world,line=require_line(db,owner,world_id,line_id)
    cp=checked_checkpoint(db,owner,world_id,line.head_checkpoint_id)
    return world,line,cp,scene_view(cp,line_id)


def background(db,owner,world,line,view):
    configured=world.source.get('portraitStage',{}).get('backgrounds',{}).get(view['descriptor']['placeId'],{})
    row=db.get(SceneImage,configured.get('sceneId')) if configured.get('sceneId') else None
    if (not row or row.owner_id!=owner or row.world_id!=world.id
            or row.line_id!=line.id and configured.get('shareWithinWorld') is not True
            or row.status!='ready' or not row.image.get('file') or row.image.get('sha256')!=configured.get('sha256')
            or row.descriptor.get('renderStage')!='environment' or row.descriptor.get('sceneCast')
            or row.descriptor.get('placeId')!=view['descriptor']['placeId']
            or configured.get('signature')!=background_signature(view) and not compatible_visit(configured,view)):
        return None
    return row


def navigation(configured,picture,view):
    # Only actual current graph exits can be offered. Authored image positions
    # are bound to the exact received pixels, never inferred from graph yaw.
    annotations=configured.get('exits',{}) if picture else {}
    exits=[]
    for point in view['exits']:
        value={**deepcopy(point),'imageBound':False}
        binding=annotations.get(point['id'],{})
        yaw,pitch=binding.get('yaw'),binding.get('pitch')
        if (picture and binding.get('sha256')==picture.image['sha256'] and binding.get('source')=='author'
                and isinstance(yaw,(int,float)) and isinstance(pitch,(int,float))
                and isfinite(yaw) and isfinite(pitch) and abs(pitch)<=pi/2):
            value.update(yaw=yaw,pitch=pitch,imageBound=True,markerSource='author')
        exits.append(value)
    return exits


def presentation(db,owner,world_id,line_id):
    world,line,cp,view=context(db,owner,world_id,line_id)
    selected=selected_images(db,owner,world_id,[p['id'] for p in view['characters']])
    sets=world.source.get('portraitStage',{}).get('sets',{})
    roles=[]
    for person in view['characters']:
        art=selected.get(person['id']);expressions={}
        group=sets.get(person['id'],{})
        if art and group.get('baseArtworkId')==art.id:
            for expression,ident in group.get('expressions',{}).items():
                if expression not in EXPRESSIONS:continue
                item=db.get(CharacterArtwork,ident,options=[defer(CharacterArtwork.full_image),defer(CharacterArtwork.avatar_image)])
                if (item and item.owner_id==owner and item.world_id==world_id and item.entity_id==person['id']
                        and item.image.get('transparent') is True):
                    expressions[expression]='/api/world/v1/character-artworks/'+item.id+'/image'
        if art and art.image.get('transparent') is True:
            expressions.setdefault('neutral','/api/world/v1/character-artworks/'+art.id+'/image')
        roles.append({**deepcopy(person),'visibility':view['characterVisibility'].get(person['id'],'visible'),
                      'expressions':expressions,'artworkId':art.id if art else None})
    picture=background(db,owner,world,line,view)
    configured=world.source.get('portraitStage',{}).get('backgrounds',{}).get(view['descriptor']['placeId'],{})
    return {'worldId':world_id,'lineId':line_id,'checkpointId':cp.id,'placeName':view['descriptor']['placeName'],
        'description':view['descriptor']['description'],'timeLabel':view['timeLabel'],'characters':roles,
        'conversations':view['conversations'],'responses':view['responses'],
        'placeId':view['descriptor']['placeId'],'exits':navigation(configured,picture,view),
        'background':{'url':f'/api/world/v1/worlds/{world_id}/lines/{line_id}/portrait-scene/background',
            'sha256':picture.image['sha256'],'readOnly':True,'initialYaw':configured.get('initialYaw',view['descriptor']['viewpoint'].get('initialYaw',0))} if picture else None}


def background_bytes(db,owner,world_id,line_id):
    from ..errors import Problem
    world,line,cp,view=context(db,owner,world_id,line_id)
    row=background(db,owner,world,line,view)
    if not row:raise Problem('PORTRAIT_BACKGROUND_UNAVAILABLE','当前地点暂无可用的空场景，可以继续对话',404)
    return image_bytes(row)
