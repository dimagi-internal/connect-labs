"""The canopy agent panel end to end, as far as labs' side of it goes.

For a PAGE and for a WORKFLOW, drawn sandboxed and not, through labs' real code:

1. the route renders the panel (host chrome: the SDK's ``canopy_host/panel.html``,
   outside any frame) with the page state canopy will read back as ``current_page`` /
   ``read_page_state``: the resource, the backing tool, the selection, the filters;
2. the panel's token URL mints, through the SDK's ``panel_token`` view, an arrival
   whose ID-JAG carries the page's scopes and nothing else (``page:read`` for a page,
   wherever it is drawn; ``workflow:read`` + ``workflow:act`` for a run);
3. canopy redeems that ID-JAG at labs' ``/o/token/`` (the conformance plugin's client:
   ``private_key_jwt`` + DPoP), and with the token calls labs' MCP as the visitor:
   only the scope's tools are listed, and ``page_context`` called with the page
   state's filters answers what the page is built from.

canopy itself is stubbed only where it is canopy: the contact-token mint
(``mint_contact_token``, an HTTPS call to canopy) and the client documents labs
fetches (the plugin's). Everything labs does is real.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from canopy_sdk import contract
from canopy_sdk.jose import unverified_claims
from django.test import Client, RequestFactory, override_settings
from django.urls import resolve

from connect_labs.mcp.tests.test_delegation import BASE, ISSUER, RESOURCE, TOKEN_ENDPOINT, _host_key, _run_mcp
from connect_labs.testing.canopy_agents import CONFORMANCE_CANOPY
from connect_labs.workflow.tests.test_page_mode import ORG_DATA, PAGE, _definition

SHARING_PAGE = {**PAGE, "config": {"agent": {"share": True}}}
SHARING_WORKFLOW = {
    "name": "Programme report",
    "opportunity_ids": [10],
    "config": {"agent": {"share": True}},
}


@pytest.fixture
def enabled(settings, canopy_client, canopy_client_documents, canopy_agents):
    """Labs with the grant on, trusting the conformance plugin's canopy client (as in
    test_delegation): canopy's client documents answer from the plugin."""
    from django.core.cache import cache

    settings.LABS_PUBLIC_URL = BASE
    settings.ALLOWED_HOSTS = ["labs.example.org", "testserver"]
    settings.CANOPY_BASE_URL = CONFORMANCE_CANOPY  # canopy's client id is derived from it
    settings.CANOPY_APP_NAME = "connect-labs"
    settings.CANOPY_SIGNING_KEY = _host_key()
    canopy_agents("ace", acts_as_visitor=True)
    cache.clear()
    with mock.patch("canopy_sdk.fetch.get_json", side_effect=lambda url, **kw: canopy_client_documents[url]):
        yield canopy_client
    cache.clear()


@pytest.fixture
def visitor(db):
    from connect_labs.users.models import User

    return User.objects.create(username="gillian", email="g@example.invalid")


def _request(path, user, context):
    request = RequestFactory().get(path)
    request.session = {"labs_oauth": {"access_token": "stub-token", "organization_data": ORG_DATA}}
    request.user = user
    request.labs_context = context
    # What the SDK signs the page token for: the route that resolved.
    request.resolver_match = resolve(path.split("?")[0])
    return request


def _wda(MockWDA, data):
    from connect_labs.workflow.data_access import WorkflowRunRecord

    wda = MockWDA.return_value
    wda.get_definition.return_value = _definition(data)
    wda.get_render_code.return_value = mock.MagicMock(data={"component_code": "function WorkflowUI() {}"})
    wda.get_workers.return_value = [{"username": "worker-one", "name": "Worker One"}]
    wda.get_run.return_value = WorkflowRunRecord(
        {"id": 503, "experiment": "workflow", "type": "workflow_run", "opportunity_id": None, "program_id": 25,
         "data": {"status": "in_progress", "definition_id": 47, "state": {}}}
    )  # fmt: skip
    return wda


def _panel(view, request, data, **kwargs):
    """Render the route and return its rendered HTML and the panel's context."""
    with mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        _wda(MockWDA, data)
        response = view.as_view()(request, **kwargs)
        response.render()
    return response, response.context_data.get("canopy_panel")


