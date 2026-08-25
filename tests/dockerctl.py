"""Docker/compose control for the live suites: stop, kill, restart and inspect the real
containers of a running Relay stack, and query its real Postgres and Redis through
`docker exec`.

Why `docker exec` rather than a database driver: these suites assert against whatever is
deployed, which may not expose Postgres or Redis to the host at all (the production
overlay deliberately doesn't). Shelling into the container is also the same thing an
operator does at 3am, so a chaos test that passes here is a procedure that works there.
"""

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

COMPOSE_PROJECT = os.environ.get("RELAY_COMPOSE_PROJECT", "relay")


def container(service: str) -> str:
    """Container name for a compose service, overridable per service for a stack that
    doesn't use the default `<project>-<service>-1` naming."""
    override = os.environ.get(f"RELAY_CONTAINER_{service.upper().replace('-', '_')}")
    return override or f"{COMPOSE_PROJECT}-{service}-1"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return _run(["docker", "info"], check=False).returncode == 0


def stack_available() -> bool:
    return docker_available() and is_running(container("api"))


def _run(
    args: list[str], *, check: bool = True, timeout: float = 120.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=timeout)


def docker(*args: str, check: bool = True, timeout: float = 120.0) -> str:
    return _run(["docker", *args], check=check, timeout=timeout).stdout.strip()


# -- lifecycle -------------------------------------------------------------------------


def is_running(name: str) -> bool:
    result = _run(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def stop(name: str, *, timeout: int = 10) -> None:
    """Graceful stop (SIGTERM, then SIGKILL after `timeout`). Used where the test needs the
    process to be *absent* for a while: a restart policy does not bring back a container
    stopped this way."""
    docker("stop", "-t", str(timeout), name)


def start(name: str) -> None:
    docker("start", name)


def kill(name: str, *, signal: str = "KILL") -> None:
    """`docker kill -s KILL` -- the process dies where it stands, no signal handler, no
    graceful shutdown. This is the difference between testing crash recovery and testing
    the graceful-shutdown path."""
    docker("kill", "-s", signal, name)


def restart(name: str, *, timeout: int = 10) -> None:
    docker("restart", "-t", str(timeout), name)


def ensure_running(name: str, *, timeout: float = 60.0) -> None:
    """Starts the container if it isn't up, then waits for it -- chaos tests must leave the
    stack the way they found it even when they fail midway."""
    if not is_running(name):
        _run(["docker", "start", name], check=False)
    wait_running(name, timeout=timeout)


def wait_running(name: str, *, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_running(name):
            return
        time.sleep(0.5)
    raise AssertionError(f"container {name} did not reach running state within {timeout}s")


def logs_since(name: str, since: str, *, tail: int = 5000) -> str:
    return docker("logs", "--since", since, "--tail", str(tail), name, check=False)


def logs(name: str, *, tail: int = 200) -> str:
    """The container's whole (retained) log tail, with no time window -- a worker that has
    had nothing to do since it started is quiet, not broken."""
    return docker("logs", "--tail", str(tail), name, check=False)


def restart_count(name: str) -> int:
    return int(docker("inspect", "-f", "{{.RestartCount}}", name))


# -- data plane ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Psql:
    """Queries the stack's real Postgres from inside its own container."""

    container_name: str
    user: str = os.environ.get("RELAY_PG_USER", "relay")
    database: str = os.environ.get("RELAY_PG_DATABASE", "relay")

    def scalar(self, sql: str) -> str:
        return docker(
            "exec", self.container_name, "psql", "-U", self.user, "-d", self.database, "-tAc", sql
        )

    def rows(self, sql: str) -> list[list[str]]:
        out = docker(
            "exec",
            self.container_name,
            "psql",
            "-U",
            self.user,
            "-d",
            self.database,
            "-tAF",
            "\x1f",
            "-c",
            sql,
        )
        return [line.split("\x1f") for line in out.splitlines() if line]

    def execute(self, sql: str) -> str:
        return self.scalar(sql)

    def count(self, table: str, where: str = "true") -> int:
        return int(self.scalar(f"SELECT COUNT(*) FROM {table} WHERE {where}"))


def psql() -> Psql:
    return Psql(container_name=container("postgres"))


def redis_cli(*args: str) -> str:
    return docker("exec", container("redis"), "redis-cli", *args)


# -- redis streams ---------------------------------------------------------------------

# Names duplicated as literals rather than imported from `relay.infra.streams`: the live
# suites talk to the deployment, and importing the application's own constants would let a
# rename silently keep the tests "passing" against a stack that no longer uses them.
DELIVERY_STREAM = "relay:deliveries:stream"
DISPATCH_GROUP = "relay:dispatchers"

_MESSAGE_ID = re.compile(r"^\d+-\d+$")


@dataclass(frozen=True, slots=True)
class PendingEntry:
    """One entry in the consumer group's pending-entries list: read by a consumer, not yet
    acked. The message id is the identity that must survive a reclaim."""

    message_id: str
    consumer: str
    idle_ms: int
    delivery_count: int


def pending_entries(
    *, stream: str = DELIVERY_STREAM, group: str = DISPATCH_GROUP
) -> list[PendingEntry]:
    raw = redis_cli("XPENDING", stream, group, "-", "+", "100").splitlines()
    entries: list[PendingEntry] = []
    for index in range(0, len(raw) - 3, 4):
        if not _MESSAGE_ID.match(raw[index].strip()):
            continue
        entries.append(
            PendingEntry(
                message_id=raw[index].strip(),
                consumer=raw[index + 1].strip(),
                idle_ms=int(raw[index + 2]),
                delivery_count=int(raw[index + 3]),
            )
        )
    return entries


def message_id_for_delivery(delivery_id: str, *, stream: str = DELIVERY_STREAM) -> str | None:
    """The stream entry that carries this delivery id, if it is still on the stream.

    `XRANGE` in redis-cli's raw mode is a flat list -- id, then alternating field/value --
    so the entry id is simply the last id-shaped line before the value we're looking for.
    """
    raw = redis_cli("XRANGE", stream, "-", "+").splitlines()
    current: str | None = None
    for line in raw:
        stripped = line.strip()
        if _MESSAGE_ID.match(stripped):
            current = stripped
        elif stripped == delivery_id:
            return current
    return None
