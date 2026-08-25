#!/usr/bin/env python3
"""Restores the latest S3 backup into a throwaway ("scratch") Postgres container -- never
the real one -- and verifies it by comparing per-table row counts against the live database.
This is the actually-*executed* half of the Phase 5 backup story: scripts/backup_postgres.py
being merely correct isn't the claim docs/runbook.md makes; a drill that was actually run and
whose output is recorded is. See docs/PROJECT_STATUS.md for the dated record of a real run.

Usage: uv run python scripts/restore_drill.py [--keep] [--no-compare-live]

--keep leaves the scratch container running afterward (for manual inspection) instead of
tearing it down. --no-compare-live skips the comparison against the live database, for
running the drill somewhere the live Postgres container isn't reachable.

Configuration comes from the environment, not from `relay.infra.settings` -- same reason as
scripts/backup_postgres.py: this has to be runnable on a deploy host that has an .env and a
compose file but no checkout of this repo.
"""

import os
import subprocess
import sys
import time
import uuid

import boto3

DEFAULT_BUCKET = "relay-backups"
DEFAULT_PREFIX = "postgres"
DEFAULT_LIVE_CONTAINER = "relay-postgres-1"

VERIFY_TABLES = (
    "tenants",
    "api_keys",
    "endpoints",
    "events",
    "outbox",
    "deliveries",
    "delivery_attempts",
)

READY_POLL_ATTEMPTS = 30
READY_POLL_INTERVAL_SECONDS = 1.0


def latest_backup_key(bucket: str, prefix: str, *, endpoint_url: str | None) -> str | None:
    s3 = boto3.client("s3", endpoint_url=endpoint_url)
    response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    objects = response.get("Contents", [])
    if not objects:
        return None
    # Keys are relay-YYYYmmddTHHMMSSZ.dump -- lexicographic order is chronological order.
    return max(obj["Key"] for obj in objects)


def download_backup(bucket: str, key: str, *, endpoint_url: str | None) -> bytes:
    s3 = boto3.client("s3", endpoint_url=endpoint_url)
    response = s3.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()  # type: ignore[no-any-return]


def start_scratch_postgres(container: str, *, db: str, user: str, password: str) -> None:
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "-e",
            f"POSTGRES_USER={user}",
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-e",
            f"POSTGRES_DB={db}",
            "postgres:16-alpine",
        ],
        check=True,
        capture_output=True,
    )


def wait_until_ready(container: str, *, user: str) -> None:
    for _ in range(READY_POLL_ATTEMPTS):
        result = subprocess.run(
            ["docker", "exec", container, "pg_isready", "-U", user],
            capture_output=True,
        )
        if result.returncode == 0:
            return
        time.sleep(READY_POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"{container} never became ready to accept connections")


def restore_into(container: str, dump: bytes, *, db: str, user: str) -> None:
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "pg_restore",
            "-U",
            user,
            "-d",
            db,
            "--no-owner",
            "--clean",
            "--if-exists",
        ],
        input=dump,
        check=True,
        capture_output=True,
    )


def row_counts(container: str, *, db: str, user: str, tables: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                user,
                "-d",
                db,
                "-tAc",
                f"SELECT count(*) FROM {table}",
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        counts[table] = int(result.stdout.strip())
    return counts


def compare_to_live(restored: dict[str, int], live: dict[str, int]) -> tuple[bool, list[str]]:
    """A restore that "worked" is one whose contents match the database it came from.

    The live database keeps taking writes while the drill runs, so the honest expectation is
    `live >= restored` per table, not equality: rows added after the dump was taken are drift,
    not data loss. `restored > live` is the alarming direction -- it means rows that existed
    at dump time are gone now -- and an empty restore of a non-empty table means the dump or
    the restore silently did nothing.
    """
    problems: list[str] = []
    for table, restored_count in restored.items():
        live_count = live.get(table)
        if live_count is None:
            problems.append(f"{table}: missing from the live database entirely")
        elif restored_count > live_count:
            problems.append(
                f"{table}: restored {restored_count} rows but live has only {live_count} -- "
                "rows present at dump time have since disappeared"
            )
        elif restored_count == 0 and live_count > 0:
            problems.append(
                f"{table}: restored 0 rows against {live_count} live -- the dump or the "
                "restore did nothing for this table"
            )
    return not problems, problems


def teardown(container: str) -> None:
    subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)


def main() -> int:
    keep = "--keep" in sys.argv
    compare_live = "--no-compare-live" not in sys.argv
    bucket = os.environ.get("BACKUP_S3_BUCKET", DEFAULT_BUCKET)
    prefix = os.environ.get("BACKUP_S3_PREFIX", DEFAULT_PREFIX)
    endpoint_url = os.environ.get("BACKUP_S3_ENDPOINT_URL") or None
    live_container = os.environ.get("BACKUP_POSTGRES_CONTAINER", DEFAULT_LIVE_CONTAINER)

    key = latest_backup_key(bucket, prefix, endpoint_url=endpoint_url)
    if key is None:
        print(f"no backups found under s3://{bucket}/{prefix}/", file=sys.stderr)
        return 1
    print(f"restoring s3://{bucket}/{key}")
    dump = download_backup(bucket, key, endpoint_url=endpoint_url)
    print(f"downloaded {len(dump):,} bytes")

    container = f"relay-restore-drill-{uuid.uuid4().hex[:8]}"
    print(f"starting scratch container {container}...")
    start_scratch_postgres(container, db="relay", user="relay", password="relay")
    try:
        wait_until_ready(container, user="relay")
        print("restoring dump...")
        restore_into(container, dump, db="relay", user="relay")
        restored = row_counts(container, db="relay", user="relay", tables=VERIFY_TABLES)
        if not compare_live:
            print("row counts in restored scratch database:")
            for table, count in restored.items():
                print(f"  {table}: {count}")
            return 0

        live = row_counts(live_container, db="relay", user="relay", tables=VERIFY_TABLES)
        print(f"{'table':<20} {'restored':>10} {'live':>10}  drift")
        for table, count in restored.items():
            drift = live.get(table, 0) - count
            print(f"  {table:<18} {count:>10} {live.get(table, 0):>10}  {drift:+}")
        ok, problems = compare_to_live(restored, live)
        if not ok:
            print("\nRESTORE DOES NOT MATCH THE LIVE DATABASE:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print(
            "\nrestore verified: every table restored, and no table lost rows that existed "
            "when the dump was taken (positive drift is writes since the dump)."
        )
        return 0
    finally:
        if keep:
            print(f"--keep passed; leaving {container} running for inspection")
        else:
            teardown(container)
            print(f"tore down {container}")


if __name__ == "__main__":
    raise SystemExit(main())
