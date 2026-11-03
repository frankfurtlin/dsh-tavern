"""Standalone real owned PG/HTTP contracts; no global destructive conftest."""
from copy import deepcopy
import io
import json
import zipfile
import pytest
from sqlalchemy import func, select
from app import models as m
from app.db import SessionLocal, engine
from app.domain import canonical, digest, make_anchor, utf16_len
from app.comparison.models import ComparisonExposure
from app.world import models as w
from app.world.state import checkpoint_hash
from app.api_comparison import router
from test_portable_domain import domain, migrate_owned_database, http_client


@pytest.fixture(scope="session", autouse=True)
def comparison_schema(migrate_owned_database):
    ComparisonExposure.__table__.create(engine, checkfirst=True)


def client(owner):
    value = http_client(owner, diagnostic=True)
    value.app.include_router(router, prefix="/api/reader/v1")
    return value


def ordinary_import(http, *, format="txt", reverse=False):
    """Exercise the actual upload, parser worker and confirm producer."""
    from app.api_books import router as books_router
    from app.worker import process
    http.app.include_router(books_router, prefix="/api/reader/v1")
    prose = "第一章 灯下\n林渡收好口信🙂。\n\n第二章 渡口\n梅舟推开木门。\n"
    data = prose.encode("utf-8")
    if format == "epub":
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
            archive.writestr("OEBPS/content.opf", '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">owned-compare-import</dc:identifier><dc:title>灯下</dc:title><dc:language>zh</dc:language></metadata><manifest><item id="page" href="page.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="page"/></spine></package>')
            archive.writestr("OEBPS/page.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>灯下</title></head><body><h1>第一章 灯下</h1><p>林渡收好<strong>口信🙂</strong>。</p><p>梅舟推开木门。</p></body></html>')
        data = output.getvalue()
    upload = http.post("/api/reader/v1/books/imports", files={"file": ("owned-compare."+format, data)},
        headers={"Idempotency-Key": "ordinary-import"})
    assert upload.status_code == 202, upload.text
    task = upload.json()["taskId"]; process(task)
    status = http.get("/api/reader/v1/books/imports/"+task)
    assert status.status_code == 200 and status.json()["importStatus"] == "awaiting_confirmation", status.text
    body = {"title": "普通导入比较"}
    if reverse: body["chapterOrder"] = [c["id"] for c in reversed(status.json()["chapters"])]
    confirmed = http.post("/api/reader/v1/books/imports/"+task+"/confirm", json=body,
        headers={"Idempotency-Key": "ordinary-confirm"})
    assert confirmed.status_code == 201, confirmed.text
    return confirmed.json()


def paths(domain, kind="world"):
    with SessionLocal() as db:
        if kind == "world":
            lines = list(db.scalars(select(w.WorldLine).where(w.WorldLine.world_id == domain["world"]).order_by(w.WorldLine.parent_line_id.nullsfirst())))
            return {"kind": kind, "worldId": domain["world"], "leftLineId": lines[0].id, "rightLineId": lines[1].id}
        lines = list(db.scalars(select(m.Branch).where(m.Branch.book_id == domain["book"]).order_by(m.Branch.parent_branch_id.nullsfirst())))
        return {"kind": kind, "bookId": domain["book"], "leftLineId": lines[0].id, "rightLineId": lines[1].id}


def endpoint(domain): return "/api/reader/v1/stories/book-"+domain["book"]+"/compare"


def immutable(db, domain):
    rows = []
    for model in (m.Book, m.Branch, m.Progress, m.Task, m.UsageRecord, m.KnowledgeSnapshot, m.ResumeTarget, w.World, w.WorldLine, w.WorldCheckpoint):
        rows += [(model.__name__, row.id, {col.name: deepcopy(getattr(row, col.name)) for col in model.__table__.columns})
                 for row in db.scalars(select(model).where(model.owner_id == domain["owner"]))]
    return sorted(rows)


def mark_world(domain, params, *, heads=False):
    with SessionLocal() as db:
        for key in ("leftLineId", "rightLineId"):
            line = db.get(w.WorldLine, params[key])
            line.reading_position = {"checkpointId": line.head_checkpoint_id if heads else domain["root_checkpoint"]}
        db.commit()


def test_compare_capabilities_is_independent_and_no_write(domain):
    with client(domain["owner"]) as http:
        response = http.get("/api/reader/v1/comparison/capabilities")
        assert response.status_code == 200
        assert response.json() == {"protocol": "reader-compare-v1", "ready": True, "revealReady": True,
            "alignments": {"book": ["latest"], "world": ["latest", "same-time"]}, "maxPageSize": 20}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ComparisonExposure)) == 0


