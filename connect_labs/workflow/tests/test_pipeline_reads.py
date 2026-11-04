"""A read answers from the cache or says a fill is under way. It never fills.

The contract of pipeline_reads.py, end to end except for the database behind the cache:
read, queue, fill, read. Design: docs/superpowers/specs/2026-11-03-one-loading-path-design.md
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from connect_labs.labs.analysis.config import AnalysisPipelineConfig
from connect_labs.labs.analysis.pipeline import EVENT_DOWNLOAD, EVENT_ERROR, EVENT_RESULT, EVENT_STATUS
from connect_labs.workflow import pipeline_reads, views
from connect_labs.workflow.pipeline_reads import fill_pipelines, read_pipeline_data, workflow_fill_slot

DEF_ID = 77
OPP = 523
OTHER = 524
SOURCES = [{"pipeline_id": 9, "alias": "visits"}]


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _config():
    return AnalysisPipelineConfig(grouping_key="username", fields=[], experiment="test", pipeline_id=9)


def _definition(opps=(OPP,), sources=SOURCES):
    return SimpleNamespace(
        id=DEF_ID, opportunity_ids=list(opps), pipeline_sources=sources, program_id=None, data={}, name="wf"
    )


def _request(path="pipeline-data/", **params):
    request = RequestFactory().get(f"/labs/workflow/api/{DEF_ID}/{path}", params)
    request.user = SimpleNamespace(pk=5, is_authenticated=True)
    request.session = {"labs_oauth": {"access_token": "tok"}}
    request.labs_context = {"opportunity_id": OPP, "opportunity": {"visit_count": 1000}}
    return request


class Harness:
    """The read's collaborators: `cached` is what the cache holds per opportunity."""

    def __init__(self, definition=None):
        self.definition = definition or _definition()
        self.cached = {}
        self.lenient = {}
        self.queued = []
        self.config = _config()

    def __enter__(self):
        record = MagicMock(schema={"fields": []})
        record.name = "Visits"
        analysis = MagicMock()
        analysis.read_cached.side_effect = lambda config, opp: self.cached.get(opp)
        analysis.get_cached_result_only.side_effect = lambda config, opp: self.lenient.get(opp)
        self.analysis = analysis
        task = MagicMock()
        task.delay.side_effect = lambda token, **kwargs: self.queued.append(kwargs)
        self.patches = [
            patch("connect_labs.workflow.data_access.WorkflowDataAccess"),
            patch("connect_labs.workflow.data_access.PipelineDataAccess"),
            patch("connect_labs.workflow.views._resolve_pipeline_definition", return_value=record),
            patch(
                "connect_labs.workflow.views._resolve_pipeline_sources_for_run",
                return_value=(self.definition.pipeline_sources, {"visits": self.config}),
            ),
            patch("connect_labs.labs.analysis.pipeline.AnalysisPipeline", return_value=analysis),
            patch("connect_labs.workflow.tasks.warm_pipelines", task),
            patch.object(pipeline_reads, "probe_cchq_access", return_value=None),
        ]
        started = [p.start() for p in self.patches]
        started[0].return_value.get_definition.return_value = self.definition
        self.wda = started[0].return_value
        self.pda = started[1]
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()

    def read(self, **params):
        return read_pipeline_data(_request(**params), DEF_ID)

    @property
    def slot(self):
        return workflow_fill_slot(DEF_ID, self.definition.opportunity_ids)


def _result(*usernames):
    return MagicMock(rows=[SimpleNamespace(username=u) for u in usernames], metadata={})


