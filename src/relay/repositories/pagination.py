import base64
import uuid
from dataclasses import dataclass
from datetime import datetime


class CursorDecodeError(ValueError):
    """Raised when a client-supplied cursor is malformed or not one this API issued."""


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None
    has_more: bool


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(token: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        created_at_raw, id_raw = raw.split("|", 1)
        return datetime.fromisoformat(created_at_raw), uuid.UUID(id_raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CursorDecodeError(f"invalid cursor: {token!r}") from exc
