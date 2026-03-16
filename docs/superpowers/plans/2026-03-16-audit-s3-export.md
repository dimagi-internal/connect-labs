# Audit Export to S3 — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export WorkflowRunRecord and AuditSessionRecord data to S3 as durable CSV backups, triggered at run create/complete and session create/complete, with a Dimagi-only Labs download page.

**Architecture:** A shared `labs/s3_export.py` utility does CSV upsert-in-place on S3 (keyed by record ID). Event hooks in `workflow/views.py` and `audit/views.py` call it after successful API writes. A new `custom_analysis/exports/` page lists both files and serves pre-signed download links.

**Tech Stack:** Python `boto3` (already in requirements), Django `env` settings pattern, S3 object metadata for last-updated/row-count, pre-signed URLs for browser download.

**Spec:** `docs/superpowers/specs/2026-03-16-audit-export-design.md`

---

## Chunk 1: Infrastructure

### Task 1: Settings — add `LABS_EXPORTS_BUCKET`

**Files:**
- Modify: `config/settings/base.py`

- [ ] **Step 1: Add the setting**

Find the block near the bottom of `config/settings/base.py` where `LABS_ADMIN_USERNAMES` is defined (around line 432). Add directly below it:

```python
# S3 bucket for exporting audit/workflow records as CSV backups.
# When None (default), all export calls are silently skipped.
LABS_EXPORTS_BUCKET = env("LABS_EXPORTS_BUCKET", default=None)
```

- [ ] **Step 2: Add to `.env` for local testing**

In the project root `.env` file, add (leave blank for now — local dev skips export when None):

```
LABS_EXPORTS_BUCKET=
```

- [ ] **Step 3: Commit**

```bash
git add config/settings/base.py .env
git commit -m "feat: add LABS_EXPORTS_BUCKET setting"
```

---

### Task 2: S3 export utility

**Files:**
- Create: `commcare_connect/labs/s3_export.py`
- Create: `commcare_connect/labs/tests/test_s3_export.py`

#### Step 1: Write failing tests

- [ ] Create `commcare_connect/labs/tests/test_s3_export.py`:

```python
"""Tests for labs/s3_export.py — S3 CSV upsert utility."""
import csv
import io
from unittest.mock import MagicMock, call, patch

import pytest
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
    err = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject"
    )
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest commcare_connect/labs/tests/test_s3_export.py -v
```
Expected: all FAIL with `ModuleNotFoundError: cannot import 's3_export'`

- [ ] **Step 3: Create `commcare_connect/labs/s3_export.py`**

```python
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
        s3 = boto3.client("s3")
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

        s3 = boto3.client("s3")
        rows = _read_rows(s3, bucket, AUDIT_SESSIONS_KEY, "session_id")
        rows[str(session.id)] = row
        _write_rows(s3, bucket, AUDIT_SESSIONS_KEY, rows, AUDIT_SESSION_FIELDS)

    except Exception:
        logger.error("S3 export failed for audit session %s", session.id, exc_info=True)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest commcare_connect/labs/tests/test_s3_export.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/labs/s3_export.py commcare_connect/labs/tests/test_s3_export.py
git commit -m "feat: add labs/s3_export.py with CSV upsert for workflow runs and audit sessions"
```

---

## Chunk 2: Event Hooks

### Task 3: Hook workflow run create and complete

**Files:**
- Modify: `commcare_connect/workflow/views.py`

- [ ] **Step 1: Add imports at top of `workflow/views.py`**

Find the existing imports block at the top of the file. Add both — neither is currently imported in this file:

```python
from commcare_connect.labs import s3_export
from commcare_connect.labs.context import get_org_data
```

- [ ] **Step 2: Hook create_run in `WorkflowRunView.get()`**

Current code in `WorkflowRunView.get()` at lines 242–256:

