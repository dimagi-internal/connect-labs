"""The engine on real Postgres: scoping, generations, the session lock, errors.

Fixture data is invented (no real visits, no names): this repo is public.
"""

from datetime import date, timedelta

import pytest
from django.db import connection
from django.utils import timezone

from connect_labs.explorer import engine
from connect_labs.explorer.engine import QueryError, run_query
from connect_labs.explorer.scope import Opportunity
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT

pytestmark = pytest.mark.django_db

HELD = Opportunity(id=901, name="Opp A", llo="LLO One")
ALSO_HELD = Opportunity(id=902, name="Opp B", llo="LLO Two")
NOT_HELD = 903


def _visit(opp, n, place, *, visit_count=10, pipeline_id=USER_VISITS_RAW_SLOT, status="approved"):
    return RawVisitCache(
        opportunity_id=opp,
        pipeline_id=pipeline_id,
        visit_count=visit_count,
        expires_at=timezone.now() + timedelta(hours=1),
        visit_id=f"{opp}-{visit_count}-{n}",
        username=f"flw{n % 3}",
        entity_id=f"case-{opp}-{n % 4}",
        visit_date=date(2026, 9, 1) + timedelta(days=n),
        status=status,
        form_json={"form": {"birth": {"place": place}, "weight": str(1500 + n)}},
    )


@pytest.fixture
def cache():
    rows = [_visit(901, i, "hospital" if i % 3 else "home") for i in range(9)]
    rows += [_visit(902, i, "home") for i in range(4)]
    rows += [_visit(NOT_HELD, i, "hospital") for i in range(7)]
    # A stale generation, an unfinalized writer, and another pipeline's slot for 901:
    # none of them may be served.
    rows += [_visit(901, i, "stale", visit_count=5) for i in range(20)]
    rows += [_visit(901, i, "pending", visit_count=-77) for i in range(20)]
    rows += [_visit(901, i, "other-slot", pipeline_id=4242) for i in range(20)]
    RawVisitCache.objects.bulk_create(rows)


def _scalar(opps, sql):
    return run_query(opps, sql).rows[0][0]


def test_reads_only_the_requested_opportunities(cache):
    assert _scalar([HELD], "SELECT COUNT(*) FROM visits") == 9
    assert _scalar([HELD, ALSO_HELD], "SELECT COUNT(*) FROM visits") == 13
    assert _scalar([HELD], f"SELECT COUNT(*) FROM visits WHERE opportunity_id = {NOT_HELD}") == 0


def test_one_finalized_generation_and_one_slot(cache):
    places = {r[0] for r in run_query([HELD], "SELECT DISTINCT form_json #>> '{form,birth,place}' FROM visits").rows}
    assert places == {"hospital", "home"}


def test_hospital_vs_home_by_llo(cache):
    result = run_query(
        [HELD, ALSO_HELD],
        """SELECT llo, form_json #>> '{form,birth,place}' AS place, COUNT(DISTINCT entity_id) AS cases
           FROM visits GROUP BY 1, 2 ORDER BY 1, 2""",
    )
    assert result.columns == ["llo", "place", "cases"]
    # 901: home on visits 0,3,6 (cases 0,3,2); hospital on the other six (cases 0-3).
    assert result.rows == [["LLO One", "home", 3], ["LLO One", "hospital", 4], ["LLO Two", "home", 4]]


def test_percent_literals_survive_the_driver(cache):
    assert _scalar([HELD], "SELECT COUNT(*) FROM visits WHERE username LIKE '%lw1%'") == 3


def test_user_with_clause_and_order_by_survive_wrapping(cache):
    result = run_query(
        [HELD],
        "WITH w AS (SELECT (form_json #>> '{form,weight}')::int AS g FROM visits) "
        "SELECT g FROM w ORDER BY g DESC LIMIT 2",
    )
    assert result.rows == [[1508], [1507]]


def test_truncation_reported(cache):
    result = run_query([HELD], "SELECT visit_id FROM visits", max_rows=5)
    assert result.row_count == 5 and result.truncated


def test_validator_refusal_is_a_query_error(cache):
    with pytest.raises(QueryError) as e:
        run_query([HELD], "SELECT * FROM labs_raw_visit_cache")
    assert e.value.code == "table_not_allowed"


def test_session_lock_is_undone_after_the_query(cache):
    settings = ("search_path", "transaction_read_only", "statement_timeout")

    def read():
        with connection.cursor() as c:
            out = {}
            for name in settings:
                c.execute(f"SHOW {name}")
                out[name] = c.fetchone()[0]
            return out

    before = read()
    assert before["transaction_read_only"] == "off"
    run_query([HELD], "SELECT COUNT(*) FROM visits")
    # Compared to what was there, not to a spelling of 20s: SHOW prints "20s".
    assert read() == before


def test_a_table_the_validator_missed_still_does_not_resolve(cache, monkeypatch):
    # Defence in depth: switch the relation allow-list OFF and name the real cache
    # table. The empty search_path means it does not resolve, so the query fails
    # instead of reading every opportunity's visits.
    from connect_labs.explorer.validator import ExplorerValidator

    monkeypatch.setattr(ExplorerValidator, "_validate_table_access", lambda self, statement, sql: None)
    with pytest.raises(QueryError) as e:
        run_query([HELD], "SELECT COUNT(*) FROM labs_raw_visit_cache")
    assert e.value.code == "invalid_sql"
    assert "does not exist" in e.value.message


def test_timeout(cache, monkeypatch):
    monkeypatch.setattr(engine, "STATEMENT_TIMEOUT_MS", 50)
    with pytest.raises(QueryError) as e:
        run_query([HELD], "SELECT COUNT(*) FROM visits, generate_series(1, 50000000)")
    assert e.value.code == "timeout"


def test_bad_sql_is_reported_not_raised_raw(cache):
    with pytest.raises(QueryError) as e:
        run_query([HELD], "SELECT no_such_column FROM visits")
    assert e.value.code == "invalid_sql"


def test_no_opportunities_refused():
    with pytest.raises(QueryError):
        run_query([], "SELECT 1 FROM visits")
