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


def _grant(monkeypatch, *, organizations=("dimagi-kmc",), opportunity_ids=()):
    """Stub the caller's Connect org tree -- which organisations they belong to
    and which opportunities they hold.

    Every write in ``mcp_tools`` is gated on this (membership of a cohort IS
    the read grant, so ``benchmarks_cohort_add_opportunities`` is a
    self-service grant unless it is gated), so a test that creates or
    populates a cohort has to say who the caller is. The access tests below
    call ``_grant`` with a DIFFERENT org and then assert the refusal, so this
    helper cannot quietly make them pass.
    """
    from connect_labs.benchmarks import mcp_tools

    monkeypatch.setattr(mcp_tools, "require_connect_token", lambda u: "dummy-token")
    # The network fetch now happens inside the shared policy module
    # (connect_labs.labs.access.scopes), not here -- see mcp_tools.py's
    # module docstring. Patched at its new home, same as
    # labs/access/tests/test_scopes.py patches it.
    monkeypatch.setattr(
        "connect_labs.labs.access.scopes.fetch_user_organization_data",
        lambda token, owner=None: {
            "organizations": [{"slug": slug} for slug in organizations],
            "opportunities": [{"id": oid} for oid in opportunity_ids],
        },
    )


def test_create_returns_the_cohort_and_persists_it(monkeypatch):
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_create

    _grant(monkeypatch, organizations=("dimagi-kmc",))
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


def test_add_opportunities_is_idempotent(monkeypatch):
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_add_opportunities, benchmarks_cohort_create

    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(523, 874, 1487))
    user = _user()
    cohort = benchmarks_cohort_create(user=user, name="KMC", organization_id="dimagi-kmc")
    benchmarks_cohort_add_opportunities(user=user, cohort_id=cohort["id"], opportunity_ids=[523, 874])
    out = benchmarks_cohort_add_opportunities(user=user, cohort_id=cohort["id"], opportunity_ids=[874, 1487])
    assert set(out["opportunity_ids"]) == {523, 874, 1487}
    assert BenchmarkCohort.objects.get(pk=cohort["id"]).members.count() == 3


def test_list_reports_membership_counts(monkeypatch):
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

    _grant(
        monkeypatch,
        organizations=("dimagi-kmc", "other-org"),
        opportunity_ids=(523, 874, 111, 222, 333),
    )
    user = _user()
    c = benchmarks_cohort_create(user=user, name="KMC", organization_id="dimagi-kmc")
    benchmarks_cohort_add_opportunities(user=user, cohort_id=c["id"], opportunity_ids=[523, 874])

    other = benchmarks_cohort_create(user=user, name="Other Org Cohort", organization_id="other-org")
    benchmarks_cohort_add_opportunities(user=user, cohort_id=other["id"], opportunity_ids=[111, 222, 333])

    rows = benchmarks_cohort_list(user=user, organization_id="dimagi-kmc")
    assert [(r["name"], r["opportunity_count"]) for r in rows["cohorts"]] == [("KMC", 2)]


def test_list_refuses_an_organisation_the_caller_does_not_belong_to(monkeypatch):
    """Filtering is not authorisation: `.filter(organization_id=...)` scopes the
    query to what was asked for, not to what the asker may have. An ungated
    list is the ENUMERATION step of the escalation F1 closed -- you need a
    cohort id to target, and this hands you every one with its owning org slug.

    Discriminating by construction: the foreign org's cohort really exists and
    really has members, so a tool that skipped the gate would visibly return it
    (the assertion below would see one row instead of a refusal), and the same
    caller listing their OWN org still gets their cohort, so it cannot be
    "always deny" either.
    """
    from connect_labs.benchmarks.mcp_tools import (
        benchmarks_cohort_add_opportunities,
        benchmarks_cohort_create,
        benchmarks_cohort_list,
    )

    owner = _user("owner")
    _grant(monkeypatch, organizations=("other-org",), opportunity_ids=(900, 901))
    theirs = benchmarks_cohort_create(user=owner, name="Theirs", organization_id="other-org")
    benchmarks_cohort_add_opportunities(user=owner, cohort_id=theirs["id"], opportunity_ids=[900, 901])

    stranger = _user("stranger")
    _grant(monkeypatch, organizations=("my-org",), opportunity_ids=(523,))
    mine = benchmarks_cohort_create(user=stranger, name="Mine", organization_id="my-org")

    with pytest.raises(MCPToolError) as exc:
        benchmarks_cohort_list(user=stranger, organization_id="other-org")
    assert exc.value.code == "PERMISSION_DENIED"

    rows = benchmarks_cohort_list(user=stranger, organization_id="my-org")["cohorts"]
    assert [r["id"] for r in rows] == [mine["id"]]


