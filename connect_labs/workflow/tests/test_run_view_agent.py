"""The run page: a workflow's own actions for its buttons, and the agent panel only when it shares."""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory


def _request(run_id=503, extra=""):
    req = RequestFactory().get(f"/labs/workflow/47/run/?run_id={run_id}&program_id=25{extra}")
    req.session = {"labs_oauth": {"access_token": "stub-token"}}
    req.user = MagicMock(username="jane_okeke", is_authenticated=True, pk=1)
    req.labs_context = {"program_id": 25}
    return req


def _context(config: dict, extra: str = "", generated: bool = True):
    """The run page's context. ``generated``: whether the workflow's opps (10, 11) hold
    generated synthetic data -- over sensitive data no agent is offered (labs/canopy_panel.py)."""
    from connect_labs.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord
    from connect_labs.workflow.views import WorkflowRunView

    rc = MagicMock()
    rc.data = {"component_code": "function WorkflowUI() {}"}
    with (
        patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA,
        patch("connect_labs.workflow.views.get_org_data", return_value={}),
        patch("connect_labs.labs.synthetic.provenance.all_generated", return_value=generated),
    ):
        wda = MockWDA.return_value
        wda.get_definition.return_value = WorkflowDefinitionRecord(
            {
                "id": 47,
                "experiment": "workflows",
                "type": "WorkflowDefinition",
                "opportunity_id": None,
                "program_id": 25,
                "data": {"name": "Programme report", "opportunity_ids": [10, 11], "config": config},
            }
        )
        wda.get_render_code.return_value = rc
        wda.get_run.return_value = WorkflowRunRecord(
            {
                "id": 503,
                "experiment": "workflow_runs",
                "type": "WorkflowRun",
                "opportunity_id": None,
                "program_id": 25,
                "data": {"status": "in_progress", "definition_id": 47, "state": {}},
            }
        )
        wda.get_workers.side_effect = lambda oid: [{"username": f"w{oid}", "name": f"Worker {oid}"}]
        view = WorkflowRunView()
        view.request = _request(extra=extra)
        view.kwargs = {"definition_id": 47}
        return view.get_context_data()


COACH = {"key": "initiate_ai_coach", "type": "start_ocs_outreach", "label": "Initiate AI coach"}
SHARED = {"templateType": "indicator_programme_report", "agent": {"share": True}, "actions": [COACH]}


@pytest.mark.django_db
def test_a_shared_run_hands_the_agent_its_selection_never_its_rows(canopy_default_agent):
    context = _context(SHARED)
    state = context["canopy_panel"]["page_state"]
    assert state["resource"] == "labs-workflow://47/runs/503"
    # A hint for the agent; which tools it may call is labs' decision, not the page's.
    assert state["backing_tool"] == "workflow_run_indicators"
    assert "backing_tools" not in state
    # Worker KEYS, in the form every run tool takes them.
    assert state["visible_ids"] == ["10::w10", "11::w11"]
    # The run's OWN scope -- the scope its tools must be called with.
    assert state["filters"]["program_id"] == 25
    assert state["filters"]["run_id"] == 503
    assert "opportunity_id" not in state["filters"]


@pytest.mark.django_db
def test_a_workflows_actions_reach_its_buttons_whether_or_not_it_shares():
    for config in (SHARED, {"actions": [COACH]}):
        data = _context(config)["workflow_data"]
        assert data["actions"] == [
            {
                "key": "initiate_ai_coach",
                "type": "start_ocs_outreach",
                "label": "Initiate AI coach",
                "description": data["actions"][0]["description"],
            }
        ]
        assert data["apiEndpoints"]["actionBase"] == "/labs/workflow/api/run/503/actions/"


@pytest.mark.django_db
def test_a_shared_run_over_sensitive_data_gets_no_panel():
    # Production visits: no agent unless labs has cleared it for sensitive data, and today
    # none is (labs/canopy_panel.py). Not a panel that refuses -- no panel at all.
    context = _context(SHARED, generated=False)
    assert "canopy_panel" not in context
    assert context["workflow_data"]["actions"]  # the workflow's own buttons are unaffected


@pytest.mark.django_db
def test_a_run_whose_workflow_does_not_share_gets_no_panel():
    context = _context({"templateType": "indicator_programme_report", "actions": [COACH]})
    assert "canopy_panel" not in context


@pytest.mark.django_db
def test_a_presentation_link_offers_no_actions_and_no_agent():
    context = _context(SHARED, extra="&present=1")
    assert "canopy_panel" not in context
    assert "actions" not in context["workflow_data"]


# The "Ask an agent" launcher is a fixed bubble the canopy widget draws 16px from
# the bottom edge, 48px tall (canopy-widget chrome.ts `.dock` / `.launcher`), in a
# shadow root labs cannot reach. It covered report content (connect-labs#2578), so
# a page with a ready panel pads its end by the launcher's height so the last row
# can scroll clear of it.
LAUNCHER_INSET_PX, LAUNCHER_HEIGHT_PX = 16, 48


def _reserve(canopy_panel):
    from django.template.loader import render_to_string

    return render_to_string("workflow/_agent_launcher_reserve.html", {"canopy_panel": canopy_panel})


def test_a_ready_panel_pads_the_end_of_the_page_by_the_launcher():
    import re

    html = _reserve({"ready": True})
    assert "data-agent-launcher-reserve" in html
    reserve = int(re.search(r"--agent-launcher-reserve:\s*(\d+)px", html).group(1))
    assert reserve >= LAUNCHER_INSET_PX + LAUNCHER_HEIGHT_PX
    assert "#content { padding-bottom: var(--agent-launcher-reserve); }" in html
    assert "scroll-padding-bottom: var(--agent-launcher-reserve)" in html


def test_the_reserve_draws_nothing_over_the_page():
    # A fixed band at the bottom of the viewport blanked the end of the window at
    # every scroll position, so every report with a launcher looked cut off at a
    # fixed height. The reserve is padding only: no element, nothing fixed.
    html = _reserve({"ready": True})
    assert "<div" not in html
    assert "fixed" not in html


def test_no_launcher_no_reserve():
    # A panel the SDK could not configure draws no launcher (panel.html renders
    # nothing), so the page keeps its full height.
    assert "data-agent-launcher-reserve" not in _reserve({"ready": False})
    assert "data-agent-launcher-reserve" not in _reserve(None)


def test_both_pages_that_draw_the_panel_reserve_room_for_its_launcher():
    from pathlib import Path

    from django.conf import settings

    for name in ("run.html", "page.html"):
        src = (Path(settings.APPS_DIR) / "templates" / "workflow" / name).read_text()
        assert (
            '{% if canopy_panel %}{% include "workflow/_agent_launcher_reserve.html" %}'
            '{% include "canopy_host/panel.html" %}{% endif %}'
        ) in src, name
