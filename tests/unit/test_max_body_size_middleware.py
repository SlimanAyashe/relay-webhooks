from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay.api.middleware import MaxBodySizeMiddleware


def _app(max_bytes: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=max_bytes)

    @app.post("/echo")
    async def echo() -> dict[str, str]:
        return {"ok": "true"}

    return app


def test_rejects_a_declared_content_length_over_the_limit() -> None:
    with TestClient(_app(max_bytes=8)) as client:
        response = client.post("/echo", content=b"x" * 100)

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["status"] == 413


def test_allows_a_body_within_the_limit() -> None:
    with TestClient(_app(max_bytes=1024)) as client:
        response = client.post("/echo", content=b"x" * 8)

    assert response.status_code == 200


def test_non_http_scope_passes_through_untouched() -> None:
    """Smoke-checks the `scope["type"] != "http"` early-return doesn't break anything
    non-HTTP (e.g. lifespan/websocket) ASGI traffic -- exercised indirectly via app
    startup, which sends a lifespan scope through every installed middleware.
    """
    with TestClient(_app(max_bytes=8)):
        pass
