"""Grade a semantic evaluation into a saved-run payload. One builder, any registry.

WHY THIS IS NOT A TEMPLATE FILE. The thing it replaces was
`workflow/templates/kmc_snapshot.py`: 351 lines of hand-written Python that
re-implemented the KMC render's `buildSnapshot()`/`cEntry()` in a second language,
in a per-template file, requiring a DEPLOY to change. That existed because the
framework's only dynamic snapshot source (`snapshot_inputs` on the definition) can
COPY -- pipeline rows, state keys, workers, verbatim -- and cannot COMPUTE. There
was no declarative way to say "snapshot = evaluate(registry, scopes)", so a repo
hook was the only route to a computed snapshot.

Almost none of that file was KMC-specific. Its banding already read registry data
(`meta.bands`, `meta.direction`, `meta.min_denominator`); what was hardcoded was
four things -- a default min-denominator, a 0.45 coverage floor, `if ind_id ==
"C16"`, and three credibility keys -- and every one of them is now registry or spec
data. So this module grades whatever registry it is handed, and a template opts in
by DECLARING a spec rather than shipping code:

    snapshot_inputs:
      builder: semantic_snapshot
      series:  C
      scopes:  [programme, llo, opportunity, flw, month, llo_month, opportunity_month]
      case_index: {pipeline: children, fields: [entity_id, username, ...]}
      credibility: {C14: mortality_recording_credible, C18: completion_recording_credible}

That spec lives on the workflow definition, so it is patchable through
`workflow_update_definition` with no deploy -- which is the whole point.

PARITY IS STILL THE RISK, and it is now structural rather than clerical. A snapshot
that disagrees with the live view is a funder-facing dashboard whose colours differ
from the one it was captured from. Previously the defence was a hand-port kept in
step by review; now both sides read the SAME registry through `measure_catalog`, so
there is one copy of every threshold. `PARITY.md` and `tests/test_snapshot.py` pin
the grading branches.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from connect_labs.semantic import gates

# `grade`'s answer when a measure has no row at all.
_NODATA = "nodata"
# Used only when a measure declares no `min_denominator` of its own and the spec
# names no default. The KMC render's own fallback was 25 (`var MIN_DEN = 25`).
_MIN_DEN_FALLBACK = 25
# The render joins an FLW's (opportunity, username) with this. It is the selection
# identity for the worker table, so it has to match exactly.
FLW_SEP = "::"


def band_of(direction: str | None, bands: Any, value: Any) -> str:
    """Which band a value falls in, by the measure's own direction and thresholds."""
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


def pool(
    measure: dict,
    rows: list[dict],
    *,
    min_denominator_default: int | None = None,
) -> dict:
    """One entry pooled ACROSS scope rows: sum the numerators, sum the denominators.

    Not the mean of the rates -- a mean would weight a 20-case LLO like a
    2,000-case one. Ports the render's `cPooled`.

    This cannot be reconstructed from a saved run's graded cells, which is why the
    builder computes it rather than leaving it to the render: a row whose own band
    is `insufficient` or `notinapp` still CONTRIBUTES its numerator and denominator
    here, but stores `value: null`. Pooling from stored values would silently drop
    exactly those rows and move the headline figure.
    """
    mid = measure["id"]
    out: dict[str, Any] = {"id": measure["indicator"], "n": 0, "value": None, "band": _NODATA}
    num = den = 0.0
    any_row = False
    for r in rows:
        nu, de = r.get(f"{mid}_numerator"), r.get(f"{mid}_denominator")
        if nu is None or de is None:
            continue
        any_row = True
        num += float(nu)
        den += float(de)
    if not any_row or not den:
        return out
    out["n"] = int(den)
    pct = 100.0 * num / den
    min_den = measure.get("min_denominator") or min_denominator_default or _MIN_DEN_FALLBACK
    if min_den and den < min_den:
        out["band"] = "insufficient"
        return out
    out["band"] = band_of(measure.get("direction"), measure.get("bands"), pct)
    out["value"] = pct / 100 if measure.get("unit") == "%" else pct
    return out


def scope_opps(row: dict, llo_map: dict[int, str]) -> list[int] | None:
    """Which opportunities a scope row covers, for the availability gate."""
    scope = row.get("scope")
    if scope in ("opportunity", "flw"):
        oid = row.get("opportunity_id")
        return [int(oid)] if oid is not None else None
    if scope == "llo":
        return [o for o, l in llo_map.items() if l == row.get("llo")]
    return None


def llo_of_row(row: dict, llo_map: dict[int, str]) -> str | None:
    if row.get("llo"):
        return row["llo"]
    oid = row.get("opportunity_id")
    if oid is not None:
        return llo_map.get(int(oid))
    return None


