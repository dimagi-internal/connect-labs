"""Choke-point instrumentation tests.

Uses the labs-only (local backend) dispatch path so no HTTP mocking is
needed — the audit decorator wraps both paths identically.
"""
import pytest

from connect_labs.audit_trail.models import Action, AuditEvent, Outcome
from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.labs.synthetic.models import SyntheticOpportunity

LABS_ONLY_OPP = 10_900


@pytest.fixture
def labs_only_client(db):
    SyntheticOpportunity.objects.create(
        opportunity_id=LABS_ONLY_OPP,
        label="audit-test-opp",
        labs_only=True,
    )
    return LabsRecordAPIClient(access_token="unused", opportunity_id=LABS_ONLY_OPP)


@pytest.mark.django_db
def test_create_and_list_are_audited(labs_only_client):
    record = labs_only_client.create_record(experiment="exp", type="task", data={"x": 1})
    events = list(AuditEvent.objects.order_by("id"))
    assert [e.action for e in events] == [Action.CREATE]
    create_event = events[0]
    assert create_event.resource_type == "task"
    assert create_event.resource_id == str(record.id)
    assert create_event.labs_only is True
    assert create_event.opportunity_id == LABS_ONLY_OPP
    assert create_event.metadata["experiment"] == "exp"

    results = labs_only_client.get_records(experiment="exp", type="task")
    list_event = AuditEvent.objects.order_by("id").last()
    assert list_event.action == Action.LIST
    assert list_event.record_count == len(results) == 1


@pytest.mark.django_db
def test_read_update_delete_are_audited(labs_only_client):
    record = labs_only_client.create_record(experiment="exp", type="task", data={"x": 1})

    labs_only_client.get_record_by_id(record.id, experiment="exp", type="task")
    labs_only_client.update_record(record.id, experiment="exp", type="task", data={"x": 2})
    labs_only_client.delete_records([record.id])

    # update_record internally re-fetches the record, so a nested READ may
    # also be logged — assert the essential actions are all present.
    actions = list(AuditEvent.objects.order_by("id").values_list("action", flat=True))
    assert Action.CREATE in actions
    assert Action.READ in actions
    assert Action.UPDATE in actions
    assert Action.DELETE in actions
    delete_event = AuditEvent.objects.filter(action=Action.DELETE).get()
    assert delete_event.record_count == 1
    assert delete_event.metadata["record_ids"] == [record.id]


@pytest.mark.django_db
def test_failure_outcome_recorded(labs_only_client):
    with pytest.raises(Exception):
        labs_only_client.update_record(999_999, experiment="exp", type="task", data={})
    failure = AuditEvent.objects.filter(outcome=Outcome.FAILURE).first()
    assert failure is not None
    assert failure.action == Action.UPDATE
    assert "error" in failure.metadata


@pytest.mark.django_db
def test_export_client_paginate_audited(monkeypatch, db):
    from connect_labs.labs.integrations.connect.export_client import ExportAPIClient

    client = ExportAPIClient(base_url="https://example.invalid", access_token="t")

    def fake_paginate(endpoint, params=None):
        yield [{"id": 1}, {"id": 2}]
        yield [{"id": 3}]

    monkeypatch.setattr(client, "_paginate", fake_paginate)
    rows = client.fetch_all("/export/opportunity/765/user_visits/")
    assert len(rows) == 3

    event = AuditEvent.objects.get()
    assert event.action == Action.EXPORT
    assert event.resource_type == "user_visits"
    assert event.record_count == 3
    assert event.opportunity_id == 765


@pytest.mark.django_db
def test_export_client_error_still_audited(monkeypatch, db):
    from connect_labs.labs.integrations.connect.export_client import ExportAPIClient, ExportAPIError

    client = ExportAPIClient(base_url="https://example.invalid", access_token="t")

    def fake_paginate(endpoint, params=None):
        yield [{"id": 1}]
        raise ExportAPIError("boom")

    monkeypatch.setattr(client, "_paginate", fake_paginate)
    with pytest.raises(ExportAPIError):
        client.fetch_all("/export/opportunity/765/user_visits/")

    event = AuditEvent.objects.get()
    assert event.outcome == Outcome.FAILURE
    assert event.record_count == 1
    assert event.metadata["error"] == "ExportAPIError"
