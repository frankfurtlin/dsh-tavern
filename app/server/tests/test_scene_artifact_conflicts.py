from copy import deepcopy
import pytest
from app.scenes.artifact_conflicts import request,cleared
from app.scenes.binding import composition_from_result
from app.scenes.scene_review import apply
from .test_scene_progressive import observation
from .test_scenes import scene_world


def conflict():
    items=['Gold-framed portrait of a man on the right wall.']
    parsed=apply({'composition':{'confidence':.98,'referenceArtifacts':items},'characters':[]},observation(),'actual-hash')
    parsed['referenceArtifactRecheck']={'version':1,'imageHash':'actual-hash','candidates':deepcopy(items),
        'observation':{**observation(),'evidence':'A coherent framed oil painting is mounted on the actual room wall, sharing perspective and brushwork.'}}
    return parsed,{'sceneReviewVersion':1,'sceneImageHash':'actual-hash','spaceKind':'interior'}


def test_wall_art_conflict_uses_original_texture_and_preserves_both_independent_receipts():
    parsed,expected=conflict();before=deepcopy(parsed)
    payload=request(b'actual-panorama',{},parsed['composition']['referenceArtifacts'])
    assert len(payload['_sceneImages'])==1 and 'UNCONFIRMED hypotheses' in payload['instruction']
    assert cleared(parsed,[],expected)
    measured=composition_from_result(parsed,[],expected)
    assert measured['verified'] and not measured['issues'] and parsed==before


@pytest.mark.parametrize('invalid',['missing','hash','candidates','uncertain','low-confidence','malformed',
    'reference-panel','extra-person','outdoor','boolean-confidence'])
def test_unresolved_or_real_artifact_cannot_be_cleared_by_conflict_recheck(invalid):
    parsed,expected=conflict();proof=parsed['referenceArtifactRecheck'];value=proof['observation']
    if invalid=='missing':parsed.pop('referenceArtifactRecheck')
    elif invalid=='hash':proof['imageHash']='other-image'
    elif invalid=='candidates':proof['candidates']=['a different picture']
    elif invalid=='uncertain':value['spaceKind']='uncertain'
    elif invalid=='low-confidence':value['confidence']=.84
    elif invalid=='boolean-confidence':value['confidence']=True
    elif invalid=='malformed':proof['observation']={'referenceArtifacts':[]}
    elif invalid=='reference-panel':value['referenceArtifacts']=['A pasted identity reference sheet, with labels and disconnected background.']
    elif invalid=='extra-person':proof['observation']=observation(1)
    elif invalid=='outdoor':value['spaceKind']='outdoor'
    assert not cleared(parsed,[],expected)
    assert 'reference-artifacts' in composition_from_result(parsed,[],expected)['issues']


def test_conflict_clearance_never_hides_primary_artifacts_or_bad_person_inventory():
    parsed,expected=conflict();parsed['sceneReview']['observation']['referenceArtifacts']=['Actual reference sheet.']
    assert 'reference-artifacts' in composition_from_result(parsed,[],expected)['issues']
    parsed,expected=conflict();expected['peopleInventoryVersion']=1
    assert 'people-count-unconfirmed' in composition_from_result(parsed,[],expected)['issues']


@pytest.mark.parametrize('outcome',['clear','artifact','unknown','malformed'])
def test_real_binding_disagreement_has_one_metered_recheck_and_keeps_story_unchanged(client,scene_world,profile,outcome):
    import json
    from unittest.mock import patch
    from app.db import SessionLocal
    from app.models import ModelProfile,Task
    from app.scenes.models import SceneImage
    from app.scenes.binding import queue_binding,bind_scene
    from app.world.models import WorldLine
    from app.errors import Problem
    from .test_scene_progressive import prepared_preview
    parent,child=prepared_preview(client,scene_world);targets={'characters':[],'fixtures':[],'exits':[]}
    with patch('app.scenes.binding.inspection_targets',return_value=targets):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child);row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'peopleInventoryVersion':1,'sceneReviewVersion':1}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();tid=row.image['bindingTaskId'];digest=row.image['sha256']
        calls=[]
        def received(model,prompt,data,limit):
            mode=data.get('inspectionMode');calls.append(mode)
            if mode=='single-panorama-composition-v1':value=observation()
            elif mode=='single-camera-physical-head-inventory':
                value={'count':0,'heads':[],'confidence':.98,'evidence':'No physical people in this actual room camera.'}
            elif mode=='single-panorama-artifact-conflict-v1':
                if outcome=='unknown':raise Problem('PROVIDER_STATUS_UNKNOWN','Unknown paid response',502)
                value=observation()
                if outcome=='artifact':value['referenceArtifacts']=['An actual pasted reference sheet with labels.']
                elif outcome=='malformed':value={'referenceArtifacts':[]}
            else:value={'characters':[],'fixtures':[],'exits':[],
                'composition':{'confidence':.98,'referenceArtifacts':['Gold-framed wall portrait.']}}
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):
            if outcome=='unknown':
                with pytest.raises(Problem):bind_scene(tid)
            else:bind_scene(tid)
    assert calls.count('single-panorama-artifact-conflict-v1')==1
    with SessionLocal() as db:
        row=db.get(SceneImage,child);task=db.get(Task,tid)
        assert row.image['sha256']==digest
        assert db.get(WorldLine,scene_world['line']).head_checkpoint_id==scene_world['cp']
        if outcome=='clear':
            assert row.image['composition']['verified'] and not row.image['composition']['issues']
            assert task.state=='succeeded'
        elif outcome!='unknown':assert 'reference-artifacts' in row.image['composition']['issues']
        else:assert row.image.get('anchorStatus')!='complete'
