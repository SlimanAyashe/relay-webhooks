#!/usr/bin/env python3
"""Verify a Relay delivery signature the way a *receiver* would -- with no access to
Relay's own code.

This script deliberately imports nothing from `src/`. It is written from
`docs/guarantees.md`'s description of the signing contract alone:

    X-Relay-Signature  = HMAC-SHA256(endpoint_secret, f"{timestamp}.{body}"), hex-encoded
    X-Relay-Timestamp  = the unix timestamp that was signed
    X-Relay-Delivery-Id = the delivery this attempt belongs to

If this file can verify a real delivery, then the documented contract is sufficient for
somebody else to implement -- which is a different, stronger claim than "Relay's `verify()`
agrees with Relay's `sign()`", and the only one that matters to a customer writing a
receiver. (The `sign`/`verify` pair living in one module is good for consistency and proves
nothing about the documentation.)

Usage:

    python3 scripts/verify_signature_independently.py \\
        --secret "$ENDPOINT_SECRET" \\
        --timestamp 1787260000 \\
        --signature 6f1e... \\
        --body '{"probe": "hello"}'

    # or read the exact bytes off disk / stdin, which is what a receiver really does
    python3 scripts/verify_signature_independently.py ... --body-file captured.json
    python3 scripts/verify_signature_independently.py ... --body-file -

Exit status is 0 when the signature verifies and 1 when it doesn't, so this is usable as a
check in a shell pipeline. `tests/e2e/test_guarantees_live.py` runs it against a delivery
captured live, then flips one byte and asserts it fails.
"""

import argparse
import hashlib
import hmac
import sys
import time

DEFAULT_TOLERANCE_SECONDS = 300


def compute_signature(secret: str, timestamp: int, body: bytes) -> str:
    """The whole contract, in three lines, from the documentation alone."""
    signed_message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), signed_message, hashlib.sha256).hexdigest()


def verify(
    secret: str,
    timestamp: int,
    body: bytes,
    signature: str,
    *,
    tolerance_seconds: int,
    now: int | None = None,
) -> tuple[bool, str]:
    """Returns (ok, why). Two independent checks, in the order a receiver should do them:
    the timestamp must be inside the replay window, and the HMAC must match under a
    constant-time comparison -- `==` on a hex digest leaks, byte by byte, how much of a
    forged signature was correct.
    """
    current = int(time.time()) if now is None else now
    skew = abs(current - timestamp)
    if skew > tolerance_seconds:
        return False, f"timestamp is {skew}s away from now, outside the {tolerance_seconds}s window"

    expected = compute_signature(secret, timestamp, body)
    if not hmac.compare_digest(expected, signature):
        return False, "signature does not match the body, timestamp and secret"
    return True, f"signature verified over {len(body)} bytes"


def _read_body(args: argparse.Namespace) -> bytes:
    if args.body_file == "-":
        return sys.stdin.buffer.read()
    if args.body_file:
        with open(args.body_file, "rb") as handle:
            return handle.read()
    return args.body.encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--secret", required=True, help="the endpoint's signing secret")
    parser.add_argument(
        "--timestamp", required=True, type=int, help="the X-Relay-Timestamp header value"
    )
    parser.add_argument(
        "--signature", required=True, help="the X-Relay-Signature header value (hex)"
    )
    body_source = parser.add_mutually_exclusive_group(required=True)
    body_source.add_argument("--body", help="the exact request body, as a string")
    body_source.add_argument(
        "--body-file", help="file holding the exact request bytes, or - for stdin"
    )
    parser.add_argument(
        "--tolerance-seconds",
        type=int,
        default=DEFAULT_TOLERANCE_SECONDS,
        help=(
            "how far the timestamp may be from now, in either direction "
            f"(default {DEFAULT_TOLERANCE_SECONDS}s, matching Relay's own default). Raise it "
            "to verify a delivery captured some time ago."
        ),
    )
    args = parser.parse_args(argv)

    ok, why = verify(
        args.secret,
        args.timestamp,
        _read_body(args),
        args.signature,
        tolerance_seconds=args.tolerance_seconds,
    )
    print(f"{'VALID  ' if ok else 'INVALID'}  {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
