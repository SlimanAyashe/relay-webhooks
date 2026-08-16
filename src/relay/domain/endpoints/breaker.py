from dataclasses import dataclass
from datetime import datetime, timedelta

from relay.domain.endpoints.entities import BreakerState


@dataclass(frozen=True, slots=True)
class BreakerTransition:
    """The endpoint's next breaker_state/consecutive_failures/opened_at, given one attempt
    outcome. Pure data -- the caller decides how (and whether) to persist it.
    """

    state: BreakerState
    consecutive_failures: int
    opened_at: datetime | None


def should_skip_for_open_breaker(
    *, state: BreakerState, opened_at: datetime | None, now: datetime, cooldown: timedelta
) -> bool:
    """Whether a delivery attempt against this endpoint should be skipped entirely rather
    than actually sent. Only true for OPEN before its cooldown has elapsed -- CLOSED always
    sends, and HALF_OPEN sends exactly one probe attempt (never skipped, since the whole
    point of half-open is to test the destination).
    """
    if state is not BreakerState.OPEN:
        return False
    if opened_at is None:
        # Defensive: OPEN with no opened_at shouldn't happen in practice, but skipping
        # forever on bad data would be worse than probing once.
        return False
    return now - opened_at < cooldown


def next_breaker_state(
    *,
    current_state: BreakerState,
    consecutive_failures: int,
    now: datetime,
    success: bool,
    failure_threshold: int,
) -> BreakerTransition:
    """Pure closed/open/half-open transition logic, keyed off one attempt's outcome:

    - CLOSED, success -> CLOSED, failure count reset to 0
    - CLOSED, failure -> CLOSED until consecutive_failures reaches failure_threshold, then
      OPEN (opened_at = now)
    - HALF_OPEN (the one probe attempt allowed through after cooldown), success -> CLOSED,
      failure count reset
    - HALF_OPEN, failure -> back to OPEN, opened_at reset to now (a fresh cooldown starts)

    Zero I/O -- persistence is the repository's job, this only decides the next state.
    """
    if success:
        return BreakerTransition(state=BreakerState.CLOSED, consecutive_failures=0, opened_at=None)

    failures = consecutive_failures + 1

    if current_state is BreakerState.HALF_OPEN:
        # The probe failed: reopen immediately, don't wait for a fresh threshold's worth
        # of failures -- one bad probe is enough evidence the destination isn't recovered.
        return BreakerTransition(
            state=BreakerState.OPEN, consecutive_failures=failures, opened_at=now
        )

    if failures >= failure_threshold:
        return BreakerTransition(
            state=BreakerState.OPEN, consecutive_failures=failures, opened_at=now
        )

    return BreakerTransition(
        state=BreakerState.CLOSED, consecutive_failures=failures, opened_at=None
    )
