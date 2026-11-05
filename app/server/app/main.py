from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import settings
from .db import engine
from .errors import register_errors, Problem
from .api_auth import router as auth
from .api_meta import router as meta, world_router
from .cards.api import presets as character_card_presets, regex_scripts as character_card_regex
from .cards.api import router as cards
from .cards.personas import router as tavern_personas
from .tavern.api import router as tavern
from .cards import models as card_space_models  # noqa: F401  (registers world.card_* tables)
from .logging_config import setup_logging

setup_logging()

app = FastAPI(title="酒馆 API", version="2.0.0", docs_url="/api/reader/v1/docs", openapi_url="/api/reader/v1/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID"])


@app.middleware("http")
async def request_id(request: Request, call_next):
    request.state.request_id = uuid4().hex
    try:
        response = await call_next(request)
    except Exception as error:
        import logging
        from fastapi.responses import JSONResponse
        logging.getLogger("reader").error("request=%s error=%s", request.state.request_id, type(error).__name__)
        response = JSONResponse(status_code=503, content={"code": "PERSISTENCE_UNAVAILABLE", "message": "服务暂时不可用，请保留输入后重试", "requestId": request.state.request_id, "retryable": True, "details": {}})
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


register_errors(app)
for router in (auth, meta, cards, character_card_presets, character_card_regex, tavern_personas, tavern):
    app.include_router(router, prefix="/api/reader/v1")
app.include_router(world_router, prefix="/api/world/v1")


@app.get("/health/live")
def live():
    return {"status": "live"}


@app.get("/health/ready")
def ready():
    with engine.connect() as connection:
        tables = [connection.scalar(text("SELECT to_regclass(:name)"), {"name": name}) for name in (
            "users", "world.character_cards", "world.tavern_chats", "world.tavern_messages", "world.tavern_presets")]
    if not all(tables):
        raise Problem("MIGRATION_REQUIRED", "数据库版本未就绪", 503)
    return {"status": "ready", "tavern": "ready", "aiMode": settings.ai_mode}
