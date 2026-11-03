"""Completed listening/view ranges only, with explicit polymorphic bindings.

No audio, model profile, account preference, token, queue or billing field is
copied. Optional tables are discovered against the actual installed schema.
"""
from copy import deepcopy
from sqlalchemy import or_, select, text
from .. import models as reader
from ..domain import canonical, digest
from ..errors import Problem
from .errors import ArchiveError


def feature_models():
    playback = getattr(reader, "PlaybackPosition", None)
    try:
        from ..comparison.models import ComparisonExposure
        exposure = ComparisonExposure
    except ModuleNotFoundError as error:
        if error.name not in {"app.comparison", "app.comparison.models"}:
            raise
        exposure = None
    return playback, exposure


def row_specs(RowSpec):
    playback, exposure = feature_models(); result = []
    if playback is not None:
        result.append(RowSpec("playback_position", playback, ("scope_key", "story_id", "kind", "mode", "line_id",
            "content_revision", "context", "paragraph_id", "last_completed_anchor", "max_heard_anchor", "ended", "version",
            "completed_at", "updated_at", "book_id", "world_id", "generation_id", "candidate_version"),
            {"book_id": "book", "world_id": "world", "generation_id": "generation_batch"}))
    if exposure is not None:
        result.append(RowSpec("comparison_exposure", exposure, ("scope_key", "kind", "resource_id", "line_id", "node_id",
            "anchor", "version", "updated_at"), {}))
    return tuple(result)


def ready(db, model):
    return model is not None and db.scalar(text("SELECT to_regclass(:name)"), {"name": model.__tablename__}) is not None


def clone(model, value):
    return model(**{column.name: deepcopy(getattr(value, column.name)) for column in model.__table__.columns})


def collect(db, owner, selected):
    from ..world.models import World, WorldLine
    branches = {row.id: row for row in selected[reader.Branch]}
    worlds = {row.id: row for row in selected[World]}
    lines = {row.id: row for row in selected[WorldLine] if row.status != "deleted"}
    playback, exposure = feature_models()
    if ready(db, playback):
        rows = db.scalars(select(playback).where(playback.owner_id == owner, or_(
            (playback.kind.in_(["reader", "draft"])) & playback.line_id.in_(branches),
            (playback.kind == "world") & playback.world_id.in_(worlds) & playback.line_id.in_(lines))))
        values = []
        for row in rows:
            value = clone(playback, row)
            if row.kind == "world":
                value.story_id = "world-"+value.world_id
            else:
                # A legacy publication may have a new private carrier Book.
                # Normalize its surviving read entry; never depend on a deleted
                # original world root or transfer another book's navigation.
                book_id = branches[row.line_id].book_id
                value.book_id = book_id; value.story_id = "book-"+book_id
                for anchor in (value.context.get("anchor"), value.last_completed_anchor, value.max_heard_anchor):
                    if anchor is not None and "bookId" in anchor: anchor["bookId"] = book_id
                value.context["bookId"] = book_id
            value.context["storyId"] = value.story_id
            values.append(value)
        selected[playback] = values
    if ready(db, exposure):
        rows = db.scalars(select(exposure).where(exposure.owner_id == owner, or_(
            (exposure.kind == "book") & exposure.line_id.in_(branches),
            (exposure.kind == "world") & exposure.resource_id.in_(worlds) & exposure.line_id.in_(lines))))
        values = []
        for row in rows:
            value = clone(exposure, row)
            if value.kind == "book":
                value.resource_id = branches[value.line_id].book_id
                value.anchor["bookId"] = value.resource_id
            values.append(value)
        selected[exposure] = values


def encode(spec_kind, fields, extra):
    if spec_kind != "playback_position": return
    prefix, identifier = fields.pop("story_id").split("-", 1)
    extra["storyRoot"] = {"type": prefix, "id": identifier}
    kind = fields["kind"]
    if kind == "world":
        anchor = fields["last_completed_anchor"]
        if fields["paragraph_id"] != anchor["checkpointId"]+":"+str(anchor["paragraph"]):
            raise ArchiveError("BACKUP_PLAYBACK_INVALID")
        fields["paragraph_id"] = str(anchor["paragraph"])
    elif kind == "draft":
        if fields["content_revision"] != fields["generation_id"]+":"+str(fields["candidate_version"]):
            raise ArchiveError("BACKUP_PLAYBACK_INVALID")
        fields["content_revision"] = fields["generation_id"]
    elif kind != "reader": raise ArchiveError("BACKUP_PLAYBACK_INVALID")


