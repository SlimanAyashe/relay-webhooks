"""Outbox domain entities and rules. See relay.domain for layering rules."""

from relay.domain.outbox.entities import OutboxEntry, OutboxStatus

__all__ = ["OutboxEntry", "OutboxStatus"]
