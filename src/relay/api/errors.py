import logging
import math
import uuid
from typing import cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from relay.domain.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    RateLimitExceeded,
    SsrfBlocked,
    ValidationError,
)

logger = logging.getLogger("relay.errors")

PROBLEM_MEDIA_TYPE = "application/problem+json"

_TYPE_BY_STATUS: dict[int, str] = {
    status.HTTP_401_UNAUTHORIZED: "/problems/unauthorized",
    status.HTTP_403_FORBIDDEN: "/problems/forbidden",
    status.HTTP_404_NOT_FOUND: "/problems/not-found",
    status.HTTP_409_CONFLICT: "/problems/conflict",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "/problems/validation-error",
    status.HTTP_429_TOO_MANY_REQUESTS: "/problems/rate-limited",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "/problems/internal-error",
}

_TITLE_BY_STATUS: dict[int, str] = {
    status.HTTP_401_UNAUTHORIZED: "Unauthorized",
    status.HTTP_403_FORBIDDEN: "Forbidden",
    status.HTTP_404_NOT_FOUND: "Not Found",
    status.HTTP_409_CONFLICT: "Conflict",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "Validation Error",
    status.HTTP_429_TOO_MANY_REQUESTS: "Too Many Requests",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "Internal Server Error",
}

# relay.domain.errors carries no HTTP knowledge (by design -- see that module); this is
# the one place that maps its typed exceptions onto status codes. RateLimitExceeded is
# handled separately below (not in this list) since it needs a Retry-After header, not
# just a status code. SsrfBlocked is mapped here for completeness of the hierarchy even
# though nothing currently raises it across the API boundary -- see its docstring.
_DOMAIN_ERROR_STATUS: list[tuple[type[DomainError], int]] = [
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ConflictError, status.HTTP_409_CONFLICT),
    (SsrfBlocked, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (ValidationError, status.HTTP_422_UNPROCESSABLE_CONTENT),
]


def _trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", None) or str(uuid.uuid4())


def _problem_response(
    request: Request,
    status_code: int,
    detail: object,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
        content={
            "type": _TYPE_BY_STATUS.get(status_code, "about:blank"),
            "title": _TITLE_BY_STATUS.get(status_code, "Error"),
            "status": status_code,
            "detail": detail,
            "instance": request.url.path,
            "trace_id": _trace_id(request),
        },
    )


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # FastAPI's add_exception_handler types handlers as Callable[[Request, Exception], ...]
    # regardless of the specific class registered; cast() narrows for mypy since this is
    # only ever invoked for a DomainError (that's what it's registered against below).
    domain_exc = cast(DomainError, exc)
    if isinstance(domain_exc, RateLimitExceeded):
        # Round up, never down -- a Retry-After that's a fraction of a second too short
        # just invites the caller to be rejected again immediately.
        retry_after = max(1, math.ceil(domain_exc.retry_after_seconds))
        return _problem_response(
            request,
            status.HTTP_429_TOO_MANY_REQUESTS,
            str(domain_exc),
            headers={"Retry-After": str(retry_after)},
        )
    for error_type, status_code in _DOMAIN_ERROR_STATUS:
        if isinstance(domain_exc, error_type):
            return _problem_response(request, status_code, str(domain_exc))
    # A DomainError that isn't one of the known subclasses: treat as a bug, not a 4xx.
    logger.exception("unmapped DomainError subclass", extra={"trace_id": _trace_id(request)})
    return _problem_response(request, status.HTTP_500_INTERNAL_SERVER_ERROR, "internal error")


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Registered against Starlette's base HTTPException, not fastapi.HTTPException (a
    # subclass): Starlette's own router raises the base class directly for an unmatched
    # route or wrong method, and a handler registered for the subclass alone wouldn't
    # catch those -- Starlette's exception-handler lookup walks up an exception's MRO,
    # never down to a more specific registered type.
    http_exc = cast(StarletteHTTPException, exc)
    return _problem_response(request, http_exc.status_code, http_exc.detail)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    validation_exc = cast(RequestValidationError, exc)
    return _problem_response(
        request, status.HTTP_422_UNPROCESSABLE_CONTENT, validation_exc.errors()
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = _trace_id(request)
    logger.exception("unhandled exception", extra={"trace_id": trace_id})
    return _problem_response(
        request, status.HTTP_500_INTERNAL_SERVER_ERROR, "internal server error"
    )


def install_error_handlers(app: FastAPI) -> None:
    """Every exception this app can raise ends up as application/problem+json: RFC 9457's
    type/title/status/detail/instance, plus trace_id so a caller can quote it back.
    Nothing here lets a raw stack trace or exception message reach the client except via
    DomainError/HTTPException's own deliberately-written .detail/str().
    """
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
