"""Cohort administration. Without these tools there is no way to make a cohort,
so the whole subsystem is unreachable -- which is how Plan 1 shipped.

The ``benchmarks_publish`` tests below reuse ``test_publish.py``'s
``SNAPSHOT`` -- the REAL output of ``connect_labs.semantic.snapshot.build()``
-- wrapped exactly as ``workflow/snapshot_builders.wrap_for_runner`` wraps it
for a saved run (``{"state": {"snapshot": SNAPSHOT}, ...}``). A hand-written
snapshot fixture is what hid the defect this whole subsystem was rebuilt to
fix (see test_publish.py's module docstring); the wrapper shape above is
itself pinned by ``test_publish_reads_the_wrapped_snapshot_shape`` below.
"""

import pytest

from connect_labs.benchmarks.models import BenchmarkCohort
from connect_labs.mcp.tool_registry import MCPToolError

pytestmark = pytest.mark.django_db


def _user(username="tester"):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create(username=username)


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
    """Also proves the ``organization_id`` filter itself: a second cohort under
    a DIFFERENT org (with its own members, so a count-based assertion can't
    accidentally pass) must not appear in the first org's listing. With only
    one cohort in the fixture, this test could not tell "filtered by org"
    apart from "returned everything" -- see the mutation to
    ``BenchmarkCohort.objects.all()`` this guards against.
    """
    from connect_labs.benchmarks.mcp_tools import (
        benchmarks_cohort_add_opportunities,
        benchmarks_cohort_create,
        benchmarks_cohort_list,
    )

    user = _user()
    c = benchmarks_cohort_create(user=user, name="KMC", organization_id="dimagi-kmc")
    benchmarks_cohort_add_opportunities(user=user, cohort_id=c["id"], opportunity_ids=[523, 874])

    other = benchmarks_cohort_create(user=user, name="Other Org Cohort", organization_id="other-org")
    benchmarks_cohort_add_opportunities(user=user, cohort_id=other["id"], opportunity_ids=[111, 222, 333])

    rows = benchmarks_cohort_list(user=user, organization_id="dimagi-kmc")
    assert [(r["name"], r["opportunity_count"]) for r in rows["cohorts"]] == [("KMC", 2)]


def test_the_tools_are_registered_with_the_mcp_server():
    """Prove the tools reach the registry via the server's DISCOVERY path
    (``connect_labs.mcp.tools``, the package the MCP server actually imports
    -- see connect_labs/mcp/tools/__init__.py, which imports the one-line
    wrapper ``connect_labs/mcp/tools/benchmarks.py``, matching the pattern
    every other Pattern-A app uses, e.g. ``connect_labs/mcp/tools/supply_chain.py``),
    not merely that the ``@register`` decorators run when their module is
    imported directly (never in doubt).

    Earlier tests in this file already do
    ``from connect_labs.benchmarks.mcp_tools import ...``, which populates
    the registry on its own and would keep ``sys.modules`` warm regardless of
    the server's wiring. To avoid that leakage masking a removed import line
    in connect_labs/mcp/tools/__init__.py or the wrapper module, this test
    forces a clean re-run of the two-hop discovery path
    (``connect_labs.mcp.tools`` -> ``connect_labs.mcp.tools.benchmarks`` ->
    ``connect_labs.benchmarks.mcp_tools``): it evicts the three benchmarks
    entries from the registry and evicts BOTH
    ``connect_labs.mcp.tools.benchmarks`` (the wrapper) and
    ``connect_labs.benchmarks.mcp_tools`` (the real module) from
    ``sys.modules``, along with each one's cached attribute on its parent
    package (import always sets that attribute, and a plain ``from X import
    Y`` checks it before falling back to a real re-import -- see the comments
    below), then reloads ``connect_labs.mcp.tools`` (which is already
    imported by this point in the session, e.g. via the MCP server app or a
    sibling test module). Reloading only re-executes the *other* ``from .
    import X`` lines as no-op cache lookups, since those submodules stay in
    ``sys.modules`` -- only the benchmarks wrapper genuinely re-imports (and,
    transitively, re-imports the real module) and re-registers. If either
    import line were removed, nothing would repopulate the evicted entries
    and this assertion would go red.

    What this does NOT prove: that no *other* code path than
    connect_labs.mcp.tools independently imports
    connect_labs.benchmarks.mcp_tools and would mask a real-world removal in
    a full pytest run (e.g. under -n auto, a different worker could still
    have warmed the module via one of the other test files that import it
    directly). It proves the wiring in __init__.py (and its wrapper) is what
    (re-)populates the registry when exercised in isolation, which is the
    strongest check achievable without spawning a subprocess.
    """
    import importlib
    import sys

    import connect_labs.benchmarks as _benchmarks_pkg
    import connect_labs.mcp.tools as _mcp_tools_pkg
    from connect_labs.mcp.tool_registry import _REGISTRY, get_tool

    tool_names = (
        "benchmarks_cohort_create",
        "benchmarks_cohort_add_opportunities",
        "benchmarks_cohort_list",
        "benchmarks_publish",
    )

    for name in tool_names:
        _REGISTRY.pop(name, None)
    sys.modules.pop("connect_labs.benchmarks.mcp_tools", None)
    sys.modules.pop("connect_labs.mcp.tools.benchmarks", None)
    # `from connect_labs.benchmarks import mcp_tools` (inside the wrapper) and
    # `from . import benchmarks` (inside __init__.py) both resolve via a
    # getattr on their parent package before falling back to a real
    # re-import, so the stale attributes on both parent packages must go too
    # -- otherwise reload() below silently reuses the already-registered
    # modules.
    if hasattr(_benchmarks_pkg, "mcp_tools"):
        delattr(_benchmarks_pkg, "mcp_tools")
    if hasattr(_mcp_tools_pkg, "benchmarks"):
        delattr(_mcp_tools_pkg, "benchmarks")

    import connect_labs.mcp.tools

    importlib.reload(connect_labs.mcp.tools)

    for name in tool_names:
        assert get_tool(name) is not None, f"{name} is not registered via the connect_labs.mcp.tools discovery path"
    assert get_tool("benchmarks_cohort_create").is_write is True


