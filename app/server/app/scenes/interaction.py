"""Scene interactions use the normal world ledger, consent and durable executor."""
from copy import deepcopy
import re
from ..world.state import public_payload
from .mentions import PAST
from .dialogue import dialogue_segments
from ..errors import Problem


def normalize_plan(plan, state, definition):
    """An obligation ID in learned is a category error, never new knowledge.

    Remove only recognized obligation IDs. Do not silently resolve obligations
    or forgive ungrounded lore: apply_plan still validates every other field.
    """
    result = deepcopy(plan)
    # The director sometimes redeclares an already registered destination while
    # moving there. An existing place ID remains authoritative: reuse it, never
    # overwrite its description or treat this redundant declaration as a new
    # entity. Other ID/type collisions still go through normal validation.
    registered = {entity['id']: entity for entity in definition.get('entities', [])}
    # Fixed fixtures may identify a place, never bypass route validation.
    invalid_location = result.get('location')
    fixture = registered.get(invalid_location, {})
    fixture_place = state.get('entities', {}).get(invalid_location, {}).get('location')
    if fixture.get('type')=='item' and not fixture_place:
        reasons=' '.join(u.get('reason','') for u in result.get('updates',[]) if u.get('field')=='location' and u.get('value')==invalid_location)
        candidates=[]
        for place in registered.values():
            if place.get('type')!='place':continue
            terms=[place['name'],*place.get('aliases',[]),*[place['name'][i:] for i in range(1,len(place['name'])-1)]]
            if any(len(term)>=2 and term in reasons and term in fixture.get('description','') for term in terms):candidates.append(place['id'])
        if len(candidates)==1:fixture_place=candidates[0]
    destination = registered.get(fixture_place, {})
    if fixture.get('type') == 'item' and destination.get('type') == 'place':
        reasons = ' '.join(u.get('reason', '') for u in result.get('updates', [])
            if u.get('field') == 'location' and u.get('value') == invalid_location)
        names = [destination['name'], *destination.get('aliases', []), *[destination['name'][i:] for i in range(1,len(destination['name'])-1)]]
        if any(len(name) >= 2 and name in reasons for name in names):
            result['location'] = fixture_place
            for update in result.get('updates', []):
                if update.get('field') == 'location' and update.get('value') == invalid_location:
                    update['value'] = fixture_place
    result['additions'] = [addition for addition in result.get('additions', [])
        if not (addition.get('type') == 'place' and registered.get(addition['id'], {}).get('type') == 'place')]
    obligations = {u['id'] for u in state.get('unresolved', [])}
    obligations.update(state.get('resolvedUnresolved', []))
    lore = {item['id'] for item in definition.get('lore', [])}
    result['learned'] = [key for key in result.get('learned', []) if key not in obligations or key in lore]
    updates = []
    resources={'money','currency','health','stamina'}|{key for costs in definition.get('rules',{}).get('actionCosts',{}).values() for key in costs if key!='minutes'}
    for update in result.get('updates', []):
        key = update.get('field', '')
        target=registered.get(update.get('entityId'),{})
        if key.startswith('relation:') and key.split(':',1)[1] not in registered:
            # A malformed optional relationship description must not discard
            # a valid confession, payment or movement. Never guess its target;
            # leave the relationship untouched and retain an explicit warning.
            warning='有一项人物关系引用未识别，原关系保留'
            if warning not in result.setdefault('warnings',[]):result['warnings'].append(warning)
            continue
        if key=='location' and target.get('type')=='item':
            reason=update.get('reason','')
            terms=[target.get('name',''),*target.get('aliases',[]),*[target.get('name','')[i:] for i in range(1,len(target.get('name',''))-1)]]
            attached=[e for e in registered.values() if e.get('type')=='character'
                and re.match(re.escape(e['name'])+r'(?:的)?[^。]{0,12}(?:依附|寄居|随)',reason)
                and re.search(r'灵魂|魂体|投影|残魂',e.get('description',''))
                and any(len(term)>=2 and term in e.get('description','') for term in terms)]
            if len(attached)==1:update={**update,'entityId':attached[0]['id']}
        if key=='presentation' and target.get('type')=='character' and update.get('value') in ('visible','voice'):
            # This is an explicit visual channel, not a resource or an inferred
            # custom field. Legacy models sometimes omit its field: prefix.
            update={**update,'field':'field:presentation'}
        elif key in ('场景观察点', '场景地点', 'field:场景观察点', 'field:场景地点'):
            # Viewpoint is player UI state, not an attribute of every companion.
            if update.get('entityId') != state['playerId']:continue
            update = {**update, 'field': key if key.startswith('field:') else 'field:' + key}
        elif key not in ('alive','location','goal','known') and ':' not in key and key not in resources:
            # A registered custom attribute has one unambiguous spelling.
            # Never infer a new field or turn a balance assignment into payment.
            fields=state.get('entities',{}).get(update.get('entityId'),{}).get('fields',{})
            if key in fields:update={**update,'field':'field:'+key}
        updates.append(update)
    result['updates'] = updates
    from .trades import normalize_receipts
    result = normalize_receipts(result, state, definition)
    from .trades import complete_priced_trade
    return complete_priced_trade(result,state,definition)


