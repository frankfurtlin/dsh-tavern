import json
from unittest.mock import patch
import pytest
from app.db import SessionLocal
from app.models import ModelProfile
from app.scenes.models import SceneImage
from app.scenes.binding import queue_binding,bind_scene
from app.errors import Problem
from app.world.models import WorldLine
from .test_scenes import scene_world
from .test_scene_progressive import prepared_preview,observation


@pytest.mark.parametrize('outcome',['correct','still-wrong','unknown','malformed','no-conflict','real-extras'])
def test_count_only_disagreement_has_one_bound_recheck_without_ignoring_actual_extras(client,scene_world,profile,outcome):
    _,child=prepared_preview(client,scene_world);targets={'characters':[],'fixtures':[],'exits':[]};calls=[]
    with patch('app.scenes.binding.inspection_targets',return_value=targets):
        with SessionLocal() as db:
            db.get(ModelProfile,profile['id']).context_window=200000
            row=db.get(SceneImage,child);row.descriptor={**row.descriptor,'sceneCast':[],'futureCast':[],
                'peopleInventoryVersion':1,'sceneReviewVersion':1}
            with patch('app.scenes.binding.settings.ai_mode','live'):queue_binding(db,row)
            db.commit();tid=row.image['bindingTaskId'];digest=row.image['sha256']
        def received(model,prompt,data,limit):
            mode=data.get('inspectionMode');calls.append(mode)
            if mode=='single-panorama-composition-v1':value=observation(4 if outcome=='real-extras' else 0 if outcome=='no-conflict' else 1)
            elif mode=='single-camera-physical-head-inventory':
                value={'count':1 if outcome=='real-extras' else 0,'heads':[{'box':[300,250,380,350],
                    'evidence':'An actual separate physical head with a visible body.'}] if outcome=='real-extras' else [],
                    'confidence':.97,'evidence':'All actual camera pixels inspected, excluding framed wall paintings.'}
            elif mode=='single-panorama-count-conflict-v1':
                assert len(data['_sceneImages'])==1 and 'previousCount' not in data
                if outcome=='unknown':raise Problem('PROVIDER_STATUS_UNKNOWN','Unknown',502)
                value=observation(1 if outcome=='still-wrong' else 0)
                if outcome=='malformed':value={'visiblePeopleCount':0}
            else:value={'characters':[],'fixtures':[],'exits':[]}
            return {'text':json.dumps(value),'finishReason':'stop','latencyMs':1,
                'usage':{'state':'known','inputTokens':10,'outputTokens':10}}
        with patch('app.worker.MockAdapter.generate',side_effect=received):
            if outcome=='unknown':
                with pytest.raises(Problem):bind_scene(tid)
            else:bind_scene(tid)
    assert calls.count('single-camera-physical-head-inventory')==4
    assert calls.count('single-panorama-count-conflict-v1')==(0 if outcome in ('no-conflict','real-extras') else 1)
    with SessionLocal() as db:
        row=db.get(SceneImage,child);assert row.image['sha256']==digest
        assert db.get(WorldLine,scene_world['line']).head_checkpoint_id==scene_world['cp']
        if outcome in ('correct','no-conflict'):assert not row.image['composition']['issues']
        elif outcome!='unknown':assert row.image['composition']['issues']
        if outcome=='correct':
            proof=row.image['sceneCountRecheck']
            assert proof['imageHash']==digest and proof['original']['visiblePeopleCount']==1
            assert proof['observation']['visiblePeopleCount']==0
