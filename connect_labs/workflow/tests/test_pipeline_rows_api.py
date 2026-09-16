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


def _call(cached=None, executed=None, definition=None, **params):
    from connect_labs.workflow import views

    pda = MagicMock()
    pda.get_definition.return_value = MagicMock(schema={"fields": []})
    pda._schema_to_config.return_value = object()
    pda.get_cached_pipeline_result.return_value = cached
    pda.execute_pipeline.return_value = executed or {"rows": [], "metadata": {}}
    wda = MagicMock()
    wda.get_definition.return_value = definition if definition is not None else _definition()
    with (
        patch.object(views, "WorkflowDataAccess", return_value=wda),
        patch.object(views, "PipelineDataAccess", return_value=pda),
    ):
        response = views.pipeline_rows_api(_request(**params), DEF_ID)
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
        assert pda.execute_pipeline.call_count == 0

    def test_a_cold_cache_executes_that_pipeline_for_that_opportunity_only(self):
        _body, status, pda = _call(
            cached=None, executed={"rows": ROWS, "metadata": {}}, alias="children", opportunity_id=523
        )
        assert status == 200
        assert pda.execute_pipeline.call_count == 1
        assert pda.execute_pipeline.call_args.args[:2] == (19776, 523)

    def test_a_referenced_pipeline_is_read_where_it_lives(self):
        definition = _definition(sources=[{"pipeline_id": 19776, "alias": "children", "home_scope": {"public": True}}])
        _body, _status, pda = _call(
            cached={"rows": ROWS, "metadata": {}}, definition=definition, alias="children", opportunity_id=523
        )
        pda.use_sources.assert_called_once_with(definition.pipeline_sources)


class TestItRefusesTheRest:
    def test_alias_and_opportunity_are_required(self):
        _body, status, _pda = _call(alias="children")
        assert status == 400

    def test_an_unknown_alias(self):
        _body, status, _pda = _call(alias="nope", opportunity_id=523)
        assert status == 404

    def test_an_opportunity_the_workflow_does_not_span(self):
        _body, status, _pda = _call(alias="children", opportunity_id=999)
        assert status == 403

    def test_too_many_case_ids(self):
        from connect_labs.workflow.views import MAX_ROWS_CASE_IDS

        ids = ",".join(f"c{i}" for i in range(MAX_ROWS_CASE_IDS + 1))
        _body, status, _pda = _call(alias="visits", opportunity_id=523, case_ids=ids)
        assert status == 400

    def test_the_limit_is_capped_and_reported(self):
        many = [{"entity_id": f"b{i}", "username": "flw_1"} for i in range(50)]
        body, _status, _pda = _call(
            cached={"rows": many, "metadata": {}}, alias="children", opportunity_id=523, limit=10
        )
        assert len(body["rows"]) == 10 and body["metadata"]["truncated"] is True


class TestTheScopeAndTheRowsAreDifferentQuestions:
    """`opportunity_id` is the scope the workflow RECORD is read in; which opp's
    rows are wanted is a separate question.

    A multi-opp drill needs them to differ: the KMC worker review is owned by opp
    523 and opens a worker in opp 874. The render sent both under one name
    (`?opportunity_id=523&...&opportunity_id=874`), and `QueryDict.get` returns
    the LAST -- so `extract_context_from_url` scoped `request.labs_context` to
    874 and the definition was read in an opportunity that does not own it,
    answering "Workflow not found" (404) with a correct-looking URL.
    """

    def test_the_rows_opp_may_differ_from_the_scope(self):
        body, status, pda = _call(
            cached={"rows": ROWS, "metadata": {}},
            alias="children",
            opportunity_id=523,
            rows_opportunity_id=874,
            username="flw_1",
            definition=_definition(opps=(523, 524, 874)),
        )
        assert status == 200, body
        # Rows came from the opp that was asked for, not the scope.
        assert body["metadata"]["opportunity_id"] == 874
        assert all(r["opportunity_id"] == 874 for r in body["rows"])

    def test_it_still_honours_a_lone_opportunity_id(self):
        """Single-opp callers predate the split and must be unaffected."""
        body, status, _ = _call(
            cached={"rows": ROWS, "metadata": {}}, alias="children", opportunity_id=523, username="flw_1"
        )
        assert status == 200
        assert body["metadata"]["opportunity_id"] == 523

    def test_the_rows_opp_is_still_checked_against_what_the_workflow_spans(self):
        """The split must not become a way to read an opp the workflow has no claim to."""
        body, status, _ = _call(
            alias="children",
            opportunity_id=523,
            rows_opportunity_id=99999,
            definition=_definition(opps=(523, 524, 874)),
        )
        assert status == 403, body


