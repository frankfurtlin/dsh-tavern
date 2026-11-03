"""Bounded quoted appearance evidence from the current public narration only."""
import re

DETAIL=re.compile(r'穿着|身着|披着|戴着|脱下|换上|袖口|脚边|怀里|手里|手中|挎着|背着|握着|抱着|拿着|提着|搁在|放在')
HISTORICAL=re.compile(r'曾经|过去|从前|以前|当年|记得|回忆|如果|假如|打算|准备|将要|明天')
SPEECH=re.compile(r'[“「『"]|(?:说|问|答|说道|笑道)[：:]')


def excerpts(text,characters):
    if not isinstance(text,str):return {}
    known={p.get('id',p.get('entityId')):p.get('name') for p in characters
           if isinstance(p,dict) and p.get('id',p.get('entityId')) and isinstance(p.get('name'),str) and p['name']}
    result={};owner=None
    for paragraph in re.split(r'\n\s*\n',text[-12000:]):
        paragraph=paragraph.strip()
        if SPEECH.search(paragraph):owner=None;continue
        if not any(name in paragraph for name in known.values()) and not re.match(r'^(?:他|她|其|一只|一顶|一件)',paragraph):owner=None
        for sentence in re.split(r'(?<=[。！？])',paragraph):
            mentioned=[ident for ident,name in known.items() if name in sentence]
            if len(mentioned)==1:
                owner=mentioned[0]
                sentence=sentence[sentence.index(known[owner]):]
            elif mentioned:owner=None
            if owner is not None and DETAIL.search(sentence) and not HISTORICAL.search(sentence):
                result.setdefault(owner,[]).append(sentence.strip()[:320])
    return {ident:' '.join(dict.fromkeys(parts))[:960] for ident,parts in result.items()}


def augment(descriptor,person):
    value=person.get('description','')
    if descriptor.get('publicAppearanceContextVersion')!=1:return value
    quote=descriptor.get('publicAppearanceContext',{}).get(person.get('entityId'))
    if not isinstance(quote,str) or not quote.strip():return value
    return (value+' Current public appearance and personal objects, quoted from narration: '+quote[:960]
        +' Use this present clothing and these objects; neutral geometric guide colours are not clothing references.')


def context(checkpoint,characters):
    from ..world.state import public_payload
    current=public_payload(checkpoint.text,checkpoint.state,checkpoint.definition)
    result=excerpts(current,characters)
    wanted={p.get('id',p.get('entityId')) for p in characters}
    if wanted<=set(result) or not hasattr(checkpoint,'_sa_instance_state'):return result
    from sqlalchemy.orm import object_session
    from ..world.models import WorldCheckpoint
    db=object_session(checkpoint);cursor=checkpoint;seen={cursor.id}
    # Omitted appearance is not a wardrobe change. Read the nearest public
    # same-place ancestor, never another owner/world or hidden entity inventory.
    for _ in range(32):
        if not db or not cursor.parent_checkpoint_id:break
        parent=db.get(WorldCheckpoint,cursor.parent_checkpoint_id)
        if (not parent or parent.id in seen or parent.owner_id!=checkpoint.owner_id
                or parent.world_id!=checkpoint.world_id
                or parent.state.get('playerId')!=checkpoint.state.get('playerId')
                or parent.state.get('location')!=checkpoint.state.get('location')):break
        seen.add(parent.id)
        prior=excerpts(public_payload(parent.text,checkpoint.state,checkpoint.definition),characters)
        for ident,quote in prior.items():result.setdefault(ident,quote)
        if wanted<=set(result):break
        cursor=parent
    return result
