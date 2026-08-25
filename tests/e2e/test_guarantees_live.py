"""One test per promise in `docs/guarantees.md`, executed against a deployed Relay through
its public API.

Deliberately small. CI already covers breadth against testcontainers; what CI structurally
cannot see is the reverse proxy, the production environment, the real workers and the gap
between "the suite passes against disposable containers" and "the thing on the domain does
what the README claims". Every test here must prove something from that list -- see
`docs/adr/0008-phase-8-live-verification.md`.
"""

import json
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from tests import dockerctl
from tests.e2e.config import LiveConfig
from tests.e2e.conftest import FAILING_EVENT_TYPE
from tests.e2e.live import AttemptFeed, EndpointHandle, LiveApi, Sandbox

REPO_ROOT = Path(__file__).resolve().parents[2]

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

# relay.domain.deliveries.backoff: full jitter over [0, min(cap, base * 2**attempt)).
BACKOFF_BASE_SECONDS = 1.0
# Wall-clock slack for a bound computed on the server and asserted here.
BACKOFF_TOLERANCE_SECONDS = 2.0
# delivery_max_attempts (8) with a 60s breaker cooldown between the last few attempts.
DLQ_TIMEOUT_SECONDS = 420.0


def _iso_seconds(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


# -- "an accepted (202) event is durably persisted before the response returns" ----------


def test_accepted_event_returns_202_and_a_location_for_the_created_event(
    api: LiveApi, sandbox: Sandbox
) -> None:
    """`202 Accepted` + `Location`, never a lying `200`: ingestion accepts the event for
    delivery, it does not confirm delivery."""
    response = api.trigger(sandbox, payload={"probe": "location-header"})

    assert response.status_code == 202, response.text
    body = response.json()
    assert UUID_PATTERN.match(body["id"])
    assert response.headers["Location"] == f"/v1/events/{body['id']}"


def test_an_accepted_event_is_actually_delivered_to_its_endpoint(
    api: LiveApi, sandbox: Sandbox, feed: AttemptFeed, happy_endpoint: EndpointHandle
) -> None:
    """The whole promise end to end on the deployed stack: ingest -> outbox -> relay ->
    Redis stream -> dispatcher -> a real HTTP request to a real destination, observed on the
    same live attempt timeline the console renders."""
    response = api.trigger_accepted(sandbox, payload={"probe": "delivered"})
    event_id = response.json()["id"]

    attempt = feed.wait_for(
        lambda e: e["event_id"] == event_id and e["delivery_state"] == "delivered",
        timeout=60.0,
        description=f"event {event_id} to reach delivered",
    )

    assert attempt["response_status"] == 200
    assert attempt["error_class"] is None
    assert attempt["endpoint_id"] == happy_endpoint.id
    assert attempt["attempt_no"] == 1


# -- "idempotency keys prevent duplicate logical ingestion" -----------------------------


def test_same_idempotency_key_with_an_identical_body_returns_the_original_event(
    api: LiveApi, sandbox: Sandbox
) -> None:
    key = str(uuid.uuid4())
    payload = {"probe": "idempotency", "n": 1}

    first = api.trigger_accepted(sandbox, payload=payload, idempotency_key=key)
    second = api.trigger_accepted(sandbox, payload=payload, idempotency_key=key)

    assert second.json()["id"] == first.json()["id"]


def test_same_idempotency_key_with_a_differing_body_is_a_409_problem_document(
    api: LiveApi, sandbox: Sandbox
) -> None:
    key = str(uuid.uuid4())
    api.trigger_accepted(sandbox, payload={"probe": "conflict", "n": 1}, idempotency_key=key)

    conflict = api.trigger_settled(
        sandbox, payload={"probe": "conflict", "n": 2}, idempotency_key=key
    )

    assert conflict.status_code == 409
    assert conflict.headers["content-type"].startswith("application/problem+json")
    problem = conflict.json()
    assert problem["status"] == 409
    assert problem["trace_id"] == conflict.headers["X-Trace-Id"]


# -- "retries use bounded exponential backoff with full jitter" -------------------------


def test_a_500_schedules_a_retry_inside_the_full_jitter_bound_for_that_attempt(
    api: LiveApi,
    live_config: LiveConfig,
    sandbox: Sandbox,
    feed: AttemptFeed,
    failing_endpoint: EndpointHandle,
) -> None:
    """Each retry's delay must fall inside `min(cap, base * 2**attempt)` -- the bound is
    what stops a recovering downstream from being hit by every failed delivery at once, and
    the jitter is what stops those retries arriving in lockstep."""
    response = api.trigger_accepted(sandbox, event_type=FAILING_EVENT_TYPE)
    event_id = response.json()["id"]

    for attempt_no in (1, 2):
        attempt = feed.wait_for(
            lambda e, n=attempt_no: e["event_id"] == event_id and e["attempt_no"] == n,
            timeout=90.0,
            description=f"attempt #{attempt_no} for event {event_id}",
        )
        assert attempt["response_status"] == 500
        assert attempt["error_class"] == "http_error"
        assert attempt["delivery_state"] == "retrying"

        bound = BACKOFF_BASE_SECONDS * (2 ** (attempt_no - 1))
        delay = _iso_seconds(attempt["next_retry_at"]) - _iso_seconds(attempt["created_at"])
        assert -BACKOFF_TOLERANCE_SECONDS <= delay <= bound + BACKOFF_TOLERANCE_SECONDS, (
            f"attempt #{attempt_no} scheduled its retry {delay:.2f}s out, outside the "
            f"{bound:.2f}s full-jitter bound"
        )


# -- "deliveries that exhaust their retry budget land in the DLQ and stay replayable" ----


@pytest.mark.slow
def test_exhausted_retries_land_in_the_dlq_and_replay_starts_a_fresh_chain(
    api: LiveApi, sandbox: Sandbox, feed: AttemptFeed, failing_endpoint: EndpointHandle
) -> None:
    """Slow by construction: proving the retry budget really is exhausted means waiting out
    the real backoff and the real breaker cooldown on the real deployment, which is the
    only version of this claim that isn't a `freezegun` argument."""
    response = api.trigger_accepted(sandbox, event_type=FAILING_EVENT_TYPE)
    event_id = response.json()["id"]

    dead = feed.wait_for(
        lambda e: e["event_id"] == event_id and e["delivery_state"] == "dead",
        timeout=DLQ_TIMEOUT_SECONDS,
        description=f"event {event_id} to exhaust its retry budget",
    )
    delivery_id = dead["delivery_id"]

    listed = [item for item in api.dlq(sandbox) if item["id"] == delivery_id]
    assert listed, f"delivery {delivery_id} is dead but absent from GET /v1/dlq"
    dead_delivery = listed[0]
    history = dead_delivery["attempts"]
    assert dead_delivery["state"] == "dead"
    assert dead_delivery["attempt_count"] == len(history)
    assert all(a["response_status"] == 500 for a in history if a["response_status"] is not None)

    replayed = api.replay(sandbox, delivery_id)
    assert replayed.status_code == 202, replayed.text
    assert replayed.json()["state"] == "pending"
    assert not [item for item in api.dlq(sandbox) if item["id"] == delivery_id], (
        "a replayed delivery leaves the DLQ until it dies again"
    )

    # A *fresh* chain: `reset_for_replay` zeroes attempt_count, so the new chain's attempt
    # numbering restarts at 1 while the exhausted chain's rows stay in delivery_attempts,
    # untouched and queryable by their own earlier created_at. That the old rows survive is
    # proven against a real Postgres in tests/integration/test_replay_service.py
    # (::test_replay_preserves_original_attempt_history_while_new_attempts_go_forward);
    # what only a live run can show is that replay re-enqueues onto the real stream and a
    # real dispatcher picks it up again.
    #
    # The wait is generous because the endpoint's breaker is open at this point -- it just
    # failed eight times. The first post-replay events on the timeline are breaker
    # deferrals, which carry `attempt_no: null` precisely because no request was made, and a
    # real attempt only follows once the cooldown elapses. That interaction between replay
    # and an open breaker is exactly the kind of thing that reads as obvious in the code and
    # is worth watching happen.
    fresh = feed.wait_for(
        lambda e: (
            e["delivery_id"] == delivery_id
            and e["attempt_no"] is not None
            and e["attempt_no"] >= 1
            and _iso_seconds(e["created_at"]) > _iso_seconds(dead["created_at"])
        ),
        timeout=180.0,
        description="a real delivery attempt from the replayed chain",
    )
    assert fresh["attempt_no"] == 1, (
        "a replayed delivery starts a fresh chain, so its attempt numbering restarts at 1"
    )


# -- "every outbound delivery request is HMAC-signed with a timestamp" ------------------


def test_a_real_delivery_carries_a_signature_that_verifies_against_the_endpoint_secret(
    api: LiveApi, sandbox: Sandbox, feed: AttemptFeed, happy_endpoint: EndpointHandle
) -> None:
    payload = {"probe": f"signature-{uuid.uuid4()}"}
    response = api.trigger_accepted(sandbox, payload=payload)
    event_id = response.json()["id"]

    attempt = feed.wait_for(
        lambda e: e["event_id"] == event_id and e["delivery_state"] == "delivered",
        timeout=60.0,
        description="the signed delivery",
    )
    headers = attempt["request_headers"]
    assert headers["X-Relay-Delivery-Id"] == attempt["delivery_id"]

    # The exact bytes Relay signs: json.dumps of the stored payload. A receiver has to be
    # able to reproduce them from what it received -- that reproducibility is the contract.
    body = json.dumps(payload)
    verdict = api.verify_signature(
        sandbox,
        secret=happy_endpoint.secret,
        timestamp=int(headers["X-Relay-Timestamp"]),
        body=body,
        signature=headers["X-Relay-Signature"],
    )
    assert verdict["valid"], verdict["detail"]
    assert verdict["reason"] == "valid"

    tampered = api.verify_signature(
        sandbox,
        secret=happy_endpoint.secret,
        timestamp=int(headers["X-Relay-Timestamp"]),
        body=body.replace("probe", "prob3"),
        signature=headers["X-Relay-Signature"],
    )
    # `reason`, not just `not valid` -- this has to fail *because the HMAC did not match*,
    # not because the attempt drifted out of the replay window while the test ran.
    assert tampered["reason"] == "signature_mismatch", tampered["detail"]


def test_an_out_of_repo_verifier_accepts_the_live_signature_and_rejects_a_tampered_byte(
    api: LiveApi, sandbox: Sandbox, feed: AttemptFeed, happy_endpoint: EndpointHandle
) -> None:
    """`scripts/verify_signature_independently.py` reimplements the documented signing
    contract and imports nothing from `src/` -- if it can verify a live delivery, the
    contract in `docs/guarantees.md` is sufficient for a real consumer, not merely
    self-consistent."""
    payload = {"probe": f"independent-{uuid.uuid4()}"}
    event_id = api.trigger_accepted(sandbox, payload=payload).json()["id"]
    attempt = feed.wait_for(
        lambda e: e["event_id"] == event_id and e["delivery_state"] == "delivered",
        timeout=60.0,
        description="a delivery to verify out of repo",
    )
    headers = attempt["request_headers"]
    script = REPO_ROOT / "scripts" / "verify_signature_independently.py"

    def run(body: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(script),
                "--secret",
                happy_endpoint.secret,
                "--timestamp",
                headers["X-Relay-Timestamp"],
                "--signature",
                headers["X-Relay-Signature"],
                "--body",
                body,
                "--tolerance-seconds",
                "86400",
            ],
            capture_output=True,
            text=True,
        )

    good = run(json.dumps(payload))
    assert good.returncode == 0, good.stdout + good.stderr

    tampered = json.dumps(payload)[:-2] + 'x"}'
    assert run(tampered).returncode != 0


