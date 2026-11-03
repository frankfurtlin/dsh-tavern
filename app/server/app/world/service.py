from copy import deepcopy
from sqlalchemy import select, case
from ..models import Book, Branch, Revision, ContentBlock, Progress, Task, SourceDocument, uid, now, Outbox
from ..domain import owned, validate_anchor, prefix_blocks, make_anchor, canonical, digest, utf16_len
from ..knowledge import safe_scope
from ..errors import Problem
from .models import World, WorldLine, WorldCheckpoint, WorldTurn, WorldEvent, WorldPublication
from .jobs import create_job, TERMINAL
from .state import initial_state, public_view, public_payload, ancestry, checkpoint_hash, visible_to, execution_state
from .preparation import batches


def source_snapshot(db, owner, anchor, full_reference=False):
    branch, revision, _ = validate_anchor(db, anchor, owner)
    book = owned(db, Book, branch.book_id, owner)
    if book.status != "active" or branch.status != "active":
        raise Problem("BRANCH_ARCHIVED", "请先恢复这本书或故事线", 409)
    safe_scope(db, anchor, revision, owner)
    blocks = prefix_blocks(db, revision, anchor, owner)
    # Only completely observed paragraph HTML is part of the public frozen
    # prefix. Never retain unread text embedded in a partial paragraph's HTML.
    import base64
    from ..storage import private_path
    source = db.scalar(select(SourceDocument).where(SourceDocument.owner_id == owner, SourceDocument.book_id == book.id))
    assets = {}
    for part in blocks:
        original = owned(db, ContentBlock, part["blockId"], owner)
        if original.text == part["text"] and original.html:
            part["html"] = original.html
            for asset_id, ref in (source.source_map.get("assets", {}) if source else {}).items():
                if ('data-asset="' + asset_id + '"') in original.html:
                    assets[asset_id] = base64.b64encode(private_path(owner, ref).read_bytes()).decode("ascii")
    if not blocks:
        raise Problem("WORLD_SOURCE_EMPTY", "请先读到一个段落，再从那里进入故事")
    chapters = []
    by_chapter = {}
    for block in blocks:
        by_chapter.setdefault(block["chapterId"], []).append(block)
    for chapter in revision.manifest:
        if chapter["id"] in by_chapter:
            chapters.append({"id": chapter["id"], "title": chapter["title"], "blocks": by_chapter[chapter["id"]]})
    reference = []
    if full_reference:
        for chapter in revision.manifest:
            for block_id in chapter["blocks"]:
                block = owned(db, ContentBlock, block_id, owner)
                reference.append({"blockId": block.id, "chapterId": chapter["id"], "text": block.text, "source": block.source})
    source_value = {"anchor": deepcopy(anchor), "branchName": branch.name, "title": book.title,
        "contentHash": digest(canonical(blocks)), "blocks": blocks, "chapters": chapters,
        "referenceBlocks": reference, "fullBookReference": full_reference, "assets": assets}
    from ..character_identity import source_references
    source_value["characterRefs"] = source_references(db, owner, source_value)
    return book, source_value


def create_world(db, owner, data):
    if data.templateId:
        from .templates import catalogue, template_definition
        template = next(item for item in catalogue() if item["id"] == data.templateId)
        rules = data.rules.model_dump()
        if "genre" not in data.rules.model_fields_set:
            rules["genre"] = template["genre"]
        definition = template_definition(data.templateId, rules)
        name = data.name.strip() or template["title"]
        source = {"schemaVersion": 2, "kind": "standalone", "templateId": data.templateId, "templateVersion": 1,
            "title": name, "summary": template["description"], "anchor": None, "contentHash": digest(canonical(definition)),
            "blocks": [], "chapters": [], "referenceBlocks": [], "fullBookReference": False}
        world = World(id=uid(), owner_id=owner, name=name, source_title=name, source=source, definition=definition, status="active")
        db.add(world); db.flush()
        return {"worldId": world.id, "taskId": None, "status": "active"}
    book, source = source_snapshot(db, owner, data.anchor.model_dump(), data.fullBookReference)
    source["kind"] = "book"
    world = World(id=uid(), owner_id=owner, name=data.name.strip() or book.title, source_book_id=book.id,
                  source_title=book.title, source=source, definition={"rules": data.rules.model_dump(), "version": 0}, status="preparing")
    db.add(world)
    db.flush()
    count = len(batches(source, min(data.maxCalls, 8)))
    task = create_job(db, owner, "world_prepare", {"worldId": world.id, "preparationBatches": count}, ["analysis"]*count, data.maxCalls)
    world.prepare_task_id = task.id
    return {"worldId": world.id, "taskId": task.id, "status": world.status}


