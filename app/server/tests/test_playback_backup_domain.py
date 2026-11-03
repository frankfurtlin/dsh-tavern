"""Completed listening/view scopes, actual PG/AES/new-owner restore.

These fixtures never initialize a speech engine or call a provider. They certify
the data adapter against a separately sealed cloud-playback API candidate.
"""
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from test_portable_domain import (
    domain, migrate_owned_database, encrypted, restored, seed_prose_history,
    http_client as base_client,
)
from app import models as m
from app.db import SessionLocal
from app.domain import canonical, digest, make_anchor, utf16_len
from app.portable.errors import ArchiveError
from app.portable.package import prepare_backup
from app.portable.restore import restore_domain
from app.storage import private_path
from app.world import models as w
from app.world.service import checkpoint_result
from app.comparison.models import ComparisonExposure


def client(owner):
    value = base_client(owner, diagnostic=True)
    from app.api_playback import router as playback_router
    from app.api_comparison import router as comparison_router
    from app.api_stories import router as stories_router
    value.app.include_router(playback_router, prefix="/api/reader/v1")
    value.app.include_router(comparison_router, prefix="/api/reader/v1")
    value.app.include_router(stories_router, prefix="/api/reader/v1")
    return value


def context(story_id, **values):
    return {"storyId": story_id, "mode": "read", "perspective": "reader", "entryOrigin": "reader", **values}


def body(kind, scope, paragraph_id, anchor, **values):
    return {"kind": kind, "mode": "draft" if kind == "draft" else "read",
        "expectedVersion": 0, "context": scope, "lineId": scope.get("worldLineId") or scope["readerBranchId"],
        "contentRevision": scope.get("checkpointId") or scope["anchor"]["revisionId"],
        "paragraphId": paragraph_id, "lastCompletedAnchor": anchor, "completed": True, "ended": False, **values}


def save(owner, value, key):
    with client(owner) as http:
        response = http.put("/api/reader/v1/playback/position", json=value, headers={"Idempotency-Key": key})
        assert response.status_code == 200, response.text
        return response.json()


def world_body(domain, checkpoint_id=None):
    with SessionLocal() as db:
        point = db.get(w.WorldCheckpoint, checkpoint_id or domain["checkpoint"])
        line = db.get(w.WorldLine, point.line_id)
        public_text = checkpoint_result(point, include_state=False)["text"]
        # Normal owned CJK fixture has one paragraph; Unicode codec boundaries
        # are separately exercised in the cloud API batch.
        assert public_text and "\n" not in public_text
        anchor = {"worldId": point.world_id, "worldLineId": line.id, "checkpointId": point.id,
            "paragraph": 0, "start": 0, "end": utf16_len(public_text), "offsetUnit": "utf16"}
        scope = context("world-"+point.world_id, worldId=point.world_id, worldLineId=line.id,
            checkpointId=domain["checkpoint"], characterId=point.state["playerId"])
        return body("world", scope, point.id+":0", anchor)


def seed_ranges(domain):
    read = body("reader", context("book-"+domain["book"], bookId=domain["book"], readerBranchId=domain["branch"],
        anchor=domain["anchor"]), domain["block"], domain["anchor"])
    save(domain["owner"], read, "owned-reader-complete")
    world = world_body(domain)
    save(domain["owner"], world, "owned-world-complete")
    earlier = world_body(domain, domain["root_checkpoint"])
    earlier["expectedVersion"] = 1
    save(domain["owner"], earlier, "owned-world-replay")
    history = seed_prose_history(domain)
    with SessionLocal() as db:
        chapter = db.scalar(select(m.GenerationChapterVersion).where(
            m.GenerationChapterVersion.generation_id == history["batch"],
            m.GenerationChapterVersion.candidate_version == 2, m.GenerationChapterVersion.chapter_index == 2))
        anchor = {"bookId": domain["book"], "generationId": history["batch"], "contentId": chapter.content_id,
            "candidateVersion": 2, "start": 0, "end": utf16_len(chapter.text), "offsetUnit": "utf16", "draft": True}
        draft = body("draft", read["context"], chapter.content_id, anchor, contentRevision=history["batch"]+":2")
    save(domain["owner"], draft, "owned-draft-complete")
    with SessionLocal() as db:
        original = list(db.scalars(select(m.PlaybackPosition).where(m.PlaybackPosition.owner_id == domain["owner"])))
        return {row.kind: {column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns}
            for row in original}, history


