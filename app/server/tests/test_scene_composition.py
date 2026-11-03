from copy import deepcopy
from math import pi
from types import SimpleNamespace
import pytest
from app.scenes.composition import character_visibility, character_presentations, scene_cast
from app.scenes.provider import prompt_for
from app.scenes.service import receipt
from app.scenes.grounding import VERSION


def view():
    return {'characters':[{'id':'yao','name':'药老','description':'寄居纳戒的灵魂。'},{'id':'lin','name':'林掌柜','description':'药商。'}],
        'characterVisibility':{'yao':'voice','lin':'visible'},
        'hotspots':{'yao':{'yaw':0,'pitch':-.6,'distance':2.5},'lin':{'yaw':0,'pitch':-.6,'distance':2.5}},
        'descriptor':{'placeName':'坊市','placeId':'market','description':'药材铺','pointLabel':'入口','lighting':'清晨'},
        'layout':{'nodes':[]}}


def test_hidden_projection_keeps_voice_without_drawing_an_extra_person():
    person={'name':'药老'}
    text='药老的投影在你走近铺子时悄然散去，声音只在你纳戒里响起：「就是这里。」'
    assert character_visibility(person,text)=='voice'
    assert character_visibility(person,text+'药老重新显出淡淡投影。')=='visible'
    assert character_visibility(person,'药老说：「药老的投影散去后，你也不必担心。」')=='visible'
    assert character_visibility(person,'你想起药老的投影散去的那天。')=='visible'


def test_panorama_bindings_are_owned_by_the_picture_not_entity_order_or_old_sprite_coordinates():
    scene=view();row=SimpleNamespace(status='ready',descriptor={},image={'sha256':'pic','bindingImageHash':'pic','anchorVersion':VERSION,'characterAnchors':{'lin':{'yaw':.7,'pitch':.5,'verified':True,'source':'image-inspection'},'yao':{'yaw':.7,'pitch':.5},'absent':{'yaw':1,'pitch':0}}})
    expected={'yao':{'mode':'voice'},'lin':{'mode':'embedded','yaw':.7,'pitch':.5}}
    assert character_presentations(row,scene)==expected
    scene['characters'].reverse();assert character_presentations(row,scene)==expected
    assert row.image['characterAnchors']['yao']['yaw']==.7
    scene['characters']=[];assert character_presentations(row,scene)=={}


def test_legacy_and_invalid_bindings_do_not_invent_markers_or_cutouts():
    scene=view();row=SimpleNamespace(status='ready',descriptor={},image={})
    assert character_presentations(row,scene)['lin']=={'mode':'offscreen'}
    for bad in [{'yaw':0,'pitch':float('nan')},{'yaw':0,'pitch':pi},{'yaw':'0','pitch':0}]:
        row.image={'characterAnchors':{'lin':bad}}
        assert character_presentations(row,scene)['lin']=={'mode':'offscreen'}


def test_future_render_cast_excludes_hidden_spirits_and_separates_shared_fixture_positions():
    scene=view();cast=scene_cast(scene)
    assert [p['entityId'] for p in cast]==['lin']
    scene['characterVisibility']['yao']='visible';cast=scene_cast(scene)
    assert abs(cast[0]['yaw']-cast[1]['yaw'])>=.48
    row=SimpleNamespace(status='ready',descriptor={'sceneCast':cast},image={})
    assert all(p['mode']=='offscreen' for p in character_presentations(row,scene).values())
    prompt=prompt_for({**scene['descriptor'],'sceneCast':cast})
    assert 'ONE coherent illustration' in prompt and '林掌柜' in prompt and 'ONCE each' in prompt
    assert 'Render an EMPTY environment' not in prompt


def test_verified_binding_survives_receipt_projection_without_changing_saved_art_or_story():
    scene=view();row=SimpleNamespace(id='image',world_id='w',line_id='l',status='ready',scene_hash='hash',generation=1,error_code=None,
        image={'file':'private.enc','sha256':'pic','bindingImageHash':'pic','anchorVersion':VERSION,'characterAnchors':{'lin':{'yaw':.7,'pitch':.5,'verified':True,'source':'image-inspection'}}},descriptor={'hotspots':{'lin':{'yaw':-2,'pitch':-.8}}})
    original=deepcopy(row.image);result=receipt(row,scene)
    assert result['characterPresentation']['lin']=={'mode':'embedded','yaw':.7,'pitch':.5}
    assert 'file' not in result['image'] and row.image==original


