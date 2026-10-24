"""Choosing what a query reads: programs, opportunities, a workflow and its pipelines.

What these pin is the containment: a program is only the opportunities of it the
person holds; a workflow is refused whole if it spans one they do not; its pipeline
relations hold only that pipeline's current rows for those opportunities -- and the
database grants nothing else, even to SQL that names the cache directly.

Fixture data is invented (no real visits, no names): this repo is public.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from connect_labs.labs.access import scopes
from connect_labs.labs.analysis.backends.sql.models import ComputedFLWCache, ComputedVisitCache
from connect_labs.labsdata import engine, pipelines, scope, spaces
from connect_labs.labsdata.engine import QueryError, run_query
from connect_labs.labsdata.pipelines import PipelineRelation
from connect_labs.labsdata.scope import Opportunity, ScopeError

pytestmark = pytest.mark.django_db

HELD = Opportunity(id=901, name="Opp A", llo="LLO One", program_id=40)
ALSO_HELD = Opportunity(id=902, name="Opp B", llo="LLO Two", program_id=40)
OTHER_PROGRAM = Opportunity(id=904, name="Opp D", llo="LLO One", program_id=41)
NOT_HELD = 903

FLW = PipelineRelation(
    name="p_flw_summary",
    alias="flw_summary",
    pipeline_id=77,
    pipeline_name="FLW summary",
    stage="aggregated",
    config_hash="current",
    field_names=("score", "met_target", "district"),
)
PER_VISIT = PipelineRelation(
    name="p_visits",
    alias="visits",
    pipeline_id=78,
    pipeline_name="Per visit",
    stage="visit_level",
    config_hash="current",
    field_names=("muac",),
)


def _flw(opp, username, score, *, config_hash="current", pipeline_id=77, expired=False, visit_count=10):
    return ComputedFLWCache(
        opportunity_id=opp,
        pipeline_id=pipeline_id,
        config_hash=config_hash,
        visit_count=visit_count,
        expires_at=timezone.now() + timedelta(hours=-1 if expired else 1),
        username=username,
        aggregated_fields={"score": score, "met_target": score >= 3, "district": f"d{score % 2}"},
        total_visits=score * 2,
    )


@pytest.fixture
def stored():
    ComputedFLWCache.objects.bulk_create(
        [_flw(901, f"w{i}", i) for i in range(1, 5)]
        + [_flw(902, f"w{i}", i + 10) for i in range(1, 3)]
        # Another opportunity's rows, an old configuration, expired rows, another pipeline,
        # and an older generation: none of them may be served.
        + [_flw(NOT_HELD, f"w{i}", 99) for i in range(1, 6)]
        + [_flw(901, f"old{i}", 50, config_hash="before-edit") for i in range(3)]
        + [_flw(901, f"gone{i}", 60, expired=True) for i in range(3)]
        + [_flw(901, f"other{i}", 70, pipeline_id=4242) for i in range(3)]
        + [_flw(901, f"stale{i}", 80, visit_count=5) for i in range(3)]
    )
    ComputedVisitCache.objects.bulk_create(
        [
            ComputedVisitCache(
                opportunity_id=901,
                pipeline_id=78,
                config_hash="current",
                visit_count=10,
                expires_at=timezone.now() + timedelta(hours=1),
                visit_id=f"v{i}",
                username="w1",
                visit_date=date(2026, 9, 1) + timedelta(days=i),
                computed_fields={"muac": 11.5 + i},
            )
            for i in range(3)
        ]
    )


def test_a_pipeline_relation_is_its_current_rows_for_the_chosen_opportunities(stored):
    result = run_query(
        [HELD, ALSO_HELD], "SELECT opportunity_id, username, score FROM p_flw_summary ORDER BY 1, 2", relations=[FLW]
    )
    assert result.rows == [
        [901, "w1", 1],
        [901, "w2", 2],
        [901, "w3", 3],
        [901, "w4", 4],
        [902, "w1", 11],
        [902, "w2", 12],
    ]


def test_pipeline_fields_become_typed_columns(stored):
    # Numbers compare as numbers and booleans filter as booleans, with no casts.
    sql = "SELECT COUNT(*), SUM(score) FROM p_flw_summary WHERE score > 2 AND met_target AND district = 'd1'"
    assert run_query([HELD], sql, relations=[FLW]).rows == [[1, 3]]


def test_a_pipeline_joins_visits_and_other_pipelines(stored):
    sql = (
        "SELECT f.username, COUNT(v.visit_id) FROM p_flw_summary f "
        "JOIN p_visits v USING (opportunity_id, username) GROUP BY 1"
    )
    assert run_query([HELD], sql, relations=[FLW, PER_VISIT]).rows == [["w1", 3]]


def test_a_pipeline_name_the_selection_does_not_carry_is_refused(stored):
    with pytest.raises(QueryError) as e:
        run_query([HELD], "SELECT * FROM p_flw_summary")
    assert e.value.code == "table_not_allowed"


def test_past_the_validator_the_database_grants_only_the_selected_pipeline_rows(stored, monkeypatch):
    from connect_labs.labsdata.validator import ExplorerValidator

    monkeypatch.setattr(ExplorerValidator, "_validate_table_access", lambda self, statement, sql: None)
    sql = "SELECT opportunity_id, pipeline_id, COUNT(*) FROM public.labs_computed_flw_cache GROUP BY 1, 2"
    # Every stored row of pipeline 77 on 901 (4 current, and old, expired and superseded
    # ones the relation itself never serves) -- but not 903's,
    # and not pipeline 4242's: the grant is (opportunity, pipeline), nothing wider.
    assert run_query([HELD], sql, relations=[FLW]).rows == [[901, 77, 13]]
    # Without the workflow chosen, no pipeline rows at all.
    assert run_query([HELD], sql).rows == []


def test_status_says_which_opportunities_have_no_current_results(stored):
    assert engine.pipeline_status([HELD, ALSO_HELD], [FLW, PER_VISIT]) == {
        "p_flw_summary": {901: 4, 902: 2},
        "p_visits": {901: 3, 902: 0},
    }


def test_validation_sees_the_real_columns_but_no_rows(stored):
    out = engine.validate_query("SELECT username, score FROM p_flw_summary", relations=[FLW], field_opps=[HELD])
    assert out["columns"] == ["username", "score"]


def test_relation_names_are_plain_identifiers():
    assert pipelines.relation_name("FLW Summary (v2)", 5) == "p_flw_summary_v2"
    assert pipelines.relation_name(None, 5) == "p_5"


# --- programs ---------------------------------------------------------------


def _directory():
    return {o.id: o for o in (HELD, ALSO_HELD, OTHER_PROGRAM)}


def _holding(opps, programs):
    return (
        mock.patch.object(scopes, "opportunity_ids", return_value=set(opps)),
        mock.patch.object(scopes, "program_ids", return_value=set(programs)),
        mock.patch.object(scope, "directory", return_value=_directory()),
    )


def test_a_program_is_the_opportunities_of_it_the_person_holds():
    a, b, c = _holding({901, 904}, {40, 41})
    with a, b, c:
        opps, programs = scope.expand(scopes.Caller(access_token="t"), [904], [40])
    # 902 is in program 40 but not held: it is not read, and not refused either.
    assert [o.id for o in opps] == [901, 904]
    assert programs == [{"id": 40, "name": "Program 40", "opportunity_ids": [901]}]


def test_a_program_the_person_does_not_hold_is_refused():
    a, b, c = _holding({901}, {40})
    with a, b, c, pytest.raises(ScopeError) as e:
        scope.expand(scopes.Caller(access_token="t"), [], [41])
    assert e.value.code == "not_held" and "41" in e.value.message


# --- workflows --------------------------------------------------------------


def _workflow(opportunity_ids, sources):
    return SimpleNamespace(id=12, name="Weekly review", pipeline_sources=sources, opportunity_ids=opportunity_ids)


def _read_pipeline(source, token, owner):
    pid = source["pipeline_id"]
    entry = {"alias": source["alias"], "pipeline_id": pid, "name": f"Pipeline {pid}"}
    if pid == 3:
        return {**entry, "error": "pipeline not readable by this person"}, None
    config = SimpleNamespace(
        terminal_stage=SimpleNamespace(value="aggregated"),
        fields=[SimpleNamespace(name="score")],
        histograms=[],
    )
    return {**entry, "visits_source": pid != 2}, config


def _choose(opportunity_ids, held):
    sources = [
        {"pipeline_id": 1, "alias": "flw summary"},
        {"pipeline_id": 2, "alias": "cases"},
        {"pipeline_id": 3, "alias": "hidden"},
    ]
    owner = {"opportunity_id": 901}
    with (
        mock.patch.object(
            pipelines, "read_workflow", return_value=(_workflow(opportunity_ids, sources), "t", owner, opportunity_ids)
        ),
        mock.patch.object(pipelines, "read_pipeline", side_effect=_read_pipeline),
        mock.patch.object(scopes, "opportunity_ids", return_value=held),
        mock.patch.object(scope, "directory", return_value=_directory()),
        mock.patch("connect_labs.labs.analysis.utils.get_config_hash", return_value="h"),
    ):
        return pipelines.workflow_selection(scopes.Caller(access_token="t"), 12, opportunity_id=901)


def test_a_workflow_brings_its_opportunities_and_its_visit_pipelines():
    chosen = _choose([901, 902], {901, 902})
    assert [o.id for o in chosen.opportunities] == [901, 902]
    assert [(r.name, r.pipeline_id, r.stage) for r in chosen.relations] == [("p_flw_summary", 1, "aggregated")]
    # Another source's pipeline is listed, not offered; one the person cannot read neither.
    assert {n["pipeline_id"]: n["reason"][:20] for n in chosen.not_offered} == {
        2: "computed from anothe",
        3: "pipeline not readabl",
    }


def test_a_workflow_spanning_an_opportunity_the_person_does_not_hold_is_refused_whole():
    with pytest.raises(ScopeError) as e:
        _choose([901, NOT_HELD], {901})
    assert e.value.code == "not_held"


def test_a_workflow_is_chosen_instead_of_opportunities_not_beside_them():
    with pytest.raises(ScopeError) as e:
        spaces.generate(scopes.Caller(access_token="t"), [901], workflow={"id": 12, "opportunity_id": 901})
    assert e.value.code == "bad_scope"


# --- the "no user visit data" gate reads the whole selection ----------------


def test_the_restricted_gate_resolves_programs_and_workflows_not_only_opportunity_ids():
    from connect_labs.mcp import visit_access

    resolve = visit_access.RESOLVERS["data_query"]
    with (
        mock.patch.object(
            visit_access, "_program_opportunity_ids", side_effect=lambda p: [10501, 10502] if p == 10500 else None
        ),
        mock.patch.object(
            visit_access, "_workflow", return_value=lambda user, args: [10601] if args.get("id") == 6 else None
        ),
    ):
        assert resolve(None, {"opportunity_ids": [10001]}) == [10001]
        assert resolve(None, {"opportunity_ids": [10001], "program_ids": [10500]}) == [10001, 10501, 10502]
        assert resolve(None, {"workflow": {"id": 6, "opportunity_id": 10601}}) == [10601]
        # A synthetic opportunity beside a production program, or a workflow the gate
        # cannot resolve, refuses the whole call -- it would otherwise read production data.
        assert resolve(None, {"opportunity_ids": [10001], "program_ids": [40]}) is None
        assert resolve(None, {"opportunity_ids": [10001], "workflow": {"id": 7, "opportunity_id": 2166}}) is None
        assert resolve(None, {"opportunity_ids": [10001], "workflow": "6"}) is None
    for name in ("data_describe", "explorer_describe", "explorer_query"):
        assert visit_access.RESOLVERS[name] is resolve
