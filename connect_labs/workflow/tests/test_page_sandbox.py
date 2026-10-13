"""Page code runs in a sandbox (workflow/sandbox.py; spec 2026-10-12-page-sandbox-design.md).

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory, override_settings

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config import service
from connect_labs.scope_config.scopes import Scope
from connect_labs.workflow import sandbox
from connect_labs.workflow.tests.test_page_mode import ORG_DATA, PAGE, _definition, _request

LABS = "https://labs.connect.dimagi.com"
SHARING_PAGE = {**PAGE, "config": {"agent": {"share": True}}}
WORKFLOW = {"name": "Weekly report", "config": {}}


def _directives(policy: str) -> dict[str, list[str]]:
    out = {}
    for part in policy.split(";"):
        bits = part.split()
        if bits:
            out[bits[0]] = bits[1:]
    return out


# --- the frame document ----------------------------------------------------------


def test_the_frame_policy_is_sandboxed_and_reaches_no_labs_origin_and_no_wildcard():
    d = _directives(sandbox.frame_policy(LABS))
    assert d["sandbox"] == ["allow-scripts"]
    assert d["default-src"] == ["'none'"]
    # Page code cannot reach labs except through the bridge, nor send data to any host.
    for value in d["connect-src"] + d["img-src"]:
        assert not value.startswith(LABS), value
        assert value not in ("*", "https:", "'self'", "https://api.mapbox.com", "https://api.mapbox.com/")
    assert "https://api.mapbox.com/styles/v1/mapbox/" in d["connect-src"]
    # Script comes from labs' static prefix and pinned CDN files, named in full.
    assert d["script-src"][0] == f"{LABS}/static/"
    assert sandbox.BABEL_URL in d["script-src"] and "'self'" not in d["script-src"]
    assert d["frame-ancestors"] == [LABS]
    for closed in ("frame-src", "form-action", "base-uri", "object-src"):
        assert d[closed] == ["'none'"]


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/labs/sandbox/render/", "/labs/sandbox/render/x/y?next=/o/authorize/&a=1"])
def test_the_shell_answers_200_without_login_or_redirect_for_any_suffix_and_query(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response["Content-Security-Policy"] == sandbox.frame_policy(HERE)
    assert "X-Frame-Options" not in response
    assert "Content-Security-Policy-Report-Only" not in response
    assert response["Cross-Origin-Resource-Policy"] == "same-origin"
    assert response["Referrer-Policy"] == "strict-origin"
    assert "workflow-sandbox-bundle.js" in response.content.decode()


@pytest.mark.django_db
def test_the_shell_carries_nothing_about_the_viewer(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="zainab-the-viewer", password="x"))
    anonymous = RequestFactory().get("/labs/sandbox/render/")
    body = client.get("/labs/sandbox/render/").content.decode()
    assert "zainab-the-viewer" not in body and "csrfmiddlewaretoken" not in body and "csrf" not in body.lower()
    # The same bytes for everyone: what a signed-in viewer gets is what no one gets.
    assert body == sandbox.sandbox_render(anonymous).content.decode()


@override_settings(STATIC_URL="https://cdn.example.org/static/")
def test_an_absolute_static_url_is_named_as_it_is():
    assert _directives(sandbox.frame_policy(LABS))["script-src"][0] == "https://cdn.example.org/static/"


@override_settings(CANOPY_BASE_URL="https://canopy.example.org/")
def test_the_host_may_frame_only_the_shell_and_the_canopy_widget_path():
    assert sandbox.host_frame_src(LABS) == (
        f"frame-src {LABS}/labs/sandbox/render/ https://canopy.example.org/embed/chat"
    )


# --- the decision: every route that draws a page ---------------------------------


def _wda(MockWDA, data):
    wda = MockWDA.return_value
    wda.get_definition.return_value = _definition(data)
    wda.get_render_code.return_value = MagicMock(data={"component_code": "function WorkflowUI() {}"})
    wda.get_workers.return_value = [{"username": "worker-one", "name": "Worker One"}]
    return wda


def _render(view_cls, request, data, **kwargs):
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        wda = _wda(MockWDA, data)
        response = view_cls.as_view()(request, **kwargs)
        if hasattr(response, "render"):
            response.render()
    return response, wda


#: RequestFactory's host. The origin is the request's own, not LABS_PUBLIC_URL (finding 3).
HERE = "http://testserver"


def _assert_sandboxed(response, origin=HERE):
    body = response.content.decode()
    assert response.status_code == 200, body[:500]
    assert response["Content-Security-Policy"].startswith(f"frame-src {origin}/labs/sandbox/render/")
    assert (
        f'"sandbox": {{"src": "/labs/sandbox/render/", "origin": "{origin}", "mode": "page", '
        '"readable_definitions": [47]}' in body
    )
    # Host mode: the runner bundle, and none of the eval path (Babel, render libraries).
    assert "workflow-runner-bundle.js" in body
    for script in ("babel", "chart.umd.min.js", "mapbox-gl.js", "connect_map.js"):
        assert script not in body
    # A page is as definable as a workflow (Jonathan, 2026-10-12): its workers and its
    # pipelines are loaded for the frame like a workflow's; only the run endpoints are gone.
    assert '"streamPipelineData": "/labs/workflow/api/47/pipeline-data/stream/"' in body
    assert '"queryPipelineRows": "/labs/workflow/api/47/pipeline-query/"' in body
    assert '"updateState": null' in body and '"completeRun": null' in body
    return body


@pytest.fixture
def member_of_acme(monkeypatch):
    def may_use(caller, *, organization_id=None, program_id=None, opportunity_id=None):
        return None if organization_id in (None, "acme") and program_id in (None, 25, 26) else "not yours"

    monkeypatch.setattr(service.access, "may_use", may_use)
    monkeypatch.setattr("connect_labs.labs.context.get_org_data", lambda request: ORG_DATA)
    monkeypatch.setattr("connect_labs.workflow.page_views.get_org_data", lambda request: ORG_DATA)
    monkeypatch.setattr("connect_labs.workflow.page_views.save_context_to_session", lambda request, ctx: None)
    service.update(
        "labs", Scope.of("organization", "acme"), {"home": {"fill": {"workflow": 47, "program_id": 25}}}, SYSTEM
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "route",
    [
        "page-address",
        "page-address-present",
        "workflow-url-no-run",
        "workflow-url-present",
        "edit-mode",
        "supply-tab",
        "workflow-url-with-run-id",
        "page-template-without-kind",
        "page-mode-on-a-workflow-definition",
    ],
)
def test_every_route_that_draws_a_page_draws_it_sandboxed(route, member_of_acme, monkeypatch):
    from connect_labs.workflow.page_views import PageView
    from connect_labs.workflow.views import WorkflowRunView

    if route.startswith("page-address"):
        query = "?present=1" if route.endswith("present") else ""
        request = _request("/labs/p/org/acme/" + query, context={})
        response, _ = _render(PageView, request, PAGE, scope_type="org", scope_key="acme")
    elif route == "supply-tab":
        response = _supply_tab(monkeypatch)
    elif route == "page-template-without-kind":
        # A page made from page_blank before `kind` was written onto pages.
        request = _request("/labs/p/org/acme/", context={})
        response, _ = _render(PageView, request, BLANK_WITHOUT_KIND, scope_type="org", scope_key="acme")
    elif route == "page-mode-on-a-workflow-definition":
        # A route that draws in page mode sandboxes whatever the definition says.
        request = _request("/labs/workflow/47/run/?program_id=25")
        monkeypatch.setattr(WorkflowRunView, "_is_page_definition", lambda self, definition_id: True)
        with patch.object(WorkflowRunView, "page_mode", True):
            response, _ = _render(WorkflowRunView, request, WORKFLOW, definition_id=47)
    else:
        query = {
            "workflow-url-no-run": "?program_id=25",
            "workflow-url-present": "?program_id=25&present=1",
            "edit-mode": "?program_id=25&edit=true",
            "workflow-url-with-run-id": "?program_id=25&run_id=503",
        }[route]
        request = _request("/labs/workflow/47/run/" + query)
        monkeypatch.setattr(WorkflowRunView, "_is_page_definition", lambda self, definition_id: True)
        response, wda = _render(WorkflowRunView, request, PAGE, definition_id=47)
        # A page has no runs: a run_id on its address is never read.
        wda.get_run.assert_not_called()
    _assert_sandboxed(response)


BLANK_WITHOUT_KIND = {
    "name": "Acme home",
    "page": {"slug": "home"},
    "templateType": "page_blank",
    "opportunity_ids": [],
    "config": {"templateType": "page_blank"},
}
OTHER_RUN = {
    "id": 503, "experiment": "w", "type": "r", "opportunity_id": None, "program_id": 25,
    "data": {"status": "completed", "definition_id": 99, "state": {"secret_state": 1},
             "snapshot": {"rows": ["another-workflows-snapshot"]}},
}  # fmt: skip


def test_a_page_template_definition_without_kind_is_a_page():
    from connect_labs.workflow.page_mode import is_page

    assert is_page(_definition(BLANK_WITHOUT_KIND))
    assert is_page({"config": {"templateType": "page_blank"}})
    assert not is_page(_definition(WORKFLOW))


@pytest.mark.django_db
def test_a_home_set_to_a_workflow_is_not_drawn(member_of_acme):
    """Settings names a workflow with runs: the home says so, and draws nothing of it."""
    from connect_labs.workflow.page_views import PageView

    response, wda = _render(
        PageView, _request("/labs/p/org/acme/", context={}), WORKFLOW, scope_type="org", scope_key="acme"
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "is not a page" in body
    assert 'id="workflow-data"' not in body and "function WorkflowUI" not in body
    assert "workflow-runner-bundle.js" not in body


@pytest.mark.django_db
def test_a_page_with_a_run_id_never_carries_another_definitions_snapshot(monkeypatch):
    from connect_labs.workflow.data_access import WorkflowRunRecord
    from connect_labs.workflow.views import WorkflowRunView

    monkeypatch.setattr(WorkflowRunView, "_is_page_definition", lambda self, definition_id: True)
    request = _request("/labs/workflow/47/run/?program_id=25&run_id=503")
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        wda = _wda(MockWDA, PAGE)
        wda.get_run.return_value = WorkflowRunRecord(OTHER_RUN)
        response = WorkflowRunView.as_view()(request, definition_id=47)
        response.render()
    body = response.content.decode()
    assert "another-workflows-snapshot" not in body and "secret_state" not in body
    assert '"id": 0' in body  # the blank in-memory instance, not run 503


@pytest.mark.django_db
def test_a_run_of_another_workflow_is_refused_on_the_workflow_route():
    from connect_labs.workflow.data_access import WorkflowRunRecord
    from connect_labs.workflow.views import WorkflowRunView

    request = _request("/labs/workflow/47/run/?program_id=25&run_id=503")
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        wda = _wda(MockWDA, WORKFLOW)
        wda.get_run.return_value = WorkflowRunRecord(OTHER_RUN)
        response = WorkflowRunView.as_view()(request, definition_id=47)
        response.render()
    body = response.content.decode()
    assert "does not belong to this workflow" in body
    assert "another-workflows-snapshot" not in body and "secret_state" not in body


@pytest.mark.django_db
def test_the_origin_is_the_requests_own_host(member_of_acme, client):
    """Another allowed host (127.0.0.1 locally) gets its own origin everywhere, not LABS_PUBLIC_URL."""
    from connect_labs.workflow.page_views import PageView

    with override_settings(ALLOWED_HOSTS=["127.0.0.1", "testserver"]):
        request = _request("/labs/p/org/acme/", context={})
        request.META["HTTP_HOST"] = "127.0.0.1:8000"
        response, _ = _render(PageView, request, PAGE, scope_type="org", scope_key="acme")
        _assert_sandboxed(response, origin="http://127.0.0.1:8000")
        shell = client.get("/labs/sandbox/render/", HTTP_HOST="127.0.0.1:8000")
    assert _directives(shell["Content-Security-Policy"])["frame-ancestors"] == ["http://127.0.0.1:8000"]


def _supply_tab(monkeypatch):
    from connect_labs.supply_chain.workflow_views import views as supply_views

    pin = SimpleNamespace(
        slug="home", label="Home", workflow_definition_id=47, opportunity_id=None, replaces=None, created_by="amina"
    )
    monkeypatch.setattr(supply_views, "_pinned", lambda request: [pin])
    monkeypatch.setattr(supply_views.WorkflowRunView, "_is_page_definition", lambda self, definition_id: True)
    monkeypatch.setattr(supply_views, "current_run_id", lambda request, pin: pytest.fail("a page starts no run"))
    request = _request("/supply/views/home/?program_id=25")
    response, _ = _render(supply_views.SupplyWorkflowPageView, request, PAGE, slug="home")
    return response


@pytest.mark.django_db
def test_a_workflow_that_is_not_a_page_runs_as_before(monkeypatch):
    from connect_labs.workflow.data_access import WorkflowRunRecord
    from connect_labs.workflow.views import WorkflowRunView

    request = _request("/labs/workflow/47/run/?program_id=25&run_id=503")
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        wda = _wda(MockWDA, WORKFLOW)
        wda.get_run.return_value = WorkflowRunRecord(
            {"id": 503, "experiment": "w", "type": "r", "opportunity_id": None, "program_id": 25,
             "data": {"status": "in_progress", "definition_id": 47, "state": {}}}
        )  # fmt: skip
        response = WorkflowRunView.as_view()(request, definition_id=47)
        response.render()
    body = response.content.decode()
    assert "Content-Security-Policy" not in response and '"sandbox"' not in body
    assert "chart.umd.min.js" in body and "connect_map.js" in body


@pytest.mark.django_db
@override_settings(LABS_PAGE_SANDBOX=False)
def test_the_emergency_switch_turns_the_sandbox_off(member_of_acme):
    from connect_labs.workflow.page_views import PageView

    response, _ = _render(
        PageView, _request("/labs/p/org/acme/", context={}), PAGE, scope_type="org", scope_key="acme"
    )
    assert '"sandbox"' not in response.content.decode() and "Content-Security-Policy" not in response


@pytest.mark.django_db
def test_a_definition_that_cannot_be_read_is_not_taken_to_be_a_workflow(monkeypatch):
    """Fail closed: "could not tell" renders here (where the render decides), not the list redirect."""
    from connect_labs.workflow.views import WorkflowRunView

    view = WorkflowRunView()
    view.request = _request()
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        MockWDA.return_value.get_definition.side_effect = RuntimeError("Connect is down")
        assert view._is_page_definition(47) is None
        request = _request("/labs/workflow/47/run/?program_id=25")
        response = WorkflowRunView.as_view()(request, definition_id=47)
    assert response.status_code == 200  # rendered (with its error), not a 302 to the list
    response.render()
    assert '"sandbox"' not in response.content.decode() and "workflow-runner-bundle" not in response.content.decode()


# --- the canopy panel on a page -------------------------------------------------


@pytest.mark.django_db
def test_a_sharing_page_gets_the_panel_naming_the_page_and_page_context(member_of_acme):
    from connect_labs.workflow.page_views import PageView

    captured = {}

    def panel_context(request, **kwargs):
        captured.update(kwargs)
        return {"ready": False, "page_state": None}

    with patch("canopy_sdk.django.pages.panel_context", panel_context):
        _render(PageView, _request("/labs/p/org/acme/", context={}), SHARING_PAGE, scope_type="org", scope_key="acme")
    assert captured["resource"] == "labs-page://organization/acme/home"
    assert captured["backing_tool"] == "page_context"
    assert captured["visible_ids"] == []
    assert captured["filters"] == {
        "definition_id": 47,
        "scope_type": "organization",
        "scope_key": "acme",
        "program_id": 25,
    }


@pytest.mark.django_db
def test_a_page_drawn_at_the_run_url_grants_its_agent_page_read_only():
    from canopy_sdk.django import conf

    from connect_labs.workflow.views import _page_only_panel

    request = _request("/labs/workflow/47/run/?program_id=25")
    run_token = conf.page_tokens().issue("labs:workflow:run", request.user.pk)
    # What the SDK signs on the run URL grants the run scopes...
    assert "workflow:act" in conf.page_tokens().scopes(run_token, request.user.pk)
    request.resolver_match = SimpleNamespace(view_name="labs:workflow:run")
    panel = {"ready": True, "page_token": run_token, "token_url": "/x?page=" + run_token, "options": {}}
    out = _page_only_panel(request, panel)
    # ...so a page's panel there is signed again, for page:read alone.
    assert conf.page_tokens().scopes(out["page_token"], request.user.pk) == ("page:read",)
    assert run_token not in out["token_url"] and out["options"]["tokenUrl"] == out["token_url"]
    # On a page's own address the SDK's token is already page:read only, and is kept.
    request.resolver_match = SimpleNamespace(view_name="labs:page_home")
    page_token = conf.page_tokens().issue("labs:page_home", request.user.pk)
    kept = {"ready": True, "page_token": page_token, "token_url": "", "options": {}}
    assert _page_only_panel(request, kept) is kept


@pytest.mark.django_db
def test_a_page_that_does_not_share_gets_no_panel(member_of_acme):
    from connect_labs.workflow.page_views import PageView

    with patch("canopy_sdk.django.pages.panel_context", side_effect=AssertionError("no panel")):
        response, _ = _render(
            PageView, _request("/labs/p/org/acme/", context={}), PAGE, scope_type="org", scope_key="acme"
        )
    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("sandboxed", [True, False], ids=["sandboxed", "not-sandboxed"])
@pytest.mark.parametrize(
    "route, panel",
    [
        ("page-address", True),
        ("page-address-present", False),
        ("workflow-url-with-run-id", True),
        ("edit-mode", False),
        ("workflow-url-present", False),
        ("page-mode-on-a-workflow-definition", False),
    ],
)
def test_which_routes_give_a_sharing_page_its_panel(route, panel, sandboxed, member_of_acme, monkeypatch):
    """A panel where a page is drawn to be used; never in edit or present mode, and never
    for a workflow a route draws in page mode (page_context reads only pages)."""
    from connect_labs.workflow.page_views import PageView
    from connect_labs.workflow.views import WorkflowRunView

    calls = []

    def panel_context(request, **kwargs):
        calls.append(kwargs)
        return {"ready": False, "page_state": None}

    monkeypatch.setattr(WorkflowRunView, "_is_page_definition", lambda self, definition_id: True)
    with patch("canopy_sdk.django.pages.panel_context", panel_context), override_settings(LABS_PAGE_SANDBOX=sandboxed):
        if route.startswith("page-address"):
            query = "?present=1" if route.endswith("present") else ""
            request = _request("/labs/p/org/acme/" + query, context={})
            _render(PageView, request, SHARING_PAGE, scope_type="org", scope_key="acme")
        elif route == "page-mode-on-a-workflow-definition":
            sharing_workflow = {**WORKFLOW, "config": {"agent": {"share": True}}}
            with patch.object(WorkflowRunView, "page_mode", True):
                _render(
                    WorkflowRunView,
                    _request("/labs/workflow/47/run/?program_id=25"),
                    sharing_workflow,
                    definition_id=47,
                )
        else:
            query = {
                "workflow-url-with-run-id": "?program_id=25&run_id=503",
                "edit-mode": "?program_id=25&edit=true",
                "workflow-url-present": "?program_id=25&present=1",
            }[route]
            _render(WorkflowRunView, _request("/labs/workflow/47/run/" + query), SHARING_PAGE, definition_id=47)
    assert bool(calls) is panel
    if panel:
        # Always the page's own resource and tool, never a run's.
        assert calls[0]["resource"].startswith("labs-page://") and calls[0]["backing_tool"] == "page_context"


@pytest.mark.django_db
def test_a_page_panel_is_not_drawn_when_the_browser_would_name_the_page(settings):
    """In the SDK's key mode the browser names its page by path: no page panel, fail closed."""
    from canopy_sdk.django import conf

    from connect_labs.workflow.views import _page_only_panel

    request = _request("/labs/workflow/47/run/?program_id=25")
    request.resolver_match = SimpleNamespace(view_name="labs:workflow:run")
    with patch.object(conf, "page_mode", return_value=conf.KEY):
        out = _page_only_panel(request, {"ready": True, "page_token": "", "token_url": "", "options": {}})
    assert out["ready"] is False


