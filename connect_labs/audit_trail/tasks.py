"""Audit-trail retention pipeline tasks.

- ``archive_audit_events``: nightly, writes the previous UTC day's events as
  gzipped JSONL to the Object-Locked archive bucket plus a SHA-256 digest
  object (batch tamper-evidence — a periodic seal instead of per-row hash
  chains).
- ``emit_canary_event``: every 30 minutes, writes a synthetic event and
  alarms (via logger.error → Sentry) if the previous canary is missing —
  detects silent pipeline failure.
- ``prune_archived_events``: manual/occasional, deletes hot rows older than
  AUDIT_TRAIL_HOT_RETENTION_DAYS only after verifying that day's archive
  object exists in S3. Uses pgtrigger.ignore — the single sanctioned bypass
  of the append-only trigger.
"""
import gzip
import hashlib
import json
import logging
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone

import pgtrigger
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone

from config import celery_app
from connect_labs.audit_trail import service
from connect_labs.audit_trail.context import audit_context
from connect_labs.audit_trail.models import Action, AuditEvent
from connect_labs.labs.s3_export import _get_s3_client

logger = logging.getLogger(__name__)

ARCHIVE_PREFIX = "audit-events"


def _archive_bucket() -> str | None:
    return getattr(settings, "AUDIT_TRAIL_ARCHIVE_BUCKET", None) or None


def _day_keys(day) -> tuple[str, str]:
    base = f"{ARCHIVE_PREFIX}/{day:%Y/%m/%d}"
    return f"{base}.jsonl.gz", f"{base}.sha256"


@celery_app.task()
def archive_audit_events(day_iso: str | None = None):
    """Archive one UTC day of audit events to S3 (default: yesterday).

    Idempotent: re-running overwrites the day's objects with the same content
    (plus any late rows). Returns a summary dict for task-result inspection.
    """
    bucket = _archive_bucket()
    if not bucket:
        logger.info("AUDIT_TRAIL_ARCHIVE_BUCKET not set; skipping archive")
        return {"archived": 0, "skipped": True}

    day = date.fromisoformat(day_iso) if day_iso else (timezone.now() - timedelta(days=1)).date()

    start = datetime.combine(day, time.min, tzinfo=dt_timezone.utc)
    end = start + timedelta(days=1)
    events = AuditEvent.objects.filter(occurred_at__gte=start, occurred_at__lt=end).order_by("id")

    lines = []
    for event in events.iterator(chunk_size=2000):
        lines.append(json.dumps(event.to_log_dict(), default=str))
    payload = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    digest = hashlib.sha256(payload).hexdigest()
    body = gzip.compress(payload)

    data_key, digest_key = _day_keys(day)
    s3 = _get_s3_client()
    s3.put_object(Bucket=bucket, Key=data_key, Body=body, ContentType="application/gzip")
    # Two digests, each naming exactly what it covers. The sidecar used to print
    # the digest of the UNCOMPRESSED payload against the ".jsonl.gz" filename, so
    # an auditor doing the obvious thing — `sha256sum` the object they downloaded
    # — got a mismatch on an archive that was in fact intact. An attestation
    # artifact that fails when the data is good is worse than none.
    # Line 1 verifies the stored object as-is; line 2 verifies the content after
    # `gunzip`, and is the digest to quote when attesting to the events.
    gz_digest = hashlib.sha256(body).hexdigest()
    content_name = data_key[: -len(".gz")]
    s3.put_object(
        Bucket=bucket,
        Key=digest_key,
        Body=(
            f"{gz_digest}  {data_key}\n"
            f"{digest}  {content_name}\n"
            f"# {len(lines)} events for {day}; line 1 = stored object, "
            f"line 2 = gunzipped content\n"
        ).encode(),
        ContentType="text/plain",
    )
    logger.info("Archived %s audit events for %s to s3://%s/%s", len(lines), day, bucket, data_key)
    return {"archived": len(lines), "day": str(day), "sha256": digest}


@celery_app.task()
def emit_canary_event():
    """Write a canary event and alert if the previous one is missing.

    A canary should exist every 30 minutes; if none landed in the last 2
    hours (worker outage, DB write failure), raise an ERROR log so Sentry
    pages someone — per the §164.308 expectation that the logging pipeline's
    own health is verified, not assumed.
    """
    two_hours_ago = timezone.now() - timedelta(hours=2)
    had_recent = AuditEvent.objects.filter(action=Action.CANARY, occurred_at__gte=two_hours_ago).exists()
    with audit_context(source="celery"):
        service.record(Action.CANARY, resource_type="canary", metadata={"task": "emit_canary_event"})
    if not had_recent:
        # Startup edge: the very first canary ever also lands here once.
        if AuditEvent.objects.filter(action=Action.CANARY).exists():
            logger.error("Audit-trail canary gap: no canary event landed in the last 2 hours")
    return {"had_recent": had_recent}


@celery_app.task()
def prune_archived_events(dry_run: bool = True):
    """Delete hot rows past retention whose day has a verified S3 archive.

    Defaults to dry_run — run with dry_run=False deliberately. Never touches
    a day without a matching archive object in S3.
    """
    bucket = _archive_bucket()
    if not bucket:
        return {"pruned": 0, "skipped": "no bucket configured"}

    cutoff = timezone.now() - timedelta(days=settings.AUDIT_TRAIL_HOT_RETENTION_DAYS)
    days = AuditEvent.objects.filter(occurred_at__lt=cutoff).dates("occurred_at", "day")
    s3 = _get_s3_client()
    pruned = 0
    skipped_days = []
    for day in days:
        data_key, _ = _day_keys(day)
        try:
            s3.head_object(Bucket=bucket, Key=data_key)
        except ClientError:
            skipped_days.append(str(day))
            continue
        day_start = datetime.combine(day, time.min, tzinfo=dt_timezone.utc)
        qs = AuditEvent.objects.filter(occurred_at__gte=day_start, occurred_at__lt=day_start + timedelta(days=1))
        if dry_run:
            pruned += qs.count()
        else:
            with pgtrigger.ignore("audit_trail.AuditEvent:append_only"):
                deleted, _ = qs.delete()
                pruned += deleted
    if skipped_days:
        logger.warning("Prune skipped days with no verified archive: %s", skipped_days)
    return {"pruned": pruned, "dry_run": dry_run, "skipped_days": skipped_days}