def test_world_common_ancestor_and_unseen_clamp_are_pure(domain):
    params = paths(domain); mark_world(domain, params)
    with SessionLocal() as db: before = immutable(db, domain)
    with client(domain["owner"]) as http:
        response = http.get(endpoint(domain), params=params); assert response.status_code == 200, response.text
        value = response.json()
        assert value["common"] == {"nodeId": domain["root_checkpoint"], "visible": True, "title": "起点"}
        assert value["left"]["hasUnseen"] and value["right"]["hasUnseen"]
        assert [item["text"] for item in value["left"]["items"]] == ["灯亮了。"]
        assert "信封摆在桌上。" not in response.text and "雨还在下。" not in response.text
    with SessionLocal() as db:
        assert immutable(db, domain) == before
        assert db.scalar(select(func.count()).select_from(ComparisonExposure)) == 0


def test_world_public_state_diff_and_matching_titles_do_not_merge_paths(domain):
    params = paths(domain); mark_world(domain, params, heads=True)
    with SessionLocal() as db:
        for key, minute in (("checkpoint", 8), ("fork", 13)):
            point = db.get(w.WorldCheckpoint, domain[key]); state = deepcopy(point.state); definition = deepcopy(point.definition)
            state["minute"] = minute; state["entities"][state["playerId"]]["fields"]["信任"] = minute
            npc = next(entity for entity in definition["entities"] if entity["type"] == "character" and entity["id"] != state["playerId"])
            npc["hiddenGoal"] = "PRIVATE_UNREAD_NPC_GOAL_SENTINEL"
            state["entities"][npc["id"]]["goal"] = "PRIVATE_CURRENT_NPC_GOAL_SENTINEL"
            point.state = state; point.definition = definition; point.title = "同名章节"
            point.summary = "PRIVATE_MEMORY_SENTINEL"; point.memory = {"private": "PRIVATE_MEMORY_SENTINEL"}
            point.content_hash = checkpoint_hash(state, definition, point.text, point.parent_checkpoint_id)
        db.commit()
    with client(domain["owner"]) as http:
        value = http.get(endpoint(domain), params=params); assert value.status_code == 200, value.text
        assert value.json()["common"]["nodeId"] == domain["root_checkpoint"]
        assert any(change["path"] == "/minute" and change["before"] == 8 and change["after"] == 13 for change in value.json()["differences"])
        assert "PRIVATE_" not in value.text
        aligned = http.get(endpoint(domain), params={**params, "alignment": "same-time", "atMinute": 8})
        assert aligned.status_code == 200, aligned.text
        assert aligned.json()["left"]["nodeId"] == domain["checkpoint"]
        assert aligned.json()["right"]["nodeId"] == domain["root_checkpoint"]


def test_reader_exact_utf16_partial_future_title_and_body_clamp(domain):
    params = paths(domain, "book")
    with SessionLocal() as db:
        progress = db.scalar(select(m.Progress).where(m.Progress.owner_id == domain["owner"]))
        block = db.get(m.ContentBlock, domain["block"])
        prefix = block.text.split("🙂")[0]+"🙂"
        progress.max_observed_anchor = make_anchor(domain["book"], domain["branch"], domain["revision"], block, utf16_len(prefix))
        db.commit(); before = immutable(db, domain)
    with client(domain["owner"]) as http:
        response = http.get(endpoint(domain), params=params); assert response.status_code == 200, response.text
        value = response.json(); assert value["left"]["items"][0]["text"] == prefix
        assert value["left"]["items"][0]["partial"] and value["left"]["hasUnseen"]
        assert value["right"]["items"] == [] and value["right"]["state"] is None
        assert "第二章 来信" not in response.text and "同一段话再次出现" not in response.text
    with SessionLocal() as db: assert immutable(db, domain) == before


