from typing import Literal
import math
from pydantic import Field, StrictBool, StrictInt, StrictFloat, StrictStr, model_validator
from ..world.schemas import Strict, WorldRules, PrepareOutput
from .plot import AuthorPlot

Scalar = StrictStr | StrictInt | StrictFloat | StrictBool | list[StrictStr] | None


class CustomField(Strict):
    id: str = Field(pattern=r'^author_[a-z][a-z0-9_]{0,39}$')
    label: str = Field(min_length=1, max_length=40)
    type: Literal['text', 'longtext', 'number', 'select', 'multiselect', 'list', 'boolean', 'image', 'random'] = 'text'
    required: bool = False
    defaultValue: Scalar = None
    options: list[str] = Field(default_factory=list, max_length=20)
    minimum: float | None = None
    maximum: float | None = None
    scope: Literal['player', 'characters'] = 'player'
    width: Literal['full','half'] = 'full'
    group: str = Field(default='',max_length=40)
    presentation: Literal['default','radio','dropdown'] = 'default'
    minHeight: int = Field(default=44,ge=44,le=400)

    @model_validator(mode='after')
    def valid(self):
        if any(not option.strip() or len(option)>80 for option in self.options) or len(set(self.options))!=len(self.options):raise ValueError('选项须非空且不重复')
        if self.type in ('select','multiselect','random') and not self.options:raise ValueError('选择或随机字段需要选项')
        if any(v is not None and not math.isfinite(v) for v in (self.minimum,self.maximum)):raise ValueError('上下限必须是有限数字')
        if self.minimum is not None and self.maximum is not None and self.minimum>self.maximum:raise ValueError('最小值不能大于最大值')
        check_value(self.model_dump(), self.defaultValue, allow_missing=True)
        return self


def check_value(field, value, *, allow_missing=False):
    if value is None:
        if field['required'] and not allow_missing:raise ValueError(field['label']+'不能为空')
        return
    kind=field['type']
    if kind in ('multiselect','list'):
        if not isinstance(value,list) or len(value)>20 or any(not isinstance(v,str) or not v.strip() or len(v)>80 for v in value) or len(set(value))!=len(value):raise ValueError(field['label']+'需要最多20项不重复的文本')
        if field['required'] and not value and not allow_missing:raise ValueError(field['label']+'不能为空')
        if kind=='multiselect' and any(v not in field['options'] for v in value):raise ValueError(field['label']+'包含无效选项')
        return
    if kind=='image':
        if value=='':
            if field['required'] and not allow_missing:raise ValueError(field['label']+'不能为空')
            return
        if not isinstance(value,str) or len(value)>150000:raise ValueError(field['label']+'图片过大')
        from .service import validate_cover
        validate_cover(value)
        return
    if kind=='number':
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError(field['label']+'需要有效数字')
        if field.get('minimum') is not None and value<field['minimum'] or field.get('maximum') is not None and value>field['maximum']:raise ValueError(field['label']+'超出范围')
    elif kind=='boolean':
        if type(value) is not bool:raise ValueError(field['label']+'需要是或否')
    elif not isinstance(value,str) or len(value)>(2000 if kind=='longtext' else 500) or field['required'] and not value.strip():raise ValueError(field['label']+'内容无效')
    elif kind in ('select','random') and value not in field['options']:raise ValueError(field['label']+'需要选择有效选项')


class FixedAction(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    label: str = Field(min_length=1,max_length=40)
    message: str = Field(min_length=1,max_length=1000)
    kind: Literal['action','dialogue','observe','wait'] = 'action'


class Opening(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default='', max_length=400)
    text: str = Field(min_length=1, max_length=1200)
    characterId: str | None = None
    locationId: str | None = None


class AuthorValue(Strict):
    title: str = Field(min_length=1,max_length=160)
    summary: str = Field(min_length=1,max_length=500)
    introduction: str = Field(default='',max_length=4000)
    genre: Literal['general','mystery','romance','fantasy','political','survival'] = 'general'
    tags: list[str] = Field(default_factory=list,max_length=10)
    coverTheme: Literal['paper','blue','forest','rose'] = 'paper'
    coverDataUrl: str = Field(default='',max_length=750000)
    authorInstructions: str = Field(default='',max_length=4000)
    suggestedModel: str = Field(default='',max_length=120,pattern=r'^[A-Za-z0-9_./ :+-]{0,120}$')
    fixedActions: list[FixedAction] = Field(default_factory=list,max_length=12)
    plot: AuthorPlot | None = None
    openings: list[Opening] = Field(min_length=1,max_length=10)
    playerFields: list[CustomField] = Field(default_factory=list,max_length=12)
    statusFields: list[CustomField] = Field(default_factory=list,max_length=20)
    formTitle: str = Field(default='你的开局信息',min_length=1,max_length=60)
    statusTitle: str = Field(default='人物状态',min_length=1,max_length=60)
    statusTheme: Literal['paper','scroll','terminal','soft'] = 'paper'
    rules: WorldRules
    definition: PrepareOutput

    @model_validator(mode='after')
    def distinct(self):
        for values in (self.openings,self.playerFields,self.statusFields,self.fixedActions):
            if len({v.id for v in values})!=len(values):raise ValueError('标识不能重复')
        if set(v.id for v in self.playerFields)&set(v.id for v in self.statusFields):raise ValueError('开局和状态字段不能重名')
        if any(f.required and f.defaultValue is None for f in self.statusFields):raise ValueError('必填状态字段需要初始值')
        if any(not tag.strip() or len(tag)>30 for tag in self.tags) or not self.title.strip() or not self.summary.strip():raise ValueError('标题、摘要和标签需要有效内容')
        return self


class SaveAuthor(Strict):
    expectedVersion: int = Field(ge=0)
    value: AuthorValue


class SubmitPublication(Strict):
    expectedVersion: int = Field(ge=0)
    authorVersion: int = Field(ge=1)
    rightsConfirmed: bool
    attribution: str = Field(min_length=1,max_length=500)


class VersionInput(Strict):
    expectedVersion: int = Field(ge=1)


class ReviewPublication(VersionInput):
    decision: Literal['approve','reject','remove']
    note: str = Field(min_length=3,max_length=1000)


class ReportInput(Strict):
    reason: str = Field(min_length=5,max_length=1000)


class FavoriteInput(Strict):
    favorite: bool