def test_page_routes_are_registered_with_a_scope_that_reaches_page_context():
    from django.urls import reverse

    from connect_labs.labs.canopy import PAGE_SCOPES, SCOPE_TOOLS
    from connect_labs.mcp.tool_registry import _REGISTRY
    from connect_labs.mcp.tools import page_context  # noqa: F401

    reverse("labs:page_home", args=["org", "acme"])
    reverse("labs:page", args=["org", "acme", "home"])
    reverse("supply_chain:workflow_view", args=["home"])
    for route in ("labs:page_home", "labs:page", "supply_chain:workflow_view", "labs:workflow:run:page"):
        assert PAGE_SCOPES[route] == ("page:read",)
    # The run URL's own scopes are a workflow's: a page drawn there signs for the page key.
    assert "page:read" not in PAGE_SCOPES["labs:workflow:run"]
    assert SCOPE_TOOLS["page:read"] == frozenset({"page_context"})
    assert "page_context" in _REGISTRY


def test_page_context_is_not_reachable_without_user_visit_data():
    from connect_labs.mcp import token_scopes

    assert "page_context" in token_scopes.USERVISIT_DATA_TOOLS
    assert "page_context" not in token_scopes.RESTRICTED_TOOLS


# --- page_context ----------------------------------------------------------------


