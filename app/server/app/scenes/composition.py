"""Panorama actors are part of the received picture, never pasted cutouts.

Bindings belong to a single image and an entity ID. Legacy images without a
verified binding keep their characters in the people list rather than inventing
a position on a plant or a wall. This metadata does not change the story ledger.
"""
from math import atan2, isfinite, pi, sin, cos
import re
from .mentions import PAST


def character_visibility(entity, text, default=None):
    name = re.escape(entity['name'])
    # Dialogue about disappearing is not evidence that an actor disappeared.
    narrative = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', '', text)
    hidden = name + r'(?:的)?(?:投影|身影|身形|魂体)[^。！？\n]{0,35}(?:散去|散尽|消散|隐去|隐没|收回|缩回|收起)'
    shown = name + r'[^。！？\n]{0,24}(?:显出|浮现|现出|凝出|重新出现)[^。！？\n]{0,12}(?:投影|身影|身形|魂体)'
    shown += '|'+name+r'(?:的)?(?:投影|身影|魂体)[^。！？\n]{0,12}(?:浮在|站在|立在|立于|悬在|出现|显了出来|负手而立|袖手而立)'
    shown += '|'+name+r'[^。！？\n]{0,24}(?:投影|魂体|虚影|身形)[^。！？\n]{0,12}(?<!没有)(?<!并未)(?<!未曾)显形'
    shown += '|'+name+r'(?:的)?(?:虚影|投影|魂体)[^。！？\n]{0,10}(?:在|立|站|浮|停|凝实)'
    attached_spirit=bool(re.search(r'纳戒|戒指|戒中',entity.get('description','')) and re.search(r'灵魂|魂体|投影|残魂',entity.get('description','')))
    mode = default if default in ('voice','visible') else 'voice' if attached_spirit else 'visible'
    hidden += '|'+name+r'[^。！？\n]{0,24}(?:没有|并未|未曾)(?:显出|现出|凝出)[^。！？\n]{0,12}(?:投影|身影|魂体)'
    hidden += '|'+name+r'[^。！？\n]{0,20}(?:退回|回到|隐入|收回|缩回)[^。！？\n]{0,12}(?:纳戒|戒中|戒指)|'+name+r'[^。！？\n]{0,20}(?:仅|只)(?:在[^。！？\n]{0,10})?(?:传音|声音)'
    for sentence in re.split(r'[。！？\n]', narrative):
        if any(word in sentence for word in (*PAST,'想起','那天','如果','假如')):continue
        for match in re.finditer('(?P<hidden>' + hidden + ')|(?P<shown>' + shown + ')', sentence):
            mode = 'voice' if match.group('hidden') else 'visible'
    return mode


def inherited_visibility(checkpoint, characters):
    from sqlalchemy.orm import object_session
    from ..world.models import WorldCheckpoint
    from ..world.state import public_payload
    db=object_session(checkpoint);history=[];current=checkpoint
    for _ in range(128):
        history.append(current)
        parent=db.get(WorldCheckpoint,current.parent_checkpoint_id) if db and current.parent_checkpoint_id else None
        if not parent or parent.owner_id!=checkpoint.owner_id or parent.world_id!=checkpoint.world_id or parent.state['location']!=checkpoint.state['location'] or parent.state['playerId']!=checkpoint.state['playerId']:break
        current=parent
    modes={}
    for scene in reversed(history):
        for entity in characters:
            reasons='\n'.join(entity['name']+c.get('reason','') for c in public_payload(scene.changes,scene.state,scene.definition) if c.get('entityId')==entity['id'])
            text=public_payload(scene.text,scene.state,scene.definition)+'\n'+reasons
            modes[entity['id']]=character_visibility(entity,text,modes.get(entity['id']))
            explicit = scene.state.get('entities', {}).get(entity['id'], {}).get('fields', {}).get('presentation')
            if explicit in ('voice', 'visible'):modes[entity['id']] = explicit
    return modes


