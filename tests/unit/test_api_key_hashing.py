import hashlib

from relay.domain.api_keys.hashing import (
    generate_api_key,
    hash_secret,
    split_api_key,
    verify_secret,
)


def test_generate_api_key_returns_matching_prefix_and_verifiable_secret() -> None:
    plaintext_key, key_prefix, key_hash = generate_api_key()

    split = split_api_key(plaintext_key)
    assert split is not None
    prefix, secret = split
    assert prefix == key_prefix
    assert verify_secret(secret, key_hash) is True


def test_generate_api_key_produces_unique_keys() -> None:
    first = generate_api_key()
    second = generate_api_key()

    assert first[0] != second[0]
    assert first[1] != second[1]


def test_verify_secret_rejects_wrong_secret() -> None:
    _, _, key_hash = generate_api_key()

    assert verify_secret("totally-wrong-secret", key_hash) is False


def test_verify_secret_rejects_truncated_secret() -> None:
    plaintext_key, _, key_hash = generate_api_key()
    _, secret = split_api_key(plaintext_key)  # type: ignore[misc]

    assert verify_secret(secret[:-1], key_hash) is False


def test_verify_secret_rejects_tampered_secret() -> None:
    plaintext_key, _, key_hash = generate_api_key()
    _, secret = split_api_key(plaintext_key)  # type: ignore[misc]
    flipped_char = "a" if secret[0] != "a" else "b"
    tampered = flipped_char + secret[1:]

    assert verify_secret(tampered, key_hash) is False


def test_verify_secret_rejects_malformed_stored_hash() -> None:
    assert verify_secret("any-secret", "no-dollar-separator-here") is False
    assert verify_secret("any-secret", "not-hex$deadbeef") is False


def test_split_api_key_rejects_missing_separator() -> None:
    assert split_api_key("no-separator-at-all") is None


def test_split_api_key_rejects_empty_prefix_or_secret() -> None:
    assert split_api_key(".secret-only") is None
    assert split_api_key("prefix-only.") is None
    assert split_api_key(".") is None
    assert split_api_key("") is None


def test_hash_secret_known_vector_is_reproducible_with_same_salt() -> None:
    # Pins the exact algorithm (salt_hex + "$" + sha256(salt + secret).hexdigest()) so a
    # future refactor can't silently change the on-disk format without this test noticing.
    salt = bytes.fromhex("00" * 16)
    secret = "known-secret-value"
    expected_digest = hashlib.sha256(salt + secret.encode()).hexdigest()

    stored_hash = f"{salt.hex()}${expected_digest}"

    assert verify_secret(secret, stored_hash) is True
    assert verify_secret("wrong-secret", stored_hash) is False


def test_hash_secret_round_trips_through_verify_secret() -> None:
    stored_hash = hash_secret("some-secret")

    assert "$" in stored_hash
    assert verify_secret("some-secret", stored_hash) is True
    assert verify_secret("some-other-secret", stored_hash) is False