def enrich_director(data, task, parent):
    # Compact schemas are supplied beside the actual state. Empty arrays in the
    # general example otherwise invite invented amount/currencyId transfer keys.
    data['outputContract']={
        'updates':{'required':['entityId','field','value','reason','visibleTo'],
            'fieldSyntax':['alive','location','goal','known','relation:existingEntityId','field:attributeName'],
            'numericResourceAssignments':'Forbidden; use resources instead, never set a balance in updates.'},
        'transfers':{'required':['itemId','fromId','toId','quantity','reason','visibleTo'],
            'itemId':'Existing item ID, or an item explicitly created in additions. Never null.',
            'quantity':'Positive integer. The property is quantity, NOT amount.',
            'currencyId':'Not a supported property. Money belongs in resources.'},
        'resources':{'required':['entityId','field','operation','amount','reason','visibleTo'],
            'completedPurchase':'Every paid item delivery needs the actual payment here. Do not omit resources or describe payment only in events/transfers.reason.',
            'operation':['spend','earn','transfer','observe'],
            'transfer':'entityId is payer; counterpartyId is recipient. Both balances change once.',
            'fields':sorted({'money','currency','health','stamina'}|{key for cost in parent.definition['rules'].get('actionCosts',{}).values() for key in cost if key!='minutes'})}}
    data['lastCommittedTurn']={'summary':parent.summary,'text':parent.text[:2400]}
    data['outputContract']['obligations']={'pending':deepcopy(parent.state.get('unresolved',[])),
        'rule':'For EACH pending obligation, compare the committed ledger, event history and this actual outcome. Put fulfilled IDs in resolvedUnresolved and write a public completion event. A statement of completion must settle its matching obligation, not merely edit a progress attribute. Do not resolve unfulfilled work or previously resolved IDs.'}
    data['outputContract']['updates']['registeredAttributes']={entity_id:sorted(entity.get('fields',{}))
        for entity_id,entity in parent.state.get('entities',{}).items()}
    data['outputContract']['updates']['newAttributes']='Never invent an unregistered field such as 炼药经验. Describe teaching/actions in public events; only update existing attributes.'
    data['outputContract']['additions']='Created evidence, copies and crafted products need an item addition AND a transfer fromId:null to the actual holder. A definition alone is not possession. Existing items must come from their actual inventory holder.'
    data['outputContract']['entityDefinitionFields']=['id','name','type','aliases','description','traits','evidence','source','hiddenGoal','knownFacts','knownBy','stackable','sourceCharacterId']
    data['outputContract']['itemIdentity']='A sample, copy and original are DIFFERENT item IDs. Never use a receipt/document ID to transfer a powder sample. Returning an item already held in the authoritative inventory requires NO second transfer.'
    data['outputContract']['servicePayment']='For a payee whose money balance is unknown, record the payer spend only; never invent an earn/zero payee balance. Known payer/payee balances use a single transfer.'
    data['placeRegistry'] = [{key: deepcopy(entity.get(key, [] if key == 'aliases' else ''))
        for key in ('id', 'name', 'aliases')}
        for entity in parent.definition.get('entities', []) if entity['type'] == 'place']
    data['outputContract']['location']='Use ONLY placeRegistry IDs, never an item ID (a cauldron is not a mountain). Keep item IDs and place IDs distinct. Recommended travel options must be reachable along rules.routes.'
    data['outputContract']['companionMovement']='When an attached spirit follows its carried vessel, update the CHARACTER entity ID, not the ring/item ID. Check entity names and types against the registry. Do not leave a following character at the previous place.'
    intent = task.payload.get('sceneIntent')
    if intent:
        data['sceneIntent'] = deepcopy(intent)
        data['placeRegistry'] = [{key: deepcopy(entity.get(key, [] if key == 'aliases' else ''))
            for key in ('id', 'name', 'aliases')}
            for entity in parent.definition.get('entities', []) if entity['type'] == 'place']
    if task.payload.get('interactionMode') == 'dialogue':
        data['outputMode'] = 'dialogue'
        memory = parent.memory.get('sceneConversation', {})
        if memory.get('playerId') == parent.state['playerId']:
            partner = task.payload.get('selectedTarget', {}).get('entityId')
            data['conversation'] = public_payload([{**entry, 'user': entry['user'][-800:], 'reply': entry['reply'][-1200:]}
                for entry in memory.get('entries', []) if entry.get('partnerId') == partner][-6:], parent.state, parent.definition)
    return data


