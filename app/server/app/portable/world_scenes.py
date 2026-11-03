"""Saved world candidate scenes are history, never committed checkpoints."""
from ..domain import canonical,digest
from .errors import ArchiveError
from .world_schema import checkpoint


def scenes(result):
    if not isinstance(result,dict):raise ArchiveError("BACKUP_WORLD_SCENE_INVALID")
    values=result.get("chapters",[])
    if not isinstance(values,list) or len(values)>10:raise ArchiveError("BACKUP_WORLD_SCENE_INVALID")
    for value in values:
        if not isinstance(value,dict) or set(value)!={"id","state","definition","changes","events","plan","text","contentHash"}:
            raise ArchiveError("BACKUP_WORLD_SCENE_INVALID")
        if not isinstance(value["id"],str) or len(value["id"])!=32 or not isinstance(value["text"],str):
            raise ArchiveError("BACKUP_WORLD_SCENE_INVALID")
        if any(not isinstance(value[k],dict) for k in ("state","definition","plan")) or any(not isinstance(value[k],list) for k in ("events","changes")):
            raise ArchiveError("BACKUP_WORLD_SCENE_INVALID")
    return values


def check_hashes(values):
    for value in values:
        if value["contentHash"]!=digest(canonical({k:v for k,v in value.items() if k!="contentHash"})):
            raise ArchiveError("BACKUP_BODY_HASH_MISMATCH")


def register_candidates(selected,writer,model):
    checkpoints={p.id for p in selected[model.WorldCheckpoint]}
    for turn in selected[model.WorldTurn]:
        values=scenes(turn.result);check_hashes(values);parent=turn.parent_checkpoint_id
        for index,value in enumerate(values):
            if value["id"] not in checkpoints:
                writer.add("world_scene",value["id"],"candidateScene",{
                    "worldId":turn.world_id,"turnId":turn.id,"sceneIndex":index,"parentCheckpointId":parent})
            parent=value["id"]


def validate_candidates(rows,metadata,token_worlds,ancestor):
    matched=set()
    for turn in rows.get("world_turn",{}).values():
        prior=set();parent=turn.parent_checkpoint_id
        for index,value in enumerate(scenes(turn.result)):
            identifier=value["id"]
            if identifier in prior:raise ArchiveError("BACKUP_WORLD_SCENE_INVALID")
            if identifier in metadata:
                if metadata[identifier]!={"worldId":turn.world_id,"turnId":turn.id,"sceneIndex":index,"parentCheckpointId":parent}:
                    raise ArchiveError("BACKUP_WORLD_SCENE_INVALID")
                matched.add(identifier)
            else:
                point=rows.get("checkpoint",{}).get(identifier)
                if not point or point.world_id!=turn.world_id or point.line_id!=turn.line_id or point.parent_checkpoint_id!=parent:
                    raise ArchiveError("BACKUP_WORLD_SCENE_INVALID")
            checkpoint(value["state"],value["definition"],event_tokens={k for k,w in token_worlds.items() if w==turn.world_id})
            prior.add(identifier)
            for entity in value["state"]["entities"].values():
                for proof in entity.get("relationEvidence",{}).values():
                    proof_id=proof["checkpointId"]
                    point=rows.get("checkpoint",{}).get(proof_id)
                    if proof_id not in prior and (not point or point.world_id!=turn.world_id or not ancestor(proof_id,turn.parent_checkpoint_id)):
                        raise ArchiveError("BACKUP_WORLD_SCENE_INVALID")
            parent=identifier
    if matched!=set(metadata):raise ArchiveError("BACKUP_WORLD_SCENE_INVALID")
