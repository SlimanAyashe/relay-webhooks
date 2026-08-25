"""The twelve failure scenarios the project plan sets as its definition of done, each
mapped to a named test that exists.

This is the audit itself, executable: the mapping lives here as data, and the tests below
fail the build if any scenario loses its proof -- a rename, a deletion, or a scenario
quietly resting on "probably covered somewhere". `docs/failure-scenarios.md` is the same
table in prose and is checked against this one so the two can't drift.
"""

from typing import NamedTuple

import pytest

from tests import testrefs


class Scenario(NamedTuple):
    number: int
    title: str
    proofs: tuple[str, ...]
    live_proofs: tuple[str, ...] = ()
    """Live/chaos tests (tests/e2e, tests/chaos) that prove the same claim against a
    deployed stack. Not run in CI -- see docs/adr/0009-phase-8-live-verification.md -- but
    still audited for existence."""


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        1,
        "Process crashes after the DB commit but before the Redis publish -> relay recovers it",
        (
            "tests/integration/test_relay_worker.py::test_outbox_row_committed_before_a_relay_run_is_recovered_on_the_next_run",
        ),
        (
            "tests/chaos/test_relay_down_across_ingest.py::test_an_event_accepted_while_the_relay_is_dead_is_delivered_once_it_returns",
        ),
    ),
    Scenario(
        2,
        "Two relay instances race for the same outbox row -> SKIP LOCKED gives it to exactly one",
        (
            "tests/integration/test_outbox_repository.py::test_skip_locked_gives_a_claimed_row_to_exactly_one_concurrent_claimer",
        ),
    ),
    Scenario(
        3,
        "Worker dies after XREADGROUP but before processing -> XAUTOCLAIM reclaims it",
        (
            "tests/integration/test_reaper_worker.py::test_run_once_reclaims_and_processes_a_message_a_dead_consumer_never_acked",
            "tests/integration/test_reaper_worker.py::test_run_once_leaves_recently_read_messages_alone",
        ),
        (
            "tests/chaos/test_dispatcher_killed_mid_delivery.py::test_a_killed_dispatcher_is_reclaimed_under_the_same_id_and_the_receiver_sees_a_duplicate",
        ),
    ),
    Scenario(
        4,
        "Worker dies after the HTTP request is sent but before acking -> the event is "
        "redelivered and the receiver sees a duplicate",
        (
            "tests/integration/test_dispatcher_worker.py::test_redelivery_after_a_post_send_pre_ack_crash_produces_an_observable_duplicate",
        ),
        (
            "tests/chaos/test_dispatcher_killed_mid_delivery.py::test_a_killed_dispatcher_is_reclaimed_under_the_same_id_and_the_receiver_sees_a_duplicate",
        ),
    ),
    Scenario(
        5,
        "Duplicate event with the same Idempotency-Key -> one event row, one delivery",
        (
            "tests/integration/test_event_ingest_service.py::test_duplicate_key_identical_body_returns_original_no_new_row",
            "tests/integration/test_event_ingest_service.py::test_duplicate_key_identical_body_does_not_create_a_second_outbox_row",
            "tests/integration/test_relay_worker.py::test_duplicate_ingest_fans_out_exactly_one_delivery",
            "tests/integration/test_events_router.py::test_ingest_duplicate_key_identical_body_returns_same_event",
        ),
        (
            "tests/e2e/test_guarantees_live.py::test_same_idempotency_key_with_an_identical_body_returns_the_original_event",
        ),
    ),
    Scenario(
        6,
        "Same key, different body -> 409",
        (
            "tests/integration/test_event_ingest_service.py::test_duplicate_key_differing_body_raises_conflict_no_new_row",
            "tests/integration/test_events_router.py::test_ingest_duplicate_key_differing_body_returns_409",
            "tests/integration/test_error_handling.py::test_409_conflict_response_matches_rfc9457_shape",
        ),
        (
            "tests/e2e/test_guarantees_live.py::test_same_idempotency_key_with_a_differing_body_is_a_409_problem_document",
        ),
    ),
    Scenario(
        7,
        "Downstream returns 500 -> retry scheduled with correctly-jittered backoff",
        (
            "tests/integration/test_dispatcher_worker.py::test_process_delivery_message_on_500_schedules_jittered_backoff",
            "tests/unit/test_backoff.py::test_bound_doubles_each_attempt_until_capped",
            "tests/unit/test_backoff.py::test_jitter_produces_varying_delays_across_calls",
        ),
        (
            "tests/e2e/test_guarantees_live.py::test_a_500_schedules_a_retry_inside_the_full_jitter_bound_for_that_attempt",
        ),
    ),
    Scenario(
        8,
        "Downstream times out mid-response -> attempt recorded as timeout, not success",
        (
            "tests/integration/test_dispatcher_worker.py::test_process_delivery_message_records_timeout_not_success",
            "tests/integration/test_dispatcher_worker.py::test_httpx_sender_classifies_a_real_timeout",
        ),
    ),
    Scenario(
        9,
        "A retry becomes due -> the scheduler moves it from the ZSET back into the stream",
        ("tests/integration/test_scheduler_worker.py::test_run_once_fires_only_due_retries",),
    ),
    Scenario(
        10,
        "Consecutive failures -> breaker opens; after cooldown -> half-open; one success -> closed",
        (
            "tests/integration/test_circuit_breaker.py::test_full_breaker_cycle_closed_open_half_open_closed",
            "tests/unit/test_breaker.py::test_full_cycle_closed_open_half_open_closed",
        ),
    ),
    Scenario(
        11,
        "Destination redirects to a forbidden IP -> blocked, with no connection made to it",
        (
            "tests/integration/test_http_sender_ssrf.py::test_redirect_to_forbidden_ip_is_blocked_with_no_connection_made",
            "tests/integration/test_mock_redirect_to_metadata_ssrf.py::test_redirect_to_metadata_mock_is_blocked_end_to_end_with_no_connection_made",
        ),
        (
            "tests/e2e/test_ssrf_live.py::test_a_forbidden_destination_is_blocked_with_no_connection_made",
            "tests/e2e/test_ssrf_live.py::test_the_redirect_probe_reached_the_mock_but_never_the_metadata_address",
        ),
    ),
    Scenario(
        12,
        "Revoked or malformed API key -> 401, and a valid key from tenant A cannot read tenant B",
        (
            "tests/integration/test_auth.py::test_authenticate_rejects_revoked_key",
            "tests/integration/test_auth.py::test_authenticate_rejects_malformed_key",
            "tests/integration/test_tenant_isolation.py::test_tenant_a_key_cannot_read_or_modify_tenant_b_endpoint",
        ),
        (
            "tests/e2e/test_auth_isolation_live.py::test_a_revoked_key_is_rejected_on_the_very_next_request",
            "tests/e2e/test_auth_isolation_live.py::test_one_tenants_key_cannot_read_another_tenants_endpoint",
        ),
    ),
)

