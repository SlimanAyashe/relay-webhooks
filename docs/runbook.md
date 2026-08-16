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

## Restore from backup

Not yet implemented -- nightly `pg_dump` to S3 and a documented, *actually executed*
restore test are Phase 5 (optional) scope per the project plan. Until then, there is no
backup/restore story beyond whatever the hosting provider offers at the disk level. This is
a known limitation, not an oversight.

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