def _json_script(html: str, element_id: str):
    start = html.index(f'id="{element_id}"')
    start = html.index(">", start) + 1
    return json.loads(html[start : html.index("</script>", start)])


def _mint(panel, user) -> dict:
    """POST the panel's own token URL, as the browser does; return the arrival canopy
    would be sent (its ID-JAG decoded)."""
    from canopy_sdk.django import views as sdk_views

    sent = {}

    def mint(config, payload, **kw):
        sent.update(payload)
        return {"token": "canopy-contact-token", "expires_at": "", "kind": "contact"}

    url = panel["options"]["tokenUrl"]
    request = RequestFactory().post(url)
    request.user = user
    # The view the URL routes to: labs' mint (labs/canopy_token.py), which records the page.
    view = resolve(url.split("?")[0]).func
    with mock.patch.object(sdk_views, "mint_contact_token", mint):
        response = view(request)
    assert response.status_code == 200, response.content
    assert json.loads(response.content)["token"] == "canopy-contact-token"
    return sent


def _scopes(id_jag: str) -> set[str]:
    claims = unverified_claims(id_jag)
    return set(str(claims.get("scope") or "").split())


def _redeem(canopy_redeem, id_jag) -> str:
    form, proof = canopy_redeem(id_jag, TOKEN_ENDPOINT, RESOURCE, ISSUER)
    response = Client().post("/o/token/", form, headers={"DPoP": proof})
    assert response.status_code == 200, response.content
    return response.json()["access_token"]


def _member_of_acme(monkeypatch):
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.scope_config import service
    from connect_labs.scope_config.scopes import Scope

    def may_use(caller, *, organization_id=None, program_id=None, opportunity_id=None):
        return None if organization_id in (None, "acme") and program_id in (None, 25, 26) else "not yours"

    monkeypatch.setattr(service.access, "may_use", may_use)
    monkeypatch.setattr("connect_labs.labs.context.get_org_data", lambda request: ORG_DATA)
    monkeypatch.setattr("connect_labs.workflow.page_views.get_org_data", lambda request: ORG_DATA)
    monkeypatch.setattr("connect_labs.workflow.page_views.save_context_to_session", lambda request, ctx: None)
    service.update(
        "labs", Scope.of("organization", "acme"), {"home": {"fill": {"workflow": 47, "program_id": 25}}}, SYSTEM
    )