class TestARead:
    def test_a_warm_cache_answers_and_queues_nothing(self):
        with Harness() as h:
            h.cached[OPP] = _result("a", "b")
            answer = h.read()
        assert answer.warming is None
        assert [r["username"] for r in answer.data["visits"]["rows"]] == ["a", "b"]
        assert {r["opportunity_id"] for r in answer.data["visits"]["rows"]} == {OPP}
        assert answer.data["visits"]["metadata"]["per_opp"][str(OPP)]["from_cache"] is True
        assert h.queued == []

    def test_a_cold_cache_queues_one_fill_and_never_runs_a_pipeline_here(self):
        with Harness() as h:
            answer = h.read()
            assert answer.warming is not None and answer.data is None
            h.read()
        assert len(h.queued) == 1, "a second reader joins the fill"
        assert h.analysis.stream_analysis.call_count == 0
        assert h.analysis.stream_analysis_ignore_events.call_count == 0

    def test_a_poll_during_a_fill_costs_no_cache_read(self):
        """It is what a waiting page sends every few seconds."""
        with Harness() as h:
            h.read()
            before = h.analysis.read_cached.call_count
            assert h.read().warming is not None
        assert h.analysis.read_cached.call_count == before

    def test_one_cold_opportunity_of_several_means_waiting(self):
        with Harness(_definition(opps=(OPP, OTHER))) as h:
            h.cached[OPP] = _result("a")
            assert h.read().warming is not None
        assert h.queued[0]["opportunity_ids"] == [OPP, OTHER]

    def test_the_fill_carries_what_the_session_supplied(self):
        """Without these a fill and the read after it could disagree about cold."""
        with Harness() as h:
            h.read(tolerance="50")
        queued = h.queued[0]
        assert queued["user_id"] == 5
        assert queued["visit_count"] == 1000
        assert queued["params"] == {"tolerance": "50"}
        assert queued["scope"] == {"opportunity_id": OPP}

    def test_hq_refusing_is_answered_before_anything_is_queued(self):
        with Harness() as h:
            refused = pipeline_reads.PipelineAnswer(error="denied", status=403, details={"cchq_auth_required": True})
            pipeline_reads.probe_cchq_access.return_value = refused
            answer = h.read()
        assert answer.status == 403 and answer.details["cchq_auth_required"] is True
        assert h.queued == []

    def test_a_read_this_viewer_may_not_make_is_not_a_fill(self):
        with Harness() as h:
            h.analysis.read_cached.side_effect = PermissionError("not yours")
            answer = h.read()
        assert h.queued == []
        assert answer.data["visits"]["metadata"]["per_opp"][str(OPP)]["error"] == "not yours"


class TestAfterAFill:
    def test_the_data_it_filled_is_served_and_the_outcome_spent(self):
        with Harness() as h:
            h.slot.finish()
            h.cached[OPP] = _result("a")
            assert len(h.read().data["visits"]["rows"]) == 1
            assert h.slot.outcome() is None

    def test_a_failed_target_is_named_and_does_not_poll_forever(self):
        with Harness() as h:
            h.slot.finish("no export", report={"visits": {str(OPP): {"error": "no export"}}})
            answer = h.read()
        assert answer.warming is None and h.queued == []
        visits = answer.data["visits"]
        assert visits["rows"] == [] and visits["metadata"]["per_opp"][str(OPP)]["error"] == "no export"

    def test_a_drifted_visit_count_is_not_an_empty_report(self):
        """The fill ran; the count check still says cold. TTL decides, as it always did
        for a read that had just downloaded."""
        with Harness() as h:
            h.slot.finish()
            h.lenient[OPP] = _result("a", "b")
            answer = h.read()
        assert len(answer.data["visits"]["rows"]) == 2

    def test_no_data_at_all_ends_the_wait(self):
        with Harness() as h:
            h.slot.finish()
            answer = h.read()
        assert answer.data["visits"]["rows"] == [] and h.queued == []

    def test_an_auth_failure_is_lifted_to_the_alias(self):
        entry = {"error": "denied", "auth_error": "commcare_hq", "auth_error_domain": "d"}
        with Harness() as h:
            h.slot.finish("denied", report={"visits": {str(OPP): entry}})
            metadata = h.read().data["visits"]["metadata"]
        assert metadata["auth_error"] == "commcare_hq" and metadata["auth_error_domain"] == "d"

    def test_a_raw_fetch_anomaly_travels_from_the_worker_to_the_banner(self):
        anomaly = {"previous": 10, "fetched": 1}
        with Harness() as h:
            h.slot.finish(report={"visits": {str(OPP): {"raw_fetch_anomaly": anomaly}}})
            h.cached[OPP] = _result("a")
            metadata = h.read().data["visits"]["metadata"]
        assert metadata["raw_fetch_anomalies"] == [{"opportunity_id": str(OPP), **anomaly}]

    def test_after_the_outcome_is_spent_the_next_cold_read_fills_again(self):
        with Harness() as h:
            h.slot.finish()
            h.read()
            assert h.read().warming is not None
        assert len(h.queued) == 1