def record_interaction(checkpoint, parent, task, turn, segments=None):
    """Called after save_memory, in the same atomic checkpoint transaction."""
    memory = deepcopy(checkpoint.memory)
    if segments is not None:
        memory['sceneTranscript']={'checkpointId':checkpoint.id,'segments':deepcopy(segments)}
    from sqlalchemy.orm import object_session
    from ..world.models import WorldCheckpoint
    db = object_session(checkpoint)
    if db:parent = db.get(WorldCheckpoint, parent.id)
    from .service import scene_view
    prior_view = scene_view(parent)
    visible_ids = {entity['id'] for entity in prior_view['characters']}
    absent = []
    for entity in checkpoint.definition['entities']:
        if entity['type']!='character' or entity['id']==checkpoint.state['playerId']:continue
        actual = checkpoint.state['entities'].get(entity['id'], {})
        if actual.get('location')!=checkpoint.state['location']:continue
        previous = parent.state['entities'].get(entity['id'], {})
        entered = parent.state['location']!=checkpoint.state['location'] or previous.get('location')!=actual.get('location')
        if not public_presence(entity, checkpoint.text, entered or entity['id'] in visible_ids):absent.append(entity['id'])
    memory['scenePresence'] = {'location': checkpoint.state['location'], 'playerId': checkpoint.state['playerId'], 'absentCharacterIds': absent}
    if task.payload.get('interactionMode') == 'dialogue':
        target = task.payload['selectedTarget']
        previous = parent.memory.get('sceneConversation', {})
        entries = deepcopy(previous.get('entries', [])) if previous.get('playerId') == parent.state['playerId'] else []
        entries.append({'turnId': turn.id, 'checkpointId': checkpoint.id,
            'partnerId': target['entityId'], 'partnerName': target['name'],
            'user': turn.action, 'reply': checkpoint.text,
            'segments': segments if segments is not None else dialogue_segments(checkpoint.text, target['name'])})
        memory['sceneConversation'] = {'playerId': parent.state['playerId'], 'entries': entries[-40:]}
        task.result = {**task.result, 'interactionMode': 'dialogue', 'partnerId': target['entityId'], 'message': turn.action}
    origin, destination = parent.state['location'], checkpoint.state['location']
    if origin != destination and parent.state['playerId']==checkpoint.state['playerId']:
        connections = deepcopy(parent.memory.get('sceneConnections', []))
        for pair in journey_connections(parent,checkpoint):
            if pair not in connections:connections.append(pair)
        memory['sceneConnections'] = connections[-128:]
    checkpoint.memory = memory