@pytest.mark.parametrize("kind", ["book", "world"])
def test_explicit_reveal_only_one_path_idempotent_cas_and_pure_lookup(domain, kind):
    params = paths(domain, kind)
    if kind == "world": mark_world(domain, params)
    with SessionLocal() as db:
        if kind == "world": node = db.get(w.WorldLine, params["rightLineId"]).head_checkpoint_id
        else: node = db.get(m.Branch, params["rightLineId"]).head_revision_id
        before = immutable(db, domain)
    data = {"kind": kind, "resourceId": domain["book" if kind == "book" else "world"],
        "lineId": params["rightLineId"], "nodeId": node, "confirmed": True, "expectedVersion": 0}
    with client(domain["owner"]) as http:
        assert http.get(endpoint(domain)+"/reveal/requests", params={"requestId": "new-view"}).json() == {"found": False, "result": None}
        rejected = http.post(endpoint(domain)+"/reveal", json={**data, "confirmed": False}, headers={"Idempotency-Key": "unconfirmed"})
        assert rejected.status_code == 409
        first = http.post(endpoint(domain)+"/reveal", json=data, headers={"Idempotency-Key": "new-view"})
        assert first.status_code == 200, first.text
        assert http.post(endpoint(domain)+"/reveal", json=data, headers={"Idempotency-Key": "new-view"}).json() == first.json()
        conflict = http.post(endpoint(domain)+"/reveal", json=data, headers={"Idempotency-Key": "another-view"})
        assert conflict.status_code == 409 and conflict.json()["code"] == "COMPARE_VIEW_CONFLICT"
        status = http.get(endpoint(domain)+"/reveal/requests", params={"requestId": "new-view"})
        assert status.json() == {"found": True, "result": first.json()}
        value = http.get(endpoint(domain), params=params).json()
        assert not value["right"]["hasUnseen"]
        assert "雨还在下。" in json.dumps(value, ensure_ascii=False) if kind == "world" else "林渡回到灯下。" in json.dumps(value, ensure_ascii=False)
        if kind == "world": assert value["left"]["hasUnseen"]
    with SessionLocal() as db:
        assert immutable(db, domain) == before
        assert db.scalar(select(func.count()).select_from(ComparisonExposure).where(ComparisonExposure.owner_id == domain["owner"])) == 1


@pytest.mark.parametrize("kind", ["book", "world"])
def test_compare_and_reveal_reject_cross_owner_and_other_path_nodes(domain, kind):
    params = paths(domain, kind)
    with client(domain["stranger"]) as stranger:
        assert stranger.get(endpoint(domain), params=params).status_code == 404
    with client(domain["owner"]) as http:
        wrong = domain["fork"] if kind == "world" else domain["revision"]
        query = {**params, "leftNodeId": wrong} if kind == "world" else {**params, "rightNodeId": wrong}
        assert http.get(endpoint(domain), params=query).status_code in (404, 422)
        reveal = http.post(endpoint(domain)+"/reveal", json={"kind": kind, "resourceId": domain["world" if kind == "world" else "book"],
            "lineId": params["leftLineId"] if kind == "world" else params["rightLineId"], "nodeId": wrong,
            "confirmed": True, "expectedVersion": 0}, headers={"Idempotency-Key": "forged"})
        assert reveal.status_code in (404, 422)


@pytest.mark.parametrize("changes", [{"limit": 21}, {"alignment": "same-time"}, {"atMinute": 8}, {"leftOffset": -1}])
def test_compare_rejects_invalid_bounded_paging_or_time_contract(domain, changes):
    with client(domain["owner"]) as http:
        assert http.get(endpoint(domain), params={**paths(domain), **changes}).status_code == 422


