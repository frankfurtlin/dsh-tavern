"""A picture never authorizes an interaction from a different visual state."""
from copy import deepcopy
from math import pi
from types import SimpleNamespace
import io
import pytest
from PIL import Image
from app.scenes.cache import appearance, compatible
from app.scenes.grounding import VERSION, grounded_points, spatial_anchors_from_result
from app.scenes.composition import character_presentations
from app.scenes.binding import anchors_from_result
from app.scenes.inspection import contact_sheet, sphere_pixel, panorama_coordinates


def test_render_route_cues_affect_cache_without_changing_story_or_exposing_hidden_places():
    from app.scenes.layout import route_visual_cues
    from app.scenes.grounding import inspection_targets
    routes=[{'id':'inn','name':'阿尔法酒馆','portalId':'path'},
            {'id':'market','name':'市场','portalId':'path'},
            {'id':'home','name':'返回寓所','portalId':'path'}]
    cues=route_visual_cues(routes,'open-air street or market')
    assert len(set(cues.values()))==3 and route_visual_cues(routes,'interior')=={}
    view={'playerId':'p','characters':[],'layout':{'nodes':[]},'exits':routes,'points':[],
          'descriptor':{'placeId':'street','pointId':'entry','exitRoutes':[{**r,'visualCue':cues[r['id']]} for r in routes]}}
    old=deepcopy(view);old['descriptor']['exitRoutes']=routes
    assert appearance(view)!=appearance(old)
    assert {r['id']:r['visualCue'] for r in inspection_targets(view)['exits']}==cues
    assert all('yaw' not in r for r in inspection_targets(view)['exits'])


def test_many_same_type_destinations_have_unique_render_cues():
    from app.scenes.layout import route_visual_cues
    for name in ('酒馆','山路'):
        routes=[{'id':str(index),'name':name} for index in range(8)]
        assert len(set(route_visual_cues(routes,'open-air street or market').values()))==8


def test_legacy_receipt_never_inherits_new_render_landmarks_from_current_planner(monkeypatch):
    from app.scenes.cache import saved_appearance
    view=scene();view['characters']=[]
    view['descriptor']['exitRoutes']=[{**view['exits'][0],'visualCue':'New stone posts.'}]
    old_descriptor=deepcopy(view['descriptor']);old_descriptor['exitRoutes']=[view['exits'][0]]
    row=SimpleNamespace(descriptor=old_descriptor,checkpoint_id='cp',owner_id='owner',world_id='world',
                        line_id='line',image={})
    checkpoint=SimpleNamespace(owner_id='owner',world_id='world')
    monkeypatch.setattr('app.scenes.cache.object_session',lambda _:SimpleNamespace(get=lambda *_:checkpoint))
    monkeypatch.setattr('app.scenes.service.scene_view',lambda *_:view)
    saved=saved_appearance(row)
    assert 'routeVisualCues' not in saved and saved!=appearance(view)


def scene():
    return {'playerId':'reader','pointId':'entry',
            'descriptor':{'placeId':'room','pointId':'entry','description':'客舍','lighting':'清晨','spaceKind':'interior'},
            'layout':{'removedFixtures':[]},'characters':[{'id':'host','description':'店主'}],
            'characterVisibility':{'host':'visible'},'points':[],
            'exits':[{'id':'garden','name':'庭院','portalId':'door','yaw':0,'pitch':-.2}]}


