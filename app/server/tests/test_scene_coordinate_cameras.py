import base64,io,json
from PIL import Image
from app.scenes.coordinate_cameras import prepare,wire_protocol
from app.scenes.coordinates import request,response,SYSTEM
from app.scenes.apertures import request as aperture_request


def picture(colour='blue',size=(512,512)):
    out=io.BytesIO();Image.new('RGB',size,colour).save(out,format='JPEG');return out.getvalue()


def decode(uri):return Image.open(io.BytesIO(base64.b64decode(uri.split(',',1)[1])))


def test_actual_camera_is_1000_pixels_while_decoder_still_maps_to_original_rays():
    original={'inspectionMode':'person-head','camera':{'viewIndex':0,'width':512,'height':512},
        '_sceneImages':['the old analysis overlay'],'measurement':'old grid'}
    prepared=prepare(original,[picture()]*8)
    assert prepared['camera']['width']==1000 and decode(prepared['_sceneImages'][0]).size==(1000,1000)
    assert decode(prepared['_sceneImages'][0]).getpixel((500,500))[2]>245
    decoder=request(prepared);wire=wire_protocol(decoder)
    assert 'frameWidth' not in wire['coordinateProtocol'] and decoder['coordinateProtocol']['frameWidth']==512
    raw={'text':json.dumps({'coordinateSystem':SYSTEM,'headBoxPixels':[100,200,300,400]})}
    result=json.loads(response(raw,decoder)['text'])
    assert result['headBoxPixels']==[51.2,102.4,153.6,204.8]
    assert original['camera']['width']==512 and original['_sceneImages']==['the old analysis overlay']


def test_panorama_and_identity_references_keep_their_exact_roles_and_order():
    original={'cameraViews':{'count':8,'width':512,'height':512},
        '_sceneImages':['panorama',*['old-camera']*8,'owned-reference-a','owned-reference-b']}
    value=prepare(original,[picture()]*8)
    assert len(value['_sceneImages'])==11 and value['_sceneImages'][0]=='panorama'
    assert value['_sceneImages'][9:]==['owned-reference-a','owned-reference-b']
    assert value['cameraViews']['width']==1000 and original['cameraViews']['width']==512
    assert all(decode(p).size==(1000,1000) for p in value['_sceneImages'][1:9])


def test_head_crop_stays_at_its_actual_aspect_and_native_opening_receives_one_1000_camera():
    uri='data:image/jpeg;base64,'+base64.b64encode(picture(size=(120,240))).decode()
    value={'inspectionMode':'head-crop','_sceneImages':[uri],'imageSize':{'width':120,'height':240}}
    assert prepare(value,[picture()]*8)==value
    aperture=aperture_request([{'id':'door'}],picture(),native_size=1000)
    assert len(aperture['_sceneImages'])==1 and decode(aperture['_sceneImages'][0]).size==(1000,1000)


def test_correction_proposal_and_reference_head_use_the_actual_transmitted_camera_scale():
    original={'inspectionMode':'person-body','camera':{'viewIndex':0},'_sceneImages':['overlay'],
        'verifiedHead':{'viewIndex':0,'headBoxPixels':[64,32,128,96]},
        'rejectedProposal':[{'bodyBoxPixels':[128,128,256,384],'groundPointPixels':[200,380],'confidence':.95}]}
    value=prepare(original,[picture()]*8)
    assert value['verifiedHead']['headBoxPixels']==[125,62.5,250,187.5]
    assert value['rejectedProposal'][0]['bodyBoxPixels']==[250,250,500,750]
    assert value['rejectedProposal'][0]['confidence']==.95 and original['verifiedHead']['headBoxPixels']==[64,32,128,96]


def test_downward_actor_measurement_uses_its_actual_pitch_and_transmitted_scale():
    original={'inspectionMode':'actor-placement','cameraContextVersion':1,
        'camera':{'viewIndex':5,'pitchDegrees':-35},'_sceneImages':['overlay']}
    value=prepare(original,[picture()]*8)
    assert 'pitched -35 degrees' in value['measurement']
    assert 'y=206.' in value['measurement']
    assert '1000x1000' in value['measurement'] and 'x/y=51..949' in value['measurement']
    assert 'shoe contact on the seat cushion' in value['poseAndFloorContract']
    horizontal=prepare({**original,'camera':{'viewIndex':1,'pitchDegrees':0}},[picture()]*8)
    assert 'y=500.0' in horizontal['measurement']


import pytest

@pytest.mark.parametrize('index',[0,1,2,3,4,5,6,7])
def test_original_panorama_detail_keeps_the_same_logical_rays_at_native_resolution(index):
    from app.scenes.inspection import detail_camera,sphere_pixel
    width,height=3072,1536
    source=Image.new('RGB',(width,height));pixels=source.load()
    # Encode the source longitude and latitude independently in two channels.
    for y in range(height):
        for x in range(width):pixels[x,y]=(round(x*255/(width-1)),round(y*255/(height-1)),90)
    out=io.BytesIO();source.save(out,format='PNG')
    actual=Image.open(io.BytesIO(detail_camera(out.getvalue(),index)))
    assert actual.size==(1024,1024)
    for x,y in [(220,710),(630,420)]:
        sx,sy=sphere_pixel(index,x/2,y/2,width,height)
        expected=((sx%width)*255/(width-1),sy*255/(height-1),90)
        assert all(abs(a-b)<4 for a,b in zip(actual.getpixel((x,y)),expected))


