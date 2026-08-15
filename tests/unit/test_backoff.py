import random

import pytest

from relay.domain.deliveries.backoff import compute_backoff_seconds


def test_first_retry_bound_is_base_seconds() -> None:
    rng = random.Random(0)
    delay = compute_backoff_seconds(0, base_seconds=1.0, cap_seconds=60.0, rng=rng)

    assert 0 <= delay < 1.0


def test_bound_doubles_each_attempt_until_capped() -> None:
    rng = random.Random(0)

    assert 0 <= compute_backoff_seconds(1, base_seconds=1.0, cap_seconds=60.0, rng=rng) < 2.0
    assert 0 <= compute_backoff_seconds(2, base_seconds=1.0, cap_seconds=60.0, rng=rng) < 4.0
    assert 0 <= compute_backoff_seconds(3, base_seconds=1.0, cap_seconds=60.0, rng=rng) < 8.0


def test_bound_never_exceeds_cap() -> None:
    rng = random.Random(0)

    delay = compute_backoff_seconds(20, base_seconds=1.0, cap_seconds=60.0, rng=rng)

    assert 0 <= delay < 60.0


def test_negative_attempt_is_rejected() -> None:
    with pytest.raises(ValueError, match="attempt must be >= 0"):
        compute_backoff_seconds(-1)


def test_jitter_produces_varying_delays_across_calls() -> None:
    rng = random.Random(0)

    delays = {
        compute_backoff_seconds(5, base_seconds=1.0, cap_seconds=60.0, rng=rng) for _ in range(20)
    }

    assert len(delays) > 1
