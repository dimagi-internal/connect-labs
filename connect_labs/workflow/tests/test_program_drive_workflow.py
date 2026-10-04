"""A PROGRAM-owned workflow over a PROGRAM-scoped Google Drive pipeline reads it ONCE.

A program workflow fans its pipelines out over its opportunities and tags every row
with the opportunity it came from. A program-scoped Drive source is the program's,
not any one opportunity's: fanned out over N opportunities it would come back N
times and every count would be N times too large. These pin, through every read
path a dashboard uses -- the page-load stream, `get_pipeline_data`, the cached
(run-completion) read and the on-demand `queryPipelineRows` endpoint -- that it is
read once, rows tagged `opportunity_id: null`, with the program gate in front.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from connect_labs.labs.analysis.backends.sql import gdrive_fetcher as gf
from connect_labs.labs.analysis.backends.sql.tests.test_gdrive_fetcher import (  # noqa: F401 - fixtures
    STAFF,
    allowed_root,
    drive,
)
from connect_labs.labs.analysis.backends.sql.tests.test_gdrive_program_scope import (
    OPPORTUNITY_ONLY_TREE,
    OWNER_TREE,
    PROGRAM,
    TARGET,
)
from connect_labs.labs.analysis.config import AnalysisPipelineConfig, CacheStage, DataSourceConfig, FieldComputation
from connect_labs.workflow import views
from connect_labs.workflow.data_access import (
    PipelineDataAccess,
    WorkflowDataAccess,
    pipeline_read_targets,
    program_drive_scope,
)

pytestmark = pytest.mark.django_db

DEF_ID = 23765
PIPELINE_ID = 7
OPPS = [1251, 1252, 1253]


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(gf, "_caller_org_tree", lambda request, token: OWNER_TREE)


def _config(stage=CacheStage.VISIT_LEVEL):
    name = "state_key" if stage == CacheStage.ENTITY else "state"
    return AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name=name, path="row.state", aggregation="first")],
        terminal_stage=stage,
        linking_field=name if stage == CacheStage.ENTITY else "entity_id",
        data_source=DataSourceConfig(
            **gf.authorize_gdrive_source(TARGET, None, STAFF, PIPELINE_ID, program_id=PROGRAM)
        ),
        pipeline_id=PIPELINE_ID,
    )


def _definition(opps=OPPS, load=None):
    source = {"pipeline_id": PIPELINE_ID, "alias": "answers", "home_scope": {"program_id": PROGRAM}}
    if load:
        source["load"] = load
    definition = MagicMock(pipeline_sources=[source], opportunity_ids=list(opps), program_id=PROGRAM)
    return definition


def _pipeline_def():
    pipeline_def = MagicMock(schema={"terminal_stage": "visit_level", "data_source": TARGET})
    pipeline_def.name = "Interview answers"
    return pipeline_def


def _downloads(fake):
    return [c for c in fake.calls if c[0] == "download"]


# --------------------------------------------------------------------------- the targets


def test_read_targets_fan_out_per_opportunity_except_for_a_program_drive_source():
    assert pipeline_read_targets(_config(), OPPS) == [(f"program:{PROGRAM}", None, None)]
    assert pipeline_read_targets(_config(), []) == [(f"program:{PROGRAM}", None, None)]
    opp_cfg = AnalysisPipelineConfig(grouping_key="username")
    assert pipeline_read_targets(opp_cfg, OPPS) == [(str(o), o, o) for o in OPPS]
    assert pipeline_read_targets(None, [5]) == [("5", 5, 5)]
    assert program_drive_scope(_config()) == PROGRAM and program_drive_scope(opp_cfg) is None


# --------------------------------------------------------------------------- get_pipeline_data / cached


def _workflow_read(config, method, **kw):
    access = WorkflowDataAccess(access_token="tok", program_id=PROGRAM)
    definition = _definition()
    try:
        with (
            patch.object(WorkflowDataAccess, "get_definition", return_value=definition),
            patch.object(PipelineDataAccess, "get_definition", return_value=_pipeline_def()),
            patch(
                "connect_labs.workflow.views._resolve_pipeline_sources_for_run",
                return_value=(definition.pipeline_sources, {"answers": config}),
            ),
        ):
            return getattr(access, method)(DEF_ID, None, **kw)["answers"]
    finally:
        access.close()


@pytest.mark.parametrize("stage", [CacheStage.VISIT_LEVEL, CacheStage.ENTITY])
def test_get_pipeline_data_reads_once_and_rows_are_not_multiplied(drive, owner, stage):  # noqa: F811
    config = _config(stage)
    drive.calls.clear()
    out = _workflow_read(config, "get_pipeline_data")
    assert out["metadata"]["row_count"] == 3, "rows were multiplied by the opportunities"
    assert len(_downloads(drive)) == 2
    assert {r["opportunity_id"] for r in out["rows"]} == {None}
    assert out["metadata"]["program_id"] == PROGRAM and out["metadata"]["read_once_for_program"] is True
    assert list(out["metadata"]["per_opp"]) == [f"program:{PROGRAM}"]

    # The run-completion read sees the same, once, from the cache.
    cached = _workflow_read(config, "get_cached_pipeline_data")
    assert cached["metadata"]["row_count"] == 3
    assert len(_downloads(drive)) == 2


def test_get_pipeline_data_refuses_an_opportunity_only_member(drive, monkeypatch):  # noqa: F811
    monkeypatch.setattr(gf, "_caller_org_tree", lambda request, token: OPPORTUNITY_ONLY_TREE)
    out = _workflow_read(_config(), "get_pipeline_data")
    assert out["rows"] == []
    assert "manages program 121" in out["metadata"]["per_opp"][f"program:{PROGRAM}"]["error"]
    assert _downloads(drive) == []


# --------------------------------------------------------------------------- the page stream


def _stream(config, *, definition, labs_context):
    request = RequestFactory().get(f"/labs/workflow/api/{DEF_ID}/pipeline-data/stream/")
    request.user = MagicMock(is_authenticated=True)
    request.labs_context = labs_context
    request.session = {"labs_oauth": {"access_token": "tok"}}
    with (
        patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA,
        patch("connect_labs.workflow.views.PipelineDataAccess") as MockPDA,
        patch("connect_labs.workflow.views._resolve_pipeline_definition", return_value=_pipeline_def()),
        patch(
            "connect_labs.workflow.views._resolve_pipeline_sources_for_run",
            return_value=(definition.pipeline_sources, {"answers": config}),
        ),
        patch.object(views.PipelineDataStreamView, "_maybe_probe_cchq_access", return_value=iter(())),
    ):
        MockWDA.return_value.get_definition.return_value = definition
        view = views.PipelineDataStreamView()
        view.kwargs = {"definition_id": DEF_ID}
        events = [json.loads(chunk[len("data: ") :]) for chunk in view.stream_data(request)]
    payloads = [e for e in events if (e.get("data") or {}).get("pipelines")]
    assert payloads, events
    return payloads[-1]["data"]["pipelines"]["answers"], MockPDA


@pytest.mark.parametrize("stage", [CacheStage.VISIT_LEVEL, CacheStage.ENTITY])
def test_the_page_stream_reads_a_program_source_once(drive, owner, stage):  # noqa: F811
    drive.calls.clear()
    out, _ = _stream(_config(stage), definition=_definition(), labs_context={"program_id": PROGRAM})
    assert len(out["rows"]) == 3, "rows were multiplied by the opportunities"
    assert len(_downloads(drive)) == 2
    assert {r["opportunity_id"] for r in out["rows"]} == {None}
    assert out["metadata"]["read_once_for_program"] is True


def test_a_program_workflow_spanning_no_opportunities_still_streams(drive, owner):  # noqa: F811
    out, pda = _stream(_config(), definition=_definition(opps=[]), labs_context={"program_id": PROGRAM})
    assert len(out["rows"]) == 3
    assert pda.call_args.kwargs.get("program_id") == PROGRAM  # records read in the program scope


# --------------------------------------------------------------------------- queryPipelineRows


def _query(config, body, *, opps=OPPS, tree=OWNER_TREE, monkeypatch):
    monkeypatch.setattr(gf, "_caller_org_tree", lambda request, token: tree)
    request = RequestFactory().post(
        f"/labs/workflow/api/{DEF_ID}/pipeline-query/?program_id={PROGRAM}",
        data=json.dumps(body),
        content_type="application/json",
    )
    request.user = MagicMock(is_authenticated=True)
    request.session = {"labs_oauth": {"access_token": "tok"}}
    request.labs_context = {"program_id": PROGRAM}
    pda = MagicMock()
    pda.get_definition.return_value = MagicMock(schema={"fields": []})
    pda._schema_to_config.return_value = config
    wda = MagicMock()
    wda.get_definition.return_value = _definition(opps=opps, load="on_demand")
    with (
        patch.object(views, "WorkflowDataAccess", return_value=wda),
        patch.object(views, "PipelineDataAccess", return_value=pda) as pda_cls,
    ):
        response = views.pipeline_query_api(request, DEF_ID)
    return response.status_code, json.loads(response.content), pda_cls


@pytest.mark.parametrize("opps", [OPPS, []])
def test_query_rows_of_a_program_source_needs_no_opportunity(drive, owner, monkeypatch, opps):  # noqa: F811
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    config = _config()
    AnalysisPipeline(access_token="tok").stream_analysis_ignore_events(config, None)  # warm, as the task would
    body = {"alias": "answers", "filters": {"state": "Kebbi"}}
    status, out, pda_cls = _query(config, body, monkeypatch=monkeypatch, opps=opps)
    assert status == 200, out
    assert out["total"] == 1 and out["opportunity_id"] is None and out["program_id"] == PROGRAM
    assert {r["opportunity_id"] for r in out["rows"]} == {None}
    assert pda_cls.call_args.kwargs == {
        "request": pda_cls.call_args.kwargs["request"],
        "access_token": "tok",
        "program_id": PROGRAM,
    }


def test_query_rows_of_a_program_source_refuses_an_opportunity_only_member(drive, monkeypatch):  # noqa: F811
    status, out, _ = _query(_config(), {"alias": "answers"}, tree=OPPORTUNITY_ONLY_TREE, monkeypatch=monkeypatch)
    assert status == 403 and "manages program 121" in out["error"]


def test_a_per_opportunity_pipeline_in_a_multi_opp_workflow_still_needs_an_opportunity(monkeypatch):
    config = AnalysisPipelineConfig(grouping_key="username", pipeline_id=PIPELINE_ID)
    status, out, _ = _query(config, {"alias": "answers"}, monkeypatch=monkeypatch)
    assert status == 400 and "opportunity_id is required" in out["error"]


def test_a_cold_program_source_is_warmed_under_the_program_key(drive, monkeypatch):  # noqa: F811
    cache.clear()
    with patch("connect_labs.workflow.tasks.warm_pipeline_query_cache") as task:
        status, out, _ = _query(_config(), {"alias": "answers"}, monkeypatch=monkeypatch)
    assert status == 202, out
    kwargs = task.delay.call_args.kwargs
    assert kwargs["opportunity_id"] == gf.program_cache_scope(PROGRAM)
    assert kwargs["scope"] == {"program_id": PROGRAM}


def test_the_warm_task_reads_a_program_source_in_the_program_scope(drive, owner):  # noqa: F811
    from connect_labs.workflow import pipeline_query

    config = _config()
    cache.set("lock", "warming", 60)
    wda = MagicMock()
    wda.get_definition.return_value = _definition(opps=[])
    with (
        patch("connect_labs.workflow.data_access.WorkflowDataAccess", return_value=wda),
        patch("connect_labs.workflow.data_access.PipelineDataAccess") as pda_cls,
        patch(
            "connect_labs.workflow.views._resolve_pipeline_sources_for_run",
            return_value=(_definition().pipeline_sources, {"answers": config}),
        ),
    ):
        out = pipeline_query.warm_pipeline_cache(
            "tok",
            definition_id=DEF_ID,
            alias="answers",
            opportunity_id=gf.program_cache_scope(PROGRAM),
            scope={"program_id": PROGRAM},
            lock_key="lock",
        )
    assert out == {"warmed": ["answers"]}, out
    assert pda_cls.call_args.kwargs.get("program_id") == PROGRAM
    assert pipeline_query.cached_queryset(config, gf.program_cache_scope(PROGRAM)).count() == 3
