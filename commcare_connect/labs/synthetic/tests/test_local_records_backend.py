"""Tests for the labs-local LabsRecord storage backend + API client dispatch."""
from __future__ import annotations

import pytest

from commcare_connect.labs.integrations.connect.api_client import LabsAPIError, LabsRecordAPIClient
from commcare_connect.labs.synthetic import local_records_backend as backend
from commcare_connect.labs.synthetic.models import LabsLocalRecord, SyntheticOpportunity


@pytest.fixture
def labs_only_opp(db):
    return SyntheticOpportunity.objects.create(
        opportunity_id=10_000,
        label="Test labs-only",
        gdrive_folder_id="folder-test",
        labs_only=True,
        allowed_domains=["@dimagi.com"],
    )


@pytest.fixture
def real_opp(db):
    """Registered synthetic, but NOT labs_only — dispatch should hit prod (we mock)."""
    return SyntheticOpportunity.objects.create(
        opportunity_id=814,
        gdrive_folder_id="folder-814",
        labs_only=False,
    )


# ─── is_labs_only_opportunity_id ───────────────────────────────────────────


@pytest.mark.django_db
def test_is_labs_only_returns_false_for_none():
    assert backend.is_labs_only_opportunity_id(None) is False


@pytest.mark.django_db
def test_is_labs_only_fast_path_for_low_opp_ids():
    """opp_ids below the reserved floor (10_000) skip the DB hit entirely."""
    assert backend.is_labs_only_opportunity_id(5_000) is False
    assert backend.is_labs_only_opportunity_id(9_999) is False


@pytest.mark.django_db
def test_is_labs_only_true_for_registered_labs_only(labs_only_opp):
    assert backend.is_labs_only_opportunity_id(10_000) is True


@pytest.mark.django_db
def test_is_labs_only_false_for_high_unregistered_id():
    """Above the floor but not in the table → still routes to prod (real opp)."""
    assert backend.is_labs_only_opportunity_id(99_999) is False


@pytest.mark.django_db
def test_is_labs_only_false_for_disabled_labs_only(db):
    """Disabled labs-only opps still dispatch local — `enabled` controls serving, not ownership."""
    SyntheticOpportunity.objects.create(opportunity_id=10_001, gdrive_folder_id="f", labs_only=True, enabled=False)
    assert backend.is_labs_only_opportunity_id(10_001) is True


# ─── is_labs_only_program_id ───────────────────────────────────────────────


@pytest.mark.django_db
def test_is_labs_only_program_id_returns_false_for_none():
    assert backend.is_labs_only_program_id(None) is False


@pytest.mark.django_db
def test_is_labs_only_program_id_fast_path_for_low_ids():
    """Program ids below the reserved floor are real Connect programs — skip the DB."""
    assert backend.is_labs_only_program_id(25) is False
    assert backend.is_labs_only_program_id(9_999) is False


@pytest.mark.django_db
def test_is_labs_only_program_id_true_for_explicit_program(db):
    """An opp filed under an explicit labs-only program id matches that program."""
    SyntheticOpportunity.objects.create(opportunity_id=10_009, program_id=10_008, gdrive_folder_id="f", labs_only=True)
    assert backend.is_labs_only_program_id(10_008) is True


@pytest.mark.django_db
def test_is_labs_only_program_id_true_for_implicit_program(db):
    """When program_id is unset, the opp is its own program (program_id == opp id)."""
    SyntheticOpportunity.objects.create(opportunity_id=10_005, gdrive_folder_id="f", labs_only=True)
    assert backend.is_labs_only_program_id(10_005) is True


@pytest.mark.django_db
def test_is_labs_only_program_id_false_for_unregistered(db):
    assert backend.is_labs_only_program_id(99_999) is False


# ─── direct backend CRUD ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_backend_create_and_get(labs_only_opp):
    rec = backend.create_record(
        opportunity_id=10_000,
        experiment="test",
        type="my_type",
        data={"key": "value"},
        username="alice",
    )
    assert rec.experiment == "test"
    assert rec.opportunity_id == 10_000
    assert rec.data == {"key": "value"}

    fetched = backend.get_record_by_id(record_id=rec.id, opportunity_id=10_000)
    assert fetched is not None
    assert fetched.id == rec.id
    assert fetched.data == {"key": "value"}


@pytest.mark.django_db
def test_backend_get_records_filters(labs_only_opp):
    backend.create_record(opportunity_id=10_000, experiment="exp_a", type="t1", data={})
    backend.create_record(opportunity_id=10_000, experiment="exp_a", type="t2", data={})
    backend.create_record(opportunity_id=10_000, experiment="exp_b", type="t1", data={})

    assert len(backend.get_records(opportunity_id=10_000)) == 3
    assert len(backend.get_records(opportunity_id=10_000, experiment="exp_a")) == 2
    assert len(backend.get_records(opportunity_id=10_000, experiment="exp_a", type="t1")) == 1


@pytest.mark.django_db
def test_backend_update_record(labs_only_opp):
    rec = backend.create_record(opportunity_id=10_000, experiment="e", type="t", data={"v": 1})
    updated = backend.update_record(
        record_id=rec.id,
        opportunity_id=10_000,
        experiment="e",
        type="t",
        data={"v": 2},
    )
    assert updated.data == {"v": 2}
    row = LabsLocalRecord.objects.get(id=rec.id)
    assert row.data == {"v": 2}


