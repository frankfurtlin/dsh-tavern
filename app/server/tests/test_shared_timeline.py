"""A graph renders shared prose once while the reader keeps the complete branch."""
import pytest
from app.db import SessionLocal
from app.domain import canonical, digest
from app.models import Branch, Revision, ContentBlock, uid
from .helpers import BASE, read_chapter, observe, operation, accept


@pytest.mark.parametrize("paragraph,edge,expected", [(-1, "endAnchor", 3), (0, "endAnchor", 3), (0, "startAnchor", 2)])
def test_fork_graph_shares_only_retained_prefix_without_changing_reader_manifest(client, reader, demo, profile, paragraph, edge, expected):
    with SessionLocal() as db:
        original = db.get(Revision, demo["revisionId"])
        before = (original.content_hash, digest(canonical(original.manifest)))
    third = read_chapter(client, demo, 2)
    anchor = third["blocks"][paragraph][edge]
    observe(client, reader, anchor)
    task, _, _ = operation(client, profile, anchor)
    result = accept(client, task).json()
    response = client.get(BASE+"/books/"+demo["bookId"]+"/timeline").json()
    original = next(b for b in response["branches"] if b["id"] == demo["branchId"])
    child = next(b for b in response["branches"] if b["id"] == result["branchId"])
    assert len(original["chapters"]) == 4
    assert child["inheritedChapterCount"] == expected
    assert len(child["chapters"]) == 1 and child["chapters"][0]["position"] == expected
    assert child["chapters"][0]["id"] == result["chapterIds"][0]
    assert child["chapters"][0]["source"] == "ai" and not child["chapters"][0]["observed"]
    assert child["sharedThroughChapterId"] == original["chapters"][expected-1]["id"]
    directory = client.get(BASE+f"/branches/{result['branchId']}/chapters", params={"revisionId": result["revisionId"]}).json()
    assert len(directory["items"]) == expected+1
    inherited = client.get(BASE+f"/branches/{result['branchId']}/chapters/{third['id']}", params={"revisionId": result["revisionId"]})
    assert inherited.status_code == (200 if expected == 3 else 404)
    with SessionLocal() as db:
        base = db.get(Revision, demo["revisionId"])
        saved = db.get(Revision, result["revisionId"])
        assert len(base.manifest) == 4 and len(saved.manifest) == expected+1
        assert (base.content_hash, digest(canonical(base.manifest))) == before


def test_nested_fork_shares_existing_ai_chapter_and_keeps_exact_frozen_origin(client, reader, demo, profile):
    anchor = read_chapter(client, demo, 2)["blocks"][-1]["endAnchor"]
    observe(client, reader, anchor)
    task, _, _ = operation(client, profile, anchor)
    first = accept(client, task).json()
    chapter = client.get(BASE+f"/branches/{first['branchId']}/chapters/{first['chapterIds'][0]}", params={"revisionId": first["revisionId"]}).json()
    anchor = chapter["blocks"][-1]["endAnchor"]
    progress = client.get(BASE+f"/books/{demo['bookId']}/progress", params={"branchId": first["branchId"], "revisionId": first["revisionId"]}).json()
    observe(client, reader, anchor, expected=progress["version"])
    task, _, _ = operation(client, profile, anchor)
    second = accept(client, task, key="nested-accept").json()
    lines = client.get(BASE+"/books/"+demo["bookId"]+"/timeline").json()["branches"]
    child = next(b for b in lines if b["id"] == first["branchId"])
    nested = next(b for b in lines if b["id"] == second["branchId"])
    assert child["inheritedChapterCount"] == 3 and len(child["chapters"]) == 1
    assert nested["inheritedChapterCount"] == 4 and len(nested["chapters"]) == 1
    assert nested["chapters"][0]["position"] == 4
    assert nested["sharedThroughChapterId"] == first["chapterIds"][0]
    assert nested["parentBranchId"] == first["branchId"]
    assert nested["forkAnchor"]["revisionId"] == first["revisionId"]


def test_default_graph_page_loads_suffix_after_twenty_shared_chapters(client, reader, demo):
    # Synthetic saved revisions in the isolated test DB: no generation involved.
    with SessionLocal() as db:
        original = db.get(Revision, demo["revisionId"])
        source = db.get(Branch, demo["branchId"])
        manifest = list(original.manifest)
        for i in range(4, 46):
            chapter_id, block_id = uid(), uid()
            body = f"第{i+1}章的测试正文。"
            db.add(ContentBlock(id=block_id, owner_id=source.owner_id, book_id=source.book_id, chapter_id=chapter_id, text=body, html=None, source="original", content_hash=digest(body)))
            manifest.append({"id": chapter_id, "title": f"第{i+1}章测试", "blocks": [block_id]})
        original.manifest, original.content_hash = manifest, digest(canonical(manifest))
        db.commit()
    anchor = read_chapter(client, demo, 24)["blocks"][0]["endAnchor"]
    with SessionLocal() as db:
        original = db.get(Revision, demo["revisionId"])
        source = db.get(Branch, demo["branchId"])
        line_id, revision_id = uid(), uid()
        child = Branch(id=line_id, owner_id=source.owner_id, book_id=source.book_id, parent_branch_id=source.id, fork_anchor=anchor, name="后续测试", head_revision_id=revision_id)
        db.add(child)
        db.flush()
        own = []
        for i in range(22):
            chapter_id, block_id = uid(), uid()
            db.add(ContentBlock(id=block_id, owner_id=source.owner_id, book_id=source.book_id, chapter_id=chapter_id, text="续写测试。", html=None, source="ai", content_hash=digest("续写测试。")))
            own.append({"id": chapter_id, "title": f"续写{i+26}", "blocks": [block_id]})
        manifest = original.manifest[:25]+own
        db.add(Revision(id=revision_id, owner_id=source.owner_id, branch_id=line_id, parent_revision_id=original.id, sequence=1, manifest=manifest, content_hash=digest(canonical(manifest)), provenance={"type": "test"}))
        db.commit()
    lines = client.get(BASE+"/books/"+demo["bookId"]+"/timeline").json()["branches"]
    child = next(b for b in lines if b["id"] == line_id)
    assert child["inheritedChapterCount"] == 25 and len(child["chapters"]) == 20
    assert child["chapters"][0]["position"] == 25 and child["nextChapterOffset"] == 45
    second = client.get(BASE+"/books/"+demo["bookId"]+"/timeline", params={"chapterOffset": 45}).json()["branches"]
    child = next(b for b in second if b["id"] == line_id)
    assert [c["position"] for c in child["chapters"]] == [45, 46]
    assert child["nextChapterOffset"] is None
