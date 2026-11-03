import io,json
from unittest.mock import Mock
import pytest
from PIL import Image
from app.errors import Problem
from app.scenes.fixtures import inspect_fixtures


def cameras():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='JPEG');return [out.getvalue()]*8


def part(identity,view=4):
    return {'id':identity,'visible':True,'viewIndex':view,'anchorPixels':[300,360],'confidence':.95,
        'groundEvidence':{'surface':'walkable-ground','evidence':'Actual visible stone paving directly beside the base of the wooden counter.'}}


def test_missing_fixture_gets_actual_floor_remeasurement_without_replacing_good_contacts():
    good=part('bench');bad={**part('counter',0),'anchorPixels':[300,30]}
    parsed={'fixtures':[good,bad],'composition':{'finishedArtwork':True}}
    targets={'fixtures':[{'id':'bench'},{'id':'counter'}],'exits':[]};stages=[]
    def call(stage,prompt,data,limit):
        stages.append(stage);assert prompt=='world_scene_fixtures' and len(data['_sceneImages'])==1
        assert data['fixtures']==[{'id':'counter'}]
        return {'text':json.dumps({'fixtures':[part('counter')]})}
    result=inspect_fixtures(parsed,targets,cameras(),(2048,1024),call,4096)
    assert stages==['analysis:fixture-view:4'] and result['fixtures'][0]==good
    assert result['fixtures'][1]['fixtureReview']['source']=='single-camera-floor-contact'
    assert parsed['fixtures'][1]==bad


def test_furniture_uncertainty_or_foreign_ids_do_not_become_floor_contacts():
    targets={'fixtures':[{'id':'counter'}],'exits':[]}
    for answer in [{**part('counter'),'groundEvidence':{'surface':'furniture','evidence':'This point is on the actual wooden panel.'}},part('foreign'),{**part('counter'),'confidence':.8}]:
        call=Mock(return_value={'text':json.dumps({'fixtures':[answer]})})
        result=inspect_fixtures({'fixtures':[]},targets,cameras(),(2048,1024),call,4096)
        assert result['fixtures']==[] and call.call_count==4


def test_unknown_fixture_review_is_not_replayed():
    call=Mock(side_effect=Problem('PROVIDER_STATUS_UNKNOWN','unknown'))
    with pytest.raises(Problem):inspect_fixtures({'fixtures':[]},{'fixtures':[{'id':'counter'}],'exits':[]},cameras(),(2048,1024),call,4096)
    assert call.call_count==1
