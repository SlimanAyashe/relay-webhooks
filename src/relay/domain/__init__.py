"""Domain layer: pure business entities and rules.

Zero I/O, zero framework imports — no FastAPI, no SQLAlchemy, no Pydantic. Entities are
plain dataclasses/value objects that express business rules themselves (e.g. an ApiKey
knows how to check its own scopes) independent of persistence or wire format.

This package imports nothing from relay.api, relay.services, relay.repositories, or
relay.infra. Every other layer may import from here; this layer depends on none of them.
Enforced by the "Domain has zero internal dependencies" import-linter contract.
"""
