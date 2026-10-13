"""A scope's pages: the list at /labs/p/<scope>/<key>/pages/, a new page, "Make home",
the links on a page, and Settings' home picker (workflow/page_manager.py).

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.messages.storage.cookie import CookieStorage
from django.http import Http404
from django.test import RequestFactory
from django.urls import resolve

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config import service
from connect_labs.scope_config.models import ScopeConfigChange
from connect_labs.scope_config.scopes import Scope
from connect_labs.workflow.data_access import WorkflowDefinitionRecord
from connect_labs.workflow.tests import test_page_mode as pm

pytestmark = pytest.mark.django_db

member_of_acme = pm.member_of_acme
ACME = Scope.of("organization", "acme")


def _record(id_, name, slug, *, program_id=None, organization_id=None, opportunity_id=None, kind="page"):
    data = {"name": name, "page": {"slug": slug}, "config": {}}
    if kind:
        data["kind"] = kind
    return WorkflowDefinitionRecord(
        {"id": id_, "experiment": "workflow", "type": "workflow_definition", "opportunity_id": opportunity_id,
         "program_id": program_id, "organization_id": organization_id, "username": "maker", "data": data}
    )  # fmt: skip


RECORDS = [
    _record(47, "Nutrition home", "home", program_id=25),
    _record(48, "Report", "report", program_id=25, kind=None),  # a workflow with runs: not a page
    _record(49, "Malaria hub", "hub", program_id=26),
    _record(50, "Acme front door", "front", organization_id=5),
]


class FakeAccess:
    """WorkflowDataAccess over RECORDS, answering only for the scope it was opened in (as the API does)."""

    created = []
    updated = []

    def __init__(self, request=None, program_id=None, opportunity_id=None, organization_id=None, **_):
        self.program_id, self.opportunity_id, self.organization_id = program_id, opportunity_id, organization_id
        self.access_token = "stub-token"

    def list_definitions(self):
        out = []
        for r in RECORDS:
            if self.program_id and r.program_id == self.program_id:
                out.append(r)
            elif self.organization_id and str(r.organization_id) == str(self.organization_id):
                out.append(r)
        return out

    def update_definition(self, definition_id, data):
        FakeAccess.updated.append((definition_id, data))

    def close(self):
        pass


@pytest.fixture
def pages(member_of_acme, monkeypatch):
    FakeAccess.created, FakeAccess.updated = [], []
    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", FakeAccess)

    def create(access, template_key, request=None, program_id=None, **_):
        FakeAccess.created.append((template_key, program_id, access.organization_id))
        return MagicMock(id=77, data={"name": "Page", "kind": "page", "page": {"slug": "home"}}), None, None

    monkeypatch.setattr("connect_labs.workflow.templates.create_workflow_from_template", create)


def _request(method="get", path="/labs/p/org/acme/pages/", data=None, user=None):
    factory = RequestFactory()
    req = getattr(factory, method)(path, data or {})
    base = pm._request(path, context={})
    req.session, req.user, req.labs_context = base.session, user or base.user, {}
    req._messages = CookieStorage(req)
    return req


def _view(req, scope_type="org", scope_key="acme"):
    from connect_labs.workflow.page_views import PagesView

    response = PagesView.as_view()(req, scope_type=scope_type, scope_key=scope_key)
    return response


def _not_a_writer(monkeypatch):
    """Can read acme and its programs, but may change none of them."""
    monkeypatch.setattr(service, "write_refusal", lambda caller, scope, tree: "you may only look")


# --- the address -------------------------------------------------------------------


def test_pages_is_the_manager_not_a_page_called_pages():
    assert resolve("/labs/p/org/acme/pages/").url_name == "page_manager"
    assert resolve("/labs/p/org/acme/report/").url_name == "page"


def test_pages_is_a_reserved_slug_and_the_mcp_refuses_it():
    from connect_labs.workflow.page_manager import slug_problem

    assert "taken" in slug_problem("pages")
    assert slug_problem("Has Space") and slug_problem("-lead") and slug_problem("")
    assert slug_problem("stock-review") is None and slug_problem("old_home") is None


# --- listing -----------------------------------------------------------------------


def test_an_organisations_list_has_its_own_pages_and_its_programs_and_marks_the_home(pages):
    service.update("labs", ACME, {"home": {"fill": {"workflow": 49, "program_id": 26}}}, SYSTEM)

    response = _view(_request())
    body = response.content.decode()

    assert response.status_code == 200
    for name in ("Nutrition home", "Malaria hub", "Acme front door"):
        assert name in body
    assert "Report" not in body  # a workflow with runs is not a page
    assert body.count('data-testid="page-manager-is-home"') == 1
    # Home is Malaria hub (49): every other page offers Make home.
    assert body.count('data-testid="page-manager-make-home"') == 2
    assert "/labs/p/programme/26/hub/" in body and "/labs/p/org/acme/front/" in body
    assert "/labs/workflow/47/run/?program_id=25&amp;edit=true" in body
    assert 'data-testid="page-manager-new"' in body


def test_someone_who_may_not_change_the_scope_sees_the_list_read_only(pages, monkeypatch):
    _not_a_writer(monkeypatch)
    body = _view(_request()).content.decode()

    assert "Nutrition home" in body
    assert 'data-testid="page-manager-make-home"' not in body and 'data-testid="page-manager-new"' not in body
    assert 'data-testid="page-manager-edit"' not in body
    assert "You may only look" in body


def test_a_scope_the_viewer_is_not_in_is_404(pages):
    with pytest.raises(Http404):
        _view(_request(path="/labs/p/org/other/pages/"), scope_key="other")


# --- make home ---------------------------------------------------------------------


def test_make_home_writes_the_scopes_home_as_the_person_and_replaces_the_old_owner(pages):
    service.update("labs", ACME, {"home": {"fill": {"workflow": 47, "program_id": 25}}}, SYSTEM)

    response = _view(
        _request("post", data={"action": "make_home", "workflow": "50", "owner": "organization:acme", "label": "Door"})
    )

    assert response.status_code == 302 and response["Location"] == "/labs/p/org/acme/pages/"
    got = service.get("labs", ACME, SYSTEM)
    assert got["data"]["home"] == {"fill": {"workflow": 50, "organization_id": "acme"}, "label": "Door"}
    change = ScopeConfigChange.objects.order_by("-id").first()
    assert change.changed_by == "amina" and change.via == "pages"


def test_make_home_is_refused_on_the_server_to_someone_who_may_not_change_the_scope(pages, monkeypatch):
    _not_a_writer(monkeypatch)
    _view(_request("post", data={"action": "make_home", "workflow": "47", "owner": "program:25"}))

    assert not service.get("labs", ACME, SYSTEM)["data"].get("home")


def test_make_home_names_only_the_scopes_own_owners(pages):
    """A form naming program 99 (not acme's) is not turned into a fill."""
    _view(_request("post", data={"action": "make_home", "workflow": "47", "owner": "program:99"}))

    assert not service.get("labs", ACME, SYSTEM)["data"].get("home")


def test_make_home_from_a_page_lands_on_the_scopes_address_never_a_url_from_the_form(pages):
    from_page = _view(
        _request("post", data={"action": "make_home", "workflow": "47", "owner": "program:25", "return_to": "home"})
    )
    off = _view(
        _request(
            "post",
            data={
                "action": "make_home",
                "workflow": "47",
                "owner": "program:25",
                "next": "https://elsewhere.example/",
            },
        )
    )

    assert from_page["Location"] == "/labs/p/org/acme/"
    assert off["Location"] == "/labs/p/org/acme/pages/"


def test_clear_home(pages):
    service.update("labs", ACME, {"home": {"fill": {"workflow": 47, "program_id": 25}}}, SYSTEM)
    _view(_request("post", data={"action": "clear_home"}))
    assert not service.get("labs", ACME, SYSTEM)["data"].get("home")


# --- a new page --------------------------------------------------------------------


def test_a_new_page_is_made_from_page_blank_in_the_chosen_program_and_opens_in_the_editor(pages):
    response = _view(
        _request("post", data={"action": "create", "name": "Stock hub", "slug": "stock-hub", "owner": "program:26",
                               "make_home": "1"})
    )  # fmt: skip

    assert FakeAccess.created == [("page_blank", 26, None)]
    assert FakeAccess.updated[0][1]["name"] == "Stock hub" and FakeAccess.updated[0][1]["page"] == {
        "slug": "stock-hub"
    }
    assert response["Location"] == "/labs/workflow/77/run/?program_id=26&edit=true"
    assert service.get("labs", ACME, SYSTEM)["data"]["home"]["fill"] == {"workflow": 77, "program_id": 26}


def test_a_new_page_kept_by_a_real_organisation_is_owned_by_its_connect_id(pages):
    _view(_request("post", data={"action": "create", "name": "Door", "slug": "door", "owner": "organization:acme"}))
    assert FakeAccess.created == [("page_blank", None, 5)]


@pytest.mark.parametrize(
    "slug, why",
    [("pages", "taken"), ("hub", "already has a page"), ("Bad Slug", "lowercase")],
)
def test_a_new_page_at_a_reserved_taken_or_bad_address_is_refused(pages, slug, why):
    req = _request("post", data={"action": "create", "name": "X", "slug": slug, "owner": "program:26"})
    response = _view(req)

    assert response["Location"] == "/labs/p/org/acme/pages/" and FakeAccess.created == []
    assert any(why in str(m) for m in req._messages._queued_messages)


def test_a_new_page_is_refused_on_the_server_to_someone_who_may_not_change_the_scope(pages, monkeypatch):
    _not_a_writer(monkeypatch)
    _view(_request("post", data={"action": "create", "name": "X", "slug": "x", "owner": "program:26"}))
    assert FakeAccess.created == []


# --- on a page ---------------------------------------------------------------------


def _serve_page(slug="hub", user=None):
    from connect_labs.workflow.page_views import PageView

    request = pm._request(f"/labs/p/org/acme/{slug}/", context={})
    if user is not None:
        request.user = user
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA:
        MockWDA.return_value.get_definition.return_value = next(r for r in RECORDS if r.data["page"]["slug"] == slug)
        MockWDA.return_value.get_render_code.return_value = MagicMock(data={"component_code": "x"})
        MockWDA.return_value.get_workers.return_value = []
        response = PageView.as_view()(request, scope_type="org", scope_key="acme", page=slug)
        response.render()
    return response.content.decode()


def test_a_page_offers_its_editor_the_pages_list_and_make_this_the_home_page(pages):
    body = _serve_page("front")
    assert 'data-testid="labs-page-pages"' in body and "/labs/p/org/acme/pages/" in body
    assert 'data-testid="labs-page-make-home"' in body and 'value="organization:acme"' in body


def test_the_home_page_does_not_offer_to_become_the_home_page(pages):
    service.update("labs", ACME, {"home": {"fill": {"workflow": 50, "organization_id": "acme"}}}, SYSTEM)
    body = _serve_page("front")
    assert 'data-testid="labs-page-pages"' in body and 'data-testid="labs-page-make-home"' not in body


def test_a_viewer_who_may_not_change_the_scope_is_offered_neither(pages, monkeypatch):
    _not_a_writer(monkeypatch)
    body = _serve_page("front")
    assert 'data-testid="labs-page-pages"' not in body and 'data-testid="labs-page-make-home"' not in body


def test_no_home_points_at_pages(pages):
    response, _, _ = pm._serve("/labs/p/org/acme/", scope_type="org", scope_key="acme")
    assert 'href="/labs/p/org/acme/pages/"' in response.content.decode()


# --- Settings' home picker -----------------------------------------------------------


def _settings(method="get", data=None):
    from connect_labs.scope_config.views import SettingsView

    req = _request(method, path="/labs/settings/organization/acme/", data=data)
    response = SettingsView.as_view()(req, scope_type="organization", scope_key="acme")
    if hasattr(response, "render"):
        response.render()
    return response


def test_settings_offers_the_scopes_pages_as_its_home(pages):
    service.update("labs", ACME, {"home": {"fill": {"workflow": 49, "program_id": 26}}}, SYSTEM)
    body = _settings().content.decode()

    assert 'data-testid="settings-home-picker"' in body
    assert '<option value="program:26:49" selected>Malaria hub' in body
    assert '<option value="organization:acme:50" >Acme front door' in body
    assert "Advanced: edit as JSON" in body


def test_settings_home_picker_saves_and_clears(pages):
    _settings("post", {"namespace": "labs", "action": "set_home", "home": "program:25:47", "version": "0"})
    got = service.get("labs", ACME, SYSTEM)
    assert got["data"]["home"] == {"fill": {"workflow": 47, "program_id": 25}, "label": "Nutrition home"}

    _settings("post", {"namespace": "labs", "action": "set_home", "home": "", "version": str(got["version"])})
    assert not service.get("labs", ACME, SYSTEM)["data"].get("home")
