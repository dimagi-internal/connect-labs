"""The workflow run tools: read a run's grading, run its actions."""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

import connect_labs.mcp.tools.workflow_run as wa
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord
from connect_labs.workflow.models import WorkflowActionExecution

pytestmark = pytest.mark.django_db

PAYLOAD = {
    "cMeasures": [
        {"indicator": "wt", "label": "Weighed", "direction": "higher", "bands": [80, 60], "unit": "%"},
        {"indicator": "fu", "label": "Followed up", "direction": "higher", "bands": [90, 70], "unit": "%"},
    ],
    "programInd": {},
    "byLLO": [],
    "byOpp": [],
    "byFLW": [
        {
            "key": "10::asha",
            "opp": 10,
            "username": "asha",
            "llo": "Org A",
            "n": 30,
            "reds": 1,
            "rows": [0, 1],
            "ind": {"wt": {"band": "red", "value": 0.4, "n": 30}, "fu": {"band": "green", "value": 0.95, "n": 30}},
        },
        {
            "key": "10::binta",
            "opp": 10,
            "username": "binta",
            "llo": "Org A",
            "n": 22,
            "reds": 0,
            "rows": [],
            "ind": {"wt": {"band": "green", "value": 0.9, "n": 22}, "fu": {"band": "yellow", "value": 0.8, "n": 22}},
        },
    ],
    "cases": [{"entity_id": "c1"}, {"entity_id": "c2"}],
    "display": {"worker": "community health worker"},
}


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="manager", password="p")


COACH = {
    "key": "initiate_ai_coach",
    "type": "start_ocs_outreach",
    "label": "Initiate AI coach",
    "defaults": {"prompt": "Talk with them.", "bot": "bot-1"},
}


def _definition(share=True, actions=(COACH,), template_type="indicator_programme_report"):
    config = {"templateType": template_type, "agent": {"share": share}, "actions": list(actions)}
    return WorkflowDefinitionRecord(
        {
            "id": 7,
            "experiment": "workflows",
            "type": "WorkflowDefinition",
            "opportunity_id": None,
            "program_id": 25,
            "data": {"name": "Programme report", "templateType": template_type, "config": config},
        }
    )


def _completed_run():
    return WorkflowRunRecord(
        {
            "id": 70,
            "experiment": "workflow_runs",
            "type": "WorkflowRun",
            "opportunity_id": None,
            "program_id": 25,
            "data": {
                "definition_id": 7,
                "status": "completed",
                "state": {},
                "snapshot": {"state": {"snapshot": PAYLOAD}, "pipelines": {}, "workers": []},
            },
        }
    )


@pytest.fixture
def wda(monkeypatch):
    fake = MagicMock()
    fake.get_run.return_value = _completed_run()
    fake.get_definition.return_value = _definition()
    fake.access_token = "tok"
    monkeypatch.setattr(wa, "_wda_for_user", lambda user, opportunity_id=None, program_id=None: fake)
    monkeypatch.setattr(wa, "_delegated_token", lambda: None)
    return fake


def _call(name, user, **kwargs):
    return get_tool(name).handler(user=user, **kwargs)


def test_context_names_the_indicators_bands_and_the_workflows_actions(user, wda):
    out = _call("workflow_run_context", user, run_id=70, program_id=25)
    assert [i["id"] for i in out["indicators"]] == ["wt", "fu"]
    assert out["indicators"][0]["bands"] == [80, 60]
    assert [(a["key"], a["label"]) for a in out["actions"]] == [("initiate_ai_coach", "Initiate AI coach")]
    assert out["grading"]["source"] == "stored"
    assert out["page_url"] == "/labs/workflow/7/run/?run_id=70&program_id=25"
    assert out["nouns"] == {"worker": "community health worker"}


def test_red_workers_come_from_the_runs_own_grading(user, wda):
    out = _call("workflow_run_indicators", user, run_id=70, program_id=25, band="red")
    assert [w["key"] for w in out["workers"]] == ["10::asha"]
    assert out["workers"][0]["matched"] == ["wt"]
    assert "rows" not in out["workers"][0]


