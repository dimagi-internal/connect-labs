"""A tool only the person's View may call runs, through canopy, only on canopy's word.

App-only tools (``_meta.ui.visibility == ["app"]``: the coaching card's preview, the edit
card's review / Confirm / Decline) are the person's click. canopy hides them from the
agent, but labs does not take that on trust: a delegated call to one runs only with a
verified ``Canopy-View`` statement for THAT ``tools/call``, from that View, for the person
the token serves (``mcp/server.py`` ``view_denial``). No statement, an agent's statement,
or one for another call is refused before the tool runs.

Through labs' real page, mint, ``/o/token/`` and MCP, as canopy calls them.
THIS REPOSITORY IS PUBLIC. Every name and value here is invented.
"""

from __future__ import annotations

import pytest
from canopy_sdk import consumer, contract

from connect_labs.mcp import server
from connect_labs.mcp.tests import test_canopy_panel_end_to_end as _panel_e2e
from connect_labs.mcp.tests.test_delegation import RESOURCE, _run_mcp
from connect_labs.mcp.tests.test_labsdata_end_to_end import SENSITIVE_DECLARED, _view_statement
from connect_labs.mcp.tests.test_page_holds_end_to_end import _run_page_grant
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.mcp.ui import WORKFLOW_ACTION_PREVIEW_URI, WORKFLOW_EDIT_CONFIRM_URI

enabled = _panel_e2e.enabled
visitor = _panel_e2e.visitor

PREVIEW = {"run_id": 503, "program_id": 25, "action": "initiate_ai_coach", "arguments": {}}


def _call(client_creds, token, tool, arguments, *, view=None, agent=False):
    """One tools/call as canopy's gateway sends it: DPoP, and a statement bound to this very
    request -- the person's View (``view``: its subject and uri) or an agent's execution."""
    from config.asgi import build_application

    async def add_headers(request):
        request.headers["DPoP"] = client_creds.dpop_proof(request.method, RESOURCE, access_token=token)
        bound = contract.rpc_digest_of_body(request.content)
        if view is not None:
            request.headers[contract.VIEW_HEADER] = _view_statement(client_creds, token, rpc=bound, **view)
        if agent:
            request.headers[contract.EXECUTION_HEADER] = consumer.execution_statement(
                client_creds.client_key,
                client_creds.client_id,
                RESOURCE,
                SENSITIVE_DECLARED,
                jkt=consumer.dpop_jkt(client_creds.dpop_key),
                access_token=token,
                rpc=bound,
            )

    async def work(mcp_client):
        result = await mcp_client.call_tool(tool, arguments, raise_on_error=False)
        return result.is_error, " ".join(getattr(c, "text", "") or "" for c in result.content)

    return _run_mcp(build_application(), {"Authorization": f"DPoP {token}"}, add_headers, work)


def test_the_view_only_tools_are_the_cards_and_each_names_its_view():
    app_only = {name for name in ("workflow_action_preview_view",) if server.app_only(get_tool(name))}
    edit_card = {
        name
        for name in ("workflow_edit_review_view", "workflow_edit_apply", "workflow_edit_decline")
        if server.app_only(get_tool(name))
    }
    assert app_only == {"workflow_action_preview_view"}
    assert edit_card == {"workflow_edit_review_view", "workflow_edit_apply", "workflow_edit_decline"}
    assert server._view_uri(get_tool("workflow_action_preview_view")) == WORKFLOW_ACTION_PREVIEW_URI
    assert server._view_uri(get_tool("workflow_edit_apply")) == WORKFLOW_EDIT_CONFIRM_URI
    # The agent's own tools are not view-only.
    assert not server.app_only(get_tool("workflow_run_action"))
    assert not server.app_only(get_tool("workflow_edit_propose"))


@pytest.mark.django_db(transaction=True)
def test_a_view_only_tool_with_no_statement_is_refused(enabled, canopy_redeem, visitor):
    token = _run_page_grant(enabled, canopy_redeem, visitor)
    failed, text = _call(enabled, token, "workflow_action_preview_view", PREVIEW)
    assert failed and "canopy did not say this call is their View" in text and "no_statement" in text


@pytest.mark.django_db(transaction=True)
def test_an_agents_call_to_a_view_only_tool_is_refused(enabled, canopy_redeem, visitor):
    token = _run_page_grant(enabled, canopy_redeem, visitor)
    failed, text = _call(enabled, token, "workflow_action_preview_view", PREVIEW, agent=True)
    assert failed and "canopy did not say this call is their View" in text


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "view, code",
    [
        ({"uri": WORKFLOW_EDIT_CONFIRM_URI}, "wrong_resource"),  # another View's statement
        ({"uri": WORKFLOW_ACTION_PREVIEW_URI, "subject": "someone-else"}, "wrong_person"),
    ],
)
def test_a_view_statement_that_does_not_fit_is_refused(enabled, canopy_redeem, visitor, view, code):
    token = _run_page_grant(enabled, canopy_redeem, visitor)
    view = {"subject": visitor.pk, **view}
    failed, text = _call(enabled, token, "workflow_action_preview_view", PREVIEW, view=view)
    assert failed and code in text


@pytest.mark.django_db(transaction=True)
def test_the_persons_own_view_passes(enabled, canopy_redeem, visitor):
    token = _run_page_grant(enabled, canopy_redeem, visitor)
    view = {"subject": visitor.pk, "uri": WORKFLOW_ACTION_PREVIEW_URI}
    _failed, text = _call(enabled, token, "workflow_action_preview_view", PREVIEW, view=view)
    # Past the gate (whatever the tool then makes of these stand-in records).
    assert "their View" not in text and "not_the_persons_view" not in text
