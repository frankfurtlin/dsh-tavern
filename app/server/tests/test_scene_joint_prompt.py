"""The isolated comparison keeps current state and ordinary prompt contracts."""
from copy import deepcopy
from app.scenes.provider import prompt_for
from app.scenes.layout import space_kind
import pytest


def descriptor():
    return {'jointCompositionVersion':2,'jointSceneReferenceVersion':1,
        'description':'唐三和小舞站在林道两侧，双手空着，尚未完成试炼。',
        'sceneCast':[{'name':'唐三','expectedPose':'standing','appearance':'蓝色便装'},
                     {'name':'小舞','expectedPose':'standing','appearance':'粉色便装和长蝎子辫'}],
        'characterReferenceOrder':[{'name':'唐三','image':2},{'name':'小舞','image':3}],
        'exitRoutes':[{'id':'camp-return','name':'营地'}]}


def test_current_pose_and_each_appearance_survive_without_mutating_state():
    value=descriptor();before=deepcopy(value)
    prompt=prompt_for(value)
    assert 'EXACTLY 2 physical people' in prompt
    assert 'ALL are STANDING' in prompt and 'hands EMPTY' in prompt
    assert '蓝色便装' in prompt and '粉色便装和长蝎子辫' in prompt
    assert '尚未完成试炼' in prompt and 'camp-return' in prompt
    assert value==before


def test_correction_requires_removing_failed_duplicates_not_preserving_them():
    value=descriptor();value['visualRepairAttempt']=1
    assert 'remove every duplicated person' in prompt_for(value)


def test_current_physical_facts_still_surround_joint_composition():
    value=descriptor();value['visualFacts']={'requirements':[{'kind':'absent-item','name':'铜制信标',
        'instruction':'Do not depict it here.'}]}
    prompt=prompt_for(value)
    assert prompt.count('Do not depict it here.')==2
    assert 'EXACTLY 2 physical people' in prompt


@pytest.mark.parametrize('name',['星斗外围营地','外围林道','林间浅溪','浅溪边的营地'])
def test_named_outdoor_place_does_not_become_indoor_from_explicit_negation(name):
    assert space_kind({'name':name,'description':'没有建筑或室内空间，只有树木和可通行的地面。'})=='outdoor natural landscape'


def test_a_room_inside_camp_retains_the_last_actual_location_noun():
    assert space_kind({'name':'营地大堂','description':'远处是森林。'})=='interior'


def test_private_guide_does_not_become_artwork_and_prompt_excludes_offscreen_history():
    import io,hashlib
    from PIL import Image
    from app.scenes.joint import scale_reference
    value=descriptor()
    for index,person in enumerate(value['sceneCast']):person['entityId']=str(index)
    value.update(jointCompositionVersion=3,description='赵无极留守营地，玩家拥有铜制信标。',
        jointScaleProposals={'0':[.4,.49,.42,.61],'1':[.6,.49,.62,.61]},
        jointCastVisuals={'0':'blue boy','1':'pink girl with braid'})
    output=io.BytesIO();Image.new('RGB',(200,100),'#9bba97').save(output,format='PNG')
    source=output.getvalue();digest=hashlib.sha256(source).hexdigest()
    guide=scale_reference(source,value)
    assert guide!=source and hashlib.sha256(source).hexdigest()==digest
    assert Image.open(io.BytesIO(guide)).size==(200,100)
    assert Image.open(io.BytesIO(guide)).getpixel((5,5))==(155,186,151)
    prompt=prompt_for(value)
    assert 'SMALL neutral anatomy sketches' in prompt
    assert '赵无极' not in prompt and '铜制信标' not in prompt
    assert 'blue boy' in prompt and 'pink girl with braid' in prompt
