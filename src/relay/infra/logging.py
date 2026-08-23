"""Structured JSON logging, configured once per process (relay.api.app's create_app() and
each worker's __main__ block). Bridges stdlib logging through structlog rather than
rewriting every existing `logging.getLogger(__name__)` call site: third-party libraries
(uvicorn, sqlalchemy, asyncpg) and this codebase's own existing loggers all end up going
through the same JSON-to-stdout pipeline, and both stdlib-style and structlog-native calls
pick up whatever's bound into structlog's contextvars (correlation_id, chiefly) for the
duration of the current request or delivery attempt -- see relay.api.middleware and
relay.workers.dispatcher for where that's bound.
"""

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # False, not the perf-optimized default: a cached logger proxy resolves the
        # then-current global config on its first-ever log call and keeps it for the life of
        # the process, silently ignoring later structlog.configure() calls -- including
        # structlog.testing.capture_logs()'s swap. This codebase's log volume doesn't need
        # the optimization; predictable behavior under test does.
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)