def journey_connections(parent, checkpoint):
    """Record actual committed journeys, including typed actions and intermediate stops."""
    from ..world.routes import journey_path
    origin,destination=parent.state['location'],checkpoint.state['location']
    if origin==destination or parent.state['playerId']!=checkpoint.state['playerId']:return []
    try:path=journey_path(checkpoint.definition,origin,destination)
    except Problem:return []
    return [{'from':a,'to':b} for a,b in zip(path,path[1:])]


def travelled_connections(checkpoint):
    """Repair older receipts by reading their own ancestry, without mutating saves."""
    from sqlalchemy.orm import object_session
    from ..world.models import WorldCheckpoint
    result=deepcopy(checkpoint.memory.get('sceneConnections',[]));db=object_session(checkpoint)
    child=checkpoint;seen=set()
    for _ in range(256):
        if not db or not child or child.id in seen:break
        seen.add(child.id)
        parent=db.get(WorldCheckpoint,child.parent_checkpoint_id) if child.parent_checkpoint_id else None
        if not parent or parent.owner_id!=checkpoint.owner_id or parent.world_id!=checkpoint.world_id or parent.state['playerId']!=checkpoint.state['playerId']:break
        if child.kind=='turn':
            for pair in journey_connections(parent,child):
                if pair not in result:result.append(pair)
        child=parent
    return result


def arrival_origin(checkpoint):
    from sqlalchemy.orm import object_session
    from ..world.models import WorldCheckpoint
    db=object_session(checkpoint);child=checkpoint;seen=set()
    for _ in range(256):
        if not db or child.id in seen:break
        seen.add(child.id)
        parent=db.get(WorldCheckpoint,child.parent_checkpoint_id) if child.parent_checkpoint_id else None
        if not parent or parent.owner_id!=checkpoint.owner_id or parent.world_id!=checkpoint.world_id or parent.state['playerId']!=checkpoint.state['playerId']:break
        if parent.state['location']!=checkpoint.state['location']:
            return parent.state['location'] if child.kind=='turn' else None
        child=parent
    return None


def visible_destination_name(entity, text, origin_name=''):
    """A physically described, public exit can lead somewhere not yet visited."""
    names=[entity['name'], *entity.get('aliases', [])]
    # The same household's "萧家后山" is called "后山" in its courtyard.
    # Derive only a shared contextual prefix, never an arbitrary suffix match.
    common=0
    for left,right in zip(origin_name,entity['name']):
        if left!=right:break
        common+=1
    if common>=2 and len(entity['name'])-common>=2:names.append(entity['name'][common:])
    for sentence in re.split(r'[。！？\n]', text):
        if any(word in sentence for word in (*PAST, '如果', '假如', '明天', '打算', '想去', '听说', '传说')):continue
        if not re.search(r'通往|通向|直通|接入|连通|连接|通到|蜿蜒|延伸', sentence):continue
        if not re.search(r'路|石阶|台阶|门|入口|出口|场边|通道|走廊', sentence):continue
        for name in sorted(filter(None, names), key=len, reverse=True):
            if name in sentence:return name
    return None


