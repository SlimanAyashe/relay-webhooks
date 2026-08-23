# Runbook

Operational how-to: deploy, roll back, drain the DLQ, and the network-layer defenses that
back up the application's own SSRF guard. Written for whoever's holding the pager (which,
for this project, is also whoever built it) -- see `docs/PROJECT_STATUS.md` for the current
deploy state and any open gotchas, and `docs/guarantees.md` for what each of these
mechanisms actually promises.

## Deploy

CI builds and pushes `ghcr.io/<repo>:<sha>` on every merge to `main`. Pushing a tag
(`vX.Y.Z`) triggers `.github/workflows/deploy.yml`, which `scp`s `scripts/deploy_remote.sh`
and `docker/compose.prod.yml` to the VPS and runs the script there. The script:

1. Pulls the new image.
2. Runs `alembic upgrade head` against it (migration happens before the swap, never on
   application startup).
3. Brings up `api`, `relay-worker`, `dispatcher`, `scheduler`, and `reaper` together on the
   new image (they share one image and version in lockstep).
4. Polls `/readyz` (checks Postgres + Redis independently) for up to 60 seconds.
5. **If `/readyz` never goes green, the swap is aborted and the previous image is brought
   back up** -- see `docs/adr/0001-phase-0-skeleton.md` for why this is abort-and-rollback
   rather than fully automated rollback.

## Roll back manually

`deploy_remote.sh <previous-image>` is the documented one-liner if a bad deploy somehow
gets past the health gate (e.g. `/readyz` green but the app is otherwise broken). Find the
previous good tag's image reference from the GHCR package list or a previous successful
deploy's Actions log, then re-run the script against it by hand on the VPS from
`/opt/relay`.

## Nightly backup

`scripts/backup_postgres.py` runs `pg_dump --format=custom` inside the running
`relay-postgres-1` container (via `docker exec`, so no Postgres client needs to be installed
on the host) and uploads the result to S3 as `s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/relay-<UTC
timestamp>.dump`, using `boto3`'s normal credential chain (`AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` / `AWS_REGION`) against real AWS S3 in production.

**Scheduling on the VPS** (`scripts/systemd/relay-backup.{service,timer}` -- written and
tested locally, but *not yet installed on the production VPS* from this repo's tooling; that
needs whoever has SSH access to run the install steps below deliberately, the same category
of action as everything else in `docs/PROJECT_STATUS.md`'s "requires deliberate execution,
not automated" list):

```bash
# On the VPS, from /opt/relay, after the usual deploy has synced the repo's scripts/ there:
sudo cp scripts/systemd/relay-backup.service scripts/systemd/relay-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now relay-backup.timer
systemctl list-timers relay-backup.timer   # confirm the next scheduled run
```

`/opt/relay/.env` needs `BACKUP_S3_BUCKET` and real AWS credentials added alongside the
existing `POSTGRES_PASSWORD`/`DATABASE_URL` entries before the timer's first real run.

## Restore from backup

`scripts/restore_drill.py` downloads the most recent backup, restores it into a **throwaway
scratch Postgres container** (never the real one), and prints per-table row counts for
comparison against the live database -- then tears the scratch container down. Run it with:

```bash
uv run python scripts/restore_drill.py          # tears down the scratch container after
uv run python scripts/restore_drill.py --keep   # leaves it running for manual inspection
```

Locally, point `BACKUP_S3_ENDPOINT_URL` at a MinIO container instead of real S3 (see
`.env.example` and `docker/compose.yml`'s `backup-drill` profile) -- the script doesn't
change, only which S3-compatible endpoint it talks to. This has been run for real; see
`docs/PROJECT_STATUS.md`'s Phase 5 section for the dated result (row counts matched exactly
across all seven tables between the live database and the restored scratch container). In
production this is the same command against real S3, restoring into a scratch container on
whatever host runs it -- the drill should be re-run periodically, not treated as a one-time
proof that never needs repeating; a restore procedure that hasn't been exercised recently
against the *current* schema is not meaningfully different from one that was never tested.

## Drain the DLQ

Dead deliveries (`deliveries WHERE state='dead'` -- retry budget exhausted, per
`delivery_max_attempts`) are inspectable and replayable via the API, not just the database:

```bash
# List a tenant's dead deliveries with their full attempt history.
curl -H "X-API-Key: $API_KEY" https://api.relay.bookr.tech/v1/dlq

# Replay one -- resets it to a fresh delivery chain (pending, full retry budget) and
# re-enqueues it. The original delivery_attempts history is never modified.
curl -X POST -H "X-API-Key: $API_KEY" \
  https://api.relay.bookr.tech/v1/deliveries/<delivery_id>/replay
```

A delivery can only be replayed while it's `dead` (`409` otherwise), and only by the
tenant that owns it (`404` for any other tenant's key, same as every other resource --
existence is never leaked across tenants).

## Observability

**Logs:** every process (`api` and all four workers) emits structured JSON to stdout --
`docker compose logs -f <service> | jq .` works uniformly. Each line carries a
`correlation_id` tying it back to the API request that originally created the event being
processed, including on worker log lines produced by a retry, not just the first attempt --
see `docs/adr/0007-phase-5-observability-and-ops.md`. Container log growth is bounded by
Docker's own `json-file` driver options (10MB x 5 files per container; the `x-logging`
anchor in both compose files) -- no host-level `logrotate` setup needed.