@pytest.mark.parametrize('text,expected',[
    ('福尔摩斯在沙发上坐着。','seated'),
    ('福尔摩斯站起身，向你点头。','standing'),
    ('福尔摩斯跪在地上查看足迹。','kneeling'),
    ('福尔摩斯说：“我坐下以后发现了线索。”',None),
    ('昨日福尔摩斯坐在沙发上。',None),
    ('如果福尔摩斯坐下，我们就可以讨论。',None),
    ('福尔摩斯没有坐下。',None),
    ('福尔摩斯请你坐下。',None),
    ('福尔摩斯请华生坐下。',None),
    ('福尔摩斯准备坐下。',None),
    ('福尔摩斯坐着，他站起身。','seated'),
    ('福尔摩斯看着你坐下。',None),
    ('福尔摩斯等你站起来。',None),
    ('福尔摩斯看着华生坐下。',None),
    ('福尔摩斯的朋友站着。',None),
    ('福尔摩斯朝你点头，你坐下。',None),
    ('福尔摩斯正要坐下。',None),
    ('福尔摩斯想坐下。',None),
    ('福尔摩斯考虑坐下。',None),
    ('福尔摩斯不急于坐下。',None),
    ('福尔摩斯可能坐下。',None),
    ('福尔摩斯也许坐下。',None),
])
def test_stable_pose_uses_explicit_named_physical_evidence_only(text,expected):
    from app.scenes.composition import named_pose
    assert named_pose({'name':'福尔摩斯'},text,other_names=['福尔摩斯','华生'])==expected


@pytest.mark.parametrize('text,expected',[
    ('Holmes plans to sit down.','standing'),
    ('Holmes might sit down.','standing'),
    ('Holmes may sit down.','standing'),
    ('Holmes is sitting by the fire.','seated'),
    ('Holmes quietly sat down.','seated'),
])
def test_pose_requires_a_direct_observed_subject_not_an_english_intention(text,expected):
    from app.scenes.composition import named_pose
    assert named_pose({'name':'Holmes'},text,'standing')==expected


@pytest.mark.parametrize('text,expected',[
    ('福尔摩斯从沙发上站起身。','standing'),
    ('福尔摩斯从椅子上缓缓站起身。','standing'),
    ('福尔摩斯准备从沙发上站起身。','seated'),
    ('福尔摩斯看着你从椅子上站起身。','seated'),
])
def test_observed_rising_from_a_seat_changes_pose_but_intention_and_observation_do_not(text,expected):
    from app.scenes.composition import named_pose
    assert named_pose({'name':'福尔摩斯'},text,'seated')==expected


@pytest.mark.parametrize('name,expected',[
    ('旧城书店','interior'),
    ('桥边照相馆','interior'),
    ('Village Bookstore','interior'),
    ('旧城书店庭院','open-air courtyard'),
    ('桥边照相馆门外','open-air setting'),
])
def test_known_bookshop_and_photo_studio_are_indoor_but_their_exteriors_are_not(name,expected):
    from app.scenes.layout import space_kind
    assert space_kind({'name':name})==expected


def test_pose_change_invalidates_ready_body_without_changing_empty_environment_key():
    from app.scenes.pipeline import base_key
    first=scene();first['descriptor']['characterPoses']={'host':'standing'}
    first_appearance=appearance(first)
    row=SimpleNamespace(descriptor={**deepcopy(first['descriptor']),'appearance':first_appearance},
                        world_id='world',profile_version=1)
    changed=deepcopy(first);changed['descriptor']['characterPoses']['host']='seated'
    assert not compatible(row,changed)
    assert compatible(row,deepcopy(first))
    other=SimpleNamespace(descriptor={**deepcopy(changed['descriptor']),'appearance':appearance(changed)},
                          world_id='world',profile_version=1)
    assert base_key(row)==base_key(other)


@pytest.mark.parametrize('evidence',['same','wrong-pose','wrong-image','unverified'])
def test_legacy_same_pose_reuses_only_its_actual_verified_body_without_rewriting_receipt(evidence):
    first=scene();legacy=appearance(first)
    first['descriptor']['characterPoses']={'host':'seated'}
    image={'sha256':'a'*64,'bindingImageHash':'a'*64,'anchorStatus':'complete','anchorVersion':VERSION,
           'characterAnchors':{'host':{'verified':evidence!='unverified','source':'image-inspection',
             'bodyEvidence':{'naturalScale':True,'pose':'standing' if evidence=='wrong-pose' else 'seated'}}}}
    if evidence=='wrong-image':image['bindingImageHash']='b'*64
    row=SimpleNamespace(status='ready',descriptor={'appearance':legacy},image=image)
    original=deepcopy(row.descriptor)
    assert compatible(row,first)==(evidence=='same')
    assert row.descriptor==original


