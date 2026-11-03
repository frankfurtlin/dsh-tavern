import json
from app.scenes.coordinates import request,response,SYSTEM


def test_tagged_normalized_xy_uses_actual_nonsquare_crop_without_transpose_or_mutation():
    original={'inspectionMode':'head-crop','imageSize':{'width':64,'height':96}}
    data=request(original);raw={'text':json.dumps({'coordinateSystem':SYSTEM,'headBoxPixels':[250,100,750,900],'confidence':.95})}
    converted=json.loads(response(raw,data)['text'])
    assert converted['headBoxPixels']==[16,9.6,48,86.4] and converted['confidence']==.95
    assert converted['coordinateSystem']=='pixels-xy-v1'
    assert 'coordinateProtocol' not in original and json.loads(raw['text'])['headBoxPixels']==[250,100,750,900]


def test_missing_wrong_units_and_out_of_range_are_not_guessed_clamped_or_accepted():
    data=request({})
    for tag,point in [(None,[595,510]),('pixels-1024',[595,510]),(SYSTEM,[1001,500]),(SYSTEM,[True,500])]:
        result=json.loads(response({'text':json.dumps({'coordinateSystem':tag,'anchorPixels':point})},data)['text'])
        assert result['anchorPixels'] is None


def test_nested_route_and_scale_boxes_convert_but_ids_counts_and_confidence_do_not():
    data=request({});raw={'coordinateSystem':SYSTEM,'visiblePeopleCount':2,'routes':[{'id':'door','viewIndex':3,
        'confidence':.95,'anchorPixels':[600,750],'scaleEvidence':{'boxPixels':[100,200,400,800]}}]}
    result=json.loads(response({'text':json.dumps(raw)},data)['text'])
    assert result['visiblePeopleCount']==2 and result['routes'][0]['viewIndex']==3
    assert result['routes'][0]['anchorPixels']==[307.2,384]
    assert result['routes'][0]['scaleEvidence']['boxPixels']==[51.2,102.4,204.8,409.6]


def test_focused_placement_request_does_not_mix_pixel_constraints_with_normalized_output():
    original={'inspectionMode':'actor-placement','futureCast':[{'entityId':'npc'}],'camera':{'viewIndex':0},'measurement':'body inside 26..486 pixels',
        'poseAndFloorContract':'horizon 256','outputContract':{}}
    data=request(original)
    assert '51..949' in data['measurement'] and 'y=500' in data['poseAndFloorContract']
    assert '26..486' not in data['measurement'] and '256' not in data['poseAndFloorContract']
    assert data['outputContract']['coordinateSystem']==SYSTEM
    assert original['measurement']=='body inside 26..486 pixels'


def test_explicit_crop_pixel_protocol_does_not_scale_a_real_head_box_twice():
    from app.scenes.coordinates import PIXELS
    data=request({'inspectionMode':'head-crop','imageSize':{'width':304,'height':320}},crop_pixels=True)
    raw={'text':json.dumps({'coordinateSystem':PIXELS,'headBoxPixels':[78,64,214,258],'confidence':.92})}
    value=json.loads(response(raw,data)['text'])
    assert value['headBoxPixels']==[78,64,214,258] and value['confidence']==.92
    assert value['coordinateEvidence']['validatedUnits'] and not value['coordinateEvidence']['converted']
    assert 'actual crop pixels' in data['outputContract']['headBoxPixels']
    for tag,box in [(SYSTEM,[78,64,214,258]),(PIXELS,[78,64,350,258])]:
        rejected=json.loads(response({'text':json.dumps({'coordinateSystem':tag,'headBoxPixels':box})},data)['text'])
        assert rejected['headBoxPixels'] is None