```python
                    run = data_access.create_run(
                        definition_id=definition_id,
                        opportunity_id=opportunity_id,
                        period_start=week_start.isoformat(),
                        period_end=week_end.isoformat(),
                        initial_state={"worker_states": {}},
                    )
                except Exception as e:
                    logger.exception("Failed to create run for opp %s", opportunity_id)
                    return super().get(request, *args, **kwargs)
                finally:
                    data_access.close()
                params = request.GET.copy()
```

After the entire `try/except/finally` compound statement — i.e., after the `finally: data_access.close()` block and before `params = request.GET.copy()` — insert. (The exception branch returns early, so this code only runs on the success path where `run` is guaranteed to be bound.)

```python
                org_data = get_org_data(request)
                opp_map = {o["id"]: o.get("name", "") for o in org_data.get("opportunities", [])}
                s3_export.upsert_workflow_run(
                    run, opportunity_name=opp_map.get(run.opportunity_id, "")
                )
```

- [ ] **Step 3: Hook complete_run in `complete_run_api()`**

Current code at lines 797–812:

```python
        result = data_access.complete_run(
            run_id=run_id,
            overall_result=overall_result,
            notes=notes,
            run=run,
        )

        if not result:
            return JsonResponse({"error": "Failed to update run"}, status=500)

        return JsonResponse(
            {
                "success": True,
                "status": "completed",
                "overall_result": overall_result,
            }
        )
```

After the `if not result:` guard (after line 805), insert the export call before the success `return`:

```python
        s3_export.upsert_workflow_run(result)
```

- [ ] **Step 4: Run existing workflow tests — verify nothing is broken**

```bash
pytest commcare_connect/workflow/ -v
```
Expected: all existing tests PASS (export is a no-op because `LABS_EXPORTS_BUCKET` is not set in test settings)

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/workflow/views.py
git commit -m "feat: export workflow run to S3 on create and complete"
```

---

### Task 4: Hook audit session create and complete

**Files:**
- Modify: `commcare_connect/audit/views.py`

- [ ] **Step 1: Add import at top of `audit/views.py`**

```python
from commcare_connect.labs import s3_export
```

- [ ] **Step 2: Hook first create_audit_session call (per-FLW loop, line ~828)**

Current code around line 828:

```python
                    session = data_access.create_audit_session(
                        username=username,
                        visit_ids=flw_visit_ids,
                        title=session_title,
                        tag=criteria.get("tag", ""),
                        opportunity_id=opp_id,
                        audit_type=audit_type,
                        criteria=normalized_criteria,
                        opportunity_name=opp_name or "",
                        visit_images=flw_images,
                        workflow_run_id=None,
                    )
                    sessions_created.append({"session_id": session.id, ...})
```

After `sessions_created.append(...)`, insert:

```python
                    s3_export.upsert_audit_session(session)
```

- [ ] **Step 3: Hook second create_audit_session call (single session, line ~881)**

Current code around line 881:

```python
            session = data_access.create_audit_session(
                username=username,
                visit_ids=visit_ids,
                ...
                workflow_run_id=None,
            )

            # Determine redirect URL
            redirect_url = reverse_lazy("audit:session_list")
```

After `session = data_access.create_audit_session(...)` and before `redirect_url = ...`, insert:

```python
            s3_export.upsert_audit_session(session)
```

- [ ] **Step 4: Hook session completion (line ~296)**

Current code around lines 292–298:

```python
                session = data_access.complete_audit_session(
                    session=session, overall_result=overall_result, notes=notes, kpi_notes=kpi_notes
                )
                session.data["completed_at"] = timezone.now().isoformat()
                session = data_access.save_audit_session(session)

                return JsonResponse({"success": True})
```

After `session = data_access.save_audit_session(session)` and before `return JsonResponse(...)`, insert:

```python
                s3_export.upsert_audit_session(session)
```

- [ ] **Step 5: Run existing audit tests — verify nothing is broken**

```bash
pytest commcare_connect/audit/ -v
```
Expected: all existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add commcare_connect/audit/views.py
git commit -m "feat: export audit session to S3 on create and complete"
```

---

## Chunk 3: Downloads Page

### Task 5: Views and URL config

