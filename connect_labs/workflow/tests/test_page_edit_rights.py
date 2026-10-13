"""Who edits a page, and a page made from page_blank before `kind` existed counting as one.

The rule (page_mode.page_edit_refusal): whoever may write the page's owning scope --
the Settings write rule for that scope -- plus Dimagi staff. It is enforced on the Edit
link, in edit mode, and on the render-code save and rename endpoints.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config import service
from connect_labs.scope_config.scopes import Scope
from connect_labs.workflow.data_access import WorkflowDefinitionRecord
from connect_labs.workflow.tests import test_page_mode as pm

pytestmark = pytest.mark.django_db

member_of_acme = pm.member_of_acme

# Made from page_blank before `kind` was written onto pages: a page all the same.
OLD_PAGE = {"name": "Old home", "templateType": "page_blank", "opportunity_ids": [], "config": {}}


def _record(data, *, program_id=25, username="someone-else"):
    return WorkflowDefinitionRecord(
        {"id": 47, "experiment": "workflow", "type": "workflow_definition", "opportunity_id": None,
         "program_id": program_id, "username": username, "data": data}
    )  # fmt: skip


def _not_in_programme_25(monkeypatch):
    """The viewer is in organisation acme, and programme 26, but not programme 25."""

    def may_use(caller, *, organization_id=None, program_id=None, opportunity_id=None):
        if organization_id not in (None, "acme") or program_id not in (None, 26):
            return "not yours"
        return None

    monkeypatch.setattr(service.access, "may_use", may_use)


# --- the Edit page link --------------------------------------------------------


def _serve_home(definition, user=None):
    from connect_labs.workflow.page_views import PageView

    service.update(
        "labs", Scope.of("organization", "acme"), {"home": {"fill": {"workflow": 47, "program_id": 25}}}, SYSTEM
    )
    request = pm._request("/labs/p/org/acme/", context={})
    if user is not None:
        request.user = user
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        MockWDA.return_value.get_definition.return_value = definition
        MockWDA.return_value.get_render_code.return_value = MagicMock(data={"component_code": "x"})
        MockWDA.return_value.get_workers.return_value = []
        response = PageView.as_view()(request, scope_type="org", scope_key="acme")
        response.render()
    return response.content.decode()


def test_a_member_of_the_pages_programme_who_did_not_write_it_is_offered_edit_page(member_of_acme):
    """Was staff-or-author only; a member of the owning programme now edits its page."""
    html = _serve_home(_record(pm.PAGE, username="someone-else"))
    assert 'data-testid="labs-page-edit"' in html
    assert "/labs/workflow/47/run/?program_id=25&amp;edit=true" in html


def test_writing_a_page_does_not_make_you_its_editor_once_you_cannot_change_its_scope(member_of_acme, monkeypatch):
    _not_in_programme_25(monkeypatch)
    from connect_labs.workflow import page_mode

    request = pm._request("/labs/p/org/acme/", context={})
    # The author: authorship alone no longer decides it.
    assert page_mode.page_edit_refusal(request, _record(pm.PAGE, username="amina"), {"program_id": 25})


def test_staff_may_edit_any_page(member_of_acme, monkeypatch):
    from connect_labs.workflow import page_mode

    _not_in_programme_25(monkeypatch)
    request = pm._request("/labs/p/org/acme/", context={})
    request.user.is_staff = True
    assert page_mode.page_edit_refusal(request, _record(pm.PAGE), {"program_id": 25}) is None


def test_a_dimagi_sign_in_is_staff_for_editing_a_page(member_of_acme, monkeypatch):
    """Staff was Django `is_staff` alone, which no OAuth-created labs user carries."""
    from connect_labs.workflow import page_mode

    _not_in_programme_25(monkeypatch)
    request = pm._request("/labs/p/org/acme/", context={})
    assert page_mode.page_edit_refusal(request, _record(pm.PAGE), {"program_id": 25})
    request.user.email = "amina@dimagi.com"
    assert page_mode.page_edit_refusal(request, _record(pm.PAGE), {"program_id": 25}) is None


def test_an_organisation_owned_page_is_edited_by_whoever_may_write_the_organisation(member_of_acme):
    from connect_labs.workflow import page_mode

    request = pm._request("/labs/p/org/acme/", context={})
    org_record = WorkflowDefinitionRecord(
        {"id": 47, "experiment": "workflow", "type": "workflow_definition", "opportunity_id": None,
         "program_id": None, "organization_id": 5, "data": pm.PAGE}
    )  # fmt: skip
    # From the record: Connect id 5 is acme in the viewer's tree.
    assert page_mode.page_owner_scope(org_record, pm.ORG_DATA) == Scope.of("organization", "acme")
    assert page_mode.page_edit_refusal(request, org_record) is None
    # An organisation id the viewer's tree does not hold names no one they can be.
    stranger = WorkflowDefinitionRecord({**org_record.__dict__, "data": pm.PAGE})
    stranger.organization_id = 999
    assert page_mode.page_owner_scope(stranger, pm.ORG_DATA) is None
    assert page_mode.page_edit_refusal(request, stranger)


# --- the server: edit mode, render-code save, rename -----------------------------


def test_edit_mode_on_a_page_is_refused_to_someone_who_cannot_change_its_scope(member_of_acme, monkeypatch):
    _not_in_programme_25(monkeypatch)
    request = pm._request("/labs/workflow/47/run/?program_id=25&edit=true", context={"program_id": 25})
    context, wda = pm._run_context(pm.PAGE, request=request)
    assert context.get("edit_refused") is True
    assert "not edit it" in context["error"]
    assert "render_code" not in context
    wda.get_render_code.assert_not_called()


def test_edit_mode_on_a_page_opens_for_a_member(member_of_acme):
    request = pm._request("/labs/workflow/47/run/?program_id=25&edit=true", context={"program_id": 25})
    context, _ = pm._run_context(pm.PAGE, request=request)
    assert not context.get("edit_refused")
    assert context.get("render_code")


def _post(view, body, user):
    request = RequestFactory().post("/x/", data=json.dumps(body), content_type="application/json")
    request.user = user
    request.labs_context = {"program_id": 25}
    request.session = {"labs_oauth": {"access_token": "t", "organization_data": pm.ORG_DATA}}
    return request


def _amina():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.get_or_create(username="amina")[0]


@pytest.mark.parametrize("data", [pm.PAGE, OLD_PAGE], ids=["kind", "page_blank"])
def test_saving_a_pages_render_code_is_refused_to_someone_who_cannot_change_its_scope(
    member_of_acme, monkeypatch, data
):
    from connect_labs.workflow.views import save_render_code_api

    _not_in_programme_25(monkeypatch)
    request = _post(save_render_code_api, {"component_code": "function WorkflowUI() {}"}, _amina())
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        MockWDA.return_value.get_definition.return_value = _record(data, username="amina")
        response = save_render_code_api(request, 47)
    assert response.status_code == 403
    MockWDA.return_value.save_render_code.assert_not_called()
    MockWDA.return_value.update_definition.assert_not_called()


def test_a_member_saves_a_pages_render_code(member_of_acme):
    from connect_labs.workflow.views import save_render_code_api

    request = _post(save_render_code_api, {"component_code": "function WorkflowUI() {}"}, _amina())
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        MockWDA.return_value.get_definition.return_value = _record(pm.PAGE)
        MockWDA.return_value.save_render_code.return_value = MagicMock(id=9)
        response = save_render_code_api(request, 47)
    assert response.status_code == 200
    MockWDA.return_value.save_render_code.assert_called_once()


def test_renaming_a_page_is_refused_to_someone_who_cannot_change_its_scope(member_of_acme, monkeypatch):
    from connect_labs.workflow.views import rename_workflow_api

    _not_in_programme_25(monkeypatch)
    request = _post(rename_workflow_api, {"name": "Mine now"}, _amina())
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        MockWDA.return_value.get_definition.return_value = _record(pm.PAGE)
        response = rename_workflow_api(request, 47)
    assert response.status_code == 403
    MockWDA.return_value.update_definition.assert_not_called()


def test_a_workflows_render_code_save_is_left_to_the_record_acl(member_of_acme, monkeypatch):
    """Not a page: unchanged, the LabsRecord write decides."""
    from connect_labs.workflow.views import save_render_code_api

    _not_in_programme_25(monkeypatch)
    request = _post(save_render_code_api, {"component_code": "function WorkflowUI() {}"}, _amina())
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        MockWDA.return_value.get_definition.return_value = _record({"name": "Report", "config": {}})
        MockWDA.return_value.save_render_code.return_value = MagicMock(id=9)
        response = save_render_code_api(request, 47)
    assert response.status_code == 200


# --- "is this a page": page_blank without `kind` ---------------------------------


def test_the_workflow_list_row_marks_a_page_made_from_page_blank():
    from connect_labs.workflow.views import WorkflowListView

    view = WorkflowListView()
    access = MagicMock()
    access.list_definitions.return_value = []
    old = _record(OLD_PAGE)
    assert view._build_workflow_row(old, [], access, {}, {})["is_page"] is True
    assert view._build_workflow_row(_record({"name": "Report", "config": {}}), [], access, {}, {})["is_page"] is False


@pytest.mark.parametrize(
    "module, call",
    [
        ("workflow_create_run", lambda m, user: m.workflow_create_run(user=user, definition_id=47, program_id=25)),
        ("workflow_run_default", lambda m, user: m.workflow_run_default(user=user, definition_id=47, program_id=25)),
    ],
)
def test_the_mcp_run_tools_refuse_a_page_made_from_page_blank(module, call):
    import importlib

    from connect_labs.mcp.tool_registry import MCPToolError

    m = importlib.import_module(f"connect_labs.mcp.tools.{module}")
    with patch.object(m, "_wda_for_user") as wda_for_user:
        wda_for_user.return_value.get_definition.return_value = _record(OLD_PAGE)
        with pytest.raises(MCPToolError, match="is a page"):
            call(m, _amina())
        wda_for_user.return_value.create_run.assert_not_called()
