"""Server-side snapshot for the KMC programme-metrics dashboard.

WHY THIS EXISTS. The workflow framework is built so an agent can create a run and
complete it over the API. `kmc_programme_metrics` could not: it declared
`snapshot_inputs.state_keys: ["frozen"]` with no `build_snapshot` hook, so the only
thing that could produce a snapshot was its RENDER, in a browser. Completing a run
any other way froze an empty one (now refused — see SnapshotStateNotStagedError).

So this is the port of `buildFrozen()` from kmc_programme_metrics_render.js. Every
number comes from the SAME place the live dashboard reads — `semantic.runtime.evaluate`
over the registry — so a frozen run and a live run cannot disagree about a value. The
part that had to be re-implemented rather than reused is the DISPLAY contract: banding,
the min-denominator rule, the two-reason availability gate, and the C16 thin-coverage
footnote all lived only in the JS.

PARITY IS THE WHOLE RISK. A snapshot that is subtly different from the live view is
worse than no snapshot: it is a funder-facing dashboard whose colours disagree with the
one it was captured from. `cEntry` is ported branch for branch below, in the same order,
and test_kmc_snapshot.py pins each branch.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from connect_labs.semantic import gates

# `cEntry`'s default when a measure has no row at all.
_NODATA = "nodata"
_MIN_DEN = 25  # render: var MIN_DEN = 25
_C16_MIN_COVERAGE = 0.45  # Neal's spec item 8


def band_of(direction: str | None, bands: Any, value: Any) -> str:
    """Port of nBandOf(). Same branch order, same guards."""
    if value is None:
        return _NODATA
    try:
        x = float(value)
    except (TypeError, ValueError):
        return _NODATA
    if x != x:  # NaN
        return _NODATA
    if not bands:
        return "unbanded"
    if direction == "higher":
        return "green" if x >= bands[0] else ("yellow" if x >= bands[1] else "red")
    if direction == "lower":
        return "green" if x <= bands[0] else ("yellow" if x <= bands[1] else "red")
    if direction == "mid2":
        # Two-sided. Guard the SHAPE: a one-dimensional band here would read as
        # 'unbanded', which is the single outcome a two-sided mortality band exists
        # to prevent (under ~2% means deaths are not recorded, not that babies live).
        if not bands or not isinstance(bands[0], (list, tuple)) or len(bands[0]) != 2:
            return "unbanded"
        if bands[0][0] <= x <= bands[0][1]:
            return "green"
        if len(bands) > 1 and isinstance(bands[1], (list, tuple)) and len(bands[1]) == 2:
            if bands[1][0] <= x <= bands[1][1]:
                return "yellow"
        return "red"
    return "unbanded"


def _scope_opps(row: dict, llo_map: dict[int, str]) -> list[int] | None:
    """Port of cScopeOpps(): which opportunities a scope row covers."""
    scope = row.get("scope")
    if scope in ("opportunity", "flw"):
        oid = row.get("opportunity_id")
        return [int(oid)] if oid is not None else None
    if scope == "llo":
        return [o for o, l in llo_map.items() if l == row.get("llo")]
    return None


def _llo_of_row(row: dict, llo_map: dict[int, str]) -> str | None:
    if row.get("llo"):
        return row["llo"]
    oid = row.get("opportunity_id")
    if oid is not None:
        return llo_map.get(int(oid))
    return None


def entry(measure: dict, row: dict | None, llo_map: dict[int, str], credible_sets: dict) -> dict:
    """Port of cEntry(). Branch order matters — see the module docstring."""
    ind_id = measure["indicator"]
    out: dict[str, Any] = {"id": ind_id, "n": 0, "value": None, "band": _NODATA}
    if not row:
        return out

    den = row.get(f"{measure['id']}_denominator")
    out["n"] = 0 if den is None else int(float(den))

    # Credibility MARKS a figure, it does not erase it — blanking hides the
    # under-recording, since pooling every LLO reads lower than credible recorders.
    llo = _llo_of_row(row, llo_map)
    credible = credible_sets.get(ind_id)
    not_credible = bool(llo) and credible is not None and credible.get(llo) is not True

    state = gates.input_state(ind_id, row, _scope_opps(row, llo_map))
    if state != "ok":
        out["band"] = state
        return out

    raw = row.get(measure["id"])
    if raw is None:
        return out
    try:
        rawf = float(raw)
    except (TypeError, ValueError):
        return out
    if rawf != rawf:
        return out

    min_den = measure.get("min_denominator") or _MIN_DEN
    if out["n"] and min_den and out["n"] < min_den:
        out["band"] = "insufficient"
        return out

    out["band"] = "notcredible" if not_credible else band_of(measure.get("direction"), measure.get("bands"), rawf)
    out["value"] = rawf / 100 if measure.get("unit") == "%" else rawf

    # Neal's spec item 8: footnote any scope where <45% of started cases carry a
    # discharge date — the rate is then computed over a self-selected minority.
    if ind_id == "C16":
        started = row.get("c02")
        if started:
            coverage = out["n"] / float(started)
            if coverage < _C16_MIN_COVERAGE:
                out["thinDenominator"] = True
                out["coverage"] = coverage
    return out


def ind_for(row: dict | None, measures: list[dict], llo_map: dict[int, str], credible_sets: dict) -> dict:
    """Port of cIndFor(): every measure's cell for one scope row."""
    return {m["indicator"]: entry(m, row, llo_map, credible_sets) for m in measures if m.get("indicator")}


