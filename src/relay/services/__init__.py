"""Services layer: use cases orchestrating relay.domain entities.

Each use case runs through a single relay.repositories unit-of-work transaction boundary.
May import relay.domain, relay.repositories, and relay.infra. Never imports FastAPI request/
response types, raises HTTPException, or returns a Pydantic wire schema — that translation
happens at the relay.api boundary. Enforced by the "Layered architecture" import-linter
contract (relay.api -> relay.services -> relay.repositories -> relay.infra).
"""
