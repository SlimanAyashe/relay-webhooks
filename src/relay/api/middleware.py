import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from relay.api.errors import PROBLEM_MEDIA_TYPE
from relay.infra.metrics import http_request_duration_seconds

logger = structlog.get_logger("relay.request")

TRACE_ID_HEADER = "X-Trace-Id"


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Propagates an inbound X-Trace-Id or generates a fresh one, stashes it on
    request.state for the error handlers to read, echoes it back on every response
    (success or failure) so a caller can quote it, and logs one structured line per
    request carrying it.

    This same id is also bound into structlog's contextvars as `correlation_id` for the
    duration of the request -- every log line anywhere in the call stack (this codebase's
    own loggers and structlog-native ones alike, see relay.infra.logging) picks it up
    automatically, no threading it through every function signature. relay.services.events
    carries it onward onto the Event row so relay.workers.relay/dispatcher/reaper can bind
    the same id into worker log lines for that event's deliveries; see
    docs/adr/0007-phase-5-observability-and-ops.md for why this reuses trace_id rather than
    minting a second, parallel correlation id.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = request.headers.get(TRACE_ID_HEADER) or str(uuid.uuid4())
        request.state.trace_id = trace_id
        structlog.contextvars.bind_contextvars(correlation_id=trace_id)

        try:
            start = time.monotonic()
            response = await call_next(request)
            duration_seconds = time.monotonic() - start

            response.headers[TRACE_ID_HEADER] = trace_id
            route = request.scope.get("route")
            path_label = route.path if route is not None else request.url.path
            http_request_duration_seconds.labels(
                method=request.method, path=path_label, status_code=str(response.status_code)
            ).observe(duration_seconds)
            logger.info(
                "request completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_seconds * 1000, 2),
            )
            return response
        finally:
            structlog.contextvars.clear_contextvars()


class MaxBodySizeMiddleware:
    """Rejects a request whose declared `Content-Length` exceeds `max_bytes` with a 413
    problem+json response *before* the body is ever read into memory -- raw ASGI, not
    BaseHTTPMiddleware, specifically so this check runs ahead of Starlette buffering the
    body for a downstream middleware/handler.

    A request that lies about (or omits) Content-Length while streaming an oversized
    chunked body isn't caught here -- that's a known residual gap, closed after the fact
    by relay.api.v1.events.router's own re-check of the actual received byte count once
    FastAPI has parsed the body. See docs/guarantees.md.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        content_length = next(
            (value for key, value in scope.get("headers", []) if key == b"content-length"), None
        )
        if content_length is not None:
            try:
                declared_bytes = int(content_length)
            except ValueError:
                declared_bytes = 0
            if declared_bytes > self._max_bytes:
                response = JSONResponse(
                    status_code=413,
                    media_type=PROBLEM_MEDIA_TYPE,
                    content={
                        "type": "/problems/payload-too-large",
                        "title": "Payload Too Large",
                        "status": 413,
                        "detail": (
                            f"payload too large: {declared_bytes} bytes, max {self._max_bytes}"
                        ),
                        "instance": scope.get("path", ""),
                    },
                )
                await response(scope, receive, send)
                return

        await self._app(scope, receive, send)
