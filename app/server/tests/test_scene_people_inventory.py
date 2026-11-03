import base64,io
from copy import deepcopy
from PIL import Image
import pytest
from app.scenes.people_inventory import request,evaluate
from app.scenes.binding import composition_from_result,inspection_stages
from .test_scene_progressive import observation
from types import SimpleNamespace

def proof(heads=()):
 return {'version':1,'imageHash':'a'*64,'views':[{'viewIndex':i,'observation':{
  'count':len(heads) if i==3 else 0,'heads':list(heads) if i==3 else [],
  'confidence':.95,'evidence':'Actual separate bodies and heads, or an empty physical camera.'}} for i in range(4)]}

def head(box):return {'box':box,'evidence':'A separate physical head attached to its own visible torso.'}

def test_two_adjacent_identical_faces_do_not_become_one_physical_person():
 p=proof([head([190,540,260,640]),head([275,530,350,635])])
 assert evaluate(p,'a'*64)=={'verified':True,'count':2}
 scene={'sceneReviewVersion':1,'peopleInventoryVersion':1,'sceneImageHash':'a'*64,'spaceKind':'interior'}
 whole=observation();whole.update(visiblePeopleCount=1,instances=[{'instanceId':'missed-duplicate','entityId':'one',
  'headBox':[100,500,130,560],'evidence':'Whole texture mistakenly saw only one physical person.'}])
 result=composition_from_result({'characters':[],'sceneReview':{'version':1,'imageHash':'a'*64,'observation':whole},'peopleInventory':p},[{'entityId':'one'}],scene)
 assert 'unexpected-people' in result['issues'] and result['visiblePeopleCount']==2


def test_physical_count_is_bound_to_image_and_all_four_owned_camera_observations():
 p=proof();assert evaluate(p,'a'*64)=={'verified':True,'count':0}
 assert not evaluate(p,'b'*64)['verified']
 for mutate in (lambda p:p['views'].pop(),lambda p:p['views'][1].update(viewIndex=0),
                lambda p:p['views'][0]['observation'].update(count=1),
                lambda p:p['views'][0]['observation'].update(confidence=.84)):
  bad=deepcopy(p);mutate(bad);assert not evaluate(bad,'a'*64)['verified']


def test_cross_camera_dedup_preserves_distinct_heads_in_same_camera(monkeypatch):
 # Controlled projection makes the association ambiguity explicit: one broad
 # old view overlaps BOTH actual heads in a clearer camera. It can match only one.
 import app.scenes.people_inventory as inventory
 monkeypatch.setattr(inventory,'sphere_pixel',lambda view,x,y,w,h:(x,y))
 p=proof([head([200,500,260,600]),head([270,500,330,600])])
 p['views'][0]['observation'].update(count=1,heads=[head([190,490,340,610])])
 assert evaluate(p,'a'*64)=={'verified':True,'count':2}
 p['views'][1]['observation'].update(count=1,heads=[head([200,500,260,600])])
 assert evaluate(p,'a'*64)=={'verified':True,'count':2}


def test_invalid_boxes_and_identical_duplicated_boxes_cannot_certify_an_inventory():
 p=proof([head([190,540,260,640]),head([190,540,260,640])])
 assert not evaluate(p,'a'*64)['verified']
 for box in ([190,540,180,640],[190,540,float('nan'),640],[190,540,1001,640],[True,540,260,640]):
  assert not evaluate(proof([head(box)]),'a'*64)['verified']


def test_inventory_wire_has_one_actual_camera_and_never_supplies_desired_cast():
 picture=io.BytesIO();Image.new('RGB',(512,512),'brown').save(picture,format='PNG')
 data=request(picture.getvalue());assert len(data['_sceneImages'])==1
 assert 'futureCast' not in data and 'expectedIds' not in data and 'expectedCount' not in data
 actual=Image.open(io.BytesIO(base64.b64decode(data['_sceneImages'][0].split(',',1)[1])))
 assert actual.size==(1000,1000)
 a=SimpleNamespace(descriptor={});b=SimpleNamespace(descriptor={'peopleInventoryVersion':1})
 assert len(inspection_stages(b,{'exits':[]}))-len(inspection_stages(a,{'exits':[]}))==4


def uncertain_window():
 p=proof([head([760,290,790,320])]);p['views'][3]['observation']['confidence']=.45
 return p


def clarify(p,heads=(),confidence=.95):
 p['views'][3]['clarification']={'source':'same-camera-person-detail-v1','viewIndex':3,
  'candidateRegions':[[760,290,790,320]],'observation':{'count':len(heads),'heads':list(heads),
   'confidence':confidence,'evidence':'Actual enlarged window geometry and full camera confirm the physical-person result.'}}
 return p


def test_uncertain_window_shape_needs_a_new_confident_same_camera_observation():
 p=uncertain_window();before=deepcopy(p)
 assert not evaluate(p,'a'*64)['verified']
 assert evaluate(clarify(p),'a'*64)=={'verified':True,'count':0}
 assert p['views'][3]['observation']==before['views'][3]['observation']
 assert evaluate(clarify(uncertain_window(),[head([760,290,790,320])]),'a'*64)=={'verified':True,'count':1}


def test_detail_does_not_waive_uncertainty_or_accept_foreign_camera_coordinates():
 assert not evaluate(clarify(uncertain_window(),confidence=.7),'a'*64)['verified']
 for field,value in [('viewIndex',2),('candidateRegions',[[1,2,3,4]]),('source','unbound-crop')]:
  p=clarify(uncertain_window());p['views'][3]['clarification'][field]=value
  assert not evaluate(p,'a'*64)['verified']


def test_people_detail_sends_real_context_crops_without_painting_or_desired_count():
 picture=io.BytesIO();Image.new('RGB',(512,512),'brown').save(picture,format='PNG')
 data=request(picture.getvalue(),clarify=uncertain_window()['views'][3]['observation'])
 assert data['inspectionMode']=='same-camera-person-detail' and len(data['_sceneImages'])==2
 assert data['candidateRegions']==[[760,290,790,320]] and 'expectedCount' not in data
 assert data['_visionStream'] is False
 for uri in data['_sceneImages']:
  actual=Image.open(io.BytesIO(base64.b64decode(uri.split(',',1)[1])))
  assert actual.size==(1000,1000) and actual.getpixel((500,500))==pytest.approx((165,42,42),abs=5)
 a=SimpleNamespace(descriptor={'peopleInventoryVersion':1})
 b=SimpleNamespace(descriptor={'peopleInventoryVersion':1,'peopleClarificationVersion':1})
 assert len(inspection_stages(b,{'exits':[]}))-len(inspection_stages(a,{'exits':[]}))==4
