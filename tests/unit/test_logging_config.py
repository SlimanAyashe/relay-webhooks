import json
import logging
from collections.abc import Iterator

import pytest

from relay.infra.logging import configure_logging


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    """configure_logging() mutates global logging state (root handlers/level) by design --
    it's meant to be called once at process startup. Save and restore it around each test
    here so this file doesn't leak a WARNING-level root logger into every test that runs
    after it in the same session (tests/unit collects before tests/integration, and a
    suppressed-below-WARNING root would silently swallow the INFO-level request-completed
    line relay.api.middleware relies on elsewhere).
    """
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.setLevel(original_level)


def test_configure_logging_sets_json_stdout_handler_at_the_given_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("WARNING")

    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1

    logging.getLogger("relay.somewhere").warning("careful now", extra={})
    line = capsys.readouterr().out.strip()
    payload = json.loads(line)

    assert payload["event"] == "careful now"
    assert payload["level"] == "warning"
    assert payload["logger"] == "relay.somewhere"
    assert "timestamp" in payload


def test_configure_logging_suppresses_below_the_configured_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("WARNING")
    capsys.readouterr()

    logging.getLogger("relay.somewhere").info("too quiet to matter")

    assert capsys.readouterr().out == ""
