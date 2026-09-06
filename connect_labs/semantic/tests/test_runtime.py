"""The semantic layer, EXECUTED in process.

The registry, compiler, Layer 1 and gates were all proven and none of them were
reachable: a grep for ``connect_labs.semantic`` across the application returned the
package and its own tests and nothing else. The workflow named "SQL semantic layer"
was serving numbers frozen into a saved run rather than numbers this code produced.

These run the real thing against real Postgres through the same
``connection.cursor()`` the analysis SQL backend uses, because "it compiles" was
never the claim in question.
"""

from __future__ import annotations

import pytest

from connect_labs.semantic.runtime import SemanticRuntimeError, evaluate, filter_to_series, load_registry

psycopg2 = pytest.importorskip("psycopg2")

pytestmark = pytest.mark.django_db


# ── registry loading and series selection (no database needed) ───────────────


def test_the_registry_loads():
    props, inds = load_registry("kmc")
    assert props["entity"] == "baby"
    assert inds["cube"] == "kmc_case"


def test_an_unknown_registry_says_so_rather_than_raising_a_bare_oserror():
    with pytest.raises(SemanticRuntimeError, match="no semantic registry"):
        load_registry("does-not-exist")


def test_selecting_a_series_keeps_the_parts_its_indicators_are_built_from():
    """Numerators, denominators and the input-availability gates carry no `meta`.
    Dropping them alongside the other series would compile an indicator whose own
    pieces no longer exist."""
    _, inds = load_registry("kmc")
    only_n = filter_to_series(inds, "N")
    names = {m["name"] for m in only_n["measures"]}

    ids = {m["meta"]["indicator"] for m in only_n["measures"] if m.get("meta")}
    assert ids and all(i.startswith("N") for i in ids)
    # the pieces survive
    assert "n09_numerator" in names and "n09_denominator" in names
    # and the other series is gone
    assert not any(i.startswith("C") for i in ids)


def test_selecting_the_C_series_excludes_the_N_series_and_the_reverse():
    """They answer different questions and disagree on maturity and growth bands by
    design, so a caller must never silently receive the other's columns."""
    _, inds = load_registry("kmc")
    c_ids = {m["meta"]["indicator"] for m in filter_to_series(inds, "C")["measures"] if m.get("meta")}
    n_ids = {m["meta"]["indicator"] for m in filter_to_series(inds, "N")["measures"] if m.get("meta")}
    assert c_ids and n_ids
    assert not (c_ids & n_ids)


def test_an_unknown_series_is_refused():
    _, inds = load_registry("kmc")
    with pytest.raises(SemanticRuntimeError, match="unknown indicator series"):
        filter_to_series(inds, "Z")


def test_evaluate_refuses_a_call_it_cannot_build_layer1_for():
    with pytest.raises(SemanticRuntimeError, match="pipeline_schema"):
        evaluate(None, [1], visit_sql=None)


# ── execution against Postgres ───────────────────────────────────────────────


@pytest.fixture
def fixture_visits(db):
    """Two babies over the same shape the parity fixture uses.

    Column names and types are copied from test_parity's DDL rather than invented:
    the compiled SQL selects them by name, so a guessed schema fails on the first
    missing column and tells you nothing about the runtime under test.
    """
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS rt_fixture_visits")
        cur.execute(
            """
            CREATE TABLE rt_fixture_visits (
                baby_case_id text, visit_date timestamp, weight_g double precision,
                child_alive_no boolean, danger_sign_yes boolean, referred_yes boolean,
                self_referral_yes boolean, ebf_recorded boolean, form_name text,
                days_discharge_to_reg double precision, birth_weight_g double precision,
                enrollment_weight_g double precision, kmc_hours_mean double precision,
                reg_date timestamp, opportunity_id int, username text
            )
            """
        )
        rows = [
            # b1: registered + started, weighed across the growth window, survives.
            ("b1", 0, 1500.0, "Registration"),
            ("b1", 10, 1650.0, "Follow-up"),
            ("b1", 28, 1900.0, "Follow-up"),
            ("b1", 44, 2100.0, "Follow-up"),
            # b2: a second worker's baby, so the flw scope has more than one row.
            ("b2", 0, 1400.0, "Registration"),
            ("b2", 30, 1750.0, "Follow-up"),
        ]
        for baby, off, w, form in rows:
            cur.execute(
                "INSERT INTO rt_fixture_visits VALUES (%s, DATE '2026-01-01' + %s, %s,"
                " false, false, false, false, true, %s, 1.0, 1500.0, 1500.0, 4.0,"
                " DATE '2026-01-01', 10042, %s)",
                (baby, off, w, form, "asha" if baby == "b1" else "ravi"),
            )
    yield "SELECT * FROM rt_fixture_visits"
    with connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS rt_fixture_visits")


