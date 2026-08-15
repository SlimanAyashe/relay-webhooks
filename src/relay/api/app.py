from fastapi import FastAPI

from relay.api.health import router as health_router
from relay.infra.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Relay", version="0.1.0")
    app.state.settings = settings
    app.include_router(health_router)
    return app


app = create_app()
