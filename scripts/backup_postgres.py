#!/usr/bin/env python3
"""Dumps the running Postgres container via `docker exec ... pg_dump` (custom format --
compressed, and pg_restore can selectively/parallel-restore from it) and uploads the result
to S3 under a timestamped key. Meant to run nightly via cron/systemd timer on the deploy VPS,
next to scripts/deploy_remote.sh -- see docs/runbook.md for the timer unit.

Points at real AWS S3 in production (BACKUP_S3_ENDPOINT_URL unset, boto3's normal credential
chain). scripts/restore_drill.py exercises the exact same S3 calls against a local
S3-compatible endpoint (MinIO) so the restore path is actually verified without needing a
real AWS account in dev -- see docs/adr/0007-phase-5-observability-and-ops.md.
"""

import subprocess
from datetime import UTC, datetime

import boto3

from relay.infra.settings import get_settings


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
    settings = get_settings()
    now = datetime.now(UTC)
    key = build_backup_key(settings.backup_s3_prefix, now=now)

    print(f"dumping {settings.backup_postgres_container} (db=relay)...")
    dump = dump_database(settings.backup_postgres_container, db="relay", user="relay")
    print(f"dump is {len(dump):,} bytes; uploading to s3://{settings.backup_s3_bucket}/{key}...")
    upload_backup(
        dump,
        bucket=settings.backup_s3_bucket,
        key=key,
        endpoint_url=settings.backup_s3_endpoint_url,
    )
    print(f"backup complete: s3://{settings.backup_s3_bucket}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
