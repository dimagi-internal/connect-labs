import gzip
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from django.utils import timezone

from connect_labs.audit_trail.models import Action, AuditEvent
from connect_labs.audit_trail.tasks import archive_audit_events, emit_canary_event, prune_archived_events


def _backdate(event, days):
    """Move an event's occurred_at into the past, bypassing the append-only trigger."""
    import pgtrigger

    with pgtrigger.ignore("audit_trail.AuditEvent:append_only"):
        AuditEvent.objects.filter(id=event.id).update(occurred_at=timezone.now() - timedelta(days=days))


@pytest.mark.django_db
def test_archive_writes_jsonl_and_digest(settings):
    settings.AUDIT_TRAIL_ARCHIVE_BUCKET = "test-audit-bucket"
    event = AuditEvent.objects.create(action=Action.READ, resource_type="thing", opportunity_id=1)
    _backdate(event, 1)

    s3 = MagicMock()
    with patch("connect_labs.audit_trail.tasks._get_s3_client", return_value=s3):
        result = archive_audit_events()

    assert result["archived"] == 1
    data_call, digest_call = s3.put_object.call_args_list
    assert data_call.kwargs["Key"].startswith("audit-events/") and data_call.kwargs["Key"].endswith(".jsonl.gz")
    lines = gzip.decompress(data_call.kwargs["Body"]).decode().strip().split("\n")
    assert json.loads(lines[0])["action"] == "read"
    assert result["sha256"] in digest_call.kwargs["Body"].decode()


@pytest.mark.django_db
def test_archive_noop_without_bucket(settings):
    settings.AUDIT_TRAIL_ARCHIVE_BUCKET = None
    assert archive_audit_events()["skipped"] is True


@pytest.mark.django_db
def test_canary_writes_event(settings):
    emit_canary_event()
    assert AuditEvent.objects.filter(action=Action.CANARY).count() == 1


@pytest.mark.django_db
def test_prune_requires_verified_archive(settings):
    settings.AUDIT_TRAIL_ARCHIVE_BUCKET = "test-audit-bucket"
    settings.AUDIT_TRAIL_HOT_RETENTION_DAYS = 10
    old_event = AuditEvent.objects.create(action=Action.READ, resource_type="thing")
    _backdate(old_event, 30)
    AuditEvent.objects.create(action=Action.READ, resource_type="recent")

    # No archive object in S3 → nothing pruned even with dry_run=False
    s3 = MagicMock()
    s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    with patch("connect_labs.audit_trail.tasks._get_s3_client", return_value=s3):
        result = prune_archived_events(dry_run=False)
    assert result["pruned"] == 0
    assert AuditEvent.objects.count() == 2

    # Archive present → old day pruned, recent row untouched
    s3 = MagicMock()
    with patch("connect_labs.audit_trail.tasks._get_s3_client", return_value=s3):
        result = prune_archived_events(dry_run=False)
    assert result["pruned"] == 1
    assert AuditEvent.objects.count() == 1