PAGE_ROUTES = {
    # A page at its own address, and at its workflow URL (where the route grants more,
    # and the scope in view is the program the address names).
    "page-address": ("/labs/p/org/acme/", {"scope_type": "org", "scope_key": "acme"}, {}, ("organization", "acme")),
    "workflow-url": (
        "/labs/workflow/47/run/?program_id=25",
        {"definition_id": 47},
        {"program_id": 25},
        ("program", "25"),
    ),
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("sandboxed", [True, False], ids=["sandboxed", "not-sandboxed"])
@pytest.mark.parametrize("route", sorted(PAGE_ROUTES))
def test_a_pages_agent_reads_the_page_as_the_visitor(enabled, canopy_redeem, visitor, monkeypatch, route, sandboxed):
    from config.asgi import build_application
    from connect_labs.mcp.tools import page_context as page_context_tool
    from connect_labs.scope_config import service
    from connect_labs.workflow.page_views import PageView
    from connect_labs.workflow.views import WorkflowRunView

    _member_of_acme(monkeypatch)
    path, kwargs, context, (scope_type, scope_key) = PAGE_ROUTES[route]
    view = PageView if route == "page-address" else WorkflowRunView
    with override_settings(LABS_PAGE_SANDBOX=sandboxed):
        response, panel = _panel(view, _request(path, visitor, context), SHARING_PAGE, **kwargs)
    html = response.content.decode()

    # 1. The panel is drawn by the host page, sandboxed or not, outside any frame.
    assert panel and panel["ready"], "a sharing page renders the panel"
    assert f"{enabled.client_id}" not in html  # nothing of canopy's client on the page
    assert ('"sandbox": {"src": "/labs/sandbox/render/"' in html) is sandboxed
    assert html.index('id="canopy-page-state"') > html.index("<body")
    state = _json_script(html, "canopy-page-state")
    assert state["resource"] == f"labs-page://{scope_type}/{scope_key}/home"
    assert state["backing_tool"] == "page_context"
    assert state["visible_ids"] == []
    assert state["filters"] == {
        "definition_id": 47,
        "scope_type": scope_type,
        "scope_key": scope_key,
        "program_id": 25,
    }
    assert len(json.dumps(state).encode()) < 8 * 1024  # canopy's cap on a page state
    if sandboxed:
        # Enforced on the host page: the frame may be navigated only to its shell and
        # the panel's own embed path (so the widget's iframe loads).
        assert response["Content-Security-Policy"].endswith(f" {CONFORMANCE_CANOPY}/embed/chat")

    # 2. The token URL mints an arrival whose ID-JAG is page:read alone, wherever the
    # page is drawn (the run URL's own registration would grant the run scopes).
    arrival = _mint(panel, visitor)
    id_jag = arrival[contract.ARRIVAL_ID_JAG_FIELD]
    assert _scopes(id_jag) == {"page:read"}

    # 3. canopy redeems it and calls labs' MCP as the visitor.
    token = _redeem(canopy_redeem, id_jag)
    monkeypatch.setattr(page_context_tool, "require_connect_token", lambda u: "stub-token")
    monkeypatch.setattr(service, "refusal", lambda caller, scope: None)
    monkeypatch.setattr(
        "connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", lambda token, owner=None: ORG_DATA
    )

    async def add_proof(request):
        request.headers["DPoP"] = enabled.dpop_proof(request.method, RESOURCE, access_token=token)

    async def work(mcp_client):
        tools = await mcp_client.list_tools()
        # As canopy's agent would: every argument from the page state's filters.
        answer = await mcp_client.call_tool("page_context", state["filters"])
        refused = await mcp_client.call_tool(
            "workflow_run_context", {"run_id": 503, "program_id": 25}, raise_on_error=False
        )
        return tools, answer, refused

    with mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess") as MockWDA:
        MockWDA.return_value.get_definition.return_value = _definition(SHARING_PAGE)
        tools, answer, refused = _run_mcp(
            build_application(), {"Authorization": f"DPoP {token}", "Canopy-Actor": "ace"}, add_proof, work
        )
        # Read where the page lives, with the VISITOR's token.
        assert MockWDA.call_args.kwargs == {"access_token": "stub-token", "program_id": 25}

    assert {t.name for t in tools} == {"page_context"}
    out = answer.structured_content
    assert out["page"]["name"] == "Acme home" and out["page"]["slug"] == "home"
    assert out["scope"]["type"] == scope_type
    assert refused.is_error

    from connect_labs.mcp.models import MCPAuditLog

    row = MCPAuditLog.objects.get(tool_name="page_context", success=True)
    assert row.user == visitor and row.actor == "ace"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("sandboxed", [True, False], ids=["sandboxed", "not-sandboxed"])
def test_a_workflows_agent_is_granted_the_run_scopes(enabled, canopy_redeem, visitor, sandboxed):
    from config.asgi import build_application
    from connect_labs.labs.canopy import SCOPE_TOOLS
    from connect_labs.workflow.views import WorkflowRunView

    path = "/labs/workflow/47/run/?run_id=503&program_id=25" + ("&sandbox=1" if sandboxed else "")
    with (
        override_settings(LABS_WORKFLOW_SANDBOX="opt_in"),
        mock.patch("connect_labs.workflow.views.get_org_data", return_value={}),
        # Generated synthetic data: over sensitive data no agent is offered at all
        # (labs/canopy_panel.py; test_canopy_clearance.py).
        mock.patch("connect_labs.labs.synthetic.provenance.all_generated", return_value=True),
    ):
        response, panel = _panel(
            WorkflowRunView, _request(path, visitor, {"program_id": 25}), SHARING_WORKFLOW, definition_id=47
        )
    html = response.content.decode()
    assert panel and panel["ready"]
    assert ('"mode": "workflow"' in html) is sandboxed
    state = _json_script(html, "canopy-page-state")
    assert state["resource"] == "labs-workflow://47/runs/503"
    assert state["backing_tool"] == "workflow_run_indicators"
    assert state["filters"]["run_id"] == 503 and state["filters"]["program_id"] == 25
    assert state["visible_ids"] == ["10::worker-one"]

    arrival = _mint(panel, visitor)
    id_jag = arrival[contract.ARRIVAL_ID_JAG_FIELD]
    assert _scopes(id_jag) == {"workflow:read", "workflow:act", "data:read"}

    token = _redeem(canopy_redeem, id_jag)

    async def add_proof(request):
        request.headers["DPoP"] = enabled.dpop_proof(request.method, RESOURCE, access_token=token)

    async def work(mcp_client):
        tools = await mcp_client.list_tools()
        page = await mcp_client.call_tool(
            "page_context",
            {"definition_id": 47, "scope_type": "program", "scope_key": "25", "program_id": 25},
            raise_on_error=False,
        )
        return tools, page

    tools, page = _run_mcp(build_application(), {"Authorization": f"DPoP {token}"}, add_proof, work)
    listed = {t.name for t in tools}
    # The run's scopes' tools that the MCP lists to a model (an app-only View tool is not).
    assert listed <= SCOPE_TOOLS["workflow:read"] | SCOPE_TOOLS["workflow:act"] | SCOPE_TOOLS["data:read"]
    assert {"workflow_run_context", "workflow_run_indicators", "workflow_run_action"} <= listed
    assert {"data_workflow_context", "data_query", "data_visits"} <= listed
    assert page.is_error  # not a page's tool


DATA_AGENT_WORKFLOW = {**SHARING_WORKFLOW, "config": {"agent": {"share": True, "agent": "data", "brief": "Check it."}}}


@pytest.mark.django_db(transaction=True)
def test_a_workflow_that_offers_the_data_agent_grants_it_the_runs_reads_and_edits_and_no_actions(
    enabled, canopy_redeem, visitor, canopy_agents, fake_canopy
):
    from config.asgi import build_application
    from connect_labs.workflow.views import WorkflowRunView

    canopy_agents("ace", offered=["labsdata"])
    fake_canopy.declare_sensitive("labsdata")
    path = "/labs/workflow/47/run/?run_id=503&program_id=25"
    with (
        mock.patch("connect_labs.workflow.views.get_org_data", return_value={}),
        mock.patch("connect_labs.labs.synthetic.provenance.all_generated", return_value=True),
    ):
        response, panel = _panel(
            WorkflowRunView, _request(path, visitor, {"program_id": 25}), DATA_AGENT_WORKFLOW, definition_id=47
        )
    assert panel and panel["ready"] and panel["agent"] == "labsdata"
    # The run page draws its Agent box (it once sat inside the edit-mode block, so
    # no run page ever showed it).
    box = _json_script(response.content.decode(), "agent-settings")
    # The legacy "data" choice is shown as the agent it names now.
    assert box["agent"] == "labsdata" and box["share"] is True
    arrival = _mint(panel, visitor)
    id_jag = arrival[contract.ARRIVAL_ID_JAG_FIELD]
    # The run's reads, the raw data behind it and edits confirmed in chat -- never
    # workflow:act -- for labsdata, on sensitive-data runners.
    assert _scopes(id_jag) == {"workflow:read", "data:read", "workflow:edit"}
    signed = json.dumps(unverified_claims(arrival["assertion"]))
    assert "labsdata" in signed and "sensitive_data" in signed
    # The grant is recorded against the page it was given on: this workflow, in its scope, and
    # the opportunities it reads -- what the agent's data_* reads are held to (page_holds).
    from connect_labs.labsdata.models import PageGrant

    hold = PageGrant.objects.get(grant_jti=unverified_claims(id_jag)["jti"]).hold
    assert hold.workflow == {"id": 47, "program_id": 25} and hold.user_id == visitor.pk
    assert hold.page_key == "labs:workflow:run:data" and hold.space_id is None

    token = _redeem(canopy_redeem, id_jag)

    async def add_proof(request):
        request.headers["DPoP"] = enabled.dpop_proof(request.method, RESOURCE, access_token=token)

    async def work(mcp_client):
        return {t.name for t in await mcp_client.list_tools()}

    listed = _run_mcp(build_application(), {"Authorization": f"DPoP {token}"}, add_proof, work)
    assert {"workflow_run_context", "data_workflow_context", "data_query"} <= listed
    assert "workflow_run_action" not in listed


@pytest.mark.django_db
def test_asking_for_the_data_agent_with_none_configured_offers_no_agent(enabled, visitor):
    from connect_labs.workflow.views import WorkflowRunView

    with (
        mock.patch("connect_labs.workflow.views.get_org_data", return_value={}),
        mock.patch("connect_labs.labs.synthetic.provenance.all_generated", return_value=True),
    ):
        _response, panel = _panel(
            WorkflowRunView,
            _request("/labs/workflow/47/run/?run_id=503&program_id=25", visitor, {"program_id": 25}),
            DATA_AGENT_WORKFLOW,
            definition_id=47,
        )
    assert not panel


@pytest.mark.django_db
@pytest.mark.parametrize(
    "agent, sensitive, expect_agent, expect_scopes",
    [
        # Any other agent labs lists: named on the panel, the run page's usual scopes.
        ("coach-bot", [], "coach-bot", {"workflow:read", "workflow:act", "data:read"}),
        # One allowed sensitive data: on sensitive-data runners, like the data agent -- reads, and edits
        # a person confirms in chat, but no actions.
        ("deep-bot", ["deep-bot"], "deep-bot", {"workflow:read", "data:read", "workflow:edit"}),
        # One labs no longer lists: no panel at all, not the site's agent in its place.
        ("gone-bot", [], None, None),
    ],
)
def test_a_workflow_offers_the_agent_it_chose_on_that_agents_terms(
    enabled, visitor, canopy_agents, agent, sensitive, expect_agent, expect_scopes
):
    from connect_labs.workflow.views import WorkflowRunView

    canopy_agents("ace", offered=["coach-bot", "deep-bot"], sensitive_data=sensitive)
    workflow = {**SHARING_WORKFLOW, "config": {"agent": {"share": True, "agent": agent}}}
    with (
        mock.patch("connect_labs.workflow.views.get_org_data", return_value={}),
        mock.patch("connect_labs.labs.synthetic.provenance.all_generated", return_value=True),
    ):
        _response, panel = _panel(
            WorkflowRunView,
            _request("/labs/workflow/47/run/?run_id=503&program_id=25", visitor, {"program_id": 25}),
            workflow,
            definition_id=47,
        )
    if expect_agent is None:
        assert not panel
        return
    assert panel and panel["ready"] and panel["agent"] == expect_agent
    arrival = _mint(panel, visitor)
    assert _scopes(arrival[contract.ARRIVAL_ID_JAG_FIELD]) == expect_scopes
    signed = json.dumps(unverified_claims(arrival["assertion"]))
    assert ("sensitive_data" in signed) == bool(sensitive)


@pytest.mark.django_db
def test_a_held_back_agent_is_never_silent(enabled, visitor, canopy_agents, fake_canopy):
    """Owner, 2026-10-22: labsdata was set up everywhere but one labs field, and the KMC
    report drew no panel and said nothing. Now the Agent box says why, and the choice is
    shown greyed with the same reason."""
    from connect_labs.workflow.views import WorkflowRunView

    canopy_agents("ace", offered=["labsdata"])
    fake_canopy.declare_sensitive("labsdata")
    with (
        mock.patch("connect_labs.workflow.views.get_org_data", return_value={}),
        mock.patch("connect_labs.labs.synthetic.provenance.all_generated", return_value=False),
    ):
        response, panel = _panel(
            WorkflowRunView,
            _request("/labs/workflow/47/run/?run_id=503&program_id=25", visitor, {"program_id": 25}),
            DATA_AGENT_WORKFLOW,
            definition_id=47,
        )
    assert not panel
    box = _json_script(response.content.decode(), "agent-settings")
    assert any("not allowed sensitive data" in r for r in box["held_back"])
    data = next(c for c in box["choices"] if c["slug"] == "labsdata")
    assert data["available"] is False and data["reasons"] == box["held_back"]
    # The default agent is not allowed sensitive data either, and says so.
    site = next(c for c in box["choices"] if c["default"])
    assert site["available"] is False and site["reasons"]