def named_pose(entity,text,default=None,other_names=()):
    """Only explicit physical statements change a stable scene pose.

    Speech, recollections, negations and hypothetical directions cannot become
    a newly painted body. Ambiguous pronouns deliberately keep the prior pose.
    """
    narrative=re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', '',text)
    patterns={'standing':r'站着|站在|站立|站起(?:身|来)?|起身|立在|立于|stands?\b|standing\b|stood\b|gets? up\b',
              'seated':r'坐着|坐在|坐下|坐回|落座|端坐|sits?\b|sitting\b|seated\b|sat\b',
              'kneeling':r'跪着|跪在|跪下|跪坐|kneels?\b|kneeling\b|knelt\b'}
    result=default
    for sentence in re.split(r'[。！？\n.!?]',narrative):
        if any(word in sentence for word in (*PAST,'想起','那天','如果','假如','明天','将会','打算')) or re.search(r'\b(?:yesterday|tomorrow|would|if|remembered)\b',sentence,re.I):continue
        for clause in re.split(r'[，,；;：:]',sentence):
            match=re.search(re.escape(entity['name'])+r"(?!的|'s\b|’s\b)",clause,re.I)
            if not match:continue
            after=clause[match.end():];findings=[]
            for pose,pattern in patterns.items():
                for found in re.finditer(pattern,after,re.I):
                    before=after[:found.start()]
                    if len(before)>14 or any(name and name!=entity['name'] and name in before for name in other_names):continue
                    # An observer watching another subject sit is still an
                    # observer. Never transfer that subject's pose to the name
                    # at the beginning of the sentence (including the player).
                    if re.search(r'你|我|他|她|它|您|看着|等(?:待)?|目送|注视|望着|\b(?:you|I|he|she|they|him|her|watch(?:es|ed|ing)?|wait(?:s|ed|ing)?)\b',before,re.I):continue
                    # Accept a direct completed/current subject phrase, rather
                    # than maintaining an open-ended list of intention words.
                    # An ambiguous bridge such as "plans to" or "possibly"
                    # cannot turn an observer's body into a new pose.
                    chinese=r'(?:已经|已|仍然|仍|正|正在|继续|又|重新|缓缓地?|慢慢地?|静静地?|一直|稳稳地?|安静地|轻轻地?|突然|便|就|才|随即|这时|此刻|如今|现在|\s|(?:在|于|从|靠在|靠着|沿着)[\u4e00-\u9fff]{1,10}(?:上|旁|边|前|下|里|内|处|中))*'
                    english=r'\s*(?:(?:is|was|has|had|been|now|still|already|quietly|slowly|suddenly)\s+)*'
                    if not (re.fullmatch(chinese,before) or re.fullmatch(english,before,re.I)):continue
                    if re.search(r'(?:不|没|未|别|要|想|欲|将|考虑|准备|打算|应该|可以|让|请|叫|劝|建议|嘱咐|要求|does not |did not |not |will |should |could |tells? |asks? |wants? |intends? |prepares? |about to )[^，,。]{0,10}$',before,re.I):continue
                    findings.append((found.start(),pose))
            if findings:result=max(findings)[1]
    return result


def inherited_poses(checkpoint,characters):
    """Public pose evidence persists across conversation and return visits.

    Read only this observer's owned ancestry; never expose hidden entity fields
    or borrow another save's body pose. A bounded missing history stays unknown.
    """
    from sqlalchemy.orm import object_session
    from ..world.models import WorldCheckpoint
    from ..world.state import public_payload,public_view
    db=object_session(checkpoint);history=[];current=checkpoint;seen=set()
    for _ in range(512):
        if not current or current.id in seen or current.owner_id!=checkpoint.owner_id or current.world_id!=checkpoint.world_id or current.state.get('playerId')!=checkpoint.state.get('playerId'):break
        seen.add(current.id)
        if current.state.get('location')==checkpoint.state.get('location'):history.append(current)
        current=db.get(WorldCheckpoint,current.parent_checkpoint_id) if db and current.parent_checkpoint_id else None
    poses={}
    for scene in reversed(history):
        public=public_view(scene.state,scene.definition)
        names=[entity['name'] for entity in public['entities'] if entity['type']=='character']
        observed={entity['id']:entity for entity in public['entities'] if entity.get('nearby')}
        text=public_payload(scene.text,scene.state,scene.definition)
        for entity in characters:
            if entity['id'] not in observed:continue
            pose=named_pose(entity,text,poses.get(entity['id']),names)
            fields=observed[entity['id']].get('state',{}).get('fields',{})
            explicit=next((fields[key] for key in ('posture','pose','姿势','姿态') if fields.get(key) in ('standing','seated','kneeling')),None)
            if explicit:pose=explicit
            if pose:poses[entity['id']]=pose
    return poses


