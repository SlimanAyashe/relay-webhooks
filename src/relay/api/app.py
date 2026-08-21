from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from relay.api.errors import install_error_handlers
from relay.api.health import router as health_router
from relay.api.middleware import MaxBodySizeMiddleware, TraceIdMiddleware
from relay.api.mock.router import router as mock_router
from relay.api.v1.deliveries.router import router as deliveries_router
from relay.api.v1.dlq.router import router as dlq_router
from relay.api.v1.endpoints.router import router as endpoints_router
from relay.api.v1.events.router import router as events_router
from relay.api.v1.sandbox.router import router as sandbox_router
from relay.infra.settings import get_settings
from relay.web.router import STATIC_DIR
from relay.web.router import router as web_router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Relay", version="0.1.0")
    app.state.settings = settings
    app.add_middleware(TraceIdMiddleware)
    # Outermost of the two: rejects an oversized request by declared Content-Length
    # before TraceIdMiddleware (or anything else) ever touches the body.
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.event_payload_max_bytes)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(endpoints_router)
    app.include_router(events_router)
    app.include_router(deliveries_router)
    app.include_router(dlq_router)
    app.include_router(sandbox_router)
    app.include_router(mock_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(web_router)
    return app


app = create_app()
