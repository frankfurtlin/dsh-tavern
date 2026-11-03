from typing import Literal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Strict):
    username: str
    password: str
    clientType: Literal["web", "android", "ios"] = "web"
    deviceName: str = Field(default="浏览器", max_length=100)
    inviteCode: str | None = Field(default=None, max_length=100)


class Refresh(Strict):
    refreshToken: str | None = None


class Anchor(Strict):
    bookId: str
    branchId: str
    revisionId: str
    chapterId: str
    blockId: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    offsetUnit: Literal["utf16"]
    quoteHash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReadingSettings(Strict):
    expectedVersion: int
    fontSize: int = Field(ge=14, le=32)
    lineHeight: float = Field(ge=1.4, le=2.2)
    theme: Literal["light", "dark", "system"]
    readingMode: Literal["scroll", "slide", "page"] | None = None
    readingWidth: int | None = Field(default=None, strict=True, ge=32, le=40)
    chapterLength: Literal["short", "standard", "long"] | None = None


class ProgressInput(Strict):
    branchId: str
    revisionId: str
    anchor: Anchor
    expectedProgressVersion: int = Field(ge=0)
    deviceId: str
    clientSeq: int = Field(ge=1)


class Target(Strict):
    mode: Literal["new_child_branch", "append_existing"] = "new_child_branch"
    name: str = Field(default="新的故事线", min_length=1, max_length=100)
    branchId: str | None = None


class Operation(Strict):
    operation: Literal["analysis", "continue_at_tail", "continue_from_anchor", "counterfactual", "role_chat", "connection_test", "style_rewrite", "world_writeback"]
    profileId: str
    chapterCount: int = Field(default=1, ge=1, le=10, strict=True)
    anchor: Anchor | None = None
    sessionId: str | None = None
    instruction: str = Field(default="", max_length=2000)
    message: str = Field(default="", max_length=4000)
    turnId: str | None = Field(default=None, max_length=100)
    expectedHeadRevisionId: str | None = None
    target: Target = Field(default_factory=Target)
    suffixPolicy: Literal["exclude_after_anchor"] = "exclude_after_anchor"
    previousTaskId: str | None = None


class StoryRunInput(Operation):
    maxCalls: int | None = Field(default=None, ge=1, le=100, strict=True)


class ConsentInput(Strict):
    planId: str
    planHash: str
    profileId: str
    maxCalls: int = Field(ge=1, le=40)
    inputTokens: int = Field(ge=1)
    outputTokens: int = Field(ge=1)


class TaskInput(Operation):
    consentId: str


class AcceptInput(Strict):
    expectedHeadRevisionId: str
    sourcePolicy: Literal["require_unchanged_head", "fork_pinned_revision"] = "require_unchanged_head"
    target: Target


class ProfileInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    baseUrl: str = Field(max_length=300)
    modelId: str = Field(min_length=1, max_length=100)
    key: str = Field(min_length=1, max_length=500)
    contextWindow: int = Field(ge=4096, le=2000000)
    outputParameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    supportsJson: bool = False
    callBudget: int = Field(default=100, ge=0)
    inputBudget: int = Field(default=1000000, ge=1)
    outputBudget: int = Field(default=100000, ge=1)


class Evidence(Strict):
    id: str = Field(validation_alias=AliasChoices('id', 'i'))
    blockId: str = Field(validation_alias=AliasChoices('blockId', 'b'))
    quote: str = Field(min_length=1, validation_alias=AliasChoices('quote', 'q'))


class CharacterOutput(Strict):
    localId: str = Field(validation_alias=AliasChoices('localId', 'i'))
    displayName: str = Field(min_length=1, max_length=100, validation_alias=AliasChoices('displayName', 'n'))
    evidenceIds: list[str] = Field(validation_alias=AliasChoices('evidenceIds', 'e'))
    priorRef: str | None = Field(default=None, validation_alias=AliasChoices('priorRef', 'p'))
    continuity: Literal["explicit", "uncertain"] = Field(default='uncertain', validation_alias=AliasChoices('continuity', 'y'))


class CharacterKnowledge(Strict):
    characterRef: str = Field(validation_alias=AliasChoices('characterRef', 'c'))
    evidenceIds: list[str] = Field(validation_alias=AliasChoices('evidenceIds', 'e'))
    learnedOrder: str | None = Field(default=None, validation_alias=AliasChoices('learnedOrder', 'o'))
    validity: Literal["at_scope", "future", "unknown"] = Field(default='unknown', validation_alias=AliasChoices('validity', 'v'))

    @field_validator('validity', mode='before')
    @classmethod
    def distinguish_fact_time(cls, value):
        # A fact happening in the past says nothing about when this character
        # learned it. Retain the fact/evidence, without granting knowledge.
        return 'unknown' if value == 'past' else value


class FactOutput(Strict):
    localId: str = Field(validation_alias=AliasChoices('localId', 'i'))
    subjectRef: str = Field(validation_alias=AliasChoices('subjectRef', 's'))
    predicate: str = Field(validation_alias=AliasChoices('predicate', 'p'))
    value: str | bool | int | None = Field(validation_alias=AliasChoices('value', 'v'))
    certainty: Literal["explicit", "inferred", "uncertain"] = Field(validation_alias=AliasChoices('certainty', 'c'))
    evidenceIds: list[str] = Field(validation_alias=AliasChoices('evidenceIds', 'e'))
    occurredOrder: str | None = Field(default=None, validation_alias=AliasChoices('occurredOrder', 'o'))
    temporalRelation: Literal["at_scope", "past", "future", "unknown"] = Field(default='unknown', validation_alias=AliasChoices('temporalRelation', 't'))
    knownBy: list[str] = Field(default_factory=list, validation_alias=AliasChoices('knownBy', 'k'))
    knowledge: list[CharacterKnowledge] = Field(default_factory=list, validation_alias=AliasChoices('knowledge', 'l'))

    @model_validator(mode='after')
    def explicit_life_state_requires_boolean(self):
        if self.predicate == 'alive' and self.certainty == 'explicit' and type(self.value) is not bool:
            raise ValueError('An explicit alive fact requires a JSON boolean')
        return self


class AnalysisOutput(Strict):
    schemaVersion: Literal[1] = Field(validation_alias=AliasChoices('schemaVersion', 'v'))
    scopeHash: str = Field(validation_alias=AliasChoices('scopeHash', 'h'))
    characters: list[CharacterOutput] = Field(validation_alias=AliasChoices('characters', 'c'))
    facts: list[FactOutput] = Field(validation_alias=AliasChoices('facts', 'f'))
    evidence: list[Evidence] = Field(validation_alias=AliasChoices('evidence', 'e'))
    unresolved: list[str] = Field(validation_alias=AliasChoices('unresolved', 'u'))
    summary: str = Field(validation_alias=AliasChoices('summary', 's'))


class ValidationOutput(Strict):
    verdict: Literal["pass", "blocked", "unknown"]
    reasonCode: Literal["SAFE", "SPOILER", "HISTORY_CONFLICT", "CHARACTER_KNOWLEDGE", "INJECTION", "UNCERTAIN"]
    evidenceIds: list[str]
    severity: Literal["none", "P0", "P1"]