def _call_page_context(data, user, monkeypatch, refusal=None, **kwargs):
    from connect_labs.mcp.tools import page_context as tool

    monkeypatch.setattr(tool, "require_connect_token", lambda u: "stub-token")
    monkeypatch.setattr(service, "refusal", lambda caller, scope: refusal)
    monkeypatch.setattr(
        "connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", lambda token, owner=None: ORG_DATA
    )
    with patch("connect_labs.workflow.data_access.WorkflowDataAccess") as MockWDA:
        MockWDA.return_value.get_definition.return_value = _definition(data)
        args = {"definition_id": 47, "scope_type": "organization", "scope_key": "acme", "program_id": 25, **kwargs}
        return tool.page_context(user, **args), MockWDA


@pytest.mark.django_db
def test_page_context_reads_the_page_as_the_visitor(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="amina")
    out, MockWDA = _call_page_context(SHARING_PAGE, user, monkeypatch)
    # Read where the page lives, with the visitor's own token.
    assert MockWDA.call_args.kwargs == {"access_token": "stub-token", "program_id": 25}
    assert out["page"] == {"id": 47, "name": "Acme home", "description": "", "slug": "home"}
    assert out["scope"]["organization"]["name"] == "Acme Health"
    assert [p["name"] for p in out["scope"]["programs"]] == ["Nutrition", "Malaria"]
    assert out["scope"]["user"]["username"] == "amina"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data,refusal,code",
    [
        (WORKFLOW | {"config": {"agent": {"share": True}}}, None, "INVALID_SCHEMA"),
        (PAGE, None, "PERMISSION_DENIED"),
        (SHARING_PAGE, "not yours", "PERMISSION_DENIED"),
    ],
    ids=["not-a-page", "does-not-share", "scope-not-the-visitors"],
)
def test_page_context_refuses(data, refusal, code, monkeypatch, django_user_model):
    from connect_labs.mcp.tool_registry import MCPToolError

    user = django_user_model.objects.create_user(username="amina")
    with pytest.raises(MCPToolError) as err:
        _call_page_context(data, user, monkeypatch, refusal=refusal)
    assert err.value.code == code