# --- benchmarks_publish -------------------------------------------------


class _StubRun:
    """A minimal stand-in for WorkflowRunRecord -- only what the publisher reads."""

    def __init__(self, *, is_completed, snapshot=None, period_end=None, completed_at=None):
        self.is_completed = is_completed
        self.snapshot = snapshot
        self.period_end = period_end
        self.completed_at = completed_at


def _wrapped_snapshot(graded_payload, state_key="snapshot"):
    """The exact shape ``workflow/snapshot_builders.wrap_for_runner`` produces:
    ``{"state": {<state_key>: <graded payload>}, "pipelines": {}, "workers": []}``.
    This is what a saved run actually stores at ``run.data["snapshot"]`` --
    verified against ``connect_labs/workflow/snapshot_builders.py`` and
    ``connect_labs/workflow/history_rebuild.py``'s identical read of it.
    """
    return {"state": {state_key: graded_payload}, "pipelines": {}, "workers": []}


def test_publish_reads_the_wrapped_snapshot_shape():
    """Pins the wrapper shape the tests below stub -- if
    ``wrap_for_runner`` ever stops nesting the graded payload under
    ``state[state_key]``, this fails here rather than every stub below
    silently matching a shape production no longer produces."""
    from connect_labs.workflow.snapshot_builders import wrap_for_runner

    payload = {"cMeasures": [], "byOpp": []}
    assert wrap_for_runner(payload) == {"state": {"snapshot": payload}, "pipelines": {}, "workers": []}
    assert wrap_for_runner(payload, "custom_key") == {
        "state": {"custom_key": payload},
        "pipelines": {},
        "workers": [],
    }


def test_publish_refuses_a_run_that_is_not_completed():
    """Publishing an in-progress run would push figures that are still moving."""
    from connect_labs.benchmarks.mcp_tools import benchmarks_publish

    with pytest.raises(Exception) as exc:
        benchmarks_publish(user=_user(), cohort_id=1, workflow_id=19778, run_id=1, opportunity_id=523)
    assert "complete" in str(exc.value).lower() or "not found" in str(exc.value).lower()


