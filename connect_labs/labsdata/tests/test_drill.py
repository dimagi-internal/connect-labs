"""Clicking a cell: the SQL for the rows behind it, and that they add up to the cell.

The property that matters is the last test's: for every cell that counts rows, the
drilled SQL selects exactly that many. Fixture data is invented: this repo is public.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest import mock

import pytest
from django.utils import timezone

from connect_labs.labs.access.scopes import Caller
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT
from connect_labs.labsdata import drill, scope, service, spaces
from connect_labs.labsdata.engine import QueryError
from connect_labs.labsdata.scope import Opportunity

OPP = Opportunity(id=921, name="Opp D", llo="LLO D")

REPORT = """SELECT form_json->'form'->>'@name' AS fname, COUNT(*) AS visits,
  COUNT(*) FILTER (WHERE form_json #>> '{form,discharge}' IS NOT NULL) AS with_dis,
  COUNT(*) FILTER (WHERE (form_json #>> '{form,discharge}')::date = visit_date) AS dis_eq_visit,
  COUNT(form_json #>> '{form,discharge}') AS dis_answered,
  SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
  MIN(visit_date) AS first_visit,
  ROUND(100.0 * COUNT(*) FILTER (WHERE flagged) / COUNT(*), 1) AS pct_flagged
FROM visits WHERE status <> 'rejected' OR flagged GROUP BY 1 ORDER BY 2 DESC"""


def test_each_column_says_what_it_is():
    assert drill.shape(REPORT).kinds == ["group"] + ["aggregate"] * 7


def test_a_filtered_count_drills_to_its_filter_inside_the_group_and_the_where():
    found = drill.drill(REPORT, ["Registration", 5, 4, 3, 4, 2, "2026-09-01", 20.0], 3)
    assert found.kind == drill.COUNTING
    assert found.sql == (
        "SELECT *\nFROM visits\n"
        "WHERE (status <> 'rejected' OR flagged)\n"
        "  AND form_json -> 'form' ->> '@name' = 'Registration'\n"
        "  AND CAST((form_json #>> '{form,discharge}') AS DATE) = visit_date"
    )


def test_a_null_group_is_is_null_and_a_sum_of_values_is_its_rows():
    sql = "SELECT username, SUM(completed_work_id) AS s FROM visits GROUP BY username"
    found = drill.drill(sql, [None, 9], 1)
    assert found.kind == "group"
    assert found.sql.endswith("WHERE username IS NULL\n  AND completed_work_id IS NOT NULL")


def test_a_ratio_shows_its_group():
    found = drill.drill(REPORT, ["Registration", 5, 4, 3, 4, 2, "2026-09-01", 20.0], 7)
    assert found.kind == "group" and found.sql.count("AND") == 1


@pytest.mark.parametrize(
    "sql, reason",
    [
        ("SELECT * FROM visits", "already the rows"),
        ("SELECT visit_id, username FROM visits", "already one row"),
        ("SELECT a.username, COUNT(*) FROM visits a JOIN visits b USING (visit_id) GROUP BY 1", "JOIN"),
        ("WITH v AS (SELECT * FROM visits) SELECT username, COUNT(*) FROM v GROUP BY 1", "WITH"),
        ("SELECT username, COUNT(*) OVER () FROM visits", "window"),
        ("SELECT COUNT(*) FROM (SELECT username FROM visits) s", "one table"),
        ("SELECT COUNT(*) FROM visits GROUP BY username", "does not show its group"),
    ],
)
def test_shapes_it_is_not_sure_of_are_refused_with_a_reason(sql, reason):
    with pytest.raises(drill.NotDrillable) as e:
        drill.shape(sql)
    assert reason in str(e.value)


@pytest.fixture
def visits(db):
    rows = []
    for n in range(30):
        form = {"@name": "Registration" if n % 3 else "Follow-up"}
        if n % 4:
            form["discharge"] = str(date(2026, 9, 1) + timedelta(days=n % 5))
        rows.append(
            RawVisitCache(
                opportunity_id=OPP.id,
                pipeline_id=USER_VISITS_RAW_SLOT,
                visit_count=30,
                expires_at=timezone.now() + timedelta(hours=1),
                visit_id=f"d{n}",
                username=f"w{n % 2}",
                visit_date=date(2026, 9, 1) + timedelta(days=n % 7),
                status=["approved", "pending", "rejected"][n % 3 if n % 5 else 0],
                flagged=n % 6 == 0,
                form_json={"form": form},
            )
        )
    RawVisitCache.objects.bulk_create(rows)


def test_every_counting_cell_drills_to_exactly_that_many_rows(visits):
    caller = Caller(access_token="t")
    with mock.patch.object(scope, "resolve", return_value=[OPP]):
        result = service.query(caller, spaces.generate(caller, [OPP.id]), REPORT)
        assert result["drill"]["columns"] == [True] * 8
        checked = 0
        for row in result["rows"]:
            for column, kind in enumerate(result["drill"]["kinds"]):
                if kind != "aggregate":
                    continue
                out = service.drill_into(caller, spaces.generate(caller, [OPP.id]), REPORT, row, column)
                if out["kind"] == drill.COUNTING:
                    assert out["matches"] is True, (result["columns"][column], row, out)
                    checked += 1
        with pytest.raises(QueryError) as e:
            service.drill_into(caller, spaces.generate(caller, [OPP.id]), "SELECT * FROM visits", ["x"], 0)
    assert checked == 2 * 5  # two groups, five counting columns each
    assert e.value.code == "not_drillable"
