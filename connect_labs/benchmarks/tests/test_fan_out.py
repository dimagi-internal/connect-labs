"""Fan-out: one opportunity report per cohort member, and none of them drifting.

A cohort is twelve opportunities that each want the same report over their own
scope. The failure this guards against is not "the report is wrong" but "the
twelve reports stopped being the same report": a render edited on one instance,
a config flag that reached none of them, a pipeline record copied per scope.

These tests drive the tool over a FAKE workflow store keyed by opportunity --
``list_definitions`` answers per scope and the creation hook writes back into
the same store -- so idempotency is a property of the tool's own logic here,
not of a stub that always says "nothing exists".
"""

import pytest

from connect_labs.mcp.tool_registry import MCPToolError

pytestmark = pytest.mark.django_db

TEMPLATE = "kmc_opp_report"


def _user(username="tester"):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create(username=username)


def _grant(monkeypatch, *, organizations=("dimagi-kmc",), opportunity_ids=()):
    """Stub the caller's Connect org tree -- see test_mcp_tools._grant."""
    from connect_labs.benchmarks import mcp_tools

    monkeypatch.setattr(mcp_tools, "require_connect_token", lambda u: "dummy-token")
    monkeypatch.setattr(
        mcp_tools,
        "fetch_user_organization_data",
        lambda token, owner=None: {
            "organizations": [{"slug": slug} for slug in organizations],
            "opportunities": [{"id": oid} for oid in opportunity_ids],
        },
    )


class _StubDefinition:
    def __init__(self, definition_id, template_type, data=None):
        self.id = definition_id
        self.template_type = template_type
        self.data = data or {}


class _FakeWorkflowStore:
    """Workflow definitions per opportunity scope, as the fan-out sees them.

    Creation writes back into the same store, which is what makes the second
    run a real test of the skip path rather than of a stub.
    """

    def __init__(self, monkeypatch, existing=None):
        from connect_labs.benchmarks import mcp_tools
        from connect_labs.workflow.data_access import WorkflowDataAccess

        self.by_opportunity = {oid: list(defs) for oid, defs in (existing or {}).items()}
        self.create_calls = []
        self._next_id = 9000

        monkeypatch.setattr(
            WorkflowDataAccess,
            "list_definitions",
            lambda dao, include_shared=False: list(self.by_opportunity.get(dao.opportunity_id, [])),
        )
        monkeypatch.setattr(mcp_tools, "create_workflow_from_template", self._create)

    def _create(self, *, data_access, template_key, request=None, **kwargs):
        self._next_id += 1
        call = {"opportunity_id": data_access.opportunity_id, "template_key": template_key, **kwargs}
        self.create_calls.append(call)
        definition = _StubDefinition(self._next_id, template_key, data=dict(kwargs))
        self.by_opportunity.setdefault(data_access.opportunity_id, []).append(definition)
        return definition, None, None


def _cohort(user, monkeypatch, *, opportunity_ids, organization_id="dimagi-kmc", name="KMC"):
    from connect_labs.benchmarks import mcp_tools

    cohort = mcp_tools.benchmarks_cohort_create(user=user, name=name, organization_id=organization_id)
    mcp_tools.benchmarks_cohort_add_opportunities(
        user=user, cohort_id=cohort["id"], opportunity_ids=list(opportunity_ids)
    )
    return cohort


def test_it_creates_one_report_per_member_and_a_second_run_creates_none(monkeypatch):
    """The whole contract in one test.

    Three members, and one of them ALREADY holds an instance of this template,
    so the first run has to distinguish rather than create-all or skip-all: a
    third opportunity holds a workflow of a DIFFERENT template, which must not
    be mistaken for this report.
    """
    from connect_labs.benchmarks import mcp_tools

    user = _user()
    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(523, 874, 1487))
    cohort = _cohort(user, monkeypatch, opportunity_ids=(523, 874, 1487))
    store = _FakeWorkflowStore(
        monkeypatch,
        existing={
            874: [_StubDefinition(4242, TEMPLATE)],
            1487: [_StubDefinition(4243, "kmc_programme_metrics")],
        },
    )

    first = mcp_tools.benchmarks_create_opp_reports(user=user, cohort_id=cohort["id"])

    assert sorted(row["opportunity_id"] for row in first["created"]) == [523, 1487]
    assert [(row["opportunity_id"], row["workflow_id"]) for row in first["skipped"]] == [(874, 4242)]
    assert first["skipped"][0]["reason"]

    second = mcp_tools.benchmarks_create_opp_reports(user=user, cohort_id=cohort["id"])

    assert second["created"] == []
    assert sorted(row["opportunity_id"] for row in second["skipped"]) == [523, 874, 1487]
    # ...and the second run's skips name the workflows the FIRST run created,
    # so "skipped" cannot be a shrug that discarded them.
    created_ids = {row["opportunity_id"]: row["workflow_id"] for row in first["created"]}
    for row in second["skipped"]:
        if row["opportunity_id"] in created_ids:
            assert row["workflow_id"] == created_ids[row["opportunity_id"]]
    assert len(store.create_calls) == 2, "the second run created a workflow"