def fetch(http, row):
    response = http.get("/api/reader/v1/playback/position", params={
        "storyId": row.story_id, "kind": row.kind, "mode": row.mode,
        "lineId": row.line_id, "contentRevision": row.content_revision})
    assert response.status_code == 200, response.text
    return response.json()


def assert_empty_target(domain):
    with SessionLocal() as db:
        for model in (m.Book, m.SourceDocument, m.PlaybackPosition, ComparisonExposure, w.World):
            assert db.scalar(select(func.count()).select_from(model).where(model.owner_id == domain["target"])) == 0
    directory = private_path(domain["target"], "unused-sentinel.txt").parent
    assert not directory.exists() or not list(directory.iterdir())


def test_three_kinds_preserve_completed_maximum_and_context_with_new_ids(domain, tmp_path):
    before, history = seed_ranges(domain)
    with SessionLocal() as db:
        target_settings = db.get(m.UserSettings, domain["target"])
        target_settings.speech_preferences = {"rate": 1.5, "voiceId": "target-device-voice", "sleepMinutes": 15, "stopAtChapterEnd": True}
        db.commit()
        old_progress = db.scalar(select(m.Progress).where(m.Progress.owner_id == domain["owner"]))
        preserved_progress = deepcopy((old_progress.anchor, old_progress.max_observed_anchor, old_progress.version))
        old_heads = {row.id: row.head_checkpoint_id for row in db.scalars(select(w.WorldLine).where(w.WorldLine.owner_id == domain["owner"]))}
    receipt = restored(domain, tmp_path); ids = receipt["idMap"]
    assert receipt["counts"]["playback_position"] == 3
    with SessionLocal() as db, client(domain["target"]) as http:
        rows = list(db.scalars(select(m.PlaybackPosition).where(m.PlaybackPosition.owner_id == domain["target"])))
        assert len(rows) == 3
        for row in rows:
            old = before[row.kind]; result = fetch(http, row)
            assert row.id == ids[("playback_position", old["id"])] and row.id != old["id"]
            assert result["version"] == row.version == old["version"]
            assert row.scope_key == digest(canonical([row.kind, row.line_id, row.content_revision]))
            assert row.context["storyId"] == row.story_id
            assert row.completed_at == old["completed_at"] and row.updated_at == old["updated_at"]
            if row.kind == "world":
                assert row.story_id == "world-"+ids[("world", domain["world"])]
                assert row.last_completed_anchor["checkpointId"] == ids[("checkpoint", domain["root_checkpoint"])]
                assert row.max_heard_anchor["checkpointId"] == ids[("checkpoint", domain["checkpoint"])]
                assert row.paragraph_id == row.last_completed_anchor["checkpointId"]+":0"
                assert row.context["characterId"] != old["context"]["characterId"]
                assert row.context["characterId"] == db.get(w.WorldCheckpoint, row.content_revision).state["playerId"]
            elif row.kind == "draft":
                assert row.content_revision == ids[("generation_batch", history["batch"])]+":2"
                assert row.paragraph_id == row.last_completed_anchor["contentId"]
                assert row.generation_id == ids[("generation_batch", history["batch"])]
            else:
                assert row.content_revision == ids[("revision", domain["revision"])]
                assert row.paragraph_id == ids[("paragraph", domain["block"])]
                assert row.last_completed_anchor["end"] == utf16_len(domain["prose"])
        fresh = db.get(m.UserSettings, domain["target"])
        assert fresh.speech_preferences == {"rate": 1.5, "voiceId": "target-device-voice", "sleepMinutes": 15, "stopAtChapterEnd": True}
        old = db.scalar(select(m.Progress).where(m.Progress.owner_id == domain["owner"]))
        assert (old.anchor, old.max_observed_anchor, old.version) == preserved_progress
        copied = db.scalar(select(m.Progress).where(m.Progress.owner_id == domain["target"]))
        assert copied.version == old.version and copied.anchor["end"] == old.anchor["end"]
        assert {row.id: row.head_checkpoint_id for row in db.scalars(select(w.WorldLine).where(w.WorldLine.owner_id == domain["owner"]))} == old_heads
        assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id == domain["target"])) == 0


