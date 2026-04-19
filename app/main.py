from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.concurrency import CallGate
from app.config import Settings, get_settings
from app.logging import configure_logging, get_logger
from app.middleware import RequestContextMiddleware
from app.persistence.db import dispose_engine, init_engine
from app.routers import sessions, twilio, webrtc

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings: Settings = get_settings()
    log.info(
        "startup",
        env=settings.env,
        model=settings.anthropic_model,
        max_concurrent_calls=settings.max_concurrent_calls,
    )
    await init_engine(settings.database_url)
    app.state.call_gate = CallGate(settings.max_concurrent_calls)
    app.state.settings = settings
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

app.add_middleware(RequestContextMiddleware)

app.include_router(twilio.router, prefix="/voice", tags=["twilio"])
app.include_router(webrtc.router, prefix="/webrtc", tags=["webrtc"])
app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    log.warning(
        "http.client_error",
        status=exc.status_code,
        detail=exc.detail,
        path=request.url.path,
    )
    return JSONResponse(
        {"error": {"status": exc.status_code, "detail": exc.detail}},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    log.warning("http.validation_error", errors=exc.errors(), path=request.url.path)
    return JSONResponse(
        {"error": {"status": 422, "detail": "validation_error", "errors": exc.errors()}},
        status_code=422,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("http.unhandled", path=request.url.path, err=str(exc))
    return JSONResponse(
        {"error": {"status": 500, "detail": "internal_error"}},
        status_code=500,
    )


@app.get("/health", include_in_schema=False)
async def health(request: Request) -> JSONResponse:
    gate: CallGate = request.app.state.call_gate
    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
            "calls": {"active": gate.active, "max": gate.max},
        }
    )


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": "fastapi-claude-voice-agent", "version": __version__}