# --- cohort administration is org-gated ---------------------------------
#
# Membership of a cohort IS the read permission (models.py:34): an opportunity
# in a cohort may read that cohort's anonymous peer figures. So an ungated
# `benchmarks_cohort_add_opportunities` is a self-service grant -- enumerate
# cohort ids, add an opportunity you already hold to someone else's cohort, and
# `benchmarks_for_opportunity` then legitimately serves you twelve delivery
# partners' figures, with no publish call and no gate ever tripped. Both writes
# are therefore gated on the caller's organisation, exactly as
# `benchmarks_publish` is.


def test_cohort_create_refuses_an_organisation_the_caller_does_not_belong_to(monkeypatch):
    """Discriminating by construction: the SAME caller is refused for the org
    they are outside and allowed for the org they are inside, in one test. A
    create that ignored the check would succeed in both halves; an "always
    deny" one would fail the second half."""
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_create

    _grant(monkeypatch, organizations=("my-org",))
    user = _user()

    with pytest.raises(MCPToolError) as exc:
        benchmarks_cohort_create(user=user, name="Theirs", organization_id="someone-elses-org")
    assert exc.value.code == "PERMISSION_DENIED"
    assert not BenchmarkCohort.objects.filter(organization_id="someone-elses-org").exists()

    out = benchmarks_cohort_create(user=user, name="Mine", organization_id="my-org")
    assert BenchmarkCohort.objects.get(pk=out["id"]).organization_id == "my-org"


def test_cohort_add_opportunities_refuses_a_cohort_in_another_organisation(monkeypatch):
    """The escalation path itself. Two cohorts exist -- one owned by the
    caller's org, one by a foreign org -- so a tool that skipped the check
    would visibly add to BOTH. The caller holds opp 523 either way, which is
    the whole point: holding the opportunity is what read enforcement checks,
    so it cannot also be what authorises the add."""
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_add_opportunities, benchmarks_cohort_create

    # Set the foreign cohort up as its own owner would have.
    owner = _user("owner")
    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(900,))
    theirs = benchmarks_cohort_create(user=owner, name="Theirs", organization_id="dimagi-kmc")
    benchmarks_cohort_add_opportunities(user=owner, cohort_id=theirs["id"], opportunity_ids=[900])

    attacker = _user("attacker")
    _grant(monkeypatch, organizations=("my-org",), opportunity_ids=(523,))
    mine = benchmarks_cohort_create(user=attacker, name="Mine", organization_id="my-org")

    with pytest.raises(MCPToolError) as exc:
        benchmarks_cohort_add_opportunities(user=attacker, cohort_id=theirs["id"], opportunity_ids=[523])
    assert exc.value.code == "PERMISSION_DENIED"
    assert BenchmarkCohort.objects.get(pk=theirs["id"]).opportunity_ids == {900}

    # ...and the same caller, same opportunity, into their OWN org's cohort, works.
    out = benchmarks_cohort_add_opportunities(user=attacker, cohort_id=mine["id"], opportunity_ids=[523])
    assert set(out["opportunity_ids"]) == {523}