class TestRefresh:
    def test_refresh_fills_even_when_the_cache_is_warm(self):
        with Harness() as h:
            h.cached[OPP] = _result("a")
            assert h.read(refresh="1").warming is not None
        assert h.queued[0]["params"] == {"refresh": "1"}

    def test_it_is_satisfied_by_the_forced_fill_not_by_any_fill(self):
        with Harness() as h:
            h.cached[OPP] = _result("a")
            h.slot.finish(forced=False)
            assert h.read(refresh="1").warming is not None
            assert len(h.queued) == 1
            h.slot.finish(forced=True)
            assert h.read(refresh="1").warming is None
        assert len(h.queued) == 1


class TestTheFill:
    def _fill(self, events, *, config=None, aliases=None, opps=(OPP,), sources=SOURCES, configs=None, **kwargs):
        analysis = MagicMock()
        analysis.stream_analysis.side_effect = events if callable(events) else (lambda c, o: iter(events))
        slot = workflow_fill_slot(DEF_ID, opps)
        token = slot.claim()
        seen = []
        real_report = pipeline_reads.WarmSlot.report

        def report(self, message, **progress):
            seen.append((message, progress))
            real_report(self, message, **progress)

        definition = _definition(opps=opps, sources=sources)
        with (
            patch("connect_labs.workflow.data_access.WorkflowDataAccess") as wda,
            patch("connect_labs.workflow.data_access.PipelineDataAccess"),
            patch(
                "connect_labs.workflow.views._resolve_pipeline_sources_for_run",
                return_value=(sources, configs or {"visits": _config()}),
            ),
            patch("connect_labs.labs.analysis.pipeline.AnalysisPipeline", return_value=analysis) as pipeline,
            patch.object(pipeline_reads.WarmSlot, "report", report),
        ):
            wda.return_value.get_definition.return_value = definition
            out = fill_pipelines(
                "tok",
                definition_id=DEF_ID,
                scope={"opportunity_id": OPP},
                slot_key=slot.key,
                claim=token,
                aliases=aliases,
                opportunity_ids=list(opps),
                **kwargs,
            )
        return out, slot, seen, pipeline, analysis

    def test_it_reports_the_downloads_real_count(self):
        events = [
            (EVENT_STATUS, {"message": "Checking..."}),
            (EVENT_DOWNLOAD, {"rows": 3200, "total": 8900}),
            (EVENT_RESULT, MagicMock(metadata={})),
        ]
        _out, slot, seen, _pipeline, _analysis = self._fill(events)
        message, progress = seen[-1]
        assert message == "Fetching visits: 3,200 / 8,900 rows (35%)"
        assert progress["processed"] == 3200 and progress["total"] == 8900
        assert progress["stage_name"] == "visits" and progress["total_stages"] == 1
        assert slot.state() is None and slot.outcome()["error"] is None

    def test_one_bad_opportunity_does_not_cost_the_rest(self):
        def stream(config, opp):
            if opp == OPP:
                return iter([(EVENT_ERROR, {"message": "no export", "exception": ValueError("no export")})])
            return iter([(EVENT_RESULT, MagicMock(metadata={}))])

        out, slot, _seen, _pipeline, analysis = self._fill(stream, opps=(OPP, OTHER))
        assert analysis.stream_analysis.call_count == 2
        assert out["failed"] == [str(OPP)]
        outcome = slot.outcome()
        assert outcome["error"] == "no export"
        assert outcome["report"]["visits"][str(OPP)]["error"] == "no export"

    def test_an_anomaly_is_left_for_the_reader(self):
        anomaly = {"previous": 10, "fetched": 1}
        _out, slot, _seen, _pipeline, _analysis = self._fill(
            [(EVENT_RESULT, MagicMock(metadata={"raw_fetch_anomaly": anomaly}))]
        )
        assert slot.outcome()["report"]["visits"][str(OPP)] == {"raw_fetch_anomaly": anomaly}

    def test_it_reads_with_the_viewers_context(self):
        with patch("django.contrib.auth.get_user_model") as users:
            viewer = SimpleNamespace(pk=5, is_authenticated=True)
            users.return_value.objects.filter.return_value.first.return_value = viewer
            with patch(
                "connect_labs.labs.integrations.commcare.cchq_tokens.cchq_token_or_none", return_value="hq-token"
            ):
                _out, slot, _seen, pipeline, _analysis = self._fill(
                    [(EVENT_RESULT, MagicMock(metadata={}))],
                    user_id=5,
                    params={"refresh": "1", "tolerance": "50"},
                    visit_count=1000,
                )
        request = pipeline.call_args.args[0]
        assert pipeline.call_args.kwargs["cchq_access_token"] == "hq-token"
        assert request.user is viewer
        assert request.GET == {"refresh": "1", "tolerance": "50"}
        assert request.labs_context["opportunity"]["visit_count"] == 1000
        assert slot.outcome()["forced"] is True

    def test_on_demand_sources_are_left_out_unless_an_eager_one_joins_them(self):
        sources = [{"pipeline_id": 9, "alias": "visits"}, {"pipeline_id": 8, "alias": "big", "load": "on_demand"}]
        configs = {"visits": _config(), "big": _config()}
        _out, _slot, _seen, _pipeline, analysis = self._fill(
            lambda c, o: iter([(EVENT_RESULT, MagicMock(metadata={}))]), sources=sources, configs=configs
        )
        assert analysis.stream_analysis.call_count == 1

    def test_a_task_a_newer_fill_replaced_does_nothing(self):
        slot = workflow_fill_slot(DEF_ID, [OPP])
        slot.claim()
        out = fill_pipelines(
            "tok", definition_id=DEF_ID, scope={"opportunity_id": OPP}, slot_key=slot.key, claim="older"
        )
        assert out == {"superseded": True}


