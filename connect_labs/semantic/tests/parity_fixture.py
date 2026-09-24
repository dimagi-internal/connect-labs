"""A multi-opportunity, multi-worker, multi-month visit fixture, and a runner that
executes a registry's compiled rollup over it.

WHY THIS EXISTS
test_parity.py proves the SQL agrees with an independent reference -- but on ONE
opportunity, ONE worker and ONE registration date, so every scope below
`programme` collapses to a single row and a refactor that mis-keyed a scope
could not be seen. This fixture reuses those same babies (the branch coverage is
the expensive part) and spreads them across three opportunities, six workers,
two LLOs and several cohort months, adds a case id that recurs in two
opportunities (the opp|case key), cases with no registration date (the cohort
COALESCE), a visit after the as-of date (the as-of cut) and a row with no case
id (excluded). `run_rollup` compiles EVERY scope with the deployment's gates on
and returns normalised rows, so two engines -- or one engine before and after a
refactor -- can be compared value for value.

It is used by test_engine_parity.py, and by the one-off harness that pinned the
golden file before the engine was made generic.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
from pathlib import Path
from typing import Any

from connect_labs.semantic.tests.test_parity import DISCHARGE_TO_REG, VISITS

AS_OF = "(DATE '2026-01-01' + 200)"
ALL_SCOPES = [
    "programme",
    "llo",
    "opportunity",
    "flw",
    "month",
    "llo_month",
    "opportunity_month",
    "flw_month",
    "case",
]
LLO_MAP = {1: "PIPN", 2: "GHI", 3: "EHA"}
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "kmc_rollup_golden.json"

# baby -> (opportunity, worker, start offset in days, has a registration date)
ASSIGN = {
    "h1": (1, "flw1", 0, True),
    "s1": (1, "flw1", 0, True),
    "d2": (1, "flw1", 31, True),
    "d1": (1, "flw2", 31, False),
    "f1": (1, "flw2", 0, True),
    "i1": (2, "flw3", 0, True),
    "k1": (2, "flw3", 45, True),
    "u1": (2, "flw1", 60, True),  # same username as opp 1's flw1: a DIFFERENT worker
    "x1": (2, "flw4", 31, True),
    "l1": (2, "flw4", 0, True),
    "p1": (3, "flw5", 0, True),
    "p2": (3, "flw5", 31, True),
    "g1": (3, "flw5", 45, True),
    "n1": (3, "flw6", 70, False),
    "m1": (3, "flw6", 0, True),
}

TABLE = "parity_visits"
DDL = f"""
CREATE TEMP TABLE {TABLE} (
    baby_case_id text, visit_date timestamp, weight_g double precision,
    child_alive_no boolean, danger_sign_yes boolean, referred_yes boolean,
    self_referral_yes boolean, ebf_recorded boolean, form_name text,
    days_discharge_to_reg double precision, birth_weight_g double precision,
    enrollment_weight_g double precision, gestational_age_wks double precision, kmc_hours_mean double precision,
    reg_date timestamp, hospital_discharge_date timestamp,
    opportunity_id int, username text
) ON COMMIT PRESERVE ROWS;
"""


def fixture_rows() -> list[tuple]:
    base = dt.date(2026, 1, 1)
    rows = []
    for baby, off, w, alive, danger, ref, form, d2r, bw, ew in VISITS:
        opp, user, start, has_reg = ASSIGN[baby]
        reg = base + dt.timedelta(days=start)
        d2 = DISCHARGE_TO_REG.get(baby)
        rows.append(
            (
                baby,
                dt.datetime.combine(reg + dt.timedelta(days=off), dt.time()),
                w,
                alive == "no",
                danger == "yes",
                ref == "yes",
                baby in ("s1", "p2"),  # self-referral on two babies' visits
                off % 2 == 0,  # ebf recorded on even-day visits
                form,
                d2r,
                bw,
                ew,
                34.0 if baby != "g1" else 50.0,  # g1: implausible gestational age
                4.0 + (off % 3),
                dt.datetime.combine(reg, dt.time()) if has_reg else None,
                dt.datetime.combine(reg - dt.timedelta(days=d2), dt.time()) if d2 is not None else None,
                opp,
                user,
            )
        )
    # The SAME case id in a second opportunity is a different baby (opp|case key).
    for off, w, form in ((0, 1480, "Registration"), (12, 1600, "Follow-up"), (30, 1850, "Follow-up")):
        day = base + dt.timedelta(days=10 + off)
        rows.append(
            ("h1", dt.datetime.combine(day, dt.time()), w, False, False, False, False, True, form)
            + (1.0, 1480.0, 1480.0, 33.0, 5.0, dt.datetime(2026, 1, 11), None, 2, "flw3")
        )
    # After the as-of date: must not exist for the report.
    rows.append(
        ("h1", dt.datetime(2026, 9, 1), 2600, True, True, True, True, True, "Follow-up")
        + (None, None, None, None, None, None, None, 1, "flw1")
    )
    # No case id: excluded from every entity.
    rows.append(
        (None, dt.datetime(2026, 2, 1), 1500, False, False, False, False, True, "Follow-up")
        + (None, None, None, None, None, None, None, 1, "flw1")
    )
    return rows


def load(conn) -> str:
    """Load the fixture into a session-local TEMP table and return a visit_sql over it.

    TEMP, not a real table: CI runs the suite under xdist, and two workers
    dropping and recreating one shared table is a race. A temp table is private to
    the connection that made it.
    """
    conn.rollback()
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
    cur.execute(DDL)
    placeholders = ", ".join(["%s"] * 18)
    cur.executemany(f"INSERT INTO {TABLE} VALUES ({placeholders})", fixture_rows())
    conn.commit()
    return f"SELECT * FROM {TABLE}"


def _norm(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return {"D": str(value)}
    if isinstance(value, float):
        return {"F": repr(value)}
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def run_rollup(conn, props_doc: dict, indicators_doc: dict, deployment: dict | None, visit_sql: str) -> list[dict]:
    """Every scope, gates on, executed; rows normalised and sorted deterministically."""
    from connect_labs.semantic.compiler import compile_rollup_sql

    settings = (deployment or {}).get("settings") or None
    sql = compile_rollup_sql(
        props_doc,
        indicators_doc,
        visit_sql,
        scopes=ALL_SCOPES,
        as_of=AS_OF,
        llo_map=LLO_MAP,
        settings=settings,
    )
    cur = conn.cursor()
    cur.execute(sql)
    cols = [c.name for c in cur.description]
    rows = [{c: _norm(v) for c, v in zip(cols, r)} for r in cur.fetchall()]
    conn.commit()
    keys = ("scope", "llo", "opportunity_id", "username", "cohort_month", "case_id")
    rows.sort(key=lambda r: tuple(json.dumps(r.get(k), sort_keys=True) for k in keys))
    return rows


def rows_differ(a: list[dict], b: list[dict], *, float_rel_tol: float = 0.0) -> list[str]:
    """Every difference between two result sets, as readable lines. Empty means identical."""
    import math

    out: list[str] = []
    if len(a) != len(b):
        out.append(f"row count {len(a)} != {len(b)}")
    for i, (x, y) in enumerate(zip(a, b)):
        if set(x) != set(y):
            out.append(f"row {i}: columns differ: {sorted(set(x) ^ set(y))}")
        for col in sorted(set(x) & set(y)):
            u, v = x[col], y[col]
            if u == v:
                continue
            if float_rel_tol and isinstance(u, dict) and isinstance(v, dict) and set(u) == set(v) == {"F"}:
                if math.isclose(float(u["F"]), float(v["F"]), rel_tol=float_rel_tol, abs_tol=float_rel_tol):
                    continue
            out.append(f"row {i} ({x.get('scope')}) {col}: {u!r} != {v!r}")
    return out