def test_cohort_add_opportunities_refuses_an_opportunity_the_caller_does_not_hold(monkeypatch):
    """The org gate alone still lets a member of org A pull org B's
    opportunity into A's cohort. The caller's opportunity list rides on the
    same fetch the org gate already made, so this check is free. Two
    opportunities are passed in one call -- one held, one not -- so a tool
    that checked nothing would add both."""
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_add_opportunities, benchmarks_cohort_create

    _grant(monkeypatch, organizations=("my-org",), opportunity_ids=(523,))
    user = _user()
    cohort = benchmarks_cohort_create(user=user, name="Mine", organization_id="my-org")

    with pytest.raises(MCPToolError) as exc:
        benchmarks_cohort_add_opportunities(user=user, cohort_id=cohort["id"], opportunity_ids=[523, 874])
    assert exc.value.code == "PERMISSION_DENIED"
    assert "874" in str(exc.value)
    # Refused as a whole -- the held one must not have been written either.
    assert BenchmarkCohort.objects.get(pk=cohort["id"]).opportunity_ids == set()

    out = benchmarks_cohort_add_opportunities(user=user, cohort_id=cohort["id"], opportunity_ids=[523])
    assert set(out["opportunity_ids"]) == {523}


def test_an_unreachable_connect_is_an_upstream_error_not_a_permission_denial(monkeypatch):
    """`fetch_user_organization_data` returns None when it cannot REACH
    Connect, which is not the same fact as "you belong to no organisations".
    Still fails closed -- nothing is written -- but an authorised publisher
    hitting a blip must not be told they lack permission (the line
    workflows.py:915-920 already draws)."""
    from connect_labs.benchmarks import mcp_tools

    _grant(monkeypatch, organizations=("my-org",))
    user = _user()
    cohort = mcp_tools.benchmarks_cohort_create(user=user, name="Mine", organization_id="my-org")

    monkeypatch.setattr("connect_labs.labs.access.scopes.fetch_user_organization_data", lambda token, owner=None: None)

    for call in (
        lambda: mcp_tools.benchmarks_cohort_create(user=user, name="Another", organization_id="my-org"),
        lambda: mcp_tools.benchmarks_cohort_add_opportunities(
            user=user, cohort_id=cohort["id"], opportunity_ids=[523]
        ),
    ):
        with pytest.raises(MCPToolError) as exc:
            call()
        assert exc.value.code == "UPSTREAM_ERROR"

    assert BenchmarkCohort.objects.count() == 1
    assert BenchmarkCohort.objects.get(pk=cohort["id"]).opportunity_ids == set()


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
        "benchmarks_cohort_delete",
        "benchmarks_publish",
        "benchmarks_create_opp_reports",
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

# The workflow every publish test below publishes under. Named because
# `_StubRun.definition_id` defaults to it: a run and the workflow_id it is
# published under must agree (see
# test_publish_refuses_a_run_that_belongs_to_a_different_workflow), so the
# happy-path tests exercise the matching branch of that guard rather than
# skipping it with a None.
WORKFLOW_ID = 19778


class _StubRun:
    """A minimal stand-in for WorkflowRunRecord -- only what the publisher reads."""

    def __init__(
        self,
        *,
        is_completed,
        snapshot=None,
        period_end=None,
        completed_at=None,
        definition_id=WORKFLOW_ID,
    ):
        self.is_completed = is_completed
        self.snapshot = snapshot
        self.period_end = period_end
        self.completed_at = completed_at
        self.definition_id = definition_id


class _StubDefinition:
    """A workflow definition carrying an instance-owned snapshot manifest --
    the shape `resolve_snapshot_contract` reads (templates/__init__.py:720)."""

    def __init__(self, data, template_type=None):
        self.data = data
        self.template_type = template_type


def _wrapped_snapshot(graded_payload, state_key="snapshot"):
    """The exact shape ``workflow/snapshot_builders.wrap_for_runner`` produces:
    ``{"state": {<state_key>: <graded payload>}, "pipelines": {}, "workers": []}``.
    This is what a saved run actually stores at ``run.data["snapshot"]`` --
    verified against ``connect_labs/workflow/snapshot_builders.py`` and
    ``connect_labs/workflow/history_rebuild.py``'s identical read of it.
    """
    return {"state": {state_key: graded_payload}, "pipelines": {}, "workers": []}


