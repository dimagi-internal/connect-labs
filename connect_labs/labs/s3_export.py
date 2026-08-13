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
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings

logger = logging.getLogger(__name__)

WORKFLOW_RUNS_KEY = "audit_of_audits/workflow_runs.csv"
AUDIT_SESSIONS_KEY = "audit_of_audits/audit_sessions.csv"
CLASSIFIER_FAILS_KEY = "audit_of_audits/classifier_fails.csv"

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

CLASSIFIER_FAIL_FIELDS = [
    "row_id",
    "session_id",
    "workflow_run_id",
    "opportunity_id",
    "opportunity_name",
    "visit_id",
    "blob_id",
    "question_id",
    "classifier_id",
    "classifier_label",
    "ai_confidence",
    "ai_flagged_at",
    "image_url",
    "form_url",
    "connect_url",
    "ai_implied_result",
    "human_result",
    "human_notes",
    "was_overridden",
    "overridden_at",
    "reviewed_by",
]


def _get_bucket() -> str | None:
    return getattr(settings, "LABS_EXPORTS_BUCKET", None) or None


def _get_s3_client():
    """Build a boto3 S3 client, passing explicit credentials when available.

    On ECS the task IAM role provides credentials automatically — LABS_AWS_*
    settings will be None and boto3 falls back to the instance metadata.
    Locally, credentials are read from .env via Django settings.
    """
    kwargs = {}
    key_id = getattr(settings, "LABS_AWS_ACCESS_KEY_ID", None)
    secret = getattr(settings, "LABS_AWS_SECRET_ACCESS_KEY", None)
    token = getattr(settings, "LABS_AWS_SESSION_TOKEN", None)
    region = getattr(settings, "LABS_AWS_DEFAULT_REGION", None) or "us-east-1"
    if key_id and secret:
        kwargs["aws_access_key_id"] = key_id
        kwargs["aws_secret_access_key"] = secret
    if token:
        kwargs["aws_session_token"] = token
    kwargs["region_name"] = region
    kwargs["config"] = Config(signature_version="s3v4")
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


def upsert_workflow_run(
    run, opportunity_name: str = "", definition_name: str = "", template_type: str = "", username: str = ""
) -> None:
    """Upsert one WorkflowRunRecord row into workflow_runs.csv on S3.

    Existing values for opportunity_name, definition_name, template_type,
    and username are preserved when the caller passes empty strings.
    """
    bucket = _get_bucket()
    if not bucket:
        return

    state = run.state or {}
    run_by = username or state.get("run_by", "") or ""

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
            "created_at": run.created_at or existing.get("created_at", ""),
            "period_start": run.period_start or existing.get("period_start", ""),
            "period_end": run.period_end or existing.get("period_end", ""),
            "status": run.status or "unknown",
            "selected_count": run.selected_count,
            "username": run_by or existing.get("username", ""),
            "session_count": state.get("session_count", "") or state.get("flws_count", ""),
            "completed_session_count": state.get("completed_session_count", "") or state.get("completed_flws", ""),
            # Templates write "avg_passed"; AoA report also checks avg_pct_passed/avg_pass_rate
            "avg_pct_passed": (
                state.get("avg_passed")
                or state.get("avg_pct_passed")
                or state.get("avg_pass_rate")
                or state.get("pass_rate")
                or ""
            ),
            "pct_passing": (
                state.get("pass_threshold")
                or state.get("pct_passing")
                or state.get("config", {}).get("threshold")
                or ""
            ),
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


