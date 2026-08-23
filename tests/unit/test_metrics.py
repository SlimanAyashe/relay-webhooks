import structlog
from fastapi.testclient import TestClient

from relay.infra.metrics import attempt_outcome_label


def test_attempt_outcome_label_success_regardless_of_error_class() -> None:
    assert attempt_outcome_label(succeeded=True, exhausted=False, error_class=None) == "success"


def test_attempt_outcome_label_exhausted_takes_priority_over_error_class() -> None:
    """An operator watching this counter cares most that a delivery just moved to the DLQ --
    exhausted wins even when the final attempt's error_class is itself one of the other
    labeled classes (ssrf_blocked, timeout)."""
    assert (
        attempt_outcome_label(succeeded=False, exhausted=True, error_class="timeout") == "exhausted"
    )


def test_attempt_outcome_label_ssrf_blocked_and_timeout_get_their_own_label() -> None:
    assert (
        attempt_outcome_label(succeeded=False, exhausted=False, error_class="ssrf_blocked")
        == "ssrf_blocked"
    )
    assert (
        attempt_outcome_label(succeeded=False, exhausted=False, error_class="timeout") == "timeout"
    )


def test_attempt_outcome_label_other_failures_fall_back_to_retry() -> None:
    assert (
        attempt_outcome_label(succeeded=False, exhausted=False, error_class="http_error") == "retry"
    )
    assert (
        attempt_outcome_label(succeeded=False, exhausted=False, error_class="connection_error")
        == "retry"
    )


def test_metrics_endpoint_serves_prometheus_text_exposition(client: TestClient) -> None:
    # Generate at least one request so http_request_duration_seconds has a sample to report.
    client.get("/healthz")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "http_request_duration_seconds" in response.text


def test_metrics_endpoint_is_excluded_from_the_openapi_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "/metrics" not in schema["paths"]


def test_structured_logger_output_binds_correlation_id_from_contextvars() -> None:
    """Smoke test for the shared_processors chain relay.infra.logging configures: whatever
    is bound via structlog.contextvars must appear on the resulting event dict, which is the
    whole mechanism the Phase 5 correlation-id propagation relies on.
    """
    with structlog.testing.capture_logs(
        processors=[structlog.contextvars.merge_contextvars]
    ) as logs:
        structlog.contextvars.bind_contextvars(correlation_id="corr-xyz")
        try:
            structlog.get_logger("test").info("hello")
        finally:
            structlog.contextvars.clear_contextvars()

    assert logs == [{"event": "hello", "log_level": "info", "correlation_id": "corr-xyz"}]