def test_asking_for_an_indicator_the_run_does_not_have_says_which_exist(user, wda):
    with pytest.raises(MCPToolError, match="known"):
        _call("workflow_run_indicators", user, run_id=70, program_id=25, indicators=["zz"])


def test_exactly_one_scope_is_required(user, wda):
    with pytest.raises(MCPToolError, match="exactly one"):
        _call("workflow_run_context", user, run_id=70)


def test_a_delegated_caller_is_refused_on_a_workflow_that_does_not_share(user, wda, monkeypatch):
    wda.get_definition.return_value = _definition(share=False)
    monkeypatch.setattr(wa, "_delegated_token", lambda: MagicMock(client_id="canopy"))
    with pytest.raises(MCPToolError, match="does not share"):
        _call("workflow_run_indicators", user, run_id=70, program_id=25)


def test_a_persons_own_agent_reads_a_workflow_that_does_not_share(user, wda):
    """Sharing is about the EMBEDDED agent; a PAT caller already has every tool."""
    wda.get_definition.return_value = _definition(share=False)
    out = _call("workflow_run_indicators", user, run_id=70, program_id=25)
    assert out["total"] == 2


# ---------------------------------------------------------------------------
# Running an action: preview, then commit
# ---------------------------------------------------------------------------


@pytest.fixture
def actionable(wda, monkeypatch):
    from connect_labs.workflow import actions

    wda.get_workers.side_effect = lambda oid: [
        {"username": "asha", "name": "Asha"},
        {"username": "binta", "name": "Binta"},
    ]
    wda.get_definition.return_value = _definition_with_opps()
    monkeypatch.setattr(actions, "_ocs_bots", lambda user, request: [{"id": "bot-1", "name": "Coach"}])
    monkeypatch.setattr(actions, "ocs_connected", lambda user: True)
    monkeypatch.setattr("connect_labs.labs.synthetic.registry.get_synthetic_opp", lambda opp: None)
    return wda


def _definition_with_opps():
    d = _definition()
    d.data["opportunity_ids"] = [10]
    return d


def test_without_confirm_the_tool_only_previews(user, actionable):
    out = _call(
        "workflow_run_action",
        user,
        run_id=70,
        program_id=25,
        action="initiate_ai_coach",
        arguments={"workers": [{"key": "10::asha", "prompt": "Weighing is red for you."}]},
    )
    assert out["needs"] == []
    [w] = out["workers"]
    assert (w["key"], w["name"], w["opportunity_id"]) == ("10::asha", "Asha", 10)
    # The person's own text is what the coach is told, inside the run's briefing.
    assert w["prompt"].startswith("BRIEFING (system text")
    assert w["prompt"].endswith("Programme team's note:\nWeighing is red for you.\n\nTalk with them.")
    assert w["opening"].startswith("Hello")
    assert "Nothing has been done" in out["next"]
    assert not WorkflowActionExecution.objects.exists()


def test_the_confirmed_call_queues_the_previewed_action_as_the_caller(actionable, django_capture_on_commit_callbacks):
    from unittest.mock import patch

    # The coaching token comes only from the View's own preview (as the viewer); off
    # canopy, only for a QA send.
    user = get_user_model().objects.create_user(username="jo", email="jo@dimagi.com", password="p")
    previewed = _call(
        "workflow_action_preview_view",
        user,
        run_id=70,
        program_id=25,
        action="initiate_ai_coach",
        arguments={"workers": [{"key": "10::asha"}]},
        deliver_to="jo.qa",
    )
    with patch("connect_labs.workflow.tasks.execute_workflow_action.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            out = _call(
                "workflow_run_action",
                user,
                run_id=70,
                program_id=25,
                action="initiate_ai_coach",
                arguments=previewed["arguments"],
                confirm=previewed["confirm"],
            )
    execution = WorkflowActionExecution.objects.get()
    assert out["execution"]["id"] == execution.pk
    assert (execution.user, execution.via, execution.run_id) == (user, "mcp", 70)
    delay.assert_called_once_with(execution.pk)


