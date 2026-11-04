"""One worker's rows, filtered where the data lives.

The framework's default hands a render every pipeline's rows for every
opportunity the workflow spans. For the KMC worker review that is ~8,900 baby
rows and ~36,000 weighings -- about 30 MB -- streamed to the browser to show one
worker's ~250 cases: ~20s warm, minutes cold, on a 1-vCPU web task everything
else then queues behind (measured 2026-09-11). This endpoint answers the question
the page actually asks.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory

from connect_labs.labs.analysis.config import AnalysisPipelineConfig

DEF_ID = 19780


def _request(**params):
    request = RequestFactory().get("/labs/workflow/api/19780/pipeline-rows/", params)
    request.user = MagicMock(is_authenticated=True)
    request.session = {"labs_oauth": {"access_token": "t"}}
    request.labs_context = {"opportunity_id": params.get("opportunity_id")}
    return request


def _definition(sources=None, opps=(523, 524)):
    definition = MagicMock()
    definition.opportunity_ids = list(opps)
    definition.program_id = None
    definition.pipeline_sources = sources or [
        {"pipeline_id": 19776, "alias": "children"},
        {"pipeline_id": 19777, "alias": "visits"},
    ]
    return definition


ROWS = [
    {"entity_id": "babyA", "username": "flw_1", "weight_g": 900},
    {"entity_id": "babyB", "username": "flw_2", "weight_g": 950},
    {"baby_case_id": "babyC", "username": "flw_1", "weight_g": 1000},
]


class _PipelineRowObject:
    """An `AnalysisResult.rows` element: attributes, no mapping interface.

    `serialize_pipeline_row` reads it by `getattr`, and pipeline-declared fields
    arrive under `custom_fields`. Anything that filters it as though it were a
    dict fails on `.get`.
    """

    __slots__ = ("username", "entity_id", "custom_fields")

    def __init__(self, row: dict):
        self.username = row.get("username")
        self.entity_id = row.get("entity_id")
        self.custom_fields = {k: v for k, v in row.items() if k not in ("username", "entity_id")}


def _config():
    return AnalysisPipelineConfig(grouping_key="username", fields=[], experiment="test", pipeline_id=19776)


def _call(cached=None, definition=None, **params):
    from connect_labs.workflow import views

    pda = MagicMock()
    pda.get_definition.return_value = MagicMock(schema={"fields": []})
    pda._schema_to_config.return_value = _config()
    pda.get_cached_pipeline_result.return_value = cached
    wda = MagicMock()
    wda.get_definition.return_value = definition if definition is not None else _definition()
    with (
        patch.object(views, "WorkflowDataAccess", return_value=wda),
        patch.object(views, "PipelineDataAccess", return_value=pda),
        patch("connect_labs.workflow.tasks.warm_pipelines") as task,
    ):
        response = views.pipeline_rows_api(_request(**params), DEF_ID)
    pda.queued = task.delay
    return json.loads(response.content), response.status_code, pda


class TestItAnswersOnlyWhatWasAsked:
    def test_one_workers_rows(self):
        body, status, pda = _call(
            cached={"rows": ROWS, "metadata": {}}, alias="children", opportunity_id=523, username="flw_1"
        )
        assert status == 200
        assert [r.get("entity_id") or r.get("baby_case_id") for r in body["rows"]] == ["babyA", "babyC"]
        assert body["metadata"]["from_cache"] is True

    def test_one_cases_weighings(self):
        body, _status, _pda = _call(
            cached={"rows": ROWS, "metadata": {}}, alias="visits", opportunity_id=523, case_ids="babyC"
        )
        assert [r["baby_case_id"] for r in body["rows"]] == ["babyC"]

    def test_every_row_carries_its_opportunity(self):
        body, _status, _pda = _call(cached={"rows": ROWS, "metadata": {}}, alias="children", opportunity_id=523)
        assert {r["opportunity_id"] for r in body["rows"]} == {523}

    def test_only_that_opportunity_is_read(self):
        _body, _status, pda = _call(cached={"rows": ROWS, "metadata": {}}, alias="children", opportunity_id=524)
        assert pda.get_cached_pipeline_result.call_args.args[1] == 524

    def test_a_cold_cache_answers_202_and_fills_that_pipeline_for_that_opportunity_only(self):
        body, status, pda = _call(cached=None, alias="children", opportunity_id=523)
        assert status == 202 and body["status"] == "warming"
        assert pda.execute_pipeline.call_count == 0, "a read must never fill in the request"
        kwargs = pda.queued.call_args.kwargs
        assert kwargs["aliases"] == ["children"] and kwargs["opportunity_ids"] == [523]

    def test_a_fill_for_a_program_owned_workflow_finds_it_in_its_program(self):
        # The page asks for one opportunity's rows with only the program in its scope.
        # Looked up in that opportunity, the worker answered "workflow N not found".
        definition = _definition()
        definition.program_id = 217
        _body, status, pda = _call(cached=None, definition=definition, alias="children", rows_opportunity_id=524)
        assert status == 202
        kwargs = pda.queued.call_args.kwargs
        assert kwargs["definition_scope"] == {"program_id": 217}
        assert kwargs["opportunity_ids"] == [524]


class TestTheStreamRelaysTheSameRead:
    """`PipelineRowsStreamView`, for the renders that open it. It does no work: it waits
    on the same read as the JSON view, says what the fill says, and sends the same rows."""

    def _events(self, cached=(), fill=None, **params):
        """Drive `stream_data`. `cached` is what each read of the cache finds, and `fill`
        runs in place of the worker, given the slot; the wait between reads finishes it."""
        from connect_labs.workflow import views
        from connect_labs.workflow.cache_warm import WarmSlot

        pda = MagicMock()
        pda.get_definition.return_value = MagicMock(schema={"fields": []})
        pda._schema_to_config.return_value = _config()
        pda.get_cached_pipeline_result.side_effect = list(cached)
        wda = MagicMock()
        wda.get_definition.return_value = _definition()
        slots = []

        def queued(_token, **kwargs):
            slots.append(WarmSlot(kwargs["slot_key"], token=kwargs["claim"]))
            slots[-1].report("Fetching visits: 3,200 / 8,900 rows (35%)", processed=3200, total=8900)

        def wait(_seconds):
            (fill or (lambda slot: slot.finish()))(slots[-1])

        view = views.PipelineRowsStreamView()
        view.kwargs = {"definition_id": DEF_ID}
        with (
            patch.object(views, "WorkflowDataAccess", return_value=wda),
            patch.object(views, "PipelineDataAccess", return_value=pda),
            patch("connect_labs.workflow.tasks.warm_pipelines") as task,
            patch.object(views.time, "sleep", side_effect=wait),
        ):
            task.delay.side_effect = queued
            raw = list(view.stream_data(_request(**params)))
        self.queued = task.delay
        return [json.loads(chunk[len("data: ") :]) for chunk in raw]

    FOUND = {"rows": ROWS, "metadata": {}}

    def test_a_cold_read_says_what_the_fill_says_then_sends_the_rows(self):
        events = self._events(cached=[None, self.FOUND], alias="children", opportunity_id=523, username="flw_1")
        messages = [e["message"] for e in events]
        assert "Fetching visits: 3,200 / 8,900 rows (35%)" in messages
        assert messages.index("Fetching visits: 3,200 / 8,900 rows (35%)") < len(messages) - 1
        final = events[-1]["data"]
        assert [r.get("entity_id") or r.get("baby_case_id") for r in final["rows"]] == ["babyA", "babyC"]
        assert final["metadata"]["row_count"] == 2

    def test_it_fills_one_opportunity_not_the_cohort(self):
        self._events(cached=[None, self.FOUND], alias="children", opportunity_id=523, rows_opportunity_id=524)
        assert self.queued.call_args.kwargs["opportunity_ids"] == [524]

    def test_a_warm_read_reports_no_fill_and_queues_none(self):
        events = self._events(cached=[self.FOUND], alias="children", opportunity_id=523)
        assert not [e for e in events if e["message"].startswith("Fetching visits")]
        assert events[-1]["data"]["metadata"]["row_count"] == 3
        assert self.queued.call_count == 0

    def test_the_two_transports_select_the_same_rows(self):
        json_body, _status, _pda = _call(cached=self.FOUND, alias="children", opportunity_id=523, username="flw_1")
        events = self._events(cached=[self.FOUND], alias="children", opportunity_id=523, username="flw_1")
        assert events[-1]["data"] == json_body

    def test_the_stream_applies_the_case_filter_too(self):
        events = self._events(cached=[self.FOUND], alias="visits", opportunity_id=523, case_ids="babyC")
        assert [r["baby_case_id"] for r in events[-1]["data"]["rows"]] == ["babyC"]

    def test_a_rejected_query_is_an_error_event_not_a_crash(self):
        """The stream is already open, so the status travels in the event."""
        events = self._events(alias="", opportunity_id=523)
        assert events[-1]["error"] == "alias and opportunity_id are required"
        assert events[-1]["data"]["status"] == 400

    def test_an_opportunity_the_workflow_does_not_span_is_refused(self):
        events = self._events(alias="children", opportunity_id=999)
        assert events[-1]["data"]["status"] == 403
        assert not any(e.get("data", {}).get("rows") for e in events)

    def test_a_failed_fill_is_an_error_event_with_no_rows(self):
        events = self._events(
            cached=[None, None], fill=lambda slot: slot.finish("boom"), alias="children", opportunity_id=523
        )
        assert events[-1]["error"] == "boom" and events[-1]["data"]["status"] == 502
        assert not any(e.get("data", {}).get("rows") for e in events)


def test_the_review_prefers_the_stream_and_can_fall_back():
    """The render half: progress is a courtesy, the rows are not.

    A transport that cannot deliver here -- no EventSource, a buffering proxy, a
    dropped connection -- must not read as "this worker has no cases", which is
    exactly the shape the empty-state guards elsewhere in this file exist to
    prevent.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "templates" / "kmc_flw_review_render.js").read_text()
    assert "/pipeline-rows/stream/" in src, "the render no longer asks for progress"
    # Both row reads go through the helper, so neither can lose progress quietly.
    assert src.count("fetchRowsWithProgress(") == 3, "expected the definition plus both call sites"
    helper = src.split("function fetchRowsWithProgress(")[1].split("\n  }\n")[0]
    assert "EventSource" in helper
    assert helper.count("plain().then(resolve, reject)") == 4, (
        "four ways the stream can fail to deliver, all of which must still yield rows: "
        "no EventSource, construction throws, the stream errors, the connection drops"
    )
    # Progress must reach the empty states that used to be static labels.
    assert "childState.message" in src
    assert "visitState.message" in src
