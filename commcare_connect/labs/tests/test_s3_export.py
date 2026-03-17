"""Tests for labs/s3_export.py — S3 CSV upsert utility."""
import csv
import io
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from commcare_connect.audit.models import AuditSessionRecord
from commcare_connect.labs import s3_export
from commcare_connect.workflow.data_access import WorkflowRunRecord

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_run(run_id=1, status="in_progress", opportunity_id=42, username="testuser"):
    """Build a minimal WorkflowRunRecord from a dict."""
    api_data = {
        "id": run_id,
        "experiment": "workflow",
        "type": "workflow_run",
        "data": {
            "definition_id": 10,
            "status": status,
            "state": {"sample_percentage": "20"},
            "created_at": "2026-03-16T10:00:00+00:00",
        },
        "username": username,
        "opportunity_id": opportunity_id,
        "organization_id": None,
        "program_id": None,
        "labs_record_id": None,
        "public": False,
    }
    return WorkflowRunRecord(api_data)


def _make_session(session_id=5, run_id=1, opp_id=42, status="in_progress", overall_result=None):
    """Build a minimal AuditSessionRecord from a dict."""
    api_data = {
        "id": session_id,
        "experiment": "audit",
        "type": "AuditSession",
        "data": {
            "title": "Test session",
            "tag": "tag1",
            "status": status,
            "overall_result": overall_result,
            "notes": "",
            "kpi_notes": "",
            "visit_ids": [101, 102],
            "opportunity_id": opp_id,
            "opportunity_name": "Test Opp",
            "created_at": "2026-03-16T10:00:00+00:00",
            # AuditSessionRecord.flw_username iterates visit_images.values() and
            # returns the username from the first non-empty image list entry.
            "visit_images": {"101": [{"username": "flw_user_1"}]},
        },
        "username": "testuser",
        "opportunity_id": opp_id,
        "organization_id": "10",
        "program_id": None,
        "labs_record_id": run_id,  # AuditSessionRecord.workflow_run_id reads from labs_record_id
        "public": False,
    }
    return AuditSessionRecord(api_data)


def _csv_body(rows: list[dict], fieldnames: list[str]) -> bytes:
    """Serialise rows to CSV bytes (same format s3_export writes)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _no_such_key_error():
    err = ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject")
    return err


# ── upsert_workflow_run ───────────────────────────────────────────────────────

@patch("commcare_connect.labs.s3_export.boto3")
def test_upsert_workflow_run_creates_new_file(mock_boto3, settings):
    """When CSV doesn't exist yet, creates it with header + one row."""
    settings.LABS_EXPORTS_BUCKET = "test-bucket"

    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    mock_s3.get_object.side_effect = _no_such_key_error()

    run = _make_run(run_id=1, status="in_progress")
    s3_export.upsert_workflow_run(run, opportunity_name="My Opp")

    mock_s3.put_object.assert_called_once()
    call_kwargs = mock_s3.put_object.call_args[1]
    assert call_kwargs["Bucket"] == "test-bucket"
    assert call_kwargs["Key"] == s3_export.WORKFLOW_RUNS_KEY
    body = call_kwargs["Body"].decode("utf-8")
    reader = list(csv.DictReader(io.StringIO(body)))
    assert len(reader) == 1
    assert reader[0]["run_id"] == "1"
    assert reader[0]["opportunity_name"] == "My Opp"
    assert reader[0]["status"] == "in_progress"


@patch("commcare_connect.labs.s3_export.boto3")
def test_upsert_workflow_run_replaces_existing_row(mock_boto3, settings):
    """When run_id row exists, it is replaced with updated data."""
    settings.LABS_EXPORTS_BUCKET = "test-bucket"

    existing_row = {f: "" for f in s3_export.WORKFLOW_RUN_FIELDS}
    existing_row.update({"run_id": "1", "status": "in_progress", "opportunity_name": "My Opp"})

    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: _csv_body([existing_row], s3_export.WORKFLOW_RUN_FIELDS))
    }

    run = _make_run(run_id=1, status="completed")
    s3_export.upsert_workflow_run(run)

    body = mock_s3.put_object.call_args[1]["Body"].decode("utf-8")
    reader = list(csv.DictReader(io.StringIO(body)))
    assert len(reader) == 1
    assert reader[0]["status"] == "completed"
    # Existing opportunity_name is preserved when new value is empty
    assert reader[0]["opportunity_name"] == "My Opp"


@patch("commcare_connect.labs.s3_export.boto3")
def test_upsert_workflow_run_no_bucket_is_noop(mock_boto3, settings):
    """When LABS_EXPORTS_BUCKET is None, does nothing."""
    settings.LABS_EXPORTS_BUCKET = None
    run = _make_run()
    s3_export.upsert_workflow_run(run)
    mock_boto3.client.assert_not_called()