@pytest.mark.parametrize('place,expected', [
    ({'name':'贝克街221B','description':'起居室里有壁炉，窗外能看见伦敦街道。'},'interior'),
    ({'name':'221B Baker Street','description':'A sitting room with a fireplace.'},'interior'),
    ({'name':'青山镇客栈'},'interior'),
    ({'name':'乌坦城街道药铺'},'interior'),
    ({'name':'青山镇客栈庭院'},'open-air courtyard'),
    ({'name':'The Blue Anchor tavern courtyard'},'open-air courtyard'),
    ({'name':'图书馆广场'},'open-air courtyard'),
    ({'name':'酒馆门外'},'open-air setting'),
    ({'name':'萧家练武场'},'open-air courtyard'),
    ({'name':'北门','description':'尚未确定的地点'},'unspecified setting'),
])
def test_place_type_uses_current_space_evidence(place,expected):
    from app.scenes.layout import space_kind,build_layout
    assert space_kind(place)==expected
    if expected=='interior':
        layout=build_layout({'id':'place',**place},[],'窗外看见街道和山路。')
        assert not any(p.get('fixture')=='path' for p in layout['nodes'])


def test_current_indoor_corridor_remains_walkable():
    from app.scenes.layout import build_layout
    layout=build_layout({'id':'hall','name':'正厅'},[],'正厅西侧的走廊通向藏书室。')
    assert any(p.get('fixture')=='path' for p in layout['nodes'])


def test_exit_inspection_retains_the_current_doorway_evidence():
    from app.scenes.grounding import inspection_targets
    view={'pointId':'door','points':[{'id':'door','name':'门口','fixture':'entrance','source':{'quote':'临街大门敞开，门外是街道。'}}],
          'exits':[{'id':'street','name':'街道','portalId':'door'}]}
    target=inspection_targets(view)
    assert not target['fixtures']
    assert target['exits'][0]['fixture']=='entrance'
    assert '临街大门' in target['exits'][0]['evidence']


def verified_image():
    return SimpleNamespace(status='ready', image={'sha256':'painted-A','bindingImageHash':'painted-A',
        'anchorVersion':VERSION,'characterAnchors':{'host':{'yaw':.1,'pitch':.2,'verified':True,'source':'image-inspection'}},
        'spatialAnchors':{'exits:garden':{'yaw':1,'pitch':-.2,'verified':True,'source':'image-inspection','kind':'exits'}}})


@pytest.mark.parametrize('change', ['light','route','portal','space','cast'])
def test_visual_changes_invalidate_a_previously_received_picture(change):
    view=scene();row=SimpleNamespace(descriptor={'appearance':appearance(view)})
    assert compatible(row,view)
    changed=deepcopy(view)
    if change=='light':changed['descriptor']['lighting']='夜'
    if change=='route':changed['exits'].append({'id':'street','name':'街道','portalId':'side-door'})
    if change=='portal':changed['exits'][0]['portalId']='other-door'
    if change=='space':changed['descriptor']['spaceKind']='outdoor'
    if change=='cast':changed['characters']=[]
    assert not compatible(row,changed)


def test_same_visual_state_reuses_picture_even_when_time_or_prose_changes():
    view=scene();row=SimpleNamespace(descriptor={'appearance':appearance(view)})
    view.update(timeLabel='清晨第二刻',text='你问候店主。')
    assert compatible(row,view)


@pytest.mark.parametrize('change', ['hash','version','verified','source','boolean-coordinate'])
def test_people_and_routes_share_the_same_pixel_evidence_gate(change):
    row=verified_image();view=scene()
    assert character_presentations(row,view)['host']['mode']=='embedded'
    assert grounded_points(row,view)['exits'][0]['imageBound']
    if change=='hash':row.image['sha256']='another-picture'
    if change=='version':row.image['anchorVersion']=VERSION-1
    for group,ident in [('characterAnchors','host'),('spatialAnchors','exits:garden')]:
        if change=='verified':row.image[group][ident]['verified']=False
        if change=='source':row.image[group][ident]['source']='generation-guidance'
        if change=='boolean-coordinate':row.image[group][ident]['yaw']=True
    assert character_presentations(row,view)['host']['mode']=='offscreen'
    assert not grounded_points(row,view)['exits'][0]['imageBound']


