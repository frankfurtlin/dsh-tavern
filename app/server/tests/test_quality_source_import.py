"""Actual ordinary imports of captured public-source candidates; no model calls."""
import hashlib
import json
from pathlib import Path
import re
import os

import pytest
from sqlalchemy import func, select
from app import models as m
from app.api_books import router
from app.db import SessionLocal
from app.worker import process
from test_portable_domain import domain, http_client, migrate_owned_database

FOLDER = Path(os.environ.get("QUALITY_SOURCE_DIR", "/evidence/frozen/server/tests/next/quality-sources"))
CATALOGUE = json.loads((FOLDER/"catalogue.json").read_text("utf-8"))


@pytest.mark.parametrize("entry", CATALOGUE["sources"], ids=[entry["genre"] for entry in CATALOGUE["sources"]])
def test_captured_literary_source_passes_actual_upload_parse_confirm_and_read_without_analysis(domain, entry):
    reading = next(name for name in entry["files"] if name.endswith("/reading.txt"))
    raw = (FOLDER/reading).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == entry["files"][reading]["sha256"]
    body = raw.decode("utf-8")
    assert len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U000323af]", body)) == entry["readingHanCharacters"] >= 100000
    assert len(body) <= 2000000 and len(raw) <= 50*1024**2
    with http_client(domain["owner"], diagnostic=True) as http:
        http.app.include_router(router, prefix="/api/reader/v1")
        uploaded = http.post("/api/reader/v1/books/imports", files={"file": ("public-literary.txt", raw, "text/plain")},
            headers={"Idempotency-Key": "public-literary-import"})
        assert uploaded.status_code == 202, uploaded.text
        task_id = uploaded.json()["taskId"]; process(task_id)
        status = http.get("/api/reader/v1/books/imports/"+task_id)
        assert status.status_code == 200 and status.json()["importStatus"] == "awaiting_confirmation", status.text
        confirmed = http.post("/api/reader/v1/books/imports/"+task_id+"/confirm", json={"title": entry["title"], "prepareCharacters": False},
            headers={"Idempotency-Key": "public-literary-confirm"})
        assert confirmed.status_code == 201, confirmed.text
        result = confirmed.json()
        chapters = http.get("/api/reader/v1/branches/"+result["branchId"]+"/chapters", params={"limit": 5})
        assert chapters.status_code == 200, chapters.text
        with SessionLocal() as db:
            revision = db.get(m.Revision, result["revisionId"])
            assert len(revision.manifest) == len(status.json()["chapters"]) > 1
            chapter = revision.manifest[0]
            stored = [db.get(m.ContentBlock, block).text for block in chapter["blocks"]]
            assert stored and all(text in body for text in stored)
            assert db.get(m.Task, task_id).state == "succeeded"
            assert db.get(m.Task, task_id).provider_state == "not_started"
            assert db.scalar(select(func.count()).select_from(m.UsageRecord).where(m.UsageRecord.owner_id == domain["owner"])) == 0
        first = http.get("/api/reader/v1/branches/"+result["branchId"]+"/chapters/"+chapter["id"])
        assert first.status_code == 200, first.text
        assert [block["text"] for block in first.json()["blocks"]] == stored
    # This proves importer/readability only; genre, rights, goldens and actual
    # literary quality remain separate gates and are not marked approved here.
