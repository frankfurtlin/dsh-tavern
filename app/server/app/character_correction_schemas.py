"""Reader recognition corrections are source-backed, exact-scope user choices."""
from typing import Literal
from pydantic import Field
from .schemas import Strict, Anchor


class CharacterRecognitionInput(Strict):
    anchor: Anchor
    expectedVersion: int = Field(ge=0, strict=True)
    displayName: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(max_length=20)
    evidenceIds: list[str] = Field(min_length=1, max_length=20)
    identityStatus: Literal["confirmed", "uncertain"]
    reason: str = Field(min_length=1, max_length=600)
