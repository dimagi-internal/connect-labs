"""`case-story`: whether a case panel offers "Coach about this baby"."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory

from connect_labs.workflow import case_coaching as cc
from connect_labs.workflow.tests.test_case_coaching import faltering, steady_gain


def _request(**params):
    request = RequestFactory().get("/labs/workflow/api/5618/case-story/", params)
    request.user = MagicMock(is_authenticated=True)
    request.session = {"labs_oauth": {"access_token": "t"}}
    return request


def _call(rows, config=cc.KMC_CASE_COACHING, opps=(10042,), **params):
    from connect_labs.workflow import views

    wda = MagicMock()
    wda.get_definition.return_value = SimpleNamespace(
        opportunity_ids=list(opps), data={"config": {"case_coaching": config} if config else {}}, template_type=None
    )
    with (
        patch.object(views, "WorkflowDataAccess", return_value=wda),
        patch("connect_labs.workflow.case_visits.load_rows", return_value=rows),
        patch("connect_labs.workflow.case_finder.registry_constants", return_value=None),
    ):
        response = views.case_story_api(_request(**params), 5618)
    return json.loads(response.content), response.status_code


def test_a_case_with_a_story_says_which():
    out, status = _call(steady_gain(), rows_opportunity_id="10042", case_id="c1")
    assert status == 200
    assert (out["story"], out["label"], out["username"]) == (cc.THRIVING, "Baby is growing well", "flw_001")


def test_a_case_with_no_story_offers_nothing():
    out, _ = _call(faltering(hours=(8, 16)), rows_opportunity_id="10042", case_id="c3")
    assert out == {"story": None}


def test_only_the_workflows_own_opportunities():
    _, status = _call(steady_gain(), rows_opportunity_id="999", case_id="c1")
    assert status == 403


def test_a_workflow_without_case_coaching_offers_nothing():
    out, _ = _call(steady_gain(), config=None, rows_opportunity_id="10042", case_id="c1")
    assert out["story"] is None