**Files:**
- Create: `commcare_connect/custom_analysis/exports/__init__.py`
- Create: `commcare_connect/custom_analysis/exports/views.py`
- Create: `commcare_connect/custom_analysis/exports/urls.py`
- Modify: `config/urls.py`
- Create: `commcare_connect/custom_analysis/exports/tests/__init__.py`
- Create: `commcare_connect/custom_analysis/exports/tests/test_views.py`

#### Step 1: Write failing tests

- [ ] Create `commcare_connect/custom_analysis/exports/tests/test_views.py`:

```python
"""Tests for the exports download page."""
import time
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client, override_settings

from commcare_connect.users.models import User

LABS_SETTINGS = dict(
    IS_LABS_ENVIRONMENT=True,
    LOGIN_URL="/labs/login/",
)


@pytest.fixture
def dimagi_client(db):
    """Authenticated client with a @dimagi.com username."""
    user, _ = User.objects.update_or_create(
        username="reviewer@dimagi.com",
        defaults={"email": ""},
    )
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "test-token",
        "expires_at": time.time() + 3600,
    }
    session.save()
    return client


@pytest.fixture
def non_dimagi_client(db):
    """Authenticated client without @dimagi.com username."""
    user, _ = User.objects.update_or_create(
        username="regularuser",
        defaults={"email": ""},
    )
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    return client


@override_settings(**LABS_SETTINGS, LABS_EXPORTS_BUCKET="test-bucket")
@patch("commcare_connect.custom_analysis.exports.views.boto3")
def test_exports_page_lists_files(mock_boto3, dimagi_client):
    """Page renders with file metadata from S3 head_object."""
    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    mock_s3.head_object.return_value = {
        "ContentLength": 4096,
        "Metadata": {"row-count": "42", "last-updated": "2026-03-16T10:00:00+00:00"},
    }

    response = dimagi_client.get("/custom_analysis/exports/")

    assert response.status_code == 200
    assert b"workflow_runs.csv" in response.content
    assert b"audit_sessions.csv" in response.content
    assert b"42" in response.content  # row count visible


@override_settings(**LABS_SETTINGS, LABS_EXPORTS_BUCKET=None)
def test_exports_page_shows_unconfigured_state(dimagi_client):
    """When no bucket is configured, page renders the not-configured message."""
    response = dimagi_client.get("/custom_analysis/exports/")
    assert response.status_code == 200
    assert b"not configured" in response.content.lower()
    assert b"workflow_runs.csv" not in response.content


@override_settings(**LABS_SETTINGS)
def test_exports_page_requires_dimagi_user(non_dimagi_client):
    """Non-Dimagi users receive 403."""
    response = non_dimagi_client.get("/custom_analysis/exports/")
    assert response.status_code == 403


@override_settings(**LABS_SETTINGS)
def test_exports_page_requires_login(client):
    """Unauthenticated requests are redirected to login."""
    response = client.get("/custom_analysis/exports/")
    assert response.status_code == 302
    assert "/labs/login/" in response["Location"]


@override_settings(**LABS_SETTINGS, LABS_EXPORTS_BUCKET="test-bucket")
@patch("commcare_connect.custom_analysis.exports.views.boto3")
def test_download_redirects_to_presigned_url(mock_boto3, dimagi_client):
    """Download endpoint generates pre-signed URL and redirects."""
    from commcare_connect.labs.s3_export import WORKFLOW_RUNS_KEY

    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"

    response = dimagi_client.get(f"/custom_analysis/exports/download/?key={WORKFLOW_RUNS_KEY}")

    assert response.status_code == 302
    assert response["Location"] == "https://s3.example.com/presigned"
    mock_s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "test-bucket", "Key": WORKFLOW_RUNS_KEY},
        ExpiresIn=900,
    )


@override_settings(**LABS_SETTINGS, LABS_EXPORTS_BUCKET="test-bucket")
def test_download_rejects_unknown_key(dimagi_client):
    """Download with an arbitrary key returns 400."""
    response = dimagi_client.get("/custom_analysis/exports/download/?key=../../etc/passwd")
    assert response.status_code == 400
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest commcare_connect/custom_analysis/exports/tests/test_views.py -v
```
Expected: FAIL (URL not found / module not found)