def _publishable_cohort(monkeypatch, user, *, name="KMC", organization_id="dimagi-kmc"):
    """A cohort under `organization_id`, populated with the SNAPSHOT's opps, by
    a caller who belongs to that org and holds those opportunities."""
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.tests.test_publish import OPPS

    _grant(monkeypatch, organizations=(organization_id,), opportunity_ids=tuple(OPPS))
    cohort = mcp_tools.benchmarks_cohort_create(user=user, name=name, organization_id=organization_id)
    mcp_tools.benchmarks_cohort_add_opportunities(user=user, cohort_id=cohort["id"], opportunity_ids=list(OPPS))
    return cohort


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


def test_publish_refuses_a_run_that_is_not_completed(monkeypatch):
    """Publishing an in-progress run would push figures that are still moving.

    The cohort is created first ON PURPOSE: with a bare `cohort_id=1` this
    raised NOT_FOUND before ever reaching the completeness guard, so it was
    green whether or not that guard existed. Asserting the message names
    completion (not "not found either") is the other half of that.
    """
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.tests.test_publish import OPPS
    from connect_labs.workflow.data_access import WorkflowDataAccess

    user = _user()
    cohort = _publishable_cohort(monkeypatch, user)
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: _StubRun(is_completed=False))
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    with pytest.raises(MCPToolError) as exc:
        mcp_tools.benchmarks_publish(
            user=user, cohort_id=cohort["id"], workflow_id=WORKFLOW_ID, run_id=1, opportunity_id=OPPS[0]
        )
    assert "not completed" in str(exc.value).lower()


def test_publish_refuses_an_in_progress_run_even_though_a_completed_one_exists(monkeypatch):
    """The completed-run guard has to reject an in_progress run SPECIFICALLY,
    not merely reject "some run" -- proven by a genuinely completed run,
    same cohort and workflow, that DOES publish right after."""
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.tests.test_publish import OPPS, SNAPSHOT
    from connect_labs.workflow.data_access import WorkflowDataAccess

    user = _user()
    cohort = _publishable_cohort(monkeypatch, user)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    runs = {
        1: _StubRun(is_completed=False),
        2: _StubRun(is_completed=True, snapshot=_wrapped_snapshot(SNAPSHOT), period_end="2026-09-11"),
    }
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: runs[run_id])

    with pytest.raises(Exception) as exc:
        mcp_tools.benchmarks_publish(
            user=user, cohort_id=cohort["id"], workflow_id=WORKFLOW_ID, run_id=1, opportunity_id=OPPS[0]
        )
    assert "complete" in str(exc.value).lower()

    out = mcp_tools.benchmarks_publish(
        user=user, cohort_id=cohort["id"], workflow_id=WORKFLOW_ID, run_id=2, opportunity_id=OPPS[0]
    )
    assert out["value_count"] > 0


def test_publish_refuses_a_run_that_belongs_to_a_different_workflow(monkeypatch):
    """A run loads fine under a foreign workflow_id, and everything downstream
    reads as normal: the state_key comes from the WRONG definition's contract
    and `source_workflow_id` -- the provenance an anonymised figure's
    defensibility rests on -- is written false. Discriminating: the same run,
    published under its OWN workflow id, succeeds immediately after, so a
    guard that simply always raised would fail the second half."""
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.models import BenchmarkPublication
    from connect_labs.benchmarks.tests.test_publish import OPPS, SNAPSHOT
    from connect_labs.workflow.data_access import WorkflowDataAccess

    user = _user()
    cohort = _publishable_cohort(monkeypatch, user)
    run = _StubRun(
        is_completed=True,
        snapshot=_wrapped_snapshot(SNAPSHOT),
        period_end="2026-09-11",
        definition_id=6621,
    )
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: run)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    with pytest.raises(MCPToolError) as exc:
        mcp_tools.benchmarks_publish(
            user=user, cohort_id=cohort["id"], workflow_id=19778, run_id=6888, opportunity_id=OPPS[0]
        )
    assert exc.value.code == "INVALID_SCHEMA"
    assert "6621" in str(exc.value)
    assert BenchmarkPublication.objects.count() == 0

    out = mcp_tools.benchmarks_publish(
        user=user, cohort_id=cohort["id"], workflow_id=6621, run_id=6888, opportunity_id=OPPS[0]
    )
    assert BenchmarkPublication.objects.get(pk=out["publication_id"]).source_workflow_id == 6621


