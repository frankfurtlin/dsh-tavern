"""Original template worlds, actual PostgreSQL/API, no real providers or books."""
from copy import deepcopy
import json
from uuid import UUID

import pytest
from sqlalchemy import select, func

from app.db import SessionLocal
from app.domain import canonical, digest, make_anchor
from app.models import Book, Branch, ContentBlock, Idempotency, KnowledgeSnapshot, ModelProfile, Progress, ResumeTarget, Revision, RoleSession, SourceDocument, Task, UsageRecord
from app.providers import MockAdapter
from app.world.models import World, WorldLine, WorldCheckpoint, WorldPublication
from app.world.state import initial_state, checkpoint_hash
from app.world.worker import process
from .helpers import BASE
from .test_world import API, post
from .test_world_security import isolated_storage_and_mock_only, _login_other

TEMPLATES = {"original-mystery-v1": "mystery", "original-fantasy-v1": "fantasy", "original-romance-v1": "romance", "douluo-forest-v1": "fantasy"}


def create(client, template="original-mystery-v1", key="standalone-create", **extra):
    response = client.post(API + "/worlds", json={"templateId": template, **extra}, headers={"Idempotency-Key": key})
    assert response.status_code in (200, 202), response.text
    return response.json()


@pytest.mark.parametrize("template,genre", TEMPLATES.items())
def test_template_creation_is_complete_without_profile_book_or_fake_task(client, reader, monkeypatch, template, genre):
    def forbidden(*args, **kwargs):
        raise AssertionError("Creating an original template must not call a model")
    monkeypatch.setattr(MockAdapter, "generate", forbidden)
    listing = client.get(API + "/templates")
    assert listing.status_code == 200, listing.text
    assert {item["id"]: item["genre"] for item in listing.json()["items"]} == TEMPLATES
    assert all(set(item) == {"id", "title", "genre", "description"} for item in listing.json()["items"])
    made = create(client, template)
    assert made == {"worldId": made["worldId"], "taskId": None, "status": "active"}
    info = client.get(API + "/worlds/" + made["worldId"])
    assert info.status_code == 200, info.text
    value = info.json()
    assert value["sourceKind"] == "standalone" and value["sourceAnchor"] is None
    assert value["sourceBookId"] is None and not value["sourceRemoved"]
    assert value["prepareTaskId"] is None and value["activeLineId"] is None
    assert value["characters"] and value["rules"]["genre"] == genre
    assert value["templateId"] == template
    with SessionLocal() as db:
        world = db.get(World, made["worldId"])
        assert world.source["kind"] == "standalone" and world.source["anchor"] is None
        assert world.source["blocks"] == world.source["chapters"] == []
        assert world.definition["entities"] and world.definition["initial"]
        ids = [e["id"] for e in world.definition["entities"]]
        assert all(UUID(identity).hex == identity for identity in ids)
        actor = value["characters"][0]["id"]
        definition, state = initial_state(world.definition, actor)
        assert state["playerId"] == actor and state["location"] in ids and state["timeLabel"]
        for model in (Book, Branch, Revision, ContentBlock, SourceDocument, Progress, ModelProfile, Task, UsageRecord):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        secrets = [e["hiddenGoal"] for e in world.definition["entities"] if e.get("hiddenGoal")]
        secrets += [l["text"] for l in world.definition["lore"] if actor not in l["knownBy"]]
        assert secrets and all(secret not in info.text and secret not in listing.text for secret in secrets)
    entry = client.get(BASE + "/stories/world-" + made["worldId"])
    assert entry.status_code == 200 and entry.json()["source"] is None
    assert all(not item["available"] for item in entry.json()["resume"].values())
    assert client.get(BASE + "/stories").json()["items"][0]["id"] == "world-" + made["worldId"]


def test_idempotent_creation_and_per_world_uuid_registries(client, reader):
    first = create(client)
    assert create(client) == first
    second = create(client, key="standalone-second")
    assert first["worldId"] != second["worldId"]
    with SessionLocal() as db:
        a, b = [db.get(World, item["worldId"]) for item in (first, second)]
        assert {e["id"] for e in a.definition["entities"]}.isdisjoint({e["id"] for e in b.definition["entities"]})
        assert {l["id"] for l in a.definition["lore"]}.isdisjoint({l["id"] for l in b.definition["lore"]})
        assert db.scalar(select(func.count()).select_from(World)) == 2
        assert db.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize("body", [{}, {"templateId": "nonexistent"}, {"templateId": ""},
    {"templateId": "original-mystery-v1", "fullBookReference": True},
    {"templateId": "original-mystery-v1", "ownerId": "other"}])
