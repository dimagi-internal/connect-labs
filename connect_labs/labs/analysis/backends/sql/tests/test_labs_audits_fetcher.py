"""The ``labs_audits`` pipeline data source: config, fetch, and the pipeline engine.

All data is invented. The labs-only tests run end to end through the local records
backend -- the path synthetic demo opportunities take -- with no HTTP.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from connect_labs.labs.analysis.backends.sql.labs_audits_fetcher import (
    fetch_labs_audits_as_visit_dicts,
    normalize_audited_visit_row,
)
from connect_labs.labs.analysis.config import (
    DATA_SOURCE_TYPES,
    AnalysisPipelineConfig,
    DataSourceConfig,
    FieldComputation,
    raw_cache_slot,
)

LABS_ONLY_OPP = 10977
PIPELINE_ID = 99101


# ── config ──────────────────────────────────────────────────────────────────


def test_labs_audits_is_a_data_source_type():
    assert "labs_audits" in DATA_SOURCE_TYPES
    assert DataSourceConfig(type="labs_audits").type == "labs_audits"


@pytest.mark.parametrize(
    "stray",
    [
        {"endpoint": "audit_reports"},
        {"form_name": "Register Mother"},
        {"case_type": "mother"},
        {"domain": "some-domain"},
        {"experiment_id": "abc"},
        {"form_lookback_days": 7},
    ],
)
def test_labs_audits_refuses_other_source_keys(stray):
    with pytest.raises(ValueError, match="labs_audits data_source takes no other keys"):
        DataSourceConfig(type="labs_audits", **stray)


def test_labs_audits_ignores_server_supplied_credentials():
    """_schema_to_config fills api_key from settings for every source; that is not a stray key."""
    assert DataSourceConfig(type="labs_audits", api_key="server-key").api_key == "server-key"


def test_an_unknown_type_is_still_refused():
    with pytest.raises(ValueError, match="Invalid data source type"):
        DataSourceConfig(type="labs_audit")


def test_labs_audits_has_a_per_pipeline_raw_slot():
    assert raw_cache_slot(PIPELINE_ID, "labs_audits", DataSourceConfig(type="labs_audits")) == PIPELINE_ID


def test_schema_to_config_reads_a_labs_audits_schema():
    from connect_labs.workflow.data_access import PipelineDataAccess

    schema = {
        "data_source": {"type": "labs_audits"},
        "grouping_key": "username",
        "terminal_stage": "visit_level",
        "fields": [
            {"name": "n_pass", "path": "audit.n_pass", "transform": "int", "aggregation": "sum"},
            {"name": "n_decided", "path": "audit.n_decided", "transform": "int", "aggregation": "sum"},
        ],
    }
    pda = PipelineDataAccess.__new__(PipelineDataAccess)  # _schema_to_config reads no instance state
    config = pda._schema_to_config(schema, PIPELINE_ID)
    assert config.data_source.type == "labs_audits"
    assert config.raw_slot_id == PIPELINE_ID
    assert [f.name for f in config.fields] == ["n_pass", "n_decided"]


# ── fetch ───────────────────────────────────────────────────────────────────


def _session_record(id, visit_results, visit_images=None, completed_at="2026-09-01T10:00:00Z"):
    from connect_labs.audit.models import AuditSessionRecord

    data = {"status": "completed", "completed_at": completed_at, "visit_results": visit_results}
    data["visit_images"] = visit_images or {}
    return AuditSessionRecord(
        {"id": id, "experiment": "audit", "type": "AuditSession", "opportunity_id": 4321, "data": data}
    )


def test_normalized_row_joins_on_the_connect_visit_id():
    row = {"visit_id": "555", "username": "flw_one", "visit_date": "2026-08-30", "n_pass": 2}
    v = normalize_audited_visit_row(row, 4321)
    assert v["id"] == "555" and v["opportunity_id"] == 4321
    assert v["username"] == "flw_one" and v["visit_date"] == "2026-08-30"
    assert v["form_json"] == {"audit": row}
    assert v["status"] == ""  # never claims to be an approved visit


def test_fetch_reads_the_opportunity_sessions_as_the_given_token():
    session = _session_record(
        1,
        {"555": {"assessments": {"b1": {"question_id": "q", "result": "pass"}}}},
        {"555": [{"blob_id": "b1", "username": "flw_one", "visit_date": "2026-08-30T08:00:00"}]},
    )
    fake = MagicMock()
    fake.get_audit_sessions.return_value = [session]
    with patch("connect_labs.audit.data_access.AuditDataAccess", return_value=fake) as cls:
        rows = fetch_labs_audits_as_visit_dicts(
            request=None, data_source=DataSourceConfig(type="labs_audits"), access_token="tok", opportunity_id=4321
        )
    cls.assert_called_once_with(opportunity_id=4321, access_token="tok")
    fake.get_audit_sessions.assert_called_once_with()  # every status
    fake.close.assert_called_once()
    assert [(r["id"], r["username"], r["form_json"]["audit"]["n_pass"]) for r in rows] == [("555", "flw_one", 1)]


def test_fetch_takes_the_token_from_the_session_and_refuses_without_one():
    request = MagicMock()
    request.session = {"labs_oauth": {"access_token": "from-session"}}
    fake = MagicMock()
    fake.get_audit_sessions.return_value = []
    with patch("connect_labs.audit.data_access.AuditDataAccess", return_value=fake) as cls:
        assert fetch_labs_audits_as_visit_dicts(request, DataSourceConfig(type="labs_audits"), None, 4321) == []
    assert cls.call_args.kwargs["access_token"] == "from-session"

    request.session = {}
    with pytest.raises(ValueError, match="requires a Connect OAuth token"):
        fetch_labs_audits_as_visit_dicts(request, DataSourceConfig(type="labs_audits"), None, 4321)


# ── labs-only opportunity, end to end (local records backend, real DB) ───────


def _labs_only_opp(opp_id=LABS_ONLY_OPP):
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    SyntheticOpportunity.objects.create(opportunity_id=opp_id, labs_only=True, enabled=True, gdrive_folder_id="gen")


def _create_session(opp_id, data):
    from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient

    client = LabsRecordAPIClient("test-token", opportunity_id=opp_id)
    return client.create_record(experiment="audit", type="AuditSession", data=data, username="auditor_a")


def _seed_two_sessions(opp_id=LABS_ONLY_OPP):
    images = {
        "7001": [
            {"blob_id": "b1", "username": "flw_one", "visit_date": "2026-09-01T09:00:00", "question_id": "q/muac"},
            {"blob_id": "b2", "username": "flw_one", "visit_date": "2026-09-01T09:00:00", "question_id": "q/child"},
        ],
        "7002": [
            {"blob_id": "b3", "username": "flw_two", "visit_date": "2026-09-02T09:00:00", "question_id": "q/muac"}
        ],
    }
    _create_session(
        opp_id,
        {
            "status": "completed",
            "completed_at": "2026-09-03T12:00:00+00:00",
            "opportunity_id": opp_id,
            "visit_images": images,
            "visit_results": {
                "7001": {"assessments": {"b1": {"question_id": "q/muac", "result": "fail"}, "b2": {"result": "pass"}}},
                "7002": {"assessments": {"b3": {"question_id": "q/muac", "result": "pass", "ai_result": "no_match"}}},
            },
        },
    )
    # A later re-review, still in progress: overturns nothing that a completed
    # session decided, but its own new decision on an image counts.
    _create_session(
        opp_id,
        {
            "status": "in_progress",
            "opportunity_id": opp_id,
            "visit_images": {"7003": [{"blob_id": "b4", "username": "flw_two", "visit_date": "2026-09-04T09:00:00"}]},
            "visit_results": {
                "7001": {"assessments": {"b1": {"question_id": "q/muac", "result": "pass"}}},
                "7003": {"assessments": {"b4": {"question_id": "q/muac", "result": None, "ai_result": "match"}}},
            },
        },
    )


@pytest.mark.django_db
def test_fetch_for_a_labs_only_opportunity_reads_the_local_backend(httpx_mock):
    # httpx_mock with no responses registered: any HTTP request fails the test.
    _labs_only_opp()
    _seed_two_sessions()
    rows = fetch_labs_audits_as_visit_dicts(None, DataSourceConfig(type="labs_audits"), "test-token", LABS_ONLY_OPP)
    by_id = {r["id"]: r["form_json"]["audit"] for r in rows}
    assert set(by_id) == {"7001", "7002", "7003"}
    assert (by_id["7001"]["n_pass"], by_id["7001"]["n_fail"], by_id["7001"]["n_decided"]) == (1, 1, 2)
    assert by_id["7001"]["username"] == "flw_one" and by_id["7001"]["question_ids"] == "q/child,q/muac"
    assert (by_id["7002"]["n_pass"], by_id["7002"]["n_decided"]) == (1, 1)
    assert (by_id["7003"]["n_pending"], by_id["7003"]["n_decided"]) == (1, 0)


def _labs_audits_config():
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[
            FieldComputation(name="n_pass", path="audit.n_pass", transform=lambda x: int(float(x)) if x else None),
            FieldComputation(
                name="n_decided", path="audit.n_decided", transform=lambda x: int(float(x)) if x else None
            ),
        ],
        data_source=DataSourceConfig(type="labs_audits"),
    )
    config.pipeline_id = PIPELINE_ID
    return config


@pytest.mark.django_db
def test_the_pipeline_engine_fills_the_source_raw_slot(httpx_mock):
    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    _labs_only_opp()
    _seed_two_sessions()
    result = AnalysisPipeline(access_token="test-token").stream_analysis_ignore_events(
        _labs_audits_config(), LABS_ONLY_OPP
    )
    # The default terminal stage aggregates per worker: the visit maker of each row.
    assert sorted(r.username for r in result.rows) == ["flw_one", "flw_two"]
    cached = RawVisitCache.objects.filter(opportunity_id=LABS_ONLY_OPP, pipeline_id=PIPELINE_ID)
    assert sorted(cached.values_list("visit_id", flat=True)) == ["7001", "7002", "7003"]
    # Nothing landed in the shared user_visits slot.
    assert not RawVisitCache.objects.filter(opportunity_id=LABS_ONLY_OPP).exclude(pipeline_id=PIPELINE_ID).exists()


# ── no audits reviewed yet: a valid, empty, cached result ───────────────────
#
# "No audits yet" is this source's normal initial state. The cache tables mark a
# config as cached by the existence of its rows, so a zero-row build used to leave
# nothing for the cache-only read (saved weeks, history rebuilds) to find.

EMPTY_OPP = 10978


def _fetch_returns(rows):
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    return patch.object(AnalysisPipeline, "_fetch_labs_audits", return_value=rows)


def _audit_row(visit_id="8001", username="flw_one"):
    return normalize_audited_visit_row(
        {
            "visit_id": visit_id,
            "username": username,
            "visit_date": "2026-09-01",
            "n_pass": 2,
            "n_decided": 2,
        },
        EMPTY_OPP,
    )


def _visit_level_config(filters=None):
    from connect_labs.labs.analysis.config import CacheStage

    config = _labs_audits_config()
    config.terminal_stage = CacheStage.VISIT_LEVEL
    if filters:
        config.filters = filters
    return config


def _pipeline():
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    return AnalysisPipeline(access_token="test-token")


@pytest.mark.django_db
@pytest.mark.parametrize("filters", [None, {"username": "flw_one"}], ids=["unfiltered", "filtered"])
def test_an_empty_audit_feed_stores_a_cache_the_cache_only_read_finds(filters):
    config = _visit_level_config(filters)
    pipeline = _pipeline()
    assert pipeline.get_cached_result_only(config, EMPTY_OPP) is None  # nothing built yet

    with _fetch_returns([]):
        result = pipeline.stream_analysis_ignore_events(config, EMPTY_OPP)
    assert result.rows == []

    cached = _pipeline().get_cached_result_only(config, EMPTY_OPP)
    assert cached is not None and cached.rows == []
    assert cached.metadata["from_sql_cache"] is True


@pytest.mark.django_db
def test_a_forced_refresh_of_an_empty_filtered_feed_does_not_fail():
    config = _visit_level_config({"username": "flw_one"})
    with _fetch_returns([]):
        result = _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP, force_refresh=True)
    assert result.rows == []
    assert _pipeline().get_cached_result_only(config, EMPTY_OPP).rows == []


@pytest.mark.django_db
def test_the_second_read_of_an_empty_feed_is_a_cache_hit_not_a_refetch():
    config = _visit_level_config()
    with _fetch_returns([]) as fetch:
        _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP)
        _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP)
    assert fetch.call_count == 1


@pytest.mark.django_db
def test_a_real_fetch_replaces_the_empty_cache():
    config = _visit_level_config()
    with _fetch_returns([]):
        _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP)
    with _fetch_returns([_audit_row()]):
        refreshed = _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP, force_refresh=True)
    assert [r.id for r in refreshed.rows] == ["8001"]

    cached = _pipeline().get_cached_result_only(config, EMPTY_OPP)
    assert [r.id for r in cached.rows] == ["8001"]


@pytest.mark.django_db
def test_audits_vanishing_replaces_a_populated_cache_with_an_empty_one():
    config = _visit_level_config()
    with _fetch_returns([_audit_row()]):
        _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP)
    with _fetch_returns([]):
        _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP, force_refresh=True)
    assert _pipeline().get_cached_result_only(config, EMPTY_OPP).rows == []


@pytest.mark.django_db
def test_the_empty_cache_expires_like_any_other():
    from django.core.cache import cache

    config = _visit_level_config()
    with _fetch_returns([]):
        _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP)
    assert _pipeline().get_cached_result_only(config, EMPTY_OPP) is not None

    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

    manager = SQLCacheManager(EMPTY_OPP, config)
    assert manager.ttl.total_seconds() == 3600  # the one-hour default every cache table uses
    cache.delete(manager._empty_marker_key())  # what the timeout does
    assert _pipeline().get_cached_result_only(config, EMPTY_OPP) is None


@pytest.mark.django_db
def test_clearing_an_opportunitys_cache_drops_the_empty_marker():
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

    config = _visit_level_config()
    with _fetch_returns([]):
        _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP)
    SQLCacheManager.delete_all_cache(EMPTY_OPP)
    assert _pipeline().get_cached_result_only(config, EMPTY_OPP) is None


@pytest.mark.django_db
def test_an_empty_feed_at_the_aggregated_stage_is_also_a_valid_empty_cache():
    config = _labs_audits_config()  # default terminal stage: AGGREGATED
    with _fetch_returns([]):
        result = _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP)
    assert result.rows == []
    cached = _pipeline().get_cached_result_only(config, EMPTY_OPP)
    assert cached is not None and cached.rows == []


@pytest.mark.django_db
def test_other_sources_still_miss_when_nothing_is_stored():
    """The marker is scoped to labs_audits: a gdrive/cchq/ocs config with no rows is a miss."""
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

    config = _visit_level_config()
    config.data_source = DataSourceConfig(type="ocs_sessions", experiment_id="e")
    SQLCacheManager(EMPTY_OPP, config).store_empty_marker()  # even a stray marker is ignored
    assert _pipeline().get_cached_result_only(config, EMPTY_OPP) is None


@pytest.mark.django_db
def test_the_workflow_cache_read_returns_zero_rows_for_an_empty_audit_feed():
    """What `workflow_rebuild_history` reads through (PipelineDataAccess.get_cached_pipeline_result):
    a result with no rows, which get_cached_pipeline_data accepts, not None (a PipelineCacheMiss)."""
    from connect_labs.workflow.data_access import PipelineDataAccess

    config = _visit_level_config()
    definition = MagicMock()
    definition.schema = {"data_source": {"type": "labs_audits"}, "terminal_stage": "visit_level"}
    definition.name = "photo_audits"
    pda = PipelineDataAccess.__new__(PipelineDataAccess)
    pda.request = None
    pda.access_token = "test-token"
    pda.get_definition = lambda _id: definition

    assert pda.get_cached_pipeline_result(PIPELINE_ID, EMPTY_OPP, config=config) is None  # cold
    with _fetch_returns([]):
        _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP)
    got = pda.get_cached_pipeline_result(PIPELINE_ID, EMPTY_OPP, config=config)
    assert got is not None
    assert got["rows"] == [] and got["metadata"]["row_count"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize("rows", [[], "one"], ids=["empty_build", "non_empty_build"])
def test_a_failed_build_never_leaves_an_empty_marker(rows):
    """The marker is published only once processing succeeded, and a build that fails
    drops the previous one: readers retry instead of answering 'empty'."""
    from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

    config = _visit_level_config()
    with _fetch_returns([]):
        _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP)
    assert SQLCacheManager(EMPTY_OPP, config).has_empty_marker()  # a marker from an earlier build

    with _fetch_returns([_audit_row()] if rows else []):
        with patch.object(SQLBackend, "_process_visit_level", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                _pipeline().stream_analysis_ignore_events(config, EMPTY_OPP, force_refresh=True)

    assert not SQLCacheManager(EMPTY_OPP, config).has_empty_marker()
    assert _pipeline().get_cached_result_only(config, EMPTY_OPP) is None


@pytest.mark.django_db
def test_a_config_scoped_delete_drops_only_that_configs_empty_marker():
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

    first = _visit_level_config()
    second = _visit_level_config()
    second.fields = second.fields[:1]  # a different config hash, another pipeline
    second.pipeline_id = PIPELINE_ID + 1
    other_opp_config = _visit_level_config()
    with _fetch_returns([]):
        _pipeline().stream_analysis_ignore_events(first, EMPTY_OPP)
        _pipeline().stream_analysis_ignore_events(second, EMPTY_OPP)
        _pipeline().stream_analysis_ignore_events(other_opp_config, EMPTY_OPP + 1)
    assert SQLCacheManager(EMPTY_OPP, first).config_hash != SQLCacheManager(EMPTY_OPP, second).config_hash

    SQLCacheManager.delete_config_cache(EMPTY_OPP, SQLCacheManager(EMPTY_OPP, first).config_hash)

    assert _pipeline().get_cached_result_only(first, EMPTY_OPP) is None
    assert _pipeline().get_cached_result_only(second, EMPTY_OPP).rows == []
    assert _pipeline().get_cached_result_only(other_opp_config, EMPTY_OPP + 1).rows == []
