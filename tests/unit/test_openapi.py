from fastapi import FastAPI


def test_security_scheme_is_documented(app: FastAPI) -> None:
    spec = app.openapi()
    scheme = spec["components"]["securitySchemes"]["APIKeyHeader"]
    assert scheme["type"] == "apiKey"
    assert scheme["name"] == "X-API-Key"
    assert scheme["description"]


def test_endpoints_post_documents_error_responses_with_examples(app: FastAPI) -> None:
    spec = app.openapi()
    responses = spec["paths"]["/v1/endpoints"]["post"]["responses"]

    for status_code in ("401", "403", "422"):
        assert status_code in responses
        example = responses[status_code]["content"]["application/problem+json"]["example"]
        assert example["status"] == int(status_code)
        assert example["trace_id"]


def test_events_post_documents_409_conflict(app: FastAPI) -> None:
    spec = app.openapi()
    responses = spec["paths"]["/v1/events"]["post"]["responses"]

    assert "409" in responses
    example = responses["409"]["content"]["application/problem+json"]["example"]
    assert example["status"] == 409


def test_endpoint_create_schema_has_field_examples(app: FastAPI) -> None:
    spec = app.openapi()
    schema = spec["components"]["schemas"]["EndpointCreate"]

    assert schema["properties"]["subscribed_event_types"]["examples"]


def test_idempotency_key_header_is_documented(app: FastAPI) -> None:
    spec = app.openapi()
    params = spec["paths"]["/v1/events"]["post"]["parameters"]
    idempotency_param = next(p for p in params if p["name"] == "Idempotency-Key")

    assert idempotency_param["required"] is True
    assert idempotency_param["description"]
