"""One focused, independently journalled review before destructive correction."""
import base64,json,re
from copy import deepcopy
from math import isfinite


def request(parsed,panorama,cameras):
    observed=parsed.get('composition',{})
    observed=observed if isinstance(observed,dict) else {}
    regions=observed.get('unpaintedGuideRegions',[])
    regions=regions if isinstance(regions,list) else []
    views=list(dict.fromkeys(int(p) for text in regions if isinstance(text,str)
        for p in re.findall(r'\bVIEW\s*([0-7])\b',text,re.I)))
    images=[panorama,*[cameras[i] for i in views]]
    return {'inspectionMode':'artwork-only','imageRoles':{'image1':'ACTUAL full 2:1 panorama; judge projection ONLY from this image',
        'laterImages':[{'image':i+2,'cameraView':view,'role':'ACTUAL rectilinear material detail, NOT the full panorama'} for i,view in enumerate(views)]},
        'instruction':'Inspect actual artwork without any people or route measurements. For projection inspect ONLY Image 1: a 2:1 ratio alone is insufficient; verify wrapping architecture, ceiling/sky above, floor below and all-around view. Later images are intentionally perspective cameras and MUST NOT change the projection judgement. For finished materials distinguish real blank architectural blockout geometry from naturally light book pages, paving, coherent smooth plaster, shadows and finished timber. Do not assume every light rectangle is a placeholder. Report actual unfinished surfaces if present. No suggested verdict or prior measurement is supplied.',
        'requiredOutput':['projection','finishedArtwork','unpaintedGuideRegions','confidence','evidence'],
        '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(p).decode() for p in images],
        '_structured':True,'_visionStream':True}


def apply(parsed,result):
    original=deepcopy(parsed)
    try:value=json.loads(result.get('text',''))
    except (ValueError,TypeError):value={}
    confidence=value.get('confidence') if isinstance(value,dict) else None
    accepted=(isinstance(value,dict) and value.get('projection')=='equirectangular'
        and value.get('finishedArtwork') is True and value.get('unpaintedGuideRegions')==[]
        and type(confidence) in (int,float) and isfinite(confidence) and .9<=confidence<=1
        and isinstance(value.get('evidence'),str) and len(value['evidence'].strip())>=30)
    original['artworkSecondReview']={'accepted':accepted,'originalComposition':deepcopy(parsed.get('composition',{})),
        'independentObservation':deepcopy(value)}
    if accepted:
        original['composition']={**original.get('composition',{}),'projection':value['projection'],
            'finishedArtwork':True,'unpaintedGuideRegions':[]}
    return original