def test_every_instance_follows_the_deployed_template(monkeypatch):
    """`render_source` is the mechanism that stops twelve copies of a render
    drifting -- it is why editing such an instance's stored render is refused."""
    from connect_labs.benchmarks import mcp_tools

    user = _user()
    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(523, 874))
    cohort = _cohort(user, monkeypatch, opportunity_ids=(523, 874))
    store = _FakeWorkflowStore(monkeypatch)

    mcp_tools.benchmarks_create_opp_reports(user=user, cohort_id=cohort["id"])

    assert len(store.create_calls) == 2
    for call in store.create_calls:
        assert call["render_source"] == {"template": TEMPLATE}


def test_the_instances_reference_one_pipeline_record_rather_than_copying_it(monkeypatch):
    """Named a source report, every instance points at THAT report's pipeline
    records via `home_scope` -- one record read where it lives, twelve readers --
    and binds to the same registry record, so the indicator definitions cannot
    fork either. Exercises the real `_linked_sources` rule."""
    from connect_labs.benchmarks import mcp_tools
    from connect_labs.workflow.data_access import PipelineDataAccess, WorkflowDataAccess

    user = _user()
    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(523, 874))
    cohort = _cohort(user, monkeypatch, opportunity_ids=(523, 874))
    store = _FakeWorkflowStore(monkeypatch)

    source = _StubDefinition(
        19780,
        "kmc_programme_metrics",
        data={
            "pipeline_sources": [
                {"pipeline_id": 19776, "alias": "children"},
                {"pipeline_id": 19777, "alias": "visits"},
            ],
            "registry_source": {"registry_id": 55},
        },
    )
    monkeypatch.setattr(WorkflowDataAccess, "get_definition", lambda dao, definition_id: source)
    # Not a shared (public) record, so `_linked_sources` falls back to the
    # source workflow's own scope -- the branch that has to be stamped.
    monkeypatch.setattr(PipelineDataAccess, "get_definition", lambda dao, definition_id: None)

    out = mcp_tools.benchmarks_create_opp_reports(
        user=user,
        cohort_id=cohort["id"],
        source_workflow_id=19780,
        source_program_id=46,
    )

    assert out["shared"] is True
    assert len(store.create_calls) == 2
    for call in store.create_calls:
        assert call["pipeline_sources_override"] == [
            {"pipeline_id": 19776, "alias": "children", "home_scope": {"program_id": 46}},
            {"pipeline_id": 19777, "alias": "visits", "home_scope": {"program_id": 46}},
        ]
        # The binding gains the home scope too, or an opportunity-scoped
        # instance cannot read the record it is bound to.
        assert call["registry_source"] == {"registry_id": 55, "program_id": 46}


def test_without_a_source_workflow_it_says_so_rather_than_pretending(monkeypatch):
    """Nothing to inherit from means each instance creates its own pipeline
    records -- correct, but twelve copies again, so the result must not claim
    otherwise."""
    from connect_labs.benchmarks import mcp_tools

    user = _user()
    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(523,))
    cohort = _cohort(user, monkeypatch, opportunity_ids=(523,))
    store = _FakeWorkflowStore(monkeypatch)

    out = mcp_tools.benchmarks_create_opp_reports(user=user, cohort_id=cohort["id"])

    assert out["shared"] is False
    assert store.create_calls[0]["pipeline_sources_override"] is None
    assert store.create_calls[0]["registry_source"] is None


def test_a_source_workflow_needs_exactly_one_scope(monkeypatch):
    """A workflow record is only readable from its own scope, so an unscoped
    source id would silently read as 'not found' against the caller's."""
    from connect_labs.benchmarks import mcp_tools

    user = _user()
    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(523,))
    cohort = _cohort(user, monkeypatch, opportunity_ids=(523,))
    store = _FakeWorkflowStore(monkeypatch)

    for kwargs in (
        {"source_workflow_id": 19780},
        {"source_workflow_id": 19780, "source_opportunity_id": 523, "source_program_id": 46},
        {"source_opportunity_id": 523},
    ):
        with pytest.raises(MCPToolError) as exc:
            mcp_tools.benchmarks_create_opp_reports(user=user, cohort_id=cohort["id"], **kwargs)
        assert exc.value.code == "INVALID_SCHEMA"
    assert store.create_calls == [], "a bad source argument still created workflows"


