import json
from copy import deepcopy
import pytest
from app.scenes.people import inspect_people,same_head
from app.scenes.heads import person_context
from .test_scene_people import cameras


def person(ident,view,pose='standing'):
    return {'entityId':ident,'viewIndex':view,'visible':True,'physicalPerson':True,'confidence':.97,
            'pose':pose,'headBoxPixels':[210,190,245,235]}


def test_search_uses_pose_without_relabelling_one_physical_person_as_two_roles():
    cast=[{'entityId':'holmes','expectedPose':'seated'},{'entityId':'peterson','expectedPose':'standing'}]
    original={'characters':[person('holmes',0),person('peterson',0)]};before=deepcopy(original);calls=[]
    def call(stage,prompt,data,limit):
        calls.append(data)
        if data['inspectionMode']=='composition-recount':return {'text':json.dumps({'visiblePeopleCount':2,'unexpectedPeople':[]})}
        ident=data['person']['entityId'];index=data['camera']['viewIndex']
        assert data['person']['expectedPose']==('seated' if ident=='holmes' else 'standing')
        return {'text':json.dumps(person(ident,index,'seated' if index==2 else 'standing'))}
    result=inspect_people(original,cast,cameras(),call,1000)
    assert result['characters'][0]['entityId']=='peterson' and result['characters'][0]['viewIndex']==0
    assert result['characters'][1]['entityId']=='holmes' and result['characters'][1]['viewIndex']==2
    assert not same_head(*result['characters']) and original==before
    assert len(calls)==4 and person_context(cast[0])['expectedPose']=='seated'


def test_two_names_cannot_claim_same_standing_head_when_every_camera_returns_it():
    cast=[{'entityId':'one'},{'entityId':'two'}];calls=[]
    def call(stage,prompt,data,limit):
        calls.append(data)
        if data['inspectionMode']=='composition-recount':return {'text':json.dumps({'visiblePeopleCount':2,'unexpectedPeople':[]})}
        if data['camera']['viewIndex']!=0:return {'text':'{"visible":false}'}
        return {'text':json.dumps(person('two',0))}
    result=inspect_people({'characters':[person('one',0)]},cast,cameras(),call,1000)
    assert [p['entityId'] for p in result['characters']]==['one'] and len(calls)==4


@pytest.mark.parametrize('second_view',[0,1,2,3])
def test_same_camera_boxes_do_not_collide_in_different_directions(second_view):
    assert same_head(person('one',0),person('two',second_view)) is (second_view==0)


def test_wrap_seam_same_head_in_two_actual_cameras_is_one_claim():
    from app.scenes.inspection import camera_pixel,sphere_pixel
    first={**person('one',2),'headBoxPixels':[240,250,275,295]}
    # The same physical head, projected into the downward view.
    corners=[camera_pixel(6,*sphere_pixel(2,x,y,2048,1024),2048,1024)
             for x in (240,275) for y in (250,295)]
    other={**person('two',6),'headBoxPixels':[min(p[0] for p in corners),min(p[1] for p in corners),
        max(p[0] for p in corners),max(p[1] for p in corners)]}
    assert same_head(first,other)


def test_body_conflict_keeps_actual_wrong_pose_and_searches_remaining_view():
    from app.scenes.bodies import inspect_bodies
    from .test_scene_bodies import fixture
    parsed,cast,frames,observed=fixture();cast[0]['expectedPose']='seated';calls=[]
    def call(stage,prompt,data,limit):
        calls.append(data)
        assert data['person']['expectedPose']=='seated'
        return {'text':json.dumps(observed)}  # Actual standing evidence never becomes seated.
    result=inspect_bodies(parsed,cast,frames,'a'*64,(2048,1024),call,1000)
    assert len(calls)==2 and result['characters'][0]['pose']=='standing'