def test_return_label_is_not_a_new_place_or_picture():
    view=scene();row=SimpleNamespace(descriptor={'appearance':appearance(view)})
    view['exits'][0]['name']='返回庭院'
    assert compatible(row,view)


def test_fixture_addition_and_geometry_require_new_picture_but_prose_does_not():
    view=scene();view['layout']['nodes']=[{'id':'bench','fixture':'bench','worldX':1,'worldZ':2,'name':'长椅'}]
    row=SimpleNamespace(descriptor={'appearance':appearance(view)})
    view['layout']['nodes'][0]['name']='你看见的长椅'
    assert compatible(row,view)
    view['layout']['nodes'][0]['worldX']=3
    assert not compatible(row,view)
    view['layout']['nodes']=[]
    assert not compatible(row,view)


def test_same_id_fixture_and_exit_never_share_evidence():
    view=scene();view['points']=[{'id':'garden','name':'道路'}]
    result={kind:[{'id':'garden','visible':True,'passable':True,'confidence':.95,'anchorPixels':[x,650]}] for kind,x in [('fixtures',200),('exits',800)]}
    row=verified_image();row.image['spatialAnchors']=spatial_anchors_from_result(result,{'fixtures':[{'id':'garden'}],'exits':[{'id':'garden'}]},(1000,1000))
    points=grounded_points(row,view)
    assert points['points'][0]['yaw']<0<points['exits'][0]['yaw']


def test_camera_sheet_centre_and_seam_preserve_real_spherical_directions():
    assert sphere_pixel(0,256,256,2048,1024)==pytest.approx((1024,512))
    assert sphere_pixel(1,256,256,2048,1024)==pytest.approx((1536,512))
    assert sphere_pixel(2,256,256,2048,1024)==pytest.approx((2048,512))
    data={'characters':[{'entityId':'host','visible':True,'confidence':.95,'viewIndex':2,'headBoxPixels':[246,242,266,258]}],
          'exits':[{'id':'garden','visible':True,'passable':True,'confidence':.95,'viewIndex':1,'anchorPixels':[256,340]}]}
    converted=panorama_coordinates(data,(2048,1024))
    anchors=anchors_from_result(converted,[{'entityId':'host'}],(2048,1024))
    assert abs(abs(anchors['host']['yaw'])-pi)<.02
    assert converted['exits'][0]['anchorPixels'][0]==pytest.approx(1536)
    assert converted['exits'][0]['anchorPixels'][1]>512
    assert data['characters'][0]['headBoxPixels']==[246,242,266,258]


def test_sheet_is_a_bounded_analysis_image_and_does_not_modify_received_bytes():
    image=Image.new('RGB',(2048,1024),'red')
    image.paste('green',(1024,0,2048,1024))
    source=io.BytesIO();image.save(source,format='JPEG');before=source.getvalue()
    output=contact_sheet(before)
    assert len(output)<=240000 and source.getvalue()==before
    with Image.open(io.BytesIO(output)) as sheet:
        assert sheet.size==(1024,1024)
        assert sheet.getpixel((768,256))[1]>sheet.getpixel((768,256))[0]


@pytest.mark.parametrize('index,pixel', [(True,[256,256]),(8,[256,256]),(0,[800,256]),(0,[float('nan'),256])])
def test_invalid_camera_detections_cannot_authorize_navigation(index,pixel):
    result=panorama_coordinates({'exits':[{'id':'garden','visible':True,'confidence':.95,'viewIndex':index,'anchorPixels':pixel}]},(2048,1024))
    assert result['exits'][0]['visible'] is False
    assert result['exits'][0]['rejectionReason']=='pixel-space-out-of-range'