def test_publish_reads_the_state_key_the_definitions_contract_names(monkeypatch):
    """`state_key` is spec-driven: a workflow whose `snapshot_inputs` names a
    different key stores its graded payload under THAT key, and the publisher
    must follow the contract rather than assume "snapshot".

    Every other publish test stubs `get_definition -> None`, so the default
    branch is all they reach -- hardcoding `state_key = "snapshot"` left all
    of them green. Here the payload lives ONLY under the contract's key, so
    the hardcode finds an empty dict and the publish fails.
    """
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.tests.test_publish import OPPS, SNAPSHOT
    from connect_labs.workflow.data_access import WorkflowDataAccess

    user = _user()
    cohort = _publishable_cohort(monkeypatch, user)
    run = _StubRun(
        is_completed=True,
        snapshot=_wrapped_snapshot(SNAPSHOT, state_key="kmc_metrics"),
        period_end="2026-09-11",
    )
    definition = _StubDefinition({"snapshot_inputs": {"state_key": "kmc_metrics", "builder": "semantic_snapshot"}})
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: run)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: definition)

    out = mcp_tools.benchmarks_publish(
        user=user, cohort_id=cohort["id"], workflow_id=WORKFLOW_ID, run_id=1, opportunity_id=OPPS[0]
    )
    assert out["value_count"] > 0


def test_publish_refuses_a_run_it_cannot_date(monkeypatch):
    """`as_of` is NOT NULL on BenchmarkPublication. With no meta.as_of, no
    period_end and no completed_at there is nothing to date the publication
    with, and without an explicit refusal the caller gets a raw psycopg
    IntegrityError naming a column. Discriminating: the same run with a
    period_end publishes, so this cannot be "always refuse"."""
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.models import BenchmarkPublication
    from connect_labs.benchmarks.tests.test_publish import OPPS, SNAPSHOT
    from connect_labs.workflow.data_access import WorkflowDataAccess

    user = _user()
    cohort = _publishable_cohort(monkeypatch, user)
    undated = _StubRun(is_completed=True, snapshot=_wrapped_snapshot(SNAPSHOT))
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: undated)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    with pytest.raises(MCPToolError) as exc:
        mcp_tools.benchmarks_publish(
            user=user, cohort_id=cohort["id"], workflow_id=WORKFLOW_ID, run_id=1, opportunity_id=OPPS[0]
        )
    assert exc.value.code == "INVALID_SCHEMA"
    assert "as-of" in str(exc.value).lower()
    assert BenchmarkPublication.objects.count() == 0

    dated = _StubRun(is_completed=True, snapshot=_wrapped_snapshot(SNAPSHOT), completed_at="2026-09-11T00:00:00Z")
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: dated)
    out = mcp_tools.benchmarks_publish(
        user=user, cohort_id=cohort["id"], workflow_id=WORKFLOW_ID, run_id=1, opportunity_id=OPPS[0]
    )
    assert out["as_of"] == "2026-09-11"


