"""The page that clones a real opportunity into a synthetic one (views.CloneRealOppView)."""

from unittest import mock

import pytest
from django.urls import reverse

from connect_labs.mcp.tool_registry import MCPToolError

ORG_DATA = "connect_labs.labs.context.get_org_data"
CLONE = "connect_labs.mcp.tools.synthetic.synthetic_clone_opp"
STATUS = "connect_labs.mcp.tools.synthetic.synthetic_job_status"
OPPS = {"opportunities": [{"id": 2230, "name": "RUTF Deliver", "visit_count": 900}, {"id": 10096, "name": "A clone"}]}


@pytest.fixture
def signed_in(client, django_user_model):
    user = django_user_model.objects.create_user(username="jo", password="x", email="jo@dimagi.com")
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_the_page_lists_real_opportunities_only(client, signed_in):
    with mock.patch(ORG_DATA, return_value=OPPS):
        body = client.get(reverse("labs:synthetic:clone")).content.decode()
    assert 'value="2230"' in body and "RUTF Deliver" in body
    # A labs-only opportunity is already synthetic: there is nothing real to measure.
    assert 'value="10096"' not in body


@pytest.mark.django_db
def test_starting_a_clone_runs_the_tools_job_as_the_signed_in_person(client, signed_in):
    with mock.patch(ORG_DATA, return_value=OPPS), mock.patch(CLONE, return_value={"task_id": "t-1"}) as clone:
        response = client.post(
            reverse("labs:synthetic:clone"), {"opportunity": ["2230"], "program_name": "RUTF, synthetic"}
        )
    assert response.status_code == 302
    assert response["Location"] == reverse("labs:synthetic:clone_status", args=["t-1"])
    (user,), kwargs = clone.call_args
    assert user.pk == signed_in.pk
    # The timelines box was not ticked, so it was not posted: a faster clone.
    assert kwargs == {"source_opportunity_ids": [2230], "program_name": "RUTF, synthetic", "case_timelines": False}


@pytest.mark.django_db
def test_a_refusal_is_shown_on_the_form(client, signed_in):
    refused = MCPToolError("PERMISSION_DENIED", "You do not have access to opportunity 2230.")
    with mock.patch(ORG_DATA, return_value=OPPS), mock.patch(CLONE, side_effect=refused):
        response = client.post(reverse("labs:synthetic:clone"), {"opportunity": ["2230"], "case_timelines": "on"})
    assert response.status_code == 400
    assert "You do not have access to opportunity 2230." in response.content.decode()


@pytest.mark.django_db
def test_nothing_chosen_is_refused_without_starting_anything(client, signed_in):
    with mock.patch(ORG_DATA, return_value=OPPS), mock.patch(CLONE) as clone:
        response = client.post(reverse("labs:synthetic:clone"), {})
    assert response.status_code == 400
    clone.assert_not_called()


@pytest.mark.django_db
def test_the_status_page_shows_the_new_opportunity_and_hides_others_jobs(client, signed_in):
    done = {
        "state": "SUCCESS",
        "status": "Done",
        "result": {
            "program_id": 10701,
            "program_name": "RUTF, synthetic",
            "clones": [{"source_opportunity_id": 2230, "opportunity_id": 10120}],
        },
    }
    with mock.patch(STATUS, return_value=done):
        body = client.get(reverse("labs:synthetic:clone_status", args=["t-1"])).content.decode()
    assert "#10120" in body and "a copy of #2230" in body
    with mock.patch(STATUS, side_effect=MCPToolError("NOT_FOUND", "no")):
        assert client.get(reverse("labs:synthetic:clone_status", args=["t-2"])).status_code == 404
