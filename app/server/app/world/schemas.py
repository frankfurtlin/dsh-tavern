from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator
from ..schemas import Anchor

FieldValue = str | int | float | bool | None | Annotated[list[Annotated[StrictStr,Field(max_length=80)]],Field(max_length=20)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorldRules(Strict):
    genre: Literal["general", "mystery", "romance", "fantasy", "political", "survival"] = "general"
    deathPolicy: Literal["end", "rescue", "rebirth"] = "end"
    relationshipNumbers: bool = False
    randomChecks: bool = False
    pace: Literal["short", "standard", "detailed"] = "standard"
    style: str = Field(default="", max_length=500)
    customRules: str = Field(default="", max_length=1500)
    actionCosts: dict[Literal["action", "dialogue", "observe", "wait"], dict[str, float]] = Field(default_factory=dict)
    abilityRequirements: dict[Literal["action", "dialogue", "observe", "wait"], dict[str, str | float]] = Field(default_factory=dict)
    routes: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("actionCosts")
    @classmethod
    def positive_costs(cls, value):
        if any(not key or cost < 0 or cost > 100000 or cost != cost for costs in value.values() for key, cost in costs.items()):
            raise ValueError("行动代价必须是明确的非负有限数字")
        return value


class CreateWorld(Strict):
    anchor: Anchor | None = None
    templateId: Literal["original-mystery-v1", "original-fantasy-v1", "original-romance-v1", "douluo-forest-v1"] | None = None
    name: str = Field(default="", max_length=160)
    rules: WorldRules = Field(default_factory=WorldRules)
    fullBookReference: bool = False
    maxCalls: int = Field(default=12, ge=1, le=100)

    @model_validator(mode="after")
    def one_source(self):
        if (self.anchor is None) == (self.templateId is None):
            raise ValueError("Choose either a reading anchor or an original template")
        if self.templateId and self.fullBookReference:
            raise ValueError("An original world has no full source book")
        return self


class BeginWorld(Strict):
    useSuggestedModel: bool = True
    openingId: str | None = Field(default=None,max_length=60)
    playerValues: dict[str, FieldValue] = Field(default_factory=dict,max_length=12)
    characterId: str | None = None
    customName: str = Field(default="", max_length=80)
    customIdentity: str = Field(default="", max_length=800)
    opening: str = Field(default="", max_length=1200)
    lineName: str = Field(default="我的故事", min_length=1, max_length=120)


class ActionInput(Strict):
    message: str = Field(min_length=1, max_length=5000)
    kind: Literal["action", "dialogue", "observe", "wait"] = "action"
    expectedCheckpointId: str
    turnId: str = Field(min_length=1, max_length=100)

    @field_validator("message")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("写下你想做的事，或选择等待")
        return value


class ForkInput(Strict):
    checkpointId: str
    mode: Literal["continue", "rechoose", "restart", "identity"] = "continue"
    name: str = Field(default="另一种可能", min_length=1, max_length=120)
    characterId: str | None = None
    customName: str = Field(default="", max_length=80)
    customIdentity: str = Field(default="", max_length=800)


class EditWorld(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: Literal["active", "archived"] | None = None
    expectedVersion: int


class EditLine(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "archived"] | None = None
    expectedVersion: int
    readingPosition: dict | None = None
    camera: dict | None = None


class WriteInput(Strict):
    chapterCount: int = Field(default=1, ge=1, le=10)
    instruction: str = Field(default="", max_length=1500)
    expectedCheckpointId: str


class AcceptWrite(Strict):
    chapterCount: int = Field(ge=1, le=10)
    expectedCheckpointId: str


class CorrectionInput(Strict):
    expectedCheckpointId: str
    entityId: str | None = None
    field: str = Field(min_length=1, max_length=120)
    value: object
    reason: str = Field(min_length=1, max_length=800)
    name: str = Field(default="调整后的世界", min_length=1, max_length=120)


class RewriteInput(Strict):
    checkpointId: str
    instruction: str = Field(default="", max_length=1500)


class Evidence(Strict):
    blockId: str
    quote: str = Field(min_length=1, max_length=600)


class EntityDefinition(Strict):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    type: Literal["character", "place", "item", "faction"]
    aliases: list[str] = Field(default_factory=list, max_length=10)
    description: str = Field(default="", max_length=1000)
    traits: str = Field(default="", max_length=500)
    evidence: list[Evidence] = Field(default_factory=list, max_length=8)
    source: Literal["original", "inferred", "game", "user"] = "original"
    hiddenGoal: str = Field(default="", max_length=400)
    knownFacts: list[str] = Field(default_factory=list, max_length=30)
    knownBy: list[str] = Field(default_factory=list, max_length=30)
    stackable: bool = False
    sourceCharacterId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class EntityInitial(Strict):
    alive: bool | None = None
    location: str | None = None
    inventory: dict[str, int] = Field(default_factory=dict)
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    relations: dict[str, str] = Field(default_factory=dict)
    goal: str = Field(default="", max_length=500)
    known: list[str] = Field(default_factory=list)


class LoreEntry(Strict):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=150)
    text: str = Field(min_length=1, max_length=1200)
    privateTerms: list[str] = Field(default_factory=list, max_length=20)
    triggers: list[str] = Field(default_factory=list, max_length=15)
    entityIds: list[str] = Field(default_factory=list)
    knownBy: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list, max_length=8)
    source: Literal["original", "inferred", "game", "user"] = "original"

    @field_validator("privateTerms")
    @classmethod
    def bounded_private_terms(cls, value):
        if any(not term.strip() or len(term) > 100 for term in value):
            raise ValueError("秘密词项必须是非空且不超过100字的准确原词")
        return list(dict.fromkeys(value))