def readable_line(db, world, exclude=None):
    query = select(WorldLine).where(WorldLine.world_id == world.id, WorldLine.owner_id == world.owner_id,
                                    WorldLine.status.in_(["active", "archived"]))
    if exclude:
        query = query.where(WorldLine.id != exclude)
    return db.scalar(query.order_by(case((WorldLine.status == "active", 0), else_=1),
                                    WorldLine.created_at, WorldLine.id).limit(1))


def world_result(db, world, *, include_definition=False):
    book = db.get(Book, world.source_book_id) if world.source_book_id else None
    line = db.get(WorldLine, world.active_line_id) if world.active_line_id else None
    if not line or line.status == "deleted":
        # Older deletions may leave a null entry pointer while an archived or
        # restored line still exists. Resolve an entry without changing history.
        line = readable_line(db, world)
    checkpoint = db.get(WorldCheckpoint, line.head_checkpoint_id) if line else None
    value = {"id": world.id, "name": world.name, "sourceBookId": book.id if book and book.owner_id == world.owner_id else None,
             "sourceTitle": world.source_title, "sourceKind": source_kind(world), "sourceAnchor": world.source.get("anchor"),
             "portraitDialogue": bool(world.source.get("portraitStage")),
             "sourceRemoved": source_kind(world) == "book" and book is None,
             "templateId": world.source.get("templateId"), "templateSummary": world.source.get("summary", ""),
             "status": world.status, "version": world.version, "activeLineId": line.id if line else None,
             "prepareTaskId": world.prepare_task_id, "createdAt": world.created_at.isoformat(), "updatedAt": world.updated_at.isoformat(),
             "description": world.definition.get("description", ""), "rules": world.definition.get("rules", {}),
             "playerName": next((e["name"] for e in checkpoint.definition.get("entities", []) if e["id"] == checkpoint.state["playerId"]), "") if checkpoint else "",
             "timeLabel": checkpoint.state["timeLabel"] if checkpoint else world.definition.get("timeLabel", ""),
             "locationName": next((e["name"] for e in checkpoint.definition.get("entities", []) if e["id"] == checkpoint.state["location"]), "") if checkpoint else ""}
    if world.source.get('authorTrial'):
        from ..authoring.trials import present
        value['authorTrial']=present(db,world)
    if include_definition:
        from ..authoring.service import effective_definition
        effective=effective_definition(db,world)
        if effective.get('authoring'):
            value['authoring']={k:deepcopy(effective['authoring'][k]) for k in ('openings','playerFields','suggestedModel','formTitle') if k in effective['authoring']}
        if effective.get('script'):
            value['recommendedCharacterId'] = effective['script']['recommendedCharacterId']
            value['opening'] = effective['script']['opening']
        # The creation sheet exposes identities, never director goals, private knowledge or source snapshots.
        value["characters"] = [{"id": e["id"], "name": e["name"], "description": e["description"], "traits": e["traits"],
            "available": effective.get("initial", {}).get(e["id"], {}).get("alive") is not False}
            for e in effective.get("entities", []) if e["type"] == "character"]
        from ..character_identity import world_fields
        value["characters"] = world_fields(db, world, [{**e, "type": "character"} for e in value["characters"]], definition=effective)
    return value