AUDIT_DOC = testrefs.REPO_ROOT / "docs" / "failure-scenarios.md"


def test_all_twelve_scenarios_are_accounted_for() -> None:
    assert [scenario.number for scenario in SCENARIOS] == list(range(1, 13))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[str(s.number) for s in SCENARIOS])
def test_every_scenario_names_a_test_that_exists(scenario: Scenario) -> None:
    assert scenario.proofs, f"scenario #{scenario.number} names no proof at all"
    missing = testrefs.missing(list(scenario.proofs + scenario.live_proofs))
    assert not missing, f"scenario #{scenario.number} names tests that don't exist: {missing}"


def test_the_audit_document_lists_every_scenario_and_the_tests_that_prove_it() -> None:
    """`docs/failure-scenarios.md` is the human-readable half of this table. If it drifts,
    the version an interviewer reads stops matching the version that runs."""
    document = AUDIT_DOC.read_text(encoding="utf-8")

    for scenario in SCENARIOS:
        assert f"| {scenario.number} |" in document, f"scenario #{scenario.number} is undocumented"
        for node_id in scenario.proofs + scenario.live_proofs:
            assert node_id in document, (
                f"{node_id} proves scenario #{scenario.number} but isn't named in {AUDIT_DOC.name}"
            )


def test_the_audit_document_names_no_test_that_has_since_disappeared() -> None:
    missing = testrefs.missing(testrefs.node_ids_in_text(AUDIT_DOC.read_text(encoding="utf-8")))
    assert not missing, f"{AUDIT_DOC.name} points at tests that no longer exist: {missing}"
