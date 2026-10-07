"""A pipeline `filters` entry naming a custom (computed) field filters the cached visit rows.

A list value means "any of these", as it does for `status`. It used to be matched as a
whole-value JSONB containment, `{"ward": ["Dabaza"]}` against a stored `"Dabaza"`, which
never matches -- the read returned zero rows while the scalar form returned the data.

Runs real Postgres. The cache is built the way `AnalysisPipeline` builds it for a filtered
config: from the UNFILTERED config, then read back through the filtered one.
"""

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager, filter_computed_visits
from connect_labs.labs.analysis.backends.sql.models import ComputedVisitCache, RawVisitCache
from connect_labs.workflow.pipeline_query import cached_queryset

# (ward, status) per visit; ids v0..v5
VISITS = [
    ("Dabaza", "approved"),
    ("Dabaza", "rejected"),
    ("Dabi", "approved"),
    ("Dabi", "approved"),
    ("Gwiwa", "approved"),
    ("Gwiwa", "rejected"),
]


def _config(opp_id, filters=None):
    from connect_labs.workflow.data_access import PipelineDataAccess

    schema = {
        "terminal_stage": "visit_level",
        "data_source": {"type": "connect_export", "endpoint": "user_visits"},
        "fields": [{"name": "ward", "path": "form.ward", "aggregation": "first"}],
        "filters": filters or {},
    }
    access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
    return access._schema_to_config(schema, definition_id=opp_id)


@pytest.fixture
def built(db):
    """Raw visits seeded and the computed cache built unfiltered, as the pipeline does."""
    opp = 41001
    for i, (ward, status) in enumerate(VISITS):
        RawVisitCache.objects.create(
            opportunity_id=opp,
            pipeline_id=opp,
            visit_count=len(VISITS),
            expires_at=timezone.now() + timezone.timedelta(days=1),
            visit_id=f"v{i}",
            username=f"u{i}",
            visit_date="2026-08-01",
            status=status,
            form_json={"form": {"ward": ward}},
        )
    result = SQLBackend().process_and_cache(
        None, _config(opp), opp, None, skip_raw_store=True, visit_count=len(VISITS)
    )
    assert len(result.rows) == len(VISITS)
    return opp


def _read_ids(opp, filters):
    result = SQLBackend().get_cached_visit_result(opp, _config(opp, filters), len(VISITS))
    assert result is not None, "cache should be valid"
    return sorted(r.id for r in result.rows)


def test_scalar_value_still_matches(built):
    assert _read_ids(built, {"ward": "Dabaza"}) == ["v0", "v1"]


def test_single_element_list_matches_like_the_scalar(built):
    assert _read_ids(built, {"ward": ["Dabaza"]}) == ["v0", "v1"]


def test_multi_element_list_is_any_of(built):
    assert _read_ids(built, {"ward": ["Dabaza", "Dabi"]}) == ["v0", "v1", "v2", "v3"]


def test_list_with_an_absent_value_matches_only_the_present_ones(built):
    assert _read_ids(built, {"ward": ["Dabi", "Nowhere"]}) == ["v2", "v3"]


def test_list_matching_nothing_returns_nothing(built):
    assert _read_ids(built, {"ward": ["Nowhere"]}) == []


def test_no_filter_returns_everything(built):
    assert len(_read_ids(built, {})) == len(VISITS)


def test_list_on_custom_field_combines_with_status_list(built):
    assert _read_ids(built, {"ward": ["Dabaza", "Dabi"], "status": ["approved"]}) == ["v0", "v2", "v3"]


def test_status_list_and_scalar_are_unchanged(built):
    assert _read_ids(built, {"status": ["rejected"]}) == ["v1", "v5"]
    assert _read_ids(built, {"status": "rejected"}) == ["v1", "v5"]


def test_the_workflow_read_path_agrees(built):
    """`pipeline_query.cached_queryset` reads the same rows with the same filters."""
    rows = cached_queryset(_config(built, {"ward": ["Dabaza", "Dabi"]}), built)
    assert sorted(r.visit_id for r in rows) == ["v0", "v1", "v2", "v3"]
    rows = cached_queryset(_config(built, {"ward": "Gwiwa"}), built)
    assert sorted(r.visit_id for r in rows) == ["v4", "v5"]


class TestArrayValuedComputedField:
    """An array-valued field filtered by an array keeps its whole-value containment."""

    @pytest.fixture
    def tags_cache(self, db):
        opp = 41002
        manager = SQLCacheManager(opp, _config(opp))
        manager.store_computed_visits(
            [
                {"visit_id": "t0", "username": "u", "computed_fields": {"tags": ["a", "b"]}},
                {"visit_id": "t1", "username": "u", "computed_fields": {"tags": ["c"]}},
                {"visit_id": "t2", "username": "u", "computed_fields": {"tags": "a"}},
            ],
            visit_count=3,
        )
        return opp

    def _ids(self, opp, value):
        qs = filter_computed_visits(ComputedVisitCache.objects.filter(opportunity_id=opp), {"tags": value})
        return sorted(qs.values_list("visit_id", flat=True))

    def test_array_filter_matches_array_field_by_whole_value_containment(self, tags_cache):
        assert self._ids(tags_cache, ["c"]) == ["t1"]
        # ["a", "b"] sits inside t0's array; t2's scalar "a" matches the element "a"
        assert self._ids(tags_cache, ["a", "b"]) == ["t0", "t2"]

    def test_array_filter_is_not_widened_to_array_members(self, tags_cache):
        # no stored array holds both "a" and "c"; only the scalar "a" matches an element
        assert self._ids(tags_cache, ["a", "c"]) == ["t2"]
