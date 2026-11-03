"""Independent physical-head inventory in separate actual camera images."""
import base64,io
from math import isfinite
from .coordinate_cameras import camera_image
from .inspection import sphere_pixel

def candidate_boxes(observation):
 heads=observation.get('heads',[]) if isinstance(observation,dict) else []
 if not isinstance(heads,list) or len(heads)>40:return []
 return [h['box'] for h in heads if isinstance(h,dict) and isinstance(h.get('box'),list) and len(h['box'])==4
  and all(type(n) in (int,float) and isfinite(n) and 0<=n<=1000 for n in h['box'])
  and h['box'][0]<h['box'][2] and h['box'][1]<h['box'][3]]

def needs_clarification(value):
 if not isinstance(value,dict):return True
 confidence=value.get('confidence');heads=value.get('heads');count=value.get('count')
 return (type(confidence) not in (int,float) or not isfinite(confidence) or not .85<=confidence<=1
  or type(count) is not int or not 0<=count<=40 or not isinstance(heads,list) or len(heads)!=count
  or len(candidate_boxes(value))!=count or not isinstance(value.get('evidence'),str) or len(value['evidence'].strip())<10
  or any(not isinstance(h.get('evidence'),str) or len(h['evidence'].strip())<10 for h in heads))

def request(camera,*,clarify=None,facts=None):
 result={'inspectionMode':'single-camera-physical-head-inventory',
  'imageSize':{'width':1000,'height':1000},
  'instruction':'Count EVERY distinct physical person visible in this ONE actual camera. No desired count, cast or identities '
    'are supplied. List one tight head box per physical person, including partially visible people. Two adjacent or '
    'overlapping heads/bodies are TWO people even when faces, clothing or identity look identical. Do not merge duplicates '
    'into one person. Exclude wall paintings, pasted portrait panels, reflections and shadows. Report uncertainty for '
    'ambiguous physical faces. Coordinates are normalized 0..1000 XY on this image only.',
  'outputContract':{'count':'integer equal to heads length','heads':[{'box':'[left,top,right,bottom] tight actual head',
    'evidence':'actual separate physical head/body, not a requested name'}],'confidence':'0..1','evidence':'actual physical people or visible absence'},
  '_sceneImages':['data:image/jpeg;base64,'+base64.b64encode(camera_image(camera)).decode()],
  '_structured':True,'_visionStream':False}
 if clarify is not None:
  from PIL import Image
  boxes=candidate_boxes(clarify);images=result['_sceneImages']
  with Image.open(io.BytesIO(camera)) as original:
   picture=original.convert('RGB');w,h=picture.size
   for box in boxes[:8]:
    left,top,right,bottom=[n*w/1000 for n in box]
    margin=max(32,2*max(right-left,bottom-top))
    bounds=(max(0,int(left-margin)),max(0,int(top-margin)),min(w,int(right+margin)+1),min(h,int(bottom+margin)+1))
    output=io.BytesIO();picture.crop(bounds).save(output,format='JPEG',quality=92,subsampling=0)
    images.append('data:image/jpeg;base64,'+base64.b64encode(camera_image(output.getvalue())).decode())
  result.update(inspectionMode='same-camera-person-detail',candidateRegions=boxes,
   detailRoles='Image 1 is the ONLY full original camera. Later images enlarge only candidate regions of that SAME camera with surrounding geometry. They are not additional people or cameras.')
  result['instruction']+=' Candidate rectangles are hypotheses, not known people. Use full image and enlarged actual pixels to distinguish a physical head/body from window frames, columns, lamps, shadows and wall art. Return ALL physical heads in the full camera, not only candidates. ALL output boxes refer to original Image 1 normalized 0..1000 XY, never enlarged crops. Do not infer hidden people or report an uncertain shape as confidently human. No previous count, verdict or expected cast is provided.'
 requirements=camera_facts(facts)
 if requirements:
  result['physicalFacts']=[{k:p[k] for k in ('id','kind','name','instruction') if k in p} for p in requirements]
  result['outputContract']['visualFactChecks']=[{'id':'each exact physicalFacts id','consistent':'boolean',
   'confidence':'number 0..1','evidence':'actual visible pixels in this ONE camera, at least 15 characters'}]
  result['instruction']+=' Independently check every physicalFacts requirement in this actual camera. '
  result['instruction']+='A forbidden live animal or actual handed-away item anywhere in this camera contradicts its requirement. Absence here is consistent; do not infer other directions. Inspect actual physical features and support, never classify an item from a shared colour alone. '
 return result