def test_invalid_standalone_creation_never_allocates_resources(client, reader, body):
    result = client.post(API + "/worlds", json=body, headers={"Idempotency-Key": "invalid-template"})
    assert result.status_code == 422, result.text
    with SessionLocal() as db:
        for model in (World, Book, Task, UsageRecord, Idempotency):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_anchor_and_template_are_exclusive_and_derived_creation_remains_unchanged(client, reader, demo, profile):
    from .test_world import start
    from .helpers import read_chapter
    at = read_chapter(client, demo)["blocks"][0]["startAnchor"]
    mixed = client.post(API + "/worlds", json={"templateId": "original-mystery-v1", "anchor": at}, headers={"Idempotency-Key": "mixed-source"})
    assert mixed.status_code == 422, mixed.text
    world, line = start(client, reader, demo, profile)
    info = client.get(API + "/worlds/" + world["id"]).json()
    assert info["sourceKind"] == "book" and info["sourceAnchor"]["bookId"] == demo["bookId"]
    assert info["sourceBookId"] == demo["bookId"] and not info["sourceRemoved"]
    assert info["prepareTaskId"] and line["headCheckpointId"]


def begin(client, made):
    world = client.get(API + "/worlds/" + made["worldId"]).json()
    actor = world["characters"][0]["id"]
    job = post(client, f"/worlds/{world['id']}/begin", {"characterId": actor}, "standalone-begin")
    process(job["taskId"])
    line = client.get(f"{API}/worlds/{world['id']}/lines/{job['lineId']}").json()
    assert line["head"]["sequence"] == 1 and line["head"]["text"]
    return world, line