def record_classifier_fails(rows: list[dict]) -> None:
    """Batch-upsert brand-new classifier-fail rows into classifier_fails.csv on S3.

    Each item in ``rows`` must have: session_id, visit_id, blob_id, classifier_id,
    and should have workflow_run_id, opportunity_id, opportunity_name, question_id,
    classifier_label, ai_confidence, ai_implied_result (the auto-applied human_result
    at flag time, or None for a flag-only classifier like the duplicate detector),
    and optionally image_url/form_url/connect_url (resolved by the caller via
    connect_labs.audit.link_helpers.resolve_urls_by_blob at record time, so the
    training-data export doesn't have to wait for a human review to link back to
    the source image/form/visit -- see connect_labs/audit/classifier_fail_sync.py
    for the fallback path that still backfills these post-review if a caller
    couldn't resolve them here).

    Re-running AI review or duplicate detection on an already-reviewed session must
    not clobber human review data recorded by sync_classifier_fail_outcomes -- when a
    row already exists, only its AI-facts fields (label/confidence/implied result) are
    refreshed; human_result/human_notes/was_overridden/overridden_at/reviewed_by are
    left untouched. URLs are the exception: a non-empty incoming URL always overwrites
    (it's a freshly-resolved link, not human-authored data), but an empty/missing
    incoming URL never clobbers an already-populated one.

    One S3 read-modify-write for the whole batch -- callers should collect every fail
    for a run and call this once, not once per row.
    """
    if not rows:
        return
    bucket = _get_bucket()
    if not bucket:
        return

    try:
        s3 = _get_s3_client()
        existing = _read_rows(s3, bucket, CLASSIFIER_FAILS_KEY, "row_id")
        now = datetime.now(timezone.utc).isoformat()

        for item in rows:
            row_id = f"{item['session_id']}:{item['blob_id']}:{item['classifier_id']}"
            ai_implied_result = item.get("ai_implied_result") or ""
            prior = existing.get(row_id)
            row = prior or {
                "row_id": row_id,
                "ai_flagged_at": now,
                "image_url": "",
                "form_url": "",
                "connect_url": "",
                "human_result": ai_implied_result,
                "human_notes": "",
                "was_overridden": "false",
                "overridden_at": "",
                "reviewed_by": "",
            }
            row.update(
                {
                    "row_id": row_id,
                    "session_id": item["session_id"],
                    "workflow_run_id": item.get("workflow_run_id") or "",
                    "opportunity_id": item.get("opportunity_id") or "",
                    "opportunity_name": item.get("opportunity_name") or "",
                    "visit_id": item["visit_id"],
                    "blob_id": item["blob_id"],
                    "question_id": item.get("question_id") or "",
                    "classifier_id": item["classifier_id"],
                    "classifier_label": item.get("classifier_label") or "",
                    "ai_confidence": (item.get("ai_confidence") if item.get("ai_confidence") is not None else ""),
                    "ai_implied_result": ai_implied_result,
                }
            )
            for url_field in ("image_url", "form_url", "connect_url"):
                value = item.get(url_field)
                if value:
                    row[url_field] = value
            existing[row_id] = row

        _write_rows(s3, bucket, CLASSIFIER_FAILS_KEY, existing, CLASSIFIER_FAIL_FIELDS)

    except Exception:
        logger.error("S3 export failed for %d classifier fail row(s)", len(rows), exc_info=True)


def sync_classifier_fail_outcomes(
    session_id,
    human_result_by_blob: dict,
    human_notes_by_blob: dict,
    url_by_blob: dict | None = None,
    reviewed_by: str = "",
) -> None:
    """Update classifier_fails.csv rows for one session with the human's current
    verdict/notes, flagging an override when the result differs from a PRIOR
    non-empty verdict on record -- a flag-only classifier (e.g. the duplicate
    detector) seeds human_result as "" (no AI-implied verdict), so the human's
    very first answer on that row is recorded but never counted as an
    override. Also backfills image/form/Connect URLs the first time they're
    available (each field is filled once and left alone after).

    Args:
        session_id: AuditSessionRecord id whose rows should be updated.
        human_result_by_blob: {blob_id: current assessment result}, only for
            blobs that have a result set.
        human_notes_by_blob: {blob_id: current assessment notes free-text},
            only for blobs that have notes.
        url_by_blob: optional {blob_id: {"image_url", "form_url", "connect_url"}}.
        reviewed_by: username of whoever triggered this save, stamped onto any
            row whose human_result changes.

    One S3 read-modify-write for the whole session; a no-op write (nothing
    changed) skips the S3 PUT entirely.
    """
    bucket = _get_bucket()
    if not bucket:
        return

    try:
        s3 = _get_s3_client()
        rows = _read_rows(s3, bucket, CLASSIFIER_FAILS_KEY, "row_id")
        now = datetime.now(timezone.utc).isoformat()
        session_id_str = str(session_id)
        changed = False

        for row in rows.values():
            if row.get("session_id") != session_id_str:
                continue
            blob_id = row.get("blob_id")

            new_result = human_result_by_blob.get(blob_id)
            prior_result = row.get("human_result")
            if new_result and new_result != prior_result:
                row["human_result"] = new_result
                # A flag-only classifier (e.g. the duplicate detector) seeds
                # human_result as "" (no AI-implied verdict) -- the human's
                # very FIRST verdict on that row is an initial assessment, not
                # an override, and must not be flagged as one. Only a result
                # that actually replaces a prior (AI-implied or human-set)
                # verdict counts as an override.
                if prior_result:
                    row["was_overridden"] = "true"
                    row["overridden_at"] = now
                if reviewed_by:
                    row["reviewed_by"] = reviewed_by
                changed = True

            new_notes = human_notes_by_blob.get(blob_id, "")
            if new_notes != row.get("human_notes", ""):
                row["human_notes"] = new_notes
                changed = True

            if url_by_blob:
                urls = url_by_blob.get(blob_id) or {}
                for field in ("image_url", "form_url", "connect_url"):
                    value = urls.get(field)
                    if value and not row.get(field):
                        row[field] = value
                        changed = True

        if changed:
            _write_rows(s3, bucket, CLASSIFIER_FAILS_KEY, rows, CLASSIFIER_FAIL_FIELDS)

    except Exception:
        logger.error("S3 sync failed for classifier fail outcomes, session %s", session_id, exc_info=True)