def scene_exits(view, checkpoint, layout):
    location = view['location']
    routes = view['rules'].get('routes', {})
    destinations = set(routes.get(location, []))
    origin=arrival_origin(checkpoint)
    # A multi-stop trip can offer the whole return journey as one action, while
    # the normal route validator still checks every leg (including one-way locks).
    if origin:
        from ..world.routes import journey_path
        try:journey_path(checkpoint.definition,location,origin)
        except Problem:origin=None
        if origin:destinations.add(origin)
    # Only journeys actually committed on this ancestry create return routes.
    if location not in routes:
        for edge in travelled_connections(checkpoint):
            if edge.get('from') == location: destinations.add(edge.get('to'))
            if edge.get('to') == location: destinations.add(edge.get('from'))
    portals = [node for node in layout['nodes'] if node.get('fixture') in ('entrance', 'path') or node.get('fixture','').startswith('path-')]
    door = portals[0] if portals else None
    places=[entity for entity in view['entities'] if entity['type']=='place' and entity['id'] in destinations and entity['id']!=location]
    # public_view filters unvisited places from its map, but a visible doorway
    # must still be usable. Reveal only its already-public name, never the
    # destination's description, occupants, lore or the rest of the hidden map.
    public_place=next((e for e in view['entities'] if e['id']==location), {})
    text=public_payload(checkpoint.text,checkpoint.state,checkpoint.definition)+'\n'+public_place.get('description','')
    outgoing=set(checkpoint.definition.get('rules',{}).get('routes',{}).get(location, []))
    represented={e['id'] for e in places}
    for entity in checkpoint.definition.get('entities', []):
        if entity['type']!='place' or entity['id'] not in outgoing or entity['id'] in represented:continue
        name=visible_destination_name(entity,text,public_place.get('name',''))
        # Compiled scripts declare physical adjacent paths explicitly. Their
        # unvisited destination names are navigational information, while the
        # destination's occupants, clues and further connections stay private.
        # Ordinary imported worlds can contain secret routes and still require
        # the public doorway evidence above. Every click uses journey_path.
        if not name and checkpoint.definition.get('script'):name=entity['name']
        if name:places.append({'id':entity['id'],'name':name})
    result=[]
    for index,entity in enumerate(places):
        portal=portals[index%len(portals)] if portals else None
        result.append({'id':entity['id'],'name':('返回' if entity['id']==origin else '')+entity['name'],'kind':'place','direction':('沿原路返回' if entity['id']==origin else '前往')+entity['name'],
            'yaw':(portal['yaw'] if portal else .85)+(index//max(1,len(portals)))*.48,
            'pitch':-.1,'portalId':portal['id'] if portal else None})
    # A described doorway offers exploration, not an invented destination or
    # an unconditional teleport through a locked door/fixed-route restriction.
    if location not in routes:
        for portal in portals:
            path_name=portal['name'] if portal['fixture'].startswith('path-') else '沿路继续探索'
            result.append({'id': 'exit:' + portal['id'], 'name': '探索门外' if portal['fixture']=='entrance' else path_name, 'kind': 'exit',
                'direction': '走出' + next(e['name'] for e in view['entities'] if e['id'] == location) if portal['fixture']=='entrance' else path_name+'，到达这条通路连接的下一个地点',
                'yaw': portal['yaw'], 'pitch': -0.1, 'portalId':portal['id'],'source': portal.get('source', {})})
    return result


def public_presence(entity, text, default=True):
    """An explicit current absence wins over a stale co-location ledger."""
    name = re.escape(entity['name'])
    absent = re.compile(r'(?:没有看见|没有见到|没看到|不见)' + name + r'|' + name + r'(?:已经|已|此刻|现在|并)?(?:离开|离去|不在这里|不在屋内|不在场|不在客舍|不在附近|走远|离开了)|'
        + name + r'[^。！？\n说问答想]{0,80}(?:开门下楼|走出(?:房|屋|大|店|客舍|寓所)?门|走出(?:屋|店|客舍|寓所)|出门而去|转身离开|离开了(?:这里|房间|屋子|客舍|寓所)|走远了|告辞|离席而去)')
    present = re.compile(name + r'(?:此刻|现在|仍|还|正)?(?:坐在|站在|就在|走进|回到|来到|在你身旁|在你面前)')
    status = default
    # Quoted intentions and another person's instructions are not departures.
    text=re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', '', text)
    for sentence in re.split(r'[。！？\n；;]', text):
        if any(word in sentence for word in (*PAST,'如果','假如','明天','打算','准备离开','准备告辞','将要','还没','尚未')):continue
        if absent.search(sentence):status=False
        elif present.search(sentence):status=True
    return status
