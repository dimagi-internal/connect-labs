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