# -- "/healthz and /readyz are genuinely distinct" ---------------------------------------


def test_healthz_and_readyz_report_different_things(api: LiveApi) -> None:
    healthz = api.get("/healthz")
    readyz = api.get("/readyz")

    assert healthz.status_code == 200
    assert healthz.json() == {"status": "ok"}
    assert readyz.status_code == 200
    assert readyz.json()["checks"] == {"postgres": "ok", "redis": "ok"}


# -- "a single correlation id ties an ingest request to every worker log line" -----------


def test_an_inbound_trace_id_is_echoed_back_on_the_response(api: LiveApi, sandbox: Sandbox) -> None:
    supplied = f"live-smoke-{uuid.uuid4()}"

    response = api.trigger_accepted(sandbox, extra_headers={"X-Trace-Id": supplied})

    assert response.headers["X-Trace-Id"] == supplied


def test_the_same_correlation_id_appears_on_api_and_worker_log_lines(
    api: LiveApi,
    sandbox: Sandbox,
    feed: AttemptFeed,
    happy_endpoint: EndpointHandle,
    requires_docker: None,
) -> None:
    """Crossing the process boundary is the whole claim, so this reads the containers' real
    stdout rather than an in-process log capture."""
    correlation_id = f"live-smoke-{uuid.uuid4()}"
    response = api.trigger_accepted(sandbox, extra_headers={"X-Trace-Id": correlation_id})
    event_id = response.json()["id"]
    feed.wait_for(
        lambda e: e["event_id"] == event_id and e["delivery_state"] == "delivered",
        timeout=60.0,
        description="the correlated delivery",
    )

    api_logs = dockerctl.logs_since(dockerctl.container("api"), "10m")
    dispatcher_logs = dockerctl.logs_since(dockerctl.container("dispatcher"), "10m")

    assert correlation_id in api_logs
    assert correlation_id in dispatcher_logs, (
        "the ingest request's correlation id never reached a dispatcher log line"
    )


