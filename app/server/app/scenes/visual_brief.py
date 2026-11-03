"""Public physical scene inputs; narrative people never become background cast."""
import re


def environment_text(descriptor):
    text = descriptor.get('description', '')
    names = {descriptor.get('observerName', '')}
    for field in ('sceneCast', 'futureCast'):
        names.update(p.get('name', '') for p in descriptor.get(field, []))
    names.discard('')
    clauses = re.split(r'(?<=[，,。；;！？\n])', text)
    anonymous = r'[一二两三四五六七八九十\d]+(?:名|位|个)?(?:陌生人|旅人|人)|众人|人群|行人|路人|旅人|旅客|旅行者|同行者|\b(?:people|persons?|travell?ers?|pedestrians?|bystanders?|crowds?)\b'
    return ''.join(part for part in clauses if not any(name in part for name in names)
                   and not re.search(anonymous, part, re.I))[:1600]


def environment_constraints(descriptor):
    retained=len(descriptor.get('sceneCast',[]))
    people=(f'Preserve EXACTLY {retained} existing physical people from the actual reference; add no other person. '
            if retained else 'The picture contains EXACTLY ZERO PEOPLE: exclude the player, companions, distant strangers, '
                'human reflections, shadows, portraits and silhouettes. ')
    if descriptor.get('spaceKind') == 'interior':
        return ('The OBSERVER is INSIDE this enclosed room, beneath its complete ceiling/roof. '
                'Surround the camera with this room, its walls, interior furniture and continuous indoor floor. '
                'Exterior scenery, if specified in THIS location, belongs ONLY beyond its windows or actual doors. '
                'Use only this location\'s stated weather, geography and time of day. '
                'Never move the camera outdoors, into a courtyard or an unenclosed veranda to show that scenery. '
                +people+'Names or people in historical context '
                'are not additional visual subjects. Paint architecture, materials, furnishings and light only.')
    return ('The observer is in the specified outdoor place, with its open sky or outdoor canopy. '
            'Do not replace the outdoor location with an indoor room. '+people)


def light_constraints(descriptor):
    light=descriptor.get('lighting','')
    if light in ('夜','深夜'):
        return 'It is NIGHT. Interior lamps/fire provide local light; visible exterior sky and scenery are DARK. No daylight or sunlight.'
    if light in ('傍晚','黄昏'):
        return 'It is DUSK with fading exterior light and local interior lamps, matching this location\'s stated weather.'
    return 'Match the stated time and weather of THIS location; do not import another scene\'s exterior scenery.'


def staging_constraints(descriptor):
    cast=descriptor.get('futureCast',[])
    if not cast:return ''
    seated=sum(p.get('expectedPose')=='seated' for p in cast)
    return (f'Keep {len(cast)} separate EMPTY staging spaces on the visible floor, clear of outgoing passages. '
            'Show open floor at medium distance in different viewing directions; avoid large foreground furniture '
            'occluding all floor near the camera. Keep the walking route continuous. '
            'The camera stands in a clear central area; place bulky furniture and loose props along the perimeter. '
            'For multiple future people, give each a separate clear floor bay in a DIFFERENT horizontal viewing '
            'direction, with room for a complete adult from head to both shoes at medium distance. '
            'Do not compress every free area into the same doorway aisle or hide the floor below a foreground counter. '
            'Do not put a coat rack, oversized hat or large object directly in front of the camera. '
            +(f'Provide {seated} clearly visible empty seats with exposed supporting surfaces. ' if seated else '')
            +('A seat carrying props is not an empty seat: keep these required seats and shoe floor unobstructed. ' if seated else
              'No seated pose is required by this brief: prioritize the distinct standing floor bays rather than filling '
              'them with extra chairs, tables or props. ')
            +'These are empty areas/furniture ONLY: do not draw any person, human guide or placeholder figure.')


def ground_feedback(image,missing_exits=None):
    """Bounded painting hints from this image's received failed floor checks."""
    from math import isfinite
    from .inspection import sphere_pixel
    if missing_exits is not None and not isinstance(missing_exits,list):return []
    digest=image.get('sha256')
    if not isinstance(digest,str) or len(digest)!=64 or image.get('bindingImageHash')!=digest:return []
    notes=image.get('pointCorrectionNotes')
    if not isinstance(notes,list):return []
    result=[]
    for note in notes[:16]:
        if not isinstance(note,dict) or missing_exits is not None and note.get('id') not in missing_exits:continue
        view=note.get('viewIndex');observed=note.get('actualPointObservations')
        if type(view) is not int or not 0<=view<8 or not isinstance(observed,dict):continue
        points=[]
        for label,key in [('A','nearPixels'),('B','farPixels'),('C','throughPointPixels')]:
            point=note.get(key);value=observed.get(label)
            if (not isinstance(point,list) or len(point)!=2 or any(type(n) not in (int,float)
                    or not isfinite(n) or not 0<=n<=512 for n in point) or not isinstance(value,dict)):continue
            surface=value.get('surface');evidence=value.get('evidence');confidence=value.get('confidence')
            if (surface not in ('furniture','wall','closed-door','unknown') or not isinstance(evidence,str)
                    or len(evidence.strip())<10 or type(confidence) not in (int,float)
                    or not isfinite(confidence) or not 0<=confidence<=1):continue
            x,y=sphere_pixel(view,*point,2048,1024)
            points.append({'label':label,'surface':surface,'evidence':evidence[:160],
                'panoramaPosition':[round((x/2048)%1,4),round(y/1024,4)],
                'reportedCovering':surface=='furniture' and bool(re.search(r'\b(?:doormat|mat|rug|cushion|runner)\b|门垫|地毯|软垫|靠垫',evidence,re.I))})
        if points:
            result.append({'routeId':str(note.get('id',''))[:64],'sourceHash':digest,'points':sorted(points,key=lambda p:not p['reportedCovering'])[:2]})
        if len(result)>=2:break
    return result


def obstructed_threshold(image):
    """A clear reported floor covering needs art repair, not repeated opinions."""
    from math import isfinite
    def confident(value):return type(value) in (int,float) and isfinite(value) and .85<=value<=1
    notes=image.get('pointCorrectionNotes',[])
    missing=image.get('missingExitIds')
    if not isinstance(notes,list) or not isinstance(missing,list) or not missing:return False
    for note in notes[:16]:
        if not isinstance(note,dict) or note.get('id') not in missing:continue
        value=note.get('actualPointObservations',{})
        if (not isinstance(value,dict) or value.get('unobstructed') is not False
                or not confident(value.get('confidence')) or any(value.get(k) is not True
                    for k in ('sameOpening','openingConfirmed','crossedThreshold'))):continue
        hints=ground_feedback({**image,'pointCorrectionNotes':[note]})
        if any(point['reportedCovering'] and confident(value[point['label']].get('confidence'))
                for hint in hints for point in hint['points']):return True
    return False