# --- the server-side guard -------------------------------------------------------


def _guarded(request):
    from connect_labs.utils.sandbox_guard import SandboxOriginGuardMiddleware

    return SandboxOriginGuardMiddleware(lambda r: SimpleNamespace(status_code=200))(request)


def _session_request(method="get", path="/labs/mcp/tokens/create/", user=True, cookie=True, **headers):
    from django.contrib.auth.models import AnonymousUser

    request = getattr(RequestFactory(), method)(path, **headers)
    if cookie:
        request.COOKIES["sessionid"] = "s"
    request.user = SimpleNamespace(is_authenticated=True) if user else AnonymousUser()
    return request


@pytest.mark.parametrize(
    "request_kwargs,status",
    [
        ({"method": "post", "HTTP_ORIGIN": "null"}, 403),
        ({"method": "get", "HTTP_ORIGIN": "null"}, 403),
        ({"method": "post", "HTTP_SEC_FETCH_SITE": "cross-site"}, 403),
        ({"method": "get", "HTTP_SEC_FETCH_SITE": "cross-site"}, 200),  # a link from elsewhere still opens labs
        ({"method": "post", "HTTP_SEC_FETCH_SITE": "same-origin", "HTTP_ORIGIN": LABS}, 200),
        # A bearer token with no session cookie: no session in play.
        ({"method": "post", "HTTP_ORIGIN": "null", "HTTP_AUTHORIZATION": "Bearer t", "cookie": False}, 200),
        # ...but a header beside a session cookie does not switch the guard off.
        ({"method": "post", "HTTP_ORIGIN": "null", "HTTP_AUTHORIZATION": "Bearer t"}, 403),
        ({"method": "get", "HTTP_ORIGIN": "null", "HTTP_AUTHORIZATION": "Bearer t"}, 403),
        ({"method": "post", "HTTP_ORIGIN": "null", "cookie": False}, 200),
        ({"method": "post", "HTTP_ORIGIN": "null", "user": False}, 200),
        ({"method": "get", "HTTP_ORIGIN": "null", "path": "/labs/sandbox/render/"}, 200),
    ],
)
def test_the_session_never_answers_an_opaque_origin_or_a_cross_site_write(request_kwargs, status):
    assert _guarded(_session_request(**request_kwargs)).status_code == status


