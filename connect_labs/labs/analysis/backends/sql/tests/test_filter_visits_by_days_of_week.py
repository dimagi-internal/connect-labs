"""Tests for SQLCacheManager's day-of-week audit filter."""
from datetime import date

import pytest

from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager


@pytest.fixture
def manager():
    return SQLCacheManager(opportunity_id=99, config=None)


@pytest.mark.django_db
class TestFilterVisitsByDaysOfWeek:
    def _seed(self, manager):
        # 2026-01-01 = Thursday, 2026-01-02 = Friday, 2026-01-03 = Saturday,
        # 2026-01-05 = Monday.
        manager.store_raw_visits(
            visit_dicts=[
                {"id": 1, "username": "alice", "status": "approved", "visit_date": "2026-01-01"},
                {"id": 2, "username": "alice", "status": "approved", "visit_date": "2026-01-02"},
                {"id": 3, "username": "bob", "status": "approved", "visit_date": "2026-01-03"},
                {"id": 4, "username": "bob", "status": "approved", "visit_date": "2026-01-05"},
            ],
            visit_count=4,
        )

    def test_filter_by_single_weekday(self, manager):
        self._seed(manager)
        ids = manager.get_filtered_visit_ids(days_of_week=[5])  # Friday
        assert ids == ["2"]

    def test_filter_by_multiple_weekdays(self, manager):
        self._seed(manager)
        ids = manager.get_filtered_visit_ids(days_of_week=[1, 6])  # Monday + Saturday
        assert sorted(ids) == ["3", "4"]

    def test_no_weekday_filter_returns_all(self, manager):
        self._seed(manager)
        ids = manager.get_filtered_visit_ids()
        assert sorted(ids) == ["1", "2", "3", "4"]

    def test_combines_with_date_range(self, manager):
        self._seed(manager)
        ids = manager.get_filtered_visit_ids(start_date=date(2026, 1, 1), end_date=date(2026, 1, 3), days_of_week=[5])
        assert ids == ["2"]

    def test_get_filtered_visits_slim_respects_weekday_filter(self, manager):
        self._seed(manager)
        visits = manager.get_filtered_visits_slim(days_of_week=[1])  # Monday
        assert len(visits) == 1
        assert visits[0]["id"] == "4"