def test_publish_refuses_a_caller_without_access_to_the_cohorts_organisation(monkeypatch):
    """Same cohort, same completed run: a caller outside the cohort's
    organisation is refused, and a caller inside it is not -- proven with
    both, not just the refusal alone, so the check can't be "always deny"."""
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.benchmarks.tests.test_publish import OPPS, SNAPSHOT
    from connect_labs.workflow.data_access import WorkflowDataAccess

    owner = _user("owner")
    cohort = _publishable_cohort(monkeypatch, owner)

    run = _StubRun(is_completed=True, snapshot=_wrapped_snapshot(SNAPSHOT), period_end="2026-09-11")
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: run)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    outsider = _user("outsider")
    _grant(monkeypatch, organizations=("some-other-org",), opportunity_ids=tuple(OPPS))
    with pytest.raises(MCPToolError) as exc:
        mcp_tools.benchmarks_publish(
            user=outsider, cohort_id=cohort["id"], workflow_id=WORKFLOW_ID, run_id=1, opportunity_id=OPPS[0]
        )
    assert exc.value.code == "PERMISSION_DENIED"

    member = _user("member")
    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=tuple(OPPS))
    out = mcp_tools.benchmarks_publish(
        user=member, cohort_id=cohort["id"], workflow_id=WORKFLOW_ID, run_id=1, opportunity_id=OPPS[0]
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
    target = _publishable_cohort(monkeypatch, user, name="Target")
    other = _publishable_cohort(monkeypatch, user, name="Other")

    run = _StubRun(is_completed=True, snapshot=_wrapped_snapshot(SNAPSHOT), period_end="2026-09-11")
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: run)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    out = mcp_tools.benchmarks_publish(
        user=user, cohort_id=target["id"], workflow_id=WORKFLOW_ID, run_id=1, opportunity_id=OPPS[0]
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
    cohort = _publishable_cohort(monkeypatch, user)

    run = _StubRun(is_completed=True, snapshot=_wrapped_snapshot(SNAPSHOT), period_end="2026-09-11")
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: run)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    out = mcp_tools.benchmarks_publish(
        user=user, cohort_id=cohort["id"], workflow_id=WORKFLOW_ID, run_id=1, opportunity_id=OPPS[0]
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
    cohort = _publishable_cohort(monkeypatch, user)

    # snapshot wrapped under the WRONG state_key -- the tool looks for
    # "snapshot" by default and finds nothing.
    run = _StubRun(
        is_completed=True, snapshot=_wrapped_snapshot({}, state_key="not_snapshot"), period_end="2026-09-11"
    )
    monkeypatch.setattr(WorkflowDataAccess, "get_run", lambda self, run_id, **kw: run)
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda self, definition_id: None)

    with pytest.raises(SnapshotShapeError):
        mcp_tools.benchmarks_publish(
            user=user, cohort_id=cohort["id"], workflow_id=WORKFLOW_ID, run_id=1, opportunity_id=OPPS[0]
        )
    assert BenchmarkPublication.objects.count() == 0


def test_both_surfaces_authorise_through_the_one_policy():
    """One policy, both surfaces -- the duplication is what drifted.

    The binding check is structural: each module's `may_use` must BE the
    policy's object, which a mention in a comment or a local reimplementation
    of the same name cannot satisfy. The source-string assertions after it are
    a supplement -- they pin that the two names the duplication went by have
    not come back -- and a source string alone would pass on a comment, so they
    are not the evidence here.
    """
    import inspect

    from connect_labs.benchmarks import data_access, mcp_tools
    from connect_labs.labs.access import scopes

    for module in (data_access, mcp_tools):
        assert (
            getattr(module, "may_use", None) is scopes.may_use
        ), f"{module.__name__} does not consult the shared policy"
    assert "_caller_organization_slugs" not in inspect.getsource(mcp_tools)
    assert "_accessible_opp_ids" not in inspect.getsource(data_access)


