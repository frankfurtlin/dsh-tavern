"""Make a CC0 synthetic EPUB via the frozen exporter in a fresh owned database."""
import importlib.util
import json
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from sqlalchemy import func, select
from app import models as m
from app.db import SessionLocal, engine
from app.portable.reading_export import ReadingExportInput, download_reading, export_reading
from app.domain import digest

assert engine.url.database == "portable_test" and engine.url.host == "db"
module_spec = importlib.util.spec_from_file_location("synthetic_fixture", "/app/tests/test_portable_domain.py")
module = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(module)
module.migrate_owned_database.__wrapped__()

with TemporaryDirectory(prefix="owned-epub-reader-") as temporary:
    lifecycle = module.domain.__wrapped__(Path(temporary))
    domain = next(lifecycle)
    try:
        with SessionLocal() as db:
            book = db.get(m.Book, domain["book"])
            book.source_label = "CC0 · synthetic test text and original image, no user book"
            book.title = "灯下的来信 · 独立阅读器测试"
            db.commit()
            scope = module.export_scope(domain, db)
            data = ReadingExportInput.model_validate({"format": "epub", "scope": scope, "range": "selected",
                "startChapter": 1, "endChapter": 2, "allowUnseen": True})
            result = export_reading(db, domain["owner"], "owned-epub-readers-sample", data)
            path, _ = download_reading(db, domain["owner"], result["id"])
            copyfile(path, "/evidence/synthetic-reading.epub")
            receipt = {"sha256": digest(Path("/evidence/synthetic-reading.epub").read_bytes()), "exportJob": result,
                "database": "owned fresh tmpfs portable_test", "sourceData": "CC0 synthetic fixture, no private user book",
                "expectedChapters": ["第一章 灯下", "第二章 来信"], "expectedParagraphs": [domain["prose"], "林渡回到灯下。"],
                "realProviderCalls": 0, "readingEngineExecuted": False}
            Path("/evidence/sample-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"sampleSha256": receipt["sha256"], "size": result["size"], "chapters": result["chapterCount"]}))
    finally:
        try:
            next(lifecycle)
        except StopIteration:
            pass
        with SessionLocal() as db:
            active = db.scalar(select(func.count()).select_from(m.Task).where(
                (m.Task.state.not_in(["succeeded", "failed", "cancelled"])) | (m.Task.persistence_state == "recoverable")))
            assert active == 0
