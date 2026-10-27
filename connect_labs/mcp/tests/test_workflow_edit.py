"""An agent's edit is proposed, shown, and saved only by a person's Confirm (mcp/tools/workflow_edit.py)."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs import canopy
from connect_labs.labs.models import UserConnectToken
from connect_labs.mcp.models import MCPAuditLog, StagedEdit
from connect_labs.mcp.testing import call_tool, mint_token
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.mcp.tools import workflow_edit
from connect_labs.mcp.ui import RESOURCES, WORKFLOW_EDIT_CONFIRM_URI
from connect_labs.users.models import User

CODE = "function Workflow() {\n  return <div>Old title</div>;\n}\n"
PATCH = {
    "workflow_id": 42,
    "opportunity_id": 100,
    "search": "Old title",
    "replace": "New title",
    "expected_version": 7,
}


def _person(username):
    user = User.objects.create(username=username)
    _, raw = mint_token(user, name="t")
    UserConnectToken.objects.create(
        user=user, access_token="connect-tok", expires_at=timezone.now() + timedelta(hours=1)
    )
    return user, raw


@pytest.fixture
def person(db):
    return _person("editor")


@pytest.fixture
def wda():
    mock = MagicMock()
    mock.get_render_code.return_value = MagicMock(version=7, component_code=CODE)
    mock.save_render_code.return_value = MagicMock(version=8)
    mock.get_definition.return_value = MagicMock(data={"name": "Visits", "config": {"sandbox": True}})
    with (
        patch("connect_labs.workflow.data_access.WorkflowDataAccess", return_value=mock),
        patch("connect_labs.mcp.tools.workflows.WorkflowDataAccess", return_value=mock),
        patch("connect_labs.mcp.tools.workflows._refuse_if_render_follows"),
        patch("connect_labs.workflow.sandbox.should_sandbox", return_value=True),
    ):
        yield mock


def _ok(response):
    result = response["result"]
    assert not result["isError"], result
    return result["structuredContent"]


def _error(response):
    result = response["result"]
    assert result["isError"], result
    return result["structuredContent"]["error"]


def _propose(raw, tool="workflow_patch_render_code", arguments=PATCH):
    return call_tool(raw, "workflow_edit_propose", {"tool": tool, "arguments": arguments})


def test_a_proposal_saves_nothing_and_says_so(person, wda):
    _, raw = person
    response = _propose(raw)
    out = _ok(response)
    assert out["state"] == "pending"
    assert "NOT saved" in response["result"]["content"][0].text
    wda.save_render_code.assert_not_called()
    edit = StagedEdit.objects.get(pk=out["change_id"])
    assert "+  return <div>New title</div>;" in edit.preview["changes"][0]["diff"]


def test_the_card_finds_the_edit_from_the_proposal_input_and_confirm_saves_it(person, wda):
    user, raw = person
    _propose(raw)
    # A sensitive conversation keeps no tool result: the card has only the proposal's input.
    card = _ok(call_tool(raw, "workflow_edit_review_view", {"tool": "workflow_patch_render_code", "arguments": PATCH}))
    assert card["confirm"] and card["state"] == "pending"

    out = _ok(call_tool(raw, "workflow_edit_apply", {"change_id": card["change_id"], "confirm": card["confirm"]}))
    assert out["state"] == "applied"
    saved = wda.save_render_code.call_args.kwargs
    assert "New title" in saved["component_code"] and saved["version"] == 8
    log = MCPAuditLog.objects.get(user=user, tool_name="workflow_patch_render_code")
    assert (log.success, log.version_before, log.version_after) == (True, 7, 8)

    again = _error(call_tool(raw, "workflow_edit_apply", {"change_id": card["change_id"], "confirm": card["confirm"]}))
    assert again["code"] == "CONFLICT", "a confirmed edit saves once"
    assert wda.save_render_code.call_count == 1


def test_decline_saves_nothing(person, wda):
    _, raw = person
    change_id = _ok(_propose(raw))["change_id"]
    card = _ok(call_tool(raw, "workflow_edit_review_view", {"change_id": change_id}))
    assert (
        _ok(call_tool(raw, "workflow_edit_decline", {"change_id": change_id, "confirm": card["confirm"]}))["state"]
        == "declined"
    )
    assert _error(call_tool(raw, "workflow_edit_apply", {"change_id": change_id, "confirm": card["confirm"]}))
    wda.save_render_code.assert_not_called()


def test_a_token_is_good_only_for_its_own_person_and_edit(person, wda):
    _, raw = person
    change_id = _ok(_propose(raw))["change_id"]
    other, other_raw = _person("someone-else")
    assert _error(call_tool(other_raw, "workflow_edit_review_view", {"change_id": change_id}))["code"] == "NOT_FOUND"
    assert (
        _error(call_tool(raw, "workflow_edit_apply", {"change_id": change_id, "confirm": "made-up"}))["code"]
        == "PERMISSION_DENIED"
    )
    wda.save_render_code.assert_not_called()


def test_render_code_outside_the_sandbox_is_refused(person, wda):
    _, raw = person
    with patch("connect_labs.workflow.sandbox.should_sandbox", return_value=False):
        err = _error(_propose(raw))
    assert err["code"] == "PERMISSION_DENIED" and "sandbox" in err["message"]
    assert not StagedEdit.objects.exists()


@pytest.mark.parametrize(
    "patch_,named",
    [
        ({"config": {"actions": []}}, "config.actions"),
        ({"config": {"agent": {"agent": "site"}}}, "config.agent"),
        ({"config": {"sandbox": False}}, "config.sandbox"),
        ({"workflow_sources": []}, "workflow_sources"),
        ({"kind": "page"}, "kind"),
    ],
)
def test_definition_changes_that_send_data_elsewhere_are_refused(person, wda, patch_, named):
    _, raw = person
    err = _error(
        _propose(
            raw,
            "workflow_update_definition",
            {"workflow_id": 42, "opportunity_id": 100, "patch": patch_, "expected_version": 3},
        )
    )
    assert err["code"] == "PERMISSION_DENIED" and named in err["message"]


def test_a_definition_change_shows_before_and_after(person, wda):
    _, raw = person
    out = _ok(
        _propose(
            raw,
            "workflow_update_definition",
            {"workflow_id": 42, "opportunity_id": 100, "patch": {"name": "Home visits"}, "expected_version": 3},
        )
    )
    diff = StagedEdit.objects.get(pk=out["change_id"]).preview["changes"][0]["diff"]
    assert '-"Visits"' in diff and '+"Home visits"' in diff


@pytest.mark.parametrize(
    "tool", ["pipeline_set_shared", "workflow_clone", "workflow_template_publish", "workflow_delete"]
)
def test_only_edits_are_proposable(person, wda, tool):
    _, raw = person
    err = _error(_propose(raw, tool, {"opportunity_id": 100}))
    assert err["code"] in ("PERMISSION_DENIED", "INVALID_SCHEMA")


def test_every_proposable_tool_is_a_write_and_none_is_in_a_delegated_scope():
    for name in workflow_edit.EDIT_TOOLS:
        spec = get_tool(name)
        assert spec is not None and spec.is_write, name
    delegated = set().union(*canopy.SCOPE_TOOLS.values())
    assert not (workflow_edit.EDIT_TOOLS & delegated), "an edit tool must be reached through a proposal"


def test_only_propose_is_the_agents_and_the_card_is_served():
    assert get_tool("workflow_edit_propose").meta["ui"]["visibility"] == ["model", "app"]
    for name in ("workflow_edit_review_view", "workflow_edit_apply", "workflow_edit_decline"):
        assert get_tool(name).meta["ui"]["visibility"] == ["app"], name
    (card,) = (r for r in RESOURCES if r.uri == WORKFLOW_EDIT_CONFIRM_URI)
    html = card.html()
    assert "workflow_edit_apply" in html and "http" not in html.split("<script>")[1].replace("https://", "")
