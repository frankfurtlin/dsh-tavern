"""Authenticated read-only previews of globally reviewed pipeline pixels."""
import io
from PIL import Image
from .models import SceneImage

GLOBAL_ISSUES={'unexpected-people','space-mismatch','lighting-mismatch','projection-mismatch',
    'people-count-unconfirmed','scene-review-unconfirmed','reference-artifacts',
    'visual-fact-mismatch','visual-facts-unconfirmed','unfinished-artwork','artwork-unconfirmed'}


def contradicted(row):
    return bool(GLOBAL_ISSUES.intersection(row.image.get('composition',{}).get('issues',[])))


def stages(db,parent):
    from .pipeline import is_parent,owned_stage
    if not is_parent(parent):return
    row=owned_stage(db,parent,parent.image.get('pipelineStageId'));seen=set()
    for _ in range(64):
        if not row or row.id in seen:break
        seen.add(row.id);yield row
        row=owned_stage(db,parent,row.descriptor.get('inputSceneId'))


def global_verdict(row):
    from .binding import composition_from_result,inspection_scene
    from .people_inventory import evaluate_facts
    verdict=composition_from_result({'sceneReview':row.image.get('sceneReview'),
        'peopleInventory':row.image.get('peopleInventory'),'characters':[]},
        row.descriptor.get('sceneCast',[]),inspection_scene(row))
    facts=evaluate_facts(row.image.get('peopleInventory'),row.descriptor.get('visualFacts'),row.image.get('sha256'))
    return verdict.get('verified') is True and not verdict.get('issues') and not facts


def invalid_preview_hashes(db,parent):
    """The latest owned observation of each texture supersedes its old alias."""
    seen=set();invalid=[]
    for row in stages(db,parent):
        digest=row.image.get('sha256')
        if not digest or digest in seen:continue
        seen.add(digest)
        if ((row.descriptor.get('visualFacts') or {})!=(parent.descriptor.get('visualFacts') or {})
                or contradicted(row) or not global_verdict(row)):
            invalid.append(digest)
    return invalid


def publish_received_environment(task_id,image_hash,reads):
    """Publish settled blind environment evidence while coordinates still run."""
    import json
    from ..db import SessionLocal
    from ..models import Task
    from ..worker import assert_task_active
    from ..world.jobs import lock_task_world
    from ..world.models import WorldLine
    from .binding import composition_from_result,inspection_scene
    with SessionLocal() as db:
        lock_task_world(db,task_id);task=db.get(Task,task_id);assert_task_active(db,task)
        row=db.get(SceneImage,task.payload.get('sceneId'))
        if (not row or row.owner_id!=task.owner_id or row.status!='ready'
                or row.image.get('bindingTaskId')!=task.id or row.image.get('sha256')!=image_hash
                or row.descriptor.get('sceneCast')):return False
        line=db.get(WorldLine,row.line_id);parent=db.get(SceneImage,row.descriptor.get('pipelineParentId'))
        if (not line or line.status!='active' or line.head_checkpoint_id!=row.checkpoint_id
                or not parent or parent.status!='queued' or parent.owner_id!=row.owner_id
                or parent.image.get('pipelineStageId')!=row.id):return False
        if (row.descriptor.get('visualFacts') or {})!=(parent.descriptor.get('visualFacts') or {}):return False
        try:observation=json.loads(reads['review']['text'])
        except (ValueError,KeyError,TypeError):return False
        review={'version':1,'imageHash':image_hash,'observation':observation}
        inventory={'version':1,'imageHash':image_hash,'views':[reads[i] for i in range(4)]}
        verdict=composition_from_result({'sceneReview':review,'peopleInventory':inventory,'characters':[]},[],inspection_scene(row))
        if verdict.get('verified') is not True or verdict.get('issues'):return False
        row.image={**row.image,'sceneReview':{**review,'previewEligible':True},'peopleInventory':inventory,'composition':verdict}
        db.commit();return True


def preview_stage(db,parent,source_hash=None):
    invalid=set(invalid_preview_hashes(db,parent))
    for row in stages(db,parent):
        proof=row.image.get('sceneReview',{})
        from .people_inventory import evaluate as evaluate_inventory
        inventory_ok=((parent.descriptor.get('peopleInventoryVersion')!=1 or row.descriptor.get('peopleInventoryVersion')==1) and (row.descriptor.get('peopleInventoryVersion')!=1 or
            evaluate_inventory(row.image.get('peopleInventory'),row.image.get('sha256'))=={'verified':True,'count':len(row.descriptor.get('sceneCast',[]))})
            )
        if (row.status=='ready' and (not row.descriptor.get('sceneCast') or row.image.get('anchorStatus')=='complete')
                and row.profile_id==parent.profile_id and row.profile_version==parent.profile_version
                and row.checkpoint_id==parent.checkpoint_id and row.descriptor.get('sceneReviewVersion')==1 and not contradicted(row)
                and row.image.get('sha256') not in invalid
                and (row.descriptor.get('visualFacts') or {})==(parent.descriptor.get('visualFacts') or {})
                and inventory_ok and isinstance(proof,dict) and proof.get('version')==1
                and proof.get('imageHash')==row.image.get('sha256') and row.image.get('file')
                and (source_hash is None or row.image.get('sha256')==source_hash)):
            if global_verdict(row):return row
    return None


def metadata(db,parent):
    row=preview_stage(db,parent)
    if not row:return None
    digest=row.image['sha256']
    return {'url':'/api/world/v1/scenes/'+parent.id+'/preview/'+digest,
            'sourceHash':digest,'revision':digest+':preview1024-v1',
            'phase':'environment' if not row.descriptor.get('sceneCast') else 'characters',
            'readOnly':True,'width':min(1024,row.image['width']),'height':min(1024,row.image['width'])//2}


def pixels(row):
    from .service import image_bytes
    with Image.open(io.BytesIO(image_bytes(row))) as source:
        image=source.convert('RGB');image.thumbnail((1024,512))
    output=io.BytesIO();image.save(output,format='WEBP',quality=82)
    return output.getvalue()
