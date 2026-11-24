"""Personal pages: a page one person owns, shared with people and scopes (workflow/personal_pages.py).

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import json
import re
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.cookie import CookieStorage
from django.http import Http404
from django.test import RequestFactory
from django.urls import resolve

from connect_labs.labs.integrations.connect.api_client import LabsAPIError
from connect_labs.labs.synthetic.models import LabsLocalRecord
from connect_labs.scope_config import service
from connect_labs.workflow import personal_pages as pp
from connect_labs.workflow.data_access import WorkflowDataAccess

pytestmark = pytest.mark.django_db

LINKS = "function WorkflowUI() { return <a href='/labs/p/programme/25/'>Nutrition</a>; }"


def _user(username, email="", **extra):
    return get_user_model().objects.create(username=username, email=email or f"{username}@people.test", **extra)


@pytest.fixture
def owner():
    return _user("amina", "amina@example.org")


@pytest.fixture
def page(owner):
    return pp.create(owner, slug="demo", name="Demo launcher", render_code=LINKS)


@pytest.fixture
def in_programme_25(monkeypatch):
    """amina and dina are members of programme 25, and of nothing else; no one else is."""

    def may_use(caller, *, organization_id=None, program_id=None, opportunity_id=None):
        member = getattr(caller.user, "username", "") in ("amina", "dina")
        return None if (member and program_id == 25 and organization_id is None and opportunity_id is None) else "no"

    monkeypatch.setattr(service.access, "may_use", may_use)


def _request(user, path="/labs/p/user/amina/demo/", method="get", data=None):
    req = getattr(RequestFactory(), method)(path, data or {})
    req.user, req.session, req.labs_context = user, {}, {}
    req._messages = CookieStorage(req)
    return req


def _open(user, username="amina", slug="demo"):
    from connect_labs.workflow.page_views import PersonalPageView

    response = PersonalPageView.as_view()(
        _request(user, f"/labs/p/user/{username}/{slug}/"), username=username, page=slug
    )
    return response


def _workflow_data(response):
    found = re.search(
        r'<script id="workflow-data" type="application/json">(.*?)</script>', response.content.decode(), re.S
    )
    return json.loads(found.group(1))


# -- where it lives ---------------------------------------------------------------------------


def test_a_personal_scope_meets_no_opportunity_programme_or_commcare_project():
    from connect_labs.hqdata.scope import hq_scope_id, is_hq_scope

    sid = pp.personal_scope_id(7)
    assert pp.is_personal_scope(sid) and pp.is_personal_scope(str(sid))
    assert not is_hq_scope(sid)
    assert (
        not pp.is_personal_scope(hq_scope_id(1)) and not pp.is_personal_scope(-25) and not pp.is_personal_scope(10133)
    )


def test_a_new_page_is_kept_in_labs_under_its_own_scope_and_shared_with_no_one(page):
    rows = LabsLocalRecord.objects.filter(opportunity_id=pp.personal_scope_id(page.pk))
    assert sorted(rows.values_list("type", flat=True)) == ["workflow_definition", "workflow_render_code"]
    definition = rows.get(type="workflow_definition")
    assert definition.id == page.definition_id and definition.data["kind"] == "page"
    assert definition.data["page"] == {"slug": "demo"} and definition.data["name"] == "Demo launcher"
    assert page.shared_people == [] and page.shared_scopes == []
    assert pp.url(page) == "/labs/p/user/amina/demo/"


def test_two_of_your_pages_cannot_share_an_address(owner, page):
    with pytest.raises(pp.PageProblem):
        pp.create(owner, slug="demo", name="Again")
    with pytest.raises(pp.PageProblem):
        pp.create(owner, slug="pages", name="Reserved")


# -- who may open and change it ---------------------------------------------------------------


def test_the_owner_opens_it_in_the_sandbox_with_no_data_endpoints(owner, page):
    response = _open(owner)
    assert response.status_code == 200
    data = _workflow_data(response)
    assert data["render_code"] == LINKS and data["is_page"] is True
    assert data["sandbox"]["mode"] == "page"
    assert all(v is None for v in data["apiEndpoints"].values())  # it reads nothing
    assert data["scope"]["type"] == "user" and data["scope"]["key"] == "amina"
    assert data["scope"]["program"] is None and data["scope"]["opportunities"] == []
    assert response["Content-Security-Policy"].startswith("frame-src ")
    assert b"Your page" in response.content


def test_the_owner_changes_it(owner, page):
    pp.update(owner, page, name="Launcher", render_code="function WorkflowUI() { return null; }")
    definition, code = pp.load(owner, page)
    assert definition.data["name"] == "Launcher" and "return null" in code


def test_a_named_person_opens_it_but_cannot_change_it(owner, page):
    by_name, by_email = _user("bilal"), _user("chen", "Chen@Example.org")
    pp.set_sharing(owner, page, people=["bilal", "chen@example.org"])
    for viewer in (by_name, by_email):
        assert _open(viewer).status_code == 200
        with pytest.raises(PermissionError):
            pp.update(viewer, page, name="Mine now")
        with pytest.raises(PermissionError):
            pp.set_sharing(viewer, page, people=[viewer.username])
        # Underneath too: their data access over the page's records refuses every write.
        dao = pp.personal_dao(viewer, page)
        with pytest.raises(LabsAPIError):
            dao.save_render_code(page.definition_id, "function WorkflowUI() { return 'x'; }", version=2)
        dao.close()
    assert pp.load(owner, page)[1] == LINKS


def test_a_member_of_a_shared_programme_opens_it(owner, page, in_programme_25):
    member = _user("dina")
    pp.set_sharing(owner, page, scopes=["programme:25"])
    assert page.shared_scopes == ["program:25"]
    assert _open(member).status_code == 200
    assert pp.edit_refusal(member, page) == pp.OWNER_ONLY


def test_anyone_else_gets_a_404_before_the_definition_is_read(owner, page, in_programme_25):
    pp.set_sharing(owner, page, people=["bilal"], scopes=["program:25"])
    stranger = _user("eve")
    with patch.object(pp, "load") as load, patch.object(WorkflowDataAccess, "get_definition") as read:
        with pytest.raises(Http404):
            _open(stranger)
        with pytest.raises(Http404):
            _open(stranger, slug="no-such-page")
    load.assert_not_called()
    read.assert_not_called()
    with pytest.raises(pp.PageNotFound):
        pp.personal_dao(stranger, page)


def test_shared_with_dimagi_it_opens_for_every_dimagi_sign_in_and_no_one_else(owner, page):
    # No membership is asked of the person sharing: the owner here is in no scope at all.
    pp.set_sharing(owner, page, scopes=["Dimagi"])
    assert page.shared_scopes == [pp.DIMAGI_SHARE]
    for email in ("kofi@dimagi.com", "agent@dimagi-ai.com", "lena@dimagi-associate.com"):
        person = _user(email.split("@")[0], email)
        assert _open(person).status_code == 200
        assert pp.edit_refusal(person, page) == pp.OWNER_ONLY
        assert page in pp.shared_with(person)
    outsider = _user("mallory", "mallory@dimagi.com.example.org")
    with pytest.raises(Http404):
        _open(outsider)
    assert pp.view_refusal(outsider, page) == pp.NOT_SHARED


def test_dimagi_sits_beside_other_scopes_and_is_removed_like_one(owner, page, in_programme_25):
    pp.set_sharing(owner, page, scopes=["program:25", "dimagi", "dimagi"])
    assert page.shared_scopes == ["program:25", pp.DIMAGI_SHARE]
    assert _open(_user("dina")).status_code == 200
    pp.set_sharing(owner, page, scopes=["program:25"])
    with pytest.raises(Http404):
        _open(_user("kofi", "kofi@dimagi.com"))


def test_dimagi_staff_may_open_and_change_it(page):
    staff = _user("fatou", is_staff=True)
    assert _open(staff).status_code == 200
    pp.update(staff, page, name="Fixed")


def test_a_scope_you_are_not_in_cannot_be_shared_with_and_a_bad_one_is_refused(owner, page, in_programme_25):
    with pytest.raises(PermissionError):
        pp.set_sharing(owner, page, scopes=["program:26"])
    with pytest.raises(pp.PageProblem):
        pp.set_sharing(owner, page, scopes=["team:25"])
    with pytest.raises(pp.PageProblem):
        pp.set_sharing(owner, page, people=["two words"])
    assert page.shared_scopes == []


# -- never a labs context ---------------------------------------------------------------------


def test_a_personal_scope_is_never_a_labs_context_and_needs_a_person(owner, page):
    from connect_labs.labs.context import validate_context_access

    sid = pp.personal_scope_id(page.pk)
    request = _request(owner)
    assert "opportunity_id" not in validate_context_access(request, {"opportunity_id": sid})
    with pytest.raises(PermissionError):
        WorkflowDataAccess(opportunity_id=sid, access_token="someone-elses-token")


# -- your pages, in labs ----------------------------------------------------------------------


def test_the_addresses_route_to_the_personal_page_views():
    from connect_labs.workflow import page_views

    assert resolve("/labs/p/me/").func.view_class is page_views.MyPagesView
    assert resolve("/labs/p/me/demo/").func is page_views.my_page
    assert resolve("/labs/p/user/amina/demo/").func.view_class is page_views.PersonalPageView
    assert resolve("/labs/p/user/a.b@example.org/demo/").kwargs["username"] == "a.b@example.org"


def test_your_pages_list_makes_one_and_shares_it(owner):
    from connect_labs.workflow.page_views import MyPagesView, my_page

    view = MyPagesView.as_view()
    made = view(_request(owner, "/labs/p/me/", "post", {"action": "create", "name": "Demo", "slug": "demo"}))
    assert made.status_code == 302
    page = pp.find("amina", "demo")
    shared = view(
        _request(
            owner, "/labs/p/me/", "post", {"action": "share", "slug": "demo", "people": "bilal\nchen@example.org"}
        )
    )
    assert shared.status_code == 302
    page.refresh_from_db()
    assert page.shared_people == ["bilal", "chen@example.org"]
    listing = view(_request(owner, "/labs/p/me/"))
    assert listing.status_code == 200 and b"/labs/p/user/amina/demo/" in listing.content
    assert my_page(_request(owner, "/labs/p/me/demo/"), page="demo")["Location"] == "/labs/p/user/amina/demo/"


def test_a_page_shared_with_you_is_listed_as_yours_to_open(owner, page):
    bilal = _user("bilal")
    pp.set_sharing(owner, page, people=["bilal"])
    assert pp.shared_with(bilal) == [page]
    assert pp.shared_with(_user("eve")) == []


# -- over MCP ---------------------------------------------------------------------------------


def test_the_mcp_tools_make_share_and_read_a_page(owner, monkeypatch):
    from connect_labs.mcp.tool_registry import MCPToolError
    from connect_labs.mcp.tools import personal_pages as tools

    monkeypatch.setattr(tools, "_token", lambda user: None)
    made = tools.personal_page_create(owner, slug="demo", name="Demo", render_code=LINKS, people=["bilal"])
    assert made["url"].endswith("/labs/p/user/amina/demo/") and made["shared_people"] == ["bilal"]
    bilal = _user("bilal")
    got = tools.personal_page_get(bilal, slug="demo", owner="amina")
    assert got["render_code"] == LINKS and got["can_edit"] is False
    with pytest.raises(MCPToolError) as refused:
        tools.personal_page_update(bilal, slug="demo", owner="amina", name="Mine")
    assert refused.value.code == "PERMISSION_DENIED"
    with pytest.raises(MCPToolError) as hidden:
        tools.personal_page_get(_user("eve"), slug="demo", owner="amina")
    assert hidden.value.code == "NOT_FOUND"
    tools.personal_page_update(owner, slug="demo", render_code="function WorkflowUI() { return null; }")
    tools.personal_page_share(owner, slug="demo", people=[])
    assert tools.personal_page_list(bilal)["shared_with_me"] == []
    assert [p["slug"] for p in tools.personal_page_list(owner)["mine"]] == ["demo"]
    tools.personal_page_delete(owner, slug="demo")
    assert pp.find("amina", "demo") is None
