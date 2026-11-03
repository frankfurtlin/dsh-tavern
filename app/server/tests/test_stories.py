"""Real PostgreSQL navigation/ownership/CAS; providers are strict mock only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text

from app.config import settings
from app.db import SessionLocal, engine
from app.domain import canonical, digest, make_anchor
from app.main import app
from app.models import Book, Branch, ContentBlock, KnowledgeSnapshot, Progress, ResumeTarget, Revision, RoleMessage, RoleSession, Task, UsageRecord, User, UserSettings, now
from app.world.models import World, WorldCheckpoint, WorldLine
from app.world.state import checkpoint_hash
from app.world.worker import process
from .helpers import BASE, read_chapter, observe
from .test_world import start, post
from .test_world_security import isolated_storage_and_mock_only, _login_other


def story_id(book):
    return "book-" + book["bookId"]


def read_context(book, anchor):
    return {"storyId": story_id(book), "mode": "read", "bookId": book["bookId"],
        "readerBranchId": book["branchId"], "anchor": anchor,
        "perspective": "reader", "entryOrigin": "reader"}


def play_context(book, world, line, checkpoint=None):
    return {"storyId": story_id(book), "mode": "play", "worldId": world["id"], "worldLineId": line["id"],
        "checkpointId": checkpoint or line["headCheckpointId"], "characterId": line["head"]["state"]["playerId"],
        "perspective": "actor", "entryOrigin": "worlds"}


def put(client, context, version=0):
    return client.put(BASE + "/stories/" + context["storyId"] + "/resume", json={"expectedVersion": version, "context": context})


def stored_boundary(db, demo):
    return [(p.id, p.anchor, p.max_observed_anchor, p.version, p.updated_at) for p in db.scalars(select(Progress).where(Progress.book_id == demo["bookId"]))]


def test_book_entry_is_readable_without_model_and_get_is_pure(client, reader, demo):
    at = read_chapter(client, demo)["blocks"][0]["startAnchor"]
    expected = {**read_context(demo, at), "entryOrigin": "library"}
    for _ in range(2):
        response = client.get(BASE + "/stories/" + story_id(demo))
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["id"] == story_id(demo) and result["kind"] == "book"
        assert result["resume"]["read"] == {"version": 0, "context": expected, "updatedAt": result["resume"]["read"]["updatedAt"], "available": True}
        assert result["resume"]["play"]["context"] is None
        assert result["resume"]["chat"]["context"] is None
        assert result["worlds"] == [] and result["canCreateWorld"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ResumeTarget)) == 0
        assert db.scalar(select(func.count()).select_from(Progress)) == 0
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_independent_world_without_reader_source_has_a_safe_entry(client, reader):
    with SessionLocal() as db:
        world = World(owner_id=reader["user"]["id"], name="独立自编世界", source_title="独立自编世界",
            source={}, definition={}, status="preparing")
        db.add(world); db.commit(); wid = world.id
    response = client.get(BASE + "/stories/world-" + wid)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["source"] is None and result["kind"] == "world"
    assert all(not target["available"] for target in result["resume"].values())
    assert result["worlds"][0]["sourceRemoved"] is False
    assert result["worlds"][0]["resumeContext"] is None
    assert client.get(BASE + "/stories").json()["items"][0]["id"] == "world-" + wid


def test_three_modes_have_independent_versions_and_no_authoritative_writes(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    at = read_chapter(client, demo)["blocks"][-1]["endAnchor"]
    with SessionLocal() as db:
        owner = reader["user"]["id"]
        snapshot = KnowledgeSnapshot(owner_id=owner, book_id=demo["bookId"], branch_id=demo["branchId"],
            revision_id=demo["revisionId"], boundary=at, scope_hash=digest("navigation-fixture"), profile_version="fixture-only",
            data={"characters": [{"id": "nav-person", "displayName": "自编人物"}], "facts": []}, completeness="complete")
        db.add(snapshot); db.flush()
        session = RoleSession(owner_id=owner, book_id=demo["bookId"], branch_id=demo["branchId"],
            pinned_revision_id=demo["revisionId"], pinned_anchor=at, character_id="nav-person", character_name="自编人物", snapshot_id=snapshot.id)
        db.add(session); db.commit()
        sid = session.id
        before = {"progress": stored_boundary(db, demo), "head": db.get(WorldLine, line["id"]).head_checkpoint_id,
            "world": deepcopy(db.get(World, world["id"]).definition), "calls": db.scalar(select(func.count()).select_from(UsageRecord)),
            "tasks": db.scalar(select(func.count()).select_from(Task)), "blocks": db.scalar(select(func.count()).select_from(ContentBlock))}
    contexts = [read_context(demo, at), play_context(demo, world, line),
        {**read_context(demo, at), "mode": "chat", "sessionId": sid, "characterId": "nav-person", "entryOrigin": "conversation"}]
    for context in contexts:
        validated = client.post(BASE + "/stories/" + story_id(demo) + "/context/validate", json=context)
        assert validated.status_code == 200 and validated.json() == {"valid": True, "context": context}, validated.text
        saved = put(client, context)
        assert saved.status_code == 200 and saved.json()["version"] == 1, saved.text
    saved = put(client, contexts[0], 1)
    assert saved.status_code == 200 and saved.json()["version"] == 2
    entry = client.get(BASE + "/stories/" + story_id(demo)).json()
    assert {mode: row["version"] for mode, row in entry["resume"].items()} == {"read": 2, "play": 1, "chat": 1}
    assert entry["worlds"][0]["resumeContext"] == contexts[1]
    assert set(entry["worlds"][0]) >= {"playerName", "timeLabel", "locationName"}
    with SessionLocal() as db:
        assert stored_boundary(db, demo) == before["progress"]
        assert db.get(WorldLine, line["id"]).head_checkpoint_id == before["head"]
        assert db.get(World, world["id"]).definition == before["world"]
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before["calls"]
        assert db.scalar(select(func.count()).select_from(Task)) == before["tasks"]
        assert db.scalar(select(func.count()).select_from(ContentBlock)) == before["blocks"]
        assert db.scalar(select(func.count()).select_from(ResumeTarget)) == 3


def test_history_resume_does_not_move_head_or_import_sibling_checkpoint(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    with SessionLocal() as db:
        old = db.get(WorldCheckpoint, line["headCheckpointId"]).parent_checkpoint_id
    context = play_context(demo, world, line, checkpoint=old)
    assert put(client, context).status_code == 200
    assert client.get(BASE + "/stories/" + story_id(demo)).json()["resume"]["play"]["context"]["checkpointId"] == old
    other = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": line["headCheckpointId"], "mode": "continue"}, "nav-fork")
    bad = {**context, "checkpointId": other["headCheckpointId"]}
    response = put(client, bad, 1)
    assert response.status_code == 404 and response.json()["code"] == "RESOURCE_NOT_FOUND"
    with SessionLocal() as db:
        assert db.get(WorldLine, line["id"]).head_checkpoint_id == line["headCheckpointId"]
        assert db.scalar(select(ResumeTarget)).version == 1


@pytest.mark.parametrize("change", [
    {"perspective": "actor"}, {"mode": "play"}, {"mode": "chat"},
    {"ownerId": "fake"}, {"sessionId": "f" * 32}, {"worldId": "f" * 32},
    {"anchor": None}, {"readerBranchId": None}, {"characterId": "fake"},
])
def test_invalid_mode_combinations_do_not_write_or_generate(client, reader, demo, change):
    context = {**read_context(demo, read_chapter(client, demo)["blocks"][0]["startAnchor"]), **change}
    response = put(client, context)
    assert response.status_code == 422, response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ResumeTarget)) == 0
        assert db.scalar(select(func.count()).select_from(Task)) == 0


def test_invalid_quote_or_utf16_surrogate_is_rejected(client, reader, demo):
    with SessionLocal() as db:
        branch = db.get(Branch, demo["branchId"])
        revision = db.get(Revision, demo["revisionId"])
        block = db.get(ContentBlock, revision.manifest[0]["blocks"][0])
        # Only synthetic example data in the isolated fixture; persist an emoji
        # to exercise actual PostgreSQL/API offsets rather than mirror a helper.
        block.text = "甲😀乙"
        block.content_hash = digest(block.text)
        at = make_anchor(demo["bookId"], branch.id, revision.id, block, 1)
        db.commit()
    valid = read_context(demo, at)
    assert put(client, valid).status_code == 200
    for bad_anchor in [{**at, "start": 2, "end": 2}, {**at, "quoteHash": "0" * 64}, {**at, "start": True}]:
        response = put(client, {**valid, "anchor": bad_anchor}, 1)
        assert response.status_code == 422, response.text
    with SessionLocal() as db:
        assert db.scalar(select(ResumeTarget)).version == 1


def test_foreign_owner_and_same_owner_wrong_story_are_opaque(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    original_header = client.headers["Authorization"]
    own = read_context(demo, read_chapter(client, demo)["blocks"][0]["startAnchor"])
    _login_other(client, "navigation_other")
    for method, suffix, body in [("get", "", None), ("put", "/resume", {"expectedVersion": 0, "context": own}),
            ("post", "/context/validate", own)]:
        response = getattr(client, method)(BASE + "/stories/" + story_id(demo) + suffix, **({"json": body} if body else {}))
        assert response.status_code == 404 and response.json()["code"] == "RESOURCE_NOT_FOUND"
    own_list = client.get(BASE + "/stories").json()
    assert own_list["items"] == []
    client.headers["Authorization"] = original_header
    with SessionLocal() as db:
        another = Book(owner_id=reader["user"]["id"], title="另一份自编空壳", source_hash=digest("another"),
            source_id="a"*32, original_branch_id="b"*32, original_revision_id="c"*32)
        db.add(another); db.commit(); another_id = another.id
    bad = {**play_context(demo, world, line), "storyId": "book-" + another_id}
    response = put(client, bad)
    assert response.status_code == 404 and response.json()["code"] == "RESOURCE_NOT_FOUND"


def test_first_write_concurrency_uses_actual_postgres_cas(client, reader, demo):
    context = read_context(demo, read_chapter(client, demo)["blocks"][0]["startAnchor"])
    barrier = Barrier(2)
    authorization = client.headers["Authorization"]
    def concurrent():
        with TestClient(app, raise_server_exceptions=False) as independent:
            independent.headers["Authorization"] = authorization
            barrier.wait(timeout=10)
            result = put(independent, context)
            return result.status_code, result.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: concurrent(), range(2)))
    assert sorted(status for status, _ in outcomes) == [200, 409], outcomes
    conflict = next(body for status, body in outcomes if status == 409)
    assert conflict["code"] == "VERSION_CONFLICT" and conflict["details"]["currentVersion"] == 1
    with SessionLocal() as db:
        rows = list(db.scalars(select(ResumeTarget)))
        assert len(rows) == 1 and rows[0].version == 1


def test_deleted_and_archived_targets_fall_back_without_writing(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    other = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": line["headCheckpointId"], "mode": "continue"}, "nav-survivor")
    assert put(client, play_context(demo, world, line)).status_code == 200
    removed = client.request(
        "DELETE", f"/api/world/v1/worlds/{world['id']}/lines/{line['id']}", json={"expectedVersion": line["version"]}, headers={"Idempotency-Key": "nav-delete-line"})
    assert removed.status_code == 200, removed.text
    current = client.get(BASE + "/stories/" + story_id(demo)).json()["resume"]["play"]
    assert current["version"] == 1 and current["context"]["worldLineId"] == other["id"]
    saved_world = client.get("/api/world/v1/worlds/" + world["id"]).json()
    archived = client.patch("/api/world/v1/worlds/" + world["id"], json={"expectedVersion": saved_world["version"], "status": "archived"})
    assert archived.status_code == 200, archived.text
    result = client.get(BASE + "/stories/" + story_id(demo)).json()
    assert result["resume"]["play"]["version"] == 1 and result["resume"]["play"]["available"] is False
    with SessionLocal() as db:
        assert db.scalar(select(ResumeTarget)).context["worldLineId"] == line["id"]
        assert db.scalar(select(ResumeTarget)).version == 1


def test_publication_is_linked_both_directions_and_survives_world_delete(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    published = post(client, f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish", {}, "nav-publish")
    context = {**read_context({"bookId": published["bookId"], "branchId": published["branchId"]}, published["readingAnchor"]),
        "storyId": "world-" + world["id"]}
    assert put(client, context).status_code == 200
    world_entry = client.get(BASE + "/stories/world-" + world["id"]).json()
    assert world_entry["resume"]["read"]["context"] == context
    book_entry = client.get(BASE + "/stories/" + story_id(demo)).json()
    assert book_entry["worlds"][0]["id"] == world["id"]
    again = post(client, f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish", {}, "nav-publish-again")
    assert again == published
    before = client.get(BASE + "/branches/" + published["branchId"] + "/chapters", params={"revisionId": published["revisionId"]}).json()
    info = client.get("/api/world/v1/worlds/" + world["id"]).json()
    deleted = client.request("DELETE", "/api/world/v1/worlds/" + world["id"], json={"expectedVersion": info["version"]}, headers={"Idempotency-Key": "nav-world-delete"})
    assert deleted.status_code == 200, deleted.text
    assert client.get(BASE + "/stories/world-" + world["id"]).status_code == 404
    assert client.get(BASE + "/stories/" + story_id(demo)).json()["worlds"] == []
    assert client.get(BASE + "/branches/" + published["branchId"] + "/chapters", params={"revisionId": published["revisionId"]}).json() == before


def test_recent_related_roots_resume_without_double_writes_or_other_branch_read_progress(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    book_at = read_chapter(client, demo)["blocks"][0]["startAnchor"]
    assert put(client, read_context(demo, book_at)).status_code == 200
    old_play = play_context(demo, world, line)
    assert put(client, old_play).status_code == 200
    # Viewing the world prefers its own saved history over original-book progress.
    wid = "world-" + world["id"]
    assert client.get(BASE + "/stories/" + wid).json()["resume"]["read"]["context"]["worldLineId"] == line["id"]
    changed = post(client, f"/worlds/{world['id']}/fork", {"checkpointId": line["headCheckpointId"], "mode": "continue"}, "nav-root-fork")
    new_play = {**play_context(demo, world, changed), "storyId": wid}
    assert put(client, new_play).status_code == 200
    resumed = client.get(BASE + "/stories/" + story_id(demo)).json()["resume"]["play"]
    assert resumed["version"] == 1 and resumed["context"] == {**new_play, "storyId": story_id(demo)}
    published = post(client, f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish", {}, "nav-root-publish")
    read = {**read_context({"bookId": published["bookId"], "branchId": published["branchId"]}, published["readingAnchor"]), "storyId": wid}
    assert put(client, read).status_code == 200
    book_resume = client.get(BASE + "/stories/" + story_id(demo)).json()["resume"]["read"]
    assert published["bookId"] != demo["bookId"]
    assert book_resume["version"] == 1 and book_resume["context"] == read_context(demo, book_at)
    # Reader navigation on that publication is visible from its world entry;
    # a newer position in an unrelated original branch cannot hijack it.
    book_publication = {**read, "storyId": "book-" + published["bookId"], "entryOrigin": "library"}
    assert put(client, book_publication, 0).status_code == 200
    assert client.get(BASE + "/stories/" + wid).json()["resume"]["read"]["context"] == {**book_publication, "storyId": wid}
    assert put(client, read_context(demo, book_at), 1).status_code == 200
    assert client.get(BASE + "/stories/" + wid).json()["resume"]["read"]["context"] == {**book_publication, "storyId": wid}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ResumeTarget)) == 5
        assert db.get(WorldLine, line["id"]).head_checkpoint_id == line["headCheckpointId"]
        assert db.scalar(select(ResumeTarget).where(ResumeTarget.story_id == story_id(demo), ResumeTarget.mode == "play")).context == old_play


def test_removed_source_world_can_read_and_play_its_frozen_history(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    context = {**play_context(demo, world, line), "storyId": "world-" + world["id"]}
    assert put(client, context).status_code == 200
    with SessionLocal() as db:
        db.delete(db.get(Book, demo["bookId"])); db.commit()
    assert client.get(BASE + "/stories/" + story_id(demo)).status_code == 404
    response = client.get(BASE + "/stories/world-" + world["id"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["source"]["sourceRemoved"] and result["source"]["bookId"] is None
    assert result["resume"]["play"]["context"] == context
    assert result["resume"]["read"]["context"]["checkpointId"] == line["headCheckpointId"]


def test_list_paginates_books_and_worlds_and_reflects_metadata(client, reader, demo, profile):
    world, _ = start(client, reader, demo, profile)
    ids, cursor = [], None
    for _ in range(3):
        response = client.get(BASE + "/stories", params={"limit": 1, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        page = response.json()
        ids.extend(row["id"] for row in page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert set(ids) == {story_id(demo), "world-" + world["id"]} and len(ids) == 2
    changed = client.patch(BASE + "/books/" + demo["bookId"], json={"expectedVersion": 1, "title": "新的自编书名", "status": "archived"})
    assert changed.status_code == 200
    result = client.get(BASE + "/stories/" + story_id(demo)).json()
    assert result["title"] == "新的自编书名" and result["status"] == "archived" and not result["canCreateWorld"]
    assert client.get(BASE + "/stories", params={"status": "archived"}).json()["items"][0]["id"] == story_id(demo)


def test_disabled_world_feature_keeps_legacy_book_reading(client, reader, demo, monkeypatch):
    monkeypatch.setattr(settings, "interactive_world_enabled", False)
    entry = client.get(BASE + "/stories/" + story_id(demo)).json()
    assert entry["resume"]["read"]["available"] and not entry["canCreateWorld"] and entry["worlds"] == []
    assert client.get(BASE + "/stories/world-" + "f"*32).status_code == 404


def test_account_cascade_cleans_resume_targets(client, reader, demo):
    assert put(client, read_context(demo, read_chapter(client, demo)["blocks"][0]["startAnchor"])).status_code == 200
    with SessionLocal() as db:
        db.delete(db.get(User, reader["user"]["id"])); db.commit()
        assert db.scalar(select(func.count()).select_from(ResumeTarget)) == 0
    assert client.get(BASE + "/stories").status_code == 401


def test_migration_0004_to_0005_preserves_source_ids_and_old_settings(client, reader, demo):
    assert settings.ai_mode == "mock" and engine.url.database.endswith("_test")
    with SessionLocal() as db:
        source_before = [(b.id, b.text, b.content_hash) for b in db.scalars(select(ContentBlock).order_by(ContentBlock.id))]
        book_before = (db.get(Book, demo["bookId"]).source_hash, db.get(Book, demo["bookId"]).original_branch_id, db.get(Book, demo["bookId"]).original_revision_id)
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "0004")
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE user_settings SET font_size=22, line_height='1.9', theme='dark', version=7 WHERE owner_id=:owner"), {"owner": reader["user"]["id"]})
        command.upgrade(cfg, "0005")
        with SessionLocal() as db:
            # Inspect the actual historical0005 columns, not the latest ORM
            # containing additive fields that this old schema cannot have.
            pref = db.execute(text("SELECT reading_mode, reading_width, chapter_length, font_size, line_height, theme, version "
                "FROM user_settings WHERE owner_id=:owner"), {"owner": reader["user"]["id"]}).one()
            assert (pref.reading_mode, pref.reading_width, pref.chapter_length) == ("scroll", 36, "standard")
            assert (pref.font_size, pref.line_height, pref.theme, pref.version) == (22, "1.9", "dark", 7)
            assert [(b.id, b.text, b.content_hash) for b in db.scalars(select(ContentBlock).order_by(ContentBlock.id))] == source_before
            book = db.get(Book, demo["bookId"])
            assert (book.source_hash, book.original_branch_id, book.original_revision_id) == book_before
            assert db.scalar(select(func.count()).select_from(ResumeTarget)) == 0
        assert client.get("/health/ready").status_code == 503  # Historical0005 is not the current schema.
        command.upgrade(cfg, "head")
        assert client.get("/health/ready").status_code == 200
    finally:
        command.upgrade(cfg, "head")


def test_legacy_settings_requests_preserve_new_reading_preferences(client, reader):
    updated = client.put(BASE + "/settings", json={"expectedVersion": 1, "fontSize": 32,
        "lineHeight": 1.8, "theme": "dark", "readingMode": "page", "readingWidth": 40, "chapterLength": "long"})
    assert updated.status_code == 200, updated.text
    legacy = client.put(BASE + "/settings", json={"expectedVersion": 2, "fontSize": 20, "lineHeight": 1.6, "theme": "light"})
    assert legacy.status_code == 200, legacy.text
    assert (legacy.json()["readingMode"], legacy.json()["readingWidth"], legacy.json()["chapterLength"]) == ("page", 40, "long")
    assert legacy.json()["version"] == 3
    conflict = client.put(BASE + "/settings", json={"expectedVersion": 2, "fontSize": 20, "lineHeight": 1.6, "theme": "light", "readingMode": "slide"})
    assert conflict.status_code == 409 and conflict.json()["code"] == "SETTINGS_CONFLICT"
    assert client.get(BASE + "/settings").json()["readingMode"] == "page"


def publication_scope_fixture(client, reader, demo, profile):
    """Two UI/API-created worlds share one source, but not a publication family."""
    first, first_line = start(client, reader, demo, profile)
    at = read_chapter(client, demo)["blocks"][-1]["endAnchor"]
    second_job = post(client, "/worlds", {"anchor": at, "rules": {}}, "scope-second-world")
    process(second_job["taskId"])
    second = client.get("/api/world/v1/worlds/" + second_job["worldId"]).json()
    character = next(c for c in second["characters"] if c["available"])
    begun = post(client, f"/worlds/{second['id']}/begin", {"characterId": character["id"]}, "scope-second-begin")
    process(begun["taskId"])
    second_line = client.get(f"/api/world/v1/worlds/{second['id']}/lines/{begun['lineId']}").json()
    publications = [post(client, f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish", {}, "scope-publish-" + str(i))
        for i, (world, line) in enumerate(((first, first_line), (second, second_line)))]
    owner = reader["user"]["id"]
    contexts = []
    with SessionLocal() as db:
        original = db.get(Revision, demo["revisionId"])
        for name, parent in (("unrelated reader child", demo["branchId"]), ("published reader child", publications[0]["branchId"])):
            parent_revision = original if parent == demo["branchId"] else db.get(Revision, publications[0]["revisionId"])
            parent_book_id = db.get(Branch, parent).book_id
            branch = Branch(owner_id=owner, book_id=parent_book_id, parent_branch_id=parent, name=name, head_revision_id="0"*32)
            db.add(branch); db.flush()
            revision = Revision(owner_id=owner, branch_id=branch.id, manifest=deepcopy(parent_revision.manifest),
                content_hash=parent_revision.content_hash, provenance={"operation": "synthetic-reader-child"})
            db.add(revision); db.flush()
            branch.head_revision_id = revision.id
            block = db.get(ContentBlock, revision.manifest[0]["blocks"][0])
            contexts.append(read_context({"bookId": parent_book_id, "branchId": branch.id},
                make_anchor(parent_book_id, branch.id, revision.id, block)))
        db.commit()
    original_read = read_context(demo, at)
    published_read = [read_context({"bookId": p["bookId"], "branchId": p["branchId"]}, p["readingAnchor"]) for p in publications]
    allowed = [published_read[0], contexts[1]]
    denied = [published_read[1], contexts[0], original_read]
    # Pinned sessions are structural navigation fixtures, with no generation.
    with SessionLocal() as db:
        sessions = {}
        instant = now()
        for index, context in enumerate(allowed + denied):
            anchor = context["anchor"]
            snapshot = KnowledgeSnapshot(owner_id=owner, book_id=context["bookId"], branch_id=context["readerBranchId"],
                revision_id=anchor["revisionId"], boundary=anchor, scope_hash=digest("scope-session-" + str(index)),
                profile_version="synthetic-navigation", completeness="complete", data={"characters": [], "facts": []})
            db.add(snapshot); db.flush()
            session = RoleSession(owner_id=owner, book_id=context["bookId"], branch_id=context["readerBranchId"],
                pinned_revision_id=anchor["revisionId"], pinned_anchor=anchor, character_id="scope-person", character_name="Scope person",
                snapshot_id=snapshot.id, created_at=instant + timedelta(seconds=index))
            db.add(session); db.flush()
            sessions[context["readerBranchId"]] = session.id
            if index == 2:  # W2 has the latest real message, not just a new session.
                db.add(RoleMessage(owner_id=owner, session_id=session.id, turn_id="scope-message", sequence=1,
                    role="user", text="Own synthetic message", task_id="0"*32, created_at=instant + timedelta(seconds=20)))
        db.commit()
        baseline = {"tasks": db.scalar(select(func.count()).select_from(Task)), "usage": db.scalar(select(func.count()).select_from(UsageRecord)),
            "heads": {line.id: line.head_checkpoint_id for line in db.scalars(select(WorldLine))}, "progress": stored_boundary(db, demo)}
    return "world-" + first["id"], allowed, denied, sessions, baseline


@pytest.mark.parametrize("mode", ["read", "chat"])
def test_world_context_requires_its_own_publication_family(client, reader, demo, profile, mode):
    root, allowed, denied, sessions, baseline = publication_scope_fixture(client, reader, demo, profile)
    for index, original in enumerate(denied + allowed):
        context = deepcopy(original)
        if mode == "chat":
            context.update(mode="chat", sessionId=sessions[context["readerBranchId"]], characterId="scope-person", entryOrigin="conversation")
        # Their anchor/session is valid at its own book root; rejection must be
        # world-family authorization rather than malformed fixture data.
        valid = client.post(BASE + "/stories/" + context["storyId"] + "/context/validate", json=context)
        assert valid.status_code == 200, valid.text
        context["storyId"] = root
        validated = client.post(BASE + "/stories/" + root + "/context/validate", json=context)
        saved = put(client, context, 0 if index <= len(denied) else 1)
        expected = 404 if index < len(denied) else 200
        assert validated.status_code == expected, validated.text
        assert saved.status_code == expected, saved.text
        if expected == 404:
            assert validated.json()["code"] == saved.json()["code"] == "RESOURCE_NOT_FOUND"
            with SessionLocal() as db:
                assert db.scalar(select(func.count()).select_from(ResumeTarget)) == 0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ResumeTarget)) == 1
        assert db.scalar(select(ResumeTarget)).version == 2
        assert db.scalar(select(func.count()).select_from(Task)) == baseline["tasks"]
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == baseline["usage"]
        assert {line.id: line.head_checkpoint_id for line in db.scalars(select(WorldLine))} == baseline["heads"]
        assert stored_boundary(db, demo) == baseline["progress"]


def test_world_chat_fallback_never_selects_another_world_or_original_branch(client, reader, demo, profile):
    root, allowed, denied, sessions, baseline = publication_scope_fixture(client, reader, demo, profile)
    before = client.get(BASE + "/stories/" + root)
    assert before.status_code == 200, before.text
    chat = before.json()["resume"]["chat"]
    assert chat["version"] == 0 and chat["context"]["sessionId"] == sessions[allowed[1]["readerBranchId"]]
    assert chat["context"]["storyId"] == root
    with SessionLocal() as db:
        for context in allowed:
            db.get(RoleSession, sessions[context["readerBranchId"]]).status = "archived"
        db.commit()
    for _ in range(2):
        assert client.get(BASE + "/stories/" + root).json()["resume"]["chat"]["context"] is None
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ResumeTarget)) == 0
        assert db.scalar(select(func.count()).select_from(Task)) == baseline["tasks"]
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == baseline["usage"]
        assert {line.id: line.head_checkpoint_id for line in db.scalars(select(WorldLine))} == baseline["heads"]
        assert stored_boundary(db, demo) == baseline["progress"]


def test_unprepared_world_cannot_read_original_source_as_its_publication(client, reader, demo):
    at = read_chapter(client, demo)["blocks"][0]["startAnchor"]
    with SessionLocal() as db:
        world = World(owner_id=reader["user"]["id"], name="Synthetic preparing world", source_title="Synthetic source",
            source_book_id=demo["bookId"], source={"anchor": at}, definition={}, status="preparing")
        db.add(world); db.commit(); wid = world.id
    entry = client.get(BASE + "/stories/world-" + wid)
    assert entry.status_code == 200, entry.text
    assert entry.json()["resume"]["read"]["context"] is None
    assert client.get(BASE + "/stories/" + story_id(demo)).json()["resume"]["read"]["available"] is True
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ResumeTarget)) == 0
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_latest_actual_book_progress_outweighs_old_resume_without_changing_resume_cas(client, reader, demo):
    chapter = read_chapter(client, demo)
    a, b = chapter["blocks"][0]["startAnchor"], chapter["blocks"][-1]["endAnchor"]
    context = read_context(demo, a)
    assert put(client, context).status_code == 200
    assert client.get(BASE + "/stories/" + story_id(demo)).json()["resume"]["read"]["context"] == context
    actual = observe(client, reader, b)
    with SessionLocal() as db:
        before = stored_boundary(db, demo)
    entry = client.get(BASE + "/stories/" + story_id(demo)).json()["resume"]["read"]
    assert entry["version"] == 1 and entry["context"]["anchor"] == b
    assert entry["context"]["entryOrigin"] == "library"
    assert put(client, context, 0).status_code == 409
    assert put(client, context, 1).status_code == 200
    assert client.get(BASE + "/stories/" + story_id(demo)).json()["resume"]["read"]["context"] == context
    with SessionLocal() as db:
        assert stored_boundary(db, demo) == before
        assert db.scalar(select(ResumeTarget)).version == 2
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_world_read_time_is_server_owned_and_independent_of_camera_head_or_rename(client, reader, demo, profile):
    world, line = start(client, reader, demo, profile)
    root = "world-" + world["id"]
    with SessionLocal() as db:
        old_id = db.get(WorldCheckpoint, line["headCheckpointId"]).parent_checkpoint_id
    context = {"storyId": root, "mode": "read", "worldId": world["id"], "worldLineId": line["id"],
        "checkpointId": line["headCheckpointId"], "perspective": "reader", "entryOrigin": "graph"}
    assert put(client, context).status_code == 200
    requested_at = now()
    response = client.patch(f"/api/world/v1/worlds/{world['id']}/lines/{line['id']}", json={"expectedVersion": line["version"],
        "readingPosition": {"checkpointId": old_id, "scroll": 25, "updatedAt": "2999-01-01T00:00:00+00:00"}})
    assert response.status_code == 200, response.text
    updated = response.json()
    saved_at = datetime.fromisoformat(updated["readingPosition"]["updatedAt"])
    assert requested_at <= saved_at <= now()
    assert updated["headCheckpointId"] == line["headCheckpointId"]
    resumed = client.get(BASE + "/stories/" + root).json()["resume"]["read"]
    assert resumed["version"] == 1 and resumed["context"]["checkpointId"] == old_id
    assert datetime.fromisoformat(resumed["updatedAt"]) == saved_at
    camera = client.patch(f"/api/world/v1/worlds/{world['id']}/lines/{line['id']}", json={"expectedVersion": updated["version"], "camera": {"x": 7, "y": 8, "zoom": .8}, "name": "Renamed line"})
    assert camera.status_code == 200, camera.text
    info = client.get("/api/world/v1/worlds/" + world["id"]).json()
    renamed = client.patch("/api/world/v1/worlds/" + world["id"], json={"expectedVersion": info["version"], "name": "Renamed world"})
    assert renamed.status_code == 200, renamed.text
    assert client.get(BASE + "/stories/" + root).json()["resume"]["read"] == resumed
    assert put(client, context, 1).status_code == 200
    # A legacy position has no reliable read timestamp and cannot outrank an
    # explicit new resume. A later action head must not do so either.
    with SessionLocal() as db:
        db.get(WorldLine, line["id"]).reading_position = {"checkpointId": old_id, "scroll": 25}
        db.commit()
    job = post(client, f"/worlds/{world['id']}/lines/{line['id']}/actions", {"message": "I observe the harbour.",
        "kind": "observe", "expectedCheckpointId": line["headCheckpointId"], "turnId": "read-time-next"}, "read-time-next")
    process(job["taskId"])
    fresh = client.get(f"/api/world/v1/worlds/{world['id']}/lines/{line['id']}").json()
    assert fresh["headCheckpointId"] != line["headCheckpointId"]
    before_get = client.get(BASE + "/books/" + demo["bookId"] + "/progress", params={"branchId": demo["branchId"], "revisionId": demo["revisionId"]}).json()
    assert client.get(BASE + "/stories/" + root).json()["resume"]["read"]["context"] == context
    assert client.get(BASE + "/books/" + demo["bookId"] + "/progress", params={"branchId": demo["branchId"], "revisionId": demo["revisionId"]}).json() == before_get


def test_world_read_prefers_latest_own_publication_progress_but_not_other_world(client, reader, demo, profile):
    root, allowed, denied, sessions, baseline = publication_scope_fixture(client, reader, demo, profile)
    assert put(client, {**allowed[0], "storyId": root}).status_code == 200
    progress = observe(client, reader, allowed[1]["anchor"])
    assert client.get(BASE + "/stories/" + root).json()["resume"]["read"]["context"]["readerBranchId"] == allowed[1]["readerBranchId"]
    other = denied[0]["anchor"]
    existing = client.get(BASE + "/books/" + other["bookId"] + "/progress",
        params={"branchId": other["branchId"], "revisionId": other["revisionId"]}).json()
    observe(client, reader, other, expected=existing["version"], sequence=2)
    entry = client.get(BASE + "/stories/" + root).json()["resume"]["read"]
    assert entry["version"] == 1 and entry["context"]["readerBranchId"] == allowed[1]["readerBranchId"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == baseline["tasks"]
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == baseline["usage"]
        assert {line.id: line.head_checkpoint_id for line in db.scalars(select(WorldLine))} == baseline["heads"]


def test_recent_world_summaries_use_valid_resume_activity_not_rename_time(client, reader, demo, profile):
    root, allowed, denied, sessions, baseline = publication_scope_fixture(client, reader, demo, profile)
    first_id = root.removeprefix("world-")
    with SessionLocal() as db:
        second = db.scalar(select(World).where(World.id != first_id))
        second_id, second_version = second.id, second.version
        first = db.get(World, first_id)
        line_id = first.active_line_id
    assert client.get(BASE + "/stories/" + story_id(demo)).json()["worlds"][0]["id"] == second_id
    line = client.get(f"/api/world/v1/worlds/{first_id}/lines/{line_id}").json()
    context = {**play_context(demo, {"id": first_id}, line), "storyId": root}
    assert put(client, context).status_code == 200
    assert client.get(BASE + "/stories/" + story_id(demo)).json()["worlds"][0]["id"] == first_id
    renamed = client.patch("/api/world/v1/worlds/" + second_id, json={"expectedVersion": second_version, "name": "Recently renamed only"})
    assert renamed.status_code == 200, renamed.text
    assert client.get(BASE + "/stories/" + story_id(demo)).json()["worlds"][0]["id"] == first_id
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == baseline["tasks"]
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == baseline["usage"]
        assert {line.id: line.head_checkpoint_id for line in db.scalars(select(WorldLine))} == baseline["heads"]
