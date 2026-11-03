"""A received correction uses its own input and never poisons the queue."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from app.db import SessionLocal
from app.scenes.grounding import queue_visual_repair
from app.scenes.models import SceneImage
from app.scenes.service import scene_view
from app.scenes.worker import joint_scene_reference,process_one
from app.world.models import World,WorldLine,WorldCheckpoint
from .test_scenes import scene_world,request


@pytest.mark.parametrize('invalid_hash',[False,True])
def test_correction_binds_received_pixels_preserves_posture_and_rejects_invalid_input_before_http(client,scene_world,profile,invalid_hash):
    w=scene_world;initial=request(client,w)
    with SessionLocal() as db:
        world=db.get(World,w['world']);world.source={**world.source,'local360Preview':True}
        row=db.get(SceneImage,initial['id']);row.status='ready'
        row.image={'sha256':'a'*64,'file':'owned-received-pixels'}
        cp=db.get(WorldCheckpoint,w['cp']);before=deepcopy(cp.state)
        head=db.get(WorldLine,w['line']).head_checkpoint_id
        cast=[{**person,'expectedPose':'standing','description':person['description']+' CURRENT standing with empty hands.'} for person in row.descriptor['sceneCast']]
        row.descriptor={**row.descriptor,'renderPipeline':0,'jointSceneReferenceVersion':1,
            'jointReferenceHash':'b'*64,'sceneCast':cast,'jointCompositionVersion':2}
        child_id=queue_visual_repair(db,row,scene_view(cp),[person['entityId'] for person in cast])
        child=db.get(SceneImage,child_id)
        assert child.descriptor['jointReferenceHash']==row.image['sha256']
        assert child.descriptor['sceneCast']==cast
        assert joint_scene_reference(db,child,child.descriptor) is row
        assert queue_visual_repair(db,child,scene_view(cp),['missing']) is None
        if invalid_hash:child.descriptor={**child.descriptor,'jointReferenceHash':'c'*64}
        db.commit()
    if invalid_hash:
        with patch('app.scenes.worker.generate',side_effect=AssertionError('No HTTP allowed for invalid owned input')):
            assert process_one() is True
        with SessionLocal() as db:
            child=db.get(SceneImage,child_id)
            assert child.status=='failed' and child.error_code=='SCENE_REFERENCE_UNAVAILABLE'
            assert child.image['failureDiagnostic']=={'requestSent':False,'responseReceived':False}
            assert process_one() is False
    with SessionLocal() as db:
        assert db.get(WorldCheckpoint,w['cp']).state==before
        assert db.get(WorldLine,w['line']).head_checkpoint_id==head
