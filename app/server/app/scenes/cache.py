"""Reuse a place's received picture across turns, visits and changed prose."""
from copy import deepcopy
from sqlalchemy.orm import object_session
from ..world.models import WorldCheckpoint


def appearance(view):
    descriptor = view['descriptor']
    return {
        'version': 3, 'playerId': view['playerId'],
        **({'visualFacts':deepcopy(descriptor['visualFacts'])} if descriptor.get('visualFacts') else {}),
        'placeId': descriptor['placeId'], 'pointId': descriptor['pointId'],
        'description': descriptor.get('description', ''),
        'environment': deepcopy(descriptor.get('environment', {})),
        'lighting': descriptor.get('lighting', '白天'),
        'spaceKind': descriptor.get('spaceKind', 'interior'),
        'routes': sorted(({'id': p['id'], 'portalId': p.get('portalId')}
                          for p in view.get('exits', [])), key=lambda p: p['id']),
        **({'routeVisualCues':{e['id']:e['visualCue'] for e in descriptor.get('exitRoutes',[]) if e.get('visualCue')}}
           if any(e.get('visualCue') for e in descriptor.get('exitRoutes',[])) else {}),
        'fixtures': fixture_signature(view['layout']),
        'removedFixtures': sorted(view['layout'].get('removedFixtures', [])),
        **({'characterLooks':deepcopy(descriptor['characterLooks'])} if descriptor.get('characterLooks') else {}),
        'cast': sorted(({'id': person['id'], 'description': person.get('description', '')[:800], 'artVersion':4,
                        **({'pose':descriptor['characterPoses'][person['id']]} if person['id'] in descriptor.get('characterPoses',{}) else {})}
                        for person in view['characters']
                        if view.get('characterVisibility', {}).get(person['id']) == 'visible'), key=lambda p: p['id']),
    }


def fixture_signature(layout):
    return sorted(({'id': n['id'], 'fixture': n.get('fixture'),
                    'x': n.get('worldX'), 'z': n.get('worldZ')}
                   for n in layout.get('nodes', []) if n['id'] != 'entry'), key=lambda n: n['id'])


def saved_appearance(row):
    if row.descriptor.get('appearance'):
        value = deepcopy(row.descriptor['appearance'])
        if value.get('version') == 2:
            # Legacy receipts can still be reused, but only for their original
            # light and connections. Never infer these from the NEW visit.
            value['version'] = 3
            value['lighting'] = row.descriptor.get('lighting', '白天')
            value['spaceKind'] = row.descriptor.get('spaceKind', 'interior')
            # A later graph can acquire return paths. Only the receipt proves
            # what the old image was instructed to render.
            value['routes'] = sorted(({'id': e['id'], 'portalId': e.get('portalId')}
                                      for e in row.descriptor.get('exitRoutes', []) if e.get('id')), key=lambda e: e['id'])
            value['fixtures'] = fixture_signature(row.descriptor.get('renderLayout', {}))
        return value
    # Old receipts remain reusable without rewriting their pictures or hashes.
    db = object_session(row)
    checkpoint = db.get(WorldCheckpoint, row.checkpoint_id) if db else None
    if not checkpoint or checkpoint.owner_id != row.owner_id or checkpoint.world_id != row.world_id:
        return None
    from .service import scene_view
    value = appearance(scene_view(checkpoint, row.line_id))
    value.update({key: deepcopy(row.descriptor.get(key, value[key]))
                  for key in ('placeId', 'pointId', 'description', 'environment')})
    value['removedFixtures'] = sorted(row.descriptor.get('renderLayout', {}).get('removedFixtures', []))
    value['lighting'] = row.descriptor.get('lighting', '白天')
    value['spaceKind'] = row.descriptor.get('spaceKind', 'interior')
    value['fixtures'] = fixture_signature(row.descriptor.get('renderLayout', {}))
    value['routes'] = sorted(({'id':e['id'],'portalId':e.get('portalId')}
                              for e in row.descriptor.get('exitRoutes', []) if e.get('id')), key=lambda e:e['id'])
    # Never infer a new render landmark from today's route planner when this
    # old receipt did not instruct the supplier to paint it.
    value.pop('routeVisualCues',None)
    value.pop('visualFacts',None)
    if row.descriptor.get('visualFacts'):value['visualFacts']=deepcopy(row.descriptor['visualFacts'])
    cues={e['id']:e['visualCue'] for e in row.descriptor.get('exitRoutes',[]) if e.get('visualCue')}
    if cues:value['routeVisualCues']=cues
    painted={p['entityId']:p.get('description','')[:800] for p in row.descriptor.get('sceneCast',[])}
    known={p['id']:p['description'] for p in value['cast']}
    for entity_id in row.image.get('characterAnchors',{}):
        if entity_id in known:painted.setdefault(entity_id,known[entity_id])
    # A person in the old story is not proof that the old background depicts
    # them. Only a received composition or a verified image anchor is evidence.
    revisions={p['entityId']:p.get('artVersion',2) for p in row.descriptor.get('sceneCast',[])}
    value['cast']=sorted(({'id':key,'description':description,'artVersion':revisions.get(key,2)} for key,description in painted.items()),key=lambda p:p['id'])
    return value


