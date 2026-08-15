import uuid
from collections.abc import Awaitable, Callable

from fastapi.testclient import TestClient

AuthHeaders = Callable[[frozenset[str]], Awaitable[tuple[uuid.UUID, dict[str, str]]]]


async def test_create_get_list_endpoint(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))

    create_response = wired_client.post(
        "/v1/endpoints",
        json={"url": "https://example.com/hook", "subscribed_event_types": ["order.created"]},
        headers=headers,
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["url"] == "https://example.com/hook"
    assert "secret" in body
    endpoint_id = body["id"]

    get_response = wired_client.get(f"/v1/endpoints/{endpoint_id}", headers=headers)
    assert get_response.status_code == 200
    assert "secret" not in get_response.json()

    list_response = wired_client.get("/v1/endpoints", headers=headers)
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert [e["id"] for e in list_body["items"]] == [endpoint_id]
    assert list_body["has_more"] is False
    assert list_body["next_cursor"] is None


async def test_create_endpoint_rejects_non_https_url(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))

    response = wired_client.post(
        "/v1/endpoints",
        json={"url": "http://example.com/hook", "subscribed_event_types": ["order.created"]},
        headers=headers,
    )

    assert response.status_code == 422


def test_missing_api_key_returns_401(wired_client: TestClient) -> None:
    response = wired_client.get("/v1/endpoints")
    assert response.status_code == 401


async def test_wrong_scope_returns_403(wired_client: TestClient, auth_headers: AuthHeaders) -> None:
    _, headers = await auth_headers(frozenset({"events:read"}))

    response = wired_client.get("/v1/endpoints", headers=headers)

    assert response.status_code == 403


async def test_get_missing_endpoint_returns_404(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))

    response = wired_client.get(f"/v1/endpoints/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404


async def test_cross_tenant_get_returns_404(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, owner_headers = await auth_headers(frozenset({"*"}))
    _, other_headers = await auth_headers(frozenset({"*"}))
    create_response = wired_client.post(
        "/v1/endpoints",
        json={"url": "https://example.com/hook", "subscribed_event_types": ["x"]},
        headers=owner_headers,
    )
    endpoint_id = create_response.json()["id"]

    response = wired_client.get(f"/v1/endpoints/{endpoint_id}", headers=other_headers)

    assert response.status_code == 404


async def test_update_and_delete_endpoint(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))
    create_response = wired_client.post(
        "/v1/endpoints",
        json={"url": "https://example.com/hook", "subscribed_event_types": ["x"]},
        headers=headers,
    )
    endpoint_id = create_response.json()["id"]

    patch_response = wired_client.patch(
        f"/v1/endpoints/{endpoint_id}", json={"status": "disabled"}, headers=headers
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["status"] == "disabled"

    delete_response = wired_client.delete(f"/v1/endpoints/{endpoint_id}", headers=headers)
    assert delete_response.status_code == 204

    get_response = wired_client.get(f"/v1/endpoints/{endpoint_id}", headers=headers)
    assert get_response.status_code == 404


async def test_list_endpoints_paginates_with_no_skips_or_duplicates(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))
    created_ids = set()
    for i in range(5):
        response = wired_client.post(
            "/v1/endpoints",
            json={"url": f"https://example.com/hook-{i}", "subscribed_event_types": ["x"]},
            headers=headers,
        )
        created_ids.add(response.json()["id"])

    seen_ids: list[str] = []
    cursor: str | None = None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        response = wired_client.get("/v1/endpoints", params=params, headers=headers)
        assert response.status_code == 200
        body = response.json()
        seen_ids.extend(item["id"] for item in body["items"])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        assert body["next_cursor"] is not None
        cursor = body["next_cursor"]

    assert len(seen_ids) == len(set(seen_ids)) == len(created_ids)
    assert set(seen_ids) == created_ids


async def test_list_endpoints_rejects_invalid_cursor(
    wired_client: TestClient, auth_headers: AuthHeaders
) -> None:
    _, headers = await auth_headers(frozenset({"*"}))

    response = wired_client.get(
        "/v1/endpoints", params={"cursor": "not-a-valid-cursor"}, headers=headers
    )

    assert response.status_code == 422
