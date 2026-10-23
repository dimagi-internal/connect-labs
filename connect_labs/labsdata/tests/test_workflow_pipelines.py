"""A workflow's pipelines, read for labsdata as the person -- wherever the workflow says they live.

KMC's per-opportunity reports (``kmc_opp_report``, fanned out by
``benchmarks_create_opp_reports``) do not own their pipelines: each references the
programme report's, shared by their owner, as ``home_scope: {"public": true}``. The
run page reads them there; ``data_workflow_context`` read every source in the
workflow's own opportunity instead, so the agent on run 25553 (workflow 21137,
opportunity 2166) was told the ``children`` (19776) and ``visits`` (19777) pipelines
were unreadable while the person was looking at them.

These tests stand in for Connect's record API with the person's own reach, so what is
checked is the rule: a workflow's own sources are read where they live, exactly when
the person could read them in Labs, and nothing else is read or written.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from connect_labs.labs.access import scopes
from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.labsdata import scope, workflow_context
from connect_labs.labsdata.scope import Opportunity
from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.workflow.data_access import PipelineDefinitionRecord

WORKFLOW_OPP = 2166
PROGRAMME_OWNER_OPP = 523
PROGRAM = 7
OTHER_WORKFLOWS_OPP = 999

pytestmark = pytest.mark.django_db

SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [{"name": "weight", "path": "form.weight", "aggregation": "avg", "transform": "float"}],
}

#: What Connect holds: each pipeline record, where it is owned, and whether its owner shared it.
RECORDS = {
    19776: {"owner": {"opportunity_id": PROGRAMME_OWNER_OPP}, "public": True, "name": "KMC Case Properties (SQL)"},
    19777: {"owner": {"opportunity_id": PROGRAMME_OWNER_OPP}, "public": True, "name": "KMC Weight Series"},
    # Owned by its owner and not shared (or shared, then unshared).
    19778: {"owner": {"opportunity_id": PROGRAMME_OWNER_OPP}, "public": False, "name": "Not shared"},
    501: {"owner": {"program_id": PROGRAM}, "public": False, "name": "Programme pipeline"},
    601: {"owner": {"opportunity_id": OTHER_WORKFLOWS_OPP}, "public": False, "name": "Another workflow's"},
}

#: Whose token reaches which scopes in Connect (the record ACL Labs itself reads under).
REACH = {
    "staff": {("opportunity_id", WORKFLOW_OPP), ("opportunity_id", PROGRAMME_OWNER_OPP), ("program_id", PROGRAM)},
    "staff-everywhere": {
        ("opportunity_id", WORKFLOW_OPP),
        ("opportunity_id", PROGRAMME_OWNER_OPP),
        ("opportunity_id", OTHER_WORKFLOWS_OPP),
        ("program_id", PROGRAM),
    },
    "llo": {("opportunity_id", WORKFLOW_OPP)},
}


def _record(pipeline_id):
    held = RECORDS[pipeline_id]
    return PipelineDefinitionRecord(
        {
            "id": pipeline_id,
            "experiment": "pipeline",
            "type": "pipeline_definition",
            "data": {"name": held["name"], "schema": SCHEMA, "version": 3, "is_shared": held["public"]},
            "opportunity_id": None,
            "program_id": None,
            "organization_id": None,
            **held["owner"],
        }
    )


class FakeConnect:
    """Connect's record reads, as the bearer of the client's token can make them."""

    def __init__(self):
        self.reads: list[tuple] = []

    def scoped(self, client, record_id, experiment=None, type=None, model_class=None, **override):
        # An override is the ONLY scope sent (``get_record_by_id``); else the client's own.
        override = {k: v for k, v in override.items() if v is not None}
        own = {
            k: getattr(client, k)
            for k in ("opportunity_id", "program_id", "organization_id")
            if getattr(client, k) is not None
        }
        (key, value), *_ = (override or own).items()
        self.reads.append(("scoped", record_id, key, value))
        held = RECORDS.get(record_id)
        if held is None or held["owner"] != {key: value} or (key, value) not in REACH[client.access_token]:
            return None
        return _record(record_id)

    def public(self, client, record_id, experiment=None, type=None, model_class=None):
        self.reads.append(("public", record_id))
        held = RECORDS.get(record_id)
        # Anyone signed in reads a shared record; an unshared one is not returned unscoped.
        return _record(record_id) if held and held["public"] else None


@pytest.fixture
def connect():
    fake = FakeConnect()
    with (
        mock.patch.object(LabsRecordAPIClient, "get_record_by_id", lambda self, *a, **kw: fake.scoped(self, *a, **kw)),
        mock.patch.object(
            LabsRecordAPIClient, "get_public_record_by_id", lambda self, *a, **kw: fake.public(self, *a, **kw)
        ),
    ):
        yield fake


def _definition(sources):
    return SimpleNamespace(
        id=21137,
        name="KMC Opportunity Report",
        template_type="kmc_opp_report",
        registry_source={"registry_id": 19784, "organization_id": 179},
        opportunity_ids=[],
        data={"config": {"agent": {"share": True, "agent": "data"}}},
        pipeline_sources=sources,
    )


def _context(token, sources):
    class FakeWDA:
        def __init__(self, **kw):
            pass

        def get_definition(self, _id):
            return _definition(sources)

        def close(self):
            pass

    with (
        mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess", FakeWDA),
        mock.patch.object(scope, "resolve", return_value=[Opportunity(id=WORKFLOW_OPP, name="KMC UG", llo="L")]),
        mock.patch.object(scope, "left_out_of", return_value=[]),
    ):
        out = workflow_context.workflow_context(scopes.Caller(access_token=token), 21137, opportunity_id=WORKFLOW_OPP)
    return {p["alias"]: p for p in out["pipelines"]}


KMC_OPP_REPORT_SOURCES = [
    {"alias": "children", "home_scope": {"public": True}, "pipeline_id": 19776},
    {"alias": "visits", "home_scope": {"public": True}, "pipeline_id": 19777},
]


def test_a_workflows_shared_pipelines_are_read_where_they_live(connect):
    """Workflow 21137's own case: both shared programme pipelines come back whole."""
    pipelines = _context("staff", KMC_OPP_REPORT_SOURCES)

    for alias, pipeline_id in (("children", 19776), ("visits", 19777)):
        entry = pipelines[alias]
        assert "error" not in entry, entry
        assert entry["name"] == RECORDS[pipeline_id]["name"]
        assert entry["home_scope"] == {"public": True}
        assert [f["name"] for f in entry["fields"]] == ["weight"]
        assert entry["visits_sql"]
    # Read where they live, never in the workflow's opportunity, which does not own them.
    assert connect.reads == [("public", 19776), ("public", 19777)]


