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
    so the other bar is a named peer's exact value.

    Asserts the tool's own app-level validation (MCPToolError), not just "some
    exception" -- pytest.raises(Exception) would also catch the model's DB
    CheckConstraint (IntegrityError), which carries no useful message and
    would leave this test green even if the app-level check were deleted.
    """
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_create
    from connect_labs.mcp.tool_registry import MCPToolError

    with pytest.raises(MCPToolError) as exc_info:
        benchmarks_cohort_create(user=_user(), name="Bad", organization_id="o", min_peers=2)
    message = str(exc_info.value)
    assert "min_peers" in message
    assert "3" in message


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
    """Prove the tools reach the registry via the server's DISCOVERY path
    (``connect_labs.mcp.tools``, the package the MCP server actually imports
    -- see connect_labs/mcp/tools/__init__.py), not merely that the
    ``@register`` decorators run when their module is imported directly
    (never in doubt).

    Earlier tests in this file already do
    ``from connect_labs.benchmarks.mcp_tools import ...``, which populates
    the registry on its own and would keep ``sys.modules`` warm regardless of
    the server's wiring. To avoid that leakage masking a removed import line
    in connect_labs/mcp/tools/__init__.py, this test forces a clean re-run of
    the discovery path: it evicts the three benchmarks entries from the
    registry and evicts ``connect_labs.benchmarks.mcp_tools`` from
    ``sys.modules``, then reloads ``connect_labs.mcp.tools`` (which is
    already imported by this point in the session, e.g. via the MCP server
    app or a sibling test module). Reloading only re-executes the *other*
    ``from . import X`` lines as no-op cache lookups, since those submodules
    stay in ``sys.modules`` -- only the benchmarks import actually re-runs
    its module body and re-registers. If the benchmarks import were removed
    from __init__.py, nothing would repopulate the evicted entries and this
    assertion would go red.

    What this does NOT prove: that no *other* code path than
    connect_labs.mcp.tools independently imports
    connect_labs.benchmarks.mcp_tools and would mask a real-world removal in
    a full pytest run (e.g. under -n auto, a different worker could still
    have warmed the module via one of the other test files that import it
    directly). It proves the wiring in __init__.py is what (re-)populates the
    registry when exercised in isolation, which is the strongest check
    achievable without spawning a subprocess.
    """
    import importlib
    import sys

    import connect_labs.benchmarks as _benchmarks_pkg
    from connect_labs.mcp.tool_registry import _REGISTRY, get_tool

    tool_names = ("benchmarks_cohort_create", "benchmarks_cohort_add_opportunities", "benchmarks_cohort_list")

    for name in tool_names:
        _REGISTRY.pop(name, None)
    sys.modules.pop("connect_labs.benchmarks.mcp_tools", None)
    # `from connect_labs.benchmarks import mcp_tools` (in __init__.py) resolves via
    # getattr(connect_labs.benchmarks, "mcp_tools") before falling back to a real
    # re-import, so the stale attribute on the parent package must go too --
    # otherwise reload() below silently reuses the already-registered module.
    if hasattr(_benchmarks_pkg, "mcp_tools"):
        delattr(_benchmarks_pkg, "mcp_tools")

    import connect_labs.mcp.tools

    importlib.reload(connect_labs.mcp.tools)

    for name in tool_names:
        assert get_tool(name) is not None, f"{name} is not registered via the connect_labs.mcp.tools discovery path"
    assert get_tool("benchmarks_cohort_create").is_write is True
