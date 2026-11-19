"""Zero rows from a successful fetch is an answer, and a cache-only read must find it.

On 2026-11-19 a report's live preview looped fill -> 502 warm_failed -> fill for an
hour (run 29804, connect-labs#2810): one of its pipelines read the opportunity's
``assigned_tasks`` export, which had no records. The pipeline returned that empty
result without storing it, so the fill always "succeeded" and the cache-only build
behind the preview always missed. The labs_audits source already recorded an empty
build (#2804); the other sources whose fetchers raise on failure now do too.

All data is invented; the fetchers are patched, so there is no HTTP.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from connect_labs.labs.analysis.backends.sql.backend import EMPTY_IS_AN_ANSWER_SOURCES
from connect_labs.labs.analysis.config import AnalysisPipelineConfig, CacheStage, DataSourceConfig, FieldComputation

OPP = 10979
PIPELINE_ID = 99201

SOURCES = {
    "connect_export": (
        DataSourceConfig(type="connect_export", endpoint="assigned_tasks"),
        "connect_labs.labs.analysis.backends.sql.connect_export_fetcher.fetch_connect_export_as_visit_dicts",
    ),
    "cchq_cases": (
        DataSourceConfig(type="cchq_cases", case_type="work_area"),
        "connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher.fetch_cchq_cases_as_visit_dicts",
    ),
    "ocs_sessions": (
        DataSourceConfig(type="ocs_sessions", experiment_id="exp-1"),
        "connect_labs.labs.analysis.backends.sql.ocs_fetcher.fetch_ocs_sessions_as_visit_dicts",
    ),
}


def _config(source, filters=None):
    data_source, _ = SOURCES[source]
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name="status_text", path="status")],
        data_source=data_source,
    )
    config.pipeline_id = PIPELINE_ID
    config.terminal_stage = CacheStage.VISIT_LEVEL
    if filters:
        config.filters = filters
    return config


def _fetch(source, **kwargs):
    return patch(SOURCES[source][1], **kwargs)


def _pipeline():
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    return AnalysisPipeline(access_token="test-token")


def test_every_source_here_is_one_the_backend_treats_as_answerable_when_empty():
    assert set(SOURCES) | {"labs_audits"} == EMPTY_IS_AN_ANSWER_SOURCES


@pytest.mark.django_db
@pytest.mark.parametrize("source", sorted(SOURCES))
@pytest.mark.parametrize("filters", [None, {"username": "flw_one"}], ids=["unfiltered", "filtered"])
def test_an_empty_fetch_stores_a_cache_the_cache_only_read_finds(source, filters):
    config = _config(source, filters)
    assert _pipeline().get_cached_result_only(config, OPP) is None  # nothing built yet

    with _fetch(source, return_value=[]):
        result = _pipeline().stream_analysis_ignore_events(config, OPP)
    assert result.rows == []

    cached = _pipeline().get_cached_result_only(config, OPP)
    assert cached is not None and cached.rows == []
    assert cached.metadata["from_sql_cache"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("source", sorted(SOURCES))
def test_the_second_read_of_an_empty_source_is_a_cache_hit_not_a_refetch(source):
    """The loop: every lap re-fetched an empty source and still read as unfilled."""
    config = _config(source)
    with _fetch(source, return_value=[]) as fetch:
        _pipeline().stream_analysis_ignore_events(config, OPP)
        _pipeline().stream_analysis_ignore_events(config, OPP)
    assert fetch.call_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize("source", sorted(SOURCES))
def test_a_failed_fetch_leaves_no_empty_answer_behind(source):
    config = _config(source)
    with _fetch(source, side_effect=RuntimeError("Connect said 503")):
        with pytest.raises(RuntimeError):
            _pipeline().stream_analysis_ignore_events(config, OPP)
    assert _pipeline().get_cached_result_only(config, OPP) is None


@pytest.mark.django_db
def test_rows_arriving_later_replace_the_empty_answer():
    config = _config("connect_export")
    with _fetch("connect_export", return_value=[]):
        _pipeline().stream_analysis_ignore_events(config, OPP)

    row = {
        "id": "task-1",
        "username": "flw_one",
        "opportunity_id": OPP,
        "visit_date": "2026-11-01",
        "status": "assigned",
        "form_json": {},
    }
    with _fetch("connect_export", return_value=[row]):
        refreshed = _pipeline().stream_analysis_ignore_events(config, OPP, force_refresh=True)
    assert len(refreshed.rows) == 1
    assert len(_pipeline().get_cached_result_only(config, OPP).rows) == 1