@pytest.mark.parametrize("template", TEMPLATES)
def test_standalone_begin_publish_and_reader_chat_have_exact_public_scope(client, reader, profile, template):
    if template == 'douluo-forest-v1':
        # Four characters plus three places need more conservative input room
        # than the 32k fixture for the original two-character premises. Keep
        # the production context gate intact; the local default is 1M.
        with SessionLocal() as db:
            db.get(ModelProfile, profile['id']).context_window = 65536
            db.commit()
    made = create(client, template)
    world, line = begin(client, made)
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, line["headCheckpointId"])
        before = {"hash": cp.content_hash, "state": deepcopy(cp.state), "definition": deepcopy(cp.definition),
            "taskCount": db.scalar(select(func.count()).select_from(Task)), "usageCount": db.scalar(select(func.count()).select_from(UsageRecord))}
        forbidden = [e["hiddenGoal"] for e in cp.definition["entities"] if e["id"] != cp.state["playerId"] and e.get("hiddenGoal")]
        forbidden += [l["text"] for l in cp.definition["lore"] if l["id"] not in cp.state["known"]]
    saved = post(client, f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish", {}, "standalone-publish")
    assert post(client, f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish", {}, "standalone-publish-again") == saved
    context = {"storyId": "world-" + world["id"], "mode": "read", "bookId": saved["bookId"], "readerBranchId": saved["branchId"],
        "anchor": saved["readingAnchor"], "perspective": "reader", "entryOrigin": "reader"}
    checked = client.post(BASE + "/stories/" + context["storyId"] + "/context/validate", json=context)
    assert checked.status_code == 200, checked.text
    with SessionLocal() as db:
        branch = db.get(Branch, saved["branchId"]); revision = db.get(Revision, saved["revisionId"])
        book = db.get(Book, saved["bookId"])
        assert book.original_branch_id == branch.id and book.original_revision_id == revision.id
        assert revision.manifest and all(c["blocks"] for c in revision.manifest)
        assert db.scalar(select(func.count()).select_from(Book)) == 1
        assert db.scalar(select(func.count()).select_from(Branch)) == 1
        body = "\n".join(db.get(ContentBlock, bid).text for chapter in revision.manifest for bid in chapter["blocks"])
        assert body and all(secret not in body for secret in forbidden)
        assert db.scalar(select(func.count()).select_from(Progress)) == 0
        assert db.get(WorldCheckpoint, cp.id).content_hash == before["hash"]
        assert db.get(WorldCheckpoint, cp.id).state == before["state"]
        assert db.get(WorldCheckpoint, cp.id).definition == before["definition"]
        assert db.scalar(select(func.count()).select_from(Task)) == before["taskCount"]
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == before["usageCount"]
        snapshot = KnowledgeSnapshot(owner_id=reader["user"]["id"], book_id=book.id, branch_id=branch.id, revision_id=revision.id,
            boundary=context["anchor"], scope_hash=digest("standalone-conversation"), profile_version="synthetic-navigation", completeness="complete", data={"characters": [], "facts": []})
        db.add(snapshot); db.flush()
        session = RoleSession(owner_id=reader["user"]["id"], book_id=book.id, branch_id=branch.id, pinned_revision_id=revision.id,
            pinned_anchor=context["anchor"], character_id="published-person", character_name="公开人物", snapshot_id=snapshot.id)
        db.add(session); db.commit(); sid = session.id
    chat = {**context, "mode": "chat", "sessionId": sid, "characterId": "published-person", "entryOrigin": "conversation"}
    assert client.post(BASE + "/stories/" + chat["storyId"] + "/context/validate", json=chat).status_code == 200
    assert client.put(BASE + "/stories/" + chat["storyId"] + "/resume", json={"context": chat, "expectedVersion": 0}).status_code == 200
    assert client.get(BASE + "/stories/" + chat["storyId"]).json()["resume"]["chat"]["context"] == chat
    with SessionLocal() as db:
        db.delete(db.get(World, world["id"])); db.commit()
    assert client.get(BASE + "/stories/book-" + saved["bookId"]).status_code == 200
    assert client.get(BASE + "/branches/" + saved["branchId"] + "/chapters", params={"revisionId": saved["revisionId"]}).status_code == 200


def test_standalone_archive_restore_and_prepare_never_create_empty_model_task(client, reader):
    made = create(client)
    info = client.get(API + "/worlds/" + made["worldId"]).json()
    retry = client.post(f"{API}/worlds/{made['worldId']}/prepare", json={}, headers={"Idempotency-Key": "template-ready-retry"})
    assert retry.status_code == 409 and retry.json()["code"] == "WORLD_ALREADY_PREPARED"
    archived = client.patch(API + "/worlds/" + made["worldId"], json={"expectedVersion": info["version"], "status": "archived"})
    assert archived.status_code == 200
    retry = client.post(f"{API}/worlds/{made['worldId']}/prepare", json={}, headers={"Idempotency-Key": "template-archived-retry"})
    assert retry.status_code == 409 and retry.json()["code"] == "WORLD_ARCHIVED"
    restored = client.patch(API + "/worlds/" + made["worldId"], json={"expectedVersion": archived.json()["version"], "status": "active"})
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    assert restored.json()["sourceRemoved"] is False
    own_header = client.headers["Authorization"]
    _login_other(client, "standalone_other")
    assert client.get(API + "/worlds/" + made["worldId"]).status_code == 404
    assert client.get(BASE + "/stories/world-" + made["worldId"]).status_code == 404
    client.headers["Authorization"] = own_header
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        assert db.scalar(select(func.count()).select_from(UsageRecord)) == 0


def test_begin_without_profile_rolls_back_only_attempt_not_original_template(client, reader):
    made = create(client)
    info = client.get(API + "/worlds/" + made["worldId"]).json()
    result = client.post(f"{API}/worlds/{made['worldId']}/begin", json={"characterId": info["characters"][0]["id"]}, headers={"Idempotency-Key": "no-profile-begin"})
    assert result.status_code in (409, 422), result.text
    with SessionLocal() as db:
        assert db.get(World, made["worldId"]).status == "active"
        assert db.get(World, made["worldId"]).active_line_id is None
        for model in (WorldLine, WorldCheckpoint, Task, UsageRecord):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_standalone_publication_filters_private_prose_in_blocks_and_source_file(client, reader, profile):
    from app.storage import private_path
    world, line = begin(client, create(client))
    with SessionLocal() as db:
        cp = db.get(WorldCheckpoint, line["headCheckpointId"])
        secrets = [lore["text"] for lore in cp.definition["lore"] if lore["id"] not in cp.state["known"]]
        secrets += [entity["hiddenGoal"] for entity in cp.definition["entities"]
                    if entity["id"] != cp.state["playerId"] and entity.get("hiddenGoal")]
        assert secrets
        # A provider can put a GM fact directly into prose: the publication
        # boundary must project it rather than relying on a careful prompt.
        cp.text = "公开的书店灯光。\n" + "\n".join(secrets)
        cp.content_hash = checkpoint_hash(cp.state, cp.definition, cp.text, cp.parent_checkpoint_id)
        before = (cp.text, cp.content_hash, deepcopy(cp.state), deepcopy(cp.definition))
        db.commit()
    publication = post(client, f"/worlds/{world['id']}/checkpoints/{line['headCheckpointId']}/publish", {}, "private-prose-publication")
    with SessionLocal() as db:
        revision = db.get(Revision, publication["revisionId"])
        body = "\n".join(db.get(ContentBlock, block).text for chapter in revision.manifest for block in chapter["blocks"])
        source = db.scalar(select(SourceDocument).where(SourceDocument.book_id == publication["bookId"]))
        file_text = private_path(source.owner_id, source.file_ref).read_text("utf-8")
        assert "公开的书店灯光。" in body
        assert body.splitlines() == [paragraph for paragraph in file_text.splitlines() if paragraph]
        assert all(secret not in body and secret not in file_text for secret in secrets)
        cp = db.get(WorldCheckpoint, line["headCheckpointId"])
        assert (cp.text, cp.content_hash, cp.state, cp.definition) == before
