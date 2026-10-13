"""Workflows' render code in the sandbox (phase 2; spec 2026-10-12-page-sandbox-design.md).

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from unittest.mock import patch

import pytest
from django.test import override_settings

from connect_labs.workflow import sandbox
from connect_labs.workflow.data_access import WorkflowRunRecord
from connect_labs.workflow.templates import TEMPLATES
from connect_labs.workflow.tests.test_page_mode import _definition, _request
from connect_labs.workflow.tests.test_page_sandbox import HERE, LABS, _directives, _wda

WORKFLOW = {"name": "Weekly report", "config": {}}
OPTED_IN = {"name": "Weekly report", "config": {"sandbox": True}, "opportunity_ids": [10]}
OPTED_OUT = {"name": "Weekly report", "config": {"sandbox": False}}
RUN_503 = {
    "id": 503, "experiment": "w", "type": "r", "opportunity_id": None, "program_id": 25,
    "data": {"status": "in_progress", "definition_id": 47, "state": {"note": "kept"}},
}  # fmt: skip


class _Req:
    def __init__(self, query=None):
        self.GET = query or {}


@pytest.fixture
def checked_template(monkeypatch):
    """A template marked as checked in the frame, for the length of one test."""
    key = "audit_with_ai_review"
    monkeypatch.setitem(TEMPLATES, key, {**TEMPLATES[key], "sandboxed": True})
    return key


# --- the decision ----------------------------------------------------------------


@pytest.mark.parametrize(
    "data, query, expected",
    [
        (WORKFLOW, {}, False),
        (OPTED_IN, {}, True),
        (OPTED_OUT, {}, False),
        # ?sandbox=1 only ever turns it ON, for anyone, on any workflow...
        (WORKFLOW, {"sandbox": "1"}, True),
        (OPTED_OUT, {"sandbox": "1"}, True),
        # ...and nothing on the address turns it off.
        (OPTED_IN, {"sandbox": "0"}, True),
    ],
)
def test_a_workflow_is_sandboxed_when_it_opts_in(data, query, expected):
    assert sandbox.should_sandbox(_definition(data), request=_Req(query)) is expected


def test_a_workflow_that_follows_a_checked_template_is_sandboxed(checked_template):
    follows = {"name": "x", "config": {}, "render_source": {"template": checked_template}}
    follows_other = {"name": "x", "config": {}, "render_source": {"template": "kmc_opp_report"}}
    assert sandbox.should_sandbox(_definition(follows))
    assert not sandbox.should_sandbox(_definition(follows_other))
    # A workflow with its own copy of the render code is not vouched for by the template.
    copy = {"name": "x", "config": {}, "templateType": checked_template}
    assert not sandbox.should_sandbox(_definition(copy))


@override_settings(LABS_WORKFLOW_SANDBOX="all")
def test_all_sandboxes_every_workflow_whatever_it_says():
    assert sandbox.should_sandbox(_definition(WORKFLOW))
    assert sandbox.should_sandbox(_definition(OPTED_OUT))


@override_settings(LABS_WORKFLOW_SANDBOX="off")
def test_off_sandboxes_no_workflow_but_still_every_page():
    from connect_labs.workflow.tests.test_page_mode import PAGE

    assert not sandbox.should_sandbox(_definition(OPTED_IN), request=_Req({"sandbox": "1"}))
    assert sandbox.should_sandbox(_definition(PAGE))


@override_settings(LABS_WORKFLOW_SANDBOX="nonsense")
def test_an_unknown_rollout_value_is_the_default_never_off():
    assert sandbox.workflow_sandbox_mode() == "opt_in"
    assert sandbox.should_sandbox(_definition(OPTED_IN))


@override_settings(LABS_PAGE_SANDBOX=False, LABS_WORKFLOW_SANDBOX="all")
def test_the_emergency_switch_covers_workflows_too():
    assert not sandbox.should_sandbox(_definition(WORKFLOW))


def test_every_template_marked_checked_opts_its_new_instances_in():
    """A checked template writes config.sandbox on every new instance (its DEFINITION),
    so a copy made from it stays sandboxed even once it stops following the template."""
    for key, template in TEMPLATES.items():
        if template.get("sandboxed"):
            assert (template["definition"].get("config") or {}).get("sandbox") is True, key


# --- the policy --------------------------------------------------------------------


def test_the_frame_may_draw_map_tiles_but_still_reaches_no_labs_origin():
    d = _directives(sandbox.frame_policy(LABS))
    assert "https://*.tile.openstreetmap.org" in d["img-src"]
    for value in d["connect-src"] + d["img-src"]:
        assert not value.startswith(LABS), value
        assert value not in ("*", "https:", "'self'")
    assert not any("openstreetmap" in v or "cartocdn" in v for v in d["connect-src"])


# --- the run route ---------------------------------------------------------------


def _render_run(data, query):
    from connect_labs.workflow.views import WorkflowRunView

    request = _request("/labs/workflow/47/run/" + query)
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        wda = _wda(MockWDA, data)
        wda.get_run.return_value = WorkflowRunRecord(RUN_503)
        response = WorkflowRunView.as_view()(request, definition_id=47)
        response.render()
    return response


@pytest.mark.django_db
@pytest.mark.parametrize(
    "query", ["?program_id=25&run_id=503", "?program_id=25&edit=true", "?program_id=25&run_id=503&present=1"]
)
def test_a_sandboxed_workflow_is_drawn_by_the_host_on_every_route(query):
    response = _render_run(OPTED_IN, query)
    body = response.content.decode()
    assert response.status_code == 200, body[:500]
    assert response["Content-Security-Policy"].startswith(f"frame-src {HERE}/labs/sandbox/render/")
    assert f'"sandbox": {{"src": "/labs/sandbox/render/", "origin": "{HERE}", "mode": "workflow"}}' in body
    # Host mode: nothing on this page compiles or runs render code.
    for script in ("babel", "chart.umd.min.js", "mapbox-gl.js", "connect_map.js", "labs_audit_breakdown.js"):
        assert script not in body
    # A workflow is the viewer's own report: its workers and pipelines are handed in.
    assert "worker-one" in body
    assert '"streamPipelineData": "/labs/workflow/api/47/pipeline-data/stream/"' in body


@pytest.mark.django_db
def test_a_sandboxed_run_keeps_its_run_and_its_actions():
    body = _render_run(OPTED_IN, "?program_id=25&run_id=503").content.decode()
    assert '"note": "kept"' in body
    assert '"actionBase": "/labs/workflow/api/run/503/actions/"' in body


@pytest.mark.django_db
def test_a_workflow_left_out_runs_as_before():
    response = _render_run(WORKFLOW, "?program_id=25&run_id=503")
    body = response.content.decode()
    assert "Content-Security-Policy" not in response and '"sandbox"' not in body
    assert "chart.umd.min.js" in body


@pytest.mark.django_db
def test_sandbox_1_on_the_address_draws_any_workflow_sandboxed():
    response = _render_run(WORKFLOW, "?program_id=25&run_id=503&sandbox=1")
    assert '"mode": "workflow"' in response.content.decode()
    assert response["Content-Security-Policy"].startswith(f"frame-src {HERE}/labs/sandbox/render/")
