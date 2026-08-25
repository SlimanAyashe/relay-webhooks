# The twelve failure scenarios

The project plan names twelve scenarios and calls them the definition of done -- not a
coverage number, twelve specific things that either happen correctly or don't. This is the
audit of that list: every scenario, the named test that proves it, and (where Phase 8 added
one) the live or chaos test that proves the same claim against the deployed stack rather
than against testcontainers.

The table is generated from the same data the audit test asserts on
(`tests/unit/test_failure_scenario_audit.py`), so a renamed or deleted test fails the build
here rather than leaving a row that reads like evidence and isn't. Run the audit with:

```bash
uv run pytest tests/unit/test_failure_scenario_audit.py
```

**How to read the columns.** *Proof* runs in CI on every pull request. *Live proof* needs a
running deployment and is run on demand and before releases -- see
`docs/live-verification.md` for how, and `docs/adr/0009-phase-8-live-verification.md` for
why crash recovery is proven by killing containers rather than by injecting faults.

| # | Scenario | Proof (CI) | Live proof (on demand) |
| --- | --- | --- | --- |
| 1 | Process crashes after the DB commit but before the Redis publish -> relay recovers it | `tests/integration/test_relay_worker.py::test_outbox_row_committed_before_a_relay_run_is_recovered_on_the_next_run` | `tests/chaos/test_relay_down_across_ingest.py::test_an_event_accepted_while_the_relay_is_dead_is_delivered_once_it_returns` |
| 2 | Two relay instances race for the same outbox row -> SKIP LOCKED gives it to exactly one | `tests/integration/test_outbox_repository.py::test_skip_locked_gives_a_claimed_row_to_exactly_one_concurrent_claimer` | -- |
| 3 | Worker dies after XREADGROUP but before processing -> XAUTOCLAIM reclaims it | `tests/integration/test_reaper_worker.py::test_run_once_reclaims_and_processes_a_message_a_dead_consumer_never_acked`<br>`tests/integration/test_reaper_worker.py::test_run_once_leaves_recently_read_messages_alone` | `tests/chaos/test_dispatcher_killed_mid_delivery.py::test_a_killed_dispatcher_is_reclaimed_under_the_same_id_and_the_receiver_sees_a_duplicate` |
| 4 | Worker dies after the HTTP request is sent but before acking -> the event is redelivered and the receiver sees a duplicate | `tests/integration/test_dispatcher_worker.py::test_redelivery_after_a_post_send_pre_ack_crash_produces_an_observable_duplicate` | `tests/chaos/test_dispatcher_killed_mid_delivery.py::test_a_killed_dispatcher_is_reclaimed_under_the_same_id_and_the_receiver_sees_a_duplicate` |
| 5 | Duplicate event with the same Idempotency-Key -> one event row, one delivery | `tests/integration/test_event_ingest_service.py::test_duplicate_key_identical_body_returns_original_no_new_row`<br>`tests/integration/test_event_ingest_service.py::test_duplicate_key_identical_body_does_not_create_a_second_outbox_row`<br>`tests/integration/test_relay_worker.py::test_duplicate_ingest_fans_out_exactly_one_delivery`<br>`tests/integration/test_events_router.py::test_ingest_duplicate_key_identical_body_returns_same_event` | `tests/e2e/test_guarantees_live.py::test_same_idempotency_key_with_an_identical_body_returns_the_original_event` |
| 6 | Same key, different body -> 409 | `tests/integration/test_event_ingest_service.py::test_duplicate_key_differing_body_raises_conflict_no_new_row`<br>`tests/integration/test_events_router.py::test_ingest_duplicate_key_differing_body_returns_409`<br>`tests/integration/test_error_handling.py::test_409_conflict_response_matches_rfc9457_shape` | `tests/e2e/test_guarantees_live.py::test_same_idempotency_key_with_a_differing_body_is_a_409_problem_document` |
| 7 | Downstream returns 500 -> retry scheduled with correctly-jittered backoff | `tests/integration/test_dispatcher_worker.py::test_process_delivery_message_on_500_schedules_jittered_backoff`<br>`tests/unit/test_backoff.py::test_bound_doubles_each_attempt_until_capped`<br>`tests/unit/test_backoff.py::test_jitter_produces_varying_delays_across_calls` | `tests/e2e/test_guarantees_live.py::test_a_500_schedules_a_retry_inside_the_full_jitter_bound_for_that_attempt` |
| 8 | Downstream times out mid-response -> attempt recorded as timeout, not success | `tests/integration/test_dispatcher_worker.py::test_process_delivery_message_records_timeout_not_success`<br>`tests/integration/test_dispatcher_worker.py::test_httpx_sender_classifies_a_real_timeout` | -- |
| 9 | A retry becomes due -> the scheduler moves it from the ZSET back into the stream | `tests/integration/test_scheduler_worker.py::test_run_once_fires_only_due_retries` | -- |
| 10 | Consecutive failures -> breaker opens; after cooldown -> half-open; one success -> closed | `tests/integration/test_circuit_breaker.py::test_full_breaker_cycle_closed_open_half_open_closed`<br>`tests/unit/test_breaker.py::test_full_cycle_closed_open_half_open_closed` | -- |
| 11 | Destination redirects to a forbidden IP -> blocked, with no connection made to it | `tests/integration/test_http_sender_ssrf.py::test_redirect_to_forbidden_ip_is_blocked_with_no_connection_made`<br>`tests/integration/test_mock_redirect_to_metadata_ssrf.py::test_redirect_to_metadata_mock_is_blocked_end_to_end_with_no_connection_made` | `tests/e2e/test_ssrf_live.py::test_a_forbidden_destination_is_blocked_with_no_connection_made`<br>`tests/e2e/test_ssrf_live.py::test_the_redirect_probe_reached_the_mock_but_never_the_metadata_address` |
| 12 | Revoked or malformed API key -> 401, and a valid key from tenant A cannot read tenant B | `tests/integration/test_auth.py::test_authenticate_rejects_revoked_key`<br>`tests/integration/test_auth.py::test_authenticate_rejects_malformed_key`<br>`tests/integration/test_tenant_isolation.py::test_tenant_a_key_cannot_read_or_modify_tenant_b_endpoint` | `tests/e2e/test_auth_isolation_live.py::test_a_revoked_key_is_rejected_on_the_very_next_request`<br>`tests/e2e/test_auth_isolation_live.py::test_one_tenants_key_cannot_read_another_tenants_endpoint` |

