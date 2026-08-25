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
6. Then `.github/workflows/deploy.yml` runs the live smoke suite (`tests/e2e`) against the
   public URL. A deploy is done when the guarantees pass against the new container, not when
   it reports ready; a smoke failure is handled exactly like a failed gate -- investigate,
   and roll back with the one-liner below. See `docs/live-verification.md`.

**This is abort-and-roll-back, not a zero-downtime deploy, and the drill measured the
difference.** `docker compose up -d` replaces the running container *before* step 4 gets to
ask anything, so a bad image is live for the whole gate window. In the 2026-08-24 drill the
public domain returned errors for **74 seconds** (a few seconds of 502/404 while the swap
happened, 55 seconds of the broken image's own 503s, then 502/404 again during the
roll-back) before recovering on its own. The gate's promise is that a bad image never
*stays* deployed -- not that nobody notices. Buying the stronger property means a second
container and a proxy cutover (blue/green), which this single-VPS deployment has
deliberately not built.

## Roll back manually

`deploy_remote.sh <previous-image>` is the documented one-liner if a bad deploy somehow
gets past the health gate (e.g. `/readyz` green but the app is otherwise broken). Find the
previous good tag's image reference from the GHCR package list or a previous successful
deploy's Actions log, then re-run the script against it by hand on the VPS from
`/opt/relay`.

Rehearsed 2026-08-24: **11 seconds** wall clock from typing the command to `/readyz` green
on the new (old) image, of which about 9 seconds were visible on the public domain as
502/404 while the container swapped. So the honest shape of a bad deploy is roughly a
minute of degradation if the gate catches it, or ~10 seconds if a human catches it and runs
this. Both are recorded in the drill log below.

## Nightly backup

`scripts/backup_postgres.py` runs `pg_dump --format=custom` inside the running
`relay-postgres-1` container (via `docker exec`, so no Postgres client needs to be installed
on the host) and uploads the result to S3 as `s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/relay-<UTC
timestamp>.dump`, using `boto3`'s normal credential chain (`AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` / `AWS_REGION`) against real AWS S3 in production.

**Scheduling on the VPS.** The timer is installed and running (see the drill log below).
The units live in `scripts/systemd/`; installing them needs three things on the host, which
a deploy does not create by itself:

```bash
# 1. an ops-only virtualenv -- boto3 and nothing else. The unit deliberately does NOT run
#    `uv run` against a checkout: a deploy puts a compose file, an .env and scripts/ into
#    /opt/relay, not this repo, so there is nothing for uv to resolve there.
uv venv /opt/relay/.venv && uv pip install --python /opt/relay/.venv/bin/python boto3

# 2. the scripts and the backup destination, kept in their own env file so the api and
#    worker containers never load the credentials that can read every database dump
install -m 700 -d /opt/relay/scripts
install -m 600 /dev/null /opt/relay/backup.env   # then fill in BACKUP_S3_* and AWS_*
cp scripts/backup_postgres.py scripts/restore_drill.py /opt/relay/scripts/

# 3. the units themselves
sudo cp scripts/systemd/relay-backup.service scripts/systemd/relay-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now relay-backup.timer
systemctl list-timers relay-backup.timer     # confirm the next scheduled run
sudo systemctl start relay-backup.service    # force one run now, rather than waiting for 03:00
journalctl -u relay-backup.service -n 20     # the dump size and the S3 key it wrote
```

`/opt/relay/backup.env` holds `BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIX`,
`BACKUP_POSTGRES_CONTAINER`, optionally `BACKUP_S3_ENDPOINT_URL`, and the AWS credentials.
Both scripts read the environment directly rather than importing `relay.infra.settings`, for
the same reason the virtualenv exists: they have to run on a host that has no checkout.

**Where the backups actually go, on this deployment.** There is no AWS account for this
project, so `BACKUP_S3_ENDPOINT_URL` points at the MinIO container from
`docker/compose.yml`'s `backup-drill` profile -- *on the same host as the database it is
backing up*. That proves the timer fires, the dump is valid, the upload works and the
restore matches; it is explicitly **not** off-host durability, and a host failure would take
both with it. Pointing this at real S3 is deleting one line from `backup.env` and putting
real credentials in it -- no code changes -- and until someone does, `docs/guarantees.md`
says so out loud.

## Restore from backup

`scripts/restore_drill.py` downloads the most recent backup, restores it into a **throwaway
scratch Postgres container** (never the real one), and prints per-table row counts for
comparison against the live database -- then tears the scratch container down. Run it with:

