"""What the results card gets beyond the rows: the agent's ``display`` (labels, formats,
chart), checked strictly and kept in the kept result; provenance (the SQL laid out,
each opportunity's freshness, a Data-page link that reproduces the query); and the
column statistics an agent gets, under the same rule as its sample.

THIS REPOSITORY IS PUBLIC. Every name and value here is invented.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model

from connect_labs.labs import canopy_panel
from connect_labs.labsdata import display, engine, fields, scope, service
from connect_labs.labsdata.scope import Opportunity, ScopeError
from connect_labs.mcp import kept_results
from connect_labs.mcp.models import KeptResult as Row
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool

pytestmark = pytest.mark.django_db

OPP = Opportunity(id=974, name="Opp Card", llo="LLO Card")
COLUMNS = ["worker_name", "delivery_place", "visits", "approval_rate", "visit_date"]
PLACES = ["home", "clinic", "hospital"]
ROWS = [
    [f"Invented Person {i:03d}", PLACES[i % 3], i % 9, round((i % 10) / 10, 2), f"2026-0{1 + i % 9}-1{i % 10}"]
    for i in range(60)
]
CACHED_AT = "2026-10-23T09:00:00+00:00"


@pytest.fixture
def owner():
    return get_user_model().objects.create_user(username="card-owner", password="x")


@pytest.fixture
def connect(monkeypatch, owner):
    seen = {}

    def resolve(caller, ids):
        if caller.user is not None and caller.user.pk != owner.pk:
            raise ScopeError("not_held", f"you do not have access to opportunity {OPP.id}")
        return [OPP]

    def run_query(opps, sql, max_rows=None, **kw):
        seen["max_rows"] = max_rows
        return engine.QueryResult(COLUMNS, [list(r) for r in ROWS], len(ROWS), False, sql, 4)

    monkeypatch.setattr(scope, "resolve", resolve)
    monkeypatch.setattr("connect_labs.mcp.tools.labsdata.require_connect_token", lambda user: "tok")
    monkeypatch.setattr("connect_labs.labs.connect_tokens.get_valid_access_token", lambda user: "tok")
    monkeypatch.setattr(engine, "run_query", run_query)
    monkeypatch.setattr(fields, "cached_at", lambda opps: {o.id: CACHED_AT for o in opps})
    monkeypatch.setattr("connect_labs.labs.synthetic.provenance.all_generated", lambda ids: False)
    return seen


def _call(user, **args):
    _, raw = MCPAccessToken.create_token(user, name="card")
    base = {"opportunity_ids": [OPP.id], "sql": "select worker_name, visits from visits where status <> 'x'"}
    return call_tool(raw, "data_query", {**base, **args})


def _agent(monkeypatch, cleared: bool):
    from connect_labs.mcp.tools import labsdata as tools

    monkeypatch.setattr(tools, "_from_canopy", lambda: True)
    monkeypatch.setattr(
        canopy_panel,
        "call_sensitive_data_clearance",
        lambda: canopy_panel.Clearance(cleared, "" if cleared else "this agent is not cleared for sensitive data"),
    )


GOOD = {
    "columns": {
        "visits": {"label": "Visits", "format": "number_0"},
        "approval_rate": {"label": "Approval‮ rate\n", "format": "percent_1"},
    },
    "chart": {"type": "bar", "x": "delivery_place", "y": "visits"},
}


# --- display ---------------------------------------------------------------------------


def test_a_display_is_kept_in_the_result_not_only_in_the_tool_input(owner, connect):
    out = _call(owner, keep_result=True, display=GOOD)["result"]["structuredContent"]
    kept = Row.objects.get().payload["display"]
    # Labels are plain text: a bidi override and a newline are dropped.
    assert kept["columns"]["approval_rate"] == {"label": "Approval rate", "format": "percent_1"}
    assert kept["chart"] == {"type": "bar", "x": "delivery_place", "y": ["visits"]}
    assert out["display"] == kept


@pytest.mark.parametrize(
    "bad, says",
    [
        ({"columns": {"nope": {"label": "x"}}}, "does not have: ['nope']"),
        ({"chart": {"type": "line", "x": "visit_date", "y": ["missing"]}}, "does not have: ['missing']"),
        ({"chart": {"type": "bar", "y": ["visits"]}}, "needs display.chart.x"),
        ({"chart": {"type": "stat", "y": ["visits", "approval_rate"]}}, "exactly one column"),
        ({"chart": {"type": "bar", "x": "visits", "y": ["visits"]}}, "cannot also be a series"),
    ],
)
def test_a_display_that_breaks_a_rule_is_refused_saying_why(owner, connect, bad, says):
    envelope = _call(owner, keep_result=True, display=bad)
    assert envelope["result"]["isError"] is True
    assert says in json.dumps(envelope)
    assert not Row.objects.exists()


@pytest.mark.parametrize(
    "bad",
    [
        {"columns": {"visits": {"format": "scientific_2"}}},
        {"columns": {"visits": {"format": "number_9"}}},
        {"columns": {"visits": {"label": "x", "color": "red"}}},
        {"chart": {"type": "pie", "x": "delivery_place"}},
        {"chart": {"type": "bar", "x": "a", "y": ["b", "c", "d", "e", "f", "g"]}},
        {"currency": "dollars"},
        {"script": "<b>"},
    ],
)
def test_the_validator_refuses_what_is_not_allowed(bad):
    with pytest.raises(display.DisplayError):
        display.validate(bad)


def test_a_bad_format_is_refused_before_the_query_runs(owner, connect):
    envelope = _call(owner, keep_result=True, display={"columns": {"visits": {"format": "number_7"}}})
    assert envelope["result"]["isError"] is True
    assert "max_rows" not in connect  # the query never ran (not even validated)


def test_labels_are_cut_and_cleaned():
    shown = display.validate({"columns": {"a": {"label": "  " + "x" * 90 + "\x00"}}})
    assert shown["columns"]["a"]["label"] == "x" * display.MAX_LABEL
    # A label that is nothing once cleaned is no label.
    assert display.validate({"columns": {"a": {"label": "\x01\x02"}}}) is None


# --- provenance ------------------------------------------------------------------------


def test_the_result_carries_formatted_sql_freshness_and_a_link_that_reproduces_it(owner, connect):
    _call(owner, keep_result=True, max_rows=120)
    payload = Row.objects.get().payload
    assert payload["sql_formatted"].startswith("SELECT\n  worker_name,")
    assert payload["sql_executed"] == "select worker_name, visits from visits where status <> 'x'"
    assert payload["opportunities"] == [{"id": OPP.id, "name": OPP.name, "llo": OPP.llo, "cached_at": CACHED_AT}]
    query = parse_qs(urlsplit(payload["open_on_page"]).query)
    assert query == {"opps": [str(OPP.id)], "sql": [payload["sql_executed"]], "max_rows": ["120"]}
    assert connect["max_rows"] == 120


def test_sql_that_cannot_be_laid_out_is_shown_as_written():
    assert service.format_sql("SELEKT ((") == "SELEKT (("
    assert service.format_sql("") == ""


def test_cached_at_says_when_each_opportunity_was_loaded(owner):
    # Nothing cached: no freshness to claim.
    assert fields.cached_at([OPP]) == {OPP.id: None}


# --- column statistics -----------------------------------------------------------------


def test_a_cleared_agent_gets_column_stats_without_any_name(owner, connect, monkeypatch):
    _agent(monkeypatch, cleared=True)
    facts = _call(owner)["result"]["structuredContent"]
    stats = {s["name"]: s for s in facts["column_stats"]}
    assert stats["visits"] == {
        "name": "visits",
        "type": "integer",
        "nulls": 0,
        "distinct": 9,
        "min": 0,
        "max": 8,
        "mean": pytest.approx(sum(r[2] for r in ROWS) / 60, abs=1e-4),
    }
    assert stats["visit_date"]["type"] == "date" and stats["visit_date"]["min"] == "2026-01-10"
    # A categorical column names its values, as data_describe does...
    assert {v["value"] for v in stats["delivery_place"]["top_values"]} == set(PLACES)
    # ...a column of names (every value distinct) names none, and no text column gets a min or max.
    assert stats["worker_name"] == {"name": "worker_name", "type": "text", "nulls": 0, "distinct": 60}
    blob = json.dumps(facts["column_stats"])
    assert "Invented Person" not in blob
    # The kept result itself carries no statistics: they are the agent's.
    assert "column_stats" not in Row.objects.get().payload


def test_an_agent_not_cleared_for_sensitive_data_gets_no_rows_and_no_stats(owner, connect, monkeypatch):
    _agent(monkeypatch, cleared=False)
    facts = _call(owner)["result"]["structuredContent"]
    assert facts["withheld"] is True
    assert "column_stats" not in facts and "sample_rows" not in facts
    assert "Invented Person" not in json.dumps(facts) and "hospital" not in json.dumps(facts)


def test_unrevealed_stats_carry_names_and_types_only():
    stats = kept_results.column_stats(COLUMNS, ROWS, reveal=False)
    assert stats == [{"name": c, "type": t} for c, t in zip(COLUMNS, ["text", "text", "integer", "numeric", "date"])]


def test_a_rare_category_is_not_named():
    rows = [["home"]] * 10 + [["Invented Clinic Of One"]]
    (stat,) = kept_results.column_stats(["place"], rows, reveal=True)
    assert "top_values" not in stat and stat["distinct"] == 2


def test_stats_stay_within_their_budget():
    wide = [f"col_{i}" for i in range(120)]
    rows = [[f"value {r} {c} " + "x" * 50 for c in range(120)] for r in range(1500)]
    stats = kept_results.column_stats(wide, rows, reveal=True)
    assert len(stats) == kept_results.STATS_MAX_COLUMNS
    assert all(s["distinct"] == kept_results.STATS_DISTINCT_CAP and s["distinct_capped"] for s in stats)
    assert len(json.dumps(stats)) < 8000


# --- the Data page link ----------------------------------------------------------------


def test_the_data_page_runs_a_card_link_only_when_asked(client, owner, monkeypatch):
    monkeypatch.setattr(scope, "directory", lambda caller: {OPP.id: OPP})
    monkeypatch.setattr(canopy_panel, "panel", lambda *a, **k: None)
    client.force_login(owner)
    page = client.get(
        "/labs/data/", {"opps": OPP.id, "sql": "SELECT 1", "max_rows": "120", "run": "1", "download": "1"}
    )
    assert page.context["default_max_rows"] == 120
    assert page.context["open_intent"] == {"run": True, "download": True, "save": False, "display": None}
    assert b'id="ex-open-intent"' in page.content
    plain = client.get("/labs/data/", {"opps": OPP.id, "sql": "SELECT 1", "max_rows": "999999"})
    assert plain.context["open_intent"]["run"] is False and plain.context["open_intent"]["download"] is False
    assert plain.context["default_max_rows"] == 5000


# --- sorting the whole result ------------------------------------------------------------

SORT_ROWS = [["b", 3], ["a", None], ["c", 10], ["d", 2], ["e", 10], ["f", None], ["g", 1]]


def _keep(owner, rows=SORT_ROWS, columns=("name", "n")):
    return kept_results.save(
        owner,
        kept_results.KIND_QUERY,
        {"columns": list(columns), "rows": rows, "sql_executed": "SELECT 1"},
        access={"opportunity_ids": [OPP.id], "sensitive": False},
        name="sort",
    )["uri"]


@pytest.mark.parametrize("db_side", [True, False])
def test_a_sort_orders_every_row_before_the_page_is_cut(owner, db_side):
    uri = _keep(owner)
    row = Row.objects.defer("payload").get() if db_side else Row.objects.get()

    def names(query):
        body, paging = kept_results.page_of(row, kept_results.parse(uri + query)[1])
        return [r[0] for r in body["rows"]], paging

    first, paging = names("?sort=n&dir=desc&page_size=3")
    assert first == ["c", "e", "b"]
    assert paging["sort"] == {"column": "n", "dir": "desc"} and paging["pages"] == 3
    # Page 3 is the tail of the WHOLE result, nulls last whichever way it is sorted.
    assert names("?sort=n&dir=desc&page_size=3&page=3")[0] == ["f"]
    assert names("?sort=n&page_size=10")[0] == ["g", "d", "b", "c", "e", "a", "f"]
    assert names("?sort=name&dir=desc&page_size=2")[0] == ["g", "f"]
    # No sort: the order the tool gave.
    assert names("?page_size=3")[0] == ["b", "a", "c"]


@pytest.mark.parametrize("query", ["?sort=nope", "?sort=n&dir=sideways", "?dir=desc", "?sort=n%3B%20DROP%20TABLE%20x"])
def test_a_sort_must_name_a_column_and_a_direction(owner, query):
    uri = _keep(owner)
    row = Row.objects.defer("payload").get()
    with pytest.raises(kept_results.Refused) as refused:
        kept_results.page_of(row, kept_results.parse(uri + query)[1])
    assert refused.value.code == "bad_uri"


@pytest.mark.django_db(transaction=True)
def test_resources_read_sorts_and_refuses_a_bad_sort_as_the_reader(owner, connect):
    from connect_labs.mcp.testing import read_resource

    _call(owner, keep_result=True)
    uri = f"labs-result://{Row.objects.get().pk}"
    _, raw = MCPAccessToken.create_token(owner, name="sort")
    contents = read_resource(raw, f"{uri}?sort=visits&dir=desc&page=1&page_size=5")
    body = json.loads(contents[0].text)
    assert [r[2] for r in body["rows"]] == [8] * 5
    assert contents[0].meta["labs/result"]["sort"] == {"column": "visits", "dir": "desc"}
    refused = read_resource(raw, f"{uri}?sort=not_a_column")
    assert isinstance(refused, Exception) and refused.error.data == {"code": "bad_uri"}
