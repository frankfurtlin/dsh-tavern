"""Resolve conflicting multi-view body measurements in one actual camera.

The already verified head identifies the person; it does not prove a body or
contact point. These exchanges remain bounded, journaled and independently
subject to the existing physical geometry checks.
"""
import base64
from copy import deepcopy
from .heads import measured_camera, person_context, proof_valid, parse


FIELDS = ('physicalPerson', 'pose', 'naturalScale', 'scaleConfidence',
          'scaleEvidence', 'bodyBoxPixels', 'groundPointPixels', 'grounding',
          'groundConfidence', 'supportingFixture', 'supportingFixtureId')


def inspect_bodies(parsed, cast, cameras, image_hash, image_size, call, limit, *, verified_fixtures=(), on_progress=None, parallel=False):
    if parallel and len(cast)>1:
        from .parallel import person_reads
        return person_reads(lambda value,people,progress:inspect_bodies(value,people,cameras,image_hash,image_size,call,limit,
            verified_fixtures=verified_fixtures,on_progress=progress),parsed,cast,on_progress=on_progress)
    from .binding import anchors_from_result
    from .inspection import panorama_coordinates
    value = deepcopy(parsed)
    people = {person['entityId']: person for person in cast}
    for ordinal, part in enumerate(value.get('characters', [])):
        if not isinstance(part, dict) or part.get('entityId') not in people: continue
        proof = part.get('headReview')
        if not proof_valid(proof, image_hash): continue
        expected_pose=people[part['entityId']].get('expectedPose')
        def pose_matches(candidate):return not expected_pose or candidate.get('pose')==expected_pose
        # Good measurements need no extra supplier request.
        projected = panorama_coordinates({'characters': [part]}, image_size)
        if pose_matches(part) and anchors_from_result(projected, cast, image_size, require_grounding=True, verified_fixtures=verified_fixtures): continue
        head_view = proof['viewIndex']
        for attempt, index in enumerate((head_view, head_view + 4)):
            if index >= len(cameras): continue
            data = {'inspectionMode': 'person-body', 'person': person_context(people[part['entityId']]),
                    'camera': {'image': 1, 'viewIndex': index, 'width': 512, 'height': 512},
                    'verifiedHead': {'viewIndex': head_view, 'headBoxPixels': proof['headBoxPixels']},
                    'measurement': 'Inspect ONLY this actual camera. The head coordinates identify a verified person in the horizontal view; when this is the downward view those coordinates do NOT apply here. Measure the actual visible body, supporting surface and shoe/floor or body/furniture contact in THIS supplied image. Do not infer shoes below a crop or through a counter. If shoes are clipped, floating or erased, do not claim floor contact. A seated pose does not prove fixture support. Use the exact output enums. Return physicalPerson:false if no matching physical body is present.',
                    '_sceneImages': ['data:image/jpeg;base64,' + base64.b64encode(measured_camera(cameras[index])).decode()],
                    '_structured': True, '_visionStream': True}
            observed = parse(call(f'analysis:body:{ordinal}:{attempt}', 'world_scene_bodies', data, limit))
            candidate = deepcopy(part)
            for key in FIELDS: candidate.pop(key, None)
            candidate.update({key: observed[key] for key in FIELDS if key in observed})
            candidate.update(bodyViewIndex=index, groundViewIndex=index)
            candidate['bodyReview'] = {'imageHash': image_hash, 'viewIndex': index, 'version': 1}
            # Never manufacture a fixture/view or move a measured point.
            for key in ('scaleEvidence', 'supportingFixture'):
                if isinstance(candidate.get(key), dict): candidate[key]['viewIndex'] = index
            part.clear(); part.update(candidate)
            if on_progress: on_progress(deepcopy(value))
            projected = panorama_coordinates({'characters': [part]}, image_size)
            if pose_matches(part) and anchors_from_result(projected, cast, image_size, require_grounding=True, verified_fixtures=verified_fixtures): break
            if observed.get('physicalPerson') is False or observed.get('naturalScale') is False or observed.get('grounding') == 'floating': break
    return value