def source_kind(world):
    # Legacy frozen reader snapshots may have lost their live book reference.
    # Only an explicit original kind makes a world independent of a book.
    return "standalone" if world.source.get("kind") == "standalone" else "book"


def require_line(db, owner, world_id, line_id, lock=False, active=False):
    world = owned(db, World, world_id, owner)
    line = owned(db, WorldLine, line_id, owner, lock=lock)
    if line.world_id != world.id:
        raise Problem("RESOURCE_NOT_FOUND", "这条世界线不存在或不可访问", 404)
    if line.status == "deleted":
        raise Problem("WORLD_LINE_DELETED", "这条世界线已删除，请打开仍保留的世界线", 404)
    if active and (world.status != "active" or line.status != "active"):
        raise Problem("WORLD_ARCHIVED", "请先恢复这个世界或世界线", 409)
    return world, line


def checked_checkpoint(db, owner, world_id, checkpoint_id):
    checkpoint = owned(db, WorldCheckpoint, checkpoint_id, owner)
    if checkpoint.world_id != world_id:
        raise Problem("RESOURCE_NOT_FOUND", "这个节点不属于当前世界", 404)
    if checkpoint.content_hash != checkpoint_hash(checkpoint.state, checkpoint.definition, checkpoint.text, checkpoint.parent_checkpoint_id):
        raise Problem("WORLD_CHECKPOINT_INVALID", "存档校验未通过，当前世界不会前进", 409)
    return checkpoint


def checkpoint_result(checkpoint, *, include_state=True, db=None):
    value = {"id": checkpoint.id, "worldId": checkpoint.world_id, "lineId": checkpoint.line_id,
             "parentCheckpointId": checkpoint.parent_checkpoint_id, "sequence": checkpoint.sequence,
             "title": checkpoint.title, "text": checkpoint.text, "action": checkpoint.action,
             "options": checkpoint.options, "changes": checkpoint.changes, "summary": checkpoint.summary,
             "kind": checkpoint.kind, "createdAt": checkpoint.created_at.isoformat()}
    if include_state:
        value["state"] = public_view(checkpoint.state, checkpoint.definition)
        if db is not None:
            from ..character_identity import world_fields
            world = owned(db, World, checkpoint.world_id, checkpoint.owner_id)
            entities = world_fields(db, world, value["state"]["entities"], checkpoint)
            value["characterScopes"] = [{"entityId": e["id"], **{k: e[k] for k in
                ("identityId", "sourceRef", "scopeVersion", "scopeRevisionId")}}
                for e in entities if e.get("type") == "character"]
    return public_payload(value, checkpoint.state, checkpoint.definition)


def line_result(db, line):
    checkpoint = owned(db, WorldCheckpoint, line.head_checkpoint_id, line.owner_id)
    return {"id": line.id, "worldId": line.world_id, "name": line.name, "parentLineId": line.parent_line_id,
        "forkCheckpointId": line.fork_checkpoint_id, "headCheckpointId": line.head_checkpoint_id,
        "pendingTaskId": line.pending_task_id, "status": line.status, "version": line.version,
        "readingPosition": line.reading_position, "camera": line.camera,
        "head": checkpoint_result(checkpoint, db=db), "createdAt": line.created_at.isoformat()}


def start_line(db, owner, world, definition, state, *, line_name="我的故事", title="故事从这里开始", summary=None):
    """Create a fresh line with its root checkpoint and make it active."""
    line_id, checkpoint_id = uid(), uid()
    line = WorldLine(id=line_id, owner_id=owner, world_id=world.id, name=line_name, head_checkpoint_id=checkpoint_id)
    root_text = ""
    if summary is None:
        summary = "进入原创世界的初始时点" if source_kind(world) == "standalone" else "进入来源故事的当前时点"
    root = WorldCheckpoint(id=checkpoint_id, owner_id=owner, world_id=world.id, line_id=line_id, sequence=0,
        parent_checkpoint_id=None, title=title, text=root_text, action="", state=state, definition=definition,
        options=[], changes=[], summary=summary, memory={"layers": []},
        content_hash=checkpoint_hash(state, definition, root_text, None), kind="start")
    db.add(line)
    db.flush()
    db.add(root)
    db.flush()
    world.active_line_id, world.updated_at = line.id, now()
    return line, root


