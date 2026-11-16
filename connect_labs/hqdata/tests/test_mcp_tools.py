"""The hq_* MCP tools, the scope that reaches them, and the door a CommCare-only person comes in by."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

import connect_labs.hqdata.mcp_tools  # noqa: F401 -- registers the tools
from connect_labs.hqdata.access import create_project
from connect_labs.hqdata.models import HQCase, HQForm, HQPipeline, HQProject, HQProjectMember, HQSource
from connect_labs.hqdata.tests.conftest import make_hq_user
from connect_labs.mcp import server as mcp_server
from connect_labs.mcp import token_scopes
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.tool_registry import _REGISTRY, MCPToolError, get_tool
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"


def call(tool, user, **args):
    return get_tool(tool).handler(user=user, **args)


@pytest.fixture
def me(db):
    return UserFactory()


@pytest.fixture
def project(me):
    project = create_project(me, "demo", hq_host=HOST, can_read_domain=lambda: True)
    HQSource.objects.create(project=project, kind="forms")
    HQSource.objects.create(project=project, kind="cases")
    return project


def stored_form(project, form_id, username, *, q="a", xmlns="http://x/1", at=1, archived=False):
    return HQForm.objects.create(
        project_id=project.pk,
        form_id=form_id,
        xmlns=xmlns,
        app_id="app1",
        username=username,
        archived=archived,
        received_on=datetime(2026, 11, at, 10, tzinfo=timezone.utc),
        form_json={"id": form_id, "form": {"q": q, "meta": {"username": username}}},
    )


# -- the scope and its tool list -------------------------------------------------------------


def test_every_hq_tool_is_named_by_the_commcare_scope_and_the_scope_names_nothing_else():
    registered = {name for name in _REGISTRY if name.startswith("hq_")}
    assert registered == set(token_scopes.COMMCARE_TOOLS)


def test_no_other_scope_reaches_an_hq_tool():
    hq = set(token_scopes.COMMCARE_TOOLS)
    assert not hq & set(token_scopes.RESTRICTED_TOOLS)
    assert not hq & set(token_scopes.NO_USERVISIT_DATA_TOOLS)
    assert not hq & set(token_scopes.DEFINITION_WRITE_TOOLS)


def test_a_commcare_token_reaches_the_commcare_tools_and_the_jobs_they_start():
    token = SimpleNamespace(claims={"auth_method": "pat"}, scopes=[token_scopes.COMMCARE_SCOPE_STRING])
    assert mcp_server.allowed_tools(token) == token_scopes.COMMCARE_TOOLS | token_scopes.JOB_TOOLS


def test_a_full_token_still_reaches_everything_including_them():
    token = SimpleNamespace(
        claims={"auth_method": "pat"}, scopes=[token_scopes.TOKEN_SCOPE_STRINGS[token_scopes.FULL]]
    )
    assert mcp_server.allowed_tools(token) is None


def test_a_commcare_token_asked_of_a_restricted_endpoint_reaches_nothing():
    token = SimpleNamespace(
        claims={"auth_method": "pat"},
        scopes=[token_scopes.COMMCARE_SCOPE_STRING, token_scopes.TOKEN_SCOPE_STRINGS[token_scopes.NO_USERVISIT_DATA]],
    )
    assert mcp_server.allowed_tools(token) == frozenset()


# -- the door --------------------------------------------------------------------------------


def test_a_commcare_only_person_gets_in_with_a_commcare_token_and_nothing_else(client):
    user = make_hq_user(client)
    _t, commcare = MCPAccessToken.create_token(user, "n", scope=token_scopes.COMMCARE)
    _t, no_visits = MCPAccessToken.create_token(user, "n", scope=token_scopes.NO_USERVISIT_DATA)
    resolved = mcp_server._verify_bearer_sync(commcare)
    assert resolved is not None and resolved[0].pk == user.pk and resolved[3] == [token_scopes.COMMCARE_SCOPE_STRING]
    assert mcp_server._verify_bearer_sync(no_visits) is None


def test_a_connect_person_may_hold_a_commcare_token_too_and_it_stays_narrow(client):
    from django.utils import timezone as dj_timezone

    from connect_labs.labs.models import UserConnectToken

    user = UserFactory()
    UserConnectToken.objects.create(
        user=user, access_token="a", refresh_token="r", expires_at=dj_timezone.now() + timedelta(hours=1)
    )
    _t, raw = MCPAccessToken.create_token(user, "n", scope=token_scopes.COMMCARE)
    resolved = mcp_server._verify_bearer_sync(raw)
    assert resolved is not None
    assert (
        mcp_server.allowed_tools(SimpleNamespace(claims={"auth_method": "pat"}, scopes=resolved[3]))
        == token_scopes.COMMCARE_TOOLS | token_scopes.JOB_TOOLS
    )


# -- reaching a project ----------------------------------------------------------------------

PROJECT_TOOLS = [
    "hq_project_get",
    "hq_project_sync",
    "hq_project_set_storage",
    "hq_forms",
    "hq_cases",
    "hq_pipeline_list",
    "hq_pipeline_save",
    "hq_pipeline_run",
    "hq_pipeline_delete",
]


@pytest.mark.parametrize("tool", PROJECT_TOOLS)
def test_a_project_i_am_not_in_is_not_found_by_every_tool_and_so_is_one_that_is_not_there(project, tool):
    stranger = UserFactory()
    for project_id in (project.pk, 999999):
        with pytest.raises(MCPToolError) as caught:
            call(tool, stranger, project_id=project_id, name="x", store_kind="forms", schema={}, storage_mode="live")
        assert caught.value.code == "NOT_FOUND"


@pytest.mark.parametrize("project_id", [None, "1", 1.5, [1]])
def test_a_project_id_that_is_not_an_integer_is_not_found_not_a_crash(me, project, project_id):
    with pytest.raises(MCPToolError):
        call("hq_project_get", me, project_id=project_id)


def test_list_and_get_describe_my_projects_only(me, project):
    other = create_project(UserFactory(), "theirs", hq_host=HOST, can_read_domain=lambda: True)
    stored_form(project, "f1", "ann")
    HQCase.objects.create(project_id=project.pk, case_id="c1", case_type="child")
    listed = call("hq_project_list", me)["projects"]
    assert [p["domain"] for p in listed] == ["demo"] and other.domain not in str(listed)
    got = call("hq_project_get", me, project_id=project.pk)
    assert got["stored"] == {"forms": 1, "cases": 1} and {s["kind"] for s in got["sources"]} == {"forms", "cases"}


# -- reading ---------------------------------------------------------------------------------


def test_forms_are_filtered_paged_and_projected(me, project):
    stored_form(project, "f1", "ann", q="one", at=1)
    stored_form(project, "f2", "bob", q="two", at=2)
    stored_form(project, "f3", "ann", q="three", at=3, xmlns="http://x/2")
    stored_form(project, "f4", "ann", q="gone", at=4, archived=True)
    HQForm.objects.filter(form_id="f2").update(deleted_at=datetime(2026, 11, 9, tzinfo=timezone.utc))

    everything = call("hq_forms", me, project_id=project.pk)
    assert everything["total"] == 2 and [f["form_id"] for f in everything["forms"]] == ["f1", "f3"]
    assert [f["form_id"] for f in call("hq_forms", me, project_id=project.pk, include_archived=True)["forms"]] == [
        "f1",
        "f3",
        "f4",
    ]
    assert [f["form_id"] for f in call("hq_forms", me, project_id=project.pk, xmlns="http://x/2")["forms"]] == ["f3"]
    assert call("hq_forms", me, project_id=project.pk, username="bob")["total"] == 0  # bob's form was deleted
    window = call(
        "hq_forms",
        me,
        project_id=project.pk,
        received_after="2026-11-02T00:00:00Z",
        received_before="2026-11-04T00:00:00Z",
    )
    assert [f["form_id"] for f in window["forms"]] == ["f3"]
    page = call("hq_forms", me, project_id=project.pk, limit=1, offset=1)
    assert page["total"] == 2 and [f["form_id"] for f in page["forms"]] == ["f3"]
    picked = call("hq_forms", me, project_id=project.pk, fields=["form.q", "form.meta.username", "form.nope"])[
        "forms"
    ][0]["values"]
    assert picked == {"form.q": "one", "form.meta.username": "ann", "form.nope": None}


@pytest.mark.parametrize(
    "args",
    [
        {"limit": "x"},
        {"offset": "y"},
        {"fields": "form.q"},
        {"fields": [1]},
        {"fields": ["a"] * 51},
        {"received_after": "yesterday"},
    ],
)
def test_bad_arguments_are_refused_with_a_reason(me, project, args):
    with pytest.raises(MCPToolError) as caught:
        call("hq_forms", me, project_id=project.pk, **args)
    assert caught.value.code == "INVALID_SCHEMA"


def test_a_call_cannot_ask_for_more_than_the_cap(me, project):
    for i in range(3):
        stored_form(project, f"f{i}", "ann", at=i + 1)
    assert len(call("hq_forms", me, project_id=project.pk, limit=10_000)["forms"]) == 3
    assert call("hq_forms", me, project_id=project.pk, limit=0)["forms"].__len__() == 1  # clamped up to one


def test_cases_are_filtered_by_type_owner_state_and_exact_properties(me, project):
    HQCase.objects.create(
        project_id=project.pk, case_id="c1", case_type="child", owner_id="o1", properties={"village": "A", "age": "4"}
    )
    HQCase.objects.create(
        project_id=project.pk, case_id="c2", case_type="child", owner_id="o2", closed=True, properties={"village": "B"}
    )
    HQCase.objects.create(project_id=project.pk, case_id="c3", case_type="household", properties={"village": "A"})

    def ids(**kw):
        return sorted(c["case_id"] for c in call("hq_cases", me, project_id=project.pk, **kw)["cases"])

    assert ids() == ["c1", "c2", "c3"]
    assert ids(case_type="child") == ["c1", "c2"]
    assert ids(owner_id="o2") == ["c2"]
    assert ids(closed=True) == ["c2"] and ids(closed=False) == ["c1", "c3"]
    assert ids(properties={"village": "A"}) == ["c1", "c3"]
    assert ids(case_type="child", properties={"village": "A"}) == ["c1"]
    got = call(
        "hq_cases",
        me,
        project_id=project.pk,
        case_type="child",
        properties={"village": "A"},
        fields=["properties.age"],
    )
    assert got["cases"][0]["values"] == {"properties.age": "4"}


@pytest.mark.parametrize(
    "properties", [{"a b": "x"}, {"x": "y", "../z": "w"}, {f"k{i}": "v" for i in range(11)}, "village=A", ["village"]]
)
def test_a_property_filter_cannot_name_something_other_than_a_property(me, project, properties):
    with pytest.raises(MCPToolError):
        call("hq_cases", me, project_id=project.pk, properties=properties)


# -- changing --------------------------------------------------------------------------------

SCHEMA = {
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [{"name": "n", "path": "form.q", "aggregation": "count"}],
}


def test_a_pipeline_is_saved_replaced_run_listed_and_deleted(me, project):
    stored_form(project, "f1", "ann")
    stored_form(project, "f2", "ann", at=2)
    saved = call("hq_pipeline_save", me, project_id=project.pk, name="per worker", store_kind="forms", schema=SCHEMA)
    again = call(
        "hq_pipeline_save",
        me,
        project_id=project.pk,
        name="per worker",
        store_kind="forms",
        schema=SCHEMA,
        xmlns="http://x/1",
    )
    assert again["id"] == saved["id"] and HQPipeline.objects.get(pk=saved["id"]).xmlns == "http://x/1"
    assert [p["name"] for p in call("hq_pipeline_list", me, project_id=project.pk)["pipelines"]] == ["per worker"]
    out = call("hq_pipeline_run", me, project_id=project.pk, name="per worker")
    assert out["count"] == 1 and out["rows"][0]["n"] == 2
    assert call("hq_pipeline_run", me, project_id=project.pk, pipeline_id=saved["id"], fresh=True)["count"] == 1
    call("hq_pipeline_delete", me, project_id=project.pk, name="per worker")
    assert not HQPipeline.objects.exists()


@pytest.mark.parametrize("args", [{"name": ""}, {"store_kind": "nope"}, {"schema": []}, {"schema": {"fields": []}}])
def test_a_pipeline_that_cannot_run_is_not_saved(me, project, args):
    base = {"name": "p", "store_kind": "forms", "schema": SCHEMA, **args}
    with pytest.raises(MCPToolError) as caught:
        call("hq_pipeline_save", me, project_id=project.pk, **base)
    assert caught.value.code == "INVALID_SCHEMA" and not HQPipeline.objects.exists()


def test_a_pipeline_of_the_wrong_name_is_not_found_and_another_projects_does_not_open_here(me, project):
    other = create_project(me, "second", hq_host=HOST, can_read_domain=lambda: True)
    saved = call("hq_pipeline_save", me, project_id=other.pk, name="p", store_kind="forms", schema=SCHEMA)
    for args in ({"name": "missing"}, {"pipeline_id": saved["id"]}):
        with pytest.raises(MCPToolError) as caught:
            call("hq_pipeline_run", me, project_id=project.pk, **args)
        assert caught.value.code == "NOT_FOUND"


def test_only_an_author_or_an_admin_can_delete_a_pipeline(project):
    member = UserFactory()
    HQProjectMember.objects.create(project=project, user=member, role="member")
    pipeline = HQPipeline.objects.create(
        project=project, name="theirs", store_kind="forms", schema=SCHEMA, created_by=project.created_by
    )
    with pytest.raises(MCPToolError) as caught:
        call("hq_pipeline_delete", member, project_id=project.pk, pipeline_id=pipeline.pk)
    assert caught.value.code == "PERMISSION_DENIED" and HQPipeline.objects.filter(pk=pipeline.pk).exists()
    call("hq_pipeline_delete", project.created_by, project_id=project.pk, pipeline_id=pipeline.pk)
    assert not HQPipeline.objects.exists()


def ran(results=None):
    """Stands in for the sync itself: what sync_project would hand back for one finished source."""
    return mock.patch("connect_labs.hqdata.jobs.sync_project", return_value=results or {})


def test_sync_is_a_durable_job_that_reports_progress():
    tool = get_tool("hq_project_sync")
    assert tool.long_running and tool.wants_progress and tool.is_write


def test_any_member_can_sync_but_a_full_read_needs_an_admin(me, project):
    member = UserFactory()
    HQProjectMember.objects.create(project=project, user=member, role="member")
    with ran() as sync:
        out = call("hq_project_sync", member, project_id=project.pk)
        assert out["project_id"] == project.pk and out["complete"] is True and sync.call_args.kwargs["mode"] is None
        with pytest.raises(MCPToolError):
            call("hq_project_sync", member, project_id=project.pk, mode="reconcile")
        call("hq_project_sync", me, project_id=project.pk, mode="reconcile")
        assert sync.call_args.kwargs["mode"] == "reconcile"
        with pytest.raises(MCPToolError):
            call("hq_project_sync", me, project_id=project.pk, mode="bogus")


def test_the_person_whose_sign_in_syncs_the_project_may_run_it_after_their_membership_has_lapsed(me, project):
    """The schedule runs as that person. CommCare checks their sign-in on every call, not a 24 hour stamp."""
    HQProjectMember.objects.filter(project=project, user=me).update(
        verified_at=datetime(2020, 1, 1, tzinfo=timezone.utc)
    )
    with ran():
        assert call("hq_project_sync", me, project_id=project.pk)["complete"] is True
    stranger = UserFactory()
    with ran() as sync:
        with pytest.raises(MCPToolError) as caught:
            call("hq_project_sync", stranger, project_id=project.pk)
    assert caught.value.code == "NOT_FOUND" and not sync.called


def test_a_project_with_nobodys_sign_in_cannot_be_synced(me, project):
    HQProject.objects.filter(pk=project.pk).update(sync_owner=None)
    with ran() as sync:
        with pytest.raises(MCPToolError):
            call("hq_project_sync", me, project_id=project.pk)
    assert not sync.called


def test_storage_is_an_admins_to_change_and_names_whose_sign_in_syncs(me, project):
    member = UserFactory()
    HQProjectMember.objects.create(project=project, user=member, role="member")
    with pytest.raises(MCPToolError) as caught:
        call("hq_project_set_storage", member, project_id=project.pk, storage_mode="persistent")
    assert caught.value.code == "PERMISSION_DENIED"
    out = call("hq_project_set_storage", me, project_id=project.pk, storage_mode="persistent")
    project.refresh_from_db()
    assert out["storage_mode"] == "persistent" and project.sync_owner == me
    with pytest.raises(MCPToolError):
        call("hq_project_set_storage", me, project_id=project.pk, storage_mode="forever")


def test_creating_a_project_needs_a_commcare_sign_in_and_commcares_word(client):
    user = make_hq_user(client, domains=("demo",))
    with mock.patch("connect_labs.hqdata.projects.CommCareDataAccess") as access:
        access.return_value.verify_hq_access.return_value = True
        created = call("hq_project_create", user, domain="demo")
        assert created["domain"] == "demo"
        with pytest.raises(MCPToolError):
            call("hq_project_create", user, domain="someone-elses")
    access.assert_called_once()


def test_creating_a_project_without_any_commcare_sign_in_is_refused(me):
    with pytest.raises(MCPToolError) as caught:
        call("hq_project_create", me, domain="demo")
    assert caught.value.code == "PERMISSION_DENIED" and "/hq/login/" in caught.value.message