@pytest.mark.django_db
def test_backend_update_missing_raises(labs_only_opp):
    with pytest.raises(LabsAPIError):
        backend.update_record(
            record_id=999999,
            opportunity_id=10_000,
            experiment="e",
            type="t",
            data={},
        )


@pytest.mark.django_db
def test_backend_delete_records(labs_only_opp):
    rec_a = backend.create_record(opportunity_id=10_000, experiment="e", type="t", data={})
    rec_b = backend.create_record(opportunity_id=10_000, experiment="e", type="t", data={})
    backend.delete_records(record_ids=[rec_a.id, rec_b.id])
    assert LabsLocalRecord.objects.count() == 0


# ─── LabsRecordAPIClient dispatch ──────────────────────────────────────────


@pytest.mark.django_db
def test_client_routes_labs_only_create_to_local(labs_only_opp):
    """create_record on a labs-only opp uses local backend (no HTTP)."""
    client = LabsRecordAPIClient(access_token="dummy", opportunity_id=10_000)
    try:
        rec = client.create_record(experiment="wf", type="workflow_definition", data={"x": 1})
    finally:
        client.close()
    assert rec.opportunity_id == 10_000
    assert LabsLocalRecord.objects.filter(opportunity_id=10_000, experiment="wf").count() == 1


@pytest.mark.django_db
def test_client_routes_labs_only_get_records_to_local(labs_only_opp):
    LabsLocalRecord.objects.create(opportunity_id=10_000, experiment="wf", type="t", data={"v": 1})
    client = LabsRecordAPIClient(access_token="dummy", opportunity_id=10_000)
    try:
        records = client.get_records(experiment="wf")
    finally:
        client.close()
    assert len(records) == 1
    assert records[0].data == {"v": 1}


@pytest.mark.django_db
def test_client_routes_labs_only_update_to_local(labs_only_opp):
    row = LabsLocalRecord.objects.create(opportunity_id=10_000, experiment="wf", type="t", data={"v": 1})
    client = LabsRecordAPIClient(access_token="dummy", opportunity_id=10_000)
    try:
        updated = client.update_record(record_id=row.id, experiment="wf", type="t", data={"v": 2})
    finally:
        client.close()
    assert updated.data == {"v": 2}
    row.refresh_from_db()
    assert row.data == {"v": 2}


@pytest.mark.django_db
def test_client_routes_labs_only_delete_to_local(labs_only_opp):
    row = LabsLocalRecord.objects.create(opportunity_id=10_000, experiment="wf", type="t", data={})
    client = LabsRecordAPIClient(access_token="dummy", opportunity_id=10_000)
    try:
        client.delete_record(row.id)
    finally:
        client.close()
    assert LabsLocalRecord.objects.filter(id=row.id).count() == 0


@pytest.mark.django_db
def test_backend_get_records_program_scoped_without_opp(db):
    """Program-scoped read (no opportunity_id) filters by program_id across opps."""
    backend.create_record(opportunity_id=10_009, program_id=10_008, experiment="workflow", type="t", data={})
    backend.create_record(opportunity_id=10_010, program_id=10_008, experiment="workflow", type="t", data={})
    backend.create_record(opportunity_id=10_011, program_id=10_007, experiment="workflow", type="t", data={})

    records = backend.get_records(program_id=10_008, experiment="workflow")
    assert len(records) == 2
    assert {r.opportunity_id for r in records} == {10_009, 10_010}


@pytest.mark.django_db
def test_client_routes_labs_only_program_scoped_get_to_local(monkeypatch):
    """Regression: a program-scoped client for a labs-only program (no opp selected)
    must dispatch to the local backend, not the production HTTP API.

    Mirrors loading the Workflows page with only a synthetic program selected
    (?program_id=10008) — previously this 404'd against connect.dimagi.com.
    """
    SyntheticOpportunity.objects.create(opportunity_id=10_009, program_id=10_008, gdrive_folder_id="f", labs_only=True)
    LabsLocalRecord.objects.create(
        opportunity_id=10_009, program_id=10_008, experiment="workflow", type="workflow_definition", data={"v": 1}
    )
    client = LabsRecordAPIClient(access_token="dummy", program_id=10_008)

    def _no_http(*args, **kwargs):
        raise AssertionError("program-scoped labs-only read must not hit the production HTTP API")

    monkeypatch.setattr(client.http_client, "get", _no_http)
    try:
        records = client.get_records(experiment="workflow", type="workflow_definition")
    finally:
        client.close()
    assert len(records) == 1
    assert records[0].data == {"v": 1}


@pytest.mark.django_db
def test_client_does_not_route_real_opps_to_local(real_opp, monkeypatch):
    """Real opps should still hit production HTTP, not local backend.

    Verified by mocking out the http client get and asserting it was called.
    """
    client = LabsRecordAPIClient(access_token="dummy", opportunity_id=814)
    called = {"hit": False}

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    def _fake_get(*args, **kwargs):
        called["hit"] = True
        return _FakeResp()

    monkeypatch.setattr(client.http_client, "get", _fake_get)
    try:
        client.get_records(experiment="anything")
    finally:
        client.close()
    assert called["hit"] is True
