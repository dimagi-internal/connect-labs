"""A project's workflow records: stored in labs, opened only by a verified member of that project."""

import pytest

from connect_labs.hqdata.access import ProjectAccessDenied, create_project
from connect_labs.hqdata.models import HQProject
from connect_labs.hqdata.scope import hq_scope_id
from connect_labs.hqdata.workflows import ProjectWorkflowsUnavailable, pipeline_dao, workflow_dao
from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.labs.synthetic.local_records_backend import is_hq_project_scope, is_labs_only_opportunity_id
from connect_labs.labs.synthetic.models import LabsLocalRecord
from connect_labs.users.tests.factories import UserFactory
from connect_labs.workflow.data_access import WorkflowDataAccess

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"


@pytest.fixture
def alice():
    return UserFactory()


@pytest.fixture
def project(alice):
    return create_project(alice, "demo", hq_host=HOST, can_read_domain=lambda: True)


def test_a_member_stores_and_reads_a_definition_with_no_connect_token(alice, project):
    dao = workflow_dao(alice, project)
    created = dao.create_definition(name="Dashboard", description="d", statuses=[{"id": "a", "label": "A"}])
    assert (
        LabsLocalRecord.objects.filter(opportunity_id=hq_scope_id(project.pk), type="workflow_definition").count() == 1
    )
    assert workflow_dao(alice, project).get_definition(created.id).name == "Dashboard"
    assert dao.access_token is None and dao.http_client is None


def test_one_projects_records_are_invisible_to_another(alice, project):
    other = create_project(alice, "other", hq_host=HOST, can_read_domain=lambda: True)
    workflow_dao(alice, project).create_definition(name="mine", description="", statuses=[])
    assert workflow_dao(alice, other).list_definitions() == []


def test_a_person_who_is_not_a_member_cannot_open_the_project(project):
    with pytest.raises(ProjectAccessDenied):
        workflow_dao(UserFactory(), project)


def test_the_scope_is_refused_to_anyone_who_is_not_a_verified_member_whoever_asks(alice, project):
    scope = hq_scope_id(project.pk)
    stranger = UserFactory()
    with pytest.raises(PermissionError):
        WorkflowDataAccess(opportunity_id=scope, user=stranger, access_token="tok")  # a token does not help
    with pytest.raises(PermissionError):
        WorkflowDataAccess(opportunity_id=scope)  # no user at all
    with pytest.raises(PermissionError):
        WorkflowDataAccess(program_id=scope, user=alice)  # only ever the opportunity scope
    with pytest.raises(PermissionError):
        WorkflowDataAccess(opportunity_id=hq_scope_id(project.pk + 99), user=alice)  # no such project
    assert WorkflowDataAccess(opportunity_id=str(scope), user=alice).opportunity_id == scope  # a string id is fine


def test_membership_that_has_lapsed_opens_nothing(alice, project):
    from datetime import timedelta

    from django.utils import timezone

    from connect_labs.hqdata.models import HQProjectMember

    HQProjectMember.objects.filter(project=project).update(verified_at=timezone.now() - timedelta(days=3))
    with pytest.raises(PermissionError):
        WorkflowDataAccess(opportunity_id=hq_scope_id(project.pk), user=alice)


def test_a_project_is_not_a_labs_only_opportunity_to_anyone_else_who_asks(project):
    # scope_config, supply scopes and the synthetic tools read this as "a SyntheticOpportunity row exists".
    assert not is_labs_only_opportunity_id(hq_scope_id(project.pk))
    assert is_hq_project_scope(hq_scope_id(project.pk))
    assert not is_hq_project_scope(hq_scope_id(project.pk + 99))


def test_a_public_record_is_never_looked_for_in_production_for_a_project(project):
    client = LabsRecordAPIClient("", opportunity_id=hq_scope_id(project.pk))
    assert client.get_public_record_by_id(1, experiment="workflow", type="workflow_definition") is None


def test_a_project_that_keeps_its_data_in_the_customers_database_is_refused_for_now(alice, project):
    HQProject.objects.filter(pk=project.pk).update(store=HQProject.STORE_EXTERNAL)
    project.refresh_from_db()
    with pytest.raises(ProjectWorkflowsUnavailable):
        workflow_dao(alice, project)


def test_deleting_a_project_deletes_its_workflows(alice, project):
    workflow_dao(alice, project).create_definition(name="x", description="", statuses=[])
    scope = hq_scope_id(project.pk)
    project.delete()
    assert not LabsLocalRecord.objects.filter(opportunity_id=scope).exists()


def test_pipeline_definitions_live_in_the_same_place(alice, project):
    assert pipeline_dao(alice, project).opportunity_id == hq_scope_id(project.pk)


def test_the_project_dashboard_is_made_once_and_reads_the_projects_forms(alice, project):
    from datetime import datetime, timezone

    from connect_labs.hqdata.models import HQForm
    from connect_labs.hqdata.workflows import ensure_project_dashboard, read_project_pipeline_data

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
    first = ensure_project_dashboard(alice, project)
    assert ensure_project_dashboard(alice, project) == first
    data = read_project_pipeline_data(alice, project, first)
    assert sorted((r["username"], r["forms"]) for r in data["workers"]["rows"]) == [("ann", 2), ("bob", 1)]
    # a change to the overview in the repo reaches a project that already has one
    from connect_labs.hqdata import workflows as module

    module.DASHBOARD_RENDER_CODE = module.DASHBOARD_RENDER_CODE + "\n// v2"
    try:
        assert ensure_project_dashboard(alice, project) == first
        assert workflow_dao(alice, project).get_render_code(first).data["component_code"].endswith("// v2")
    finally:
        module.DASHBOARD_RENDER_CODE = module.DASHBOARD_RENDER_CODE.removesuffix("\n// v2")


def test_a_queryset_delete_of_projects_also_removes_their_workflows(alice, project):
    workflow_dao(alice, project).create_definition(name="x", description="", statuses=[])
    scope = hq_scope_id(project.pk)
    HQProject.objects.filter(pk=project.pk).delete()
    assert not LabsLocalRecord.objects.filter(opportunity_id=scope).exists()


def test_a_scope_that_arrives_as_text_does_not_break_every_other_data_access():
    from connect_labs.hqdata.scope import is_hq_scope

    assert not is_hq_scope("12") and not is_hq_scope("an-org-slug") and not is_hq_scope(None)
    assert is_hq_scope(str(hq_scope_id(3)))
