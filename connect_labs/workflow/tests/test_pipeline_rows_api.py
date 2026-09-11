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