def test_it_refuses_a_cohort_in_an_organisation_the_caller_does_not_belong_to(monkeypatch):
    """Membership of a cohort IS the read grant for its published peer figures,
    so a tool that creates the workflows which READ them, against someone else's
    cohort, is another way in -- the escalation Task 2 found in two sibling
    tools.

    Discriminating by construction: the foreign cohort really has members the
    caller really holds (so only the ORG gate can refuse it), and the same
    caller fanning out their OWN cohort succeeds immediately after, so an
    always-deny tool fails the second half.
    """
    from connect_labs.benchmarks import mcp_tools

    owner = _user("owner")
    _grant(monkeypatch, organizations=("their-org",), opportunity_ids=(523, 874))
    theirs = _cohort(owner, monkeypatch, opportunity_ids=(523,), organization_id="their-org", name="Theirs")

    attacker = _user("attacker")
    _grant(monkeypatch, organizations=("my-org",), opportunity_ids=(523, 874))
    mine = _cohort(attacker, monkeypatch, opportunity_ids=(874,), organization_id="my-org", name="Mine")
    store = _FakeWorkflowStore(monkeypatch)

    with pytest.raises(MCPToolError) as exc:
        mcp_tools.benchmarks_create_opp_reports(user=attacker, cohort_id=theirs["id"])
    assert exc.value.code == "PERMISSION_DENIED"
    assert store.create_calls == [], "a workflow was created against another organisation's cohort"

    out = mcp_tools.benchmarks_create_opp_reports(user=attacker, cohort_id=mine["id"])
    assert [row["opportunity_id"] for row in out["created"]] == [874]


def test_it_refuses_when_the_caller_does_not_hold_every_member_opportunity(monkeypatch):
    """Creating a workflow inside an opportunity's scope is a write into that
    opportunity. Two members, one held and one not, so a tool that checked
    nothing would write into both -- and nothing is created for the held one
    either, because a half-done fan-out is worse than none."""
    from connect_labs.benchmarks import mcp_tools

    user = _user()
    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(523, 874))
    cohort = _cohort(user, monkeypatch, opportunity_ids=(523, 874))
    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(523,))
    store = _FakeWorkflowStore(monkeypatch)

    with pytest.raises(MCPToolError) as exc:
        mcp_tools.benchmarks_create_opp_reports(user=user, cohort_id=cohort["id"])
    assert exc.value.code == "PERMISSION_DENIED"
    assert "874" in str(exc.value)
    assert store.create_calls == []

    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(523, 874))
    out = mcp_tools.benchmarks_create_opp_reports(user=user, cohort_id=cohort["id"])
    assert sorted(row["opportunity_id"] for row in out["created"]) == [523, 874]


def test_it_refuses_an_unknown_cohort_and_an_unknown_template(monkeypatch):
    """Both up front: a typo must not create eleven workflows and then fail."""
    from connect_labs.benchmarks import mcp_tools

    user = _user()
    _grant(monkeypatch, organizations=("dimagi-kmc",), opportunity_ids=(523,))
    cohort = _cohort(user, monkeypatch, opportunity_ids=(523,))
    store = _FakeWorkflowStore(monkeypatch)

    with pytest.raises(MCPToolError) as exc:
        mcp_tools.benchmarks_create_opp_reports(user=user, cohort_id=cohort["id"] + 1000)
    assert exc.value.code == "NOT_FOUND"

    with pytest.raises(MCPToolError) as exc:
        mcp_tools.benchmarks_create_opp_reports(user=user, cohort_id=cohort["id"], template_key="no_such_template")
    assert exc.value.code == "NOT_FOUND"
    assert store.create_calls == []


def test_an_empty_cohort_creates_nothing(monkeypatch):
    from connect_labs.benchmarks import mcp_tools

    user = _user()
    _grant(monkeypatch, organizations=("dimagi-kmc",))
    cohort = mcp_tools.benchmarks_cohort_create(user=user, name="Empty", organization_id="dimagi-kmc")
    store = _FakeWorkflowStore(monkeypatch)

    out = mcp_tools.benchmarks_create_opp_reports(user=user, cohort_id=cohort["id"])
    assert out == {"created": [], "skipped": [], "shared": False}
    assert store.create_calls == []


def test_the_default_template_is_the_opportunity_report_and_it_is_registered():
    """The default has to name a template that exists, or the tool's own
    up-front check turns every default call into a NOT_FOUND."""
    import inspect

    from connect_labs.benchmarks.mcp_tools import benchmarks_create_opp_reports
    from connect_labs.mcp.tool_registry import get_tool
    from connect_labs.workflow.templates import get_template

    default = inspect.signature(benchmarks_create_opp_reports).parameters["template_key"].default
    assert default == TEMPLATE
    assert get_template(default) is not None

    tool = get_tool("benchmarks_create_opp_reports")
    assert tool is not None and tool.is_write is True