def grade(
    measure: dict,
    row: dict | None,
    *,
    llo_map: dict[int, str],
    deployment: dict,
    credibility: dict[str, dict],
    min_denominator_default: int | None = None,
) -> dict:
    """One measure's cell for one scope row. Branch order is load-bearing."""
    ind_id = measure["indicator"]
    out: dict[str, Any] = {"id": ind_id, "n": 0, "value": None, "band": _NODATA}
    if not row:
        return out

    den = row.get(f"{measure['id']}_denominator")
    out["n"] = 0 if den is None else int(float(den))

    # Credibility MARKS a figure, it does not erase it — blanking hides the
    # under-recording, since pooling every LLO reads lower than credible recorders.
    llo = llo_of_row(row, llo_map)
    credible = credibility.get(ind_id)
    not_credible = bool(llo) and credible is not None and credible.get(llo) is not True

    state = gates.input_state(measure, row, scope_opps(row, llo_map), deployment=deployment)
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

    min_den = measure.get("min_denominator") or min_denominator_default or _MIN_DEN_FALLBACK
    if out["n"] and min_den and out["n"] < min_den:
        out["band"] = "insufficient"
        return out

    out["band"] = "notcredible" if not_credible else band_of(measure.get("direction"), measure.get("bands"), rawf)
    out["value"] = rawf / 100 if measure.get("unit") == "%" else rawf

    # A thin-coverage footnote: the rate is computed over a self-selected minority
    # of the scope. Both the threshold and the measure it is a fraction OF come from
    # the registry (`meta.min_input_coverage` / `meta.coverage_denominator`). This
    # was `if ind_id == "C16"` against a 0.45 literal, which is a programme rule
    # (Neal's spec item 8) sitting in framework code — so a second indicator needing
    # the same treatment meant editing Python.
    floor = measure.get("min_input_coverage")
    base_id = measure.get("coverage_denominator")
    if floor and base_id:
        base = row.get(base_id)
        if base:
            coverage = out["n"] / float(base)
            if coverage < float(floor):
                out["thinDenominator"] = True
                out["coverage"] = coverage
    return out


def grade_all(row: dict | None, measures: list[dict], **kw) -> dict:
    """Every measure's cell for one scope row, keyed by indicator."""
    return {m["indicator"]: grade(m, row, **kw) for m in measures if m.get("indicator")}


def case_rows(pipelines: dict, spec: dict, llo_map: dict[int, str]) -> list[dict]:
    """Slim per-case records for the drill in a saved run.

    A live dashboard still holds the case rows in memory and reads only `.length`
    off the snapshot; a saved run has no live pipeline behind it, so a snapshot that
    carried no cases would dead-end the drill at the worker.

    Which pipeline and which fields are spec, not code: `case_index: {pipeline,
    fields}`. Deliberately SLIM — the per-visit weight series is excluded because a
    snapshot has a 5 MB hard cap and the series would not fit, and the longitudinal
    workflow fetches it live for the one case a user opens.
    """
    cfg = spec.get("case_index") or {}
    alias = cfg.get("pipeline")
    fields = cfg.get("fields") or []
    if not alias or not fields:
        return []
    source = ((pipelines or {}).get(alias) or {}).get("rows") or []
    out = []
    for r in source:
        oid = r.get("opportunity_id")
        rec = {k: r.get(k) for k in fields if r.get(k) is not None}
        if oid is not None:
            rec["llo"] = llo_map.get(int(oid))
        out.append(rec)
    return out


def resolve_credibility(spec: dict, settings: dict) -> dict[str, dict]:
    """indicator -> credibility table, from the spec's mapping onto registry settings.

    The mapping is spec (`credibility: {C14: mortality_recording_credible}`) and the
    tables are registry data. Neither was: three indicator ids and three settings
    keys were baked into the template's hook.
    """
    return {ind: (settings or {}).get(key) or {} for ind, key in (spec.get("credibility") or {}).items()}