def test_image_edit_transport_keeps_references_and_uses_only_the_edit_workflow(monkeypatch):
    """Inspect the actual HTTP body rather than a reconstructed request fixture."""
    import base64, io, json, httpx
    from PIL import Image
    from app.scenes import provider
    image=io.BytesIO();Image.new('RGB',(128,64),'#987654').save(image,format='PNG')
    source=image.getvalue();requests=[];client=httpx.Client
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(source).decode()}]})
    monkeypatch.setattr(provider,'public_address',lambda:'1.1.1.1')
    monkeypatch.setattr(provider,'decrypt_key',lambda _: 'test-only-secret')
    monkeypatch.setattr(provider,'proxy_options',lambda _: {})
    monkeypatch.setattr(provider.httpx,'Client',lambda **kwargs:client(transport=httpx.MockTransport(respond),**kwargs))
    descriptor={'placeName':'起居室','description':'明亮的起居室','pointLabel':'入口','lighting':'清晨'}
    provider.generate(descriptor,'encrypted',source)
    body=requests[0]
    assert 'return_base64' not in body
    assert body['extra_body']['response_format']=='b64_json'
    assert len(body['extra_body']['image'])==1
    assert body['extra_body']['image'][0].startswith('data:image/png;base64,')
    assert base64.b64decode(body['extra_body']['image'][0].split(',')[1])==source
    provider.generate({**descriptor,'sceneCast':[{'entityId':'a','yaw':3.1}],
        'characterReferenceOrder':[{'entityId':'a'}]},'encrypted',character_references=[source])
    body=requests[1]
    assert 'return_base64' not in body and len(body['extra_body']['image'])==2
    assert base64.b64decode(body['extra_body']['image'][1].split(',')[1])==source
    assert 'Images 2 onward' in body['prompt']


