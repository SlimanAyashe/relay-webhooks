from fastapi import FastAPI

from relay.api.errors import install_error_handlers
from relay.api.health import router as health_router
from relay.api.middleware import TraceIdMiddleware
from relay.api.v1.deliveries.router import router as deliveries_router
from relay.api.v1.dlq.router import router as dlq_router
from relay.api.v1.endpoints.router import router as endpoints_router
from relay.api.v1.events.router import router as events_router
from relay.infra.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Relay", version="0.1.0")
    app.state.settings = settings
    app.add_middleware(TraceIdMiddleware)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(endpoints_router)
    app.include_router(events_router)
    app.include_router(deliveries_router)
    app.include_router(dlq_router)
    return app


app = create_app()
