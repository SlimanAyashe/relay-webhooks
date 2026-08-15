from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from relay.domain.api_keys import ApiKey
from relay.domain.api_keys.hashing import split_api_key, verify_secret
from relay.domain.tenants import Tenant
from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

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
        if matched is None or matched.is_revoked():
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
