"""Row level, whole visits, Scout's result shaping and errors, and the cleared-agent path.

Fixture data is invented (no real visits, no names): this repo is public.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from connect_labs.audit_trail.models import AuditEvent
from connect_labs.labs import canopy_panel
from connect_labs.labs.access import scopes
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT
from connect_labs.labsdata import engine, scope, service, workflow_context
from connect_labs.labsdata.engine import QueryError, fetch_visits, run_query
from connect_labs.labsdata.scope import Opportunity

pytestmark = pytest.mark.django_db

OPP = Opportunity(id=931, name="Opp R", llo="LLO R")
OTHER = Opportunity(id=932, name="Opp S", llo="LLO S")


def _visit(opp, n, **extra):
    return RawVisitCache(
        opportunity_id=opp,
        pipeline_id=USER_VISITS_RAW_SLOT,
        visit_count=6,
        expires_at=timezone.now() + timedelta(hours=1),
        visit_id=f"v{opp}-{n}",
        username=f"flw{n % 2}",
        entity_id=f"case{n % 3}",
        visit_date=date(2026, 9, 1) + timedelta(days=n),
        status="approved" if n % 4 else "rejected",
        reason="" if n % 4 else "duplicate",
        images=[{"question": "photo", "id": f"img{n}"}],
        form_json={"form": {"child_alive": "yes" if n % 5 else "no", "weight": str(1500 + n), "notes": "x" * 9000}},
        **extra,
    )


@pytest.fixture
def cache():
    RawVisitCache.objects.bulk_create([_visit(OPP.id, n) for n in range(6)] + [_visit(OTHER.id, n) for n in range(3)])


def _caller():
    return scopes.Caller(access_token="t")


@pytest.fixture
def resolved():
    with mock.patch.object(scope, "resolve", return_value=[OPP]):
        yield


# --- the engine -------------------------------------------------------------


def test_a_result_that_fills_the_cap_exactly_is_not_truncated(cache):
    exact = run_query([OPP], "SELECT visit_id FROM visits", max_rows=6)
    over = run_query([OPP], "SELECT visit_id FROM visits", max_rows=5)
    asked_less = run_query([OPP], "SELECT visit_id FROM visits LIMIT 2", max_rows=5)
    assert (exact.row_count, exact.truncated) == (6, False)
    assert (over.row_count, over.truncated) == (5, True)
    assert (asked_less.row_count, asked_less.truncated) == (2, False)


def test_visits_carries_every_raw_column(cache):
    result = run_query([OPP], "SELECT * FROM visits WHERE status = 'rejected' ORDER BY visit_id LIMIT 1")
    row = dict(zip(result.columns, result.rows[0]))
    assert row["reason"] == "duplicate"
    assert row["images"] == [{"question": "photo", "id": "img0"}]
    assert row["form_json"]["form"]["child_alive"] == "no"
    assert {c for c, _t, _m in engine.VISIT_COLUMNS} == set(result.columns)


def test_fetch_visits_returns_whole_visits_from_the_given_opportunities_only(cache):
    found = fetch_visits([OPP], [f"v{OPP.id}-1", f"v{OTHER.id}-1"])
    assert [v["visit_id"] for v in found] == [f"v{OPP.id}-1"]
    assert len(found[0]["form_json"]["form"]["notes"]) == 9000  # whole, not cut
    with pytest.raises(QueryError) as e:
        fetch_visits([OPP], [str(n) for n in range(engine.MAX_VISITS_PER_READ + 1)])
    assert e.value.code == "unknown_visits"


def test_errors_say_how_to_recover(cache):
    with pytest.raises(QueryError) as e:
        run_query([OPP], "SELECT * FROM users_user")
    assert e.value.as_dict() == {
        "error": "table_not_allowed",
        "message": e.value.message,
        "category": "sql",
        "retryable": False,
        "recovery_action": "use_allowed_relations",
    }
    with pytest.raises(QueryError) as e:
        run_query([OPP], "SELECT nope FROM visits")
    assert e.value.as_dict()["recovery_action"] == "fix_query"


# --- what an agent receives ---------------------------------------------------


def test_shape_for_agent_cuts_long_cells_and_says_so():
    big = {"form": {"notes": "x" * (service.AGENT_CELL_CHARS + 10)}}
    out = service.shape_for_agent(
        {"columns": ["visit_id", "form_json"], "rows": [["v1", big]], "row_count": 1, "truncated": True}
    )
    cell = out["rows"][0][1]
    assert isinstance(cell, str) and cell.endswith("chars]") and len(cell) < service.AGENT_CELL_CHARS + 40
    assert any("data_visits" in n for n in out["notes"])
    assert any("do not present them as complete" in n for n in out["notes"])


def test_shape_for_agent_keeps_the_result_inside_its_budget():
    row = ["y" * 3000]
    out = service.shape_for_agent({"columns": ["c"], "rows": [row] * 200, "row_count": 200, "truncated": False})
    assert out["rows_returned"] < 200
    assert any("fit in your context" in n for n in out["notes"])


@pytest.mark.parametrize("granted", [False, True])
def test_real_rows_reach_an_agent_only_when_labs_has_cleared_it(cache, resolved, granted):
    clearance = canopy_panel.Clearance(granted, "" if granted else "not cleared")
    with mock.patch.object(canopy_panel, "call_real_data_clearance", return_value=clearance):
        if not granted:
            with pytest.raises(service.RealDataRefused):
                service.query(_caller(), [OPP.id], "SELECT COUNT(*) FROM visits", for_agent=True)
            with pytest.raises(service.RealDataRefused):
                service.visits(_caller(), [OPP.id], [f"v{OPP.id}-1"], for_agent=True)
            return
        out = service.query(_caller(), [OPP.id], "SELECT visit_id, form_json FROM visits", for_agent=True)
        visits = service.visits(_caller(), [OPP.id], [f"v{OPP.id}-1"], for_agent=True)
    assert out["rows_returned"] == 6 and "notes" in out
    assert visits["visits"][0]["visit_id"] == f"v{OPP.id}-1"


def test_no_call_is_cleared_without_canopys_statement(settings):
    # Labs fully configured for labsdata, but outside a call canopy vouched for, the data
    # tools never see real rows (mcp/execution.py; test_execution_statement.py).
    settings.CANOPY_DATA_AGENT_SLUG = "labsdata"
    settings.CANOPY_SENSITIVE_AGENTS = ["labsdata"]
    settings.CANOPY_RUNNER_REQUIREMENTS = ["sensitive_data"]
    assert canopy_panel.call_real_data_clearance().granted is False
    assert service.agent_sees_real_rows(True, [OPP]) is False


def test_a_person_reads_whole_visits_and_it_is_audited(cache, resolved):
    out = service.visits(_caller(), [OPP.id], [f"v{OPP.id}-2", "nope"])
    assert [v["visit_id"] for v in out["visits"]] == [f"v{OPP.id}-2"]
    assert out["not_found"] == ["nope"]
    event = AuditEvent.objects.filter(resource_type=service.VISITS_RESOURCE_TYPE).latest("id")
    assert event.opportunity_id == OPP.id and event.record_count == 1


# --- a workflow's recipe over visits -------------------------------------------

SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "filters": {"status": ["approved"]},
    "fields": [
        {"name": "visits", "path": "form.weight", "aggregation": "count"},
        {
            "name": "deaths",
            "paths": ["form.case_id", "form.case.@case_id"],
            "aggregation": "count_distinct",
            "filter_path": "form.child_alive",
            "filter_value": "no",
        },
        {"name": "weight", "path": "form.weight", "aggregation": "avg", "transform": "float"},
    ],
}


def test_workflow_context_hands_over_a_visits_query_that_runs(cache, resolved):
    from connect_labs.workflow.data_access import PipelineDataAccess

    definition = SimpleNamespace(
        id=77,
        name="KMC review",
        template_type="kmc_flw_flags",
        registry_source={"registry_id": 5},
        opportunity_ids=[OPP.id],
        data={"config": {"agent": {"share": True, "agent": "data", "brief": "Deaths are counted per case."}}},
        pipeline_sources=[{"pipeline_id": 501, "alias": "flw_flags"}],
    )
    pipeline = SimpleNamespace(name="FLW flags", schema=SCHEMA)
    real_to_config = PipelineDataAccess._schema_to_config

    class FakeWDA:
        def __init__(self, **kw):
            pass

        def get_definition(self, _id):
            return definition

        def close(self):
            pass

    class FakePDA:
        def __init__(self, **kw):
            pass

        def get_definition(self, _id):
            return pipeline

        def _schema_to_config(self, schema, pipeline_id):
            return real_to_config(self, schema, pipeline_id)

        def close(self):
            pass

    sibling = Opportunity(id=OTHER.id, name=OTHER.name, llo=OTHER.llo, program_id=7, program_name="KMC")
    with (
        mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess", FakeWDA),
        mock.patch("connect_labs.workflow.data_access.PipelineDataAccess", FakePDA),
        mock.patch.object(scope, "left_out_of", return_value=[sibling]) as left_out,
    ):
        out = workflow_context.workflow_context(_caller(), 77, opportunity_id=OPP.id)

    left_out.assert_called_once_with(mock.ANY, [OPP.id])
    assert out["programme_opportunities_not_in_workflow"] == [
        {"id": OTHER.id, "name": OTHER.name, "llo": OTHER.llo, "program": "KMC"}
    ]
    assert "programme_opportunities_not_in_workflow" in out["how_to_verify"]

    assert out["workflow"]["registry_source"] == {"registry_id": 5}
    assert out["workflow"]["brief"] == "Deaths are counted per case."
    assert out["opportunities"] == [{"id": OPP.id, "name": OPP.name, "llo": OPP.llo}]
    flags = out["pipelines"][0]
    assert flags["alias"] == "flw_flags" and flags["grouping_key"] == "username"
    assert [f["name"] for f in flags["fields"]] == ["visits", "deaths", "weight"]
    assert "visits_sql_error" not in flags, flags.get("visits_sql_error")
    rows = run_query([OPP], flags["visits_sql"]).rows
    # The pipeline's own status filter is kept: only approved visits.
    assert len(rows) == 4


def test_workflow_context_needs_exactly_one_scope():
    with pytest.raises(scope.ScopeError):
        workflow_context.workflow_context(_caller(), 1)
    with pytest.raises(scope.ScopeError):
        workflow_context.workflow_context(_caller(), 1, opportunity_id=1, program_id=2)


# --- a programme's opportunities a workflow leaves out (Scout #922) ----------


def _opp(oid, program):
    return Opportunity(id=oid, name=f"Opp {oid}", llo="LLO", program_id=program, program_name=f"P{program}")


def test_left_out_of_names_the_programmes_other_held_opportunities():
    seen = {o.id: o for o in [_opp(1, 7), _opp(2, 7), _opp(3, 7), _opp(4, 8), _opp(5, None)]}
    with (
        mock.patch.object(scope, "directory", return_value=seen),
        # 3 is in the programme but not held: never named.
        mock.patch.object(scopes, "opportunity_ids", return_value={1, 2, 4, 5}),
    ):
        assert [o.id for o in scope.left_out_of(_caller(), [1])] == [2]
        assert scope.left_out_of(_caller(), [1, 2]) == []
        # An opportunity with no programme has no siblings to miss.
        assert scope.left_out_of(_caller(), [5]) == []


def test_left_out_of_says_nothing_when_holdings_are_unknowable():
    seen = {o.id: o for o in [_opp(1, 7), _opp(2, 7)]}
    with (
        mock.patch.object(scope, "directory", return_value=seen),
        mock.patch.object(scopes, "opportunity_ids", side_effect=scopes.ScopesUnavailable("down")),
    ):
        assert scope.left_out_of(_caller(), [1]) == []


def test_directory_reads_each_opportunitys_programme():
    tree = {
        "organizations": [{"slug": "llo-a", "name": "LLO A"}],
        "programs": [{"id": 7, "name": "KMC"}],
        "opportunities": [
            {"id": 1, "name": "Opp 1", "organization": "llo-a", "program": 7},
            {"id": 2, "name": "Opp 2", "organization": "llo-a", "program": 9, "program_name": "Own name"},
            {"id": 3, "name": "Opp 3", "organization": "llo-a"},
        ],
    }
    with mock.patch.object(scope, "_org_tree", return_value=tree):
        seen = scope.directory(_caller())
    assert (seen[1].program_id, seen[1].program_name, seen[1].llo) == (7, "KMC", "LLO A")
    assert (seen[2].program_id, seen[2].program_name) == (9, "Own name")
    assert (seen[3].program_id, seen[3].program_name) == (None, "")


def test_a_cut_off_result_points_the_agent_at_the_full_result_on_the_page():
    shaped = service.shape_for_agent({"rows": [[1]], "row_count": 1, "truncated": True, "open_on_page": "/x"})
    assert any("open_on_page" in n and "CSV" in n for n in shaped["notes"])
