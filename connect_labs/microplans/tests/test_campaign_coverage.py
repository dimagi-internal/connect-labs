"""Reported coverage of a CommCare-run campaign against its plan, from a project's stored forms."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from connect_labs.hqdata.access import create_project
from connect_labs.hqdata.computed import ComputedProblem, clean
from connect_labs.hqdata.models import HQForm
from connect_labs.hqdata.stores import store_for
from connect_labs.hqdata.workflows import PageProblem, create_page, read_project_pipeline_data
from connect_labs.microplans.coverage import campaign
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"
DIST = "http://x/cps-distribution"
POP = "http://x/cps-population"
CODE = "function WorkflowUI({ pipelines }) { return <div>{(pipelines.coverage || {}).rows.length}</div>; }"


def distribution(project, n, child, cycle, csi, district, dose="oui", day=1):
    return HQForm.objects.create(
        project_id=project.pk,
        form_id=f"d{n}",
        xmlns=DIST,
        app_id="rcom",
        received_on=datetime(2026, 7, day, 10, tzinfo=timezone.utc),
        form_json={
            "form": {
                "case": {"@case_id": child},
                "var": {"passage": cycle},
                "localisation": {"csi_id": csi, "district_id": district},
                "administration_dose": {"spaq_recu": dose},
            }
        },
    )


def plan(project, n, csi, a, b, day=1):
    return HQForm.objects.create(
        project_id=project.pk,
        form_id=f"p{n}",
        xmlns=POP,
        app_id="chefcsi",
        received_on=datetime(2026, 6, day, 10, tzinfo=timezone.utc),
        form_json={
            "form": {
                "var": {"location": {"csi_id": csi}},
                "population": {"population_3_11_mois": a, "population_12_59_mois": b},
            }
        },
    )


@pytest.fixture
def me():
    return UserFactory()


@pytest.fixture
def project(me):
    project = create_project(me, "echis-niger", hq_host=HOST, can_read_domain=lambda: True)
    rows = [
        # csi-a in district d1: cycle 1 sees 3 children and serves 2 (one refused, one seen twice); cycle 2 serves 1
        ("c1", "contact_1", "csi-a", "d1", "oui"),
        ("c1", "contact_1", "csi-a", "d1", "oui"),
        ("c2", "contact_1", "csi-a", "d1", "oui"),
        ("c3", "contact_1", "csi-a", "d1", "non"),
        ("c1", "contact_2", "csi-a", "d1", "oui"),
        # csi-b in district d2 has no plan record
        ("c9", "contact_1", "csi-b", "d2", "oui"),
    ]
    for i, row in enumerate(rows):
        distribution(project, i, *row)
    plan(project, 0, "csi-a", "10", "30")
    plan(project, 1, "csi-a", "20", "60", day=20)  # a later plan record replaces the earlier one
    return project


def test_children_are_counted_once_per_cycle_and_only_when_the_dose_was_given(project):
    with store_for(project).session() as store:
        found = campaign.counts(store, project.pk, campaign.CampaignSpec())
    assert found[("d1", "csi-a", 1)] == {"seen": 3, "served": 2}
    assert found[("d1", "csi-a", 2)] == {"seen": 1, "served": 1}
    assert found[("d2", "csi-b", 1)] == {"seen": 1, "served": 1}


def test_a_centres_plan_is_its_latest_record_and_a_centre_with_none_is_said(project):
    with store_for(project).session() as store:
        table, meta = campaign.rows(
            store, project.pk, campaign.CampaignSpec(), {"csi-a": "CSI A", "d1": "District One"}
        )
    first = next(r for r in table if r["csi_id"] == "csi-a" and r["cycle"] == 1)
    assert first["target"] == 80.0 and first["children_served"] == 2 and first["csi"] == "CSI A"
    assert first["district"] == "District One"
    assert [r["target"] for r in table if r["csi_id"] == "csi-b"] == [None]
    assert meta["health_centres"] == 2 and meta["health_centres_without_plan"] == 1 and meta["cycles"] == [1, 2]
    assert meta["data_through"].startswith("2026-07-01")


def test_a_page_reads_the_coverage_as_a_computed_source_and_a_non_member_does_not(me, project):
    source = {"computed": "campaign_coverage", "alias": "coverage", "params": {}}
    page = create_page(me, project, name="Coverage", render_code=CODE, pipeline_sources=[source])
    with patch("connect_labs.labs.integrations.commcare.cchq_tokens.cchq_token_or_none", return_value=None):
        data = read_project_pipeline_data(me, project, page.id)
    assert data["coverage"]["metadata"]["row_count"] == 3
    assert "ids rather than names" in data["coverage"]["metadata"]["names_note"]
    assert {r["csi_id"] for r in data["coverage"]["rows"]} == {"csi-a", "csi-b"}
    with pytest.raises(Exception):
        read_project_pipeline_data(UserFactory(), project, page.id)


def test_a_computed_source_with_a_bad_name_or_setting_is_refused_when_the_page_is_made(me, project):
    for bad in (
        {"computed": "no-such", "alias": "x"},
        {"computed": "campaign_coverage", "alias": "x", "params": {"goal": 3}},
        {"computed": "campaign_coverage", "alias": "x", "params": {"child_path": "not a path"}},
        {"computed": "campaign_coverage", "alias": "x", "params": {"nope": 1}},
        {"computed": "campaign_coverage", "alias": "x", "extra": 1},
    ):
        with pytest.raises(PageProblem):
            create_page(me, project, name="Bad", render_code=CODE, pipeline_sources=[bad])
    assert clean({"computed": "campaign_coverage", "alias": "c"})["params"]["goal"] == 0.95
    with pytest.raises(ComputedProblem):
        clean({"computed": "x", "alias": "c"})


def test_the_coverage_page_is_made_once_kept_current_and_opened_from_the_workflows_page(client, me, project):
    from connect_labs.hqdata.workflows import ensure_campaign_coverage_page, load_page

    first = ensure_campaign_coverage_page(me, project)
    assert ensure_campaign_coverage_page(me, project) == first  # idempotent
    definition, code = load_page(me, project, first)
    assert definition.name == "Campaign coverage" and "WorkflowUI" in code
    assert definition.pipeline_sources[0]["computed"] == "campaign_coverage"
    data = read_project_pipeline_data(me, project, first)
    assert data["coverage"]["metadata"]["row_count"] == 3
