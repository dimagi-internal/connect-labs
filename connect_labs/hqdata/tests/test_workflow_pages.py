"""A CommCare-only person opens a workflow over their project under /hq/."""

import json
import re
from datetime import datetime, timezone

import pytest
from django.test import Client

from connect_labs.hqdata.models import HQForm, HQProject
from connect_labs.hqdata.tests.conftest import make_hq_user
from connect_labs.hqdata.tests.test_web_views import add_project
from connect_labs.hqdata.workflows import ensure_project_dashboard, workflow_dao

pytestmark = pytest.mark.django_db


def store(project, form_id, username):
    HQForm.objects.create(
        project_id=project.pk,
        form_id=form_id,
        xmlns="http://x/1",
        app_id="app1",
        username=username,
        received_on=datetime(2026, 11, 1, 10, tzinfo=timezone.utc),
        form_json={"id": form_id, "received_on": "2026-11-01T10:00:00Z", "form": {"meta": {"username": username}}},
    )


def workflow_data(response):
    return json.loads(
        re.search(
            r'<script id="workflow-data" type="application/json">(.*?)</script>', response.content.decode(), re.S
        ).group(1)
    )


def test_opening_the_overview_makes_it_and_draws_it_in_the_sandbox_with_no_connect_endpoints(client):
    user = make_hq_user(client)
    project = add_project(user)
    resp = client.post(f"/hq/projects/{project.pk}/workflows/")
    assert resp.status_code == 302
    page = client.get(resp["Location"])
    assert page.status_code == 200
    data = workflow_data(page)
    assert data["sandbox"]["mode"] == "page" and data["is_page"] is True
    assert "WorkflowUI" in data["render_code"]
    api = data["apiEndpoints"]
    assert api["getPipelineData"] == f"{resp['Location']}pipeline-data/"
    assert all(v is None for k, v in api.items() if k != "getPipelineData")  # nothing reaches /labs/...
    assert page["Content-Security-Policy"].startswith("frame-src ")


def test_the_data_endpoint_returns_the_projects_rows_as_the_member(client):
    user = make_hq_user(client)
    project = add_project(user)
    for i, name in enumerate(["ann", "ann", "bob"]):
        store(project, f"f{i}", name)
    definition_id = ensure_project_dashboard(user, project)
    got = client.get(f"/hq/projects/{project.pk}/workflows/{definition_id}/pipeline-data/").json()
    assert sorted((r["username"], r["forms"]) for r in got["workers"]["rows"]) == [("ann", 2), ("bob", 1)]


def test_a_person_who_is_not_in_the_project_gets_a_404_everywhere(client):
    owner_client = Client()
    owner = make_hq_user(owner_client, username="owner@example.org")
    project = add_project(owner)
    definition_id = ensure_project_dashboard(owner, project)
    make_hq_user(client, username="stranger@example.org")
    base = f"/hq/projects/{project.pk}/workflows/"
    assert client.get(base).status_code == 404
    assert client.post(base).status_code == 404
    assert client.get(f"{base}{definition_id}/").status_code == 404
    assert client.get(f"{base}{definition_id}/pipeline-data/").status_code == 404


def test_one_projects_page_cannot_be_opened_through_another_projects_url(client):
    user = make_hq_user(client, domains=("one", "two"))
    one, two = add_project(user, "one"), add_project(user, "two")
    definition_id = ensure_project_dashboard(user, one)
    assert client.get(f"/hq/projects/{two.pk}/workflows/{definition_id}/").status_code == 404
    assert client.get(f"/hq/projects/{two.pk}/workflows/{definition_id}/pipeline-data/").status_code == 404


def test_a_url_context_is_ignored_and_a_stored_schema_cannot_name_another_project(client):
    user = make_hq_user(client, domains=("one", "two"))
    one, two = add_project(user, "one"), add_project(user, "two")
    store(one, "a1", "ann")
    store(two, "b1", "zed")
    definition_id = ensure_project_dashboard(user, one)
    # Point the stored pipeline at the OTHER project the person also belongs to.
    from connect_labs.hqdata.workflows import pipeline_dao

    dao = pipeline_dao(user, one)
    record = dao.list_definitions()[0]
    schema = record.schema
    schema["data_source"]["project_id"] = two.pk
    dao.update_definition(record.id, schema=schema)
    dao.close()
    got = client.get(
        f"/hq/projects/{one.pk}/workflows/{definition_id}/pipeline-data/?opportunity_id=1&program_id=2"
    ).json()
    assert [r["username"] for r in got["workers"]["rows"]] == ["ann"]  # project one's rows, never two's


def test_a_project_in_the_customers_database_says_why_instead_of_failing(client):
    user = make_hq_user(client)
    project = add_project(user, store=HQProject.STORE_EXTERNAL)
    resp = client.get(f"/hq/projects/{project.pk}/workflows/")
    assert resp.status_code == 409 and b"your own database" in resp.content


def test_a_run_is_not_served_here_only_a_page(client):
    user = make_hq_user(client)
    project = add_project(user)
    dao = workflow_dao(user, project)
    run_like = dao.create_definition(name="not a page", description="", statuses=[{"id": "a", "label": "A"}])
    dao.close()
    assert client.get(f"/hq/projects/{project.pk}/workflows/{run_like.id}/").status_code == 404