def test_world_paging_and_pinned_older_node_do_not_show_future(domain):
    params = paths(domain); mark_world(domain, params, heads=True)
    with client(domain["owner"]) as http:
        first = http.get(endpoint(domain), params={**params, "limit": 1}).json()
        assert first["left"]["nextOffset"] == 1 and len(first["left"]["items"]) == 1
        second = http.get(endpoint(domain), params={**params, "leftOffset": 1, "rightOffset": 1, "limit": 1}).json()
        assert second["left"]["items"][0]["id"] == domain["checkpoint"]
        assert second["left"]["nextOffset"] is None
        older = http.get(endpoint(domain), params={**params, "leftNodeId": domain["root_checkpoint"]}).json()
        assert [item["id"] for item in older["left"]["items"]] == [domain["root_checkpoint"]]


def test_reveal_commit_ack_loss_recovers_by_lookup_without_second_post(domain):
    from sqlalchemy import event
    params = paths(domain); mark_world(domain, params)
    data = {"kind": "world", "resourceId": domain["world"], "lineId": params["rightLineId"],
        "nodeId": domain["fork"], "confirmed": True, "expectedVersion": 0}
    fired = False
    def mark(session, *_):
        if any(isinstance(row, ComparisonExposure) for row in session.new | session.dirty):
            session.info["synthetic_compare_write"] = True
    def lost(session):
        nonlocal fired
        if not fired and session.info.get("synthetic_compare_write"):
            fired = True
            raise ConnectionError("synthetic committed compare acknowledgment lost")
    with client(domain["owner"]) as http:
        event.listen(SessionLocal.class_, "after_commit", lost)
        event.listen(SessionLocal.class_, "before_flush", mark)
        try:
            first = http.post(endpoint(domain)+"/reveal", json=data, headers={"Idempotency-Key": "lost-view"})
            assert first.status_code == 503 and fired
        finally:
            event.remove(SessionLocal.class_, "after_commit", lost)
            event.remove(SessionLocal.class_, "before_flush", mark)
        status = http.get(endpoint(domain)+"/reveal/requests", params={"requestId": "lost-view"})
        assert status.status_code == 200 and status.json()["found"] and status.json()["result"]["version"] == 1
        compared = http.get(endpoint(domain), params=params).json()
        assert compared["right"]["viewVersion"] == 1 and not compared["right"]["hasUnseen"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ComparisonExposure).where(ComparisonExposure.owner_id == domain["owner"])) == 1


def test_identical_reader_titles_and_prose_with_distinct_content_ids_are_not_common(domain):
    with SessionLocal() as db:
        source = db.get(m.ContentBlock, domain["block"])
        parent = db.get(m.Branch, domain["branch"])
        branch = m.Branch(owner_id=domain["owner"], book_id=domain["book"], name="独立同名正文", head_revision_id=m.uid())
        block = m.ContentBlock(owner_id=domain["owner"], book_id=domain["book"], chapter_id=m.uid(), text=source.text, content_hash=source.content_hash)
        db.add_all([branch, block]); db.flush()
        manifest = [{"id": block.chapter_id, "title": "第一章 灯下", "blocks": [block.id]}]
        revision = m.Revision(id=branch.head_revision_id, owner_id=domain["owner"], branch_id=branch.id,
            manifest=manifest, content_hash=digest(canonical(manifest)), provenance={"operation": "synthetic"})
        db.add(revision); db.flush()
        end = make_anchor(domain["book"], branch.id, revision.id, block, utf16_len(block.text))
        db.add(m.Progress(owner_id=domain["owner"], book_id=domain["book"], branch_id=branch.id,
            revision_id=revision.id, anchor=end, max_observed_anchor=end)); db.commit()
        params = {"kind": "book", "bookId": domain["book"], "leftLineId": parent.id, "rightLineId": branch.id}
    with client(domain["owner"]) as http:
        response = http.get(endpoint(domain), params=params); assert response.status_code == 200, response.text
        assert response.json()["common"]["nodeId"] is None
        assert response.json()["left"]["items"][0]["text"] == response.json()["right"]["items"][0]["text"]


