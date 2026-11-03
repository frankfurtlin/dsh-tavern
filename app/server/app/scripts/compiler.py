"""Compile semantic story cards into a closed engine ledger deterministically."""
from .schemas import ScriptBlueprint
from ..world.schemas import EntityDefinition, EntityInitial, LoreEntry, PrepareOutput
from ..errors import Problem


def compile_blueprint(raw, source_blocks):
    value=ScriptBlueprint.model_validate(raw).model_dump()
    blocks={b['blockId']:b['text'] for b in source_blocks}
    def evidence(ids):
        if any(i not in blocks for i in ids):raise Problem('SCRIPT_REFERENCE_INVALID','原文引用段落不存在',422)
        return [{'blockId':i,'quote':blocks[i][:160]} for i in ids[:3]]
    entities=[];initial={}
    for p in value['places']:
        entities.append(EntityDefinition(id=p['id'],name=p['name'],type='place',description=p['description'],source='inferred',evidence=evidence(p['sourceBlockIds'])).model_dump())
        initial[p['id']]=EntityInitial().model_dump()
    for p in value['people']:
        goal=p['hiddenGoal'].strip()
        goal_evidence=p['goalEvidence']
        if goal and not goal_evidence:
            raise Problem('SCRIPT_GOAL_EVIDENCE_INVALID','人物动机需要对应原文引文',422)
        if any(e['blockId'] not in blocks or e['quote'] not in blocks[e['blockId']] for e in goal_evidence):
            raise Problem('SCRIPT_GOAL_EVIDENCE_INVALID','人物动机引用须来自对应原文段落',422)
        # A private motive is neither a public appearance nor another person's
        # knowledge. The engine injects it only into the owner's NPC context.
        # Exact motive citations remain in the private authoring blueprint.
        # Public identity citations must not acquire a criminal confession.
        entities.append(EntityDefinition(id=p['id'],name=p['name'],type='character',description=p['description'],traits=p['traits'],hiddenGoal=goal,knownFacts=p['knows'],source='inferred',evidence=evidence(p['sourceBlockIds'])).model_dump())
        initial[p['id']]=EntityInitial(alive=True,location=p['placeId'],goal=goal,known=p['knows']).model_dump()
    ids=set(initial)
    for p in value['items']:
        if p['ownerId'] not in ids:raise Problem('SCRIPT_REFERENCE_INVALID','物品的初始持有者不存在',422)
        entities.append(EntityDefinition(id=p['id'],name=p['name'],type='item',description=p['description'],stackable=p['quantity']>1,source='inferred',evidence=evidence(p['sourceBlockIds'])).model_dump())
        initial[p['ownerId']]['inventory'][p['id']]=p['quantity']
    lore=[LoreEntry(id=c['id'],title=c['title'],text=c['text'],knownBy=c['knownBy'],entityIds=c['knownBy'],privateTerms=c['privateTerms'],triggers=c['triggers'],source='inferred',evidence=evidence(c['sourceBlockIds'])).model_dump() for c in value['clues']]
    definition=PrepareOutput(title=value['title'],description=value['synopsis'],timeLabel=value['timeLabel'],location=value['startPlaceId'],entities=entities,initial=initial,lore=lore,unresolved=[value['objectives'][0]['description']],narrativeStyle=value['narrativeStyle']).model_dump()
    return {'synopsis':value['synopsis'],'definition':definition,'routes':{p['id']:p['neighbors'] for p in value['places']},**{key:value[key] for key in ('scenes','objectives','endings','recommendedCharacterId','opening','conservedItemIds')}}