def begin_world(db, owner, world, data, *, profile_id=None):
    if world.status != "active" or not world.definition.get("entities"):
        raise Problem("WORLD_NOT_READY", "世界还在准备，可以稍后继续", 409)
    from ..character_identity import resolve_world_character
    from ..authoring.service import effective_definition, configure_start, initialize_fields
    effective, selected_character, opening=configure_start(effective_definition(db,world),data)
    character = resolve_world_character(db, world, effective, selected_character) if selected_character else None
    definition, state = initial_state(effective, character, data.customName, data.customIdentity)
    initialize_fields(definition,state)
    suggested=definition.get('authoring',{}).get('suggestedModel','') if data.useSuggestedModel else ''
    line, root = start_line(db, owner, world, definition, state, line_name=data.lineName)
    if suggested or profile_id:
        from ..models import ModelProfile
        from ..play_controls.models import PlayControl
        from ..play_controls.service import text_profile
        choices=list(db.scalars(select(ModelProfile).where(ModelProfile.owner_id==owner,ModelProfile.id==profile_id))) if profile_id else list(db.scalars(select(ModelProfile).where(ModelProfile.owner_id==owner,ModelProfile.model_id==suggested,ModelProfile.status=='active')))
        selected=None
        for choice in choices:
            try:selected=text_profile(db,owner,choice.id);break
            except Problem:
                if profile_id:raise
                continue
        if profile_id and not selected:raise Problem('MODEL_UNAVAILABLE','所选文本模型不可用',409)
        if selected:
            from ..play_controls.models import MemoryRevision
            setting=PlayControl(id=uid(),owner_id=owner,scope_kind='line',scope_id=line.id,world_id=world.id,profile_id=selected.id,version=1,memory={'summary':'','notes':''});db.add(setting);db.flush()
            db.add(MemoryRevision(owner_id=owner,control_id=setting.id,version=1,memory={'summary':'','notes':''}))
        db.flush()
    opening = opening.strip() or "观察眼前的处境，让在场人物自然行动；在第一个需要我决定的地方停下。"
    from .schemas import ActionInput
    result = create_turn(db, owner, world, line, ActionInput(message=opening, kind="observe",
        expectedCheckpointId=root.id, turnId="opening:" + line.id))
    from ..foundation.events import emit
    emit(db,owner,'play-start:'+line.id,'fact.play.started',data={'worldId':world.id,'lineId':line.id})
    return {"lineId": line.id, "checkpointId": root.id, **result}