```bash
uv run python scripts/restore_drill.py               # tears down the scratch container after
uv run python scripts/restore_drill.py --keep        # leaves it running for inspection
uv run python scripts/restore_drill.py --no-compare-live  # when the live DB isn't reachable
```

By default the drill now **diffs the restored row counts against the live database** rather
than only printing them, and exits non-zero if they disagree. The comparison expects `live >=
restored` per table -- the live database keeps taking writes while the drill runs, so rows
added after the dump are drift, not loss. `restored > live` (rows that existed at dump time
are gone) and "restored 0 rows against a non-empty live table" are the failures.

Point `BACKUP_S3_ENDPOINT_URL` at a MinIO container instead of real S3 (see `.env.example`
and `docker/compose.yml`'s `backup-drill` profile) -- the script doesn't change, only which
S3-compatible endpoint it talks to. This has been run for real against a dump the systemd
timer's own service produced from the production database; see the drill log below and
`docs/PROJECT_STATUS.md` for the dated results.

Re-run it periodically rather than treating it as a one-time proof: a restore procedure that
hasn't been exercised recently against the *current* schema is not meaningfully different
from one that was never tested.

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

**This has not been applied to the production VPS, and Phase 8 measured exactly what that
costs.** The VPS (`srv1737964.hstgr.cloud`) is shared with other, pre-existing production
services (`bookr.tech`, n8n) per `docs/PROJECT_STATUS.md` -- touching host-level
`ufw`/`nftables` state on a shared box without coordinating is explicitly called out there as
out of bounds for this project to do unilaterally. What follows is the rule set that *should*
be applied, for whoever has authority over that host's firewall to review and apply
deliberately, not something this repo's tooling applies automatically.

### Verify it (run this before and after applying anything)

```bash
uv run python scripts/verify_egress_firewall.py            # or: make verify-egress
uv run python scripts/verify_egress_firewall.py --markdown # table for pasting here
```

The script opens raw TCP sockets from inside the dispatcher container -- no HTTP, no Relay
code -- so it measures the network layer with the application's SSRF guard entirely out of
the picture. It exits 0 when every decisive private target is blocked, 1 when one answered,
and 2 when a control target failed (meaning the probe itself proved nothing).

A word on reading its output: a *timeout* against an address where nothing listens is
ambiguous -- a dropped packet and an absent host look identical. The decisive targets are
therefore addresses that demonstrably do listen, namely the host's own SSH port reached over
a Docker bridge gateway. Those turn a DROP rule into an observable difference.

### Result, 2026-08-23 (before any rules were applied)

| Target | Address | Result |
| --- | --- | --- |
| host over the relay bridge (sshd) | `172.16.4.1:22` | **CONNECTED** |
| host over bookr's bridge (sshd) | `172.16.2.1:22` | **CONNECTED** |
| host over docker0 (sshd) | `172.16.0.1:22` | **CONNECTED** |
| cloud metadata | `169.254.169.254:80` | timeout (nothing listens; inconclusive by itself) |
| RFC1918 10/8, 192.168/16 | `10.0.0.1:80`, `192.168.0.1:80` | timeout (same) |
| public control | `1.1.1.1:443` | connected, as it must |
| intra-network control | `postgres:5432` | connected, as it must |

`iptables -S DOCKER-USER` was empty, `INPUT` policy `ACCEPT`, `ufw` inactive. So: **the
application-layer SSRF guard is currently the only thing standing between Relay and the
host's own private interfaces.** Cross-bridge traffic (e.g. to another service's database
container) is already blocked, but by Docker's own inter-bridge isolation rather than by
anything this project put there.

### What the drill corrected about the rules below

The `DOCKER-USER` rules originally documented here would **not** have closed the gap the
drill found. `DOCKER-USER` is part of the `FORWARD` chain, and a packet from a container to
its own bridge gateway is destined for the host itself -- it goes through `INPUT`, never
through `FORWARD`. Blocking container-to-host traffic needs an `INPUT` rule, and the
`FORWARD`-chain rules need an explicit exception for the relay network's own subnet or they
will also cut `api` off from `postgres` and `redis`.

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
# The relay network's own subnet first, or api can no longer reach postgres/redis:
nft add rule inet relay_egress forward iifname "<relay-bridge-iface>" ip daddr <relay-subnet> accept
nft add rule inet relay_egress forward iifname "<relay-bridge-iface>" ip daddr { \
    10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, \
    169.254.0.0/16, 127.0.0.0/8, 100.64.0.0/10 \
  } drop