@pytest.mark.django_db
def test_a_csrf_exempt_endpoint_the_runner_reaches_checks_the_origin(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="amina", password="x"))
    response = client.post(
        "/tasks/api/single-create/", data="{}", content_type="application/json", HTTP_ORIGIN="https://evil.example"
    )
    assert response.status_code == 403 and b"did not come from a labs page" in response.content


@pytest.mark.django_db
def test_a_same_origin_write_to_it_passes_the_origin_check(rf):
    from connect_labs.utils.sandbox_guard import require_same_origin

    view = require_same_origin(lambda request: SimpleNamespace(status_code=201))
    ok = rf.post("/tasks/api/single-create/", HTTP_ORIGIN="http://testserver")
    no_origin = rf.post("/tasks/api/single-create/")
    evil = rf.post("/tasks/api/single-create/", HTTP_ORIGIN="https://labs.connect.dimagi.com.evil.example")
    assert view(ok).status_code == 201 and view(no_origin).status_code == 201 and view(evil).status_code == 403


# --- the site-wide policy and the page's own -------------------------------------


@pytest.mark.parametrize("report_only", [True, False])
def test_the_site_policy_leaves_the_shell_alone_and_keeps_both_on_a_page(report_only, settings):
    from django.http import HttpResponse

    from connect_labs.utils.csp import ContentSecurityPolicyMiddleware

    settings.CSP_REPORT_ONLY = report_only
    shell = HttpResponse("x", content_type="text/html")
    shell["Content-Security-Policy"] = "sandbox allow-scripts"
    shell.csp_exempt = True
    page = HttpResponse("x", content_type="text/html")
    page["Content-Security-Policy"] = "frame-src https://x/"
    page.csp_extra_policy = True
    out = {}
    for name, response in (("shell", shell), ("page", page)):
        out[name] = ContentSecurityPolicyMiddleware(lambda r, resp=response: resp)(RequestFactory().get("/"))
    assert out["shell"]["Content-Security-Policy"] == "sandbox allow-scripts"
    assert "Content-Security-Policy-Report-Only" not in out["shell"]
    if report_only:
        assert out["page"]["Content-Security-Policy"] == "frame-src https://x/"
        assert "frame-ancestors" in out["page"]["Content-Security-Policy-Report-Only"]
    else:
        assert re.match(r"^frame-src https://x/, default-src ", out["page"]["Content-Security-Policy"])


def test_the_guard_runs_straight_after_authentication_before_any_labs_middleware(settings):
    """Nothing session-dependent (OAuth refresh, context save, redirects) runs for a request it refuses."""
    stack = list(settings.MIDDLEWARE)
    guard = stack.index("connect_labs.utils.sandbox_guard.SandboxOriginGuardMiddleware")
    assert stack[guard - 1] == "django.contrib.auth.middleware.AuthenticationMiddleware"
    later = [m for m in stack[guard + 1 :] if m.startswith("connect_labs.")]
    assert "connect_labs.campaign.middleware.CampaignOAuthSessionMiddleware" in later