def create_turn(db, owner, world, line, data, kind="world_turn", *, chapter_count=1):
    if line.status != "active" or world.status != "active":
        raise Problem("WORLD_ARCHIVED", "请先恢复这条世界线", 409)
    if line.head_checkpoint_id != data.expectedCheckpointId:
        raise Problem("WORLD_CHECKPOINT_CONFLICT", "世界已经前进，输入仍然保留", 409,
                      {"currentCheckpointId": line.head_checkpoint_id})
    if line.pending_task_id:
        pending = db.get(Task, line.pending_task_id)
        # A failed persistence write still owns this slot and can be reclaimed.
        # Replacing it would authorize both the new turn and its durable retry.
        if pending and (pending.state not in TERMINAL or
                        (pending.state == "failed" and pending.persistence_state == "recoverable")):
            raise Problem("WORLD_TURN_PENDING", "当前回合还在进行，可以停止或稍后继续", 409)
    parent = checked_checkpoint(db, owner, world.id, line.head_checkpoint_id)
    if parent.state["ended"]:
        raise Problem("WORLD_ENDED", "这条故事已结束，可以回到历史节点另选", 409)
    if parent.state["entities"][parent.state["playerId"]].get("alive") is False and not (
            data.kind == "wait" or any(x in data.message for x in ("复活", "救援", "求救", "重生"))):
        raise Problem("WORLD_PLAYER_UNAVAILABLE", "当前人物无法行动，可以等待救援或选择历史节点", 409)
    turn_id = uid()
    # Reserve one bounded correction of a received director response. Normal
    # turns still make three calls; unknown provider responses are not retried.
    stages = [stage for _ in range(chapter_count) for stage in ("analysis", "analysis", "generation", "validation")]
    task = create_job(db, owner, kind, {"worldId": world.id, "lineId": line.id, "turnId": turn_id,
                     "chapterCount": chapter_count, "parentCheckpointId": parent.id}, stages)
    turn = WorldTurn(id=turn_id, owner_id=owner, world_id=world.id, line_id=line.id, parent_checkpoint_id=parent.id,
        turn_key=data.turnId, task_id=task.id, action=data.message, kind=data.kind, status="queued")
    db.add(turn)
    line.pending_task_id = task.id
    return {"turnId": turn.id, "taskId": task.id, "lineId": line.id}


def commit_turn(db, task, turn, parent, state, definition, plan, text, changes, events, checkpoint_id, *, kind="turn"):
    world, line = require_line(db, task.owner_id, turn.world_id, turn.line_id, lock=True, active=True)
    from ..character_identity import world_reference
    for entity in definition["entities"]:
        if entity["type"] == "character":world_reference(world, entity)
    if task.cancel_requested or line.pending_task_id != task.id or line.head_checkpoint_id != parent.id:
        raise Problem("WORLD_CHECKPOINT_CONFLICT", "这轮的起点已经变化，正文仍作为草稿保留", 409)
    from ..worker import assert_task_active, add_event
    assert_task_active(db, task)
    checkpoint = WorldCheckpoint(id=checkpoint_id, owner_id=task.owner_id, world_id=world.id, line_id=line.id,
        parent_checkpoint_id=parent.id, sequence=parent.sequence + 1, title=plan["title"], text=text,
        action=turn.action, state=state, definition=definition, options=[] if state["ended"] else plan["options"], changes=changes,
        summary=plan["summary"], memory=deepcopy(parent.memory),
        content_hash=checkpoint_hash(state, definition, text, parent.id), kind=kind)
    db.add(checkpoint)
    db.flush()
    for event in events:
        db.add(WorldEvent(owner_id=task.owner_id, world_id=world.id, checkpoint_id=checkpoint.id,
            entity_id=event.get("entityId"), type=event["type"], data=event, visible_to=event["visibleTo"], minute=event.get("minute", state["minute"])))
    line.head_checkpoint_id, line.pending_task_id, line.version = checkpoint.id, None, line.version + 1
    world.active_line_id, world.updated_at = line.id, now()
    turn.status, turn.checkpoint_id, turn.draft_text = "committed", checkpoint.id, text
    task.state, task.persistence_state, task.result_outcome = "succeeded", "saved", "complete"
    task.error_code = None
    received = {key: task.result[key] for key in ('reusedDirectorTaskId','reusedNarratorTaskId') if task.result.get(key)}
    task.result = {**received, "phase": "ready", "worldId": world.id, "lineId": line.id, "checkpointId": checkpoint.id, "turnId": turn.id}
    add_event(db, task, "turn_committed", {"checkpointId": checkpoint.id, "lineId": line.id})
    from ..foundation.events import checkpoint_facts
    checkpoint_facts(db, checkpoint, parent)
    return checkpoint


