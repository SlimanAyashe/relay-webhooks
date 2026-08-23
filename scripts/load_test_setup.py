#!/usr/bin/env python3
"""Provisions a full (non-sandbox) tenant, API key, and a batch of endpoints against one
of the Phase 4 mock receivers, as fixture data for the k6 load test harness
(tests/load/events_ingest.js) and its matrix runner (tests/load/run_matrix.sh).

Why this talks to the database directly instead of calling the API: the only
HTTP-reachable way to obtain a tenant identity is `POST /v1/sandbox`, and the sandbox's
hard-capped quotas (`Settings.sandbox_max_endpoints=3`, `sandbox_max_events=20`,
`sandbox_rate_limit_requests_per_second=1.0`, a 60-minute key TTL -- see
`docs/adr/0006-phase-4-demo-console.md`) exist specifically to make it useless for
sustained load. A load-test tenant needs the same unlimited-by-quota shape as a real
tenant, so this script creates one directly via the same service/repository layer the
API itself uses (`EndpointService`, `ApiKeyService`, `TenantRepository`), against
whatever `DATABASE_URL` is configured in the environment -- exactly like `alembic
upgrade head` does from `make migrate`.

Registered endpoint URLs must be `https://`: `EndpointService`'s `_validate_url` enforces
that for every tenant, load-test or not (see `docs/adr/0005-phase-3-security-resilience.md`).
`--base-url` must therefore point at an HTTPS-reachable copy of this service's `/mock/*`
router -- normally the deployed instance (e.g. `https://api.relay.bookr.tech`) -- since the
local Compose stack's Caddy only terminates plain HTTP on :8080. This script does not stand
up TLS for you; see tests/load/README.md for the operational tradeoffs this implies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from typing import TextIO
from urllib.parse import urlparse

from relay.repositories.unit_of_work import UnitOfWork, get_unit_of_work
from relay.services.api_keys.service import ApiKeyService
from relay.services.endpoints.service import EndpointService

# Maps the plan's "destination health" axis onto the Phase 4 mock receivers
# (relay.api.mock.router) that actually exist. "failing" intentionally does not mean
# always-500: the plan calls out the 50%-failing cell specifically as the one where the
# retry scheduler and circuit breaker interact and queue depth behaves non-linearly --
# always-500 would just trip every endpoint's breaker almost immediately and go quiet.
PROFILE_MOCK_PATHS = {
    "healthy": "/mock/always-200",
    "failing": "/mock/flaky-50",
}

DEFAULT_EVENT_TYPE = "load-test.ping"
# api_keys.scopes is a plain set of strings checked via ApiKey.has_scope(); "*" is the
# wildcard scope (relay.domain.api_keys.entities.WILDCARD_SCOPE) recognized there. A
# load-test tenant needs every scope a real tenant might exercise (events:write,
# endpoints:read/write) so it's simplest to grant the wildcard directly rather than
# duplicate that scope list here and have it drift.
FULL_SCOPE = frozenset({"*"})


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    tenant_id: uuid.UUID
    tenant_name: str
    api_key: str
    base_url: str
    profile: str
    mock_path: str
    event_type: str
    endpoint_ids: list[uuid.UUID] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "tenant_id": str(self.tenant_id),
            "tenant_name": self.tenant_name,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "profile": self.profile,
            "mock_path": self.mock_path,
            "event_type": self.event_type,
            "endpoint_count": len(self.endpoint_ids),
            "endpoint_ids": [str(endpoint_id) for endpoint_id in self.endpoint_ids],
        }


def _validate_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        raise SystemExit(
            "--base-url must be https:// -- registered endpoints are rejected otherwise "
            f"by relay.services.endpoints.service.EndpointService._validate_url: got {base_url!r}. "
            "See tests/load/README.md for why (the endpoint URL validation rule is a "
            "production security invariant, not something this script works around)."
        )
    if not parsed.netloc:
        raise SystemExit(f"--base-url must include a host: got {base_url!r}")


async def provision(
    *, count: int, profile: str, base_url: str, event_type: str, uow: UnitOfWork
) -> ProvisionResult:
    mock_path = PROFILE_MOCK_PATHS[profile]
    destination_url = base_url.rstrip("/") + mock_path

    tenant_name = f"load-test-{uuid.uuid4().hex[:10]}"
    async with uow:
        tenant = await uow.tenants.create(tenant_name, is_sandbox=False)
        await uow.commit()

    _api_key, plaintext_key = await ApiKeyService(uow).issue(tenant.id, FULL_SCOPE)

    endpoint_service = EndpointService(uow)
    endpoint_ids = [
        (await endpoint_service.register(tenant.id, destination_url, frozenset({event_type}))).id
        for _ in range(count)
    ]

    return ProvisionResult(
        tenant_id=tenant.id,
        tenant_name=tenant_name,
        api_key=plaintext_key,
        base_url=base_url,
        profile=profile,
        mock_path=mock_path,
        event_type=event_type,
        endpoint_ids=endpoint_ids,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="HTTPS base URL of the Relay instance whose /mock/* router the registered "
        "endpoints should target, e.g. https://api.relay.bookr.tech",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of endpoints to register, all pointing at the same mock receiver "
        "-- the plan's concurrent-destinations axis (10 or 100). Default: 10.",
    )
    parser.add_argument(
        "--profile",
        default="healthy",
        choices=sorted(PROFILE_MOCK_PATHS),
        help="'healthy' -> /mock/always-200, 'failing' -> /mock/flaky-50 (~50%% error "
        "rate) -- the plan's destination-health axis. Default: healthy.",
    )
    parser.add_argument(
        "--event-type",
        default=DEFAULT_EVENT_TYPE,
        help=f"Event type the endpoints subscribe to and the k6 script must ingest "
        f"under (RELAY_EVENT_TYPE). Default: {DEFAULT_EVENT_TYPE!r}.",
    )
    parser.add_argument(
        "--output",
        type=argparse.FileType("w"),
        default=sys.stdout,
        help="Where to write the resulting JSON fixture (default: stdout).",
    )
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be at least 1")
    return args


async def _run(args: argparse.Namespace) -> ProvisionResult:
    _validate_base_url(args.base_url)
    uow = get_unit_of_work()
    return await provision(
        count=args.count,
        profile=args.profile,
        base_url=args.base_url,
        event_type=args.event_type,
        uow=uow,
    )


def _write_result(result: ProvisionResult, output: TextIO) -> None:
    json.dump(result.to_json(), output, indent=2)
    output.write("\n")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = asyncio.run(_run(args))
    _write_result(result, args.output)
    if args.output is not sys.stdout:
        print(
            f"provisioned tenant {result.tenant_name} ({result.tenant_id}) with "
            f"{len(result.endpoint_ids)} endpoint(s) against {result.mock_path} -> "
            f"{args.output.name}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
