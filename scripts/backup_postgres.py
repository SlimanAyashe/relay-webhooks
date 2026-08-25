#!/usr/bin/env python3
"""Dumps the running Postgres container via `docker exec ... pg_dump` (custom format --
compressed, and pg_restore can selectively/parallel-restore from it) and uploads the result
to S3 under a timestamped key. Meant to run nightly via cron/systemd timer on the deploy VPS,
next to scripts/deploy_remote.sh -- see docs/runbook.md for the timer unit.

Points at real AWS S3 in production (BACKUP_S3_ENDPOINT_URL unset, boto3's normal credential
chain). scripts/restore_drill.py exercises the exact same S3 calls against a local
S3-compatible endpoint (MinIO) so the restore path is actually verified without needing a
real AWS account in dev -- see docs/adr/0007-phase-5-observability-and-ops.md.

Configuration comes from the environment directly rather than from
`relay.infra.settings.Settings`, and the defaults below mirror it. That is deliberate: this
script has to run from a systemd timer on the deploy host, where /opt/relay holds a compose
file and an .env and *not* a checkout of this repo -- an import of the application package
would make the nightly backup depend on the one thing a deploy doesn't put there. The
Phase 8 drill (docs/runbook.md) is what surfaced that; before it, the timer had never been
installed anywhere the difference showed up.
"""

import os
import subprocess
from datetime import UTC, datetime

import boto3

DEFAULT_BUCKET = "relay-backups"
DEFAULT_PREFIX = "postgres"
DEFAULT_CONTAINER = "relay-postgres-1"


def dump_database(container: str, *, db: str, user: str) -> bytes:
    """Runs pg_dump inside the already-running Postgres container rather than requiring a
    Postgres client on whatever host runs this script -- the container already has one, and
    matches its own server version exactly.
    """
    result = subprocess.run(
        ["docker", "exec", container, "pg_dump", "-U", user, "--format=custom", db],
        capture_output=True,
        check=True,
    )
    return result.stdout


def build_backup_key(prefix: str, *, now: datetime) -> str:
    return f"{prefix}/relay-{now.strftime('%Y%m%dT%H%M%SZ')}.dump"


def upload_backup(data: bytes, *, bucket: str, key: str, endpoint_url: str | None) -> None:
    s3 = boto3.client("s3", endpoint_url=endpoint_url)
    s3.put_object(Bucket=bucket, Key=key, Body=data)


def main() -> int:
    bucket = os.environ.get("BACKUP_S3_BUCKET", DEFAULT_BUCKET)
    prefix = os.environ.get("BACKUP_S3_PREFIX", DEFAULT_PREFIX)
    container = os.environ.get("BACKUP_POSTGRES_CONTAINER", DEFAULT_CONTAINER)
    # An empty-but-set value means "real AWS S3", same as unset -- systemd EnvironmentFile
    # has no way to express "absent", so the empty string has to mean it too.
    endpoint_url = os.environ.get("BACKUP_S3_ENDPOINT_URL") or None

    key = build_backup_key(prefix, now=datetime.now(UTC))

    print(f"dumping {container} (db=relay)...")
    dump = dump_database(container, db="relay", user="relay")
    print(f"dump is {len(dump):,} bytes; uploading to s3://{bucket}/{key}...")
    upload_backup(dump, bucket=bucket, key=key, endpoint_url=endpoint_url)
    print(f"backup complete: s3://{bucket}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
