"""Tests for the day-of-week audit creation filter (AuditCriteria.days_of_week)."""
from connect_labs.audit.data_access import AuditCriteria, filter_visits_for_audit


def test_from_dict_parses_snake_case():
    criteria = AuditCriteria.from_dict({"days_of_week": [1, 5]})
    assert criteria.days_of_week == [1, 5]


def test_from_dict_parses_camel_case():
    criteria = AuditCriteria.from_dict({"daysOfWeek": [5]})
    assert criteria.days_of_week == [5]


def test_from_dict_defaults_to_none_when_empty():
    criteria = AuditCriteria.from_dict({})
    assert criteria.days_of_week is None


def test_from_dict_defaults_to_none_when_all_days_selected_as_empty_list():
    """An empty list ("All" selected, no restriction) parses to None, not []."""
    criteria = AuditCriteria.from_dict({"days_of_week": []})
    assert criteria.days_of_week is None


def test_pandas_filter_by_single_weekday():
    # 2026-01-01 = Thursday, 2026-01-02 = Friday, 2026-01-03 = Saturday.
    visits = [
        {"id": 1, "visit_date": "2026-01-01"},
        {"id": 2, "visit_date": "2026-01-02"},
        {"id": 3, "visit_date": "2026-01-03"},
    ]
    criteria = AuditCriteria(audit_type="date_range", days_of_week=[5])  # Friday
    result = filter_visits_for_audit(visits, criteria)
    assert result == [2]


def test_pandas_filter_by_multiple_weekdays():
    visits = [
        {"id": 1, "visit_date": "2026-01-01"},  # Thursday
        {"id": 2, "visit_date": "2026-01-02"},  # Friday
        {"id": 3, "visit_date": "2026-01-05"},  # Monday
    ]
    criteria = AuditCriteria(audit_type="date_range", days_of_week=[1, 5])  # Monday + Friday
    result = filter_visits_for_audit(visits, criteria)
    assert sorted(result) == [2, 3]


def test_pandas_no_weekday_filter_returns_all_days():
    visits = [
        {"id": 1, "visit_date": "2026-01-01"},
        {"id": 2, "visit_date": "2026-01-02"},
    ]
    criteria = AuditCriteria(audit_type="date_range", days_of_week=None)
    result = filter_visits_for_audit(visits, criteria)
    assert sorted(result) == [1, 2]


def test_pandas_weekday_filter_ignored_for_non_date_range_audit_type():
    """days_of_week only applies to audit_type == 'date_range'."""
    visits = [
        {"id": 1, "visit_date": "2026-01-01"},  # Thursday
        {"id": 2, "visit_date": "2026-01-02"},  # Friday
    ]
    criteria = AuditCriteria(audit_type="last_n_per_flw", days_of_week=[5])
    result = filter_visits_for_audit(visits, criteria)
    assert sorted(result) == [1, 2]
