"""Semantic indicators over a CommCare project: the registries and compiler Connect's reports use, on a
project's own pipelines. Every form, child and name here is invented.

    form  worker  child  dose
    f1    ann     c1     oui
    f2    ann     c2     non
    f3    bob     c3     oui
    (later) f4 bob c2 oui     -> c2 served too

    children 3, served 2 (66.67%); after f4, served 3 (100%).
"""

import pytest

from connect_labs.hqdata.mcp_semantic_tools import (
    commcare_indicators,
    commcare_registry_create,
    commcare_registry_delete,
    commcare_registry_list,
)
from connect_labs.hqdata.models import HQForm
from connect_labs.hqdata.tests.conftest import make_pipeline
from connect_labs.hqdata.tests.test_pipelines import store_form
from connect_labs.hqdata.workflows import PageProblem, create_page, read_project_pipeline_data
from connect_labs.mcp.tool_registry import MCPToolError

pytestmark = pytest.mark.django_db


@pytest.fixture
def alice(db):
    from connect_labs.users.tests.factories import UserFactory

    return UserFactory()


@pytest.fixture
def project(alice):
    from connect_labs.hqdata.access import create_project

    return create_project(alice, "demo", hq_host="www.commcarehq.org", can_read_domain=lambda: True)


PROPERTIES = {
    "version": 1,
    "entity": {"name": "child", "plural": "children", "key": "child", "cohort_date": "first_visit"},
    "visit_columns": [{"name": "got_dose", "sql": "(dose = 'oui')"}],
    "pipelines": {"entity": "doses"},
    "aggregates": [
        {"name": "first_visit", "sql": "MIN(visit_date)"},
        {"name": "doses_given", "sql": "COUNT(*) FILTER (WHERE got_dose)"},
    ],
    "properties": [{"name": "served", "type": "bool", "sql": "doses_given > 0"}],
}
INDICATORS = {
    "version": 1,
    "cube": "smc_child",
    "defaults": {"min_denominator": 1},
    "series": ["S"],
    "measures": [
        {"name": "s01", "title": "Children", "type": "number", "sql": "{s01_numerator}", "meta": {"indicator": "S01"}},
        {"name": "s01_numerator", "type": "count"},
        {"name": "s01_denominator", "type": "count"},
        {
            "name": "s02",
            "title": "Children served",
            "type": "number",
            "sql": "100.0 * {s02_numerator} / NULLIF({s02_denominator}, 0)",
            "meta": {"indicator": "S02", "unit": "%"},
        },
        {"name": "s02_numerator", "type": "count", "filters": [{"sql": "{CUBE}.served"}]},
        {"name": "s02_denominator", "type": "count"},
    ],
}


def dose_form(project, form_id, username, child, dose):
    row = store_form(project, form_id, username, "1")
    row.form_json = {**row.form_json, "form": {**row.form_json["form"], "child": child, "dose": dose}}
    row.save(update_fields=["form_json"])
    return row


@pytest.fixture
def doses(project, alice):
    dose_form(project, "f1", "ann", "c1", "oui")
    dose_form(project, "f2", "ann", "c2", "non")
    dose_form(project, "f3", "bob", "c3", "oui")
    return make_pipeline(
        project,
        created_by=alice,
        name="doses",
        schema={
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "fields": [{"name": "child", "path": "form.child"}, {"name": "dose", "path": "form.dose"}],
        },
    )


@pytest.fixture
def registry(project, alice):
    return commcare_registry_create(
        alice, project_id=project.pk, name="SMC", properties_doc=PROPERTIES, indicators_doc=INDICATORS
    )


def programme(rows):
    return next(r for r in rows if r.get("scope") in (None, "programme"))