def test_a_canopy_call_is_recorded_as_canopy(user, actionable, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setattr(wa, "_delegated_token", lambda: MagicMock(client_id="canopy"))
    monkeypatch.setattr(wa, "_caller_actor", lambda: "ace")
    previewed = _call(
        "workflow_action_preview_view",
        user,
        run_id=70,
        program_id=25,
        action="initiate_ai_coach",
        arguments={"workers": [{"key": "10::asha"}]},
    )
    with patch("connect_labs.workflow.tasks.execute_workflow_action.delay"):
        _call(
            "workflow_run_action",
            user,
            run_id=70,
            program_id=25,
            action="initiate_ai_coach",
            arguments=previewed["arguments"],
            confirm=previewed["confirm"],
        )
    execution = WorkflowActionExecution.objects.get()
    assert (execution.via, execution.actor) == ("canopy", "ace")


def test_an_action_the_workflow_does_not_offer_is_refused(user, actionable):
    with pytest.raises(MCPToolError, match="no action"):
        _call("workflow_run_action", user, run_id=70, program_id=25, action="launch", arguments={"workers": []})


def test_a_forged_confirm_is_refused(user, actionable):
    with pytest.raises(MCPToolError, match="not a token"):
        _call(
            "workflow_run_action",
            user,
            run_id=70,
            program_id=25,
            action="initiate_ai_coach",
            arguments={"workers": [{"key": "10::asha"}]},
            confirm="forged",
        )


def test_status_reports_only_the_callers_runs_on_this_run(user, wda):
    mine = WorkflowActionExecution.objects.create(
        user=user,
        via="page",
        definition_id=7,
        run_id=70,
        action_key="k",
        action_type="create_task",
        arguments={"workers": []},
    )
    other = get_user_model().objects.create_user(username="other", password="p")
    WorkflowActionExecution.objects.create(
        user=other,
        via="page",
        definition_id=7,
        run_id=70,
        action_key="k",
        action_type="create_task",
        arguments={"workers": []},
    )
    out = _call("workflow_action_status", user, run_id=70, program_id=25)
    assert [e["id"] for e in out["executions"]] == [mine.pk]


def test_a_live_run_is_graded_now_and_reread_from_a_short_cache(user, wda, monkeypatch):
    from django.core.cache import cache

    cache.clear()
    live = _completed_run()
    live.data["status"] = "in_progress"
    live.data["snapshot"] = None
    wda.get_run.return_value = live
    built = MagicMock(
        return_value={"payload": {"state": {"snapshot": PAYLOAD}}, "opportunity_ids": [10], "opportunity_id": 10}
    )
    monkeypatch.setattr("connect_labs.workflow.snapshot_runtime.build_snapshot_for_run", built)
    monkeypatch.setattr("connect_labs.workflow.snapshot_runtime.cache_state", lambda ids: {"cold": False})

    first = _call("workflow_run_indicators", user, run_id=70, program_id=25, band="red")
    second = _call("workflow_run_context", user, run_id=70, program_id=25)

    assert first["source"] == "live"
    assert [w["key"] for w in first["workers"]] == ["10::asha"]
    assert second["grading"]["source"] == "live"
    built.assert_called_once()
    # Keyed by scope as well as run id (labs-local and production run ids overlap),
    # and by whether the call was restricted.
    assert "cases" not in cache.get(f"wf-agent-graded:v2:{user.pk}:p25:70:f")


def test_saving_an_invalid_action_declaration_is_refused_before_anything_is_read(user):
    tool = get_tool("workflow_update_definition")
    with pytest.raises(MCPToolError, match="not an action type"):
        tool.handler(
            user=user,
            workflow_id=7,
            program_id=25,
            expected_version=1,
            patch={"config": {"actions": [{"key": "x", "type": "send_sms"}]}},
        )


def test_worker_rows_carry_the_name_the_report_shows(user, wda):
    """An agent maps "Asha Banda" to a worker key without previewing everyone."""
    PAYLOAD["byFLW"][0]["name"] = "Asha Banda"
    try:
        out = _call("workflow_run_indicators", user, run_id=70, program_id=25, band="red")
    finally:
        PAYLOAD["byFLW"][0].pop("name")
    assert [(w["key"], w["name"]) for w in out["workers"]] == [("10::asha", "Asha Banda")]


def test_a_coaching_preview_briefs_each_worker_from_the_runs_grading(user, actionable):
    out = _call(
        "workflow_run_action",
        user,
        run_id=70,
        program_id=25,
        action="initiate_ai_coach",
        arguments={"workers": [{"key": "10::asha"}, {"key": "10::binta"}]},
    )
    asha, binta = out["workers"]
    assert "1. Weighed [wt] — 12 of 30 (40%), band red" in asha["prompt"]
    assert "1. Followed up [fu] — 18 of 22 (80%), band yellow" in binta["prompt"]
    assert asha["prompt"].endswith("Programme team's note:\nTalk with them.")
    assert asha["indicators"] == ["wt"]


# ---------------------------------------------------------------------------
# MCP Apps: a coaching send is the person's click, never the agent's call
# ---------------------------------------------------------------------------


@pytest.fixture
def canopy(monkeypatch):
    """The call comes through canopy for the person looking at the View (a delegated token)."""
    monkeypatch.setattr(wa, "_delegated_token", lambda: MagicMock(client_id="canopy"))


def _coach_preview(user, tool="workflow_run_action", **extra):
    return _call(
        tool,
        user,
        run_id=70,
        program_id=25,
        action="initiate_ai_coach",
        arguments={"workers": [{"key": "10::asha"}]},
        **extra,
    )


def test_the_agents_coaching_preview_carries_no_confirm(user, actionable):
    out = _coach_preview(user)
    assert "confirm" not in out and "confirm_expires_in" not in out
    assert out["sent_by"] == "click"
    assert "Nothing has been done" in out["next"]
    # It says where the person sends it from, and that the agent cannot.
    assert "Send on the card" in out["next"] and "Start coaching" in out["next"]
    assert "no `confirm` for you" in out["next"]
    assert out["page_url"] == "/labs/workflow/7/run/?run_id=70&program_id=25"
    # Everything else the person needs to see is still there.
    assert out["workers"][0]["briefing"]["topics"]
    assert out["workers"][0]["opening"].startswith("Hello")


def test_the_agent_cannot_turn_its_preview_into_a_send(user, actionable):
    """Calling again with its preview's own arguments just previews again: still no token."""
    out = _coach_preview(user)
    again = _call(
        "workflow_run_action",
        user,
        run_id=70,
        program_id=25,
        action="initiate_ai_coach",
        arguments=out["arguments"],
    )
    assert "confirm" not in again and "execution" not in again
    assert not WorkflowActionExecution.objects.exists()


def test_a_non_coaching_preview_still_gives_the_agent_its_token(user, actionable):
    task = {"key": "follow_up", "type": "create_task", "label": "Follow up", "defaults": {"title": "Follow up"}}
    definition = _definition_with_opps()
    definition.data["config"]["actions"] = [COACH, task]
    actionable.get_definition.return_value = definition
    out = _call(
        "workflow_run_action",
        user,
        run_id=70,
        program_id=25,
        action="follow_up",
        arguments={"workers": [{"key": "10::asha"}]},
    )
    assert out["confirm"] and "sent_by" not in out


def test_the_view_preview_has_the_picture_inline_and_the_viewers_token(user, actionable, canopy):
    import base64

    out = _coach_preview(user, tool="workflow_action_preview_view")
    assert out["confirm"] and out["confirm_expires_in"] > 0
    assert out["sent_by"] == "click"
    # The picture goes with the conversation the View sends.
    assert out["arguments"]["include_image"] is True
    image = out["workers"][0]["image"]
    assert image["data_uri"].startswith("data:image/png;base64,")
    png = base64.b64decode(image["data_uri"].split(",", 1)[1])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) == image["bytes"] <= wa.VIEW_IMAGE_MAX_BYTES
    assert image["caption"].startswith("A bar chart of")
    assert out["executions"] == []


