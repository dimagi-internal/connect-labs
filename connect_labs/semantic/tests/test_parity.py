"""EXECUTE the compiled SQL and prove it equals the JavaScript implementation.

This is the claim the whole approach rests on: moving Layer 2 and Layer 3 out of
the browser and into SQL must not move the numbers. So the same fixture cases go
through both paths -- the compiled SQL against real Postgres, and a faithful port
of kmc_programme_metrics_render.js -- and every indicator must agree.

Skipped when no Postgres is reachable; the structural tests in test_compiler.py
still run everywhere.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest
import yaml

from connect_labs.semantic.compiler import compile_indicator_sql

psycopg2 = pytest.importorskip("psycopg2")

REGISTRY = Path(__file__).resolve().parents[1] / "registry" / "kmc"
DSN = os.environ.get(
    "SEMANTIC_TEST_DSN",
    "host=127.0.0.1 port=5432 user=postgres password=postgres dbname=postgres",
)
ELIG, SWING = 28, 0.25
LO, HI = 21, 35
PLAUSIBLE_LO, PLAUSIBLE_HI = 10, 20


@pytest.fixture(scope="module")
def conn():
    try:
        c = psycopg2.connect(DSN, connect_timeout=4)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no Postgres for the parity test: {exc}")
    yield c
    c.close()


# ── The fixture: visits, chosen to exercise each branch ──────────────────────
# (baby, day_offset, weight_g, alive, danger, referred, form_name, ...)
VISITS = [
    # b1 — registered, started, eligible, clean growth ~14 g/kg/d, survives
    ("b1", 0, 1500, "yes", "no", "no", "Registration", 2.0, 1500.0, 1.0),
    ("b1", 10, 1650, "yes", "no", "no", "Follow-up", None, None, None),
    ("b1", 28, 1900, "yes", "no", "no", "Follow-up", None, None, None),
    ("b1", 40, 2100, "yes", "no", "no", "Follow-up", None, None, None),
    # b2 — died before day 28 -> early_exit, outcome_known
    ("b2", 0, 1200, "yes", "yes", "yes", "Registration", 5.0, 1200.0, 3.0),
    ("b2", 9, 1150, "no", "yes", "yes", "Follow-up", None, None, None),
    # b3 — lost to follow-up: eligible by age, last visit < 28d, alive
    ("b3", 0, 1400, "yes", "no", "no", "Registration", 1.0, 1400.0, 1.0),
    ("b3", 5, 1420, "yes", "no", "no", "Follow-up", None, None, None),
    # b4 — implausible swing -> weight_consistent false
    ("b4", 0, 1300, "yes", "no", "no", "Registration", 0.0, 1300.0, 2.0),
    ("b4", 12, 3000, "yes", "no", "no", "Follow-up", None, None, None),
    ("b4", 30, 3100, "yes", "no", "no", "Follow-up", None, None, None),
    # b5 — only one weight day -> not computable
    ("b5", 0, 1600, "yes", "no", "no", "Registration", 9.0, 1600.0, 4.0),
    ("b5", 33, 1600, "yes", "no", "no", "Follow-up", None, None, None),
    # b6 — registration only, never started
    ("b6", 0, None, "yes", "no", "no", "Registration", None, None, None),
    # b8 — last visit at day 29: outcome_known flips if the eligibility gate moves
    # off 28. Without a case straddling the gate the parity test cannot detect a
    # changed constant, and "SQL matches JS" would be a weaker claim than it reads.
    ("b8", 0, 1550, "yes", "no", "no", "Registration", 2.0, 1550.0, 1.0),
    ("b8", 29, 1800, "yes", "no", "no", "Follow-up", None, None, None),
    # b9 — first visit carries NO weight, so the first weighing is 6 days later.
    # Anchoring the growth window on the first WEIGHT instead of the first VISIT
    # shifts w28 selection and silently changes C09-C13. Every other fixture baby is
    # weighed on its first visit, which is exactly why real data caught this and the
    # fixture did not.
    # Weights on days 10 and 44 only. Anchored on the FIRST VISIT (day 0) both ages
    # are 10 and 44 -- outside the [21,35] window -- so early growth is NULL and the
    # baby is not weight_gain_data_sufficient. Anchored on the first WEIGHT (day 10)
    # the second reading lands at age 34, inside the window, and the baby wrongly
    # counts toward C09-C13. The reading has to cross the boundary for the anchor to
    # matter; an earlier version of this case kept both readings in-window under both
    # anchors and therefore proved nothing.
    ("b9", 0, None, "yes", "no", "no", "Registration", 1.0, 1500.0, 1.0),
    ("b9", 10, 1500, "yes", "no", "no", "Follow-up", None, None, None),
    ("b9", 44, 1700, "yes", "no", "no", "Follow-up", None, None, None),
    # b7 — birth-copy: enrolment weight == birth weight
    ("b7", 0, 1700, "yes", "no", "no", "Registration", 3.0, 1700.0, 1700.0),
    ("b7", 14, 1850, "yes", "no", "no", "Follow-up", None, None, None),
    ("b7", 31, 2000, "yes", "no", "no", "Follow-up", None, None, None),
]

DDL = """
DROP TABLE IF EXISTS fixture_visits;
CREATE TABLE fixture_visits (
    baby_case_id text, visit_date timestamp, weights double precision,
    child_alive_no boolean, danger_sign_yes boolean, referred_yes boolean,
    self_referral_yes boolean, ebf_recorded boolean, form_name text,
    days_discharge_to_reg double precision, birth_weight_g double precision,
    enrollment_weight_g double precision, kmc_hours_mean double precision,
    reg_date timestamp, opportunity_id int, username text
);
"""


def _load(conn):
    conn.rollback()
    cur = conn.cursor()
    cur.execute(DDL)
    for baby, off, w, alive, danger, ref, form, d2r, bw, ew in VISITS:
        cur.execute(
            "INSERT INTO fixture_visits VALUES (%s, DATE '2026-01-01' + %s, %s, %s, %s, %s,"
            " false, true, %s, %s, %s, %s, 4.0, DATE '2026-01-01', 1, 'flw1')",
            (baby, off, w, alive == "no", danger == "yes", ref == "yes", form, d2r, bw, ew),
        )
    conn.commit()


# ── A faithful port of the render-code logic ─────────────────────────────────


def _js_properties(as_of_offset=200):
    """Mirror kmc_programme_metrics_render.js lines ~575-670."""
    babies: dict[str, dict] = {}
    for baby, off, w, alive, danger, ref, form, d2r, bw, ew in VISITS:
        b = babies.setdefault(
            baby,
            {
                "days": {},
                "forms": [],
                "visits": 0,
                "deaths": 0,
                "danger": 0,
                "ref": 0,
                "d2r": None,
                "bw": None,
                "ew": None,
            },
        )
        b["visits"] += 1
        b["forms"].append(form)
        if alive == "no":
            b["deaths"] += 1
        if danger == "yes":
            b["danger"] += 1
        if ref == "yes":
            b["ref"] += 1
        if d2r is not None:
            b["d2r"] = d2r
        if bw is not None:
            b["bw"] = bw
        if ew is not None:
            b["ew"] = ew
        b.setdefault("offs", []).append(off)
        b["n_raw"] = b.get("n_raw", 0) + (1 if w is not None else 0)
        b["n_raw_round"] = b.get("n_raw_round", 0) + (1 if w is not None and w % 100 == 0 else 0)
        if w is not None and 400 <= w <= 8000:
            b["days"][off] = w

    out = {}
    for name, b in babies.items():
        d = {}
        n_reg = sum(1 for f in b["forms"] if "regist" in f.lower())
        n_fu = len(b["forms"]) - n_reg
        d["registered"] = n_reg >= 1
        d["started"] = n_fu >= 1
        first, last = min(b["offs"]), max(b["offs"])
        d["days_since_first_visit"] = as_of_offset - first
        d["days_first_to_last"] = last - first
        d["eligible"] = d["started"] and d["days_since_first_visit"] >= ELIG
        d["died"] = b["deaths"] > 0
        d["outcome_known"] = d["died"] or d["days_first_to_last"] >= ELIG
        d["early_exit"] = d["died"] and d["days_first_to_last"] < ELIG
        ws = [(k, b["days"][k]) for k in sorted(b["days"])]
        d["n_weights"] = len(ws)
        span = (ws[-1][0] - ws[0][0]) if len(ws) >= 2 else 0
        d["weight_computable"] = len(ws) >= 2 and span >= 7
        consistent = d["weight_computable"]
        for i in range(1, len(ws)):
            if abs(ws[i][1] - ws[i - 1][1]) > SWING * ws[i - 1][1]:
                consistent = False
                break
        d["weight_consistent"] = consistent
        d["early_g_per_kg_day"] = None
        if d["weight_computable"]:
            w0 = ws[0]
            w28 = None
            for day, wv in ws:
                if LO <= (day - first) <= HI:
                    w28 = (day, wv)
            if w28 and w28[0] != w0[0]:
                dd = w28[0] - w0[0]
                if dd > 0:
                    d["early_g_per_kg_day"] = (w28[1] - w0[1]) / (w0[1] / 1000) / dd
        d["weight_gain_data_sufficient"] = d["early_g_per_kg_day"] is not None and d["weight_consistent"]
        d["growth_class"] = None
        if d["weight_gain_data_sufficient"]:
            g = d["early_g_per_kg_day"]
            d["growth_class"] = "slow" if g < PLAUSIBLE_LO else ("fast" if g > PLAUSIBLE_HI else "plausible")
        d["n_weight_readings"] = b["n_raw"]
        d["n_weights_round_100_raw"] = b["n_raw_round"]
        d["ever_danger_sign"] = b["danger"] > 0
        d["referred"] = b["ref"] > 0
        d["num_visits"] = b["visits"]
        d["enrolled_within_3d"] = None if b["d2r"] is None else b["d2r"] <= 3
        d["days_discharge_to_reg"] = b["d2r"]
        d["enrollment_is_birth_copy"] = None if b["bw"] is None or b["ew"] is None else abs(b["bw"] - b["ew"]) < 1
        out[name] = d
    return out


def _js_indicators(props):
    rows = list(props.values())

    def ratio(numf, denf):
        den = [r for r in rows if denf(r)]
        if not den:
            return None
        return 100.0 * len([r for r in den if numf(r)]) / len(den)

    def mean(valf, denf):
        vals = [valf(r) for r in rows if denf(r)]
        vals = [v for v in vals if isinstance(v, (int, float))]
        return sum(vals) / len(vals) if vals else None

    return {
        "C01": float(len([r for r in rows if r["registered"]])),
        "C02": float(len([r for r in rows if r["started"]])),
        "C05": float(len(rows)),
        "C07": ratio(lambda r: r["weight_computable"], lambda r: r["eligible"] and not r["early_exit"]),
        "C08": ratio(lambda r: r["weight_consistent"], lambda r: r["weight_computable"]),
        "C09": ratio(lambda r: r["weight_gain_data_sufficient"], lambda r: r["eligible"] and not r["early_exit"]),
        "C10": ratio(lambda r: r["growth_class"] == "plausible", lambda r: r["weight_gain_data_sufficient"]),
        "C13": mean(lambda r: r["early_g_per_kg_day"], lambda r: r["weight_gain_data_sufficient"]),
        "C14": ratio(lambda r: r["died"], lambda r: r["eligible"] and r["outcome_known"]),
        "C15": ratio(lambda r: not r["outcome_known"], lambda r: r["eligible"]),
        "C20": ratio(lambda r: r["ever_danger_sign"], lambda r: r["eligible"]),
        "C24": mean(lambda r: r["num_visits"], lambda r: r["started"]),
        "C28": ratio(lambda r: r["enrollment_is_birth_copy"], lambda r: r["enrollment_is_birth_copy"] is not None),
        # C31 is sum-over-sum across RAW weight readings, not a case ratio.
        "C31": (
            100.0 * sum(r["n_weights_round_100_raw"] for r in rows) / sum(r["n_weight_readings"] for r in rows)
            if sum(r["n_weight_readings"] for r in rows)
            else None
        ),
    }


def test_sql_matches_the_javascript(conn):
    props_doc = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    _load(conn)

    visit_sql = "SELECT * FROM fixture_visits"
    sql = compile_indicator_sql(
        props_doc,
        registry,
        visit_sql,
        scope="programme",
        as_of="(DATE '2026-01-01' + 200)",
    )
    cur = conn.cursor()
    cur.execute(sql)
    cols = [c.name for c in cur.description]
    row = dict(zip(cols, cur.fetchone()))

    expected = _js_indicators(_js_properties())
    mismatches = []
    for ind, want in expected.items():
        got = row.get(ind.lower())
        if want is None and got is None:
            continue
        if want is None or got is None:
            mismatches.append(f"{ind}: sql={got!r} js={want!r}")
        elif not math.isclose(float(got), float(want), rel_tol=1e-6, abs_tol=1e-6):
            mismatches.append(f"{ind}: sql={float(got):.6f} js={float(want):.6f}")
    assert not mismatches, "SQL and JS disagree:\n  " + "\n  ".join(mismatches)


def test_denominators_are_reported_alongside_values(conn):
    """No bare numbers: every indicator's denominator comes back with it."""
    props_doc = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    _load(conn)
    sql = compile_indicator_sql(
        props_doc,
        registry,
        "SELECT * FROM fixture_visits",
        scope="programme",
        as_of="(DATE '2026-01-01' + 200)",
    )
    cur = conn.cursor()
    cur.execute(sql)
    cols = {c.name for c in cur.description}
    for m in registry["measures"]:
        if m.get("meta"):
            assert f"{m['name']}_denominator" in cols


