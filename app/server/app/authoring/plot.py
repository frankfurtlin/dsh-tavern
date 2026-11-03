"""Author-edited plot uses the same state and settlement validator as imports."""
from copy import deepcopy
from types import SimpleNamespace
from pydantic import Field
from ..scripts.schemas import ScriptOutput, ScriptObjective, ScriptScene, ScriptEnding
from ..world.schemas import Strict


class AuthoredScript(ScriptOutput):
    # A deliberately short handmade story need not invent three filler quests.
    scenes: list[ScriptScene] = Field(min_length=1,max_length=40)
    objectives: list[ScriptObjective] = Field(min_length=1,max_length=16)


class AuthorPlot(Strict):
    synopsis: str = Field(default='作者配置的互动剧情',max_length=1500)
    scenes: list[ScriptScene] = Field(min_length=1,max_length=40)
    objectives: list[ScriptObjective] = Field(min_length=1,max_length=16)
    endings: list[ScriptEnding] = Field(min_length=1,max_length=5)
    recommendedCharacterId: str
    opening: str = Field(min_length=1,max_length=1200)
    conservedItemIds: list[str] = Field(default_factory=list,max_length=12)


def from_world(world):
    script=world.definition.get('script') if world.definition.get('script',{}).get('authored') else world.source.get('script',{}).get('value') or world.definition.get('script')
    if not script: return None
    return {key:deepcopy(script.get(key,[] if key in ('scenes','objectives','endings','conservedItemIds') else
                         '作者配置的互动剧情' if key=='synopsis' else '')) for key in AuthorPlot.model_fields}


def compile_plot(world,value):
    from ..scripts.service import validate
    plot=deepcopy(value['plot'])
    marker={'blockId':'AUTHOR-CONFIG','chapterId':'author-setting','text':'作者改编：'+value['title']+'\n'+value['introduction'], 'source':'user'}
    if world.source.get('publicationId'):
        for entry in [*plot['scenes'],*plot['objectives'],*plot['endings'],*[s for e in plot['endings'] for s in [*e.get('settlementSteps',[]),*e.get('transitions',[])]]]:
            if entry.get('sourceBlockIds'):entry['sourceBlockIds']=[marker['blockId']]
            if 'origin' in entry:entry['origin']='adaptation'
    for entry in [*plot['scenes'],*plot['objectives']]:
        if not entry.get('sourceBlockIds'): entry['sourceBlockIds']=[marker['blockId']]
    context=SimpleNamespace(definition={'rules':value['rules']},source={**world.source,'blocks':[ *world.source.get('blocks',[]),marker]})
    complete={**plot,'definition':value['definition'],'routes':value['rules'].get('routes',{})}
    result,compiled=validate(complete,context,publication=True,authored=True)
    compiled['script']['authored']=True
    return compiled,[marker]
