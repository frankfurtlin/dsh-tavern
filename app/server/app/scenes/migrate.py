from sqlalchemy import text
from ..db import engine
from ..world.models import WorldBase
from .models import SceneImage, SceneProfile, CharacterLook,CharacterArtwork


def migrate():
    with engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(82370219)'))
        if not connection.scalar(text("SELECT to_regclass('world.instances')")):
            raise RuntimeError('World schema required')
        WorldBase.metadata.create_all(connection,tables=[SceneProfile.__table__,SceneImage.__table__,CharacterArtwork.__table__,CharacterLook.__table__])
        connection.execute(text('ALTER TABLE world.scene_images ALTER COLUMN line_id DROP NOT NULL, ALTER COLUMN checkpoint_id DROP NOT NULL'))


if __name__ == '__main__':
    migrate()
