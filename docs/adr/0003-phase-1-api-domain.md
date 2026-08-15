# 0003. Phase 1 API and domain decisions

## Decision

Four decisions define Phase 1's shape: a strict three-model-set separation (Pydantic wire
schemas, domain dataclass entities, SQLAlchemy ORM models — never collapsed into one), an
idempotency-key conflict rule where identical-body replays return the original event and
differing-body replays 409 (never silently overwrite), cursor (keyset) pagination instead of
`OFFSET` for `GET /v1/endpoints`, and a salted-SHA-256-over-the-secret-portion API key scheme
where the key's prefix and secret carry independent entropy.

## Context

Phase 1 is the first feature work landing on top of Phase 0's empty skeleton, and it's also
where the layering rules Phase 0 only scaffolded (`api -> services -> repositories -> infra`,
domain independent of all four) get their first real exercise under actual business logic —
tenants, API keys, endpoint CRUD, event ingest. Getting these four decisions right here matters
disproportionately: the model-set separation and pagination approach are patterns every later
router/service in Phases 2-4 will repeat, and the idempotency and key-hashing choices are
directly load-bearing for the guarantees the project claims (`Idempotency-Key` honored on all
POSTs, API keys hashed at rest).

## Alternatives considered

- **Collapsing Pydantic schemas and domain entities into one class** (Pydantic models used
  directly as the "business object"): fewer files, but it means every domain method (`ApiKey.
  has_scope()`, `Endpoint.is_active()`) either lives on a class that also carries FastAPI/wire
  concerns, or business logic ends up scattered in routers instead. The three-model-set split
  keeps `relay.domain` framework-free (enforced by `import-linter`'s independence contract) and
  makes the api/service boundary an explicit translation (`from_domain()`/`created_from_domain()`
  classmethods) instead of an implicit one.
- **Application-level dedup only** for idempotency keys (check-then-insert, no DB constraint):
  simpler, but has a race window between the check and the insert under concurrent identical
  requests. The `UNIQUE(tenant_id, idempotency_key)` constraint is the real enforcement; the
  service-layer check-first is an optimization that avoids hitting that constraint on the common
  (non-racing) path, and `EventRepository.create()` catches the constraint violation as a
  fallback (via a `SAVEPOINT`, so the failed insert doesn't abort the surrounding transaction).
- **`OFFSET`/`LIMIT` pagination** instead of cursor-based: far simpler to implement and to reason
  about for a client, but `OFFSET N` gets slower as `N` grows (the DB still walks and discards
  the first `N` rows) and rows shift under concurrent inserts/deletes between pages, producing
  skipped or duplicated results. Keyset pagination over `(created_at, id)` — a stable total order
  even when two rows share a timestamp — costs an opaque cursor client-side in exchange for
  O(page size) queries at any depth and no drift under concurrent writes.
- **bcrypt/argon2** for API key hashing instead of salted SHA-256: those algorithms are designed
  to be *slow*, specifically to resist offline brute-forcing of low-entropy human-chosen
  passwords. An API key is server-generated with 256 bits of entropy (`secrets.token_urlsafe`)
  — brute-forcing the hash isn't the realistic threat, so a slow KDF only adds latency to every
  authenticated request for no real security gain. Salted SHA-256 (salt stored alongside the
  digest, since it isn't itself sensitive) is the same approach GitHub and Stripe use for their
  own API tokens.
- **A single random token as the whole key** (no separate prefix) instead of `<prefix>.<secret>`:
  simpler, but then the only way to look up a presented key is to hash it and scan, or to store
  it in a way that permits a lookup — either scanning every row's hash (linear) or accepting
  that the lookup key must itself be derivable without a scan. A public, independently-random
  prefix stored in plaintext with a DB index gives an O(1) candidate lookup while the actual
  credential (the secret) never appears anywhere but the hash.

## Tradeoff accepted

The three-model-set split means routine changes (e.g. adding a field to `Endpoint`) touch three
places (domain entity, ORM model, one or more Pydantic schemas) instead of one — accepted because
the alternative is a class that's simultaneously a wire format, a business object, and a
persistence record, which is exactly the kind of implicit coupling that makes later phases
(delivery engine, signing, breaker state machine) harder to reason about safely.

Keyset pagination means "jump to page 5" isn't possible — only "next page from this cursor" —
which is a real capability loss for a UI that wants arbitrary page numbers. Accepted because
Relay's endpoint list is not expected to need arbitrary-page jumping, and correctness (no
skipped/duplicated rows under concurrent writes) matters more than that convenience here.

The idempotency-key DB constraint means a losing concurrent request pays for a failed `INSERT`
(caught and converted, not free) on the rare double-submit race — accepted because the
alternative (no DB-level constraint) would leave a real duplicate-row window that no amount of
application-level checking can fully close.
