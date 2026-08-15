from fastapi import FastAPI

from relay.api.health import router as health_router
from relay.api.v1.endpoints.router import router as endpoints_router
from relay.infra.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Relay", version="0.1.0")
    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(endpoints_router)
    return app


app = create_app()
