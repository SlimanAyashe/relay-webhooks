from hypothesis import given
from hypothesis import strategies as st

from relay.infra.signing import sign, verify

_SECRET = "s3cr3t-signing-key"
_TOLERANCE = 300


@given(body=st.binary(max_size=4096), timestamp=st.integers(min_value=0, max_value=2_000_000_000))
def test_a_correctly_signed_request_within_tolerance_verifies(body: bytes, timestamp: int) -> None:
    signature = sign(_SECRET, timestamp, body)

    assert verify(_SECRET, timestamp, body, signature, tolerance_seconds=_TOLERANCE, now=timestamp)


@given(
    body=st.binary(min_size=1, max_size=4096),
    timestamp=st.integers(min_value=0, max_value=2_000_000_000),
    tamper_index=st.integers(min_value=0),
)
def test_a_single_tampered_payload_byte_fails_verification(
    body: bytes, timestamp: int, tamper_index: int
) -> None:
    signature = sign(_SECRET, timestamp, body)

    index = tamper_index % len(body)
    tampered = bytearray(body)
    tampered[index] ^= 0xFF  # flip every bit of one byte -- guaranteed to differ
    tampered_body = bytes(tampered)

    assert not verify(
        _SECRET, timestamp, tampered_body, signature, tolerance_seconds=_TOLERANCE, now=timestamp
    )


@given(
    body=st.binary(max_size=4096),
    timestamp=st.integers(min_value=0, max_value=2_000_000_000),
    drift=st.integers(min_value=_TOLERANCE + 1, max_value=10_000_000),
    into_future=st.booleans(),
)
def test_a_timestamp_outside_the_tolerance_window_is_rejected_even_with_a_correct_signature(
    body: bytes, timestamp: int, drift: int, into_future: bool
) -> None:
    signature = sign(_SECRET, timestamp, body)
    now = timestamp + drift if into_future else timestamp - drift

    assert not verify(_SECRET, timestamp, body, signature, tolerance_seconds=_TOLERANCE, now=now)


def test_verify_uses_constant_time_comparison_not_string_equality() -> None:
    """Not a timing assertion (those are flaky in CI) -- just pins that verify() goes
    through hmac.compare_digest rather than `==`, by checking a signature that differs only
    in its very first character still fails (a naive short-circuiting `==` would too, but
    this at least proves no accidental early-return path skips the real comparison).
    """
    body = b'{"order_id": "123"}'
    timestamp = 1_700_000_000
    signature = sign(_SECRET, timestamp, body)
    tampered_signature = ("0" if signature[0] != "0" else "1") + signature[1:]

    assert not verify(
        _SECRET, timestamp, body, tampered_signature, tolerance_seconds=_TOLERANCE, now=timestamp
    )


def test_sign_is_deterministic_for_the_same_inputs() -> None:
    assert sign(_SECRET, 1_700_000_000, b"payload") == sign(_SECRET, 1_700_000_000, b"payload")


def test_sign_differs_across_secrets() -> None:
    assert sign("secret-a", 1_700_000_000, b"payload") != sign(
        "secret-b", 1_700_000_000, b"payload"
    )


def test_verify_rejects_wrong_secret() -> None:
    timestamp = 1_700_000_000
    body = b"payload"
    signature = sign("right-secret", timestamp, body)

    assert not verify(
        "wrong-secret", timestamp, body, signature, tolerance_seconds=_TOLERANCE, now=timestamp
    )
