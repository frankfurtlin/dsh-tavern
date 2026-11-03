"""Explicit fresh inspection may strengthen facts on the same owned pixels."""
from copy import deepcopy
from .cache import appearance,saved_appearance,compatible
from ..models import now
from ..errors import Problem


def eligible(row,view):
    if row.checkpoint_id!=view.get('checkpointId'):return False
    saved=saved_appearance(row);wanted=appearance(view)
    if not isinstance(saved,dict):return False
    old=saved.get('visualFacts') or {};new=wanted.get('visualFacts') or {}
    if not new or new==old:return False
    if old.get('cameraInspectionVersion')==1 and new.get('cameraInspectionVersion')!=1:return False
    # Add requirements or independent cameras, never weaken a previous fact.
    if any(part not in new.get('requirements',[]) for part in old.get('requirements',[])):return False
    left={k:v for k,v in saved.items() if k!='visualFacts'}
    right={k:v for k,v in wanted.items() if k!='visualFacts'}
    return left==right


def apply(parent,child,view):
    if not (eligible(parent,view) or compatible(parent,view)):return False
    if (not child or child.owner_id!=parent.owner_id or child.world_id!=parent.world_id
            or child.line_id!=parent.line_id or child.checkpoint_id!=parent.checkpoint_id
            or child.profile_id!=parent.profile_id or child.profile_version!=parent.profile_version
            or child.id!=parent.id and child.descriptor.get('pipelineParentId')!=parent.id):
        raise Problem('SCENE_INSPECTION_UNAVAILABLE','已保存图片不属于当前场景',409)
    wanted=appearance(view);facts=wanted.get('visualFacts');changed=False
    for row in [parent,*([child] if child.id!=parent.id else [])]:
        descriptor=row.descriptor
        previous=descriptor.get('visualFacts') or {}
        saved=saved_appearance(row) or {};previous_appearance=saved.get('visualFacts') or {}
        if previous==(facts or {}) and previous_appearance==(facts or {}):continue
        if row.checkpoint_id!=view.get('checkpointId') or not facts:
            raise Problem('SCENE_INSPECTION_UNAVAILABLE','当前画面的事实规则无法确认，未复用旧核验',409)
        for old in (previous,previous_appearance):
            if (any(part not in facts.get('requirements',[]) for part in old.get('requirements',[]))
                    or old.get('cameraInspectionVersion')==1 and facts.get('cameraInspectionVersion')!=1):
                raise Problem('SCENE_INSPECTION_UNAVAILABLE','不能降低已保存图片的事实核验要求',409)
        history=[*descriptor.get('inspectionFactHistory',[]),{
            'imageHash':row.image.get('sha256'),'requestId':row.request_id,'updatedAt':now().isoformat(),
            'previousFacts':deepcopy(descriptor.get('visualFacts')),
            'previousAppearanceFacts':deepcopy((descriptor.get('appearance') or {}).get('visualFacts'))}][-8:]
        row.descriptor={**descriptor,'visualFacts':deepcopy(facts),
            'appearance':{**deepcopy(saved),'visualFacts':deepcopy(facts)},'inspectionFactHistory':history}
        # These are derived observations, not original provider receipts.
        # They cannot authorize a preview under a stronger fact contract.
        row.image={k:v for k,v in row.image.items() if k not in ('sceneReview','peopleInventory','composition')}
        changed=True
    return changed
