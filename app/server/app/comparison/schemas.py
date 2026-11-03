from typing import Literal
from pydantic import Field,model_validator
from ..schemas import Strict

ID=r"^[a-f0-9]{32}$"
PROTOCOL="reader-compare-v1"


class CompareInput(Strict):
    kind:Literal["book","world"]
    leftLineId:str=Field(pattern=ID)
    rightLineId:str=Field(pattern=ID)
    bookId:str|None=Field(default=None,pattern=ID)
    worldId:str|None=Field(default=None,pattern=ID)
    leftNodeId:str|None=Field(default=None,pattern=ID)
    rightNodeId:str|None=Field(default=None,pattern=ID)
    alignment:Literal["latest","same-time"]="latest"
    atMinute:int|None=Field(default=None,ge=0,strict=True)
    leftOffset:int=Field(default=0,ge=0,le=10000,strict=True)
    rightOffset:int=Field(default=0,ge=0,le=10000,strict=True)
    limit:int=Field(default=10,ge=1,le=20,strict=True)

    @model_validator(mode="after")
    def coherent(self):
        if self.kind=="world":
            if self.bookId or not self.worldId:raise ValueError("World comparison requires one world")
            if (self.alignment=="same-time")!=(self.atMinute is not None):raise ValueError("Explicit story time required")
        elif self.worldId or not self.bookId or self.atMinute is not None or self.alignment!="latest":
            raise ValueError("Reader comparison requires one book")
        return self


class RevealInput(Strict):
    confirmed:bool=Field(strict=True)
    kind:Literal["book","world"]
    resourceId:str=Field(pattern=ID)
    lineId:str=Field(pattern=ID)
    nodeId:str=Field(pattern=ID)
    expectedVersion:int=Field(ge=0,strict=True)