def test_a_page_reads_indicators_computed_from_the_projects_own_pipeline(alice, project, doses, registry):
    page = create_page(
        alice,
        project,
        name="SMC",
        pipeline_sources=[
            {"pipeline_id": doses.id, "alias": "doses"},
            {"alias": "kpis", "indicators": {"registry_id": registry["id"]}},
        ],
    )
    kpis = read_project_pipeline_data(alice, project, page.id)["kpis"]
    assert "error" not in kpis["metadata"], kpis["metadata"]
    row = programme(kpis["rows"])
    assert row["s01"] == 3 and row["s02"] == pytest.approx(66.67, abs=0.01)
    assert {m["id"] for m in kpis["metadata"]["measures"]} >= {"s01", "s02"}

    # A sync adds a form: the next read carries it, nothing to warm or clear.
    dose_form(project, "f4", "bob", "c2", "oui")
    row = programme(read_project_pipeline_data(alice, project, page.id)["kpis"]["rows"])
    assert row["s01"] == 3 and row["s02"] == pytest.approx(100.0)


def test_a_registry_can_be_evaluated_without_a_page_and_by_worker(alice, project, doses, registry):
    out = commcare_indicators(
        alice, project_id=project.pk, registry_id=registry["id"], pipelines={"doses": doses.id}, scopes=["flw"]
    )
    by_worker = {r["username"]: r for r in out["rows"]}
    assert by_worker["ann"]["s01"] == 2 and by_worker["bob"]["s01"] == 1
    assert out["registry"]["registry_id"] == registry["id"]


def test_a_built_in_registry_runs_over_a_cases_pipeline(alice, project):
    from datetime import datetime, timezone

    from connect_labs.hqdata.models import HQCase

    for i in range(3):
        HQCase.objects.create(
            project_id=project.pk,
            case_id=f"c{i}",
            case_type="child",
            case_name=f"Child {i}",
            date_opened=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
    cases = make_pipeline(
        project,
        created_by=alice,
        name="cases",
        store_kind="cases",
        schema={
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "fields": [{"name": "n", "path": "case.case_name"}],
        },
    )
    out = commcare_indicators(
        alice, project_id=project.pk, registry_name="visit_quality", pipelines={"visits": cases.id}
    )
    assert programme(out["rows"])["q01"] == 3  # three cases, three beneficiaries


def test_a_registry_a_page_reads_is_not_deleted(alice, project, doses, registry):
    create_page(
        alice,
        project,
        name="SMC",
        pipeline_sources=[
            {"pipeline_id": doses.id, "alias": "doses"},
            {"alias": "kpis", "indicators": {"registry_id": registry["id"]}},
        ],
    )
    with pytest.raises(MCPToolError, match="read by page"):
        commcare_registry_delete(alice, project_id=project.pk, registry_id=registry["id"])
    assert [r["id"] for r in commcare_registry_list(alice, project_id=project.pk)["registries"]] == [registry["id"]]


def test_a_member_of_another_project_cannot_see_its_registries(alice, project, registry):
    from connect_labs.users.tests.factories import UserFactory

    with pytest.raises(MCPToolError, match="not a member"):
        commcare_registry_list(UserFactory(), project_id=project.pk)


@pytest.mark.parametrize(
    "source, match",
    [
        ({"alias": "k", "indicators": {}}, "names its registry"),
        ({"alias": "k", "indicators": {"registry_id": 1, "name": "kmc"}}, "not both"),
        ({"alias": "k", "indicators": {"registry_id": "1"}}, "is a number"),
        ({"alias": "k", "indicators": {"name": "kmc"}, "scopes": ["galaxy"]}, "scopes"),
    ],
)
def test_a_bad_indicator_source_is_refused_in_words(alice, project, source, match):
    with pytest.raises(PageProblem, match=match):
        create_page(alice, project, name="x", pipeline_sources=[source])


def test_a_registry_that_cannot_be_bound_shows_its_error_on_its_source_alone(alice, project, doses):
    page = create_page(
        alice,
        project,
        name="SMC",
        pipeline_sources=[
            {"pipeline_id": doses.id, "alias": "doses"},
            {"alias": "kpis", "indicators": {"name": "kmc"}},  # its entity pipeline is not on this page
        ],
    )
    data = read_project_pipeline_data(alice, project, page.id)
    assert data["kpis"]["metadata"]["error"] and len(data["doses"]["rows"]) == HQForm.objects.count()
