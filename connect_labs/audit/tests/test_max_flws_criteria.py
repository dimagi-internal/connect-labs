"""Tests for the max_flws audit-creation filter (AuditCriteria.max_flws).

Caps a run to the first N field workers found in the window, resolved into a
concrete username list by run_audit_creation's Stage 1 (visit fetch) before
extraction/AI review run -- see connect_labs.audit.tasks.run_audit_creation.
"""
from unittest.mock import MagicMock

from connect_labs.audit import tasks
from connect_labs.audit.data_access import AuditCriteria
from connect_labs.audit.models import AuditSessionRecord


def test_from_dict_parses_snake_case():
    criteria = AuditCriteria.from_dict({"max_flws": 3})
    assert criteria.max_flws == 3


def test_from_dict_parses_camel_case():
    criteria = AuditCriteria.from_dict({"maxFlws": 2})
    assert criteria.max_flws == 2


def test_from_dict_defaults_to_none_when_absent():
    criteria = AuditCriteria.from_dict({})
    assert criteria.max_flws is None


def test_max_flws_caps_extraction_to_first_n_flws_sorted(monkeypatch):
    """flwA, flwB, flwC visits are all fetched (cheap, SQL-only), but only the
    first 2 usernames sorted alphabetically (flwA, flwB) make it into
    extraction -- flwC's visit must never reach extract_images_for_visits."""
    visits = [
        {"id": 101, "username": "flwB"},
        {"id": 102, "username": "flwA"},
        {"id": 103, "username": "flwC"},
    ]
    instances = []

    def _make(**_kwargs):
        da = MagicMock()
        da.get_visit_ids_for_audit.return_value = ([101, 102, 103], visits)
        da.extract_images_for_visits.return_value = {}
        da.get_flw_names.return_value = {}
        da.create_audit_session.side_effect = lambda **kw: AuditSessionRecord(
            {
                "id": 1,
                "experiment": "audit",
                "type": "AuditSession",
                "data": {"title": kw["title"], "tag": kw["tag"]},
                "opportunity_id": kw.get("opportunity_id"),
            }
        )
        instances.append(da)
        return da

    monkeypatch.setattr(tasks, "AuditDataAccess", MagicMock(side_effect=_make))

    tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": 1973, "name": "EHA"}],
            "criteria": {
                "audit_type": "date_range",
                "start_date": "2026-06-22",
                "end_date": "2026-06-28",
                "sample_percentage": 100,
                "granularity": "per_flw",
                "tag": "muac",
                "max_flws": 2,
            },
        }
    )

    da = instances[0]
    da.get_visit_ids_for_audit.assert_called_once()
    _, fetch_kwargs = da.get_visit_ids_for_audit.call_args
    assert fetch_kwargs["return_visits"] is True

    extraction_args, _ = da.extract_images_for_visits.call_args
    assert set(extraction_args[0]) == {101, 102}  # flwB (101) + flwA (102), not flwC (103)


def test_no_max_flws_skips_return_visits_fetch(monkeypatch):
    """Without max_flws, Stage 1 uses the plain (flat visit_ids) call shape --
    no need to pay for return_visits=True when there's nothing to cap."""
    instances = []

    def _make(**_kwargs):
        da = MagicMock()
        da.get_visit_ids_for_audit.return_value = [101, 102]
        da.extract_images_for_visits.return_value = {}
        da.get_flw_names.return_value = {}
        da.create_audit_session.side_effect = lambda **kw: AuditSessionRecord(
            {
                "id": 1,
                "experiment": "audit",
                "type": "AuditSession",
                "data": {"title": kw["title"], "tag": kw["tag"]},
                "opportunity_id": kw.get("opportunity_id"),
            }
        )
        instances.append(da)
        return da

    monkeypatch.setattr(tasks, "AuditDataAccess", MagicMock(side_effect=_make))

    tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": 1973, "name": "EHA"}],
            "criteria": {
                "audit_type": "date_range",
                "start_date": "2026-06-22",
                "end_date": "2026-06-28",
                "sample_percentage": 100,
                "granularity": "per_flw",
                "tag": "muac",
            },
        }
    )

    da = instances[0]
    _, fetch_kwargs = da.get_visit_ids_for_audit.call_args
    assert "return_visits" not in fetch_kwargs