def test_publish_refuses_an_in_progress_run_even_though_a_completed_one_exists(monkeypatch):
    """The completed-run guard has to reject an in_progress run SPECIFICALLY,
    not merely reject "some run" -- proven by a genuinely completed run,
    same cohort and workflow, that DOES publish right after."""
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.tests.test_publish import OPPS, SNAPSHOT
    from connect_labs.workflow.data_access import WorkflowDataAccess

    user = _user()
    cohort = mcp_tools.benchmarks_cohort_create(user=user, name="KMC", organization_id="dimagi-kmc")
    mcp_tools.benchmarks_cohort_add_opportunities(user=user, cohort_id=cohort["id"], opportunity_ids=list(OPPS))

    monkeypatch.setattr(mcp_tools, "require_connect_token", lambda u: "dummy-token")
    monkeypatch.setattr(
        mcp_tools,
        "fetch_user_organization_data",
        lambda token, owner=None: {"organizations": [{"slug": "dimagi-kmc"}]},
    )
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    runs = {
        1: _StubRun(is_completed=False),
        2: _StubRun(is_completed=True, snapshot=_wrapped_snapshot(SNAPSHOT), period_end="2026-09-11"),
    }
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: runs[run_id])

    with pytest.raises(Exception) as exc:
        mcp_tools.benchmarks_publish(
            user=user, cohort_id=cohort["id"], workflow_id=19778, run_id=1, opportunity_id=OPPS[0]
        )
    assert "complete" in str(exc.value).lower()

    out = mcp_tools.benchmarks_publish(
        user=user, cohort_id=cohort["id"], workflow_id=19778, run_id=2, opportunity_id=OPPS[0]
    )
    assert out["value_count"] > 0


def test_publish_refuses_a_caller_without_access_to_the_cohorts_organisation(monkeypatch):
    """Same cohort, same completed run: a caller outside the cohort's
    organisation is refused, and a caller inside it is not -- proven with
    both, not just the refusal alone, so the check can't be "always deny"."""
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.tests.test_publish import OPPS, SNAPSHOT
    from connect_labs.workflow.data_access import WorkflowDataAccess

    owner = _user("owner")
    cohort = mcp_tools.benchmarks_cohort_create(user=owner, name="KMC", organization_id="dimagi-kmc")
    mcp_tools.benchmarks_cohort_add_opportunities(user=owner, cohort_id=cohort["id"], opportunity_ids=list(OPPS))

    run = _StubRun(is_completed=True, snapshot=_wrapped_snapshot(SNAPSHOT), period_end="2026-09-11")
    monkeypatch.setattr(mcp_tools, "require_connect_token", lambda u: "dummy-token")
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: run)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    outsider = _user("outsider")
    monkeypatch.setattr(
        mcp_tools,
        "fetch_user_organization_data",
        lambda token, owner=None: {"organizations": [{"slug": "some-other-org"}]},
    )
    with pytest.raises(MCPToolError) as exc:
        mcp_tools.benchmarks_publish(
            user=outsider, cohort_id=cohort["id"], workflow_id=19778, run_id=1, opportunity_id=OPPS[0]
        )
    assert exc.value.code == "PERMISSION_DENIED"

    member = _user("member")
    monkeypatch.setattr(
        mcp_tools,
        "fetch_user_organization_data",
        lambda token, owner=None: {"organizations": [{"slug": "dimagi-kmc"}]},
    )
    out = mcp_tools.benchmarks_publish(
        user=member, cohort_id=cohort["id"], workflow_id=19778, run_id=1, opportunity_id=OPPS[0]
    )
    assert out["value_count"] > 0