def test_restored_position_cas_receipt_and_other_owner_read_are_isolated(domain, tmp_path):
    before, _ = seed_ranges(domain); restored(domain, tmp_path)
    with SessionLocal() as db:
        row = db.scalar(select(m.PlaybackPosition).where(m.PlaybackPosition.owner_id == domain["target"], m.PlaybackPosition.kind == "reader"))
        copied = body(row.kind, row.context, row.paragraph_id, row.last_completed_anchor,
            expectedVersion=row.version, contentRevision=row.content_revision, ended=True)
        query = {"storyId": row.story_id, "kind": row.kind, "mode": row.mode, "lineId": row.line_id, "contentRevision": row.content_revision}
    with client(domain["target"]) as http, client(domain["stranger"]) as stranger:
        first = http.put("/api/reader/v1/playback/position", json=copied, headers={"Idempotency-Key": "restored-listening-cas"})
        duplicate = http.put("/api/reader/v1/playback/position", json=copied, headers={"Idempotency-Key": "restored-listening-cas"})
        stale = http.put("/api/reader/v1/playback/position", json=copied, headers={"Idempotency-Key": "restored-listening-stale"})
        assert first.status_code == duplicate.status_code == 200 and first.json() == duplicate.json()
        assert first.json()["version"] == before["reader"]["version"]+1 and stale.status_code == 409
        assert stranger.get("/api/reader/v1/playback/position", params=query).status_code == 404
    with SessionLocal() as db:
        old = db.get(m.PlaybackPosition, before["reader"]["id"])
        assert old.version == before["reader"]["version"] and not old.ended


def test_explicit_book_and_world_view_ranges_restore_without_advancing_progress_or_head(domain, tmp_path):
    with SessionLocal() as db:
        other = db.scalar(select(m.Branch).where(m.Branch.book_id == domain["book"], m.Branch.id != domain["branch"]))
        world_line = db.get(w.WorldCheckpoint, domain["checkpoint"]).line_id
        before_progress = db.scalar(select(m.Progress).where(m.Progress.owner_id == domain["owner"])).version
        before_head = db.get(w.WorldLine, world_line).head_checkpoint_id
        selections = [("book", domain["book"], other.id, other.head_revision_id),
            ("world", domain["world"], world_line, domain["checkpoint"])]
    with client(domain["owner"]) as http:
        for kind, resource, line, node in selections:
            response = http.post("/api/reader/v1/stories/"+kind+"-"+resource+"/compare/reveal",
                json={"confirmed": True, "kind": kind, "resourceId": resource, "lineId": line, "nodeId": node, "expectedVersion": 0},
                headers={"Idempotency-Key": "view-range-"+kind})
            assert response.status_code == 200, response.text
    receipt = restored(domain, tmp_path); ids = receipt["idMap"]
    with SessionLocal() as db:
        originals = list(db.scalars(select(ComparisonExposure).where(ComparisonExposure.owner_id == domain["owner"])))
        assert len(originals) == receipt["counts"]["comparison_exposure"] == 2
        for old in originals:
            copy = db.get(ComparisonExposure, ids[("comparison_exposure", old.id)])
            assert copy.owner_id == domain["target"] and copy.version == old.version
            assert copy.resource_id == ids[(old.kind, old.resource_id)]
            assert copy.line_id == ids[("branch" if old.kind == "book" else "world_line", old.line_id)]
            assert copy.node_id == ids[("revision" if old.kind == "book" else "checkpoint", old.node_id)]
            assert copy.scope_key == digest(canonical([copy.kind, copy.resource_id, copy.line_id, copy.node_id]))
            assert copy.anchor["revisionId" if old.kind == "book" else "checkpointId"] == copy.node_id
        assert db.get(w.WorldLine, world_line).head_checkpoint_id == before_head
        assert db.scalar(select(m.Progress).where(m.Progress.owner_id == domain["owner"])).version == before_progress
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.owner_id == domain["target"], m.Task.state.not_in(["succeeded", "failed", "cancelled"]))) == 0


