"""Keep destructive archive fixtures confined to their named databases."""
import os
from pathlib import Path
from app.config import settings
from app.db import engine


def require_owned_runtime(database, container_storage):
    assert database.endswith("_test") and engine.url.database == database
    assert settings.ai_mode == "mock"
    if engine.url.host == "db":
        assert settings.storage_path == Path(container_storage)
        return
    root = Path(__file__).resolve().parents[2] / "artifacts/private"
    configured = os.environ.get("NATIVE_TEST_STORAGE", "")
    port = int(os.environ.get("READER_TEST_POSTGRES_PORT", "55432"))
    assert port in (55432, 55433)
    assert configured and engine.url.host == "127.0.0.1" and engine.url.port == port
    path = Path(configured).resolve()
    assert path.is_relative_to(root.resolve()) and settings.storage_path.resolve() == path