**Metrics -- two scrape targets, not one:**

| Target | Serves |
| --- | --- |
| `api:8000/metrics` | `http_request_duration_seconds` (API request latency, labeled by route/status) |
| `dispatcher:9100/metrics` | `delivery_attempts_total` (by outcome), `circuit_breaker_state` (per endpoint), `delivery_queue_depth`, `delivery_in_flight` |

Both are unauthenticated (a Prometheus scraper has no tenant API key), same as Prometheus
targets always are. `api`'s port is already reachable at `https://api.relay.bookr.tech` in
production -- if a real Prometheus server is ever pointed at this deployment, restrict
`/metrics` at the Traefik layer (an IP-allowlist middleware, or simply never adding a public
router for it) before doing so; this repo's tooling does not add or remove that restriction
itself, the same stance taken on the egress-firewall rules below. Locally:
`curl localhost:8000/metrics` and `curl localhost:9100/metrics`.

## Demo console abuse controls

`POST /v1/sandbox` is the one route on this service that creates an identity for an
unauthenticated caller, and the console it powers is a public outbound HTTP proxy by
design (register a URL, Relay calls it). The controls, all in `relay.infra.settings.Settings`:

| Control | Setting | Default |
| --- | --- | --- |
| Sandbox key TTL | `sandbox_ttl_minutes` | 60 |
| Max endpoints per sandbox | `sandbox_max_endpoints` | 3 |
| Max events per sandbox | `sandbox_max_events` | 20 |
| Sandbox event-ingest rate | `sandbox_rate_limit_requests_per_second` / `_burst` | 1.0 req/s, burst 3 |
| Per-IP sandbox creation rate | `sandbox_creation_rate_limit_requests_per_second` / `_burst` | ~1 per 20s, burst 3 |
| Max event payload size | `event_payload_max_bytes` | 64 KiB |
| Outbound connect/read timeout | `outbound_connect_timeout_seconds` / `outbound_read_timeout_seconds` | 5s each |
| Process-wide outbound concurrency | `dispatcher_concurrency` (Phase 2, not console-specific) | 10 |

If the sandbox is being abused (scripted key farming past the per-IP limiter, e.g. from a
botnet of IPs), the fastest containment is dropping `sandbox_creation_rate_limit_burst`
and `_requests_per_second` via an env var redeploy -- no code change needed. A fixed,
identifying `User-Agent` (`outbound_user_agent` setting) is sent on every outbound
delivery request specifically so an abuse report against a customer's server names this
service and a docs URL, and is answerable.

