from typing import Literal
from pydantic import Field, model_validator
from ..schemas import Strict, Anchor

class AssetScope(Strict):
    kind: Literal["book", "world"]
    bookId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    branchId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    revisionId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    worldId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    lineId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    checkpointId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    identityId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    entityId: str | None = Field(default=None, min_length=1, max_length=80)
    anchor: Anchor | None = None

    @model_validator(mode="after")
    def exact_kind(self):
        book = (self.bookId, self.branchId, self.revisionId)
        world = (self.worldId, self.lineId, self.checkpointId)
        if self.kind == "book" and (not all(book) or any(world) or self.entityId):
            raise ValueError("Book images require one exact book/branch/revision")
        if self.kind == "world" and (not all(world) or any(book)):
            raise ValueError("World images require one exact world/line/checkpoint")
        if self.kind == "world" and self.anchor:
            raise ValueError("World images do not use a reader anchor")
        if self.anchor and (self.anchor.bookId,self.anchor.branchId,self.anchor.revisionId) != book:
            raise ValueError("Image anchor must match its book scope")
        return self

    def public(self):
        return self.model_dump(exclude_none=True)

class Crop(Strict):
    x: int = Field(strict=True, ge=0)
    y: int = Field(strict=True, ge=0)
    width: int = Field(strict=True, ge=1)
    height: int = Field(strict=True, ge=1)

class PlacementInput(Strict):
    assetId: str = Field(pattern=r"^[a-f0-9]{32}$")
    scope: AssetScope
    purpose: Literal["cover", "avatar", "scene", "illustration"]
    contentId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    paragraphId: str | None = Field(default=None, min_length=1, max_length=80)
    side: Literal["before", "after"] = "after"
    order: int = Field(strict=True, ge=0, le=1000, default=0)
    alt: str = Field(max_length=300, default="")
    placementId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    expectedVersion: int | None = Field(default=None, strict=True, ge=1)

class PlacementDelete(Strict):
    expectedVersion: int = Field(strict=True, ge=1)