def camera_facts(facts):
 if not isinstance(facts,dict) or facts.get('cameraInspectionVersion')!=1:return []
 return [p for p in facts.get('requirements',[]) if p.get('kind') in ('forbidden-live-animal','absent-transferred-item')]


def evaluate_facts(proof,facts,image_hash):
 requirements=camera_facts(facts)
 if not requirements:return []
 if (not isinstance(proof,dict) or proof.get('version')!=1 or proof.get('imageHash')!=image_hash
  or not isinstance(proof.get('views'),list) or len(proof['views'])!=4
  or [v.get('viewIndex') for v in proof['views'] if isinstance(v,dict)]!=list(range(4))):return ['visual-facts-unconfirmed']
 from .facts import evaluate as check_facts
 issues=[]
 for view in proof['views']:
  observation=view.get('observation')
  if not isinstance(observation,dict):return ['visual-facts-unconfirmed']
  issues.extend(check_facts(observation,{'requirements':requirements}))
 return list(dict.fromkeys(issues))

def observed_view(view):
 """A detail replaces its own hypotheses only with the same camera binding."""
 value=view.get('observation')
 clarification=view.get('clarification')
 forced=isinstance(clarification,dict) and clarification.get('reason')=='independent-scene-count-conflict-v1'
 if not needs_clarification(value) and not forced:return value
 if (not isinstance(clarification,dict) or clarification.get('source')!='same-camera-person-detail-v1'
     or clarification.get('viewIndex')!=view.get('viewIndex')
     or clarification.get('candidateRegions')!=candidate_boxes(value)):return None
 detail=clarification.get('observation')
 return None if needs_clarification(detail) else detail


def evaluate(proof,image_hash):
 invalid={'verified':False,'count':None}
 if not isinstance(proof,dict) or proof.get('version')!=1 or proof.get('imageHash')!=image_hash:return invalid
 views=proof.get('views')
 if not isinstance(views,list) or len(views)!=4 or [v.get('viewIndex') for v in views if isinstance(v,dict)]!=list(range(4)):return invalid
 boxes=[]
 for view in views:
  value=observed_view(view);index=view['viewIndex']
  if value is None:return invalid
  confidence=value.get('confidence');count=value.get('count');heads=value.get('heads')
  if (type(confidence) not in (int,float) or not isfinite(confidence) or not .85<=confidence<=1
      or type(count) is not int or not 0<=count<=40 or not isinstance(heads,list) or len(heads)!=count
      or not isinstance(value.get('evidence'),str) or len(value['evidence'].strip())<10):return invalid
  same_view=[];seen_boxes=[];used_clusters=set()
  for head in heads:
   if not isinstance(head,dict):return invalid
   box=head.get('box')
   if (not isinstance(box,list) or len(box)!=4 or any(type(n) not in (int,float) or not isfinite(n) or not 0<=n<=1000 for n in box)
       or not box[0]<box[2] or not box[1]<box[3]
       or not isinstance(head.get('evidence'),str) or len(head['evidence'].strip())<10):return invalid
   corners=[sphere_pixel(index,x*512/1000,y*512/1000,2048,1024)
            for x in (box[0],box[2]) for y in (box[1],box[3])]
   candidate=[min(p[0] for p in corners),min(p[1] for p in corners),max(p[0] for p in corners),max(p[1] for p in corners)]
   if candidate in seen_boxes:return invalid
   seen_boxes.append(candidate)
   best=None;best_overlap=0
   for cluster,(other,source) in enumerate(boxes):
    if cluster in used_clusters:continue
    for offset in (-2048,0,2048):
     overlap=max(0,min(candidate[2],other[2]+offset)-max(candidate[0],other[0]+offset))*max(0,min(candidate[3],other[3])-max(candidate[1],other[1]))
     area=min((candidate[2]-candidate[0])*(candidate[3]-candidate[1]),(other[2]-other[0])*(other[3]-other[1]))
     if area and overlap/area>=.5 and overlap/area>best_overlap:
      best=cluster;best_overlap=overlap/area
   if best is None:same_view.append((candidate,index))
   else:used_clusters.add(best)
  # Never merge two distinct heads from the SAME camera, even when they overlap.
  boxes.extend(same_view)
 return {'verified':True,'count':len(boxes)}