nft add rule inet relay_egress forward iifname "<relay-bridge-iface>" ip6 daddr { \
    fc00::/7, fe80::/10, ::1/128 \
  } drop

# Container -> host. This is the half the FORWARD rules above cannot reach, and the half the
# 2026-08-23 drill found open. ESTABLISHED first, or replies to host-initiated connections
# (Traefik -> api, among others) die with it.
nft add chain inet relay_egress input '{ type filter hook input priority 0; policy accept; }'
nft add rule inet relay_egress input iifname "<relay-bridge-iface>" ct state established,related accept
nft add rule inet relay_egress input iifname "<relay-bridge-iface>" drop
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
# ...and this one last, so -I leaves it at the top: the stack's own network stays reachable.
iptables -I DOCKER-USER -i <relay-bridge-iface> -d <relay-subnet> -j RETURN

# Container -> host (the INPUT half; see above). Insert the DROP first, then the ACCEPT, so
# the ACCEPT ends up above it.
iptables -I INPUT -i <relay-bridge-iface> -j DROP
iptables -I INPUT -i <relay-bridge-iface> -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
```

Neither rule set survives a reboot on its own; whoever applies them should also persist them
(a `oneshot` systemd unit ordered `After=docker.service`, or `iptables-persistent`). Re-run
`scripts/verify_egress_firewall.py` afterwards -- it should exit 0, with the two control
targets still connecting.

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

## Drill log

An operational claim is worth what its last rehearsal proved. Each row is a drill that was
actually executed, with the command that reproduces it -- re-run them periodically rather
than treating any of them as settled; a procedure that hasn't been exercised against the
*current* system is not meaningfully different from one that was never tested.

| Date | Drill | Command | Result |
| --- | --- | --- | --- |
| 2026-08-24 | Deploy gate aborts a bad image | `RELAY_SKIP_PULL=1 ./deploy_remote.sh relay:deploy-gate-drill-broken` | Gate held: `/readyz` never went green, swap aborted, previous image restored automatically, script exited 1. **74s of public downtime** during the window (see Deploy above) |
| 2026-08-24 | Rollback one-liner | `RELAY_SKIP_PULL=1 ./deploy_remote.sh relay:<previous-sha>` | 11s wall clock, ~9s of 502/404 on the domain |
| 2026-08-24 | Live smoke suite against production | `RELAY_E2E_BASE_URL=https://relay.bookr.tech uv run pytest tests/e2e` | 44 passed in ~11 min. One test failed on the first run and was wrong, not the system: it asserted a replayed delivery's attempts keep counting up, but `reset_for_replay` deliberately restarts a fresh chain at 1 |
| 2026-08-25 | Chaos suite against production | `RELAY_E2E_BASE_URL=https://relay.bookr.tech uv run pytest tests/chaos -v -s` | 3 passed in ~3 min. Took three runs: two of my own test bugs, and one real defect in the app (stale pooled Redis connections after a restart — see `docs/failure-modes.md`) |
| 2026-08-24 | Egress firewall posture | `make verify-egress` | **Not blocked**: the container reached the host's SSH port over every bridge gateway. See "Defense in depth" below |
| 2026-08-23 | Nightly backup timer fires | `systemctl start relay-backup.service && journalctl -u relay-backup.service` | 51,174-byte dump written to `s3://relay-backups/postgres/relay-20260823T020829Z.dump` |
| 2026-08-23, 2026-08-24 | ...and fires *unattended*, on its own schedule | `systemctl list-timers relay-backup.timer` | Two further dumps written with no human involved, at 03:29:00 and 03:20:10 -- which is the actual claim: a timer that only ever ran because somebody typed `systemctl start` has not been shown to work |
| 2026-08-23 | Restore a real production dump | `uv run python scripts/restore_drill.py` | Restored the timer's own dump into a scratch container; all seven tables matched the live database, `+2` drift on `delivery_attempts` from writes after the dump |

The deploy-gate drill needs an image whose `/readyz` deliberately fails. Build one by
layering a broken `health.py` over the current image rather than committing one:

```bash
printf 'FROM relay:<sha>\nCOPY health.py /app/src/relay/api/health.py\n' > Dockerfile
# health.py: same routes, but readyz sets response.status_code = 503
docker build -t relay:deploy-gate-drill-broken .
```

`RELAY_SKIP_PULL=1` exists for exactly this: the drill deploys a locally built image that
was never pushed anywhere. Never use it for a real deploy -- skipping the pull would
silently ship whatever stale tag happens to be on the host.