def test_separate_camera_images_have_local_coordinates_and_independent_direction():
    from app.scenes.inspection import camera_images
    image=Image.new('RGB',(2048,1024),'red');image.paste('green',(1024,0,2048,1024))
    source=io.BytesIO();image.save(source,format='PNG')
    views=camera_images(source.getvalue())
    assert len(views)==4
    with Image.open(io.BytesIO(views[1])) as right,Image.open(io.BytesIO(views[3])) as left:
        assert right.size==left.size==(512,512)
        assert right.getpixel((256,256))[1]>right.getpixel((256,256))[0]
        assert left.getpixel((256,256))[0]>left.getpixel((256,256))[1]
    invalid=panorama_coordinates({'characters':[{'entityId':'holmes','visible':True,'confidence':.95,
        'viewIndex':1,'headBoxPixels':[778,196,826,254]}]},(2048,1024))
    assert invalid['characters'][0]['rejectionReason']=='pixel-space-out-of-range'
    assert not anchors_from_result(invalid,[{'entityId':'holmes'}],(2048,1024))


def grounded_adult(**changes):
    part={'entityId':'host','visible':True,'confidence':.95,'physicalPerson':True,
        'viewIndex':0,'headBoxPixels':[224,170,256,212],
        'bodyViewIndex':0,'bodyBoxPixels':[200,160,280,500],
        'pose':'standing','naturalScale':True,'scaleConfidence':.95,
        'scaleEvidence':{'referenceKind':'door','viewIndex':0,'boxPixels':[300,90,410,500],
                         'depthRelation':'same-depth','description':'Adult standing beside this door; head and shoulders below lintel.'},
        'grounding':'floor','groundConfidence':.95,'groundViewIndex':0,'groundPointPixels':[240,490]}
    return {**part,**changes}


def test_normal_near_adult_requires_body_and_contact_instead_of_tiny_head_threshold():
    part=grounded_adult()
    converted=panorama_coordinates({'characters':[part]},(2048,1024))
    assert not anchors_from_result(converted,[{'entityId':'host'}],(2048,1024))
    anchors=anchors_from_result(converted,[{'entityId':'host'}],(2048,1024),require_grounding=True)
    assert anchors, converted
    assert anchors['host']['bodyEvidence']['pose']=='standing'
    assert anchors['host']['bodyEvidence']['scaleEvidence']['referenceKind']=='door'
    assert part['bodyBoxPixels']==[200,160,280,500]


@pytest.mark.parametrize('changes',[
    {'physicalPerson':False},{'naturalScale':False},{'grounding':'floating'},
    {'scaleConfidence':.79},{'groundConfidence':.79},
    {'bodyBoxPixels':[216,162,265,226]},  # An isolated head is not a body.
    {'groundPointPixels':[450,480]},  # Contact belongs to a different part of the room.
    {'groundPointPixels':[240,130]},  # Contact cannot sit above the person's head.
    {'scaleEvidence':{'referenceKind':'door','viewIndex':0,'boxPixels':[300,90,999,500],
                      'depthRelation':'same-depth','description':'Door'}},
    {'grounding':'fixture','supportingFixtureId':'not-measured'}
])
def test_known_or_unverified_body_failures_cannot_bind_a_character(changes):
    converted=panorama_coordinates({'characters':[grounded_adult(**changes)]},(2048,1024))
    assert not anchors_from_result(converted,[{'entityId':'host'}],(2048,1024),require_grounding=True)


def test_missing_ground_evidence_is_detector_failure_not_permission_to_repaint():
    from app.scenes.binding import body_evidence_issue
    converted=panorama_coordinates({'characters':[grounded_adult()]},(2048,1024))['characters'][0]
    assert body_evidence_issue(converted) is None
    del converted['scaleEvidence']
    assert body_evidence_issue(converted)=='body-evidence-unconfirmed'
    converted['naturalScale']=False
    assert body_evidence_issue(converted) is None


def test_same_depth_giant_or_detached_head_cannot_pass_detector_boolean():
    from app.scenes.binding import body_evidence_issue
    for changes in (
        {'scaleEvidence':{'referenceKind':'door','viewIndex':0,'boxPixels':[300,390,410,500],
                          'depthRelation':'same-depth','description':'Entire door at the same depth.'}},
        {'headBoxPixels':[300,170,332,212]},
    ):
        converted=panorama_coordinates({'characters':[grounded_adult(**changes)]},(2048,1024))
        assert body_evidence_issue(converted['characters'][0])=='body-evidence-contradictory'
        assert not anchors_from_result(converted,[{'entityId':'host'}],(2048,1024),require_grounding=True)