def identity_start(path):
    """Restart the most recent identity entry, including a same-character re-entry."""
    origin = path[0]
    for previous, checkpoint in zip(path, path[1:]):
        if checkpoint.kind == "identity":
            origin = checkpoint
        elif checkpoint.kind == "fork" and checkpoint.summary == "以新的身份进入故事" and (
            checkpoint.state["playerId"] != previous.state["playerId"]
            or previous.summary != checkpoint.summary
        ):
            # Older identity forks had no explicit kind. A copied continuation
            # summary does not create another identity origin.
            origin = checkpoint
    return origin


def fork_line(db, owner, world, data):
    if world.status != "active":
        raise Problem("WORLD_ARCHIVED", "请先恢复这个世界", 409)
    selected = checked_checkpoint(db, owner, world.id, data.checkpointId)
    parent = selected
    if data.mode == "rechoose":
        if not selected.parent_checkpoint_id:
            raise Problem("WORLD_HISTORY_START", "这是起点，可以从这里继续")
        parent = checked_checkpoint(db, owner, world.id, selected.parent_checkpoint_id)
    elif data.mode == "restart":
        parent = identity_start(ancestry(db, selected, owner))
        parent = checked_checkpoint(db, owner, world.id, parent.id)
    state, definition = execution_state(parent, ancestry(db, parent, owner)), deepcopy(parent.definition)
    if data.mode == "identity":
        from ..character_identity import resolve_world_character
        character = resolve_world_character(db, world, definition, data.characterId) if data.characterId else None
        seed = deepcopy(definition)
        seed["location"] = state["location"]
        if character and character in state["entities"]:
            seed["initial"] = {**seed.get("initial", {}), character: deepcopy(state["entities"][character])}
        perspective, changed = initial_state(seed, character, data.customName, data.customIdentity)
        # The current state is a perspective seed, never a replacement for frozen origin data.
        definition["entities"], definition["version"] = perspective["entities"], perspective["version"]
        # Change perspective at this time, without resetting everyone else's lived state.
        player = changed["playerId"]
        if player not in state["entities"]:
            state["entities"][player] = changed["entities"][player]
        elif state["entities"][player].get("alive") is False:
            raise Problem("WORLD_CHARACTER_DEAD", "这个人物在此时已经死亡")
        state["playerId"], state["known"] = player, list(changed["known"])
        state["entities"][player]["known"] = list(changed["known"])
        state["location"] = state["entities"][player].get("location") or state["location"]
        state["observed"] = {}
        state["discovered"] = [player, state["location"]] + [k for k, e in state["entities"].items() if e.get("location") == state["location"]]
        state["ended"] = False
    line_id, cp_id = uid(), uid()
    line = WorldLine(id=line_id, owner_id=owner, world_id=world.id, name=data.name,
        parent_line_id=parent.line_id, fork_checkpoint_id=parent.id, head_checkpoint_id=cp_id)
    db.add(line)
    db.flush()
    display_text = parent.text if data.mode != 'identity' else ''
    checkpoint = WorldCheckpoint(id=cp_id, owner_id=owner, world_id=world.id, line_id=line.id,
        parent_checkpoint_id=parent.id, sequence=parent.sequence, title=data.name, text=display_text, action="", state=state,
        definition=definition, options=[] if data.mode == "identity" else parent.options, changes=[], summary="以新的身份进入故事" if data.mode == "identity" else parent.summary, memory=deepcopy(parent.memory),
        content_hash=checkpoint_hash(state, definition, display_text, parent.id),
        kind="identity" if data.mode == "identity" else "fork")
    db.add(checkpoint)
    db.flush()
    world.active_line_id, world.updated_at = line.id, now()
    if data.mode in ('rechoose', 'restart'):
        from ..foundation.events import emit
        completed = db.scalar(select(WorldCheckpoint.id).where(WorldCheckpoint.owner_id == owner,
            WorldCheckpoint.world_id == world.id, WorldCheckpoint.state['plot']['resolution']['phase'].astext == 'resolved').limit(1))
        if completed and state.get('plot', {}).get('resolution', {}).get('phase') != 'resolved':
            emit(db, owner, 'replay:' + line.id, 'fact.line.replay.started', data={'worldId':world.id,'lineId':line.id})
    return line_result(db, line)