def test_the_review_sends_the_scope_and_the_rows_opp_under_different_names():
    """The render half: one `opportunity_id` per URL, and the rows opp named
    separately -- otherwise the duplicate key silently re-scopes the request."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "templates" / "kmc_flw_review_render.js").read_text()
    assert "rows_opportunity_id=" in src, "the rows opp still rides on opportunity_id"
    # Neither pipeline-rows URL may append a second opportunity_id of its own.
    # Split on the CONCATENATION, which is what a URL build looks like: the
    # progress helper also names the path, to rewrite an already-built URL onto
    # the streaming transport, and that site builds nothing and must not be
    # scanned as though it did.
    builds = src.split("'/pipeline-rows/' +")[1:]
    assert len(builds) == 2, "expected exactly two pipeline-rows URL builds (cases, weighings)"
    for head in (chunk[:400] for chunk in builds):
        assert "'alias=" in head
        assert "&opportunity_id=" not in head, "a second opportunity_id re-scopes the whole request"


class TestRecordsAreReadWhereTheyLiveNotWhereTheRowsAre:
    """Three questions, historically one opportunity id.

    A pipeline RECORD lives with the workflow that references it (unless its
    source names a `home_scope`), while the ROWS wanted are some other spanned
    opportunity's. `PipelineDataAccess` splits these already -- the client's own
    scope governs `get_definition`, and `execute_pipeline` /
    `get_cached_pipeline_result` take the data opportunity as an explicit
    argument -- but this view built the client from the ROWS opp, so the record
    read went looking in an opportunity that does not own it.

    Verified against production 2026-09-14: pipeline 19776 reads as "KMC Case
    Properties (SQL)" in opp 523 (the workflow's own) and "No pipeline with id
    19776" in opp 874 (the worker's).
    """

    def _run(self, **params):
        from connect_labs.workflow import views

        pda = MagicMock()
        pda.get_definition.return_value = MagicMock(schema={"fields": []})
        pda._schema_to_config.return_value = object()
        pda.get_cached_pipeline_result.return_value = {"rows": ROWS, "metadata": {}}
        wda = MagicMock()
        wda.get_definition.return_value = _definition(opps=(523, 524, 874))
        with (
            patch.object(views, "WorkflowDataAccess", return_value=wda),
            patch.object(views, "PipelineDataAccess", return_value=pda) as pda_cls,
        ):
            response = views.pipeline_rows_api(_request(**params), DEF_ID)
        return json.loads(response.content), response.status_code, pda, pda_cls

    def test_the_pipeline_client_is_scoped_to_the_workflow_not_the_rows_opp(self):
        body, status, pda, pda_cls = self._run(
            alias="children", opportunity_id=523, rows_opportunity_id=874, username="flw_1"
        )
        assert status == 200, body
        assert pda_cls.call_args.kwargs["opportunity_id"] == 523, "the pipeline record is read in the rows opp"
        # ...while the rows themselves are still read for the opp that was asked for.
        assert pda.get_cached_pipeline_result.call_args.args[1] == 874

    def test_a_lone_opportunity_id_scopes_both(self):
        """Single-opp callers: the two coincide, so nothing changes for them."""
        _, status, pda, pda_cls = self._run(alias="children", opportunity_id=523, username="flw_1")
        assert status == 200
        assert pda_cls.call_args.kwargs["opportunity_id"] == 523
        assert pda.get_cached_pipeline_result.call_args.args[1] == 523


class TestTheStreamCarriesProgressWithoutTheCohort:
    """`PipelineRowsStreamView`: the same answer, with the fetch's progress first.

    The framework could always report a cold fetch -- `AnalysisPipelineSSEMixin`
    turns each page of the paginated Connect read into `Fetching visits: 3,200 /
    8,900 rows (36%)`. But it rode the same SSE stream as the ROWS, so this page,
    which opted out of that stream's ~30 MB payload (`config.noPipelineStream`),
    lost the progress with it and showed a static label for the 38.7s, 61.3s and
    50.7s reads measured live on 2026-09-16.

    So what is under test is the decoupling: progress events must arrive, and the
    final payload must still be the server-FILTERED rows, identical to what the
    one-shot transport returns for the same query.
    """

    def _events(self, pipeline_events, definition=None, **params):
        """Drive `stream_data` and return the parsed SSE payloads in order."""
        from connect_labs.workflow import views

        pda = MagicMock()
        pda.get_definition.return_value = MagicMock(schema={"fields": []})
        pda._schema_to_config.return_value = object()
        wda = MagicMock()
        wda.get_definition.return_value = definition if definition is not None else _definition()

        pipeline = MagicMock()
        pipeline.stream_analysis.return_value = iter(pipeline_events)
        view = views.PipelineRowsStreamView()
        view.kwargs = {"definition_id": DEF_ID}
        with (
            patch.object(views, "WorkflowDataAccess", return_value=wda),
            patch.object(views, "PipelineDataAccess", return_value=pda),
            patch("connect_labs.labs.analysis.pipeline.AnalysisPipeline", return_value=pipeline),
        ):
            raw = list(view.stream_data(_request(**params)))
        return [json.loads(chunk[len("data: ") :]) for chunk in raw], pipeline

    @staticmethod
    def _result(rows):
        """A pipeline result as `stream_analysis` really yields one.

        Its `.rows` are row OBJECTS, not dicts -- the JSON transport gets dicts
        from `execute_pipeline`, this one does not. Handing this helper dicts is
        what hid the bug that reached production: `row.get` on an object is None,
        so filtering raised "'NoneType' object is not callable" on the first warm
        read. The fake has to have the real shape or it proves nothing.
        """
        return MagicMock(rows=[_PipelineRowObject(r) for r in rows], metadata={})

    def _pipeline_events(self, rows, with_download=True):
        from connect_labs.labs.analysis.pipeline import EVENT_DOWNLOAD, EVENT_RESULT, EVENT_STATUS

        events = [(EVENT_STATUS, {"message": "Checking entity-level cache..."})]
        if with_download:
            events += [
                (EVENT_DOWNLOAD, {"rows": 3200, "total": 8900}),
                (EVENT_DOWNLOAD, {"rows": 8900, "total": 8900}),
            ]
        events.append((EVENT_RESULT, self._result(rows)))
        return events

    def test_a_cold_read_reports_the_fetch_as_a_percentage(self):
        events, _ = self._events(self._pipeline_events(ROWS), alias="children", opportunity_id=523, username="flw_1")
        messages = [e["message"] for e in events]
        assert "Fetching visits: 3,200 / 8,900 rows (35%)" in messages
        assert "Fetching visits: 8,900 / 8,900 rows (100%)" in messages

    def test_progress_arrives_before_the_rows(self):
        """A percentage that only lands with the payload is not progress."""
        events, _ = self._events(self._pipeline_events(ROWS), alias="children", opportunity_id=523)
        fetching = [i for i, e in enumerate(events) if e["message"].startswith("Fetching visits")]
        complete = [i for i, e in enumerate(events) if e.get("data")]
        assert fetching and complete
        assert max(fetching) < min(complete)

    def test_the_last_event_carries_the_filtered_rows(self):
        events, _ = self._events(self._pipeline_events(ROWS), alias="children", opportunity_id=523, username="flw_1")
        final = events[-1]["data"]
        assert [r.get("entity_id") or r.get("baby_case_id") for r in final["rows"]] == ["babyA", "babyC"]
        assert final["metadata"]["alias"] == "children"
        assert final["metadata"]["row_count"] == 2

    def test_it_streams_one_opportunity_not_the_cohort(self):
        """The reason the payload stays small: one opp, filtered server-side."""
        _events, pipeline = self._events(
            self._pipeline_events(ROWS), alias="children", opportunity_id=523, rows_opportunity_id=524
        )
        assert pipeline.stream_analysis.call_args.kwargs["opportunity_id"] == 524

    def test_a_warm_read_reports_no_fetch_progress(self):
        """Nothing was fetched, so nothing may claim to have been."""
        events, _ = self._events(
            self._pipeline_events(ROWS, with_download=False), alias="children", opportunity_id=523
        )
        assert not [e for e in events if e["message"].startswith("Fetching visits")]
        assert events[-1]["data"]["metadata"]["row_count"] == 3

    def test_the_two_transports_select_the_same_rows(self):
        """The stream is the JSON view with progress in front, or it is a fork.

        Parity is over WHICH rows are selected and what the metadata says -- not
        over the literal dicts. In production both paths emit
        `serialize_pipeline_row` output, but this file's JSON fixture stuffs raw
        dicts straight into the cache and so bypasses the serializer; comparing
        the two dicts whole would be comparing the fixture, not the code. The
        canonical shape is asserted on its own below.
        """
        json_body, _status, _pda = _call(
            cached={"rows": ROWS, "metadata": {}}, alias="children", opportunity_id=523, username="flw_1"
        )
        events, _ = self._events(
            self._pipeline_events(ROWS, with_download=False),
            alias="children",
            opportunity_id=523,
            username="flw_1",
        )
        streamed = events[-1]["data"]

        def identify(rows):
            return [(r.get("entity_id"), r.get("username"), r.get("weight_g")) for r in rows]

        assert identify(streamed["rows"]) == identify(json_body["rows"])
        # `from_cache` is the one field that legitimately differs: the JSON view
        # reads the processed cache itself, while the stream lets the pipeline
        # decide and report it.
        assert {k: v for k, v in streamed["metadata"].items() if k != "from_cache"} == {
            k: v for k, v in json_body["metadata"].items() if k != "from_cache"
        }

    def test_the_streamed_rows_are_serialized_not_raw_pipeline_objects(self):
        """The bug that reached production, pinned.

        `stream_analysis` yields row OBJECTS. Filtering them as dicts raised
        "'NoneType' object is not callable" (`row.get` is None on an object) on
        the very first warm read on labs. Every payload path must go through
        `serialize_pipeline_row`, which is why that function documents itself as
        the single producer of row dicts.
        """
        events, _ = self._events(
            self._pipeline_events(ROWS, with_download=False), alias="children", opportunity_id=523
        )
        rows = events[-1]["data"]["rows"]
        assert rows, "the stream returned nothing to check"
        for row in rows:
            assert isinstance(row, dict)
            # The canonical key set, including the two fields ace#1657 lost.
            for key in ("id", "username", "entity_id", "visit_date", "status", "flagged", "total_visits"):
                assert key in row, f"{key} missing: rows did not go through serialize_pipeline_row"
        # The pipeline's own declared field still survives the serialization,
        # and the framework's opportunity tag is stamped on every row.
        assert [r["weight_g"] for r in rows] == [900, 950, 1000]
        assert {r["opportunity_id"] for r in rows} == {523}

    def test_the_stream_applies_the_case_filter_too(self):
        events, _ = self._events(
            self._pipeline_events(ROWS, with_download=False),
            alias="visits",
            opportunity_id=523,
            case_ids="babyC",
        )
        assert [r["baby_case_id"] for r in events[-1]["data"]["rows"]] == ["babyC"]

    def test_a_rejected_query_is_an_error_event_not_a_crash(self):
        """The stream is already open, so the status travels in the event."""
        events, _ = self._events(self._pipeline_events(ROWS), alias="", opportunity_id=523)
        assert events[-1]["error"] == "alias and opportunity_id are required"
        assert events[-1]["data"]["status"] == 400

    def test_an_opportunity_the_workflow_does_not_span_is_refused(self):
        events, _ = self._events(self._pipeline_events(ROWS), alias="children", opportunity_id=999)
        assert events[-1]["data"]["status"] == 403
        assert not any(e.get("data", {}).get("rows") for e in events)

    def test_a_failing_pipeline_names_itself_and_yields_no_rows(self):
        from connect_labs.labs.analysis.pipeline import EVENT_ERROR

        events, _ = self._events(
            [(EVENT_ERROR, {"message": "boom", "exception": ValueError("boom")})],
            alias="children",
            opportunity_id=523,
        )
        assert events[-1]["error"] == "ValueError: boom"
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