def build(
    *,
    spec: dict,
    rows: list[dict],
    measures: list[dict],
    deployment: dict,
    cases: list[dict] | None = None,
    meta: dict | None = None,
    generated_at: str | None = None,
    visit_rows: list[dict] | None = None,
) -> dict:
    """Assemble the saved-run payload from evaluated semantic rows.

    `visit_rows` are the visit-level pipeline rows (spec `visits_pipeline`). The
    monthly trend counts them by VISIT month -- activity -- which is a different
    grouping from the cohort month the semantic rows are built on, so it cannot
    come out of `evaluate()` and is counted here instead.
    """
    llo_map = deployment.get("llo_map") or {}
    settings = deployment.get("settings") or {}
    credibility = resolve_credibility(spec, settings)
    grade_kw = {
        "llo_map": llo_map,
        "deployment": deployment,
        "credibility": credibility,
        "min_denominator_default": spec.get("min_denominator_default"),
    }

    by_scope: dict[str, list[dict]] = {}
    for r in rows:
        by_scope.setdefault(r.get("scope"), []).append(r)

    measures = [m for m in measures if m.get("indicator")]
    programme = (by_scope.get("programme") or [None])[0]

    by_opp = []
    for r in sorted(by_scope.get("opportunity") or [], key=lambda x: x.get("opportunity_id") or 0):
        oid = r.get("opportunity_id")
        by_opp.append(
            {
                "opp": int(oid) if oid is not None else None,
                "llo": llo_map.get(int(oid)) if oid is not None else None,
                "rows": [],
                "ind": grade_all(r, measures, **grade_kw),
                "n": int(float(r.get("n_cases") or 0)),
            }
        )

    by_llo = []
    for r in sorted(by_scope.get("llo") or [], key=lambda x: str(x.get("llo"))):
        ind = grade_all(r, measures, **grade_kw)
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
    # INDEXES into `cases`, not copies of it. Holding the case dicts here as well as
    # in the flat index stored every case TWICE: measured on a 9,011-case cohort,
    # 3.07 MB each way — 6.14 MB of a 7.0 MB payload against a 5 MB cap, so the
    # snapshot was refused outright. An index list is 0.04 MB and the render
    # rehydrates it on load, so every consumer still sees case objects.
    #
    # The reference is the POSITION, deliberately not an id: a synthetic cohort
    # reuses entity ids across cloned opportunities (751 of 8,173 ids appeared under
    # more than one opp), so an id-keyed index silently drops 838 of 9,011 cases.
    case_idx_by_flw: dict[tuple, list[int]] = {}
    for i, c in enumerate(cases):
        case_idx_by_flw.setdefault((c.get("opportunity_id"), c.get("username")), []).append(i)

    by_flw = []
    for r in by_scope.get("flw") or []:
        opp = r.get("opportunity_id")
        username = r.get("username")
        ind = grade_all(r, measures, **grade_kw)
        by_flw.append(
            {
                # The render's selection identity AND its React key
                # (`f.key === selFLW`). Without it every row's key is undefined, so
                # selecting one worker matches all of them.
                "key": f"{opp}{FLW_SEP}{username or '(unassigned)'}",
                "opp": opp,
                # `flw` is what the worker table renders and what an audit payload
                # sends; `username` is what the scope row calls it. Both, because the
                # two names are load-bearing in different places.
                "flw": username,
                "username": username,
                "llo": llo_of_row(r, llo_map),
                "rows": case_idx_by_flw.get((opp, username), []),
                "ind": ind,
                # The red/yellow badges on the worker table. by_llo computed these and
                # byFLW did not, so the badges simply did not appear on a saved run.
                "reds": sum(1 for c in ind.values() if c["band"] == "red"),
                "yellows": sum(1 for c in ind.values() if c["band"] == "yellow"),
                "n": int(float(r.get("n_cases") or 0)),
            }
        )

    # For every credibility-gated indicator, the figure pooled over the recorders
    # the workbook accepts. The programme-wide row pools EVERY LLO, so on an
    # indicator like mortality it reads lower than reality -- non-recorders
    # contribute denominator without deaths. The headline card must therefore show
    # the credible-recorder pool, and a saved run cannot rebuild it (see `pool`).
    llo_rows = by_scope.get("llo") or []
    pooled_over_credible = {}
    for m in measures:
        ind_id = m["indicator"]
        table = credibility.get(ind_id)
        if table is None:
            continue
        # An absent table means "no basis to gate", matching the render's
        # `credible === null` branch: pool everything rather than withhold it all.
        credible_rows = [r for r in llo_rows if not table or table.get(r.get("llo")) is True]
        names = [r.get("llo") for r in credible_rows if r.get("llo")]
        pooled_over_credible[ind_id] = {
            "ind": pool(m, credible_rows, min_denominator_default=spec.get("min_denominator_default"))
            if credible_rows
            else None,
            "llos": names,
            "of": len(llo_rows),
        }

    def _month(r: dict) -> str:
        return str(r.get("cohort_month") or "")[:7]

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    # Visits counted by the month they HAPPENED in, per drill scope. The render's
    # trend tab draws "babies started" (a cohort-month measure, from the semantic
    # rows) against "visits" (activity that month, from the visit rows); the two
    # are different groupings on purpose, and the second one is only derivable here.
    visit_rows = visit_rows or []
    llo_month_rows = by_scope.get("llo_month") or []

    def _visits_by_month(pred) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in visit_rows:
            if not pred(v):
                continue
            k = str(v.get("visit_date") or "")[:7]
            if k:
                out[k] = out.get(k, 0) + 1
        return out

    def _series(scope_rows, visits_pred, drilled_llo=None, drilled=False):
        """One trend point per month for one drill scope.

        Each point carries the graded indicators, the cohort size, the visit
        count, and -- for every credibility-gated indicator -- the figure pooled
        over the credible recorders that month. The pool cannot be rebuilt from
        the graded cells (see `pool`), and undrilled it spans the credible LLOs'
        rows for the month while drilled it is the scope's own row, if the
        registry does not suppress it there. Months present only in the visit
        rows still appear, as the live path's union does.
        """
        by_month: dict[str, dict] = {}
        for r in scope_rows:
            by_month.setdefault(_month(r), r)
        vcounts = _visits_by_month(visits_pred)
        out = []
        for k in sorted(k for k in set(by_month) | set(vcounts) if k):
            r = by_month.get(k)
            pooled: dict[str, dict | None] = {}
            for m in measures:
                table = credibility.get(m["indicator"])
                if table is None:
                    continue
                if not drilled:
                    rows = [
                        x for x in llo_month_rows if _month(x) == k and (not table or table.get(x.get("llo")) is True)
                    ]
                else:
                    ok = r is not None and (not table or bool(drilled_llo and table.get(drilled_llo) is True))
                    rows = [r] if ok else []
                pooled[m["indicator"]] = (
                    pool(m, rows, min_denominator_default=spec.get("min_denominator_default")) if rows else None
                )
            out.append(
                {
                    "month": k,
                    "ind": grade_all(r, measures, **grade_kw),
                    "n": int(float((r or {}).get("n_cases") or 0)),
                    "visits": vcounts.get(k, 0),
                    "pooled": pooled,
                }
            )
        return out

    monthly = _series(by_scope.get("month") or [], lambda v: True)
    # Monthly per drill scope, so a saved run still supports the LLO and opportunity
    # drill with no live pipeline behind it. Which scopes exist is spec-driven; the
    # `<prefix>:<ident>` keying is the render's contract.
    monthly_by_scope: dict[str, list[dict]] = {"all": monthly}
    for prefix, scope_name, field in (("llo:", "llo_month", "llo"), ("opp:", "opportunity_month", "opportunity_id")):
        grouped: dict = {}
        for r in by_scope.get(scope_name) or []:
            ident = r.get(field)
            if ident is None:
                continue
            grouped.setdefault(ident, []).append(r)
        for ident, rows in grouped.items():
            if field == "llo":
                pred = lambda v, i=ident: llo_map.get(_int(v.get("opportunity_id"))) == i  # noqa: E731
                drilled_llo = ident
            else:
                pred = lambda v, i=_int(ident): _int(v.get("opportunity_id")) == i  # noqa: E731
                drilled_llo = llo_map.get(_int(ident))
            monthly_by_scope[f"{prefix}{ident}"] = _series(rows, pred, drilled_llo=drilled_llo, drilled=True)

    return {
        # 3: `byFLW[].rows` carries positions into `cases`; `credibility` replaces the
        # single-purpose `mortalityCredible`; graded by the framework, not a template.
        "schema": 3,
        "generated_at": generated_at or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        # A snapshot is "the numbers as published", which has to include what
        # published them — otherwise a later band-threshold change silently
        # re-grades a published run. This is the same catalog the live view grades
        # with, so the two cannot use different thresholds.
        "cMeasures": measures,
        # Every credibility table the spec named, not just mortality's. The render
        # reads these instead of keeping its own copy.
        "credibility": credibility,
        # indicator -> {ind, llos, of}: the figure pooled over credible recorders,
        # the LLOs it pooled, and how many there were in total. The render's
        # headline card reads this; it cannot pool from the graded cells because a
        # row banded `insufficient` still contributes to the pool while storing no
        # value. Shaped as the render's own memo returns it.
        "pooledOverCredible": pooled_over_credible,
        "programInd": grade_all(programme, measures, **grade_kw),
        "byLLO": by_llo,
        "byOpp": by_opp,
        "byFLW": by_flw,
        "monthly": monthly,
        "monthlyByScope": monthly_by_scope,
        "nSeries": None,  # a template's own extra tab, captured only when it was run
        # The availability facts the gates graded with, so a saved run can explain
        # WHY a cell reads n/a without the repo it was built from.
        "deployment": {
            "llo_map": llo_map,
            "app_asks": deployment.get("app_asks") or {},
            "asks_as": deployment.get("asks_as") or {},
        },
        # Flat case index and the ONLY copy of the case records: `byFLW[].rows` holds
        # positions into this list.
        "cases": cases,
        "meta": meta or {},
    }
