"""
S3 CSV export utility for WorkflowRunRecord and AuditSessionRecord.

Each record type maps to one CSV file in S3. Rows are upserted in-place
keyed by record ID (read → insert/replace → write back). All public
functions are best-effort: failures are logged and silenced so that a
broken S3 configuration never interrupts the user-facing action.

When LABS_EXPORTS_BUCKET is None or unset, all calls are no-ops.

Race condition note: with multiple Gunicorn workers, two simultaneous
writes to the same file can produce a lost-update. For a backup-only
use case this is acceptable — S3 versioning preserves prior state and
a subsequent write for the same record will self-heal.
"""
import csv
import io
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from django.conf import settings

logger = logging.getLogger(__name__)

WORKFLOW_RUNS_KEY = "audit_of_audits/workflow_runs.csv"
AUDIT_SESSIONS_KEY = "audit_of_audits/audit_sessions.csv"

WORKFLOW_RUN_FIELDS = [
    "run_id",
    "definition_id",
    "definition_name",
    "template_type",
    "opportunity_id",
    "opportunity_name",
    "created_at",
    "period_start",
    "period_end",
    "status",
    "selected_count",
    "username",
    "session_count",
    "completed_session_count",
    "avg_pct_passed",
    "pct_passing",
    "tasks_created",
    "images_reviewed",
    "pct_sampled",
]

AUDIT_SESSION_FIELDS = [
    "session_id",
    "workflow_run_id",
    "opportunity_id",
    "opportunity_name",
    "organization_id",
    "flw_username",
    "status",
    "overall_result",
    "title",
    "tag",
    "notes",
    "kpi_notes",
    "visit_count",
    "created_at",
]


def _get_bucket() -> str | None:
    return getattr(settings, "LABS_EXPORTS_BUCKET", None) or None


def _get_s3_client():
    """Build a boto3 S3 client, passing explicit credentials when available.

    On ECS the task IAM role provides credentials automatically (no env vars
    needed). Locally, credentials come from .env via django-environ which
    populates os.environ at settings load time.
    """
    kwargs = {}
    key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    if key_id and secret:
        kwargs["aws_access_key_id"] = key_id
        kwargs["aws_secret_access_key"] = secret
    kwargs["region_name"] = region
    return boto3.client("s3", **kwargs)


def _read_rows(s3_client, bucket: str, key: str, id_field: str) -> dict:
    """Return existing CSV rows as {id_value: row_dict}, or {} if file absent."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response["Body"].read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        return {row[id_field]: row for row in reader}
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            return {}
        raise


def _write_rows(s3_client, bucket: str, key: str, rows: dict, fieldnames: list[str]) -> None:
    """Serialise rows back to S3, stamping metadata."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows.values())
    content = buf.getvalue().encode("utf-8")
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType="text/csv",
        Metadata={
            "row-count": str(len(rows)),
            "last-updated": datetime.now(timezone.utc).isoformat(),
        },
    )


def upsert_workflow_run(run, opportunity_name: str = "", definition_name: str = "", template_type: str = "") -> None:
    """Upsert one WorkflowRunRecord row into workflow_runs.csv on S3.

    Existing values for opportunity_name, definition_name, and
    template_type are preserved when the caller passes empty strings
    (e.g. on run creation before these are known).
    """
    bucket = _get_bucket()
    if not bucket:
        return

    state = run.state or {}
    run_by = run.username or state.get("run_by", "") or ""

    try:
        s3 = _get_s3_client()
        rows = _read_rows(s3, bucket, WORKFLOW_RUNS_KEY, "run_id")
        existing = rows.get(str(run.id), {})

        row = {
            "run_id": run.id,
            "definition_id": run.definition_id or "",
            "definition_name": definition_name or existing.get("definition_name", ""),
            "template_type": template_type or existing.get("template_type", ""),
            "opportunity_id": run.opportunity_id,
            "opportunity_name": opportunity_name or existing.get("opportunity_name", ""),
            "created_at": run.created_at or "",
            "period_start": run.period_start or "",
            "period_end": run.period_end or "",
            "status": run.status or "unknown",
            "selected_count": run.selected_count,
            "username": run_by,
            "session_count": state.get("session_count", 0),
            "completed_session_count": state.get("completed_session_count", 0),
            "avg_pct_passed": state.get("avg_pct_passed", ""),
            "pct_passing": state.get("pct_passing", ""),
            "tasks_created": state.get("tasks_created", ""),
            "images_reviewed": state.get("images_reviewed", ""),
            "pct_sampled": state.get("sample_percentage", ""),
        }
        rows[str(run.id)] = row
        _write_rows(s3, bucket, WORKFLOW_RUNS_KEY, rows, WORKFLOW_RUN_FIELDS)

    except Exception:
        logger.error("S3 export failed for workflow run %s", run.id, exc_info=True)


def upsert_audit_session(session) -> None:
    """Upsert one AuditSessionRecord row into audit_sessions.csv on S3."""
    bucket = _get_bucket()
    if not bucket:
        return

    try:
        org_id = session.organization_id
        org_id_out = int(org_id) if org_id is not None else ""

        row = {
            "session_id": session.id,
            "workflow_run_id": session.workflow_run_id or "",
            "opportunity_id": session.opportunity_id,
            "opportunity_name": session.opportunity_name or "",
            "organization_id": org_id_out,
            "flw_username": session.flw_username or "",
            "status": session.status or "",
            "overall_result": session.overall_result or "",
            "title": session.title or "",
            "tag": session.tag or "",
            "notes": session.notes or "",
            "kpi_notes": session.kpi_notes or "",
            "visit_count": len(session.visit_ids) if session.visit_ids else 0,
            "created_at": session.data.get("created_at", ""),
        }

        s3 = _get_s3_client()
        rows = _read_rows(s3, bucket, AUDIT_SESSIONS_KEY, "session_id")
        rows[str(session.id)] = row
        _write_rows(s3, bucket, AUDIT_SESSIONS_KEY, rows, AUDIT_SESSION_FIELDS)

    except Exception:
        logger.error("S3 export failed for audit session %s", session.id, exc_info=True)
