"""On-demand pipeline sources and `actions.queryPipelineRows` (pipeline_query_api).

Workflow 23765's answers pipeline was 173k rows of free text, streamed whole to the
browser on every load (27 s, ~435 MB of tab heap) to read one question's answers at a
time. A source marked `load: "on_demand"` is no longer streamed; render code asks the
server for the page it needs, which answers in SQL from the pipeline's cache.

These pin: the endpoint's filters / search / ordering / paging against real cache
rows; its refusals (unknown field, bad limit, no workflow, an opportunity the workflow
does not span, a Drive pipeline whose own stamp does not verify); the cold-cache
handoff to a background warm; and that the stream withholds on-demand sources.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.models import ComputedEntityCache, ComputedVisitCache
from connect_labs.labs.analysis.config import (
    AnalysisPipelineConfig,
    CacheStage,
    DataSourceConfig,
    FieldComputation,
)
from connect_labs.labs.analysis.utils import get_config_hash
from connect_labs.workflow import views
from connect_labs.workflow.data_access import (
    WorkflowDataAccess,
    is_on_demand_source,
    streamed_and_skipped_aliases,
)

pytestmark = pytest.mark.django_db

DEF_ID = 23765
OPP = 1251
PIPELINE_ID = 23786

ANSWERS = [
    # (qid, state, answer)
    ("4.12", "Kebbi", "No drugs at the facility"),
    ("4.12", "Kebbi", "We walk far for drugs"),
    ("4.12", "Borno", "Drugs arrive late"),
    ("4.13", "Kebbi", "Pay is late"),
    ("4.13", "Borno", None),
    ("5.01", "Kano", "Training was short"),
]


def _visit_config(**kw):
    return AnalysisPipelineConfig(
        grouping_key="username",
        fields=[
            FieldComputation(name="qid", path="row.qid"),
            FieldComputation(name="state", path="row.state"),
            FieldComputation(name="answer", path="row.answer"),
            FieldComputation(name="n", path="row.n"),
        ],
        terminal_stage=CacheStage.VISIT_LEVEL,
        pipeline_id=PIPELINE_ID,
        **kw,
    )


def _seed_visits(config, rows=ANSWERS, opp=OPP):
    expires = timezone.now() + timedelta(hours=1)
    ComputedVisitCache.objects.bulk_create(
        ComputedVisitCache(
            opportunity_id=opp,
            pipeline_id=config.pipeline_id,
            config_hash=get_config_hash(config),
            visit_count=len(rows),
            expires_at=expires,
            visit_id=f"f:{i}",
            username="",
            computed_fields={"qid": q, "state": s, "answer": a, "n": i},
        )
        for i, (q, s, a) in enumerate(rows)
    )


def _definition(sources=None, opps=(OPP,)):
    definition = MagicMock()
    definition.opportunity_ids = list(opps)
    definition.pipeline_sources = sources or [{"pipeline_id": PIPELINE_ID, "alias": "answers", "load": "on_demand"}]
    return definition


def _post(config, body, *, definition=None, scope=OPP):
    request = RequestFactory().post(
        f"/labs/workflow/api/{DEF_ID}/pipeline-query/?opportunity_id={scope}",
        data=json.dumps(body),
        content_type="application/json",
    )
    request.user = MagicMock(is_authenticated=True)
    request.session = {"labs_oauth": {"access_token": "tok"}}
    request.labs_context = {"opportunity_id": scope}
    pda = MagicMock()
    pda.get_definition.return_value = MagicMock(schema={"fields": []})
    pda._schema_to_config.return_value = config
    wda = MagicMock()
    wda.get_definition.return_value = definition if definition is not None else _definition()
    with (
        patch.object(views, "WorkflowDataAccess", return_value=wda),
        patch.object(views, "PipelineDataAccess", return_value=pda),
    ):
        response = views.pipeline_query_api(request, DEF_ID)
    return response.status_code, json.loads(response.content)


# --------------------------------------------------------------------------- answers


class TestQueries:
    def test_filter_by_a_value(self):
        config = _visit_config()
        _seed_visits(config)
        status, body = _post(config, {"alias": "answers", "filters": {"qid": "4.12"}})
        assert status == 200 and body["status"] == "ready"
        assert body["total"] == 3
        assert {r["answer"] for r in body["rows"]} == {
            "No drugs at the facility",
            "We walk far for drugs",
            "Drugs arrive late",
        }
        assert all(r["opportunity_id"] == OPP for r in body["rows"])

    def test_filter_by_any_of_several_values_and_by_null(self):
        config = _visit_config()
        _seed_visits(config)
        _status, body = _post(config, {"alias": "answers", "filters": {"qid": ["4.13", "5.01"], "state": "Kebbi"}})
        assert body["total"] == 1 and body["rows"][0]["answer"] == "Pay is late"
        _status, body = _post(config, {"alias": "answers", "filters": {"answer": None}})
        assert body["total"] == 1 and body["rows"][0]["state"] == "Borno"

    def test_numbers_compare_with_numbers(self):
        config = _visit_config()
        _seed_visits(config)
        _status, body = _post(config, {"alias": "answers", "filters": {"n": [0, 5.0]}})
        assert sorted(r["n"] for r in body["rows"]) == [0, 5]

    def test_search_is_a_case_insensitive_substring_over_named_fields(self):
        config = _visit_config()
        _seed_visits(config)
        _status, body = _post(config, {"alias": "answers", "search": {"text": "DRUGS", "fields": ["answer"]}})
        assert body["total"] == 3
        # A bare string searches every declared field; wildcards are literal.
        _status, body = _post(config, {"alias": "answers", "search": "%"})
        assert body["total"] == 0

    def test_order_and_page_without_overlap(self):
        config = _visit_config()
        _seed_visits(config)
        seen = []
        for offset in (0, 2, 4):
            _status, body = _post(config, {"alias": "answers", "order_by": ["-n"], "limit": 2, "offset": offset})
            assert body["total"] == 6
            seen += [r["n"] for r in body["rows"]]
        assert seen == [5, 4, 3, 2, 1, 0]

    def test_entity_stage_reads_the_entity_cache(self):
        config = AnalysisPipelineConfig(
            grouping_key="username",
            fields=[FieldComputation(name="state", path="row.state")],
            terminal_stage=CacheStage.ENTITY,
            linking_field="state",
            pipeline_id=PIPELINE_ID,
        )
        ComputedEntityCache.objects.bulk_create(
            ComputedEntityCache(
                opportunity_id=OPP,
                pipeline_id=PIPELINE_ID,
                config_hash=get_config_hash(config),
                visit_count=3,
                expires_at=timezone.now() + timedelta(hours=1),
                entity_id=s,
                total_visits=t,
                aggregated_fields={"state": s},
            )
            for s, t in (("Kebbi", 3), ("Borno", 2), ("Kano", 1))
        )
        _status, body = _post(config, {"alias": "answers", "order_by": "-total_visits", "limit": 2})
        assert body["total"] == 3
        assert [(r["entity_id"], r["total_visits"]) for r in body["rows"]] == [("Kebbi", 3), ("Borno", 2)]

    def test_rows_of_another_pipeline_or_opportunity_are_never_read(self):
        config = _visit_config()
        _seed_visits(config, rows=[("x", "Kebbi", "other opp")], opp=999)
        _seed_visits(config)
        _status, body = _post(config, {"alias": "answers", "search": "other opp"})
        assert body["total"] == 0


# --------------------------------------------------------------------------- refusals


class TestRefusals:
    def test_unknown_field_is_refused_wherever_it_appears(self):
        config = _visit_config()
        _seed_visits(config)
        for body in (
            {"filters": {"nope": 1}},
            {"search": {"text": "a", "fields": ["nope"]}},
            {"order_by": "-nope"},
        ):
            status, out = _post(config, {"alias": "answers", **body})
            assert status == 400 and "unknown field 'nope'" in out["error"]

    @pytest.mark.parametrize("limit", [0, 501, "x"])
    def test_limit_is_capped(self, limit):
        status, out = _post(_visit_config(), {"alias": "answers", "limit": limit})
        assert status == 400 and out["status"] == "error"

    def test_unknown_query_keys_are_refused(self):
        status, out = _post(_visit_config(), {"alias": "answers", "where": "1=1"})
        assert status == 400 and "unknown query keys" in out["error"]

    def test_no_workflow(self):
        status, out = _post(_visit_config(), {"alias": "answers"}, definition=False)
        assert status == 404

    def test_an_opportunity_the_workflow_does_not_span(self):
        status, out = _post(_visit_config(), {"alias": "answers", "opportunity_id": 4242})
        assert status == 403

    def test_an_alias_the_workflow_does_not_have(self):
        status, out = _post(_visit_config(), {"alias": "other"})
        assert status == 404

    def test_a_drive_pipeline_whose_own_stamp_does_not_verify(self, settings):
        settings.LABS_WORKFLOW_GDRIVE_ROOT_IDS = ["ROOT"]
        config = _visit_config(
            data_source=DataSourceConfig(type="gdrive", folder_id="folderA", authorization={"signature": "forged"})
        )
        _seed_visits(config)  # a warm cache is no permission to read it
        status, out = _post(config, {"alias": "answers"})
        assert status == 403 and out["error"].startswith("Google Drive source:")


# --------------------------------------------------------------------------- a cold cache


class TestColdCache:
    def setup_method(self):
        cache.clear()

    def test_a_cold_cache_is_warmed_in_the_background_once(self):
        config = _visit_config()
        with patch("connect_labs.workflow.tasks.warm_pipeline_query_cache") as task:
            status, out = _post(config, {"alias": "answers"})
            assert status == 202 and out["status"] == "warming" and out["retry_after_ms"] > 0
            _post(config, {"alias": "answers"})
        assert task.delay.call_count == 1
        kwargs = task.delay.call_args.kwargs
        assert kwargs["alias"] == "answers" and kwargs["opportunity_id"] == OPP
        assert kwargs["scope"] == {"opportunity_id": OPP}

    def test_a_failed_warm_is_reported_then_retried(self):
        from connect_labs.workflow.pipeline_query import warm_cache_key

        config = _visit_config()
        cache.set(warm_cache_key(OPP, config) + ":error", "Drive said no", 60)
        with patch("connect_labs.workflow.tasks.warm_pipeline_query_cache") as task:
            status, out = _post(config, {"alias": "answers"})
            assert status == 502 and "Drive said no" in out["error"]
            status, _out = _post(config, {"alias": "answers"})
            assert status == 202
        assert task.delay.call_count == 1

    def test_the_warm_task_runs_the_pipeline_and_releases_its_lock(self):
        from connect_labs.workflow import pipeline_query

        config = _visit_config()
        cache.set("lock", "warming", 60)
        wda = MagicMock()
        wda.get_definition.return_value = _definition()
        with (
            patch("connect_labs.workflow.data_access.WorkflowDataAccess", return_value=wda),
            patch("connect_labs.workflow.data_access.PipelineDataAccess"),
            patch(
                "connect_labs.workflow.views._resolve_pipeline_sources_for_run",
                return_value=(_definition().pipeline_sources, {"answers": config}),
            ),
            patch("connect_labs.labs.analysis.pipeline.AnalysisPipeline") as pipeline,
        ):
            out = pipeline_query.warm_pipeline_cache(
                "tok",
                definition_id=DEF_ID,
                alias="answers",
                opportunity_id=OPP,
                scope={"opportunity_id": OPP},
                lock_key="lock",
            )
        assert out == {"warmed": ["answers"]}
        pipeline.return_value.stream_analysis_ignore_events.assert_called_once_with(config, OPP)
        assert cache.get("lock") is None


# --------------------------------------------------------------------------- on-demand sources


class TestOnDemandSources:
    def test_add_pipeline_source_sets_keeps_and_clears_the_flag(self):
        stored = {"pipeline_sources": [{"pipeline_id": 1, "alias": "a"}]}
        wda = WorkflowDataAccess.__new__(WorkflowDataAccess)
        wda.get_definition = lambda _id: MagicMock(data=dict(stored))
        wda.update_definition = lambda _id, data: stored.update(data) or MagicMock(
            pipeline_sources=data["pipeline_sources"]
        )

        wda.add_pipeline_source(9, 1, "a", load="on_demand")
        assert stored["pipeline_sources"] == [{"pipeline_id": 1, "alias": "a", "load": "on_demand"}]
        wda.add_pipeline_source(9, 2, "a")  # a re-point keeps it
        assert stored["pipeline_sources"] == [{"pipeline_id": 2, "alias": "a", "load": "on_demand"}]
        wda.add_pipeline_source(9, 2, "a", load="eager")
        assert stored["pipeline_sources"] == [{"pipeline_id": 2, "alias": "a"}]
        with pytest.raises(ValueError):
            wda.add_pipeline_source(9, 2, "a", load="lazy")

    def test_an_on_demand_source_is_skipped_unless_an_eager_one_joins_it(self):
        sources = [
            {"alias": "answers", "pipeline_id": 1, "load": "on_demand"},
            {"alias": "big", "pipeline_id": 2, "load": "on_demand"},
            {"alias": "summary", "pipeline_id": 3},
        ]
        joins = MagicMock(joins=[MagicMock(from_alias="big")])
        execute, on_demand = streamed_and_skipped_aliases(sources, {"summary": joins})
        assert on_demand == {"answers", "big"}
        assert execute == {"summary", "big"}  # big still fills the cache summary JOINs
        assert is_on_demand_source(sources[0]) and not is_on_demand_source(sources[2])

    def test_the_stream_withholds_on_demand_rows(self):
        from connect_labs.workflow.views import PipelineDataStreamView

        class FakeMixin:
            def __init__(self):
                from connect_labs.labs.analysis.models import EntityRow

                self._pipeline_result = MagicMock(rows=[EntityRow(entity_id="4.12", total_visits=3)])
                self._pipeline_from_cache = True

            def stream_pipeline_events(self, *a, **k):
                return iter(())

        request = RequestFactory().get(f"/labs/workflow/api/{DEF_ID}/pipeline-data/stream/?opportunity_id={OPP}")
        request.user = MagicMock(is_authenticated=True)
        request.labs_context = {"opportunity_id": OPP}
        request.session = {"labs_oauth": {"access_token": "t"}}
        sources = [
            {"pipeline_id": PIPELINE_ID, "alias": "answers", "load": "on_demand"},
            {"pipeline_id": 23787, "alias": "by_question"},
        ]
        definition = _definition(sources=sources)
        pipeline_def = MagicMock(schema={"terminal_stage": "entity"})
        pipeline_def.name = "summary"
        with (
            patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA,
            patch("connect_labs.workflow.views.PipelineDataAccess"),
            patch("connect_labs.workflow.views._resolve_pipeline_definition", return_value=pipeline_def) as resolve,
            patch("connect_labs.workflow.views._resolve_pipeline_sources_for_run", return_value=(sources, {})),
            patch.object(PipelineDataStreamView, "_maybe_probe_cchq_access", return_value=iter(())),
            patch("connect_labs.labs.analysis.pipeline.AnalysisPipeline") as pipeline,
            patch("connect_labs.labs.analysis.sse_streaming.AnalysisPipelineSSEMixin", FakeMixin),
        ):
            MockWDA.return_value.get_definition.return_value = definition
            view = PipelineDataStreamView()
            view.kwargs = {"definition_id": DEF_ID}
            events = [json.loads(chunk[len("data: ") :]) for chunk in view.stream_data(request)]

        pipelines = [e for e in events if (e.get("data") or {}).get("pipelines")][-1]["data"]["pipelines"]
        assert pipelines["answers"]["rows"] == [] and pipelines["answers"]["metadata"]["on_demand"] is True
        assert len(pipelines["by_question"]["rows"]) == 1
        # The on-demand pipeline was never executed.
        assert pipeline.return_value.stream_analysis.call_count == 1
        assert [c.args[1] for c in resolve.call_args_list] == [23787]
