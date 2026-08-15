from fastapi.testclient import TestClient


def test_app_boots_and_serves_openapi(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Relay"
