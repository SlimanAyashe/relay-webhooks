"""Built-in mock destinations for the demo console (Delivery Theater) -- selectable as a
registered endpoint's URL so an interviewer can see every failure path (retry exhaustion,
timeout, partial failure, SSRF block) without needing their own server. Not versioned
(`/mock/...`, not `/v1/mock/...`) for the same reason /healthz isn't: these are fixture
endpoints of this service itself, not part of the public API surface.

Every route accepts POST -- the only method HttpxOutboundSender ever actually sends.
"""

import asyncio
import random

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

router = APIRouter(prefix="/mock", tags=["mock"])

# Longer than Settings.outbound_read_timeout_seconds (default 5s) so this reliably
# demonstrates the timeout attempt path rather than merely a slow success.
_SLOW_RESPONSE_SECONDS = 8.0

# Deliberately the real cloud metadata IP (not a fake/example one) -- the whole point of
# this receiver is to demonstrate the SSRF guard blocking a redirect to exactly the
# address it exists to protect. No connection to it is ever actually made; the guard
# rejects it before HttpxOutboundSender's redirect-following loop connects anywhere.
_METADATA_REDIRECT_TARGET = "http://169.254.169.254/latest/meta-data/"


@router.post("/always-200", summary="Mock receiver: always returns 200")
async def always_200(request: Request) -> JSONResponse:
    return JSONResponse({"mock": "always-200", "received_bytes": len(await request.body())})


@router.post("/always-500", summary="Mock receiver: always returns 500")
async def always_500() -> JSONResponse:
    return JSONResponse({"mock": "always-500"}, status_code=500)


@router.post("/slow-8s", summary="Mock receiver: sleeps 8s before responding 200")
async def slow_8s() -> JSONResponse:
    await asyncio.sleep(_SLOW_RESPONSE_SECONDS)
    return JSONResponse({"mock": "slow-8s"})


@router.post("/flaky-50", summary="Mock receiver: 200 or 500 with 50% probability each")
async def flaky_50() -> JSONResponse:
    if random.random() < 0.5:
        return JSONResponse({"mock": "flaky-50", "outcome": "ok"})
    return JSONResponse({"mock": "flaky-50", "outcome": "fail"}, status_code=500)


@router.post(
    "/redirect-to-metadata",
    summary="Mock receiver: redirects to the cloud metadata IP",
    description=(
        "Responds with a 307 redirect to 169.254.169.254 -- registering this as an "
        "endpoint and triggering an event demonstrates the SSRF guard blocking the "
        "redirect hop live; the attempt is recorded with error_class=ssrf_blocked and no "
        "connection to the metadata address is ever made."
    ),
)
async def redirect_to_metadata() -> RedirectResponse:
    # 307 (not 301/302): preserves the POST method and body on the hop, matching what a
    # real misconfigured receiver redirecting a webhook POST would do.
    return RedirectResponse(url=_METADATA_REDIRECT_TARGET, status_code=307)