def test_exact_partial_reader_fork_anchor_survives_without_title_matching(domain):
    params = paths(domain, "book")
    with SessionLocal() as db:
        branch = db.get(m.Branch, params["rightLineId"])
        block = db.get(m.ContentBlock, domain["block"])
        branch.fork_anchor = make_anchor(domain["book"], domain["branch"], domain["revision"], block, 2)
        revision = db.get(m.Revision, branch.head_revision_id)
        revision.manifest = revision.manifest[1:]; revision.content_hash = digest(canonical(revision.manifest))
        db.commit(); expected = deepcopy(branch.fork_anchor)
    with client(domain["owner"]) as http:
        response = http.get(endpoint(domain), params=params); assert response.status_code == 200, response.text
        assert response.json()["common"]["nodeId"] is None
        assert response.json()["common"]["anchor"] == expected


def test_readonly_graph_bulk_queries_shared_ancestry_and_private_metadata(domain):
    from sqlalchemy import event
    from app.world.graph import graph
    with SessionLocal() as db:
        point = db.get(w.WorldCheckpoint, domain["checkpoint"])
        point.action = "PRIVATE_GRAPH_SENTINEL"
        definition = deepcopy(point.definition)
        npc = next(entity for entity in definition["entities"] if entity["type"] == "character" and entity["id"] != point.state["playerId"])
        npc["hiddenGoal"] = "PRIVATE_GRAPH_SENTINEL"
        point.definition = definition; point.content_hash = checkpoint_hash(point.state, definition, point.text, point.parent_checkpoint_id)
        point.summary = "PRIVATE_GRAPH_SUMMARY"; point.changes = [{"private": "PRIVATE_GRAPH_CHANGES"}]
        db.commit(); before = immutable(db, domain)
        calls = []
        def count(*args): calls.append(args[2])
        event.listen(engine, "before_cursor_execute", count)
        try: value = graph(db, domain["owner"], domain["world"])
        finally: event.remove(engine, "before_cursor_execute", count)
        assert len(calls) <= 4
        assert len(value["nodes"]) == 3 and len({row["id"] for row in value["nodes"]}) == 3
        assert all(row["text"] == row["summary"] == "" and not row["changes"] and not row["options"] for row in value["nodes"])
        assert "PRIVATE_GRAPH_" not in json.dumps(value)
        assert immutable(db, domain) == before


@pytest.mark.parametrize("invalid", ["missing", "cycle", "head-hash", "other-world"])
def test_graph_rejects_invalid_ancestry_without_cross_world_nodes(domain, invalid):
    from app.world.graph import graph
    from app.errors import Problem
    with SessionLocal() as db:
        point = db.get(w.WorldCheckpoint, domain["root_checkpoint"])
        if invalid == "missing": point.parent_checkpoint_id = m.uid()
        elif invalid == "cycle": point.parent_checkpoint_id = domain["checkpoint"]
        elif invalid == "head-hash": db.get(w.WorldCheckpoint, domain["checkpoint"]).content_hash = "0"*64
        else:
            extra = w.World(owner_id=domain["owner"], name="无关世界", source_title="独立来源", source={"kind": "standalone"}, definition=point.definition)
            db.add(extra); db.flush(); point.world_id = extra.id
        db.commit()
        with pytest.raises(Problem) as caught: graph(db, domain["owner"], domain["world"])
        assert caught.value.code in {"RESOURCE_NOT_FOUND", "WORLD_HISTORY_INVALID", "WORLD_CHECKPOINT_INVALID"}


def test_world_story_does_not_authorize_unpublished_reader_branch(domain):
    with client(domain["owner"]) as http:
        response = http.get("/api/reader/v1/stories/world-"+domain["world"]+"/compare", params=paths(domain, "book"))
        assert response.status_code == 404


