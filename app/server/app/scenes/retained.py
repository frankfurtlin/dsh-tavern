"""Owned, same-checkpoint environment choices; no public intermediate artwork."""
from sqlalchemy import select
from .models import SceneImage


def environment(db,parent):
    from .pipeline import is_parent
    if not is_parent(parent):return None
    rows=list(db.scalars(select(SceneImage).where(SceneImage.owner_id==parent.owner_id,
        SceneImage.world_id==parent.world_id,SceneImage.line_id==parent.line_id,
        SceneImage.checkpoint_id==parent.checkpoint_id,SceneImage.status=='ready',
        SceneImage.profile_id==parent.profile_id,SceneImage.profile_version==parent.profile_version,
        SceneImage.descriptor['pipelineParentId'].astext==parent.id,
        SceneImage.descriptor['renderStage'].astext.in_(('environment','environment-edit'))).order_by(SceneImage.created_at).limit(128)))
    from .room_repairs import protected_people
    allowed={p['entityId'] for p in parent.descriptor.get('sceneCast',[])};usable=[]
    for index,row in enumerate(rows):
        cast=row.descriptor.get('sceneCast',[])
        if not {p['entityId'] for p in cast}.issubset(allowed):continue
        if protected_people(row.descriptor,row.image) is not None:
            usable.append(((len(cast),not row.image.get('missingExitIds'),index),row))
    if usable:return max(usable,key=lambda pair:pair[0])[1]
    return next((r for r in rows if r.descriptor.get('renderStage')=='environment'),None)


def choices(db,parent):
    from .pipeline import is_parent,base_key
    if not is_parent(parent):return []
    found=[]
    for row in db.scalars(select(SceneImage).where(SceneImage.owner_id==parent.owner_id,
            SceneImage.world_id==parent.world_id,SceneImage.line_id==parent.line_id,
            SceneImage.checkpoint_id==parent.checkpoint_id,SceneImage.profile_id==parent.profile_id,
            SceneImage.profile_version==parent.profile_version,SceneImage.status.in_(('failed','unknown','ready'))
            ).order_by(SceneImage.created_at.desc()).limit(64)):
        if not is_parent(row) or base_key(row)!=base_key(parent):continue
        image=environment(db,row)
        if image and image.image.get('sha256'):
            found.append({'sceneId':row.id,'generation':row.generation,'createdAt':row.created_at.isoformat(),
                'previewUrl':'/api/world/v1/scenes/'+row.id+'/retained-image'})
        if len(found)>=8:break
    return found