- [ ] **Step 3: Create `commcare_connect/custom_analysis/exports/__init__.py`** (empty file)

- [ ] **Step 4: Create `commcare_connect/custom_analysis/exports/views.py`**

```python
"""Downloads page for S3-backed CSV exports."""
import logging

import boto3
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect
from django.views import View
from django.views.generic import TemplateView

from commcare_connect.custom_analysis.audit_of_audits.views import DimagiUserRequiredMixin
from commcare_connect.labs.s3_export import AUDIT_SESSIONS_KEY, WORKFLOW_RUNS_KEY

logger = logging.getLogger(__name__)

_ALLOWED_KEYS = frozenset([WORKFLOW_RUNS_KEY, AUDIT_SESSIONS_KEY])


class ExportsIndexView(LoginRequiredMixin, DimagiUserRequiredMixin, TemplateView):
    """Lists available S3 export files with metadata and download links.

    Dimagi-staff only. No overview tile — accessed via direct URL.
    """

    template_name = "custom_analysis/exports/index.html"

    def get_context_data(self, **kwargs):
        from django.conf import settings

        context = super().get_context_data(**kwargs)
        bucket = getattr(settings, "LABS_EXPORTS_BUCKET", None) or None
        files = []

        if bucket:
            s3 = boto3.client("s3")
            for key, label in [
                (WORKFLOW_RUNS_KEY, "workflow_runs.csv"),
                (AUDIT_SESSIONS_KEY, "audit_sessions.csv"),
            ]:
                try:
                    meta = s3.head_object(Bucket=bucket, Key=key)
                    custom = meta.get("Metadata", {})
                    size_kb = round(meta["ContentLength"] / 1024, 1)
                    files.append(
                        {
                            "key": key,
                            "label": label,
                            "last_updated": custom.get("last-updated", ""),
                            "size_kb": size_kb,
                            "row_count": custom.get("row-count", "—"),
                        }
                    )
                except Exception:
                    logger.warning("Could not read S3 metadata for %s", key, exc_info=True)

        context["files"] = files
        context["bucket_configured"] = bool(bucket)
        return context


class DownloadExportView(LoginRequiredMixin, DimagiUserRequiredMixin, View):
    """Generates a 15-minute pre-signed S3 URL and redirects the browser to it."""

    def get(self, request, *args, **kwargs):
        from django.conf import settings

        key = request.GET.get("key", "")
        if key not in _ALLOWED_KEYS:
            return HttpResponse("Invalid export key.", status=400)

        bucket = getattr(settings, "LABS_EXPORTS_BUCKET", None) or None
        if not bucket:
            return HttpResponse("Export storage is not configured.", status=404)

        s3 = boto3.client("s3")
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=900,
        )
        return HttpResponseRedirect(url)
```

- [ ] **Step 5: Create `commcare_connect/custom_analysis/exports/urls.py`**

```python
from django.urls import path

from . import views

app_name = "exports"

urlpatterns = [
    path("", views.ExportsIndexView.as_view(), name="index"),
    path("download/", views.DownloadExportView.as_view(), name="download"),
]
```

- [ ] **Step 6: Register in `config/urls.py`**

After the `audit_of_audits` entry (around line 44), add:

```python
    path(
        "custom_analysis/exports/",
        include("commcare_connect.custom_analysis.exports.urls", namespace="exports"),
    ),
```

- [ ] **Step 7: Create `commcare_connect/custom_analysis/exports/tests/__init__.py`** (empty file)

- [ ] **Step 8: Run tests — verify they pass**

```bash
pytest commcare_connect/custom_analysis/exports/tests/test_views.py -v
```
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add commcare_connect/custom_analysis/exports/ config/urls.py
git commit -m "feat: add exports download page with pre-signed S3 links"
```

---

### Task 6: Template

**Files:**
- Create: `commcare_connect/templates/custom_analysis/exports/index.html`

- [ ] **Step 1: Create the template**

```html
{% extends "base.html" %}