def scene_cast(view, world_name=''):
    """Public cast rendered together with the environment on future requests."""
    result = []
    for entity in view['characters'][:8]:
        if view.get('characterVisibility', {}).get(entity['id']) != 'visible':continue
        spot = view['hotspots'].get(entity['id'])
        if not spot:continue
        yaw=spot['yaw']
        # Several actors can have the same semantic fixture (the counter). Give
        # the renderer distinct ground positions rather than one shared point.
        for offset in [0, .6, -.6, 1.2, -1.2, 1.8, -1.8, 2.4]:
            candidate=yaw+offset
            if all(abs((candidate-other['yaw']+pi)%(2*pi)-pi)>=.48 for other in result):
                yaw=candidate;break
        distance=max(4.5,spot.get('distance',3.8));pose=view['descriptor'].get('viewpoint',{})
        from .characters import character_descriptor
        art=character_descriptor(entity,world_name)
        result.append({'entityId': entity['id'], 'name': entity['name'], 'artVersion':4,
            **({'expectedPose':view['descriptor']['characterPoses'][entity['id']]}
               if entity['id'] in view['descriptor'].get('characterPoses',{}) else {}),
            'description': entity.get('description', '')[:800],
            'appearance':art.get('appearance','Preserve the public age, gender and occupation. Period clothing appropriate to this world; same painted style as the environment.'),
            'presentation':art.get('presentation','solid'),
            'yaw': yaw, 'pitch': 0, 'distance':distance,'heightMetres':1.75,'maximumAngularHeightDegrees':24,
            'worldX': pose.get('x',0)+sin(yaw)*distance, 'worldZ': pose.get('z',0)-cos(yaw)*distance})
    return result


def character_presentations(row, view):
    # Saved verified coordinates override generation guidance. Neither source
    # authorizes a missing/absent entity, or a hidden spirit's projection.
    from .grounding import binding_matches
    anchors = row.image.get('characterAnchors', {}) if binding_matches(row) else {}
    result = {}
    for entity in view['characters']:
        ident = entity['id']
        if view.get('characterVisibility', {}).get(ident) == 'voice':
            result[ident] = {'mode': 'voice'}
            continue
        anchor = anchors.get(ident, {})
        head_version=getattr(row,'descriptor',{}).get('headInspectionVersion')
        if head_version in (1,2):
            from .heads import proof_valid
            if not proof_valid(anchor.get('headReview'),row.image.get('sha256'),version=head_version):
                result[ident]={'mode':'offscreen'}
                continue
        yaw, pitch = anchor.get('yaw'), anchor.get('pitch')
        if (anchor.get('verified') is True and anchor.get('source') == 'image-inspection'
                and all(type(v) in (int, float) and isfinite(v) for v in (yaw, pitch)) and -pi/2 <= pitch <= pi/2):
            # Keep the name above the painted head, not over the mouth. Older
            # measured receipts retain their pixels; no new inspection needed.
            box=anchor.get('unwrappedHeadBox') or anchor.get('headBox')
            valid_box=(isinstance(box,list) and len(box)==4
                and all(type(v) in (int,float) and isfinite(v) for v in box)
                and 0<=box[1]<box[3]<=1 and 0<box[2]-box[0]<=.12
                and box[3]-box[1]<=.14 and -1<=box[0]<box[2]<=2)
            head_pitch=anchor.get('headPitch')
            if type(head_pitch) in (int,float) and isfinite(head_pitch) and -pi/2<=head_pitch<=pi/2:
                pitch=head_pitch
            if valid_box:
                pitch=(.5-box[1])*pi
            result[ident] = {'mode': 'embedded', 'yaw': yaw, 'pitch': pitch}
            if valid_box:
                # Labels sit beside the measured head, leaving its face clear.
                # Do not stack a fixed-height badge above a loose detector box:
                # that multiplies a small camera error into a floating label.
                result[ident].update(labelYaw=(box[2]%1-.5)*2*pi,
                                     labelLeftYaw=(box[0]%1-.5)*2*pi,
                                     labelPitch=(.5-(box[1]+box[3])/2)*pi)
        else:
            result[ident] = {'mode': 'offscreen'}
    return result
