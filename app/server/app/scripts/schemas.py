from typing import Literal
from pydantic import AliasChoices, Field, model_validator
from ..world.schemas import Strict, PrepareOutput, WorldRules, Evidence


class ScriptInput(Strict):
    title: str = Field(default='', max_length=160)
    text: str = Field(default='', max_length=1000000)
    sampleId: Literal['blue-carbuncle','hound-baskervilles'] | None = None
    genre: Literal['mystery','general','fantasy','romance','political','survival'] = 'mystery'

    @model_validator(mode='after')
    def source(self):
        if self.sampleId and self.text.strip():raise ValueError('请选择示例或自己的文本')
        if not self.sampleId and len(self.text.strip()) < 100:raise ValueError('请提供至少100字的完整故事')
        return self


class ScriptScene(Strict):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=800)
    placeId: str
    characterIds: list[str] = Field(default_factory=list, max_length=15)
    sourceBlockIds: list[str] = Field(min_length=1, max_length=30)


class PlotCheck(Strict):
    kind: Literal['visit','inventory','acquired','knowledge','transfer','field','location','transition']
    entityId: str = 'player'
    placeId: str | None = None
    itemId: str | None = None
    quantity: int = Field(default=1, ge=1, le=100000)
    loreId: str | None = None
    toId: str | None = None
    field: str | None = None
    value: str | bool | int | None = None


class SettlementStep(Strict):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=600)
    requires: list[str] = Field(default_factory=list, max_length=12)
    checks: list[PlotCheck] = Field(min_length=1, max_length=8)
    sourceBlockIds: list[str] = Field(default_factory=list, max_length=20)
    origin: Literal['source','adaptation'] = 'adaptation'
    completionClaims: list[str] = Field(default_factory=list, max_length=8)


class SettlementEffect(Strict):
    entityId: str
    field: str
    value: str | bool | int


class ScriptTransition(Strict):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    authorityId: str
    placeId: str
    actionTerms: list[str] = Field(min_length=1, max_length=8)
    requires: list[str] = Field(default_factory=list, max_length=12)
    checks: list[PlotCheck] = Field(min_length=1, max_length=8)
    effects: list[SettlementEffect] = Field(min_length=1, max_length=8)
    minMinutes: int = Field(default=1, ge=1, le=1440)
    origin: Literal['source','adaptation'] = 'adaptation'
    sourceBlockIds: list[str] = Field(default_factory=list, max_length=20)


class ScriptObjective(Strict):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=600)
    requires: list[str] = Field(default_factory=list, max_length=12)
    checks: list[PlotCheck] = Field(min_length=1, max_length=8)
    sourceBlockIds: list[str] = Field(min_length=1, max_length=20)


class ScriptEnding(Strict):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=800)
    requires: list[str] = Field(min_length=1, max_length=12)
    checks: list[PlotCheck] = Field(default_factory=list, max_length=8)
    sourceBlockIds: list[str] = Field(default_factory=list, max_length=20)
    selectionDescription: str = Field(default='', max_length=800)
    settlementSteps: list[SettlementStep] = Field(default_factory=list, max_length=12)
    transitions: list[ScriptTransition] = Field(default_factory=list, max_length=12)


class ScriptOutput(Strict):
    synopsis: str = Field(min_length=1, max_length=1500)
    definition: PrepareOutput
    routes: dict[str, list[str]]
    scenes: list[ScriptScene] = Field(min_length=3, max_length=40)
    objectives: list[ScriptObjective] = Field(min_length=3, max_length=16)
    endings: list[ScriptEnding] = Field(min_length=1, max_length=5)
    recommendedCharacterId: str
    opening: str = Field(min_length=1, max_length=1200)
    conservedItemIds: list[str] = Field(default_factory=list, max_length=12)


class ScriptPublish(Strict):
    expectedVersion: int
    value: ScriptOutput | None = None


class ScriptPerson(Strict):
    id: str
    name: str = Field(validation_alias=AliasChoices('name','name:'))
    description: str
    traits: str = ''
    placeId: str | None = None
    knows: list[str] = Field(default_factory=list)
    hiddenGoal: str = Field(default='', max_length=400)
    goalEvidence: list[Evidence] = Field(default_factory=list, max_length=3)
    sourceBlockIds: list[str] = Field(default_factory=list)


class ScriptPlace(Strict):
    id: str
    name: str
    description: str
    neighbors: list[str]
    sourceBlockIds: list[str] = Field(default_factory=list)


class ScriptItem(Strict):
    id: str
    name: str
    description: str
    ownerId: str
    quantity: int = Field(default=1, ge=1)
    sourceBlockIds: list[str] = Field(default_factory=list)


class ScriptClue(Strict):
    id: str
    title: str
    text: str
    knownBy: list[str]
    privateTerms: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    sourceBlockIds: list[str] = Field(default_factory=list)


class ScriptBlueprint(Strict):
    title: str
    synopsis: str
    timeLabel: str
    startPlaceId: str
    recommendedCharacterId: str
    opening: str
    narrativeStyle: str = '中文沉浸叙事，第二人称视角；人物、因果、时代与原作一致。'
    people: list[ScriptPerson] = Field(min_length=2, max_length=20)
    places: list[ScriptPlace] = Field(min_length=3, max_length=18)
    items: list[ScriptItem] = Field(default_factory=list, max_length=16)
    clues: list[ScriptClue] = Field(min_length=1, max_length=24)
    scenes: list[ScriptScene] = Field(min_length=3, max_length=40)
    objectives: list[ScriptObjective] = Field(min_length=3, max_length=16)
    endings: list[ScriptEnding] = Field(min_length=1, max_length=3)
    conservedItemIds: list[str] = Field(default_factory=list, max_length=12)