@pytest.mark.parametrize("defect", ["max-before-last", "reader-partial", "world-wrong-character", "view-wrong-line"])
def test_invalid_completed_or_view_scope_rolls_back_the_whole_new_copy(domain, tmp_path, defect):
    seed_ranges(domain)
    with SessionLocal() as db:
        if defect == "view-wrong-line":
            other = db.scalar(select(m.Branch).where(m.Branch.book_id == domain["book"], m.Branch.id != domain["branch"]))
            db.add(ComparisonExposure(owner_id=domain["owner"], scope_key=digest(defect), kind="book", resource_id=domain["book"],
                line_id=other.id, node_id=domain["revision"], anchor=domain["anchor"]))
        else:
            row = db.scalar(select(m.PlaybackPosition).where(m.PlaybackPosition.owner_id == domain["owner"],
                m.PlaybackPosition.kind == ("reader" if defect == "reader-partial" else "world")))
            if defect == "max-before-last":
                row.last_completed_anchor, row.max_heard_anchor = row.max_heard_anchor, row.last_completed_anchor
                row.paragraph_id = row.last_completed_anchor["checkpointId"]+":0"
            elif defect == "reader-partial":
                row.last_completed_anchor = {**row.last_completed_anchor, "start": 0, "end": 2}
            else:
                point = db.get(w.WorldCheckpoint, domain["checkpoint"])
                wrong = next(entry["id"] for entry in point.definition["entities"] if entry["id"] != point.state["playerId"])
                row.context = {**row.context, "characterId": wrong}
        db.commit()
    original = encrypted(domain, tmp_path)
    with original.open("rb") as source, prepare_backup(source, "synthetic-test-password", tmp_path/"import") as prepared:
        with SessionLocal() as db, pytest.raises(ArchiveError, match="BACKUP_(PLAYBACK|COMPARE)_INVALID"):
            restore_domain(db, prepared, domain["target"])
    assert_empty_target(domain)


