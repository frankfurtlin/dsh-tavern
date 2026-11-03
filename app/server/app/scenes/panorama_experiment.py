"""Operator-only panorama experiments; no API or default configuration reads this.

Keep the current transport and model fixed while testing an independently drawn
projection example. A 2:1 file is never treated as proof of spherical geometry.
"""
from copy import deepcopy
from hashlib import sha256
from math import cos, pi, sin, sqrt
import base64
import io

from PIL import Image, ImageStat, ImageChops


STRATEGIES = ('baseline', 'geometry', 'geometry-cast')
GUIDE_VERSION = 'story-projection-example-v1'


def projection_example(width=1024):
    """Original ray-cast floor grid and cylinders, not competitor artwork."""
    if type(width) is not int or width < 64 or width > 2048 or width % 2:
        raise ValueError('An even bounded panorama width is required')
    image = Image.new('RGB', (width, width // 2))
    pixels = image.load()
    columns = [(12*sin(a), -12*cos(a), colour) for a, colour in
               ((.5, (160, 133, 112)), (2.0, (116, 147, 153)),
                (3.7, (151, 154, 121)), (5.2, (144, 126, 151)))]
    directions = [(sin((x/width-.5)*2*pi), -cos((x/width-.5)*2*pi))
                  for x in range(width)]
    for y in range(image.height):
        latitude = (.5-y/(image.height-1))*pi
        dy, horizontal = sin(latitude), cos(latitude)
        for x, (sx, sz) in enumerate(directions):
            dx, dz = horizontal*sx, horizontal*sz
            distance = 1.6/-dy if dy < -1e-7 else float('inf')
            if dy < -1e-7:
                gx, gz = dx*distance, dz*distance
                line = min(abs(gx-round(gx)), abs(gz-round(gz))) < .025
                colour = (156, 154, 145) if line else (213, 208, 192)
            else:
                colour = (205, 218, 223)
            a = dx*dx + dz*dz
            if a > 1e-10:
                for cx, cz, paint in columns:
                    b = -2*(dx*cx+dz*cz)
                    discriminant = b*b-4*a*(cx*cx+cz*cz-.6*.6)
                    if discriminant < 0:
                        continue
                    hit = (-b-sqrt(discriminant))/(2*a)
                    if 0 < hit < distance and 0 <= 1.6+dy*hit <= 3.2:
                        colour, distance = paint, hit
            pixels[x, y] = colour
    # Both edges are the same meridian. Preserve exact periodicity in the guide.
    image.paste(image.crop((0, 0, 1, image.height)), (width-1, 0))
    return image


def request_variant(request, descriptor, strategy):
    if strategy not in STRATEGIES:
        raise ValueError('Unknown isolated panorama strategy')
    if descriptor.get('kind') == 'character' or descriptor.get('renderStage') != 'environment':
        raise ValueError('Experiments accept an environment stage only')
    value = deepcopy(request)
    if strategy == 'baseline':
        return value
    if value.get('extra_body', {}).get('image'):
        raise ValueError('Do not silently replace actual scene or identity references')
    if descriptor.get('sceneCast'):
        raise ValueError('Baseline environment must be empty for the controlled comparison')
    guide = projection_example().resize((3136, 1344), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    guide.save(output, format='PNG')
    value.pop('return_base64', None)
    value['extra_body'] = {'image': ['data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()],
                           'response_format': 'b64_json'}
    instruction = (
        'Image 1 is ONLY a generic longitude-latitude projection example, NOT this location. '
        'Its floor grid and columns show spherical distortion and horizon height only. '
        'Replace ALL guide pixels with original finished artwork of the requested setting. '
        'Do not copy its grid, columns, colours, blank surfaces or landmarks. '
        'The 21:9 transport canvas represents normalized full 360x180 longitude-latitude coordinates; '
        'all rows including both poles will be normalized to a 2:1 sphere texture. '
        'Keep the eye-level horizon at mid-height. Put the wrap seam on low-detail continuous '
        'surfaces, away from faces, exits and major objects. No player body, hands, feet, '
        'shadow or reflection. No fisheye disk, panels, writing or diagram lines.\n')
    if strategy == 'geometry-cast':
        cast = descriptor.get('experimentCast', [])
        if not cast or len(cast) > 4:
            raise ValueError('Direct-cast experiment requires one to four public characters')
        # Build a separate instruction; do not accidentally keep the empty-scene
        # prohibition from the environment baseline in a direct-cast request.
        from .visual_style import style_prompt
        from ..domain import canonical
        instruction += (
            'Create one complete illustrated spherical scene in a SINGLE generation. '
            + 'Location: '+descriptor['placeName']+'. '+descriptor['description']+'. '
            + 'Light: '+descriptor['lighting']+'. '+style_prompt(descriptor)+'. '
            + 'The camera is the unseen standing player at eye height 1.6 metres. '
            + 'Draw EXACTLY these public physically present NPCs, once each: '
            + canonical([{k: p.get(k) for k in ('entityId', 'name', 'appearance', 'expectedPose')}
                         for p in cast])+'. '
            + 'People belong naturally in this scene, at believable distance and scale, '
            + 'not portraits or framed pictures. Match their stated era and appearance. '
            + 'Retain real room space in every direction, continuous ceiling/sky and floor. '
            + 'Required visible outgoing passages: '+canonical(descriptor.get('exitRoutes', []))
            + '. No extra people. No guide residue. The full picture must be finished.')
        value['prompt'] = instruction
    else:
        prompt = value['prompt'].replace(
            'Create an entirely finished original panorama from the scene description. No layout drawing or blockout image is supplied.',
            'Create an entirely finished original panorama of the scene description; use the generic example only for spherical projection.')
        value['prompt'] = instruction+prompt
    return value


def metrics(source, normalized):
    """Measurements only; edge blending cannot certify semantic correctness."""
    def measurement(data):
        with Image.open(io.BytesIO(data)) as original:
            image = original.convert('RGB')
        width, height = image.size
        edge = ImageChops.difference(image.crop((0, 0, 1, height)),
                                    image.crop((width-1, 0, width, height)))
        return {'width': width, 'height': height, 'bytes': len(data),
                'sha256': sha256(data).hexdigest(),
                'edgeDelta': round(sum(ImageStat.Stat(edge).mean)/3, 4)}
    return {'source': measurement(source), 'normalized': measurement(normalized),
            'geometryAccepted': None, 'note': 'Pixel dimensions and seam deltas are not geometry verdicts'}


def review_findings(observation, expected_people, expected_exits):
    """Reject contradictory exploratory observations before considering scores.

    This is not a gameplay grounding proof and never returns a publish permit.
    """
    if not isinstance(observation, dict):
        return {'issues': ['invalid-observation'], 'canPublishGameplay': False}
    issues = []
    count = observation.get('peopleCount')
    if type(count) is not int or count < 0:
        issues.append('invalid-people-count')
    elif count != len(expected_people):
        issues.append('cast-count-mismatch')
        if observation.get('peopleMatch') is True:
            issues.append('contradictory-people-verdict')
    if observation.get('settingMatches') is not True:
        issues.append('setting-unconfirmed')
    if observation.get('finishedArtwork') is not True or observation.get('guideResidue') is not False:
        issues.append('finished-artwork-unconfirmed')
    allowed = {e['id'] for e in expected_exits}
    exits = observation.get('exits')
    if not isinstance(exits, list):
        issues.append('invalid-exit-evidence')
        exits = []
    if any(not isinstance(e, dict) or e.get('id') not in allowed for e in exits):
        issues.append('invented-exit-id')
    confirmed = {e.get('id') for e in exits if isinstance(e, dict) and e.get('visiblyPassable') is True}
    if allowed-confirmed:
        issues.append('required-exit-unconfirmed')
    if observation.get('projection') != 'equirectangular' or observation.get('allAroundCoherent') is not True:
        issues.append('spherical-geometry-unconfirmed')
    return {'issues': issues, 'canPublishGameplay': False,
            'note': 'Exploratory image review cannot authorize coordinates or state changes'}