## Scenarios with no live counterpart, and why

- **#2 (`SKIP LOCKED`), #9 (retry ZSET promotion)** -- both are proven by driving two
  concurrent claimers, or a clock, against a real Postgres/Redis. A live run would assert
  the same thing more slowly and less deterministically, with no additional deployment-only
  risk being covered: neither depends on the proxy, the environment, or the image.
- **#8 (timeout classification)** -- the `slow-8s` mock does exercise this path live (it is
  how the chaos test gets a delivery to stay in flight long enough to kill a worker), but
  the assertion that a timeout is recorded *as a timeout* rather than as a success is
  identical in both places, so it is not duplicated as a separate live test.
- **#10 (breaker cycle)** -- the closed -> open half of the cycle happens live during the
  DLQ smoke test; the half-open probe needs a 60s cooldown per transition, which
  `freezegun` skips in CI and a live test would simply have to wait out. The live suite
  asserts the breaker state that the console displays, not the full cycle.

## Gap closed while writing this audit

Scenario #5 asks for "one event row, **one delivery**". The suite proved the event row and
the outbox row, but nothing asserted that a duplicate ingest fans out exactly one delivery
-- the version of the bug a receiver would actually notice.
`tests/integration/test_relay_worker.py::test_duplicate_ingest_fans_out_exactly_one_delivery`
was written to close it.

