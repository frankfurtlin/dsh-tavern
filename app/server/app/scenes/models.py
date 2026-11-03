from sqlalchemy import String, Text, Integer, LargeBinary, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ..world.models import WorldBase, WorldOwned


class SceneProfile(WorldOwned, WorldBase):
    __tablename__ = 'scene_profiles'
    __table_args__ = (UniqueConstraint('owner_id'), {'schema':'world'})
    key_cipher: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    daily_limit: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(20), default='active')


class SceneImage(WorldOwned, WorldBase):
    __tablename__ = 'scene_images'
    __table_args__ = (UniqueConstraint('owner_id','line_id','scene_hash','generation'), {'schema':'world'})
    world_id: Mapped[str] = mapped_column(ForeignKey('world.instances.id',ondelete='CASCADE'),index=True)
    line_id: Mapped[str | None] = mapped_column(ForeignKey('world.lines.id',ondelete='CASCADE'),index=True)
    checkpoint_id: Mapped[str | None] = mapped_column(ForeignKey('world.checkpoints.id',ondelete='CASCADE'))
    profile_id: Mapped[str] = mapped_column(ForeignKey('world.scene_profiles.id',ondelete='CASCADE'))
    profile_version: Mapped[int] = mapped_column(Integer)
    scene_hash: Mapped[str] = mapped_column(String(64),index=True)
    generation: Mapped[int] = mapped_column(Integer,default=1)
    status: Mapped[str] = mapped_column(String(20),default='queued',index=True)
    descriptor: Mapped[dict] = mapped_column(JSONB)
    image: Mapped[dict] = mapped_column(JSONB,default=dict)
    request_id: Mapped[str | None] = mapped_column(String(200))
    error_code: Mapped[str | None] = mapped_column(String(50))


class CharacterArtwork(WorldOwned, WorldBase):
    """An immutable, portable pair of full-body art and its derived avatar."""
    __tablename__ = 'character_artworks'
    __table_args__ = ({'schema':'world'},)
    world_id: Mapped[str] = mapped_column(ForeignKey('world.instances.id',ondelete='CASCADE'),index=True)
    entity_id: Mapped[str] = mapped_column(String(80))
    source_kind: Mapped[str] = mapped_column(String(24))
    license_note: Mapped[str] = mapped_column(String(1000))
    descriptor: Mapped[dict] = mapped_column(JSONB)
    image: Mapped[dict] = mapped_column(JSONB)
    full_image: Mapped[bytes] = mapped_column(LargeBinary)
    avatar_image: Mapped[bytes] = mapped_column(LargeBinary)

    @property
    def status(self):return 'ready'


class CharacterLook(WorldOwned, WorldBase):
    __tablename__ = 'character_looks'
    __table_args__ = (UniqueConstraint('owner_id','world_id','entity_id'), {'schema':'world'})
    world_id: Mapped[str] = mapped_column(ForeignKey('world.instances.id',ondelete='CASCADE'),index=True)
    entity_id: Mapped[str] = mapped_column(String(80))
    selected_scene_id: Mapped[str | None] = mapped_column(ForeignKey('world.scene_images.id',ondelete='SET NULL'))
    selected_artwork_id: Mapped[str | None] = mapped_column(ForeignKey('world.character_artworks.id',ondelete='SET NULL'))
    version: Mapped[int] = mapped_column(Integer,default=1)
