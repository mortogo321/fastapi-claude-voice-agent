"""HTTP middleware.

`RequestContextMiddleware` assigns every request a short request_id (client
header `X-Request-ID` wins when provided) and binds it into the structlog
contextvars registry so every log record inside the handler carries it
automatically. The same id is emitted on the response for client-side
correlation.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
_log = structlog.get_logger("http")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            _log.exception(
                "http.error",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[REQUEST_ID_HEADER] = request_id
        _log.info(
            "http.response",
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response
