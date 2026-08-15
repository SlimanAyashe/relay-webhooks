"""API layer: FastAPI routers and Pydantic wire schemas.

Converts between the wire format (Pydantic) and relay.domain entities at the boundary, then
delegates to relay.services for all business logic. No DB session or ORM import belongs here.
May import relay.services, relay.repositories, and relay.infra. Enforced by the "Layered
architecture" import-linter contract (relay.api -> relay.services -> relay.repositories ->
relay.infra).
"""