def test_the_view_previews_text_leaves_out_the_picture_and_the_token(user, actionable, canopy):
    from connect_labs.mcp.tool_registry import get_tool as registry_tool

    out = _coach_preview(user, tool="workflow_action_preview_view")
    text = registry_tool("workflow_action_preview_view").text(out)
    assert "data:image" not in text and out["confirm"] not in text
    assert "inline_png_bytes" in text


def test_the_view_sends_what_it_showed(user, actionable, django_capture_on_commit_callbacks, canopy):
    from unittest.mock import patch

    shown = _coach_preview(user, tool="workflow_action_preview_view")
    with patch("connect_labs.workflow.tasks.execute_workflow_action.delay"):
        with django_capture_on_commit_callbacks(execute=True):
            sent = _call(
                "workflow_run_action",
                user,
                run_id=70,
                program_id=25,
                action="initiate_ai_coach",
                arguments=shown["arguments"],
                confirm=shown["confirm"],
            )
    execution = WorkflowActionExecution.objects.get()
    assert sent["execution"]["id"] == execution.pk
    assert execution.arguments["include_image"] is True
    # A reload of the View shows it was sent.
    again = _coach_preview(user, tool="workflow_action_preview_view")
    assert [e["id"] for e in again["executions"]] == [execution.pk]