def references(spec_kind, fields, add):
    if spec_kind == "playback_position":
        kind = fields["kind"]
        if kind not in {"reader", "world", "draft"}: raise ArchiveError("BACKUP_PLAYBACK_INVALID")
        add("line_id", "world_line" if kind == "world" else "branch")
        add("content_revision", {"reader": "revision", "world": "checkpoint", "draft": "generation_batch"}[kind])
        if kind != "world": add("paragraph_id", "paragraph" if kind == "reader" else "chapter_content")
    elif spec_kind == "comparison_exposure":
        kind = fields["kind"]
        if kind not in {"book", "world"}: raise ArchiveError("BACKUP_COMPARE_INVALID")
        for name, target in (("resource_id", kind), ("line_id", "branch" if kind == "book" else "world_line"),
            ("node_id", "revision" if kind == "book" else "checkpoint")):
            add(name, target)


def decode(spec_kind, values, record):
    if spec_kind == "playback_position":
        root = record.get("storyRoot")
        if not isinstance(root, dict) or set(root) != {"type", "id"} or root["type"] not in {"book", "world"}:
            raise ArchiveError("BACKUP_PLAYBACK_INVALID")
        values["story_id"] = root["type"]+"-"+root["id"]
        values["context"]["storyId"] = values["story_id"]
        kind = values["kind"]
        if kind == "world":
            anchor = values["last_completed_anchor"]
            if values["paragraph_id"] != str(anchor["paragraph"]): raise ArchiveError("BACKUP_PLAYBACK_INVALID")
            values["paragraph_id"] = anchor["checkpointId"]+":"+str(anchor["paragraph"])
        elif kind == "draft":
            if values["content_revision"] != values["generation_id"]: raise ArchiveError("BACKUP_PLAYBACK_INVALID")
            values["content_revision"] += ":"+str(values["candidate_version"])
        elif kind != "reader": raise ArchiveError("BACKUP_PLAYBACK_INVALID")
        values["scope_key"] = digest(canonical([kind, values["line_id"], values["content_revision"]]))
    elif spec_kind == "comparison_exposure":
        values["scope_key"] = digest(canonical([values["kind"], values["resource_id"], values["line_id"], values["node_id"]]))


def validate(db, owner, rows):
    if rows.get("resume_target"):
        from ..api_stories import StoryContext, validate_context
        for row in rows["resume_target"].values():
            try:
                context = StoryContext.model_validate(row.context)
                if row.version < 1 or context.mode != row.mode or context.storyId != row.story_id:
                    raise ArchiveError("BACKUP_RESUME_INVALID")
                # An archived world can retain its last played historical point
                # while new actions stay disabled. Validate that point with the
                # same read authorization; do not unarchive or move its head.
                probe = context
                if context.mode == "play":
                    probe = StoryContext.model_validate({**context.model_dump(exclude_none=True), "mode": "read", "perspective": "reader"})
                validate_context(db, owner, row.story_id, probe)
            except (Problem, ValueError, TypeError, KeyError) as error:
                raise ArchiveError("BACKUP_RESUME_INVALID") from error
    if rows.get("playback_position"):
        from ..api_playback import scope, completion_order
        from ..api_stories import StoryContext
        for row in rows["playback_position"].values():
            try:
                authorized = scope(db, owner, row.story_id, row.kind, row.mode, row.line_id, row.content_revision,
                    context=StoryContext.model_validate(row.context))
                latest = completion_order(db, owner, authorized, row.last_completed_anchor, row.paragraph_id)
                maximum = completion_order(db, owner, authorized, row.max_heard_anchor)
                if (maximum < latest or row.version < 1 or row.book_id != authorized["bookId"] or
                    row.world_id != authorized["worldId"] or (row.kind == "draft" and row.generation_id != authorized["batch"].id) or
                    (row.kind != "draft" and (row.generation_id is not None or row.candidate_version is not None))):
                    raise ArchiveError("BACKUP_PLAYBACK_INVALID")
            except (Problem, ValueError, TypeError, KeyError) as error:
                raise ArchiveError("BACKUP_PLAYBACK_INVALID") from error
    if rows.get("comparison_exposure"):
        from ..comparison.service import selected_anchor
        from ..comparison.schemas import RevealInput
        from ..domain import validate_anchor, anchor_order
        for row in rows["comparison_exposure"].values():
            try:
                expected = selected_anchor(db, owner, RevealInput(confirmed=True, kind=row.kind, resourceId=row.resource_id,
                    lineId=row.line_id, nodeId=row.node_id, expectedVersion=row.version))
                if row.version < 1: raise ArchiveError("BACKUP_COMPARE_INVALID")
                if row.kind == "world":
                    if row.anchor != expected: raise ArchiveError("BACKUP_COMPARE_INVALID")
                else:
                    _, revision, _ = validate_anchor(db, row.anchor, owner)
                    if revision.id != row.node_id or anchor_order(revision, row.anchor) > anchor_order(revision, expected):
                        raise ArchiveError("BACKUP_COMPARE_INVALID")
            except (Problem, ValueError, TypeError, KeyError) as error:
                raise ArchiveError("BACKUP_COMPARE_INVALID") from error
