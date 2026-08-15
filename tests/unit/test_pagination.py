import base64
import uuid
from datetime import UTC, datetime

import pytest

from relay.repositories.pagination import CursorDecodeError, decode_cursor, encode_cursor


def test_cursor_round_trips_created_at_and_id() -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    row_id = uuid.uuid4()

    token = encode_cursor(created_at, row_id)
    decoded_created_at, decoded_id = decode_cursor(token)

    assert decoded_created_at == created_at
    assert decoded_id == row_id


def test_decode_cursor_rejects_garbage_input() -> None:
    with pytest.raises(CursorDecodeError):
        decode_cursor("not-a-valid-cursor")


def test_decode_cursor_rejects_valid_base64_wrong_shape() -> None:
    garbage = base64.urlsafe_b64encode(b"no-pipe-separator-here").decode()
    with pytest.raises(CursorDecodeError):
        decode_cursor(garbage)
