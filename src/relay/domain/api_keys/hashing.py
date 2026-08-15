import hashlib
import hmac
import secrets

_PREFIX_ENTROPY_BYTES = 6
_SECRET_ENTROPY_BYTES = 32
_SALT_BYTES = 16
_SEPARATOR = "."
_HASH_SEPARATOR = "$"


def generate_api_key() -> tuple[str, str, str]:
    """Returns (plaintext_key, key_prefix, key_hash).

    The plaintext key is shown to the caller exactly once, at issuance -- only key_prefix
    and key_hash are ever persisted. key_prefix and the secret are independently random
    (not one derived from the other), so leaking the prefix through logs or a lookup
    index costs the secret's full entropy nothing.
    """
    key_prefix = secrets.token_urlsafe(_PREFIX_ENTROPY_BYTES)
    secret = secrets.token_urlsafe(_SECRET_ENTROPY_BYTES)
    plaintext_key = f"{key_prefix}{_SEPARATOR}{secret}"
    return plaintext_key, key_prefix, hash_secret(secret)


def hash_secret(secret: str) -> str:
    """Salted SHA-256 over the secret portion only -- never the prefix. Format is
    `<salt_hex>$<digest_hex>`; the salt is random per key and stored alongside the
    digest since it isn't itself sensitive.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.sha256(salt + secret.encode()).hexdigest()
    return f"{salt.hex()}{_HASH_SEPARATOR}{digest}"


def split_api_key(plaintext_key: str) -> tuple[str, str] | None:
    """Splits a presented key into (key_prefix, secret) for the auth lookup path.

    Returns None for anything structurally malformed -- missing separator, empty prefix,
    or empty secret -- rather than raising, since this runs directly on untrusted client
    input from a request header.
    """
    key_prefix, sep, secret = plaintext_key.partition(_SEPARATOR)
    if not sep or not key_prefix or not secret:
        return None
    return key_prefix, secret


def verify_secret(secret: str, stored_hash: str) -> bool:
    """Constant-time verification of a presented secret against a stored hash.

    A malformed stored_hash (never attacker-controlled -- it's our own persisted data)
    fails fast. The presented secret's digest always runs through
    hmac.compare_digest so comparison time doesn't leak how much of a guess matched.
    """
    salt_hex, sep, digest_hex = stored_hash.partition(_HASH_SEPARATOR)
    if not sep:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    candidate = hashlib.sha256(salt + secret.encode()).hexdigest()
    return hmac.compare_digest(candidate, digest_hex)
