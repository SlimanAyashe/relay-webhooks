import random

DEFAULT_BASE_SECONDS = 1.0
DEFAULT_CAP_SECONDS = 60.0


def compute_backoff_seconds(
    attempt: int,
    *,
    base_seconds: float = DEFAULT_BASE_SECONDS,
    cap_seconds: float = DEFAULT_CAP_SECONDS,
    rng: random.Random | None = None,
) -> float:
    """Full-jitter exponential backoff: `min(cap, base * 2**attempt)` bounds the delay for
    this retry (`attempt` = retries already made, 0 for the first retry after the initial
    attempt), then the actual wait is drawn uniformly from `[0, bound)`.

    Full jitter -- rather than a fixed or only-capped delay -- is what breaks synchronized
    retry storms: if a downstream endpoint recovers, every failed delivery backed off by the
    same formula would otherwise retry at the exact same instant and immediately overwhelm it
    again.
    """
    if attempt < 0:
        raise ValueError(f"attempt must be >= 0, got {attempt}")
    bound = min(cap_seconds, base_seconds * (2**attempt))
    generator = rng or random
    return generator.uniform(0, bound)
