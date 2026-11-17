"""The hq_workflow_* tools: a member writes, reads and changes workflow pages over a CommCare project."""

from datetime import datetime, timezone

import pytest

import connect_labs.hqdata.mcp_workflow_tools  # noqa: F401 -- registers the tools
from connect_labs.hqdata.access import create_project
from connect_labs.hqdata.models import HQForm, HQProject, HQProjectMember
from connect_labs.hqdata.workflows import ensure_project_dashboard
from connect_labs.mcp import token_scopes
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"

CODE = """function WorkflowUI({ pipelines }) {
  const rows = (pipelines.workers || {}).rows || [];
  return <div>{rows.length} workers</div>;
}"""
SCHEMA = {
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [{"name": "forms", "path": "form.meta.username", "aggregation": "count"}],
}


def call(tool, user, **args):
    return get_tool(tool).handler(user=user, **args)


@pytest.fixture
def me():
    return UserFactory()


@pytest.fixture
def project(me):
    project = create_project(me, "demo", hq_host=HOST, can_read_domain=lambda: True)
    for i, name in enumerate(["ann", "ann", "bob"]):
        HQForm.objects.create(
            project_id=project.pk,
            form_id=f"f{i}",
            xmlns="http://x/1",
            app_id="app1",
            username=name,
            received_on=datetime(2026, 11, 1, 10, tzinfo=timezone.utc),
            form_json={"id": f"f{i}", "received_on": "2026-11-01T10:00:00Z", "form": {"meta": {"username": name}}},
        )
    return project


@pytest.fixture
def pipeline(me, project):
    return call("hq_pipeline_save", me, project_id=project.pk, name="per worker", store_kind="forms", schema=SCHEMA)[
        "id"
    ]


def make(me, project, pipeline, **extra):
    return call(
        "hq_workflow_create",
        me,
        project_id=project.pk,
        name="Workers",
        render_code=CODE,
        pipeline_sources=[{"hq_pipeline_id": pipeline, "alias": "workers"}],
        **extra,
    )


def test_every_workflow_tool_is_in_the_commcare_scope():
    names = {"list", "get", "create", "update", "delete", "data", "authoring_guide"}
    assert {f"hq_workflow_{n}" for n in names} <= token_scopes.COMMCARE_TOOLS


def test_a_member_creates_a_page_that_reads_a_saved_pipeline_and_sees_exactly_what_it_will_render(
    me, project, pipeline
):
    made = make(me, project, pipeline)
    assert made["url"].endswith(f"/commcare/projects/{project.pk}/workflows/{made['workflow_id']}/")
    got = call("hq_workflow_get", me, project_id=project.pk, workflow_id=made["workflow_id"])
    assert got["render_code"] == CODE and got["pipeline_sources"] == [{"hq_pipeline_id": pipeline, "alias": "workers"}]
    data = call("hq_workflow_data", me, project_id=project.pk, workflow_id=made["workflow_id"])["pipelines"]
    assert sorted((r["username"], r["forms"]) for r in data["workers"]["rows"]) == [("ann", 2), ("bob", 1)]
    listed = call("hq_workflow_list", me, project_id=project.pk)["workflows"]
    assert [w["id"] for w in listed] == [made["workflow_id"]]


def test_a_page_updates_in_place_and_a_change_to_the_pipeline_reaches_it(me, project, pipeline):
    made = make(me, project, pipeline)
    wid = made["workflow_id"]
    new = CODE.replace("workers", "people")
    out = call("hq_workflow_update", me, project_id=project.pk, workflow_id=wid, render_code=new, name="People")
    assert out["changed"] == ["name", "render_code"]
    got = call("hq_workflow_get", me, project_id=project.pk, workflow_id=wid)
    assert got["render_code"] == new and got["name"] == "People"
    call(
        "hq_pipeline_save",
        me,
        project_id=project.pk,
        name="per worker",
        store_kind="forms",
        schema={**SCHEMA, "fields": [{"name": "n", "path": "form.meta.username", "aggregation": "count"}]},
    )
    rows = call("hq_workflow_data", me, project_id=project.pk, workflow_id=wid)["pipelines"]["workers"]["rows"]
    assert "n" in rows[0]


@pytest.mark.parametrize(
    "bad",
    [
        {"pipeline_sources": [{"hq_pipeline_id": 999999, "alias": "x"}]},  # not in this project
        {"pipeline_sources": [{"pipeline_id": 1, "alias": "x"}]},  # a source kind that is not allowed
        {"pipeline_sources": [{"hq_pipeline_id": 1, "alias": "1bad"}]},
        {"config": {"sandbox": False}},  # not an author's to choose
        {"config": {"showFilters": "yes"}},
        {"render_code": ""},
        {"render_code": "x" * 400_000},
        {"name": "  "},
    ],
)
def test_a_page_is_refused_in_words_rather_than_saved_broken(me, project, pipeline, bad):
    args = {"project_id": project.pk, "name": "Workers", "pipeline_sources": [], **bad}
    with pytest.raises(MCPToolError) as err:
        call("hq_workflow_create", me, **args)
    assert err.value.code == "INVALID_SCHEMA"
    assert call("hq_workflow_list", me, project_id=project.pk)["workflows"] == []  # nothing half made


