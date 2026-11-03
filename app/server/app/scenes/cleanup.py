from sqlalchemy import event, select
from sqlalchemy.orm import Session
from ..storage import private_path
from ..world.models import World, WorldLine
from .models import SceneImage

@event.listens_for(Session,'before_flush')
def collect_deleted(session,context,instances):
    for row in session.deleted:
        if isinstance(row,(World,WorldLine)):
            query=select(SceneImage).where(SceneImage.owner_id==row.owner_id)
            query=query.where(SceneImage.world_id==row.id) if isinstance(row,World) else query.where(SceneImage.line_id==row.id)
            for image in session.scalars(query):
                name='scene-'+image.world_id+'-'+image.id+'.enc'
                session.info.setdefault('scene_files_to_remove',set()).add((image.owner_id,name))
                session.info.setdefault('scene_files_to_remove',set()).add((image.owner_id,'scene-character-source-'+image.world_id+'-'+image.id+'.enc'))

@event.listens_for(Session,'after_commit')
def remove_deleted(session):
    for owner,name in session.info.pop('scene_files_to_remove',set()):
        private_path(owner,name).unlink(missing_ok=True)

@event.listens_for(Session,'after_rollback')
def retain_rollback(session):session.info.pop('scene_files_to_remove',None)
