"""End-to-end Postgres test for period-scoped FLW reads (ace#764).

The LLO weekly review freezes a saved-run snapshot per week. Before this
change the FLW aggregation ignored the run's period and every weekly snapshot
froze the same all-time total — so "Week 1" and "Week 2" rendered identical.

These tests seed one raw-visit cache spanning two weeks and assert that
`SQLBackend.get_period_scoped_flw_result` re-aggregates that single cache to a
half-open `[date_from, date_to)` window — producing genuinely different per-FLW
counts per week, while an unwindowed read still returns the all-time total.
"""

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import AnalysisPipelineConfig, CacheStage, FieldComputation

PIPELINE_ID = 7001
OPP_ID = 8642


def _config(date_from: str = "", date_to: str = "") -> AnalysisPipelineConfig:
    cfg = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name="n", path="form.x", aggregation="count")],
        histograms=[],
        filters={},
        experiment="test",
        terminal_stage=CacheStage.AGGREGATED,
        date_from=date_from,
        date_to=date_to,
    )
    cfg.pipeline_id = PIPELINE_ID
    return cfg


def _seed(opp_id: int, rows: list[tuple[str, str]]) -> None:
    """rows: (username, visit_date ISO). Each is one approved visit."""
    future = timezone.now() + timezone.timedelta(days=1)
    for i, (username, vdate) in enumerate(rows):
        RawVisitCache.objects.create(
            opportunity_id=opp_id,
            pipeline_id=PIPELINE_ID,
            visit_count=len(rows),
            expires_at=future,
            visit_id=str(30000 + i),
            username=username,
            form_json={"form": {"x": 1}},
            visit_date=vdate,
            status="approved",
        )


# Amara works both weeks; Deepa only week 1; Bto only week 2.
_TWO_WEEKS = [
    ("amara", "2026-05-16"),
    ("amara", "2026-05-18"),  # week 1: amara=2
    ("deepa", "2026-05-19"),  # week 1: deepa=1
    ("amara", "2026-05-24"),  # week 2: amara=1
    ("bo", "2026-05-25"),
    ("bo", "2026-05-26"),  # week 2: bo=2
]


def _counts(result) -> dict[str, int]:
    return {r.username: r.total_visits for r in result.rows}


@pytest.mark.django_db
class TestPeriodScopedFlwRead:
    def test_no_window_returns_all_time_total(self, db):
        _seed(OPP_ID, _TWO_WEEKS)
        result = SQLBackend().get_period_scoped_flw_result(OPP_ID, _config())
        assert _counts(result) == {"amara": 3, "deepa": 1, "bo": 2}

    def test_week1_window_scopes_to_week1(self, db):
        _seed(OPP_ID, _TWO_WEEKS)
        result = SQLBackend().get_period_scoped_flw_result(
            OPP_ID, _config(date_from="2026-05-15", date_to="2026-05-22")
        )
        # bo (week 2 only) drops out entirely; amara is 2, not 3.
        assert _counts(result) == {"amara": 2, "deepa": 1}

    def test_week2_window_scopes_to_week2(self, db):
        _seed(OPP_ID, _TWO_WEEKS)
        result = SQLBackend().get_period_scoped_flw_result(
            OPP_ID, _config(date_from="2026-05-22", date_to="2026-05-29")
        )
        # deepa (week 1 only) drops out; amara is 1, not 3.
        assert _counts(result) == {"amara": 1, "bo": 2}

    def test_adjacent_weeks_do_not_double_count_boundary(self, db):
        """Half-open windows: a visit exactly on the shared boundary date
        (05-22) belongs to week 2 only, never both."""
        _seed(OPP_ID, [("amara", "2026-05-22")])
        wk1 = SQLBackend().get_period_scoped_flw_result(OPP_ID, _config(date_from="2026-05-15", date_to="2026-05-22"))
        wk2 = SQLBackend().get_period_scoped_flw_result(OPP_ID, _config(date_from="2026-05-22", date_to="2026-05-29"))
        assert _counts(wk1) == {}  # excluded from week 1 (< 05-22)
        assert _counts(wk2) == {"amara": 1}  # included in week 2 (>= 05-22)

    def test_empty_raw_cache_is_a_miss(self, db):
        """No raw cache for the pipeline slot → None, so the caller surfaces a
        PipelineCacheMiss ("reload the dashboard") rather than freezing zeros."""
        result = SQLBackend().get_period_scoped_flw_result(
            OPP_ID, _config(date_from="2026-05-15", date_to="2026-05-22")
        )
        assert result is None

    def test_empty_window_with_populated_cache_is_zero_rows_not_miss(self, db):
        """A populated cache but a window with no visits returns an empty
        result (not None) — a legitimately quiet week, not a cache miss."""
        _seed(OPP_ID, _TWO_WEEKS)
        result = SQLBackend().get_period_scoped_flw_result(
            OPP_ID, _config(date_from="2026-06-01", date_to="2026-06-08")
        )
        assert result is not None
        assert _counts(result) == {}
