"""Actual same-task EPUB parsing interleaves at atomic image publication."""
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, current_thread
from unittest.mock import patch
from pathlib import Path

from PIL import Image

from app.db import SessionLocal, engine
from app.config import settings
from app.models import Task, UsageRecord, now
from app.storage import private_path
from app.worker import claim, process
from sqlalchemy import select
from .helpers import BASE


def image_epub():
    image = io.BytesIO()
    Image.new("RGB", (8, 8), (31, 82, 63)).save(image, format="PNG")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        archive.writestr("OPS/book.opf", '<package version="3.0"><metadata><title>并发图片夹具</title></metadata><manifest><item id="c" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c"/></spine></package>')
        archive.writestr("OPS/chapter.xhtml", '<html><body><h1>第一章</h1><p>只属于测试的短篇。<img src="picture.png"/></p></body></html>')
        archive.writestr("OPS/picture.png", image.getvalue())
    return output.getvalue(), image.getvalue()


def test_reclaimed_epub_parsers_have_independent_atomic_image_files(client, reader):
    assert settings.ai_mode == "mock" and engine.url.database == "reader_test"
    content, expected_image = image_epub()
    uploaded = client.post(BASE + "/books/imports", files={"file": ("lease.epub", content, "application/epub+zip")},
                           headers={"Idempotency-Key": "lease-epub-images"})
    assert uploaded.status_code == 202, uploaded.text
    task_id = uploaded.json()["taskId"]
    a = claim("same-worker")
    assert a == task_id
    old_at_replace, new_at_replace, release_old, release_new = (Event() for _ in range(4))
    import os
    actual_replace = os.replace
    def paused_replace(source, target):
        if Path(target).name.startswith("asset-"):
            if current_thread().name.startswith("old-epub-lease"):
                old_at_replace.set()
                assert release_old.wait(10)
            elif current_thread().name.startswith("new-epub-lease"):
                new_at_replace.set()
                assert release_new.wait(10)
        return actual_replace(source, target)
    with patch("app.storage.os.replace", paused_replace), ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-epub-lease") as old_pool, ThreadPoolExecutor(max_workers=1, thread_name_prefix="new-epub-lease") as new_pool:
        old = old_pool.submit(process, a)
        try:
            assert old_at_replace.wait(10)
            with SessionLocal() as db:
                db.get(Task, task_id).lease_expires_at = now() - timedelta(seconds=1)
                db.commit()
            b = claim("same-worker")
            assert b == task_id and b.lease_token != a.lease_token
            new = new_pool.submit(process, b)
            assert new_at_replace.wait(10)
            release_old.set()
            old.result(timeout=10)
        finally:
            release_old.set()
            release_new.set()
        new.result(timeout=10)
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task.state == "succeeded" and not task.error_code, (task.state, task.error_code, task.result)
        assert task.result["importStatus"] == "awaiting_confirmation"
        parsed = json.loads(private_path(task.owner_id, task.checkpoint["parsedRef"]).read_text("utf-8"))
        assert len(parsed["assets"]) == 1
        assert private_path(task.owner_id, next(iter(parsed["assets"].values()))).read_bytes() == expected_image
        assert not list((settings.storage_path / task.owner_id).glob("*.tmp"))
        assert not db.scalar(select(UsageRecord.id).where(UsageRecord.task_id == task_id))
