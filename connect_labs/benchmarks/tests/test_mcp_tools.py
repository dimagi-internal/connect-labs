"""Cohort administration. Without these tools there is no way to make a cohort,
so the whole subsystem is unreachable -- which is how Plan 1 shipped."""

import pytest

from connect_labs.benchmarks.models import BenchmarkCohort

pytestmark = pytest.mark.django_db


def _user():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create(username="tester")


def test_create_returns_the_cohort_and_persists_it():
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_create

    out = benchmarks_cohort_create(user=_user(), name="KMC", organization_id="dimagi-kmc")
    assert out["name"] == "KMC"
    assert BenchmarkCohort.objects.filter(pk=out["id"]).exists()


def test_create_refuses_a_min_peers_below_the_floor():
    """The floor exists because at 2 the reader is one of the two contributors,
    so the other bar is a named peer's exact value."""
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_create

    with pytest.raises(Exception):
        benchmarks_cohort_create(user=_user(), name="Bad", organization_id="o", min_peers=2)


def test_add_opportunities_is_idempotent():
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_add_opportunities, benchmarks_cohort_create

    user = _user()
    cohort = benchmarks_cohort_create(user=user, name="KMC", organization_id="dimagi-kmc")
    benchmarks_cohort_add_opportunities(user=user, cohort_id=cohort["id"], opportunity_ids=[523, 874])
    out = benchmarks_cohort_add_opportunities(user=user, cohort_id=cohort["id"], opportunity_ids=[874, 1487])
    assert set(out["opportunity_ids"]) == {523, 874, 1487}
    assert BenchmarkCohort.objects.get(pk=cohort["id"]).members.count() == 3


def test_list_reports_membership_counts():
    from connect_labs.benchmarks.mcp_tools import (
        benchmarks_cohort_add_opportunities,
        benchmarks_cohort_create,
        benchmarks_cohort_list,
    )

    user = _user()
    c = benchmarks_cohort_create(user=user, name="KMC", organization_id="dimagi-kmc")
    benchmarks_cohort_add_opportunities(user=user, cohort_id=c["id"], opportunity_ids=[523, 874])
    rows = benchmarks_cohort_list(user=user, organization_id="dimagi-kmc")
    assert [(r["name"], r["opportunity_count"]) for r in rows["cohorts"]] == [("KMC", 2)]


def test_the_tools_are_registered_with_the_mcp_server():
    import connect_labs.benchmarks.mcp_tools  # noqa: F401
    from connect_labs.mcp.tool_registry import get_tool

    for name in ("benchmarks_cohort_create", "benchmarks_cohort_add_opportunities", "benchmarks_cohort_list"):
        assert get_tool(name) is not None, f"{name} is not registered"
    assert get_tool("benchmarks_cohort_create").is_write is True