def test_purge_saved_view_range_and_receipt_preserves_other_path_and_is_atomic(domain):
    from app.comparison.cleanup import purge
    params = paths(domain); mark_world(domain, params)
    with client(domain["owner"]) as http:
        for key, node in (("leftLineId", domain["checkpoint"]), ("rightLineId", domain["fork"])):
            value = http.post(endpoint(domain)+"/reveal", json={"kind": "world", "resourceId": domain["world"],
                "lineId": params[key], "nodeId": node, "confirmed": True, "expectedVersion": 0}, headers={"Idempotency-Key": key})
            assert value.status_code == 200
        with SessionLocal() as db:
            purge(db, domain["owner"], "world", domain["world"], params["rightLineId"])
            db.rollback()
            assert db.scalar(select(func.count()).select_from(ComparisonExposure).where(ComparisonExposure.owner_id == domain["owner"])) == 2
            purge(db, domain["owner"], "world", domain["world"], params["rightLineId"]); db.commit()
            assert db.scalar(select(func.count()).select_from(ComparisonExposure).where(ComparisonExposure.owner_id == domain["owner"])) == 1
        assert http.get(endpoint(domain)+"/reveal/requests", params={"requestId": "rightLineId"}).status_code == 404
        assert http.get(endpoint(domain)+"/reveal/requests", params={"requestId": "leftLineId"}).json()["found"]


def test_reader_facts_require_verified_cache_and_exact_observed_evidence_without_registry_writes(domain):
    from app.domain import prefix_blocks
    from app.knowledge import ANALYZER, snapshot_hash
    with client(domain["owner"]) as http:
        response = http.post("/api/reader/v1/model-profiles", json={"name": "isolated compare cache verifier",
            "baseUrl": "mock://deterministic", "modelId": "fixture-v1", "key": "SYNTHETIC-ONLY-NOT-A-REAL-KEY", "contextWindow": 32768})
        assert response.status_code == 201, response.text
        profile_id = response.json()["id"]
        with SessionLocal() as db:
            profile = db.get(m.ModelProfile, profile_id); revision = db.get(m.Revision, domain["revision"])
            block = db.get(m.ContentBlock, domain["block"])
            first = make_anchor(domain["book"], domain["branch"], domain["revision"], block, 0, 2)
            fact_anchor = make_anchor(domain["book"], domain["branch"], domain["revision"], block, 0, 4)
            row = db.scalar(select(m.KnowledgeSnapshot).where(m.KnowledgeSnapshot.owner_id == domain["owner"]))
            row.analyzer_version = ANALYZER; row.profile_version = profile_id+":"+str(profile.version)
            row.scope_hash = snapshot_hash(domain["anchor"], prefix_blocks(db, revision, domain["anchor"], domain["owner"]), profile)
            row.data = {"characters": [{"id": "local-0", "displayName": "林渡", "evidenceIds": ["e1", "e2"]}],
                "facts": [{"id": "f1", "subjectId": "local-0", "predicate": "occupation", "value": "把信", "status": "confirmed",
                    "readerRevealAnchor": fact_anchor, "evidenceIds": ["e2"], "knowledge": [], "knownBy": []}],
                "evidence": [{"id": "e1", "quote": "林渡", "anchor": first}, {"id": "e2", "quote": "林渡把信", "anchor": fact_anchor}]}
            progress = db.scalar(select(m.Progress).where(m.Progress.owner_id == domain["owner"]))
            progress.max_observed_anchor = make_anchor(domain["book"], domain["branch"], domain["revision"], block, 2)
            db.commit(); before = immutable(db, domain)
            identities = db.scalar(select(func.count()).select_from(m.CharacterIdentity))
            aliases = db.scalar(select(func.count()).select_from(m.CharacterAlias))
        params = paths(domain, "book")
        response = http.get(endpoint(domain), params=params); assert response.status_code == 200, response.text
        state = response.json()["left"]["state"]
        assert state["characters"] == {domain["identity"]: {"name": "林渡"}}
        assert state["facts"] == {}
        with SessionLocal() as db:
            assert immutable(db, domain) == before
            assert db.scalar(select(func.count()).select_from(m.CharacterIdentity)) == identities
            assert db.scalar(select(func.count()).select_from(m.CharacterAlias)) == aliases
            progress = db.scalar(select(m.Progress).where(m.Progress.owner_id == domain["owner"]))
            progress.max_observed_anchor = domain["anchor"]; db.commit()
        state = http.get(endpoint(domain), params=params).json()["left"]["state"]
        assert state["facts"] == {domain["identity"]+":occupation": [{"value": "把信", "status": "confirmed"}]}