def compatible(row, view):
    if (row.descriptor.get('publicAppearanceContextVersion')==1
            and row.descriptor.get('publicAppearanceContext',{})!=view['descriptor'].get('publicAppearanceContext',{})):
        return False
    saved=saved_appearance(row);desired=appearance(view)
    if isinstance(saved,dict):
        wanted={person['id']:person for person in desired['cast']}
        for person in saved.get('cast',[]):
            expected=wanted.get(person['id'],{}).get('pose')
            if expected and not person.get('pose'):
                # Legacy artwork can keep its own received bytes when its
                # measured body already matches. Today's text cannot supply
                # a missing posture for yesterday's unverified picture.
                actual=verified_pose(row,person['id'])
                if actual:person['pose']=actual
    return saved == desired


def verified_pose(row,entity_id):
    if getattr(row,'status',None)!='ready':return None
    from .grounding import binding_matches
    if not binding_matches(row):return None
    anchor=row.image.get('characterAnchors',{}).get(entity_id,{})
    evidence=anchor.get('bodyEvidence',{})
    if (anchor.get('verified') is True and anchor.get('source')=='image-inspection'
            and evidence.get('naturalScale') is True and evidence.get('pose') in ('standing','seated','kneeling')):
        return evidence['pose']
    return None


def public_scene(row):
    return (not row.descriptor.get('pipelineParentId')
            and row.descriptor.get('kind') not in ('scene-environment','scene-actor-edit','scene-environment-edit','reference-probe','character'))


def known_failed_scene_upgrade(db,row,current_version):
    """One new revision may repair confirmed failure, never an unresolved POST.

    Completed ready images remain reusable across revisions. Advancing a failed
    old public reservation creates a NEW receipt, preserving the old failure;
    a failed current revision does not authorize an endless refresh loop.
    """
    if (not public_scene(row) or row.status!='failed'
            or row.descriptor.get('environmentGeometryVersion',0)>=current_version):return False
    failed=row
    if row.descriptor.get('renderPipeline')==1:
        from .pipeline import owned_stage
        failed=owned_stage(db,row,row.image.get('pipelineStageId'))
        if not failed:return False
    if row.error_code=='SCENE_CONNECTION_FAILED':
        diagnostic=failed.image.get('failureDiagnostic',{})
        return (failed.status=='failed' and failed.error_code=='SCENE_CONNECTION_FAILED'
                and diagnostic.get('requestSent') is False and diagnostic.get('responseReceived') is False)
    if row.error_code!='SCENE_VISUAL_UNCONFIRMED' or failed.status!='ready':return False
    from ..models import Task
    task=db.get(Task,failed.image.get('bindingTaskId')) if failed.image.get('bindingTaskId') else None
    return bool(task and task.owner_id==row.owner_id and task.state=='succeeded'
                and task.provider_state=='completed' and task.payload.get('sceneId')==failed.id
                and task.payload.get('worldId')==row.world_id
                and failed.image.get('bindingImageHash')==failed.image.get('sha256')
                and failed.image.get('sha256'))


def usable_reference(row):
    # Version 1 mixed outdoor places with a generic room prompt. An edit of
    # those pictures would preserve the incorrect room even after this fix.
    return (public_scene(row)
            and row.descriptor.get('appearance', {}).get('version', 2) >= 2
            and not row.image.get('scaleFailureIds')
            and not row.image.get('missingCharacterIds')
            and not row.image.get('composition',{}).get('issues'))
