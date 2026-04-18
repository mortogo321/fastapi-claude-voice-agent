from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import __version__
from app.config import get_settings
from app.logging import configure_logging, get_logger
from app.persistence.db import dispose_engine, init_engine
from app.routers import sessions, twilio, webrtc

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    log.info("startup", env=settings.env, model=settings.anthropic_model)
    await init_engine(settings.database_url)
    try:
        yield
    finally:
        await dispose_engine()
        log.info("shutdown")


app = FastAPI(
    title="fastapi-claude-voice-agent",
    version=__version__,
    description="Realtime voice AI agent on Claude Opus 4.7.",
    lifespan=lifespan,
)

app.include_router(twilio.router, prefix="/voice", tags=["twilio"])
app.include_router(webrtc.router, prefix="/webrtc", tags=["webrtc"])
app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": __version__})


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": "fastapi-claude-voice-agent", "version": __version__}