def test_rollup_equals_per_scope_queries(conn):
    """One GROUPING SETS pass must give exactly what N separate queries gave.

    This is the guard on the optimisation: the reason to collapse the scopes into
    one pass is speed, and the only way that is a win rather than a regression is
    if the numbers are untouched.
    """
    from connect_labs.semantic.compiler import compile_rollup_sql

    props_doc = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    _load(conn)
    visit_sql = "SELECT * FROM fixture_visits"
    as_of = "(DATE '2026-01-01' + 200)"
    scopes = ["programme", "opportunity", "flw", "month"]

    per_scope = {}
    cur = conn.cursor()
    for sc in scopes:
        cur.execute(compile_indicator_sql(props_doc, registry, visit_sql, scope=sc, as_of=as_of))
        cols = [c.name for c in cur.description]
        per_scope[sc] = [dict(zip(cols, r)) for r in cur.fetchall()]

    cur.execute(compile_rollup_sql(props_doc, registry, visit_sql, scopes=scopes, as_of=as_of))
    rcols = [c.name for c in cur.description]
    rolled: dict[str, list[dict]] = {s: [] for s in scopes}
    for r in cur.fetchall():
        row = dict(zip(rcols, r))
        rolled.setdefault(row["scope"], []).append(row)

    def key(sc, row):
        return tuple(str(row.get(c)) for c in ("opportunity_id", "username", "cohort_month"))

    for sc in scopes:
        assert len(rolled[sc]) == len(per_scope[sc]), f"{sc}: row count differs"
        a = {key(sc, r): r for r in per_scope[sc]}
        b = {key(sc, r): r for r in rolled[sc]}
        assert set(a) == set(b), f"{sc}: grouping keys differ"
        for k in a:
            for m in registry["measures"]:
                n = m["name"]
                x, y = a[k].get(n), b[k].get(n)
                if x is None and y is None:
                    continue
                assert x is not None and y is not None, f"{sc}/{k}/{n}: {x!r} vs {y!r}"
                assert math.isclose(
                    float(x), float(y), rel_tol=1e-9, abs_tol=1e-9
                ), f"{sc}/{k}/{n}: per-scope={x} rollup={y}"


