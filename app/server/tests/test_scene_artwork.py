import base64,json
from app.scenes.artwork import request,apply


def observation():
    return {'projection':'equirectangular','finishedArtwork':True,'unpaintedGuideRegions':[],
        'confidence':.96,'evidence':'Continuous curved ceiling and surrounding shelves wrap around the full panorama; the disputed light rectangles are detailed painted book pages, not unfinished solid guides.'}


def test_focused_artwork_review_preserves_original_findings_and_other_failure_gates():
    parsed={'composition':{'projection':'perspective','finishedArtwork':False,
        'unpaintedGuideRegions':['VIEW 2: white rectangles'],'referenceArtifacts':['a copied face'],
        'openSky':True,'visiblePeopleCount':4},'characters':[{'entityId':'npc'}]}
    result=apply(parsed,{'text':json.dumps(observation())})
    assert result['composition']['projection']=='equirectangular' and result['composition']['finishedArtwork']
    assert result['composition']['referenceArtifacts']==['a copied face'] and result['composition']['openSky']
    assert result['composition']['visiblePeopleCount']==4 and result['characters']==parsed['characters']
    assert result['artworkSecondReview']['originalComposition']==parsed['composition']
    assert parsed['composition']['projection']=='perspective'


def test_missing_weak_or_actual_unfinished_review_never_overrides_failure():
    parsed={'composition':{'projection':'perspective','finishedArtwork':False}}
    for value in ({}, {**observation(),'confidence':.89}, {**observation(),'finishedArtwork':False},
                  {**observation(),'unpaintedGuideRegions':['plain guide wall']},{**observation(),'projection':'uncertain'}):
        result=apply(parsed,{'text':json.dumps(value)})
        assert result['composition']==parsed['composition'] and not result['artworkSecondReview']['accepted']


def test_only_full_panorama_and_explicitly_disputed_actual_views_are_sent_without_prior_verdict():
    payload=request({'composition':{'unpaintedGuideRegions':['Horizontal VIEW 3: pages','VIEW 3 floor']}},b'panorama',[bytes([i]) for i in range(8)])
    images=[base64.b64decode(p.split(',',1)[1]) for p in payload['_sceneImages']]
    assert images==[b'panorama',b'\x03'] and 'composition' not in payload
    assert payload['imageRoles']['laterImages'][0]['cameraView']==3
    assert len(request({'composition':{'unpaintedGuideRegions':None}},b'p',[b'c']*8)['_sceneImages'])==1
