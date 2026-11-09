"""``microplans_coverage_check``: the tool reads households through the Data query path and never returns one."""

from unittest.mock import patch

import pandas as pd
import pytest

from connect_labs.labsdata import service
from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.tools import coverage_check as tool

SETTINGS = {"facilities": [{"id": "C1", "lon": 8.0, "lat": 13.5}], "catchment_k": 3, "max_dist_m": 30}
BUILDINGS = pd.DataFrame({"lon": [8.0, 8.0001, 8.0002, 8.5], "lat": [13.5, 13.5, 13.5, 13.9], "area_m2": [50.0] * 4})


class Query:
    """Answers the count, then each shard, the way the Data query path does."""

    def __init__(self, count, shards, truncated=False):
        self.count, self.shards, self.truncated, self.sqls = count, list(shards), truncated, []

    def __call__(self, caller, space, sql, max_rows=None, *, for_agent=False, shaped=True):
        self.sqls.append(sql)
        if sql.startswith("SELECT count("):
            return {"rows": [[self.count]], "truncated": False}
        return {"rows": self.shards.pop(0) if self.shards else [], "truncated": self.truncated}


@pytest.fixture
def wired():
    with (
        patch.object(tool, "_caller", lambda user: object()),
        patch.object(tool, "_space", lambda *a, **k: object()),
        patch.object(tool, "_from_canopy", lambda: False),
        patch.object(tool.check, "default_fetch", lambda filters: lambda lon, lat, r: BUILDINGS),
    ):
        yield


def test_it_reads_the_households_in_shards_and_returns_counts_only(wired):
    rows = [["C1", "hh1", "13.50005 8.00005 0.0 5.0", "contact_1"], ["C1", "hh2", None, "contact_1"]]
    query = Query(2, [rows])
    with patch.object(service, "query", query):
        out = tool.microplans_coverage_check(None, SETTINGS, opportunity_ids=[1])
    assert query.sqls[0].startswith("SELECT count(") and "BETWEEN '00' AND 'ff'" in query.sqls[1]
    (area,) = out["areas"]
    assert area["households"] == 2 and area["data_quality"]["no_point"] == 1
    assert area["cycles"][1]["households_visited"] == 2 and area["cycles"][1]["buildings_reached"] >= 1
    assert out["total"]["buildings"] == 3
    assert "hh1" not in str(out) and "13.50005" not in str(out)


def test_a_cut_read_is_an_error_not_a_smaller_answer(wired):
    with patch.object(service, "query", Query(5000, [[["C1", "hh", None, "c1"]]], truncated=True)):
        with pytest.raises(MCPToolError) as err:
            tool.microplans_coverage_check(None, SETTINGS, opportunity_ids=[1])
    assert err.value.code == "CAPACITY"


def test_bad_settings_are_an_invalid_schema_error(wired):
    with pytest.raises(MCPToolError) as err:
        tool.microplans_coverage_check(None, {"facilities": []}, opportunity_ids=[1])
    assert err.value.code == "INVALID_SCHEMA"


def test_an_agent_labs_has_not_cleared_gets_nothing_for_production_rows(wired):
    def refuse(*a, **k):
        raise service.SensitiveDataRefused()

    with patch.object(service, "query", refuse):
        out = tool.microplans_coverage_check(None, SETTINGS, opportunity_ids=[1])
    assert out["withheld"] is True and "areas" not in out