def test_a_page_cannot_name_another_projects_pipeline(me, project, pipeline):
    other = create_project(me, "other", hq_host=HOST, can_read_domain=lambda: True)
    foreign = call("hq_pipeline_save", me, project_id=other.pk, name="p", store_kind="forms", schema=SCHEMA)["id"]
    with pytest.raises(MCPToolError):
        call(
            "hq_workflow_create",
            me,
            project_id=project.pk,
            name="x",
            pipeline_sources=[{"hq_pipeline_id": foreign, "alias": "a"}],
        )


def test_a_person_outside_the_project_finds_nothing_and_changes_nothing(me, project, pipeline):
    made = make(me, project, pipeline)
    stranger = UserFactory()
    wid = made["workflow_id"]
    for tool, extra in (
        ("hq_workflow_list", {}),
        ("hq_workflow_get", {"workflow_id": wid}),
        ("hq_workflow_data", {"workflow_id": wid}),
        ("hq_workflow_update", {"workflow_id": wid, "name": "pwned"}),
        ("hq_workflow_delete", {"workflow_id": wid}),
        ("hq_workflow_create", {"name": "x"}),
    ):
        with pytest.raises(MCPToolError) as err:
            call(tool, stranger, project_id=project.pk, **extra)
        assert err.value.code == "NOT_FOUND", tool
    assert call("hq_workflow_get", me, project_id=project.pk, workflow_id=wid)["name"] == "Workers"


def test_a_workflow_id_from_another_project_is_not_found_here(me, project, pipeline):
    other = create_project(me, "other", hq_host=HOST, can_read_domain=lambda: True)
    made = make(me, project, pipeline)
    for tool in ("hq_workflow_get", "hq_workflow_data"):
        with pytest.raises(MCPToolError) as err:
            call(tool, me, project_id=other.pk, workflow_id=made["workflow_id"])
        assert err.value.code == "NOT_FOUND"


def test_delete_is_for_the_author_or_an_admin_and_keeps_a_backup(me, project, pipeline):
    made = make(me, project, pipeline)
    colleague = UserFactory()
    HQProjectMember.objects.create(project=project, user=colleague, role=HQProjectMember.ROLE_MEMBER)
    with pytest.raises(MCPToolError) as err:
        call("hq_workflow_delete", colleague, project_id=project.pk, workflow_id=made["workflow_id"])
    assert err.value.code == "PERMISSION_DENIED"
    # any member may CHANGE a page, as the tool says; only the author or an admin may delete it
    call("hq_workflow_update", colleague, project_id=project.pk, workflow_id=made["workflow_id"], description="d")
    assert call("hq_workflow_delete", me, project_id=project.pk, workflow_id=made["workflow_id"]) == {"deleted": True}
    from connect_labs.labs.models import DeletedWorkflowBackup

    assert DeletedWorkflowBackup.objects.filter(definition_id=made["workflow_id"]).exists()
    assert call("hq_workflow_list", me, project_id=project.pk)["workflows"] == []


def test_the_overview_is_listed_and_only_an_admin_can_delete_it(me, project):
    wid = ensure_project_dashboard(me, project)
    assert [w["name"] for w in call("hq_workflow_list", me, project_id=project.pk)["workflows"]] == [
        "Project overview"
    ]
    colleague = UserFactory()
    HQProjectMember.objects.create(project=project, user=colleague, role=HQProjectMember.ROLE_MEMBER)
    with pytest.raises(MCPToolError):
        call("hq_workflow_delete", colleague, project_id=project.pk, workflow_id=wid)


def test_a_project_in_a_customers_database_says_why(me, project):
    HQProject.objects.filter(pk=project.pk).update(store=HQProject.STORE_EXTERNAL)
    with pytest.raises(MCPToolError) as err:
        call("hq_workflow_list", me, project_id=project.pk)
    assert "your own database" in str(err.value)


def test_the_guide_is_available_with_no_project():
    guide = call("hq_workflow_authoring_guide", UserFactory())["guide"]
    assert "WorkflowUI" in guide and "hq_pipeline_save" in guide


def test_the_data_a_page_gets_has_every_row_but_the_tool_caps_what_it_returns(me, project, pipeline):
    made = make(me, project, pipeline)
    got = call("hq_workflow_data", me, project_id=project.pk, workflow_id=made["workflow_id"], max_rows=1)
    assert got["pipelines"]["workers"]["row_count"] == 2 and len(got["pipelines"]["workers"]["rows"]) == 1
    assert got["pipelines"]["workers"]["truncated"] is True


# --- review fixes ---------------------------------------------------------------------------------