def test_a_programme_pipeline_is_read_in_its_programme_by_someone_who_holds_it(connect):
    pipelines = _context("staff", [{"alias": "programme", "home_scope": {"program_id": PROGRAM}, "pipeline_id": 501}])

    assert "error" not in pipelines["programme"], pipelines["programme"]
    assert pipelines["programme"]["home_scope"] == {"program_id": PROGRAM}
    assert connect.reads == [("scoped", 501, "program_id", PROGRAM)]


def test_a_person_who_cannot_read_a_pipeline_in_labs_is_refused_it(connect):
    """Their own reach decides: a programme they are not in, a pipeline its owner has not shared."""
    pipelines = _context(
        "llo",
        [
            {"alias": "programme", "home_scope": {"program_id": PROGRAM}, "pipeline_id": 501},
            {"alias": "unshared", "home_scope": {"public": True}, "pipeline_id": 19778},
        ],
    )

    for alias in ("programme", "unshared"):
        entry = pipelines[alias]
        assert "not readable by this person" in entry["error"]
        assert "fields" not in entry and "visits_sql" not in entry


def test_another_workflows_pipeline_stays_refused(connect):
    """Only the workflow's own sources are read, each only where the source says it lives.

    A source with no home scope is read in the workflow's own scope and nowhere else --
    not unscoped, not in the scope that owns it -- so a pipeline of another workflow is
    refused even to someone who could open it there.
    """
    pipelines = _context("staff-everywhere", [{"alias": "borrowed", "pipeline_id": 601}])

    assert "not readable by this person in the workflow's own scope" in pipelines["borrowed"]["error"]
    assert connect.reads == [("scoped", 601, "opportunity_id", WORKFLOW_OPP)]


def test_writes_to_a_shared_pipeline_are_unchanged(connect):
    """Reading a shared pipeline through the workflow gives no write to it.

    ``workflow:edit`` saves through ``pipeline_update_schema`` (``workflow_edit_apply``),
    which reads and writes only in the scope it is given: from the workflow's
    opportunity, a pipeline owned elsewhere is not found, and nothing is written.
    """
    from connect_labs.mcp.tools.pipelines import pipeline_update_schema
    from connect_labs.workflow.data_access import PipelineDataAccess

    with (
        mock.patch("connect_labs.mcp.tools.pipelines.require_connect_token", return_value="staff"),
        mock.patch.object(PipelineDataAccess, "update_definition") as update,
        pytest.raises(MCPToolError) as refused,
    ):
        pipeline_update_schema(
            SimpleNamespace(pk=3, username="staff"),
            pipeline_id=19776,
            schema=SCHEMA,
            expected_version=3,
            opportunity_id=WORKFLOW_OPP,
        )

    assert refused.value.code == "NOT_FOUND"
    update.assert_not_called()
    assert connect.reads == [("scoped", 19776, "opportunity_id", WORKFLOW_OPP)]
