"""Public, named scene areas and a separate, reproducible rendering layout."""
from hashlib import sha256
from math import atan2,pi,hypot,sin,cos
import re
from .mentions import current_object_mention, absent_object_mention, PAST

VERSION=2

def space_kind(place):
    name=place.get('name','')
    explicit=place.get('spaceKind')
    if explicit in ('interior','open-air courtyard','open-air street or market','outdoor natural landscape'):return explicit
    # The last location noun wins: a town's inn is indoors, its inn doorway
    # viewed from outside is outdoors. Distant scenery in a description is not
    # evidence that the observer is standing there.
    if re.search(r'门外|屋外|楼外|外面|路口|outside|outdoors',name,re.I):return 'open-air setting'
    kinds=[('interior',r'客舍|寓所|居所|住所|宅邸|公寓|客栈|酒馆|药铺|拍卖场|图书馆|书店|照相馆|大堂|书房|起居室|客厅|走廊|(?:室|厅|房)$|\b(?:inn|tavern|library|bookstore|apartment|room|shop|corridor)\b'),
           ('open-air courtyard',r'练武场|演武场|广场|庭院|院落|\b(?:courtyard|plaza)\b'),
           ('open-air street or market',r'街道|街市|坊市|镇|村|码头|栈道|\b(?:street|market|pier)\b'),
           ('outdoor natural landscape',r'后山|山脉|林地|森林|林道|营地|浅溪|溪岸|溪边|山道|山路|河岸|\b(?:forest|mountain|riverbank|campsite|trail)\b')]
    matches=[(m.end(),kind) for kind,pattern in kinds for m in re.finditer(pattern,name,re.I)]
    named=max(matches,key=lambda match:match[0])[1] if matches else None
    if named and named!='open-air street or market':return named
    # A proper name may contain no space-type word. Use the current place's
    # description, rather than treating every unknown name as open countryside.
    description=place.get('description','')
    if re.search(r'起居室|客厅|屋内|室内|屋里|大厅|书房|柜台|天花板|壁炉|\b(?:indoors|sitting room|ceiling|fireplace)\b',description,re.I):return 'interior'
    return named or 'unspecified setting'
# Only structural fixtures describe a place to stand. Portable props and people
# are interaction targets, never invented destinations.
AREAS=[('hearth','炉边',r'火炉|炉子|炉边|炉火|壁炉|炭火',(2.2,-1.8)),
       ('bench','条凳旁',r'条凳|长凳|长椅',(-2.0,1.0)),
       ('table','桌边',r'木桌|桌边|桌上|桌旁',(0.5,-1.0)),
       ('window','窗边',r'窗户|窗边|窗外|窗前',(-2.8,-2.2)),
       ('entrance','门口',r'房门|门口|门边|门外|入口|出口|大门|木门|院门|城门|后门|侧门|门洞',(0.2,3.0)),
       ('counter','柜台前',r'柜台|柜前',(2.8,1.5)),
       ('steps','台阶旁',r'台阶|阶前',(-1.5,2.6)),
       ('path','道路',r'山道|小路|道路|石径|街道|通道|通路|走廊|栈道|步道',(-0.2,-3.0))]

def stable_id(place_id,key):return 'area-'+sha256((place_id+':'+key).encode()).hexdigest()[:16]