def test_saved_reader_publication_survives_deleted_world_root_in_private_backup(domain, tmp_path):
    # The older portable fixture's hand-written chapter parts predate its
    # source/html projection. Match the actual frozen-source producer shape
    # before exercising the publication API rather than accepting malformed
    # fixture inputs as a product publication failure.
    with SessionLocal() as db:
        world = db.get(w.World, domain["world"]); source = deepcopy(world.source)
        parts = {value["blockId"]: value for value in source["blocks"]}
        for chapter in source["chapters"]:
            chapter["blocks"] = [deepcopy(parts[value["blockId"]]) for value in chapter["blocks"]]
        world.source = source; db.commit()
    with client(domain["owner"]) as http:
        publication = http.post(f"/api/reader/v1/worlds/{domain['world']}/checkpoints/{domain['checkpoint']}/publish",
            headers={"Idempotency-Key": "owned-listening-publication"})
        assert publication.status_code == 200, publication.text
        published = publication.json()
    with SessionLocal() as db:
        revision = db.get(m.Revision, published["revisionId"])
        paragraph = db.get(m.ContentBlock, revision.manifest[-1]["blocks"][-1])
        anchor = make_anchor(published["bookId"], published["branchId"], revision.id, paragraph, utf16_len(paragraph.text))
        value = body("reader", context("world-"+domain["world"], bookId=published["bookId"],
            readerBranchId=published["branchId"], anchor=anchor), paragraph.id, anchor)
    save(domain["owner"], value, "owned-published-reader-complete")
    # Existing actual deletion detaches the publication and retains its reader
    # family. The source reader row still records its old world entry string.
    with client(domain["owner"]) as http:
        response = http.delete("/api/reader/v1/worlds/"+domain["world"])
        assert response.status_code == 200, response.text
    with SessionLocal() as db:
        old = db.scalar(select(m.PlaybackPosition).where(m.PlaybackPosition.owner_id == domain["owner"]))
        assert old.story_id == "world-"+domain["world"] and old.kind == "reader"
    receipt = restored({**domain, "book": published["bookId"]}, tmp_path); ids = receipt["idMap"]
    with SessionLocal() as db, client(domain["target"]) as http:
        row = db.get(m.PlaybackPosition, ids[("playback_position", old.id)])
        assert row.story_id == "book-"+ids[("book", published["bookId"])]
        assert row.context["storyId"] == row.story_id and row.book_id == ids[("book", published["bookId"])]
        assert fetch(http, row)["position"]["lastCompletedAnchor"]["blockId"] == ids[("paragraph", paragraph.id)]
        assert db.scalar(select(func.count()).select_from(w.World).where(w.World.owner_id == domain["target"])) == 0


@pytest.mark.parametrize("mode", ["play", "chat"])
def test_actual_saved_resume_remaps_local_character_without_losing_selected_time(domain, tmp_path, mode):
    with SessionLocal() as db:
        if mode == "play":
            point = db.get(w.WorldCheckpoint, domain["root_checkpoint"])
            scope = {"storyId": "world-"+domain["world"], "mode": "play", "perspective": "actor", "entryOrigin": "worlds",
                "worldId": domain["world"], "worldLineId": point.line_id, "checkpointId": point.id, "characterId": point.state["playerId"]}
        else:
            session = db.scalar(select(m.RoleSession).where(m.RoleSession.owner_id == domain["owner"]))
            scope = {"storyId": "book-"+domain["book"], "mode": "chat", "perspective": "reader", "entryOrigin": "conversation",
                "bookId": domain["book"], "readerBranchId": session.branch_id, "anchor": session.pinned_anchor,
                "characterId": session.character_id, "sessionId": session.id}
    with client(domain["owner"]) as http:
        response = http.put("/api/reader/v1/stories/"+scope["storyId"]+"/resume", json={"expectedVersion": 0, "context": scope})
        assert response.status_code == 200, response.text
    receipt = restored(domain, tmp_path); ids = receipt["idMap"]
    story_id = ("world-"+ids[("world", domain["world"])]) if mode == "play" else ("book-"+ids[("book", domain["book"])])
    with SessionLocal() as db, client(domain["target"]) as http:
        saved = db.scalar(select(m.ResumeTarget).where(m.ResumeTarget.owner_id == domain["target"], m.ResumeTarget.mode == mode))
        assert saved.context["characterId"] != scope["characterId"]
        if mode == "play":
            target = db.get(w.WorldCheckpoint, ids[("checkpoint", domain["root_checkpoint"])])
            assert saved.context["characterId"] == target.state["playerId"]
            assert saved.context["checkpointId"] == target.id
        else:
            target = db.get(m.RoleSession, ids[("role_session", scope["sessionId"])])
            assert saved.context["sessionId"] == target.id and saved.context["characterId"] == target.character_id
        result = http.get("/api/reader/v1/stories/"+story_id)
        assert result.status_code == 200, result.text
        assert result.json()["resume"][mode]["context"] == saved.context