@patch("commcare_connect.labs.s3_export.boto3")
def test_upsert_workflow_run_s3_error_is_silenced(mock_boto3, settings):
    """When S3 raises an unexpected error, it is logged and swallowed."""
    settings.LABS_EXPORTS_BUCKET = "test-bucket"
    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    mock_s3.get_object.side_effect = RuntimeError("network error")

    run = _make_run()
    # Must not raise
    s3_export.upsert_workflow_run(run)
    mock_s3.put_object.assert_not_called()


# ── upsert_audit_session ──────────────────────────────────────────────────────

@patch("commcare_connect.labs.s3_export.boto3")
def test_upsert_audit_session_creates_new_file(mock_boto3, settings):
    """When CSV doesn't exist yet, creates it with header + one row."""
    settings.LABS_EXPORTS_BUCKET = "test-bucket"

    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    mock_s3.get_object.side_effect = _no_such_key_error()

    session = _make_session(session_id=5, run_id=1, status="in_progress")
    s3_export.upsert_audit_session(session)

    call_kwargs = mock_s3.put_object.call_args[1]
    assert call_kwargs["Key"] == s3_export.AUDIT_SESSIONS_KEY
    body = call_kwargs["Body"].decode("utf-8")
    reader = list(csv.DictReader(io.StringIO(body)))
    assert len(reader) == 1
    assert reader[0]["session_id"] == "5"
    assert reader[0]["workflow_run_id"] == "1"
    assert reader[0]["visit_count"] == "2"


@patch("commcare_connect.labs.s3_export.boto3")
def test_upsert_audit_session_replaces_existing_row(mock_boto3, settings):
    """When session_id row exists, status is updated."""
    settings.LABS_EXPORTS_BUCKET = "test-bucket"

    existing_row = {f: "" for f in s3_export.AUDIT_SESSION_FIELDS}
    existing_row.update({"session_id": "5", "status": "in_progress"})

    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: _csv_body([existing_row], s3_export.AUDIT_SESSION_FIELDS))
    }

    session = _make_session(session_id=5, status="completed", overall_result="pass")
    s3_export.upsert_audit_session(session)

    body = mock_s3.put_object.call_args[1]["Body"].decode("utf-8")
    reader = list(csv.DictReader(io.StringIO(body)))
    assert len(reader) == 1
    assert reader[0]["status"] == "completed"
    assert reader[0]["overall_result"] == "pass"


@patch("commcare_connect.labs.s3_export.boto3")
def test_upsert_audit_session_no_bucket_is_noop(mock_boto3, settings):
    """When LABS_EXPORTS_BUCKET is None, does nothing."""
    settings.LABS_EXPORTS_BUCKET = None
    session = _make_session()
    s3_export.upsert_audit_session(session)
    mock_boto3.client.assert_not_called()


@patch("commcare_connect.labs.s3_export.boto3")
def test_upsert_audit_session_s3_error_is_silenced(mock_boto3, settings):
    """When S3 raises an unexpected error, it is logged and swallowed."""
    settings.LABS_EXPORTS_BUCKET = "test-bucket"
    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    mock_s3.get_object.side_effect = RuntimeError("network error")

    session = _make_session()
    # Must not raise
    s3_export.upsert_audit_session(session)
    mock_s3.put_object.assert_not_called()


@patch("commcare_connect.labs.s3_export.boto3")
def test_upsert_audit_session_organization_id_coerced_to_int(mock_boto3, settings):
    """organization_id (str in API) is written as int string, not raw string."""
    settings.LABS_EXPORTS_BUCKET = "test-bucket"
    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    mock_s3.get_object.side_effect = _no_such_key_error()

    session = _make_session(session_id=5)
    # _make_session sets organization_id="10" (string from API)
    s3_export.upsert_audit_session(session)

    body = mock_s3.put_object.call_args[1]["Body"].decode("utf-8")
    reader = list(csv.DictReader(io.StringIO(body)))
    assert reader[0]["organization_id"] == "10"


@patch("commcare_connect.labs.s3_export.boto3")
def test_row_count_in_metadata(mock_boto3, settings):
    """put_object metadata row-count reflects cumulative rows after each write."""
    settings.LABS_EXPORTS_BUCKET = "test-bucket"
    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3

    # First call: file doesn't exist yet
    mock_s3.get_object.side_effect = _no_such_key_error()
    s3_export.upsert_workflow_run(_make_run(run_id=1))

    first_call = mock_s3.put_object.call_args_list[0][1]
    assert first_call["Metadata"]["row-count"] == "1"

    # Second call: simulate S3 returning what was just written
    first_body = first_call["Body"]
    mock_s3.get_object.side_effect = None
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: first_body)
    }
    s3_export.upsert_workflow_run(_make_run(run_id=2))

    second_call = mock_s3.put_object.call_args_list[1][1]
    assert second_call["Metadata"]["row-count"] == "2"
