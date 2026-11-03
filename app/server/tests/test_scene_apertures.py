import pytest
from app.scenes.apertures import candidates,SYSTEM,request
from app.scenes.surfaces import apply_surface
from app.scenes.grounding import spatial_anchors_from_result
from app.scenes.inspection import panorama_coordinates

TARGET={'id':'yard','allowedPassageKinds':['door-open'],'requiredSurfaceReview':True,
    'requiredGroundEvidence':True,'independentReviewRequired':True,'requiredReviewSource':'single-camera-measurement'}


def proposal():
    return candidates({'coordinateSystem':SYSTEM,'routes':[{'id':'yard','matched':True,'kind':'door-open',
        'box_2d':[200,300,800,700],'confidence':.95,'evidence':'An actual large open wooden door connects the room to a paved courtyard.'}]},[TARGET],0)['exits'][0]


def test_native_yx_box_only_proposes_points_and_cannot_publish_without_blind_floor_proof():
    part=proposal()
    assert part['passageEvidence']['apertureBoxPixels']==[153.6,102.4,358.4,409.6]
    assert not part['passable'] and not part['passageEvidence']['independentReview']['matched']
    parsed=panorama_coordinates({'exits':[part]},(2048,1024))
    assert not spatial_anchors_from_result(parsed,{'fixtures':[],'exits':[TARGET]},(2048,1024),require_evidence=True)
    rejected=apply_surface(part,{'sameOpening':False,'crossedThreshold':False},'a'*64)
    assert not rejected['passable'] and not rejected['passageEvidence']['independentReview']['matched']


def test_actual_three_point_verdict_grants_contact_without_fabricating_earlier_floor_evidence():
    part=proposal();result={key:True for key in ('sameOpening','crossedThreshold','openingConfirmed','unobstructed')}
    result['confidence']=.96
    for label in ('A','B','C'):result[label]={'surface':'walkable-ground','confidence':.96,'evidence':'The actual marked centre is on clearly visible unobstructed paving.'}
    confirmed=apply_surface(part,result,'a'*64)
    assert confirmed['passable'] and confirmed['passageEvidence']['independentReview']['continuous']
    assert confirmed['passageEvidence']['groundEvidence']['nearSurface']==result['A']['surface']
    assert 'groundEvidence' not in part['passageEvidence']
    parsed=panorama_coordinates({'exits':[confirmed]},(2048,1024))
    assert 'exits:yard' in spatial_anchors_from_result(parsed,{'fixtures':[],'exits':[TARGET]},(2048,1024),require_evidence=True)


def test_unlabelled_or_foreign_native_box_never_authorizes_a_passage():
    for value in ({'routes':[]},{'coordinateSystem':SYSTEM,'routes':[{'id':'foreign','matched':True}]}):
        assert candidates(value,[TARGET],0)=={'exits':[]}


def test_explicitly_tagged_json_array_string_is_syntax_only_and_still_needs_blind_proof():
    part=candidates({'coordinateSystem':SYSTEM,'routes':[{'id':'yard','matched':True,'kind':'door-open',
        'box_2d':'[200,300,800,700]','confidence':.95,'evidence':'The complete actual open door frame reaches the visible paved threshold.'}]},[TARGET],0)['exits'][0]
    assert not part['passable'] and part['requiresBlindApertureCheck']
    assert part['passageEvidence']['apertureBoxPixels']==[153.6,102.4,358.4,409.6]


def measured_answer():
    return {'coordinateSystem':SYSTEM,'routes':[{'id':'yard','matched':True,'kind':'door-open',
        'box_2d':[200,300,800,700],'confidence':.96,'evidence':'Actual visible door frame above the floor threshold.',
        'walkPoints_yx':{'near':[860,500],'beyond':[720,500],'through':[650,500]}}]}


def test_measured_yx_floor_points_replace_heuristic_sill_points_but_remain_unverified():
    part=candidates(measured_answer(),[TARGET],0,measure_points=True)['exits'][0]
    assert part['anchorPixels']==[256,440.32]
    assert part['passageEvidence']['independentReview']['beyondPointPixels']==[256,368.64]
    assert part['passageEvidence']['throughPointPixels']==[256,332.8]
    assert part['requiresBlindApertureCheck'] and not part['passable']
    rejected=apply_surface(part,{'A':{'surface':'wall','confidence':.99},'confidence':.99,
        'sameOpening':True,'unobstructed':True,'openingConfirmed':True,'crossedThreshold':True},'a'*64)
    assert not rejected['passable']