{% block title %}Data Exports{% endblock %}

{% block content %}
<div class="max-w-3xl mx-auto px-4 py-8">

  <div class="mb-6">
    <h1 class="text-2xl font-semibold text-gray-900">Data Exports</h1>
    <p class="text-sm text-gray-500 mt-1">
      CSV backups of workflow runs and audit sessions stored in S3.
      Download links expire after 15 minutes.
    </p>
  </div>

  {% if not bucket_configured %}
  <div class="rounded-lg bg-yellow-50 border border-yellow-200 p-4 text-sm text-yellow-800">
    <strong>Not configured.</strong>
    Set the <code>LABS_EXPORTS_BUCKET</code> environment variable to enable exports.
  </div>

  {% elif not files %}
  <div class="rounded-lg bg-gray-50 border border-gray-200 p-4 text-sm text-gray-600">
    No export files found yet. Files are created when workflow runs or audit sessions are saved.
  </div>

  {% else %}
  <div class="overflow-hidden rounded-lg border border-gray-200 shadow-sm">
    <table class="min-w-full divide-y divide-gray-200 text-sm">
      <thead class="bg-gray-50">
        <tr>
          <th class="px-4 py-3 text-left font-medium text-gray-600">File</th>
          <th class="px-4 py-3 text-right font-medium text-gray-600">Rows</th>
          <th class="px-4 py-3 text-right font-medium text-gray-600">Size</th>
          <th class="px-4 py-3 text-left font-medium text-gray-600">Last Updated</th>
          <th class="px-4 py-3"></th>
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100 bg-white">
        {% for f in files %}
        <tr>
          <td class="px-4 py-3 font-mono text-gray-800">{{ f.label }}</td>
          <td class="px-4 py-3 text-right text-gray-600">{{ f.row_count }}</td>
          <td class="px-4 py-3 text-right text-gray-500">{{ f.size_kb }} KB</td>
          <td class="px-4 py-3 text-gray-500">{{ f.last_updated }}</td>
          <td class="px-4 py-3 text-right">
            <a href="{% url 'exports:download' %}?key={{ f.key|urlencode }}"
               class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-md
                      bg-purple-600 text-white hover:bg-purple-700 transition-colors">
              <i class="fa-solid fa-download text-xs"></i>
              Download
            </a>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

</div>
{% endblock %}
```

- [ ] **Step 2: Verify template renders (smoke test)**

```bash
pytest commcare_connect/custom_analysis/exports/tests/test_views.py::test_exports_page_lists_files -v
```
Expected: PASS (template renders without error)

- [ ] **Step 3: Commit**

```bash
git add commcare_connect/templates/custom_analysis/exports/index.html
git commit -m "feat: add exports download page template"
```

---

## Final verification

- [ ] **Run the full test suite**

```bash
pytest commcare_connect/labs/tests/test_s3_export.py \
       commcare_connect/custom_analysis/exports/tests/test_views.py \
       commcare_connect/workflow/ \
       commcare_connect/audit/ \
       -v
```
Expected: all PASS

- [ ] **Smoke test the full flow locally (optional)**

Set `LABS_EXPORTS_BUCKET=my-real-bucket` in `.env`, run the dev server, create a workflow run, then visit `/custom_analysis/exports/` to verify the file appears and the download link works.

- [ ] **Final commit / open PR**

```bash
git push -u origin feat/audit-s3-export
```
Then open a PR from `feat/audit-s3-export` → `main`.

---

## AWS setup checklist (one-time, after merge)

- [ ] Create S3 bucket `labs-jj-exports` with versioning enabled
- [ ] Add IAM policy to ECS task role: `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` on `arn:aws:s3:::labs-jj-exports/*`
- [ ] Add `LABS_EXPORTS_BUCKET=labs-jj-exports` env var via the `aws-env-update` skill
- [ ] Deploy via `deploy-labs` skill
- [ ] Verify `/custom_analysis/exports/` shows both files after a test workflow run