@pytest.mark.parametrize("format,reverse", [("txt", False), ("txt", True), ("epub", False)])
def test_compare_actual_import_hash_and_confirmed_directory_order_are_supported_without_rewriting(domain, format, reverse):
    with client(domain["owner"]) as http:
        imported = ordinary_import(http, format=format, reverse=reverse)
        address = "/api/reader/v1/stories/book-"+imported["bookId"]+"/compare"
        query = {"kind": "book", "bookId": imported["bookId"], "leftLineId": imported["branchId"],
            "rightLineId": imported["branchId"]}
        with SessionLocal() as db:
            revision = db.get(m.Revision, imported["revisionId"])
            original = deepcopy(revision.manifest), revision.content_hash, deepcopy(revision.provenance)
            assert original[1] != digest(canonical(original[0]))
            expected = [db.get(m.ContentBlock, block).text for chapter in revision.manifest for block in chapter["blocks"]]
            if reverse: assert revision.manifest[0]["title"] == "第二章 渡口"
            before = immutable(db, domain)
        response = http.get(address, params=query)
        assert response.status_code == 200, response.text
        assert response.json()["left"]["items"] == [] and response.json()["left"]["hasUnseen"]
        reveal = http.post(address+"/reveal", json={"kind": "book", "resourceId": imported["bookId"],
            "lineId": imported["branchId"], "nodeId": imported["revisionId"], "confirmed": True, "expectedVersion": 0},
            headers={"Idempotency-Key": "explicit-import-view"})
        assert reveal.status_code == 200, reveal.text
        compared = http.get(address, params=query)
        assert compared.status_code == 200, compared.text
        assert [item["text"] for item in compared.json()["left"]["items"]] == expected
        assert not compared.json()["left"]["hasUnseen"] and "<strong>" not in compared.text
        with SessionLocal() as db:
            revision = db.get(m.Revision, imported["revisionId"])
            assert (revision.manifest, revision.content_hash, revision.provenance) == original
            assert immutable(db, domain) == before
            assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id == domain["owner"])) == 0


@pytest.mark.parametrize("fault", ["text", "html", "duplicate", "chapter", "provenance", "source-order"])
def test_import_hash_compatibility_does_not_accept_changed_or_misbound_source(domain, fault):
    with client(domain["owner"]) as http:
        imported = ordinary_import(http, format="epub" if fault == "html" else "txt")
        with SessionLocal() as db:
            revision = db.get(m.Revision, imported["revisionId"])
            block = db.get(m.ContentBlock, revision.manifest[0]["blocks"][0])
            if fault == "text":
                block.text += "篡改"; block.content_hash = digest(block.text)
            elif fault == "html": block.html += "<em>篡改</em>"
            elif fault == "duplicate":
                value = deepcopy(revision.manifest); value[0]["blocks"].append(block.id); revision.manifest = value
            elif fault == "chapter": block.chapter_id = domain["anchor"]["chapterId"]
            elif fault == "provenance": revision.provenance = {"type": "ai", "sourceId": db.get(m.Book, imported["bookId"]).source_id}
            else:
                source = db.get(m.SourceDocument, db.get(m.Book, imported["bookId"]).source_id)
                value = deepcopy(source.source_map)
                first, second = [key for chapter in revision.manifest for key in chapter["blocks"]]
                value["blocks"][first]["normalizedStart"], value["blocks"][second]["normalizedStart"] = value["blocks"][second]["normalizedStart"], value["blocks"][first]["normalizedStart"]
                source.source_map = value
            db.commit()
        address = "/api/reader/v1/stories/book-"+imported["bookId"]+"/compare"
        response = http.get(address, params={"kind": "book", "bookId": imported["bookId"],
            "leftLineId": imported["branchId"], "rightLineId": imported["branchId"]})
        assert response.status_code == 409 and response.json()["code"] == "REVISION_INVALID", response.text
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(ComparisonExposure).where(
                ComparisonExposure.owner_id == domain["owner"], ComparisonExposure.resource_id == imported["bookId"])) == 0