class PrepareOutput(Strict):
    title: str = Field(max_length=160)
    description: str = Field(max_length=1200)
    timeLabel: str = Field(max_length=100)
    location: str
    entities: list[EntityDefinition] = Field(max_length=50)
    initial: dict[str, EntityInitial]
    lore: list[LoreEntry] = Field(default_factory=list, max_length=50)
    unresolved: list[str] = Field(default_factory=list, max_length=20)
    narrativeStyle: str = Field(default="", max_length=400)


class Update(Strict):
    entityId: str
    field: str = Field(max_length=100)
    value: object
    reason: str = Field(min_length=1, max_length=400)
    visibleTo: list[str] = Field(default_factory=list)


class Transfer(Strict):
    itemId: str
    fromId: str | None = None
    toId: str | None = None
    quantity: int = Field(ge=1, le=100000)
    reason: str = Field(min_length=1, max_length=400)
    visibleTo: list[str] = Field(default_factory=list)


class EventOutput(Strict):
    type: str = Field(max_length=40)
    entityId: str | None = None
    description: str = Field(min_length=1, max_length=500)
    visibleTo: list[str] = Field(default_factory=list)
    dueMinute: int | None = Field(default=None, ge=0)
    loreIds: list[str] = Field(default_factory=list, max_length=15)
    requiresDecision: bool = False


class ResourceOperation(Strict):
    entityId: str
    field: str = Field(min_length=1, max_length=80)
    operation: Literal["earn", "spend", "observe", "transfer"]
    amount: float = Field(ge=0, le=100000000, allow_inf_nan=False)
    counterpartyId: str | None = None
    reason: str = Field(min_length=1, max_length=400)
    evidence: list[Evidence] = Field(default_factory=list, max_length=8)
    visibleTo: list[str] = Field(default_factory=list)


class Option(Strict):
    text: str = Field(min_length=1, max_length=80)
    kind: Literal["action", "dialogue", "observe", "wait"] = "action"


class EventResolution(Strict):
    eventId: str
    status: Literal["fulfilled", "missed", "cancelled"]
    description: str = Field(min_length=1, max_length=500)
    atMinute: int | None = Field(default=None, ge=0)


class WorkItem(Strict):
    itemId: str
    quantity: int = Field(ge=1, le=100000)


class WorkOperation(Strict):
    id: str = Field(min_length=1, max_length=100)
    operation: Literal['start', 'continue', 'complete', 'cancel']
    title: str = Field(min_length=1, max_length=100)
    inputs: list[WorkItem] = Field(default_factory=list, max_length=20)
    outputs: list[WorkItem] = Field(default_factory=list, max_length=10)


class BlockedTransition(Strict):
    transitionId: str
    reason: str = Field(min_length=1, max_length=400)


class DirectorOutput(Strict):
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(default="", max_length=600)
    elapsedMinutes: int = Field(ge=0, le=525600)
    timeLabel: str = Field(max_length=100)
    location: str | None = None
    updates: list[Update] = Field(default_factory=list, max_length=30)
    transfers: list[Transfer] = Field(default_factory=list, max_length=20)
    resources: list[ResourceOperation] = Field(default_factory=list, max_length=20)
    events: list[EventOutput] = Field(default_factory=list, max_length=20)
    eventResolutions: list[EventResolution] = Field(default_factory=list, max_length=20)
    additions: list[EntityDefinition] = Field(default_factory=list, max_length=5)
    newLore: list[LoreEntry] = Field(default_factory=list, max_length=5)
    learned: list[str] = Field(default_factory=list, max_length=15)
    publicOutcome: str = Field(min_length=1, max_length=1500)
    options: list[Option] = Field(default_factory=list, max_length=5)
    critical: list[str] = Field(default_factory=list, max_length=10)
    resolvedUnresolved: list[str] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=5)
    work: list[WorkOperation] = Field(default_factory=list, max_length=5)
    transitionIds: list[str] = Field(default_factory=list, max_length=4)
    blockedTransitions: list[BlockedTransition] = Field(default_factory=list, max_length=4)


class NarrativeReview(Strict):
    consistent: bool
    issues: list[str] = Field(default_factory=list, max_length=8)
