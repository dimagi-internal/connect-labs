"""Pages: workflows with no runs (page_mode.py), their addresses (page_views.py), their
sources (workflow_sources.py), and an organisation coming in.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config import service
from connect_labs.scope_config.scopes import Scope

ORG_DATA = {
    "organizations": [{"id": 5, "slug": "acme", "name": "Acme Health"}],
    "programs": [
        {"id": 25, "name": "Nutrition", "organization": "acme"},
        {"id": 26, "name": "Malaria", "organization": "acme"},
    ],
    "opportunities": [
        {"id": 10, "name": "Kano", "program": 25, "organization": "partner"},
        {"id": 11, "name": "Jigawa", "program": 26, "organization": "partner"},
    ],
}

PAGE = {"name": "Acme home", "kind": "page", "page": {"slug": "home"}, "opportunity_ids": [], "config": {}}


def _definition(data, id_=47, program_id=25):
    from connect_labs.workflow.data_access import WorkflowDefinitionRecord

    return WorkflowDefinitionRecord(
        {"id": id_, "experiment": "workflow", "type": "workflow_definition", "opportunity_id": None,
         "program_id": program_id, "data": data}
    )  # fmt: skip


def _request(path="/labs/workflow/47/run/?program_id=25", context=None):
    req = RequestFactory().get(path)
    req.session = {"labs_oauth": {"access_token": "stub-token", "organization_data": ORG_DATA}}
    from django.contrib.auth import get_user_model

    req.user, _ = get_user_model().objects.get_or_create(username="amina")
    req.labs_context = context if context is not None else {"program_id": 25}
    return req


def _run_context(data, request=None, page_mode=False, page_scope=None):
    from connect_labs.workflow.views import WorkflowRunView

    rc = MagicMock()
    rc.data = {"component_code": "function WorkflowUI() {}"}
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        wda = MockWDA.return_value
        wda.get_definition.return_value = _definition(data)
        wda.get_render_code.return_value = rc
        wda.get_workers.return_value = []
        view = WorkflowRunView()
        view.request = request or _request()
        view.kwargs = {"definition_id": 47}
        view.page_mode = page_mode
        view.page_scope = page_scope
        return view.get_context_data(), wda


# --- page mode -------------------------------------------------------------------


@pytest.mark.django_db
def test_a_page_renders_with_no_run_and_no_run_endpoints():
    context, wda = _run_context(PAGE)
    data = context["workflow_data"]
    assert context["is_page"] is True and data["is_page"] is True
    assert data["instance"]["id"] == 0 and data["instance"]["state"] == {}
    for key in ("updateState", "completeRun", "renameRun", "getSnapshot", "saveWorkerResult"):
        assert data["apiEndpoints"][key] is None
    assert "actionBase" not in data["apiEndpoints"] and "canopy_panel" not in context
    wda.create_run.assert_not_called()
    wda.get_run.assert_not_called()


@pytest.mark.django_db
def test_a_page_reads_the_scope_in_view_with_names_and_what_is_under_it():
    context, _ = _run_context(PAGE, page_mode=True, page_scope=Scope.of("organization", "acme"))
    scope = context["workflow_data"]["scope"]
    assert scope["type"] == "organization" and scope["organization"]["name"] == "Acme Health"
    assert [p["name"] for p in scope["programs"]] == ["Nutrition", "Malaria"]
    assert [o["name"] for o in scope["opportunities"]] == ["Kano", "Jigawa"]
    assert scope["programs"][0]["supply_url"] == "/supply/?program_id=25"
    assert scope["settings_url"] == "/labs/settings/organization/acme/"


@pytest.mark.django_db
def test_an_opportunitys_page_reads_its_programme_owners_org_not_its_partners():
    scope = _run_context(PAGE, page_mode=True, page_scope=Scope.of("opportunity", 10))[0]["workflow_data"]["scope"]
    assert scope["organization"]["slug"] == "acme" and scope["program"]["name"] == "Nutrition"


@pytest.mark.django_db
def test_a_page_gets_the_settings_it_declares():
    service.update("supply", Scope.of("program", 25), {"tabs": {"supply_chain:alerts": {"hidden": True}}}, SYSTEM)
    context, _ = _run_context({**PAGE, "config_reads": ["supply", "no-such-namespace"]})
    assert context["workflow_data"]["config"] == {"supply": {"tabs": {"supply_chain:alerts": {"hidden": True}}}}


@pytest.mark.django_db
def test_a_workflow_with_runs_keeps_its_run():
    from connect_labs.workflow.data_access import WorkflowRunRecord

    request = _request("/labs/workflow/47/run/?program_id=25&run_id=503")
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        wda = MockWDA.return_value
        wda.get_definition.return_value = _definition({"name": "Report", "config": {}})
        wda.get_render_code.return_value = MagicMock(data={"component_code": "x"})
        wda.get_run.return_value = WorkflowRunRecord(
            {"id": 503, "experiment": "w", "type": "r", "opportunity_id": None, "program_id": 25,
             "data": {"status": "in_progress", "definition_id": 47, "state": {}}}
        )  # fmt: skip
        from connect_labs.workflow.views import WorkflowRunView

        view = WorkflowRunView()
        view.request, view.kwargs = request, {"definition_id": 47}
        data = view.get_context_data()["workflow_data"]
    assert data["is_page"] is False and data["instance"]["id"] == 503 and data["apiEndpoints"]["updateState"]


def test_page_kind_comes_from_the_template_onto_the_definition():
    from connect_labs.workflow.templates import create_workflow_from_template

    access = MagicMock(opportunity_id=None, program_id=25, organization_id=None, access_token="t")
    access.create_definition.return_value = MagicMock(id=9, data={})
    create_workflow_from_template(access, "page_blank", program_id=25)
    kwargs = access.create_definition.call_args.kwargs
    assert kwargs["kind"] == "page" and kwargs["page"] == {"slug": "home"} and kwargs["config_reads"] == ["supply"]


# --- workflow sources ------------------------------------------------------------


@pytest.mark.django_db
def test_a_source_the_viewer_cannot_read_comes_back_as_its_error_not_a_500():
    from connect_labs.workflow import workflow_sources

    with patch("connect_labs.workflow.workflow_sources.WorkflowDataAccess") as MockWDA:
        MockWDA.return_value.get_definition.return_value = None
        out = workflow_sources.read(_request(), {"alias": "r", "workflow": 99, "program_id": 25})
    assert "cannot open it" in out["error"]


@pytest.mark.django_db
def test_latest_run_is_the_newest_and_carries_only_its_summary():
    from connect_labs.workflow import workflow_sources
    from connect_labs.workflow.data_access import WorkflowRunRecord

    def run(i, status, extra=None):
        return WorkflowRunRecord(
            {"id": i, "experiment": "w", "type": "r", "opportunity_id": None, "program_id": 25,
             "data": {"status": status, "definition_id": 99, "snapshot_summary": {"n": i}, **(extra or {})}}
        )  # fmt: skip

    with patch("connect_labs.workflow.workflow_sources.WorkflowDataAccess") as MockWDA:
        wda = MockWDA.return_value
        wda.get_definition.return_value = _definition({"name": "Report"}, id_=99)
        wda.list_runs.return_value = [run(1, "completed"), run(3, "completed", {"snapshot": {"big": 1}}), run(2, "in_progress")]
        out = workflow_sources.read(_request(), {"alias": "r", "workflow": 99, "program_id": 25})
        saved = workflow_sources.read(_request(), {"alias": "r", "workflow": 99, "read": "saved_runs", "program_id": 25})
    assert out["latest_run"]["id"] == 3 and out["latest_run"]["summary"] == {"n": 3}
    assert "snapshot" not in out["latest_run"]
    assert out["latest_run"]["url"] == "/labs/workflow/99/run/?program_id=25&run_id=3"
    assert [r["id"] for r in saved["runs"]] == [3, 1]


@pytest.mark.django_db
def test_an_undeclared_alias_is_refused():
    from connect_labs.workflow import workflow_sources

    with pytest.raises(workflow_sources.WorkflowSourceError, match="declares no workflow source 'x'"):
        workflow_sources.find(_definition({"workflow_sources": [{"alias": "r", "workflow": 1}]}), "x")


# --- addresses and the home slot ------------------------------------------------


@pytest.fixture
def member_of_acme(monkeypatch):
    def may_use(caller, *, organization_id=None, program_id=None, opportunity_id=None):
        if organization_id not in (None, "acme") or program_id not in (None, 25, 26):
            return "not yours"
        return None

    monkeypatch.setattr(service.access, "may_use", may_use)
    monkeypatch.setattr("connect_labs.labs.context.get_org_data", lambda request: ORG_DATA)
    monkeypatch.setattr("connect_labs.workflow.page_views.get_org_data", lambda request: ORG_DATA)
    monkeypatch.setattr("connect_labs.workflow.page_views.save_context_to_session", lambda request, ctx: None)


def _serve(path, **kwargs):
    from connect_labs.workflow.page_views import PageView

    request = _request(path, context={})
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        wda = MockWDA.return_value
        wda.get_definition.return_value = _definition(PAGE)
        wda.get_render_code.return_value = MagicMock(data={"component_code": "function WorkflowUI() {}"})
        wda.get_workers.return_value = []
        response = PageView.as_view()(request, **kwargs)
        if hasattr(response, "render"):
            response.render()
        return response, wda, request


@pytest.mark.django_db
def test_an_org_with_no_home_says_so_and_points_at_settings(member_of_acme):
    response, wda, _ = _serve("/labs/p/org/acme/", scope_type="org", scope_key="acme")
    body = response.content.decode()
    assert response.status_code == 200 and 'data-testid="labs-page-none"' in body
    assert "/labs/settings/organization/acme/" in body
    wda.get_definition.assert_not_called()


@pytest.mark.django_db
def test_an_org_lands_on_its_home_page_owned_by_one_of_its_programmes(member_of_acme):
    service.update(
        "labs", Scope.of("organization", "acme"), {"home": {"fill": {"workflow": 47, "program_id": 25}}}, SYSTEM
    )
    response, wda, request = _serve("/labs/p/org/acme/", scope_type="org", scope_key="acme")
    body = response.content.decode()
    assert response.status_code == 200 and 'id="workflow-root"' in body
    # Read where the page lives, rendered as the organisation's page.
    assert request.labs_context == {"program_id": 25}
    assert '"type": "organization"' in body and "Acme Health" in body
    wda.create_run.assert_not_called()


@pytest.mark.django_db
def test_a_scope_the_viewer_is_not_in_is_refused_before_any_read(member_of_acme):
    from django.http import Http404

    with pytest.raises(Http404):
        _serve("/labs/p/org/other/", scope_type="org", scope_key="other")


@pytest.mark.django_db
def test_an_unknown_scope_word_is_not_found(member_of_acme):
    from django.http import Http404

    with pytest.raises(Http404):
        _serve("/labs/p/team/acme/", scope_type="team", scope_key="acme")


@pytest.mark.django_db
def test_a_page_by_slug_in_a_programme(member_of_acme):
    from connect_labs.workflow.page_views import PageView

    request = _request("/labs/p/programme/25/home/", context={})
    with (
        patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA,
        patch("connect_labs.workflow.data_access.WorkflowDataAccess") as MockFind,
    ):
        MockFind.return_value.list_definitions.return_value = [_definition({"name": "x"}, id_=1), _definition(PAGE)]
        wda = MockWDA.return_value
        wda.get_definition.return_value = _definition(PAGE)
        wda.get_render_code.return_value = MagicMock(data={"component_code": "function WorkflowUI() {}"})
        response = PageView.as_view()(request, scope_type="programme", scope_key="25", page="home")
        response.render()
    assert response.status_code == 200 and '"is_page": true' in response.content.decode()


@pytest.mark.django_db
def test_an_org_named_on_the_overview_lands_on_its_home(member_of_acme, client, django_user_model):
    from connect_labs.labs.views import LabsOverviewView

    service.update(
        "labs", Scope.of("organization", "acme"), {"home": {"fill": {"workflow": 47, "program_id": 25}}}, SYSTEM
    )
    named = _request("/labs/overview/?organization_id=acme", context={"organization_slug": "acme", "organization_id": 5})
    assert LabsOverviewView.as_view()(named)["Location"] == "/labs/p/org/acme/"


@pytest.mark.django_db
def test_a_remembered_org_does_not_redirect_the_overview(member_of_acme):
    from connect_labs.labs.views import LabsOverviewView

    service.update(
        "labs", Scope.of("organization", "acme"), {"home": {"fill": {"workflow": 47, "program_id": 25}}}, SYSTEM
    )
    remembered = _request("/labs/overview/", context={"organization_slug": "acme", "organization_id": 5})
    with patch("connect_labs.labs.views.LabsOverviewView.get_context_data", return_value={}):
        response = LabsOverviewView.as_view()(remembered)
    assert response.status_code == 200