class TestTheEndpoints:
    def test_the_json_view_answers_202_then_the_data(self):
        with Harness() as h:
            response = views.get_pipeline_data_api(_request(), DEF_ID)
            assert response.status_code == 202
            body = json.loads(response.content)
            assert body["status"] == "warming" and body["retry_after_ms"] > 0
            assert body["progress"]["status"] == "running" and body["progress"]["message"] == body["message"]
            h.slot.finish()
            h.cached[OPP] = _result("a")
            response = views.get_pipeline_data_api(_request(), DEF_ID)
        assert response.status_code == 200
        assert json.loads(response.content)["visits"]["rows"][0]["username"] == "a"

    def test_a_refusal_keeps_its_status_and_its_details(self):
        with Harness() as h:
            pipeline_reads.probe_cchq_access.return_value = pipeline_reads.PipelineAnswer(
                error="denied", status=403, details={"cchq_auth_required": True, "authorize_url": "/x"}
            )
            response = views.get_pipeline_data_api(_request(), DEF_ID)
            assert h.queued == []
        assert response.status_code == 403
        assert json.loads(response.content) == {"error": "denied", "cchq_auth_required": True, "authorize_url": "/x"}

    def test_the_stream_relays_the_fill_then_sends_the_same_payload(self):
        with Harness() as h:

            def wait(_seconds):
                h.slot.finish()
                h.cached[OPP] = _result("a")

            h.queued_report = "Fetching visits: 10 rows..."
            view = views.PipelineDataStreamView()
            view.kwargs = {"definition_id": DEF_ID}
            with patch.object(views.time, "sleep", side_effect=wait):
                raw = list(view.stream_data(_request("pipeline-data/stream/")))
            json_body = json.loads(views.get_pipeline_data_api(_request(), DEF_ID).content)
        events = [json.loads(chunk[len("data: ") :]) for chunk in raw]
        assert events[0]["message"] == "Loading workflow configuration..."
        assert events[1]["message"] == pipeline_reads.COLD_PIPELINES_MESSAGE
        assert events[-1]["data"]["pipelines"] == json_body