def test_every_declared_scope_actually_executes(conn):
    """Compiling is not the bar -- the SQL has to RUN.

    `llo` compiled cleanly for weeks and failed at execution with "column
    props.llo does not exist". A test that only asserted compilation could never
    have caught it, so this one executes every scope in SCOPES against Postgres.
    Scopes needing a caller-supplied column are driven with one.
    """
    from connect_labs.semantic.compiler import INTRINSIC_SCOPE_COLUMNS, SCOPES

    props_doc = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    _load(conn)
    cur = conn.cursor()
    as_of = "(DATE '2026-01-01' + 200)"

    for scope in SCOPES:
        needs_llo = "llo" in SCOPES[scope]
        sql = compile_indicator_sql(
            props_doc,
            registry,
            "SELECT * FROM fixture_visits",
            scope=scope,
            as_of=as_of,
            llo_map={1: "PIPN"} if needs_llo else None,
        )
        try:
            cur.execute(sql)
            cur.fetchall()
        except Exception as exc:  # pragma: no cover - the point is the message
            conn.rollback()
            raise AssertionError(f"scope {scope!r} compiled but did not run: {exc}") from exc

    # And the intrinsic set really is intrinsic: no map needed.
    for scope in SCOPES:
        if set(SCOPES[scope]) <= set(INTRINSIC_SCOPE_COLUMNS):
            cur.execute(
                compile_indicator_sql(
                    props_doc,
                    registry,
                    "SELECT * FROM fixture_visits",
                    scope=scope,
                    as_of=as_of,
                )
            )
            cur.fetchall()


def test_suppression_marks_the_non_credible_llo(conn):
    """C14 must come back flagged for an LLO the settings say is not credible.

    The registry declared these rules and the compiler ignored them, so a
    mortality figure would have been published for an LLO the workbook says does
    not record deaths credibly -- the exact gap this project raised about the
    other implementation.
    """
    props_doc = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    _load(conn)
    cur = conn.cursor()
    cur.execute(
        compile_indicator_sql(
            props_doc,
            registry,
            "SELECT * FROM fixture_visits",
            scope="llo",
            as_of="(DATE '2026-01-01' + 200)",
            llo_map={1: "GHI"},
            settings={"mortality_recording_credible": {"PIPN": True, "GHI": False}},
        )
    )
    cols = [c.name for c in cur.description]
    row = dict(zip(cols, cur.fetchone()))
    assert "c14_suppressed" in cols, "suppression column was not emitted"
    assert row["c14_suppressed"] is True, "GHI is not credible but C14 came back unsuppressed"
    assert row["c14"] is not None, "the value is still computed -- suppression is display, not deletion"