# The case fields a frozen run needs to render the FLW -> cases drill and to hand a
# case off to the longitudinal view. Deliberately SLIM: the per-visit weight series
# is NOT carried. A frozen snapshot has a 5 MB hard cap, ~9,000 KMC cases fit
# comfortably at this width, and the series would not — the longitudinal workflow
# fetches it live for the one case a user opened, which is the only time it is needed.
_CASE_FIELDS = (
    "entity_id",
    "username",
    "opportunity_id",
    "reg_date",
    "dob",
    "gender",
    "birth_weight_g",
    "first_weight_g",
    "last_weight_g",
    "total_visits",
    "first_visit_date",
    "last_visit_date",
    "last_kmc_status",
)


def case_rows(pipelines: dict, llo_map: dict[int, str]) -> list[dict]:
    """Slim per-case records from the entity pipeline, for the frozen drill.

    `buildFrozen()` stores `rows: []` at every level because the LIVE render already
    holds the case rows in memory and only reads `.length` off the frozen ones. A
    frozen run has no live pipeline behind it, so a snapshot that copies that shape
    can show counts and nothing else — the drill dead-ends at the FLW.
    """
    children = ((pipelines or {}).get("children") or {}).get("rows") or []
    out = []
    for r in children:
        oid = r.get("opportunity_id")
        rec = {k: r.get(k) for k in _CASE_FIELDS if r.get(k) is not None}
        if oid is not None:
            rec["llo"] = llo_map.get(int(oid))
        out.append(rec)
    return out


def build(
    *,
    rows: list[dict],
    measures: list[dict],
    llo_map: dict[int, str],
    credible_sets: dict,
    cases: list[dict] | None = None,
    meta: dict | None = None,
    generated_at: str | None = None,
) -> dict:
    """Assemble the `frozen` payload from evaluated semantic rows.

    Mirrors buildFrozen()'s shape exactly, including the empty `rows: []` lists —
    the render reads `frozen.byLLO[].rows` for a length only, and carrying the case
    rows would multiply the snapshot size for nothing.
    """
    by_scope: dict[str, list[dict]] = {}
    for r in rows:
        by_scope.setdefault(r.get("scope"), []).append(r)

    programme = (by_scope.get("programme") or [None])[0]
    measures = [m for m in measures if m.get("indicator")]

    by_opp = []
    for r in sorted(by_scope.get("opportunity") or [], key=lambda x: x.get("opportunity_id") or 0):
        oid = r.get("opportunity_id")
        by_opp.append(
            {
                "opp": int(oid) if oid is not None else None,
                "llo": llo_map.get(int(oid)) if oid is not None else None,
                "rows": [],
                "ind": ind_for(r, measures, llo_map, credible_sets),
                "n": int(float(r.get("n_cases") or 0)),
            }
        )

    by_llo = []
    for r in sorted(by_scope.get("llo") or [], key=lambda x: str(x.get("llo"))):
        ind = ind_for(r, measures, llo_map, credible_sets)
        by_llo.append(
            {
                "llo": r.get("llo"),
                "rows": [],
                "ind": ind,
                "reds": sum(1 for c in ind.values() if c["band"] == "red"),
                "yellows": sum(1 for c in ind.values() if c["band"] == "yellow"),
                "opps": [o for o in by_opp if o["llo"] == r.get("llo")],
            }
        )

    cases = cases or []
    cases_by_flw: dict[tuple, list[dict]] = {}
    for c in cases:
        cases_by_flw.setdefault((c.get("opportunity_id"), c.get("username")), []).append(c)

    by_flw = []
    for r in by_scope.get("flw") or []:
        key = (r.get("opportunity_id"), r.get("username"))
        mine = cases_by_flw.get(key, [])
        by_flw.append(
            {
                "opp": r.get("opportunity_id"),
                "username": r.get("username"),
                "llo": _llo_of_row(r, llo_map),
                # The drill's payload. `rows` stays the key the render already reads.
                "rows": mine,
                "ind": ind_for(r, measures, llo_map, credible_sets),
                "n": int(float(r.get("n_cases") or 0)),
            }
        )

    def _month(r: dict) -> str:
        return str(r.get("cohort_month") or "")[:7]

    monthly = [
        {
            "month": _month(r),
            "ind": ind_for(r, measures, llo_map, credible_sets),
            "n": int(float(r.get("n_cases") or 0)),
        }
        for r in sorted(by_scope.get("month") or [], key=_month)
    ]

    # Monthly per drill scope, so a frozen run still supports the LLO and
    # opportunity drill with no live pipeline behind it.
    monthly_by_scope: dict[str, list[dict]] = {"all": monthly}
    for key, scope_name, field in (("llo:", "llo_month", "llo"), ("opp:", "opportunity_month", "opportunity_id")):
        for r in by_scope.get(scope_name) or []:
            ident = r.get(field)
            if ident is None:
                continue
            monthly_by_scope.setdefault(f"{key}{ident}", []).append(
                {
                    "month": _month(r),
                    "ind": ind_for(r, measures, llo_map, credible_sets),
                    "n": int(float(r.get("n_cases") or 0)),
                }
            )
    for k in monthly_by_scope:
        monthly_by_scope[k] = sorted(monthly_by_scope[k], key=lambda x: x["month"])

    return {
        "schema": 1,
        "generated_at": generated_at or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        # A snapshot is "the numbers as published", which has to include what
        # published them — otherwise a later band-threshold change silently
        # re-grades a published run.
        "cMeasures": measures,
        "mortalityCredible": credible_sets.get("C14") or {},
        "programInd": ind_for(programme, measures, llo_map, credible_sets),
        "byLLO": by_llo,
        "byOpp": by_opp,
        "byFLW": by_flw,
        "monthly": monthly,
        "monthlyByScope": monthly_by_scope,
        "nSeries": None,  # the SQL tab is captured only when it was run; never required
        # Flat case index, so the case table and a direct case link do not have to
        # walk byFLW. Same objects as byFLW[].rows — json dedupes on write, not here.
        "cases": cases,
        "meta": meta or {},
    }