def test_scene_guide_moves_camera_and_keeps_cast_away_from_transport_seam():
    import io
    from PIL import Image
    from app.scenes.guide import scene_guide
    from app.scenes.provider import character_layout, guide_rotation, encode_panorama
    descriptor={'spaceKind':'interior','viewpoint':{'x':0,'z':0},
        'renderLayout':{'nodes':[{'id':'table','fixture':'table','worldX':0,'worldZ':-3}]}}
    entry=scene_guide(descriptor)
    close=scene_guide({**descriptor,'viewpoint':{'x':1,'z':-2.35}})
    assert entry.tobytes()!=close.tobytes()
    def table_pixels(image):
        return [(i%image.width,i//image.width) for i,rgb in enumerate(image.getdata()) if rgb==(124,91,61)]
    before,after=table_pixels(entry),table_pixels(close)
    assert len(after)>len(before)*1.5
    assert sum(x for x,y in after)/len(after)<close.width*.45
    portraits=[]
    for colour in ('red','blue'):
        output=io.BytesIO();Image.new('RGB',(64,128),colour).save(output,format='PNG');portraits.append(output.getvalue())
    cast=[{'entityId':'a','yaw':3.07},{'entityId':'b','yaw':3.67}]
    guided={**descriptor,'sceneCast':cast,'characterReferenceOrder':[{'entityId':'a'},{'entityId':'b'}]}
    data=character_layout(guided,portraits)
    with Image.open(io.BytesIO(data)) as image:
        assert image.size==(3136,1344)
        coloured=[i%image.width for i,(r,g,b) in enumerate(image.convert('RGB').getdata()) if 155<r<181 and 130<g<155 and 105<b<130 and .482<(i//image.width)/image.height<.502]
        assert coloured and min(coloured)>image.width*.35 and max(coloured)<image.width*.65
    restored,metadata=encode_panorama(data,reference_yaw=guide_rotation(guided))
    assert metadata['referenceYaw']==guide_rotation(guided)
    with Image.open(io.BytesIO(restored)) as image:
        reds=[i%image.width for i,(r,g,b) in enumerate(image.convert('RGB').getdata()) if 155<r<181 and 130<g<155 and 105<b<130 and .482<(i//image.width)/image.height<.502]
        assert max(reds)>image.width*.9


def test_extra_people_and_perspective_are_independent_of_valid_cast_anchors():
    from app.scenes.binding import composition_from_result
    from app.scenes.cache import usable_reference
    cast=[{'entityId':'a'},{'entityId':'b'}]
    good={'composition':{'projection':'equirectangular','confidence':.95,'visiblePeopleCount':2,'unexpectedPeople':[],'referenceArtifacts':[]}}
    assert composition_from_result(good,cast)['issues']==[]
    bad=deepcopy(good);bad['composition']['visiblePeopleCount']=3;bad['composition']['unexpectedPeople']=['woman by the fireplace']
    result=composition_from_result(bad,cast)
    assert result['verified'] and result['issues']==['unexpected-people']
    row=SimpleNamespace(descriptor={'appearance':{'version':3}},image={'composition':result})
    assert not usable_reference(row)
    bad['composition'].update(projection='perspective',visiblePeopleCount=2,unexpectedPeople=[])
    assert composition_from_result(bad,cast)['issues']==['projection-mismatch']
    bad['composition']['visiblePeopleCount']=True
    assert not composition_from_result(bad,cast)['verified']
    assert not composition_from_result({'characters':[]},cast)['verified']


def test_interior_with_open_sky_is_rejected_even_when_panorama_and_cast_match():
    from app.scenes.binding import composition_from_result
    observed={'composition':{'projection':'equirectangular','confidence':.95,'visiblePeopleCount':0,
                            'unexpectedPeople':[],'referenceArtifacts':[],'spaceKind':'outdoor','openSky':True}}
    result=composition_from_result(observed,[],{'spaceKind':'interior'})
    assert result['verified'] and result['issues']==['space-mismatch']
    observed['composition'].update(spaceKind='interior',openSky=False)
    assert composition_from_result(observed,[],{'spaceKind':'interior'})['issues']==[]
    del observed['composition']['openSky']
    assert not composition_from_result(observed,[],{'spaceKind':'interior'})['verified']


@pytest.mark.parametrize('finished,regions,expected',[
    (False,['VIEW 3: white untextured cuboid beside painted brickwork.'],'unfinished-artwork'),
    (True,['VIEW 1: blank gray blockout stairs.'],'unfinished-artwork'),
    (None,[],'artwork-unconfirmed'),
    (True,None,'artwork-unconfirmed'),
    (True,[],None),
])
def test_finished_artwork_is_independent_of_valid_people_and_open_routes(finished,regions,expected):
    from app.scenes.binding import composition_from_result
    result={'composition':{'projection':'equirectangular','confidence':.95,
        'visiblePeopleCount':0,'unexpectedPeople':[],'referenceArtifacts':[],
        'finishedArtwork':finished,'unpaintedGuideRegions':regions}}
    measured=composition_from_result(result,[],{'artworkInspectionVersion':1})
    assert measured['issues']==([expected] if expected else [])
    if expected=='artwork-unconfirmed':assert measured['verified'] is False
    else:assert measured['verified'] is True


def test_preexisting_receipt_does_not_invent_new_artwork_inspection():
    from app.scenes.binding import composition_from_result
    result={'composition':{'projection':'equirectangular','confidence':.95,
        'visiblePeopleCount':0,'unexpectedPeople':[],'referenceArtifacts':[]}}
    assert composition_from_result(result,[])['issues']==[]
    assert composition_from_result(result,[])['finishedArtwork'] is None


def test_one_exit_cannot_make_an_extra_door_on_the_adjacent_wall():
    from app.scenes.guide import scene_guide
    for camera in ({'x':0,'z':0},{'x':2,'z':1}):
        picture=scene_guide({'spaceKind':'interior','viewpoint':camera,'exitRoutes':[{'yaw':3.075}]})
        # At eye level, blue pixels are open portals. Count circular runs so a
        # door straddling the panorama seam is still a single physical opening.
        row=[]
        for x in range(picture.width):
            red,green,blue=picture.getpixel((x,picture.height//2))
            row.append(red<140 and green>red+15 and blue>red+18)
        assert sum(visible and not row[index-1] for index,visible in enumerate(row))==1
