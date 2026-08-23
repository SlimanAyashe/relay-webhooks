from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus scrape target for everything this api process itself records --
    http_request_duration_seconds, chiefly. No tenant auth: a scraper has no API key to send,
    matching how every Prometheus target works. Excluded from the OpenAPI schema like the web
    console (relay.web.router) -- this isn't part of the tenant-facing /v1 contract, and its
    text-exposition body doesn't fit the JSON response shapes documented there.

    delivery_attempts_total, circuit_breaker_state, delivery_queue_depth, and
    delivery_in_flight are recorded by the dispatcher process instead (see
    relay.infra.metrics) and scraped from its own exporter -- see
    docs/adr/0007-phase-5-observability-and-ops.md for why this is two scrape targets, not
    one.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
