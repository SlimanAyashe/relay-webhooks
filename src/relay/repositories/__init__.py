"""Repositories layer: persistence.

SQLAlchemy 2.0 ORM models and repository classes that translate between them and
relay.domain entities — callers never see an ORM object. May import relay.domain and
relay.infra. Never imported by relay.domain; never imports relay.services or relay.api.
Enforced by the "Layered architecture" import-linter contract (relay.api -> relay.services
-> relay.repositories -> relay.infra).
"""
