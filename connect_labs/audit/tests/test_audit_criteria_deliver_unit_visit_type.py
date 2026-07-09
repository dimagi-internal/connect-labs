"""Tests for the Deliver Unit Type / Visit Type audit creation filters."""
from connect_labs.audit.data_access import AuditCriteria, filter_visits_for_audit


def test_from_dict_parses_snake_case():
    criteria = AuditCriteria.from_dict(
        {
            "deliver_unit_types": ["CHW Home Visit", "Malnutrition Screening"],
            "visit_statuses": ["approved", "rejected"],
        }
    )
    assert criteria.deliver_unit_types == ["CHW Home Visit", "Malnutrition Screening"]
    assert criteria.visit_statuses == ["approved", "rejected"]


def test_from_dict_parses_camel_case():
    criteria = AuditCriteria.from_dict({"deliverUnitTypes": ["CHW Home Visit"], "visitStatuses": ["pending"]})
    assert criteria.deliver_unit_types == ["CHW Home Visit"]
    assert criteria.visit_statuses == ["pending"]


def test_from_dict_defaults_to_none_when_empty():
    criteria = AuditCriteria.from_dict({})
    assert criteria.deliver_unit_types is None
    assert criteria.visit_statuses is None


def test_pandas_filter_by_deliver_unit_type():
    visits = [
        {
            "id": 1,
            "form_json": {"form": {"@name": "CHW Home Visit"}},
            "status": "approved",
            "visit_date": "2026-01-01",
        },
        {
            "id": 2,
            "form_json": {"form": {"@name": "Malnutrition Screening"}},
            "status": "approved",
            "visit_date": "2026-01-01",
        },
    ]
    criteria = AuditCriteria(audit_type="date_range", deliver_unit_types=["CHW Home Visit"])
    result = filter_visits_for_audit(visits, criteria)
    assert result == [1]


def test_pandas_filter_by_visit_status():
    visits = [
        {"id": 1, "deliver_unit_id": 10, "status": "approved", "visit_date": "2026-01-01"},
        {"id": 2, "deliver_unit_id": 10, "status": "rejected", "visit_date": "2026-01-01"},
        {"id": 3, "deliver_unit_id": 10, "status": "pending", "visit_date": "2026-01-01"},
    ]
    criteria = AuditCriteria(audit_type="date_range", visit_statuses=["approved", "pending"])
    result = filter_visits_for_audit(visits, criteria)
    assert sorted(result) == [1, 3]


def test_pandas_filter_combines_deliver_unit_type_and_status():
    visits = [
        {
            "id": 1,
            "form_json": {"form": {"@name": "CHW Home Visit"}},
            "status": "approved",
            "visit_date": "2026-01-01",
        },
        {
            "id": 2,
            "form_json": {"form": {"@name": "CHW Home Visit"}},
            "status": "rejected",
            "visit_date": "2026-01-01",
        },
        {
            "id": 3,
            "form_json": {"form": {"@name": "Malnutrition Screening"}},
            "status": "approved",
            "visit_date": "2026-01-01",
        },
    ]
    criteria = AuditCriteria(
        audit_type="date_range", deliver_unit_types=["CHW Home Visit"], visit_statuses=["approved"]
    )
    result = filter_visits_for_audit(visits, criteria)
    assert result == [1]


def test_pandas_filter_by_deliver_unit_type_missing_form_json_excluded():
    """A visit with no form_json (or no form.@name) never matches a deliver-unit-type filter."""
    visits = [
        {
            "id": 1,
            "form_json": {"form": {"@name": "CHW Home Visit"}},
            "status": "approved",
            "visit_date": "2026-01-01",
        },
        {"id": 2, "form_json": {}, "status": "approved", "visit_date": "2026-01-01"},
        {"id": 3, "status": "approved", "visit_date": "2026-01-01"},
    ]
    criteria = AuditCriteria(audit_type="date_range", deliver_unit_types=["CHW Home Visit"])
    result = filter_visits_for_audit(visits, criteria)
    assert result == [1]


def test_no_filter_returns_all_visits():
    visits = [
        {"id": 1, "deliver_unit_id": 10, "status": "approved", "visit_date": "2026-01-01"},
        {"id": 2, "deliver_unit_id": 20, "status": "rejected", "visit_date": "2026-01-01"},
    ]
    criteria = AuditCriteria(audit_type="date_range")
    result = filter_visits_for_audit(visits, criteria)
    assert sorted(result) == [1, 2]