# -- "every log line, from every process, is structured JSON on stdout" -----------------


@pytest.mark.parametrize("service", ["api", "relay-worker", "dispatcher", "scheduler", "reaper"])
def test_every_process_logs_structured_json_to_stdout(service: str, requires_docker: None) -> None:
    raw = dockerctl.logs(dockerctl.container(service))
    lines = [line for line in raw.splitlines() if line.strip()]
    assert lines, f"{service} has produced no log output at all"

    # Every line, not a sample: one stray print() or a library logging outside the
    # structlog bridge is exactly what this claim is about.
    non_json = [line for line in lines if not line.startswith("{")]
    assert not non_json, f"{service} emitted non-JSON log lines: {non_json[:3]}"
    parsed = [json.loads(line) for line in lines]
    assert all("event" in entry and "level" in entry for entry in parsed)


# -- "circuit breaker state and delivery outcomes are observable via Prometheus" ---------


def test_the_api_exposes_its_request_latency_histogram(api: LiveApi) -> None:
    body = api.get("/metrics").text

    assert "http_request_duration_seconds" in body


def test_the_dispatcher_exposes_attempt_outcomes_and_breaker_state(requires_docker: None) -> None:
    """The second scrape target -- deliberately not routed publicly, so this one is read
    from inside the deployment (see `docs/runbook.md`'s two-target table)."""
    body = dockerctl.docker(
        "exec",
        dockerctl.container("dispatcher"),
        "python",
        "-c",
        "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:9100/metrics')"
        ".read().decode())",
    )

    assert "delivery_attempts_total" in body
    assert "delivery_queue_depth" in body
    assert "circuit_breaker_state" in body