def test_archived_world_keeps_saved_play_time_and_requires_explicit_restore(domain, tmp_path):
    with SessionLocal() as db:
        point = db.get(w.WorldCheckpoint, domain["root_checkpoint"])
        scope = {"storyId": "world-"+domain["world"], "mode": "play", "perspective": "actor", "entryOrigin": "worlds",
            "worldId": domain["world"], "worldLineId": point.line_id, "checkpointId": point.id, "characterId": point.state["playerId"]}
        version = db.get(w.World, domain["world"]).version
    with client(domain["owner"]) as http:
        saved = http.put("/api/reader/v1/stories/"+scope["storyId"]+"/resume", json={"expectedVersion": 0, "context": scope})
        assert saved.status_code == 200, saved.text
        archived = http.patch("/api/reader/v1/worlds/"+domain["world"], json={"expectedVersion": version, "status": "archived"})
        assert archived.status_code == 200, archived.text
    receipt = restored(domain, tmp_path); ids = receipt["idMap"]; world_id = ids[("world", domain["world"])]
    with SessionLocal() as db, client(domain["target"]) as http:
        world = db.get(w.World, world_id)
        assert world.status == "archived"
        old_head = db.get(w.WorldLine, world.active_line_id).head_checkpoint_id
        saved = db.scalar(select(m.ResumeTarget).where(m.ResumeTarget.owner_id == domain["target"], m.ResumeTarget.mode == "play"))
        assert saved.context["checkpointId"] == ids[("checkpoint", domain["root_checkpoint"])]
        before = http.get("/api/reader/v1/stories/world-"+world_id)
        assert before.status_code == 200 and not before.json()["resume"]["play"]["available"]
        enabled = http.patch("/api/reader/v1/worlds/"+world_id, json={"expectedVersion": world.version, "status": "active"})
        assert enabled.status_code == 200, enabled.text
        after = http.get("/api/reader/v1/stories/world-"+world_id)
        assert after.status_code == 200 and after.json()["resume"]["play"]["context"] == saved.context
    with SessionLocal() as db:
        assert db.get(w.WorldLine, ids[("world_line", scope["worldLineId"])]).head_checkpoint_id == old_head


@pytest.mark.parametrize("mode", ["play", "chat"])
def test_cross_scope_saved_resume_cannot_leave_half_a_restored_story(domain, tmp_path, mode):
    with SessionLocal() as db:
        if mode == "play":
            head = db.get(w.WorldCheckpoint, domain["checkpoint"])
            other = db.get(w.WorldCheckpoint, domain["fork"])
            scope = {"storyId": "world-"+domain["world"], "mode": "play", "perspective": "actor", "entryOrigin": "worlds",
                "worldId": domain["world"], "worldLineId": other.line_id, "checkpointId": head.id, "characterId": head.state["playerId"]}
        else:
            session = db.scalar(select(m.RoleSession).where(m.RoleSession.owner_id == domain["owner"]))
            scope = {"storyId": "book-"+domain["book"], "mode": "chat", "perspective": "reader", "entryOrigin": "conversation",
                "bookId": domain["book"], "readerBranchId": session.branch_id, "anchor": session.pinned_anchor,
                "characterId": "local-1", "sessionId": session.id}
        db.add(m.ResumeTarget(owner_id=domain["owner"], story_id=scope["storyId"], mode=mode, context=scope)); db.commit()
    original = encrypted(domain, tmp_path)
    with original.open("rb") as source, prepare_backup(source, "synthetic-test-password", tmp_path/"import") as prepared:
        with SessionLocal() as db, pytest.raises(ArchiveError, match="BACKUP_RESUME_INVALID"):
            restore_domain(db, prepared, domain["target"])
    assert_empty_target(domain)