def test_the_N_series_actually_runs_and_returns_rows(fixture_visits):
    """The whole point. Not "it compiles" — it executes, through the same cursor the
    analysis SQL backend uses, and hands back rows."""
    rows = evaluate(
        None,
        [10042],
        visit_sql=fixture_visits,
        series="N",
        scope="programme",
        as_of="'2026-04-01'",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["n_cases"] == 2
    # a value AND its denominator, which is the registry's structural rule
    assert "n03" in row and "n03_denominator" in row
    # and nothing from the other series leaked in
    assert not any(k.startswith("c0") for k in row)


def test_several_scopes_come_back_from_ONE_pass(fixture_visits):
    """compile_rollup_sql exists because per-scope calls re-run the entire Layer 1
    extraction each time — 28.2s + 31.2s + 27.3s for three scopes on opp 10042."""
    rows = evaluate(
        None,
        [10042],
        visit_sql=fixture_visits,
        series="N",
        scopes=["programme", "opportunity", "flw"],
        as_of="'2026-04-01'",
    )
    assert {r["scope"] for r in rows} == {"programme", "opportunity", "flw"}


def test_a_failing_query_raises_a_readable_error_not_a_raw_driver_traceback(db):
    """The compiled statement is hundreds of lines and the useful part is which
    relation or column was missing, so the SQL goes to debug and the message stays
    readable.

    Wrapped in atomic(): a failed statement aborts the surrounding transaction, and
    without a savepoint to roll back to that leaks into teardown as
    InFailedSqlTransaction — an error about the NEXT thing, which is exactly the
    kind of misdirection this test exists to prevent.
    """
    from django.db import transaction

    with pytest.raises(SemanticRuntimeError, match="semantic query failed"):
        with transaction.atomic():
            evaluate(None, [10042], visit_sql="SELECT * FROM no_such_relation", series="N")


def test_selecting_a_series_does_not_drag_in_the_other_series_parts():
    """An indicator is three measures and only the VALUE carries `meta`, so "keep
    everything without meta" is the obvious rule and the wrong one — it retains every
    other series' numerators and denominators, which compile into the result as
    columns nobody asked for.

    Caught live: the first N-series result came back carrying c01_numerator.
    """
    _, inds = load_registry("kmc")
    names = {m["name"] for m in filter_to_series(inds, "N")["measures"]}
    assert names, "the N series must survive its own filter"
    assert not [n for n in names if n.startswith("c")], "C-series parts leaked into the N series"

    c_names = {m["name"] for m in filter_to_series(inds, "C")["measures"]}
    assert not [n for n in c_names if n.startswith("n")], "N-series parts leaked into the C series"


def test_an_indicator_keeps_its_denominator_even_when_its_value_never_references_it():
    """A count indicator's sql is just {x_numerator}. Following references alone
    drops the denominator — and the registry's no-bare-numbers rule is precisely
    that a value is never reportable without one."""
    _, inds = load_registry("kmc")
    names = {m["name"] for m in filter_to_series(inds, "N")["measures"]}
    for ind in ("n01", "n02", "n03", "n04"):
        assert f"{ind}_denominator" in names, f"{ind} lost its denominator"


def test_the_measure_catalog_carries_what_a_renderer_needs_to_band_a_value():
    """Bands, direction and unit travel WITH the rows, from the same YAML that
    produced the numbers — otherwise a renderer keeps its own copy and the threshold
    drifts from the measure it grades, which is the duplication this layer exists to
    end."""
    from connect_labs.semantic.runtime import measure_catalog

    _, inds = load_registry("kmc")
    cat = {c["indicator"]: c for c in measure_catalog(filter_to_series(inds, "N"))}
    assert len(cat) == 14

    banded = [c for c in cat.values() if c["bands"]]
    assert len(banded) == 9, "9 of the 14 carry a band; the counts and medians do not"
    for c in banded:
        assert c["direction"] in {"higher", "lower", "mid2"}, c["indicator"]
        assert c["bands_source"], f"{c['indicator']} has a band with no provenance"


def test_mortality_is_two_sided_because_implausibly_low_means_under_recording():
    """The spec is explicit that under ~2% mortality means deaths are not being
    recorded, not that babies are surviving. A one-sided lower-is-better band would
    paint exactly that failure green."""
    from connect_labs.semantic.runtime import measure_catalog

    _, inds = load_registry("kmc")
    n13 = [c for c in measure_catalog(filter_to_series(inds, "N")) if c["indicator"] == "N13"][0]
    assert n13["direction"] == "mid2"
    assert n13["bands"][0][0] == 4 and n13["bands"][1][0] == 2


def test_percent_bands_are_on_the_percent_scale_the_measures_return():
    """The N-series measures return 100.0 * num/den. The C-series stores the same
    thresholds as FRACTIONS, so copying them across without rescaling would be wrong
    by 100x and would band everything green."""
    from connect_labs.semantic.runtime import measure_catalog

    _, inds = load_registry("kmc")
    for c in measure_catalog(filter_to_series(inds, "N")):
        if c["unit"] == "%" and c["bands"] and c["direction"] != "mid2":
            assert max(c["bands"]) > 1, f"{c['indicator']} bands look like fractions"


def test_evaluate_refuses_the_raw_schema_dict_by_name():
    """The bug that made the endpoint's first live call a 500.

    build_visit_sql delegates to the pipeline engine's query builder, which wants an
    AnalysisPipelineConfig. Handed the stored schema DICT instead, it failed five
    frames down as `'dict' object has no attribute 'terminal_stage'` — naming neither
    the offending argument nor the caller — and reached the caller as a bare
    "An internal error occurred".
    """
    with pytest.raises(SemanticRuntimeError, match="AnalysisPipelineConfig"):
        evaluate({"fields": [], "terminal_stage": "entity"}, [10042], series="N")