@pytest.mark.parametrize('change',['reversed','outside','boolean','nan','missing','extra'])
def test_bad_explicit_floor_points_cannot_fall_back_to_guessed_usable_coordinates(change):
    answer=measured_answer();points=answer['routes'][0]['walkPoints_yx']
    if change=='reversed':points['through']=[900,500]
    elif change=='outside':points['near']=[1100,500]
    elif change=='boolean':points['near'][0]=True
    elif change=='nan':points['near'][0]=float('nan')
    elif change=='missing':points.pop('through')
    else:points['unapproved']=[1,2]
    assert not candidates(answer,[TARGET],0,measure_points=True)['exits']


def test_native_reinspection_keeps_actual_failed_pixel_findings_and_explicit_yx_schema():
    notes=[{'viewIndex':0,'nearPixels':[256,417.6],'actualPointObservations':{'A':{'surface':'wall'}}}]
    value=request([TARGET],b'actual-image',measure_points=True,rejected_points=notes)
    assert value['rejectedSameImagePointMeasurements']==notes
    assert 'walkPoints_yx' in value['outputSchema']['properties']['routes']['items']['required']
    assert '[y,x]' in value['instruction'] and 'vertical riser' in value['correctionInstruction']
    value['rejectedSameImagePointMeasurements'][0]['nearPixels'][0]=0
    assert notes[0]['nearPixels'][0]==256


def test_near_point_on_threshold_is_recorded_for_correction_without_claiming_a_surface_verdict():
    answer=measured_answer();answer['routes'][0]['walkPoints_yx']['near']=[810,500]
    result=candidates(answer,[TARGET],0,measure_points=True)
    assert not result['exits']
    rejected=result['rejectedPointProposals'][0]
    assert rejected['id']=='yard' and 'geometric rejection' in rejected['proposalRejection']
    assert 'actualPointObservations' not in rejected


@pytest.mark.parametrize('change',['reversed','threshold','absent'])
def test_box_hypothesis_after_invalid_point_geometry_still_requires_independent_crossing(change):
    answer=measured_answer();points=answer['routes'][0]['walkPoints_yx']
    if change=='reversed':points['through']=[900,500]
    elif change=='threshold':points['near']=[810,500]
    else:answer['routes'][0]['walkPoints_yx']=None
    result=candidates(answer,[TARGET],0,measure_points=True,fallback_on_geometry=True)
    part=result['exits'][0]
    assert part['anchorPixels']==[256,425.6]
    assert part['passageEvidence']['pointProposal']['source']=='measured-opening-box-hypothesis-v1'
    assert not part['passable'] and part['requiresBlindApertureCheck']
    parsed=panorama_coordinates({'exits':[part]},(2048,1024))
    assert not spatial_anchors_from_result(parsed,{'fixtures':[],'exits':[TARGET]},(2048,1024),require_evidence=True)
    if change!='absent':
        assert result['rejectedPointProposals'] and 'walkPoints_yx' not in part['passageEvidence']['pointProposal']
    observed={key:True for key in ('sameOpening','crossedThreshold','openingConfirmed','unobstructed')}
    observed.update(confidence=.96,**{label:{'surface':'walkable-ground','confidence':.96,
        'evidence':'Actual original marked pixels lie on continuous unobstructed walking floor.'} for label in ('A','B','C')})
    good=apply_surface(part,observed,'a'*64,single_camera=True)
    assert good['passable']
    for label in ('A','B','C'):
        from copy import deepcopy
        for invalid in ({'surface':'furniture'}, {'confidence':.84}):
            bad=deepcopy(observed);bad[label].update(invalid)
            assert not apply_surface(part,bad,'a'*64,single_camera=True)['passable']
    for gate in ('sameOpening','crossedThreshold','openingConfirmed','unobstructed'):
        assert not apply_surface(part,{**observed,gate:False},'a'*64,single_camera=True)['passable']


@pytest.mark.parametrize('change',['outside','boolean','nan','missing','extra','cropped-threshold'])
def test_new_box_hypothesis_never_repairs_invalid_units_syntax_or_hidden_approach(change):
    answer=measured_answer();points=answer['routes'][0]['walkPoints_yx']
    if change=='outside':points['near']=[1100,500]
    elif change=='boolean':points['near'][0]=True
    elif change=='nan':points['near'][0]=float('nan')
    elif change=='missing':points.pop('through')
    elif change=='extra':points['unapproved']=[1,2]
    else:answer['routes'][0]['box_2d']=[100,300,999,700]
    assert not candidates(answer,[TARGET],0,measure_points=True,fallback_on_geometry=True)['exits']