def test_composition_missing_reference_check_or_conflicting_people_count_is_unconfirmed():
    from app.scenes.binding import composition_from_result
    value={'composition':{'projection':'equirectangular','confidence':.95,'visiblePeopleCount':1,
                          'unexpectedPeople':[],'referenceArtifacts':[]},'characters':[grounded_adult()]}
    assert composition_from_result(value,[{'entityId':'host'}])['verified']
    value['composition']['visiblePeopleCount']=0
    assert not composition_from_result(value,[{'entityId':'host'}])['verified']
    value['composition']['visiblePeopleCount']=1;del value['composition']['referenceArtifacts']
    assert not composition_from_result(value,[{'entityId':'host'}])['verified']


def test_cropped_door_cannot_be_used_as_a_complete_same_depth_scale_reference():
    from app.scenes.binding import body_evidence_issue
    part=grounded_adult(scaleEvidence={'referenceKind':'door','viewIndex':0,'boxPixels':[300,0,410,110],
                                       'depthRelation':'same-depth','description':'Door cut off by camera edge.'})
    converted=panorama_coordinates({'characters':[part]},(2048,1024))
    assert body_evidence_issue(converted['characters'][0])=='body-evidence-unconfirmed'
    assert not anchors_from_result(converted,[{'entityId':'host'}],(2048,1024),require_grounding=True)


def test_sofa_support_requires_actual_contact_box_even_for_a_known_fixture_id():
    support={'kind':'sofa','viewIndex':0,'boxPixels':[100,200,400,505],'confidence':.95}
    part=grounded_adult(pose='seated',grounding='fixture',supportingFixture=support)
    converted=panorama_coordinates({'characters':[part]},(2048,1024))
    assert anchors_from_result(converted,[{'entityId':'host'}],(2048,1024),require_grounding=True)
    part['supportingFixtureId']='sofa-known';part.pop('supportingFixture')
    converted=panorama_coordinates({'characters':[part]},(2048,1024))
    assert not anchors_from_result(converted,[{'entityId':'host'}],(2048,1024),require_grounding=True,verified_fixtures=['sofa-known'])
    part['supportingFixture']={**support,'boxPixels':[350,200,500,505]}
    converted=panorama_coordinates({'characters':[part]},(2048,1024))
    assert not anchors_from_result(converted,[{'entityId':'host'}],(2048,1024),require_grounding=True,verified_fixtures=['sofa-known'])


def test_downward_views_expose_floor_and_have_their_own_coordinate_space():
    from app.scenes.inspection import camera_images
    import io
    from PIL import Image
    source=io.BytesIO();Image.new('RGB',(2048,1024),'#123456').save(source,format='PNG')
    assert len(camera_images(source.getvalue(),include_ground=True))==8
    assert sphere_pixel(4,256,256,2048,1024)==pytest.approx((1024,512+1024*35/180))
    converted=panorama_coordinates({'characters':[grounded_adult(groundViewIndex=4,groundPointPixels=[240,390])]},(2048,1024))
    part=converted['characters'][0]
    assert part['groundPointPixels'][1]>512
    invalid=panorama_coordinates({'characters':[grounded_adult(bodyBoxPixels=[200,160,700,500])]},(2048,1024))
    assert invalid['characters'][0]['rejectionReason']=='pixel-space-out-of-range'


def test_giant_faces_and_sky_heads_cannot_be_accepted_as_natural_scene_actors():
    for box in ([.49,.225,.575,.397],[.5,.1,.52,.14]):
        assert not anchors_from_result({'characters':[{'entityId':'host','visible':True,'confidence':.99,'headBox':box}]},[{'entityId':'host'}])
    assert anchors_from_result({'characters':[{'entityId':'host','visible':True,'confidence':.95,'headBox':[.5,.48,.51,.50]}]},[{'entityId':'host'}])