def route_visual_cues(exits,kind):
    """Private render landmarks distinguish routes without revealing a hidden
    destination's description, occupants or lore. These are illustration
    instructions; the final inspector must still find the actual painted cue.
    """
    if kind=='interior':return {}
    motifs=('short square stone border posts','low timber rails',
            'small stacks of rounded border stones')
    result={}
    for index,exit in enumerate(sorted(exits,key=lambda p:p['id'])):
        name=exit['name']
        if re.search(r'酒馆|客栈|\b(?:tavern|inn)\b',name,re.I):
            cue='Open recessed tavern DOORWAY into the tavern interior, with visible interior floor through the threshold, amber windows and wooden barrels beside this SAME doorway. An adjacent street arch is not this route.'
        elif re.search(r'市场|坊市|集市|\bmarket\b',name,re.I):
            cue='Open walking passage toward visible canvas awnings and empty market stalls, without any people.'
        elif re.search(r'寓所|宅邸|公寓|住所|住宅|居所|民居|\b(?:apartment|residence|townhouse|home|house)\b',name,re.I):
            cue='Open townhouse DOORWAY into its residential interior entrance hall, with visible interior floor beyond the same threshold. THREE exterior stone steps directly below this doorway join its interior floor. A street arch or continuing outdoor lane is not this route.'
        else:cue='Open walking passage.'
        result[exit['id']]=cue
    # Only otherwise indistinguishable destinations need extra border motifs.
    # A tavern doorway, residential steps and a canvas market already differ;
    # gratuitous object-count requirements distract from the actual threshold.
    bases=list(result.values());ordinals={}
    for ident,cue in list(result.items()):
        if bases.count(cue)<2:continue
        index=ordinals.get(cue,0);ordinals[cue]=index+1
        result[ident]=cue+' At this opening only, '+str(index//len(motifs)+1)+' '+motifs[index%len(motifs)]+'.'
    return result

def route_fixture_links(exits,layout):
    """Connect source-named exterior stairs to their own destination entrance.

    Generic entrance and stair IDs can differ. An exact public destination
    name in the source quote establishes the additional relationship. An
    ambiguous or unrelated stair keeps its independent geometry.
    """
    exits=[route for route in exits if isinstance(route.get('id'),str)]
    links={route['id']:[] for route in exits}
    for node in layout.get('nodes',[]):
        if node.get('fixture')!='steps':continue
        quote=node.get('source',{}).get('quote','');matches=[]
        for route in exits:
            name=re.sub(r'^(?:返回|前往|走回|走到|通往)\s*','',route.get('name','')).strip()
            if route.get('portalId')==node['id'] or len(name)>=2 and name in quote:
                matches.append(route['id'])
        if len(matches)==1:links[matches[0]].append(node['id'])
    return {ident:ids for ident,ids in links.items() if ids}


def build_layout(place,items,text,current_text=None,removed_fixtures=(),allow_background_evidence=True):
    origin={'id':'entry','name':place['name'],'direction':'走回'+place['name'],'x':50,'y':50,'worldX':0.0,'worldZ':0.0,'source':{'entityId':place['id']},'yaw':0,'pitch':0}
    nodes=[origin]
    # Spoken advice can describe a distant street or tomorrow's destination.
    # It is not physical evidence for a viewpoint in the current location.
    def physical(value):
        return re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', '', value)
    text=physical(text)
    present=text if current_text is None else physical(current_text)
    current_sentences=re.split(r'(?<=[。！？\n])',present)
    chunks=[*reversed(current_sentences)]
    if allow_background_evidence:chunks.extend([place.get('description',''),text,*[e.get('description','') for e in items if e.get('nearby')]])
    removed=set(removed_fixtures)
    for key,name,pattern,(x,z) in AREAS:
        # Seeing a road through a window does not create an indoor viewpoint
        # or an exit through the wall. Outdoor journeys discover their own path.
        if key=='path' and space_kind(place)=='interior':
            # Distant roads seen through a window are not indoor viewpoints;
            # a real corridor attached to the current room remains walkable.
            pattern=r'走廊|通道|通路'
        for sentence in current_sentences:
            if any(w in sentence for w in PAST):continue
            for clause in re.split(r'[，,；;：:]',sentence):
                for match in re.finditer(pattern,clause):
                    if absent_object_mention(clause,match.group()):removed.add(key)
                    elif current_object_mention(clause,match.group()):removed.discard(key)
        if key in removed:continue
        quote=next((sentence.strip() for chunk in chunks for sentence in re.split(r'(?<=[。！？\n])',chunk) if any(current_object_mention(sentence,match.group()) for match in re.finditer(pattern,sentence))),'')
        if not quote:continue
        nodes.append({'id':stable_id(place['id'],key),'name':name,'direction':'走到'+name,
                      'x':50+x*12,'y':50+z*10,'worldX':x,'worldZ':z,'yaw':atan2(x,-z),'pitch':-0.1,
                      'source':{'quote':quote[:240]},'fixture':key})
    # A pier, boardwalk or bridge has two walkable ends even when the prose
    # only names the structure. Its endpoints must be rendered and clickable;
    # this reveals no new destination and never overrides explicit world routes.
    indoor = space_kind(place)=='interior'
    linear = next((name for name in ('码头','栈道','步道','桥') if name in place['name']),None) if not indoor else None
    if linear and not re.search(r'(?:码头|栈道|步道|桥)(?:已经|已)?(?:坍塌|塌毁|消失|被拆除)',present):
        nodes=[node for node in nodes if node.get('fixture')!='path']
        # Shore-side piers continue along the bank on either side; don't put a
        # walking marker straight out over the water or behind it through a wall.
        ends=[(-3.0,0.0),(3.0,0.0)] if linear=='码头' else [(0.0,-3.0),(0.0,3.0)]
        for (key,name),(x,z) in zip([('path-forward','沿'+linear+'前行'),('path-back','沿'+linear+'返回')],ends):
            if key in removed or 'path' in removed:continue
            nodes.append({'id':stable_id(place['id'],key),'name':name,'direction':name,
                'x':50+x*10,'y':50+z*10,'worldX':x,'worldZ':z,'yaw':atan2(x,-z),'pitch':-0.1,
                'source':{'entityId':place['id'],'quote':place['name']+'的'+linear+'通路'},'fixture':key})
    # This is an illustrated scene layout, not a claim about compass bearings
    # in the source novel. Every destination still needs a public fixture.
    links=[{'from':'entry','to':n['id']} for n in nodes[1:]]
    return {'version':VERSION,'kind':'rendered-scene','nodes':nodes,'links':links,'removedFixtures':sorted(removed),'evidenceTruncated':not allow_background_evidence}

def view_geometry(layout,point_id):
    point=next((p for p in layout['nodes'] if p['id']==point_id),layout['nodes'][0])
    focus=next((p for p in layout['nodes'] if p['id']!=point['id']),point) if point['id']=='entry' else point
    x,z=point['worldX'],point['worldZ']
    if point['id']!='entry':
        distance=hypot(x,z)
        if distance:x-=x/distance*0.65;z-=z/distance*0.65
    look=atan2(focus['worldX']-x,-(focus['worldZ']-z))
    return point,{'x':x,'z':z,'label':point['name'],
                  'initialYaw':look,'lookAt':focus['name']}

def hotspot_positions(layout,point,characters,items):
    result={}
    for index,entity in enumerate(characters+items):
        h=int(sha256(entity['id'].encode()).hexdigest()[:8],16)
        context=entity.get('description','')+entity['name']
        area=next((p for p in layout['nodes'][1:] if p.get('fixture')=='bench' and any(w in context for w in ['凳','铜牌','铜路牌']) or p.get('fixture')=='hearth' and any(w in context for w in ['壶','炉','炭火']) or p.get('fixture')=='counter' and any(w in context for w in ['柜台','拍卖','鉴定','炼药','掌柜'])),None)
        if entity['type']=='character':
            # A full-body sprite is anchored by its shoes, 1.6m below the
            # observer's eyes. Distance controls both pitch and apparent size.
            # Stand in the open foreground BEFORE furniture, rather than at a
            # fixture's centre (which puts feet on counters). On a pier use its
            # actual walkway bearing, never an arbitrary direction over water.
            paths=[p for p in layout['nodes'][1:] if p.get('fixture','').startswith('path-')]
            ground=paths[h%len(paths)] if paths else area or next((p for p in layout['nodes'][1:] if p.get('fixture')=='entrance'),None)
            angle=atan2(ground['worldX']-point['worldX'],-(ground['worldZ']-point['worldZ'])) if ground else -.35+(h%3)*.35
            if area and not paths:angle+=.38
            distance=2.2+(h%3)*.15
            x,z=point['worldX']+sin(angle)*distance,point['worldZ']-cos(angle)*distance
            if paths and '码头' in layout['nodes'][0]['name']:z+=.45
            dx,dz=x-point['worldX'],z-point['worldZ'];distance=max(1.8,hypot(dx,dz))
            pitch=atan2(-1.6,distance)
            # Illustrated interiors often frame counters higher than a physical
            # eye-level panorama. Keep full-body feet on the visible foreground
            # floor, below the counter front, rather than on its painted surface.
            if area and area.get('fixture')=='counter' and not paths:pitch=min(pitch,-.82)
            result[entity['id']]={'yaw':atan2(dx,-dz),'pitch':pitch,'distance':distance,'height':1.72,'worldX':x,'worldZ':z}
            continue
        angle=atan2(area['worldX']-point['worldX'],-(area['worldZ']-point['worldZ'])) if area else (h%6283)/1000-pi
        result[entity['id']]={'yaw':angle+(h%11-5)*0.025,'pitch':-0.3}
    return result
