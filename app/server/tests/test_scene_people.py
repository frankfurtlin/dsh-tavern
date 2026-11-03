import io,json
from unittest.mock import Mock
import pytest
from PIL import Image
from app.scenes.people import inspect_people
from app.errors import Problem


def cameras():
    out=io.BytesIO();Image.new('RGB',(512,512),'brown').save(out,format='JPEG');return [out.getvalue()]*8


def test_bulk_missed_person_requires_real_camera_measurement_and_separate_count():
    calls=[]
    def call(stage,prompt,data,limit):
        calls.append(data)
        if data['inspectionMode']=='composition-recount':return {'text':json.dumps({'visiblePeopleCount':1,'unexpectedPeople':[]})}
        assert len(data['_sceneImages'])==1 and data['camera']['viewIndex']==0
        return {'text':json.dumps({'entityId':'npc','visible':True,'physicalPerson':True,'confidence':.95,'headBoxPixels':[210,180,240,220],'bodyBoxPixels':[200,180,260,430]})}
    original={'composition':{'visiblePeopleCount':0,'unexpectedPeople':[]},'characters':[{'entityId':'npc','visible':False}]}
    result=inspect_people(original,[{'entityId':'npc','name':'店员'}],cameras(),call,16384)
    assert result['characters'][0]['visible'] and result['characters'][0]['viewIndex']==0
    assert result['composition']['visiblePeopleCount']==1 and len(calls)==2
    assert 'headReview' not in result['characters'][0]  # still needs blind crop
    assert original['characters'][0]['visible'] is False


def test_absence_and_wrong_identity_never_become_manufactured_people():
    call=Mock(return_value={'text':json.dumps({'entityId':'other','visible':True,'physicalPerson':True,'confidence':.99,'headBoxPixels':[200,180,240,220]})})
    result=inspect_people({'characters':[]},[{'entityId':'npc'}],cameras(),call,16384)
    assert result['characters']==[] and call.call_count==4


def test_unknown_single_camera_request_is_not_replayed():
    call=Mock(side_effect=Problem('PROVIDER_STATUS_UNKNOWN','unknown'))
    with pytest.raises(Problem):inspect_people({'characters':[]},[{'entityId':'npc'}],cameras(),call,16384)
    assert call.call_count==1
