import base64
import io
from copy import deepcopy

from PIL import Image
import pytest

from app.scenes.panorama_experiment import projection_example, request_variant, metrics, review_findings


def descriptor():
    return {'kind': 'scene-environment', 'renderStage': 'environment', 'sceneCast': [],
            'placeName': '旧书店', 'description': '书架、木柜台与灯光', 'lighting': '黄昏',
            'experimentCast': [{'entityId': 'clerk', 'name': '陈墨', 'appearance': '年轻店员'}]}


def request():
    return {'model': 'agnes-image-2.5-flash', 'ratio': '21:9', 'size': '2K',
            'prompt': 'Create an entirely finished original panorama from the scene description. No layout drawing or blockout image is supplied. EXACTLY ZERO PEOPLE.',
            'return_base64': True}


def test_experiment_baseline_preserves_original_request_and_descriptor():
    original, desc = request(), descriptor()
    frozen = deepcopy((original, desc))
    assert request_variant(original, desc, 'baseline') == original
    assert (original, desc) == frozen


def test_projection_example_is_periodic_full_sphere_without_copied_assets():
    image = projection_example(128)
    assert image.size == (128, 64)
    assert image.crop((0, 0, 1, 64)).tobytes() == image.crop((127, 0, 128, 64)).tobytes()
    assert image.getpixel((20, 0)) == (205, 218, 223)
    assert image.getpixel((20, 63)) != image.getpixel((20, 0))


@pytest.mark.parametrize('width', [63, 65, 4096, True])
def test_projection_example_rejects_unbounded_or_odd_input(width):
    with pytest.raises(ValueError):
        projection_example(width)


def test_geometry_uses_editing_protocol_without_conflicting_no_reference_instruction():
    body = request_variant(request(), descriptor(), 'geometry')
    assert body['ratio'] == '21:9' and body['size'] == '2K'
    assert 'return_base64' not in body
    assert 'No layout drawing or blockout image is supplied' not in body['prompt']
    assert 'EXACTLY ZERO PEOPLE' in body['prompt']
    assert body['extra_body']['response_format'] == 'b64_json'
    uri = body['extra_body']['image'][0]
    image = Image.open(io.BytesIO(base64.b64decode(uri.split(',')[1])))
    assert image.size == (3136, 1344)


def test_direct_cast_removes_empty_stage_prohibition_without_changing_checkpoint_input():
    desc = descriptor()
    frozen = deepcopy(desc)
    body = request_variant(request(), desc, 'geometry-cast')
    assert 'EXACTLY ZERO PEOPLE' not in body['prompt']
    assert '陈墨' in body['prompt'] and 'clerk' in body['prompt']
    assert desc == frozen


@pytest.mark.parametrize('strategy', ['geometry', 'geometry-cast'])
def test_experiment_cannot_replace_an_actual_scene_or_character_reference(strategy):
    body = request()
    body['extra_body'] = {'image': ['original-real-artwork']}
    with pytest.raises(ValueError, match='actual scene'):
        request_variant(body, descriptor(), strategy)


def test_metrics_do_not_accept_geometry_based_on_image_shape():
    image = Image.new('RGB', (128, 64), 'gray')
    output = io.BytesIO()
    image.save(output, format='PNG')
    result = metrics(output.getvalue(), output.getvalue())
    assert result['source']['edgeDelta'] == 0
    assert result['geometryAccepted'] is None


def test_review_cannot_hide_extra_people_behind_a_positive_match_boolean():
    result = review_findings({'peopleCount': 5, 'peopleMatch': True}, [{'id': 'one'}, {'id': 'two'}], [])
    assert 'cast-count-mismatch' in result['issues']
    assert 'contradictory-people-verdict' in result['issues']
    assert result['canPublishGameplay'] is False


def test_review_cannot_invent_discovered_exits():
    result = review_findings({'peopleCount': 0, 'exits': [{'id': 'made-up', 'visiblyPassable': True}]}, [], [])
    assert 'invented-exit-id' in result['issues']
    assert result['canPublishGameplay'] is False


def test_exploratory_review_never_authorizes_gameplay_even_with_positive_observations():
    result = review_findings({'peopleCount': 0, 'settingMatches': True, 'finishedArtwork': True,
                             'guideResidue': False, 'exits': [], 'projection': 'equirectangular',
                             'allAroundCoherent': True}, [], [])
    assert not result['issues']
    assert result['canPublishGameplay'] is False
