from sqlalchemy import ForeignKey, String, Integer, LargeBinary, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ..models import Identity, User

class AssetsBase(DeclarativeBase):
    pass

User.__table__.to_metadata(AssetsBase.metadata)

class AssetOwned(Identity):
    owner_id: Mapped[str] = mapped_column(ForeignKey(User.__table__.c.id, ondelete="CASCADE"), index=True)

class ImageAsset(AssetOwned, AssetsBase):
    __tablename__ = "image_assets"
    purpose: Mapped[str] = mapped_column(String(20))
    source_kind: Mapped[str] = mapped_column(String(40), default="user_upload")
    license_note: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(40))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    origin_scope: Mapped[dict] = mapped_column(JSONB)
    crop: Mapped[dict] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("id", "owner_id"),)

class AssetVariant(AssetsBase):
    __tablename__ = "image_asset_variants"
    asset_id: Mapped[str] = mapped_column(ForeignKey("image_assets.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(String(24), primary_key=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(40))
    data: Mapped[bytes] = mapped_column(LargeBinary)

class AssetPlacement(AssetOwned, AssetsBase):
    __tablename__ = "image_asset_placements"
    asset_id: Mapped[str] = mapped_column(ForeignKey("image_assets.id", ondelete="RESTRICT"), index=True)
    scope: Mapped[dict] = mapped_column(JSONB)
    purpose: Mapped[str] = mapped_column(String(20))
    content_id: Mapped[str | None] = mapped_column(String(32))
    paragraph_id: Mapped[str | None] = mapped_column(String(80))
    side: Mapped[str] = mapped_column(String(16), default="after")
    order: Mapped[int] = mapped_column(Integer, default=0)
    alt: Mapped[str] = mapped_column(String(300), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)

ASSET_TABLES = [ImageAsset.__table__, AssetVariant.__table__, AssetPlacement.__table__]