## Defense in depth: VPS egress firewall

The application-layer SSRF guard (`relay.infra.ssrf_guard`, IP-pinned via
`relay.infra.pinned_transport`) is the primary defense against Relay being used to reach
internal/loopback/link-local/metadata addresses, but per `docs/guarantees.md` the app is
deliberately *not* claimed to be the only thing standing between an outbound HTTP proxy and
whatever the host can otherwise reach. A network-layer egress rule that denies the
container's traffic to private ranges regardless of what the application code does is the
second, independent layer -- if the app-layer guard has a bug, this is what still holds.

**This has not been applied to the production VPS.** The VPS (`srv1737964.hstgr.cloud`) is
shared with other, pre-existing production services (`bookr.tech`, n8n) per
`docs/PROJECT_STATUS.md` -- touching host-level `ufw`/`nftables` state on a shared box
without coordinating is explicitly called out there as out of bounds for this project to do
unilaterally. What follows is the rule set that *should* be applied, for whoever has
authority over that host's firewall to review and apply deliberately, not something this
repo's tooling applies automatically.

### nftables (preferred if the host already uses nftables)

Scope the rule to the Relay containers' egress interface (Docker's bridge network for the
`relay_default` compose project) rather than the whole host, so other services on the same
VPS aren't affected:

```bash
# Identify the bridge interface for the relay_default network first:
docker network inspect relay_default --format '{{ (index .Options "com.docker.network.bridge.name") }}'
# Typically something like br-<network-id-prefix> if not explicitly named.

nft add table inet relay_egress
nft add chain inet relay_egress forward '{ type filter hook forward priority 0; policy accept; }'
nft add rule inet relay_egress forward iifname "<relay-bridge-iface>" ip daddr { \
    10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, \
    169.254.0.0/16, 127.0.0.0/8, 100.64.0.0/10 \
  } drop
nft add rule inet relay_egress forward iifname "<relay-bridge-iface>" ip6 daddr { \
    fc00::/7, fe80::/10, ::1/128 \
  } drop
```

### UFW (if the host manages iptables via UFW instead)

UFW's rule language doesn't scope by Docker bridge interface as cleanly as nftables, so the
equivalent needs manual `iptables`/`ufw route` rules referencing the bridge's actual
interface name and the `DOCKER-USER` chain (Docker manages `FORWARD` itself and stomps
plain `ufw` rules there on restart):

```bash
iptables -I DOCKER-USER -i <relay-bridge-iface> -d 10.0.0.0/8 -j DROP
iptables -I DOCKER-USER -i <relay-bridge-iface> -d 172.16.0.0/12 -j DROP
iptables -I DOCKER-USER -i <relay-bridge-iface> -d 192.168.0.0/16 -j DROP
iptables -I DOCKER-USER -i <relay-bridge-iface> -d 169.254.0.0/16 -j DROP
iptables -I DOCKER-USER -i <relay-bridge-iface> -d 127.0.0.0/8 -j DROP
iptables -I DOCKER-USER -i <relay-bridge-iface> -d 100.64.0.0/10 -j DROP
```

### Why this is still worth doing even with the app-layer guard in place

- The app-layer guard runs in application code that can have bugs, be bypassed by a future
  refactor that forgets to call it, or be raced by something outside this specific code
  path (a future feature that makes an outbound call some other way).
- It costs nothing at request time -- these are static kernel-level rules, not something
  evaluated per request the way the DNS-resolve-then-check guard is.
- "I didn't trust my own validation code enough to also block this at the network layer"
  is a materially stronger thing to be able to say than "the code checks for it," and it's
  the honest position: `docs/guarantees.md` already says the SSRF guard mitigates, not
  eliminates, in the specific case of a resolver or redirect path the guard doesn't see.