def test_an_unreachable_connect_at_the_opportunity_check_is_upstream_not_denied(monkeypatch):
    """The opportunity gate is a SECOND resolution, and the window is real.

    `_require_organization_access` resolves the caller once; the held-opportunity
    check resolves again. If Connect's TTL cache expires in between and the
    refetch blips, the shared policy used to hand back an empty set and this
    tool told an authorised caller "You do not hold opportunity 523" -- a
    permission verdict for a network fault, which is exactly what
    `_raise_for_denial`'s UPSTREAM_ERROR branch exists to avoid on the org gate.
    """
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_add_opportunities, benchmarks_cohort_create

    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(523,))
    user = _user()
    cohort = benchmarks_cohort_create(user=user, name="KMC", organization_id="dimagi-kmc")

    calls = {"n": 0}

    def _blip_after_the_org_gate(token, owner=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"organizations": [{"slug": "dimagi-kmc"}], "opportunities": [{"id": 523}]}
        return None

    monkeypatch.setattr(
        "connect_labs.labs.access.scopes.fetch_user_organization_data",
        _blip_after_the_org_gate,
    )

    with pytest.raises(MCPToolError) as exc_info:
        benchmarks_cohort_add_opportunities(user=user, cohort_id=cohort["id"], opportunity_ids=[523])
    assert exc_info.value.code == "UPSTREAM_ERROR"
    assert calls["n"] >= 2, "the org gate never resolved, so this did not exercise the second fetch"
    # Fails closed either way: nothing was written.
    assert BenchmarkCohort.objects.get(pk=cohort["id"]).members.count() == 0


def test_publish_survives_a_history_read_that_fails():
    """A failed history read costs the SERIES and nothing else. The point values
    are the publication's substance and come from the snapshot already in hand,
    so a refused publication would be the worse outcome."""
    from connect_labs.benchmarks.mcp_tools import _run_history

    class _Boom:
        def list_runs(self, definition_id):
            raise RuntimeError("upstream is down")

    assert _run_history(_Boom(), 1, "snapshot") == []


def test_run_history_projects_each_completed_run_to_its_per_opportunity_cells():
    from connect_labs.benchmarks.mcp_tools import _run_history

    def _run(date, value, completed=True):
        return _StubRun(
            is_completed=completed,
            period_end=date,
            snapshot={
                "state": {
                    "snapshot": {
                        "byOpp": [{"opp": 500, "ind": {"C15": {"id": "C15", "value": value, "n": 100}}}],
                        "series": {"N": {"byOpp": [{"opp": 500, "ind": {"N08": {"id": "N08", "value": value}}}]}},
                    }
                }
            },
        )

    class _WDA:
        def list_runs(self, definition_id):
            # out of order, and one still running
            return [_run("2026-02-28", 2), _run("2026-01-31", 1), _run("2026-03-31", 3, completed=False)]

    out = _run_history(_WDA(), 1, "snapshot")
    assert [r["date"] for r in out] == ["2026-01-31", "2026-02-28"], "not oldest-first, or kept an in-progress run"
    assert out[0]["byOpp"]["C"][500]["C15"]["value"] == 1
    assert out[0]["byOpp"]["N"][500]["N08"]["value"] == 1, "the scorecard family was not projected"


def test_run_history_keeps_one_point_per_period_and_the_latest_wins():
    """A period can hold several completed runs — a hand-saved one and the one
    `workflow_rebuild_history` generated for the same week — and they do not
    agree. Taking all of them made consecutive points alternate between two
    unrelated figures for the whole series, which renders as a wildly
    oscillating indicator rather than as the duplication it is."""
    from connect_labs.benchmarks.mcp_tools import _run_history

    def _run(period, completed_at, value):
        return _StubRun(
            is_completed=True,
            period_end=period,
            completed_at=completed_at,
            snapshot={"state": {"snapshot": {"byOpp": [{"opp": 500, "ind": {"C15": {"id": "C15", "value": value}}}]}}},
        )

    class _WDA:
        def list_runs(self, definition_id):
            return [
                _run("2026-01-31", "2026-09-09T19:00:00Z", 41.8),  # hand-saved
                _run("2026-01-31", "2026-09-11T13:00:00Z", 69.2),  # rebuilt later
                _run("2026-02-28", "2026-09-11T14:00:00Z", 70.0),
            ]

    out = _run_history(_WDA(), 1, "snapshot")
    assert [r["date"] for r in out] == ["2026-01-31", "2026-02-28"], "a period contributed more than one point"
    assert out[0]["byOpp"]["C"][500]["C15"]["value"] == 69.2, "the superseded run won"


