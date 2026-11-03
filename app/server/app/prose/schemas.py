from typing import Literal
from pydantic import Field, model_validator
from ..schemas import Strict, Anchor, Target

class GenerationSelection(Strict):
    purpose: Literal['expand', 'rewrite']
    startAnchor: Anchor
    endAnchor: Anchor
    sourcePolicy: Literal['requireUnchanged', 'forkPinned'] = 'requireUnchanged'

class CreateGeneration(Strict):
    protocol: Literal['reader-prose-v1']
    originAnchor: Anchor
    operation: Literal['continue_at_tail', 'continue_from_anchor', 'counterfactual']
    chapterCount: int = Field(strict=True, ge=1, le=10)
    chapterLength: Literal['short', 'standard', 'long']
    instruction: str = Field(default='', max_length=2000)
    profileId: str
    expectedHeadRevisionId: str
    target: Target

    selection: GenerationSelection | None = None

    @model_validator(mode='after')
    def selection_shape(self):
        if self.selection is not None and (self.chapterCount != 1
                or self.operation != 'continue_from_anchor' or self.target.mode != 'new_child_branch'
                or self.selection.endAnchor != self.originAnchor):
            raise ValueError('选区写作只生成一份写法，并保留原故事线')
        return self

    def model_dump(self, *args, **kwargs):
        # Adding this optional capability must not change old durable intent hashes.
        value = super().model_dump(*args, **kwargs)
        if self.selection is None:
            value.pop('selection', None)
        return value

class AcceptSelectionGeneration(Strict):
    candidateVersion: int = Field(strict=True, ge=1)
    expectedSourceHeadRevisionId: str
    sourcePolicy: Literal['requireUnchanged', 'forkPinned'] = 'requireUnchanged'
    targetBranchId: str | None = None
    expectedTargetHeadRevisionId: str | None = None

class AcceptGeneration(Strict):
    candidateVersion: int = Field(strict=True, ge=1)
    prefixCount: int = Field(strict=True, ge=1, le=10)
    expectedAcceptedPrefix: int = Field(strict=True, ge=0, le=10)
    expectedHeadRevisionId: str
    target: Literal['child', 'append']
    sourcePolicy: Literal['requireUnchanged', 'forkPinned']

class RewriteGeneration(Strict):
    candidateVersion: int = Field(strict=True, ge=1)
    expectedAcceptedPrefix: int = Field(strict=True, ge=0, le=10)
    instruction: str = Field(default='', max_length=2000)

class SelectionEdit(Strict):
    startAnchor: Anchor
    endAnchor: Anchor
    replacementText: str | None = Field(max_length=1_000_000)
    expectedSourceHeadRevisionId: str
    sourcePolicy: Literal['requireUnchanged', 'forkPinned'] = 'requireUnchanged'
    targetBranchId: str | None = None
    expectedTargetHeadRevisionId: str | None = None
    name: str = Field(default='正文修订', min_length=1, max_length=100)

class SelectionRevisionAction(Strict):
    action: Literal['select_candidate', 'restore_original']
    candidateRevisionId: str | None = None
    expectedSourceHeadRevisionId: str
    expectedTargetHeadRevisionId: str
    sourcePolicy: Literal['requireUnchanged', 'forkPinned'] = 'requireUnchanged'

    @model_validator(mode='after')
    def candidate_for_action(self):
        if (self.action == 'select_candidate') != bool(self.candidateRevisionId):
            raise ValueError('候选选择必须指定版本，恢复原文不指定候选')
        return self
