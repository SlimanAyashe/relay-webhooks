"""HMAC-SHA256 request signing: pure, framework-agnostic, no HTTP or DB imports so it's
directly unit-testable. Every outbound delivery request carries the signature this module
computes as `X-Relay-Signature`, alongside `X-Relay-Timestamp` -- the receiver runs the
same `sign()` over the timestamp and raw body it received and compares constant-time via
`verify()`, exactly mirroring what this module does on the sending side.
"""

import hashlib
import hmac
import time

_ALGORITHM = hashlib.sha256


def _signed_message(timestamp: int, body: bytes) -> bytes:
    # `timestamp.body` -- a literal "." joins the two, then the exact bytes being sent so
    # the signature covers the payload byte-for-byte, not some re-serialization of it.
    return f"{timestamp}.".encode() + body


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """Computes the hex-digest HMAC-SHA256 signature over `f"{timestamp}.{body}"` using
    the endpoint's stored secret. Both signer and verifier live here so they can never
    drift out of sync with each other.
    """
    return hmac.new(secret.encode(), _signed_message(timestamp, body), _ALGORITHM).hexdigest()


def verify(
    secret: str,
    timestamp: int,
    body: bytes,
    signature: str,
    *,
    tolerance_seconds: int,
    now: int | None = None,
) -> bool:
    """Verifies a signature was produced by `sign()` with this secret, using
    `hmac.compare_digest` so a mismatch can't be timed byte-by-byte, and rejects any
    timestamp more than `tolerance_seconds` away from `now` in *either* direction --
    protects against both a stale replayed request and a clock far enough in the future to
    otherwise out-live the tolerance window once it arrives.
    """
    current = now if now is not None else int(time.time())
    if abs(current - timestamp) > tolerance_seconds:
        return False
    expected = sign(secret, timestamp, body)
    return hmac.compare_digest(expected, signature)