def test_the_views_qa_send_is_refused_to_anyone_not_dimagi_staff(user, actionable):
    with pytest.raises(MCPToolError, match="only available to Dimagi staff") as e:
        _coach_preview(user, tool="workflow_action_preview_view", deliver_to="someone.qa")
    assert e.value.code == "PERMISSION_DENIED"


def test_the_views_qa_send_previews_to_the_staff_member(actionable, canopy):
    staff = get_user_model().objects.create_user(username="jo", email="jo@dimagi.com", password="p")
    out = _coach_preview(staff, tool="workflow_action_preview_view", deliver_to="  jo.qa ")
    assert out["arguments"]["deliver_to"] == "jo.qa"
    assert out["deliver_to"] == "jo.qa" and out["confirm"]
    assert out["workers"][0]["sending_to"].startswith("sending to jo.qa")
    # An empty deliver_to clears one the agent's arguments carried.
    cleared = _call(
        "workflow_action_preview_view",
        staff,
        run_id=70,
        program_id=25,
        action="initiate_ai_coach",
        arguments={"workers": [{"key": "10::asha"}], "deliver_to": "jo.qa"},
        deliver_to="",
    )
    assert "deliver_to" not in cleared["arguments"]


def test_off_canopy_the_views_preview_reaches_no_worker(user, actionable):
    """An agent's own MCP connection can name the app-only tool, but gets no token for a
    worker: that takes a person's click on canopy's card or the Labs page."""
    with pytest.raises(MCPToolError, match="only by a person's click") as e:
        _coach_preview(user, tool="workflow_action_preview_view")
    assert e.value.code == "PERMISSION_DENIED"


def test_off_canopy_the_views_preview_still_previews_a_qa_send(actionable):
    staff = get_user_model().objects.create_user(username="ace", email="ace@dimagi.com", password="p")
    out = _coach_preview(staff, tool="workflow_action_preview_view", deliver_to="ace.test")
    assert out["arguments"]["deliver_to"] == "ace.test" and out["confirm"]


def test_the_view_preview_says_whether_the_viewer_has_connected_ocs(user, actionable, canopy, monkeypatch):
    from connect_labs.workflow import actions

    out = _coach_preview(user, tool="workflow_action_preview_view")
    assert out["ocs"]["connected"] is True
    assert out["ocs"]["connect_url"].endswith("/labs/ocs/initiate/")
    monkeypatch.setattr(actions, "ocs_connected", lambda user: False)
    assert _coach_preview(user, tool="workflow_action_preview_view")["ocs"]["connected"] is False
