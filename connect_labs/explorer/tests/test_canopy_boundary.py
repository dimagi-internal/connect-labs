"""The canopy boundary: an agent calling through canopy never receives real visit data.

It may get form STRUCTURE, semantic-registry definitions, validation over zero rows,
and full results on SYNTHETIC opportunities; it hands SQL to the page, where the
person runs it. A person's own call (the page, their MCP sign-in) is unaffected.
"""

from datetime import date, timedelta
from unittest import mock

import pytest
from django.utils import timezone

from connect_labs.explorer import guidance, scope, service
from connect_labs.explorer.engine import QueryError, validate_query
from connect_labs.explorer.scope import Opportunity
from connect_labs.labs.access import scopes
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT
from connect_labs.mcp.tool_registry import MCPToolError

pytestmark = pytest.mark.django_db

REAL = Opportunity(id=921, name="Real opp", llo="LLO R")


@pytest.fixture
def real_visits():
    RawVisitCache.objects.bulk_create(
        [
            RawVisitCache(
                opportunity_id=REAL.id,
                pipeline_id=USER_VISITS_RAW_SLOT,
                visit_count=30,
                expires_at=timezone.now() + timedelta(hours=1),
                visit_id=f"r{n}",
                username="flw",
                entity_id=f"c{n}",
                visit_date=date(2026, 9, 1),
                form_json={"form": {"birth": {"place": ["hospital", "home"][n % 2]}}},
            )
            for n in range(30)
        ]
    )


def _caller():
    return scopes.Caller(access_token="t")


@pytest.fixture
def resolved():
    with mock.patch.object(scope, "resolve", return_value=[REAL]):
        yield


def test_agent_query_on_real_data_is_refused(real_visits, resolved):
    with pytest.raises(service.RealDataRefused):
        service.query(_caller(), [REAL.id], "SELECT COUNT(*) FROM visits", for_agent=True)


def test_agent_query_on_synthetic_data_runs(real_visits, resolved):
    with mock.patch.object(service, "all_generated", return_value=True):
        out = service.query(_caller(), [REAL.id], "SELECT COUNT(*) FROM visits", for_agent=True)
    assert out["rows"] == [[30]]


def test_the_person_still_gets_real_results(real_visits, resolved):
    assert service.query(_caller(), [REAL.id], "SELECT COUNT(*) FROM visits")["rows"] == [[30]]


def test_agent_describe_on_real_data_is_structure_only(real_visits, resolved):
    with mock.patch.object(service, "related_registries", return_value=[]):
        out = service.describe(_caller(), [REAL.id], for_agent=True)
    place = next(f for f in out["fields"] if f["path"] == "form.birth.place")
    assert set(place) >= {"path", "sql", "type"}
    assert not {"values", "filled_pct", "distinct_in_sample"} & set(place)
    assert out["opportunities"] == [{"opportunity_id": REAL.id, "opportunity_name": "Real opp", "llo": "LLO R"}]
    assert out["sampled_visits"] is None
    assert out["real_data_visible_to_you"] is False
    assert out["answering_rules"] == guidance.AGENT_RULES


def test_agent_describe_does_not_load_real_data(real_visits, resolved):
    with mock.patch.object(service, "warm") as warm, mock.patch.object(service, "related_registries", return_value=[]):
        service.describe(_caller(), [REAL.id], load_missing=True, for_agent=True)
    warm.assert_not_called()


def test_the_person_describe_keeps_values(real_visits, resolved):
    with mock.patch.object(service, "related_registries", return_value=[]):
        out = service.describe(_caller(), [REAL.id])
    place = next(f for f in out["fields"] if f["path"] == "form.birth.place")
    assert {v["value"] for v in place["values"]} == {"hospital", "home"}


def test_validate_runs_over_zero_rows(real_visits):
    ok = validate_query("SELECT form_json #>> '{form,birth,place}' AS place, COUNT(*) AS n FROM visits GROUP BY 1")
    assert ok == {"valid": True, "columns": ["place", "n"], "sql_executed": ok["sql_executed"]}
    with pytest.raises(QueryError) as e:
        validate_query("SELECT nope FROM visits")
    assert e.value.code == "invalid_sql"
    with pytest.raises(QueryError):
        validate_query("SELECT * FROM labs_raw_visit_cache")


def test_validate_returns_no_rows_even_for_an_aggregate(real_visits):
    # COUNT(*) over the empty relation is 0; validate returns columns only, never rows.
    out = validate_query("SELECT COUNT(*) AS n FROM visits")
    assert "rows" not in out


def test_mcp_tools_apply_the_boundary_for_canopy_calls(real_visits, resolved):
    from connect_labs.mcp.tools import explorer as tools

    user = mock.Mock(username="visitor")
    with (
        mock.patch.object(tools, "_caller", return_value=_caller()),
        mock.patch.object(tools, "_from_canopy", return_value=True),
        mock.patch.object(service, "related_registries", return_value=[]),
    ):
        with pytest.raises(MCPToolError) as e:
            tools.explorer_query(user, [REAL.id], "SELECT COUNT(*) FROM visits")
        described = tools.explorer_describe(user, [REAL.id])
    assert e.value.code == "PERMISSION_DENIED"
    assert all("values" not in f for f in described["fields"])
    assert tools.explorer_validate(user, "SELECT 1 AS one FROM visits")["valid"] is True
    assert tools.explorer_validate(user, "DELETE FROM visits")["valid"] is False


def test_a_persons_own_mcp_call_is_not_canopy():
    from connect_labs.mcp.tools import explorer as tools

    assert tools._from_canopy() is False  # no delegated token in this context


def test_validate_is_reachable_and_scoped():
    from connect_labs.labs.canopy import SCOPE_TOOLS
    from connect_labs.mcp import token_scopes

    assert "explorer_validate" in SCOPE_TOOLS["explorer:read"]
    assert "explorer_validate" in token_scopes.NO_USERVISIT_DATA_TOOLS
    assert "explorer_validate" not in token_scopes.USERVISIT_DATA_TOOLS
