from uuid import uuid4
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from .config import settings
from .db import engine
from .errors import register_errors, Problem
from .api_auth import router as auth
from .api_books import router as books
from .api_ai import router as ai
from .api_reading import router as reading
from .api_story import router as story
from .api_stories import router as stories
from .api_characters import router as characters
from .api_playback import router as playback
from .api_comparison import router as comparison
from .prose.api import router as prose
from .assets.api import router as assets
from .portable.api import router as portable
from .portable.upload_limit import BackupUploadLimit
from .world.api import router as worlds, config_router as world_config
from .logging_config import setup_logging
from .foundation.api import router as product, waffo_webhook
from .foundation.analytics import router as analytics
from .foundation.prompts import router as author_prompts
from .foundation.vision_api import router as vision_models
from .authoring.api import router as authoring
from .play_controls.api import router as play_controls
from .foundation.upload_limit import ProductUploadLimit
from .experience.api import router as experience
from .cards.api import group_chats as character_card_groups, presets as character_card_presets, regex_scripts as character_card_regex
from .cards.api import router as cards
from .cards import models as card_space_models  # noqa: F401  (registers world.card_lore/chat_presets/regex_scripts/card_group_chats)
from .experience import curated as reviewed_library
from .experience import usage as action_usage
from .experience import beta as beta_operations

setup_logging()

app = FastAPI(title="故事阅读私人 API", version="1.0.0", docs_url="/api/reader/v1/docs", openapi_url="/api/reader/v1/openapi.json")
app.add_middleware(BackupUploadLimit)
app.add_middleware(ProductUploadLimit)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID"])


@app.middleware("http")
async def request_id(request: Request, call_next):
    request.state.request_id = uuid4().hex
    try:
        response = await call_next(request)
    except Exception as error:
        import logging
        from fastapi.responses import JSONResponse
        logging.getLogger("reader").error("request=%s error=%s", request.state.request_id, type(error).__name__)
        response = JSONResponse(status_code=503, content={"code":"PERSISTENCE_UNAVAILABLE","message":"服务暂时不可用，请保留输入后重试","requestId":request.state.request_id,"retryable":True,"details":{}})
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


register_errors(app)
for router in (auth, books, ai, reading, story, stories, characters, playback, comparison, prose, assets, portable, product, analytics, author_prompts, authoring, vision_models, play_controls, experience, cards, character_card_presets, character_card_regex, character_card_groups):
    app.include_router(router, prefix="/api/reader/v1")
app.include_router(world_config, prefix="/api/world/v1")
app.include_router(worlds, prefix="/api/world/v1")
from .scripts.api import router as scripts
app.include_router(scripts, prefix="/api/world/v1")
from .scenes.api import router as scenes
from .scenes.worker import start as start_scenes, shutdown as stop_scenes
app.include_router(scenes, prefix="/api/world/v1")
app.add_event_handler("startup", start_scenes)
app.add_event_handler("shutdown", stop_scenes)
app.add_api_route("/api/v1/payments/waffo/webhook", waffo_webhook, methods=["POST"], include_in_schema=False)


@app.get("/health/live")
def live():
    return {"status": "live"}


@app.get("/health/ready")
def ready():
    from .foundation.models import Account
    product_tables = tuple(t.name for t in Account.metadata.sorted_tables if t.name.startswith("product_"))
    with engine.connect() as connection:
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if version != "0022" or not connection.scalar(text("SELECT to_regclass('product_accounts')")) or not connection.scalar(text("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='usage' AND column_name='billing_metadata')")) or not all(connection.scalar(text("SELECT to_regclass(:name)"), {"name": name})
                for name in (*product_tables, "image_assets", "image_asset_variants", "image_asset_placements", "portable_operations", "playback_positions", "comparison_exposures", "play_controls", "play_memory_revisions", "world_chat_sessions", "world_chat_messages")) or not connection.scalar(text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name='user_settings' AND column_name='speech_preferences')")):
            raise Problem("MIGRATION_REQUIRED", "数据库版本未就绪", 503)
        configured = bool(connection.scalar(text("SELECT EXISTS (SELECT 1 FROM product_config WHERE key='platform-models' AND value->'text'->>'key_cipher' IS NOT NULL)")))
    return {"status": "ready", "reading": "ready", "aiMode": settings.ai_mode, "modelConfigured": configured}
