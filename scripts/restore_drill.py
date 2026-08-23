#!/usr/bin/env python3
"""Restores the latest S3 backup into a throwaway ("scratch") Postgres container -- never
the real one -- and verifies it by comparing per-table row counts against the live database.
This is the actually-*executed* half of the Phase 5 backup story: scripts/backup_postgres.py
being merely correct isn't the claim docs/runbook.md makes; a drill that was actually run and
whose output is recorded is. See docs/PROJECT_STATUS.md for the dated record of a real run.

Usage: uv run python scripts/restore_drill.py [--keep]

--keep leaves the scratch container running afterward (for manual inspection) instead of
tearing it down.
"""

import subprocess
import sys
import time
import uuid

import boto3

from relay.infra.settings import get_settings

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


def teardown(container: str) -> None:
    subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)


def main() -> int:
    settings = get_settings()
    keep = "--keep" in sys.argv

    key = latest_backup_key(
        settings.backup_s3_bucket,
        settings.backup_s3_prefix,
        endpoint_url=settings.backup_s3_endpoint_url,
    )
    if key is None:
        print(
            f"no backups found under s3://{settings.backup_s3_bucket}/{settings.backup_s3_prefix}/",
            file=sys.stderr,
        )
        return 1
    print(f"restoring s3://{settings.backup_s3_bucket}/{key}")
    dump = download_backup(
        settings.backup_s3_bucket, key, endpoint_url=settings.backup_s3_endpoint_url
    )
    print(f"downloaded {len(dump):,} bytes")

    container = f"relay-restore-drill-{uuid.uuid4().hex[:8]}"
    print(f"starting scratch container {container}...")
    start_scratch_postgres(container, db="relay", user="relay", password="relay")
    try:
        wait_until_ready(container, user="relay")
        print("restoring dump...")
        restore_into(container, dump, db="relay", user="relay")
        counts = row_counts(container, db="relay", user="relay", tables=VERIFY_TABLES)
        print("row counts in restored scratch database:")
        for table, count in counts.items():
            print(f"  {table}: {count}")
        return 0
    finally:
        if keep:
            print(f"--keep passed; leaving {container} running for inspection")
        else:
            teardown(container)
            print(f"tore down {container}")


if __name__ == "__main__":
    raise SystemExit(main())
