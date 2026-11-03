"""An owned edit input never becomes an accepted output proof."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.errors import Problem
from app.scenes.worker import joint_scene_reference
from app.scenes.models import SceneImage
from app.world.models import World


def fixture():
    world=SimpleNamespace(owner_id='owner',source={'local360Preview':True})
    source=SimpleNamespace(id='input',owner_id='owner',world_id='world',line_id='line',checkpoint_id='cp',
       profile_id='image-profile',profile_version=1,status='ready',
       image={'file':'actual-encrypted-file','sha256':'a'*64,'anchorStatus':'unavailable'},descriptor={'placeId':'camp'})
    row=SimpleNamespace(owner_id='owner',world_id='world',line_id='line',checkpoint_id='cp',profile_id='image-profile',profile_version=1)
    descriptor={'jointSceneReferenceVersion':1,'renderPipeline':0,'referenceSceneId':'input','jointReferenceHash':'a'*64,'placeId':'camp'}
    db=Mock();db.get.side_effect=lambda model,ident: world if model is World and ident=='world' else source if model is SceneImage and ident=='input' else None
    return db,world,source,row,descriptor


def test_inconclusive_pixels_can_be_explicit_edit_input_without_claiming_a_quality_pass():
    db,world,source,row,descriptor=fixture();before=deepcopy(source.__dict__)
    assert joint_scene_reference(db,row,descriptor) is source
    assert source.__dict__==before and source.image['anchorStatus']=='unavailable'


@pytest.mark.parametrize('problem',['owner','world','line','checkpoint','profile','profile-version','status',
 'missing-file','hash','place','scope','pipeline','private-stage','character','version'])
def test_input_cannot_cross_owner_world_checkpoint_or_become_a_hidden_gate_bypass(problem):
    db,world,source,row,descriptor=fixture()
    if problem=='owner':source.owner_id='other'
    elif problem=='world':source.world_id='other'
    elif problem=='line':source.line_id='other'
    elif problem=='checkpoint':source.checkpoint_id='old'
    elif problem=='profile':source.profile_id='other'
    elif problem=='profile-version':source.profile_version=2
    elif problem=='status':source.status='unknown'
    elif problem=='missing-file':source.image.pop('file')
    elif problem=='hash':descriptor['jointReferenceHash']='b'*64
    elif problem=='place':source.descriptor['placeId']='creek'
    elif problem=='scope':world.source={}
    elif problem=='pipeline':descriptor['renderPipeline']=1
    elif problem=='private-stage':descriptor['renderStage']='actor'
    elif problem=='character':descriptor['kind']='character'
    elif problem=='version':descriptor['jointSceneReferenceVersion']=2
    with pytest.raises(Problem) as error:joint_scene_reference(db,row,descriptor)
    assert error.value.code=='SCENE_REFERENCE_UNAVAILABLE'