def publish_reading(db, owner, world, checkpoint):
    saved = db.scalar(select(WorldPublication).where(WorldPublication.owner_id == owner, WorldPublication.checkpoint_id == checkpoint.id))
    if saved:
        owned(db, Book, saved.book_id, owner)
        validate_anchor(db, saved.anchor, owner)
        return {"bookId": saved.book_id, "branchId": saved.branch_id, "revisionId": saved.revision_id, "readingAnchor": saved.anchor}
    path = [c for c in ancestry(db, checkpoint, owner) if c.text and c.kind not in ("start", "fork", "correction")]
    if not path:
        raise Problem("WORLD_PUBLICATION_EMPTY", "完成一个回合后就可以保存为阅读故事线")
    return publish_standalone(db, owner, world, checkpoint, path)


def publish_standalone(db, owner, world, checkpoint, path):
    """Publish only visible scene prose, never an author's initial private setup."""
    chapters = []
    if source_kind(world) == "book":
        for chapter in world.source.get("chapters", []):
            parts = [part["text"] for part in chapter["blocks"] if part["text"].strip()]
            if parts:
                chapters.append((chapter["title"], parts))
    prefix_count = len(chapters)
    for cp in path:
        visible = public_payload({"title": cp.title, "text": cp.text}, cp.state, cp.definition)
        paragraphs = [paragraph for paragraph in visible["text"].splitlines() if paragraph.strip()]
        if paragraphs:
            chapters.append((visible["title"] or "第" + str(len(chapters) + 1) + "章", paragraphs))
    if len(chapters) == prefix_count:
        raise Problem("WORLD_PUBLICATION_EMPTY", "当前没有可保存的公开正文")
    content = "\n\n".join(paragraph for _, paragraphs in chapters for paragraph in paragraphs)
    book_id, branch_id, revision_id, source_id = uid(), uid(), uid(), uid()
    book = Book(id=book_id, owner_id=owner, title=world.name, source_hash=digest(content), source_id=source_id,
        original_branch_id=branch_id, original_revision_id=revision_id, source_label="来自本人互动世界的独立公开阅读快照")
    db.add(book); db.flush()
    branch = Branch(id=branch_id, owner_id=owner, book_id=book.id, name="游玩故事", head_revision_id=revision_id)
    db.add(branch); db.flush()
    manifest, blocks = [], []
    for title, paragraphs in chapters:
        chapter_id, ids = uid(), []
        for paragraph in paragraphs:
            block = ContentBlock(id=uid(), owner_id=owner, book_id=book.id, chapter_id=chapter_id,
                text=paragraph, source="ai", content_hash=digest(paragraph))
            db.add(block); blocks.append(block); ids.append(block.id)
        manifest.append({"id": chapter_id, "title": title, "blocks": ids})
    revision = Revision(id=revision_id, owner_id=owner, branch_id=branch.id, sequence=1, manifest=manifest,
        content_hash=digest(canonical(manifest)), provenance={"type": "ai", "operation": "world_publication", "worldId": world.id,
            "worldLineId": checkpoint.line_id, "worldCheckpointId": checkpoint.id, "chapterCount": len(path)})
    db.add(revision); db.flush()
    from ..storage import write_private
    import base64
    assets = {}
    for asset_id, value in world.source.get("assets", {}).items():
        assets[asset_id] = write_private(owner, "publication-asset-" + source_id + "-" + asset_id + ".png", base64.b64decode(value, validate=True))
    # Match by original frozen paragraph order rather than text (duplicates are
    # legal), and do not copy GM referenceBlocks or private director setup.
    prefix_parts = [part for chapter in world.source.get("chapters", []) for part in chapter["blocks"] if part["text"].strip()]
    for block, part in zip(blocks, prefix_parts):
        block.source = part["source"]
        block.html = part.get("html")
    from ..character_identity import world_identity, world_proof
    character_bindings = []
    # Character indexing is optional before entering a world. Recover the
    # precise original source occurrence from verified world evidence too.
    # Do not use global name matching or a broad citation span as identity proof.
    prefix_people = []
    for entity in world.definition.get("entities", []):
        if entity["type"] != "character":
            continue
        proof = world_proof(world, entity)
        if proof:
            prefix_people.append({"name": proof["originalLabel"], "identityId": world_identity(db, world, entity).id,
                "proof": proof})
    for ref in world.source.get("characterRefs", []):
        if ref.get("proof") and not any(p["proof"] == ref["proof"] for p in prefix_people):
            prefix_people.append({"name": ref["proof"]["originalLabel"], "identityId": ref["identityId"], "proof": ref["proof"]})
    cursor = 0
    generated = [c for c in path if any(p.strip() for p in public_payload(c.text, c.state, c.definition).splitlines())]
    for index, (_, paragraphs) in enumerate(chapters):
        if index < prefix_count:
            people = prefix_people
        else:
            # Resolve by generated chapter sequence, never by a possibly repeated title.
            cp = generated[index - prefix_count]
            visible = public_view(cp.state, cp.definition)["entities"]
            people = [{"name": e["name"], "identityId": world_identity(db, world,
                next(original for original in cp.definition["entities"] if original["id"] == e["id"])).id}
                for e in visible if e["type"] == "character" and
                sum(original["type"] == "character" and original["name"] == e["name"] for original in cp.definition["entities"]) == 1]
        for paragraph in paragraphs:
            block = blocks[cursor]
            part = prefix_parts[cursor] if index < prefix_count else None
            cursor += 1
            for person in people:
                if len({p["identityId"] for p in people if p["name"] == person["name"]}) != 1:
                    continue
                if part:
                    proof = person["proof"]
                    start = proof["start"]
                    if proof["blockId"] != part["blockId"] or paragraph.encode("utf-16-le")[start*2:(start+utf16_len(person["name"]))*2].decode("utf-16-le") != person["name"]:
                        continue
                else:
                    point = paragraph.find(person["name"])
                    if point < 0:
                        continue
                    start = utf16_len(paragraph[:point])
                character_bindings.append({"name": person["name"], "identityId": person["identityId"], "blockId": block.id,
                    "start": start, "end": start + utf16_len(person["name"])})
    file_ref = write_private(owner, "world-publication-" + source_id + ".txt", content.encode("utf-8"))
    db.add(SourceDocument(id=source_id, owner_id=owner, book_id=book.id, file_hash=digest(content), file_ref=file_ref,
        format="txt", encoding="utf-8", normalized_ref=file_ref, source_map={"assets": assets, "worldId": world.id,
            "kind": "world_publication", "characterBindings": character_bindings}))
    first_generated = owned(db, ContentBlock, manifest[prefix_count]["blocks"][0], owner)
    anchor = make_anchor(book.id, branch.id, revision.id, first_generated)
    if prefix_count:
        last_prefix = owned(db, ContentBlock, manifest[prefix_count-1]["blocks"][-1], owner)
        inherited = make_anchor(book.id, branch.id, revision.id, last_prefix, utf16_len(last_prefix.text))
        db.add(Progress(owner_id=owner,book_id=book.id,branch_id=branch.id,revision_id=revision.id,anchor=inherited,max_observed_anchor=inherited,device_sequences={}))
    db.add(WorldPublication(owner_id=owner, world_id=world.id, checkpoint_id=checkpoint.id,
        book_id=book.id, branch_id=branch.id, revision_id=revision.id, anchor=anchor))
    db.add(Outbox(owner_id=owner, event_type="story_committed", payload={"branchId": branch.id, "revisionId": revision.id}))
    return {"bookId": book.id, "branchId": branch.id, "revisionId": revision.id, "readingAnchor": anchor}
