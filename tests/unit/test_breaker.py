from datetime import UTC, datetime, timedelta

from relay.domain.endpoints import BreakerState
from relay.domain.endpoints.breaker import next_breaker_state, should_skip_for_open_breaker

NOW = datetime(2026, 1, 1, tzinfo=UTC)
THRESHOLD = 3
COOLDOWN = timedelta(seconds=60)


def test_closed_stays_closed_on_success_and_resets_failures() -> None:
    transition = next_breaker_state(
        current_state=BreakerState.CLOSED,
        consecutive_failures=2,
        now=NOW,
        success=True,
        failure_threshold=THRESHOLD,
    )
    assert transition.state is BreakerState.CLOSED
    assert transition.consecutive_failures == 0
    assert transition.opened_at is None


def test_closed_stays_closed_below_threshold_on_failure() -> None:
    transition = next_breaker_state(
        current_state=BreakerState.CLOSED,
        consecutive_failures=1,
        now=NOW,
        success=False,
        failure_threshold=THRESHOLD,
    )
    assert transition.state is BreakerState.CLOSED
    assert transition.consecutive_failures == 2
    assert transition.opened_at is None


def test_closed_opens_once_failure_threshold_is_reached() -> None:
    transition = next_breaker_state(
        current_state=BreakerState.CLOSED,
        consecutive_failures=THRESHOLD - 1,
        now=NOW,
        success=False,
        failure_threshold=THRESHOLD,
    )
    assert transition.state is BreakerState.OPEN
    assert transition.consecutive_failures == THRESHOLD
    assert transition.opened_at == NOW


def test_half_open_closes_on_success() -> None:
    transition = next_breaker_state(
        current_state=BreakerState.HALF_OPEN,
        consecutive_failures=THRESHOLD,
        now=NOW,
        success=True,
        failure_threshold=THRESHOLD,
    )
    assert transition.state is BreakerState.CLOSED
    assert transition.consecutive_failures == 0
    assert transition.opened_at is None


def test_half_open_reopens_immediately_on_probe_failure() -> None:
    """One failed probe is enough evidence to reopen -- it doesn't need to accumulate a
    fresh threshold's worth of failures first.
    """
    transition = next_breaker_state(
        current_state=BreakerState.HALF_OPEN,
        consecutive_failures=THRESHOLD,
        now=NOW,
        success=False,
        failure_threshold=THRESHOLD,
    )
    assert transition.state is BreakerState.OPEN
    assert transition.consecutive_failures == THRESHOLD + 1
    assert transition.opened_at == NOW


def test_should_skip_when_open_and_cooldown_not_elapsed() -> None:
    opened_at = NOW
    now = NOW + timedelta(seconds=1)
    assert should_skip_for_open_breaker(
        state=BreakerState.OPEN, opened_at=opened_at, now=now, cooldown=COOLDOWN
    )


def test_should_not_skip_when_open_and_cooldown_elapsed() -> None:
    opened_at = NOW
    now = NOW + COOLDOWN
    assert not should_skip_for_open_breaker(
        state=BreakerState.OPEN, opened_at=opened_at, now=now, cooldown=COOLDOWN
    )


def test_should_not_skip_when_closed() -> None:
    assert not should_skip_for_open_breaker(
        state=BreakerState.CLOSED, opened_at=None, now=NOW, cooldown=COOLDOWN
    )


def test_should_not_skip_when_half_open() -> None:
    """HALF_OPEN always allows its one probe through -- never skipped."""
    assert not should_skip_for_open_breaker(
        state=BreakerState.HALF_OPEN, opened_at=NOW, now=NOW, cooldown=COOLDOWN
    )


def test_full_cycle_closed_open_half_open_closed() -> None:
    """closed -> (threshold failures) -> open -> (cooldown elapses, one probe) -> half_open
    -> (probe succeeds) -> closed, matching testing scenario #10 from the plan.
    """
    state = BreakerState.CLOSED
    failures = 0
    opened_at: datetime | None = None
    now = NOW

    for _ in range(THRESHOLD):
        transition = next_breaker_state(
            current_state=state,
            consecutive_failures=failures,
            now=now,
            success=False,
            failure_threshold=THRESHOLD,
        )
        state, failures, opened_at = (
            transition.state,
            transition.consecutive_failures,
            transition.opened_at,
        )

    assert state is BreakerState.OPEN
    assert opened_at == now

    now = now + COOLDOWN
    assert not should_skip_for_open_breaker(
        state=state, opened_at=opened_at, now=now, cooldown=COOLDOWN
    )
    # Cooldown elapsed: the caller transitions to half_open before the probe (mirroring
    # DeliveryAttemptService.attempt()), then evaluates the probe's outcome from there.
    state = BreakerState.HALF_OPEN

    transition = next_breaker_state(
        current_state=state,
        consecutive_failures=failures,
        now=now,
        success=True,
        failure_threshold=THRESHOLD,
    )
    assert transition.state is BreakerState.CLOSED
    assert transition.consecutive_failures == 0