def test_cohort_create_can_switch_off_the_complete_series_rule(monkeypatch):
    """R6 is the difference between a trend and no trend for a cohort whose
    members joined at different times — it kept 5 of 12 on the live KMC
    cohort — so it has to be settable at creation. There is no update tool, so
    a cohort created without it is stuck with it."""
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_create

    _grant(monkeypatch, organizations=("my-org",))
    user = _user()

    relaxed = benchmarks_cohort_create(
        user=user, name="relaxed", organization_id="my-org", require_complete_series=False
    )
    assert relaxed["require_complete_series"] is False
    assert BenchmarkCohort.objects.get(pk=relaxed["id"]).require_complete_series is False

    # The same caller, same call, without the argument: the safe rule stays on.
    default = benchmarks_cohort_create(user=user, name="default", organization_id="my-org")
    assert default["require_complete_series"] is True
    assert BenchmarkCohort.objects.get(pk=default["id"]).require_complete_series is True


def test_cohort_create_accepts_a_stringified_boolean_and_refuses_a_bogus_one(monkeypatch):
    """MCP clients differ on whether they coerce against the declared schema, so
    the flag can arrive as the string "false". Coerced here rather than left to
    Django, whose ValidationError names a column and not the argument — and
    treated as an error when unrecognisable, because "false" is truthy in Python
    and a silent bool() would switch a disclosure rule ON when asked for OFF."""
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_create

    _grant(monkeypatch, organizations=("my-org",))
    user = _user()

    out = benchmarks_cohort_create(
        user=user, name="stringy", organization_id="my-org", require_complete_series="false"
    )
    assert BenchmarkCohort.objects.get(pk=out["id"]).require_complete_series is False

    with pytest.raises(MCPToolError) as exc:
        benchmarks_cohort_create(user=user, name="bogus", organization_id="my-org", require_complete_series="maybe")
    assert exc.value.code == "INVALID_SCHEMA"
    assert not BenchmarkCohort.objects.filter(name="bogus").exists()


def test_cohort_delete_removes_the_grant_and_its_publications(monkeypatch):
    """A cohort IS a read grant, so revoking one has to be possible — and it has
    to take the published values with it, or the grant is gone and the figures
    it authorised are still sitting there."""
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_create, benchmarks_cohort_delete
    from connect_labs.benchmarks.models import BenchmarkPublication

    _grant(monkeypatch, organizations=("my-org",))
    user = _user()
    created = benchmarks_cohort_create(user=user, name="doomed", organization_id="my-org")
    cohort = BenchmarkCohort.objects.get(pk=created["id"])
    cohort.members.create(opportunity_id=501)
    BenchmarkPublication.objects.create(cohort=cohort, source_workflow_id=1, source_run_id=2, as_of="2026-09-11")

    out = benchmarks_cohort_delete(user=user, cohort_id=cohort.pk)
    assert out["publications_deleted"] == 1 and out["members_removed"] == 1
    assert not BenchmarkCohort.objects.filter(pk=cohort.pk).exists()
    assert not BenchmarkPublication.objects.filter(cohort_id=cohort.pk).exists()


def test_cohort_delete_refuses_a_cohort_in_another_organisation(monkeypatch):
    """Same check that gates creating one — otherwise anyone could revoke
    anyone's benchmark."""
    from connect_labs.benchmarks.mcp_tools import benchmarks_cohort_delete

    theirs = BenchmarkCohort.objects.create(name="theirs", organization_id="someone-elses-org")
    _grant(monkeypatch, organizations=("my-org",))

    with pytest.raises(MCPToolError) as exc:
        benchmarks_cohort_delete(user=_user(), cohort_id=theirs.pk)
    assert exc.value.code == "PERMISSION_DENIED"
    assert BenchmarkCohort.objects.filter(pk=theirs.pk).exists()
