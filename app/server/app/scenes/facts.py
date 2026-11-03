"""Read-only visible facts from public ledger and explicit completed events."""
import re
from hashlib import sha256
from copy import deepcopy
from math import isfinite


def visual_facts(checkpoint,public,items,text):
    requirements=[]
    # Returning to a room must not revive a processed prop. Read only that
    # location's public ancestry, including across intervening visits elsewhere.
    # Do not turn a report in one room into a ban on all poultry in a market.
    if checkpoint is not None and hasattr(checkpoint,'_sa_instance_state'):
        from sqlalchemy.orm import object_session
        from ..world.models import WorldCheckpoint
        from ..world.state import public_payload
        db=object_session(checkpoint);ancestor=checkpoint;history=[];seen=set()
        while db and ancestor and len(seen)<128 and ancestor.id not in seen:
            seen.add(ancestor.id)
            if ancestor.owner_id!=checkpoint.owner_id or ancestor.world_id!=checkpoint.world_id:break
            if ancestor.state.get('playerId')!=checkpoint.state.get('playerId'):break
            if ancestor.state.get('location')==checkpoint.state.get('location'):
                history.append(public_payload(ancestor.text,checkpoint.state,checkpoint.definition))
            ancestor=db.get(WorldCheckpoint,ancestor.parent_checkpoint_id) if ancestor.parent_checkpoint_id else None
        text='\n'.join([*reversed(history),text])
    # Completed physical transformations prohibit the live/intact animal, not
    # a cooked meal. Intentions, negation and hypothetical events are excluded.
    for noun,english in (('鹅','goose'),('鸡','chicken'),('鸭','duck'),('鱼','fish')):
        for sentence in re.split(r'[。！？\n]',text):
            if noun not in sentence:continue
            if re.search(r'(?:如果|假如|要是|准备|打算|计划|想要|想把|正要|尚未|还没|没有|并未|未曾|请|不要|别|要先|需要|明天|稍后|待会|(?:要|会|将)(?=先|把|剖|宰|杀|烤|煮)).{0,12}(?:剖|宰|杀|烤|煮)',sentence):continue
            verbs=r'(?:剖开|宰杀|杀死|煮熟|烤熟|炖熟)'
            # Actual saved Blue prose says “我妻子把鹅嗉剖开，就在里面发现了这东西”.
            # 把 places the object before its completed action without 已/被.
            # Intentions, requests, negation and hypotheticals are fenced above.
            if not re.search(verbs+r'.{0,8}'+noun+'|'+noun+r'.{0,8}(?:已经|已|被).{0,8}'+verbs+r'|把.{0,5}'+noun+r'.{0,8}'+verbs,sentence):continue
            requirements.append({'id':'no-live-'+english,'kind':'forbidden-live-animal','name':noun,
                'instruction':'Do not depict any live or intact '+english+'. It has already been physically processed; a living animal would contradict the current story.',
                'source':{'kind':'public-completed-event','quote':sentence.strip()[:400]}})
            break
    visible={item['id'] for item in items if item.get('type')=='item'}
    # A recent PUBLIC handover to someone present cannot establish absence.
    # Never read another character's hidden inventory to make this decision.
    nearby={e['name'] for e in public.get('entities',[]) if e.get('nearby')}
    holdings=(public.get('plot') or {}).get('confirmedHoldings',[])
    possibly_here={h.get('itemName') for h in holdings if h.get('holderName') in nearby}
    for entity in public.get('entities',[]):
        if entity.get('type')!='item' or entity['id'] in visible or entity['name'] in possibly_here:continue
        requirements.append({'id':'absent-'+entity['id'],'kind':'absent-item','name':entity['name'],
            'instruction':'This known item is not physically present at the current location or in the player inventory. Do not depict it here.',
            'source':{'kind':'public-ledger','entityId':entity['id']}})
    # The public handover ledger survives after an item leaves discovered
    # entities. Do not infer a return from its absence in that entity list.
    visible_names={item['name'] for item in items if item.get('type')=='item'}
    issued={part['name'] for part in requirements}
    for holding in holdings:
        name,holder=holding.get('itemName'),holding.get('holderName')
        if (not isinstance(name,str) or not name.strip() or not isinstance(holder,str) or not holder.strip()
                or name in visible_names or name in possibly_here or name in issued):continue
        requirements.append({'id':'absent-handover-'+sha256(name.encode()).hexdigest()[:16],
            'kind':'absent-transferred-item','name':name,
            'instruction':'This publicly handed-away item is not currently visible with the player or at this location. '
                'Do not depict it here or invent its return. Reject an actual depiction of this item, not a same-coloured textile, light or decorative motif.',
            'source':{'kind':'public-handover','holderName':holder}})
        issued.add(name)
    return {'version':1,'requirements':requirements,
        **({'cameraInspectionVersion':1} if any(p['kind'] in ('forbidden-live-animal','absent-transferred-item') for p in requirements) else {})} if requirements else None


def prompt(descriptor):
    facts=descriptor.get('visualFacts') or {}
    if not facts.get('requirements'):return ''
    lines=[]
    if any(p['kind']=='forbidden-live-animal' for p in facts['requirements']):
        # Repeating the animal's name in negative prompts kept introducing it
        # into otherwise empty floor repairs. The exact per-animal constraint
        # remains in the independent image review, never relaxed here.
        lines.append('Physical processing of the food animal is complete. Paint only the present setting and listed people. '
            'Exclude living food animals; do not illustrate historical anecdotes or add their props.')
    lines.extend(p['name']+': '+p['instruction'] for p in facts['requirements'] if p['kind']!='forbidden-live-animal')
    return ('CURRENT PHYSICAL STORY FACTS override historical mentions and the input image. '
        'Change only the picture, never invent story state. '+
        '\n'.join(lines)+'\n')


def character_description(descriptor,person):
    from .public_appearance import augment
    description=augment(descriptor,person)
    nouns=[p['name'] for p in (descriptor.get('visualFacts') or {}).get('requirements',[]) if p['kind']=='forbidden-live-animal']
    return ''.join(clause for clause in re.split(r'(?<=[，,。；;])',description)
        if not any(noun in clause for noun in nouns)) if nouns else description


def evaluate(observation,facts):
    required=facts.get('requirements',[]) if isinstance(facts,dict) else []
    if not required:return []
    checks=observation.get('visualFactChecks')
    if not isinstance(checks,list) or len(checks)!=len(required):return ['visual-facts-unconfirmed']
    expected={r['id'] for r in required};seen=set();issues=[]
    for check in checks:
        if not isinstance(check,dict):return ['visual-facts-unconfirmed']
        ident=check.get('id');confidence=check.get('confidence')
        if (not isinstance(ident,str) or ident not in expected or ident in seen
                or type(check.get('consistent')) is not bool or type(confidence) not in (int,float)
                or not isfinite(confidence) or not .85<=confidence<=1
                or not isinstance(check.get('evidence'),str) or len(check['evidence'].strip())<15):
            return ['visual-facts-unconfirmed']
        seen.add(ident)
        if not check['consistent']:issues.append('visual-fact-mismatch')
    return list(dict.fromkeys(issues))
