from fastapi.testclient import TestClient


def test_always_200_returns_200(client: TestClient) -> None:
    response = client.post("/mock/always-200", content=b'{"a":1}')
    assert response.status_code == 200


def test_always_500_returns_500(client: TestClient) -> None:
    response = client.post("/mock/always-500")
    assert response.status_code == 500


def test_flaky_50_returns_200_or_500(client: TestClient) -> None:
    statuses = {client.post("/mock/flaky-50").status_code for _ in range(30)}
    assert statuses <= {200, 500}
    # Overwhelmingly likely with 30 coin flips; a flake here would mean the randomness
    # collapsed to a constant, not just bad luck.
    assert len(statuses) == 2


def test_redirect_to_metadata_returns_a_redirect_to_the_metadata_ip(client: TestClient) -> None:
    response = client.post("/mock/redirect-to-metadata", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "http://169.254.169.254/latest/meta-data/"