@pytest.mark.parametrize('index',[-1,8,True,None])
def test_native_detail_refuses_unknown_camera_indices(index):
    from app.scenes.inspection import detail_camera
    with pytest.raises(ValueError):detail_camera(picture(),index)


def test_native_markers_keep_exact_logical_point_centres_in_the_whole_camera():
    from app.scenes.coordinate_cameras import marked_camera
    source=picture('#bab8ac',(1024,1024))
    data=marked_camera(source,[('A',[230,370],'#00c9ef')])
    actual=Image.open(io.BytesIO(data))
    assert actual.size==(1000,1000) and len(data)<=240000
    assert all(abs(a-b)<8 for a,b in zip(actual.getpixel((round(230*1000/512),round(370*1000/512))),(186,184,172)))
    # The ring must be at the scaled hypothesis; the centre stays source pixels.
    x,y=round(230*1000/512),round(370*1000/512)
    assert any(actual.getpixel((x+dx,y))[1]-actual.getpixel((x+dx,y))[0]>35 for dx in range(8,16))


@pytest.mark.parametrize('standing,posed',[(140,100),(180,140)])
def test_actor_readability_constraint_uses_one_wire_scale_without_lowering_the_actual_pixel_threshold(standing,posed):
    original={'inspectionMode':'actor-placement','cameraContextVersion':1,
        'camera':{'viewIndex':0,'pitchDegrees':0},'_sceneImages':['overlay'],
        'futureCast':[{'entityId':'npc'}],
        'readableBodyHeightPixels':{'standing':standing,'posed':posed},
        'readableCharacterContract':f'standing {standing} pixels in actual 512px camera'}
    decoder=request(prepare(original,[picture()]*8));wire=wire_protocol(decoder)
    contract=wire['readableCharacterContract']
    assert '512px' not in contract and 'normalized 0..1000' in contract
    assert f'{standing*1000/512:.10g}' in contract and f'{posed*1000/512:.10g}' in contract
    assert 'readableBodyHeightPixels' not in wire
    assert decode(wire['_sceneImages'][0]).size==(1000,1000)
    raw={'text':json.dumps({'coordinateSystem':SYSTEM,'bodyBoxPixels':[200,100,400,100+standing*1000/512]})}
    measured=json.loads(response(raw,decoder)['text'])['bodyBoxPixels']
    assert measured[3]-measured[1]==standing
    assert original['readableBodyHeightPixels']=={'standing':standing,'posed':posed}


def route_wire():
    from app.scenes.passages import camera_review_requests
    target={'id':'door','independentReviewRequired':True,'allowedPassageKinds':['door-open'],'requiredGroundEvidence':True}
    original,_=next(camera_review_requests([target],[picture()]*8))
    return original,request(original)


def test_actual_route_request_has_one_output_scale_and_retains_the_true_camera_dimensions():
    from copy import deepcopy
    original,decoder=route_wire();before=deepcopy(original);wire=wire_protocol(decoder)
    assert decode(wire['_sceneImages'][0]).size==(512,512)
    assert wire['coordinateProtocol']['inputCameraSize']==512 and 'frameWidth' not in wire['coordinateProtocol']
    assert '0..1000' in wire['coordinateContract'] and '0..512' not in wire['coordinateContract']
    assert '58.59375 normalized units' in wire['anchorContract']
    assert '9.765625 normalized units' in wire['openingContract']
    assert '117.1875 normalized units' in wire['openingContract']
    assert '5px' not in wire['responseShape']['routes'][0]['throughPointPixels']
    assert original==before


def test_normalized_route_receipt_restores_exact_original_geometry_and_physical_distances():
    _,decoder=route_wire()
    measured={'thresholdBoxPixels':[100,250,250,390],'apertureBoxPixels':[100,50,250,390],
        'anchorPixels':[160,390],'beyondPointPixels':[160,380],'throughPointPixels':[160,370]}
    normalized={key:[n*1000/512 for n in values] for key,values in measured.items()}
    raw={'text':json.dumps({'coordinateSystem':SYSTEM,'routes':[{'id':'door',**normalized}]})}
    result=json.loads(response(raw,decoder)['text'])['routes'][0]
    assert {k:result[k] for k in measured}==measured
    assert result['anchorPixels'][1]-result['beyondPointPixels'][1]==10
    assert result['beyondPointPixels'][1]-result['throughPointPixels'][1]==10


def test_unresized_legacy_head_camera_still_requests_normalized_output_without_a_pixel_range_conflict():
    original={'inspectionMode':'person-head','camera':{'width':512,'height':512},
        'outputContract':{'visible':'boolean','headBoxPixels':'tight head, within 0..512'}}
    value=request(original)
    assert 'normalized 0..1000' in value['outputContract']['headBoxPixels']
    assert '0..512' not in value['outputContract']['headBoxPixels']
    measured=json.loads(response({'text':json.dumps({'coordinateSystem':SYSTEM,'headBoxPixels':[200,200,400,400]})},value)['text'])
    assert measured['headBoxPixels']==[102.4,102.4,204.8,204.8]
    assert original['outputContract']['headBoxPixels']=='tight head, within 0..512'
