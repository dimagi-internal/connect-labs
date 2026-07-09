"""Tests for SQLCacheManager's deliver-unit-type/visit-status audit filters."""
import pytest

from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager


@pytest.fixture
def manager():
    return SQLCacheManager(opportunity_id=99, config=None)


@pytest.mark.django_db
class TestFilterVisitsDeliverUnitTypeAndStatus:
    def _seed(self, manager):
        manager.store_raw_visits(
            visit_dicts=[
                {
                    "id": 1,
                    "username": "alice",
                    "status": "approved",
                    "form_json": {"form": {"@name": "CHW Home Visit"}},
                },
                {
                    "id": 2,
                    "username": "alice",
                    "status": "rejected",
                    "form_json": {"form": {"@name": "Malnutrition Screening"}},
                },
                {
                    "id": 3,
                    "username": "bob",
                    "status": "pending",
                    "form_json": {"form": {"@name": "CHW Home Visit"}},
                },
            ],
            visit_count=3,
        )

    def test_filter_by_deliver_unit_types(self, manager):
        self._seed(manager)
        ids = manager.get_filtered_visit_ids(deliver_unit_types=["CHW Home Visit"])
        assert sorted(ids) == ["1", "3"]

    def test_filter_by_visit_statuses(self, manager):
        self._seed(manager)
        ids = manager.get_filtered_visit_ids(visit_statuses=["approved", "pending"])
        assert sorted(ids) == ["1", "3"]

    def test_filter_combines_deliver_unit_type_and_status(self, manager):
        self._seed(manager)
        ids = manager.get_filtered_visit_ids(deliver_unit_types=["CHW Home Visit"], visit_statuses=["approved"])
        assert ids == ["1"]

    def test_no_filter_returns_all(self, manager):
        self._seed(manager)
        ids = manager.get_filtered_visit_ids()
        assert sorted(ids) == ["1", "2", "3"]

    def test_get_filtered_visits_slim_respects_filters(self, manager):
        self._seed(manager)
        visits = manager.get_filtered_visits_slim(deliver_unit_types=["Malnutrition Screening"])
        assert len(visits) == 1
        assert visits[0]["id"] == "2"

    def test_get_distinct_deliver_unit_types(self, manager):
        self._seed(manager)
        types = manager.get_distinct_deliver_unit_types()
        assert types == ["CHW Home Visit", "Malnutrition Screening"]

    def test_get_distinct_deliver_unit_types_excludes_missing_form_name(self, manager):
        manager.store_raw_visits(
            visit_dicts=[
                {"id": 1, "username": "alice", "status": "approved", "form_json": {}},
                {"id": 2, "username": "bob", "status": "approved", "form_json": None},
            ],
            visit_count=2,
        )
        assert manager.get_distinct_deliver_unit_types() == []
