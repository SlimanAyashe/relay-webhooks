# 0002. Join the host's shared Traefik instead of running Caddy in production

## Decision

In production, Relay's `api` container is routed and TLS-terminated by a Traefik instance that
already runs on the deploy VPS, via Docker labels (`traefik.enable`, `traefik.http.routers.*`,
`certresolver=letsencrypt`). `docker/compose.prod.yml` no longer runs a `caddy` service or binds
80/443. Caddy is still used locally (`docker/compose.yml`, `docker/Caddyfile`) — this decision is
production-only.

## Context

ADR 0001 chose Caddy for both local dev and production TLS, on the assumption that the production
VPS would be dedicated to Relay. The VPS actually provisioned (`srv1737964.hstgr.cloud`, a
Hostinger box) is shared: it already runs `bookr.tech` and an n8n instance behind a
panel-managed Traefik container (`traefik-traefik-1`) that runs on host networking, owns ports
80/443, and does Let's Encrypt via the Docker provider (`--providers.docker`,
`exposedbydefault=false`, per-container opt-in via labels — the same pattern `bookr` already uses
for `bookr.tech`).

A second process binding 80/443 isn't possible without taking that Traefik down or moving it,
which would affect the other two live services for no benefit to Relay.

## Alternatives considered

- **Move the existing Traefik off 80/443, give Relay's Caddy those ports**: works but is strictly
  worse — it touches infrastructure two unrelated production services depend on, to preserve a
  proxy choice (Caddy) that has no functional advantage here over labels on the existing Traefik.
- **Run Caddy on alternate host ports behind Traefik** (Traefik as a dumb TCP passthrough to
  Caddy, Caddy still doing ACME): adds a second ACME client and a second hop for no isolation
  benefit — Traefik's Docker-label routing already gives Relay the same effective isolation
  (its own router/service, no shared vhost config file to edit).
- **Keep Caddy, request a dedicated VPS**: rejected — the shared box is what exists; provisioning
  a second VPS for a portfolio project's Phase 0 skeleton isn't worth the cost for the isolation
  gained.

## Consequences

- Relay's domain is a subdomain of an existing domain the VPS owner controls (`bookr.tech`), not
  a fresh domain — decided with the project owner, see `docs/PROJECT_STATUS.md`.
- `docker/Caddyfile.prod` is never created; the "Configure production Caddy for real domain TLS"
  Phase 0 ticket is satisfied by the Traefik labels in `docker/compose.prod.yml` instead.
- Local dev is unaffected — `docker/compose.yml` still runs Caddy on `:8080` exactly as before,
  since there's no shared-proxy constraint on a laptop.
- If Relay ever moves to its own VPS, reintroducing a Relay-owned Caddy in production is a
  contained change: drop the labels, add back a `caddy` service bound to 80/443, write a real
  `Caddyfile.prod`.
