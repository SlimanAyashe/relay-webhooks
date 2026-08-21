from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from relay.domain.api_keys import ApiKey
from relay.domain.api_keys.hashing import split_api_key, verify_secret
from relay.domain.tenants import Tenant
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work

_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description=(
        "A Relay API key, in the form `<prefix>.<secret>`. Scoped to a single "
        "tenant; every request is authorized and tenant-scoped by this key."
    ),
)

_INVALID_KEY_DETAIL = "missing or invalid API key"


@dataclass(frozen=True, slots=True)
class AuthContext:
    """The authenticated tenant and the key used to authenticate, scoped to this
    request. Every downstream repository/service call a route makes must be scoped to
    `tenant.id` — never to a tenant_id read from the request body or path.
    """

    tenant: Tenant
    api_key: ApiKey


async def _authenticate(presented_key: str | None, uow: UnitOfWork) -> AuthContext:
    if not presented_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_INVALID_KEY_DETAIL)

    split = split_api_key(presented_key)
    if split is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_INVALID_KEY_DETAIL)
    key_prefix, secret = split

    async with uow:
        candidates = await uow.api_keys.get_by_prefix(key_prefix)
        matched = next((k for k in candidates if verify_secret(secret, k.key_hash)), None)
        if matched is None or matched.is_revoked() or matched.is_expired(datetime.now(UTC)):
            # A sandbox key past its TTL fails exactly like a revoked one -- same 401,
            # same detail message, so an expired key doesn't leak its own expiry as a
            # distinguishable signal.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_INVALID_KEY_DETAIL)

        tenant = await uow.tenants.get(matched.tenant_id)

    if tenant is None or tenant.is_deleted():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_INVALID_KEY_DETAIL)

    return AuthContext(tenant=tenant, api_key=matched)


def require_scope(scope: str) -> Callable[..., Coroutine[Any, Any, AuthContext]]:
    """Returns a FastAPI dependency that authenticates the request and requires the
    given scope, e.g. `Depends(require_scope("endpoints:write"))`. Rejects
    missing/malformed/revoked keys with 401, and a valid key lacking the scope with 403
    -- either way, before any route handler code runs.
    """

    async def dependency(
        presented_key: str | None = Security(_api_key_header),
        uow: UnitOfWork = Depends(get_unit_of_work),
    ) -> AuthContext:
        context = await _authenticate(presented_key, uow)
        if not context.api_key.has_scope(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="insufficient scope")
        return context

    return dependency


def require_scope_allow_query_key(
    scope: str,
) -> Callable[..., Coroutine[Any, Any, AuthContext]]:
    """Like require_scope, but also accepts the key via a `?api_key=` query parameter --
    needed only for the SSE attempt stream (relay.api.v1.sandbox.router.stream_attempts),
    since a browser's native EventSource can't set the X-API-Key header. A key in the URL
    ends up in access logs and browser history, a real tradeoff accepted only for this one
    read-only, short-lived (sandbox TTL) stream -- every other route requires the header.
    """

    async def dependency(
        request: Request,
        uow: UnitOfWork = Depends(get_unit_of_work),
    ) -> AuthContext:
        presented_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        context = await _authenticate(presented_key, uow)
        if not context.api_key.has_scope(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="insufficient scope")
        return context

    return dependency