def test_publish_writes_a_publication_only_for_the_named_cohort(monkeypatch):
    """A second cohort with the SAME membership must get nothing -- proves
    the tool publishes to the cohort it was called with, not to every
    cohort an opportunity belongs to."""
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.models import BenchmarkPublication
    from connect_labs.benchmarks.tests.test_publish import OPPS, SNAPSHOT
    from connect_labs.workflow.data_access import WorkflowDataAccess

    user = _user()
    target = mcp_tools.benchmarks_cohort_create(user=user, name="Target", organization_id="dimagi-kmc")
    mcp_tools.benchmarks_cohort_add_opportunities(user=user, cohort_id=target["id"], opportunity_ids=list(OPPS))
    other = mcp_tools.benchmarks_cohort_create(user=user, name="Other", organization_id="dimagi-kmc")
    mcp_tools.benchmarks_cohort_add_opportunities(user=user, cohort_id=other["id"], opportunity_ids=list(OPPS))

    run = _StubRun(is_completed=True, snapshot=_wrapped_snapshot(SNAPSHOT), period_end="2026-09-11")
    monkeypatch.setattr(mcp_tools, "require_connect_token", lambda u: "dummy-token")
    monkeypatch.setattr(
        mcp_tools,
        "fetch_user_organization_data",
        lambda token, owner=None: {"organizations": [{"slug": "dimagi-kmc"}]},
    )
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: run)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    out = mcp_tools.benchmarks_publish(
        user=user, cohort_id=target["id"], workflow_id=19778, run_id=1, opportunity_id=OPPS[0]
    )

    assert out["cohort_id"] == target["id"]
    assert out["value_count"] > 0
    pub = BenchmarkPublication.objects.get(pk=out["publication_id"])
    assert pub.cohort_id == target["id"]
    assert pub.values.count() == out["value_count"]
    assert BenchmarkPublication.objects.filter(cohort_id=other["id"]).count() == 0


def test_publish_returns_the_documented_shape_and_withholds_non_rate_indicators(monkeypatch):
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.tests.test_publish import OPPS, SNAPSHOT
    from connect_labs.workflow.data_access import WorkflowDataAccess

    user = _user()
    cohort = mcp_tools.benchmarks_cohort_create(user=user, name="KMC", organization_id="dimagi-kmc")
    mcp_tools.benchmarks_cohort_add_opportunities(user=user, cohort_id=cohort["id"], opportunity_ids=list(OPPS))

    run = _StubRun(is_completed=True, snapshot=_wrapped_snapshot(SNAPSHOT), period_end="2026-09-11")
    monkeypatch.setattr(mcp_tools, "require_connect_token", lambda u: "dummy-token")
    monkeypatch.setattr(
        mcp_tools,
        "fetch_user_organization_data",
        lambda token, owner=None: {"organizations": [{"slug": "dimagi-kmc"}]},
    )
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: run)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    out = mcp_tools.benchmarks_publish(
        user=user, cohort_id=cohort["id"], workflow_id=19778, run_id=1, opportunity_id=OPPS[0]
    )

    assert set(out) == {"publication_id", "cohort_id", "as_of", "value_count", "withheld_indicator_ids"}
    assert out["as_of"] == "2026-09-11"
    # C01 is `unit: n` -- a raw case count -- and must never be published.
    assert "C:C01" in out["withheld_indicator_ids"]


def test_publish_lets_snapshot_shape_error_propagate_rather_than_publishing_nothing(monkeypatch):
    """A run whose snapshot carries no benchmarkable indicator at all (the
    empty-payload case, e.g. a missing/renamed state_key) must fail loudly,
    not silently write zero rows -- the exact defect that shipped once."""
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.models import BenchmarkPublication
    from connect_labs.benchmarks.publish import SnapshotShapeError
    from connect_labs.benchmarks.tests.test_publish import OPPS
    from connect_labs.workflow.data_access import WorkflowDataAccess

    user = _user()
    cohort = mcp_tools.benchmarks_cohort_create(user=user, name="KMC", organization_id="dimagi-kmc")
    mcp_tools.benchmarks_cohort_add_opportunities(user=user, cohort_id=cohort["id"], opportunity_ids=list(OPPS))

    # snapshot wrapped under the WRONG state_key -- the tool looks for
    # "snapshot" by default and finds nothing.
    run = _StubRun(
        is_completed=True, snapshot=_wrapped_snapshot({}, state_key="not_snapshot"), period_end="2026-09-11"
    )
    monkeypatch.setattr(mcp_tools, "require_connect_token", lambda u: "dummy-token")
    monkeypatch.setattr(
        mcp_tools,
        "fetch_user_organization_data",
        lambda token, owner=None: {"organizations": [{"slug": "dimagi-kmc"}]},
    )
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: run)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    with pytest.raises(SnapshotShapeError):
        mcp_tools.benchmarks_publish(
            user=user, cohort_id=cohort["id"], workflow_id=19778, run_id=1, opportunity_id=OPPS[0]
        )
    assert BenchmarkPublication.objects.count() == 0