def test_no_pipeline_stream_is_not_a_flag_a_page_may_set_because_it_would_blank_the_page(me, project, pipeline):
    with pytest.raises(MCPToolError):
        call("hq_workflow_create", me, project_id=project.pk, name="x", config={"noPipelineStream": True})


def test_one_source_that_cannot_run_shows_its_own_error_and_leaves_the_others_alone(me, project, pipeline):
    made = make(me, project, pipeline)
    wid = made["workflow_id"]
    call(
        "hq_workflow_update",
        me,
        project_id=project.pk,
        workflow_id=wid,
        pipeline_sources=[{"hq_pipeline_id": pipeline, "alias": "workers"}],
    )
    from connect_labs.hqdata.models import HQPipeline

    HQPipeline.objects.filter(pk=pipeline).update(schema={"fields": []})  # no longer runnable
    other = call("hq_pipeline_save", me, project_id=project.pk, name="ok", store_kind="forms", schema=SCHEMA)["id"]
    call(
        "hq_workflow_update",
        me,
        project_id=project.pk,
        workflow_id=wid,
        pipeline_sources=[{"hq_pipeline_id": pipeline, "alias": "broken"}, {"hq_pipeline_id": other, "alias": "good"}],
    )
    data = call("hq_workflow_data", me, project_id=project.pk, workflow_id=wid)["pipelines"]
    assert data["good"]["row_count"] == 2
    assert "error" in list(data["broken"]["metadata"]["per_opp"].values())[0]


def test_changing_a_flag_keeps_the_others_instead_of_replacing_them(me, project, pipeline):
    wid = make(me, project, pipeline, config={"showFilters": True, "showSummaryCards": True})["workflow_id"]
    call("hq_workflow_update", me, project_id=project.pk, workflow_id=wid, config={"renderWhileLoading": True})
    assert call("hq_workflow_get", me, project_id=project.pk, workflow_id=wid)["config"] == {
        "showFilters": True,
        "showSummaryCards": True,
        "renderWhileLoading": True,
    }


def test_the_overview_is_labs_to_keep_a_member_cannot_change_it_and_a_page_with_its_name_is_just_a_page(
    me, project, pipeline
):
    overview = ensure_project_dashboard(me, project)
    with pytest.raises(MCPToolError) as err:
        call("hq_workflow_update", me, project_id=project.pk, workflow_id=overview, render_code=CODE)
    assert err.value.code == "INVALID_SCHEMA"
    copy = call("hq_workflow_create", me, project_id=project.pk, name="Project overview", render_code=CODE)
    assert ensure_project_dashboard(me, project) == overview  # found by its marker, not its name
    assert call("hq_workflow_get", me, project_id=project.pk, workflow_id=copy["workflow_id"])["render_code"] == CODE


def test_an_overview_made_before_the_marker_existed_is_adopted_and_repaired(me, project):
    from connect_labs.hqdata.workflows import DASHBOARD_RENDER_CODE

    overview = ensure_project_dashboard(me, project)
    dao = workflow_dao_for(me, project)
    data = dict(dao.get_definition(overview).data)
    data.pop("system")
    data["pipeline_sources"] = []
    dao.update_definition(overview, data)
    dao.save_render_code(overview, "function WorkflowUI(){return null}")
    dao.close()
    assert ensure_project_dashboard(me, project) == overview
    dao = workflow_dao_for(me, project)
    fixed = dao.get_definition(overview).data
    assert fixed["system"] and fixed["pipeline_sources"]
    assert dao.get_render_code(overview).data["component_code"] == DASHBOARD_RENDER_CODE
    dao.close()


def workflow_dao_for(user, project):
    from connect_labs.hqdata.workflows import workflow_dao

    return workflow_dao(user, project)


def test_a_misspelt_argument_is_refused_not_ignored(me, project, pipeline):
    wid = make(me, project, pipeline)["workflow_id"]
    with pytest.raises(MCPToolError) as err:
        call("hq_workflow_update", me, project_id=project.pk, workflow_id=wid, renderCode=CODE)
    assert err.value.code == "INVALID_SCHEMA"


def test_text_that_cannot_be_encoded_is_a_clear_refusal_not_a_traceback(me, project):
    with pytest.raises(MCPToolError) as err:
        call("hq_workflow_create", me, project_id=project.pk, name="x", render_code="function WorkflowUI(){}\ud800")
    assert err.value.code == "INVALID_SCHEMA"


def test_a_stray_lookup_error_is_not_reported_as_a_missing_page(me, project, pipeline, monkeypatch):
    wid = make(me, project, pipeline)["workflow_id"]

    def boom(*a, **k):
        raise KeyError("engine bug")

    monkeypatch.setattr("connect_labs.hqdata.mcp_workflow_tools.read_project_pipeline_data", boom)
    with pytest.raises(KeyError):
        call("hq_workflow_data", me, project_id=project.pk, workflow_id=wid)
