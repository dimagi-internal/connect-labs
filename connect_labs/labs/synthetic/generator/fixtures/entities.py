"""Per-entity longitudinal planning for the generator (issue #713).

Turns a cohort's ``LongitudinalSpec`` into an ordered list of ``PlannedVisit``s —
each tagged with a stable ``entity_id``, its owner FLW, visit date, visit index,
and the numeric field values for that visit. The engine builds full visit dicts
from these. Keeping the planning pure makes the longitudinal logic testable in
isolation from visit-dict assembly.

Mirror mode replays real de-identified case series (exact owner/timing/count,
jittered values clamped to each case's own range). Net-new (synthetic) trajectory
planning lands here too in a later step.
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from dataclasses import dataclass
from typing import Any

from .manifest import LongitudinalSpec


@dataclass(frozen=True)
class PlannedVisit:
    entity_id: str
    entity_name: str
    beneficiary_idx: int  # 1-based; indexes household GPS placement
    owner: str  # persona id
    visit_date: dt.date
    visit_index: int  # 1-based position within this entity's series
    # Field overrides for this visit: numeric measures as floats, date leaves as
    # reconstructed ISO date strings (e.g. a constant child_dob). fill_form_json
    # writes both directly, bypassing the marginal draws.
    forced_values: dict[str, Any]


def _series_ranges(visits: list[dict]) -> dict[str, tuple[float, float]]:
    """Per-field (min, max) across a case's own series, for jitter clamping."""
    acc: dict[str, list[float]] = {}
    for v in visits:
        for path, val in (v.get("values") or {}).items():
            acc.setdefault(path, []).append(float(val))
    return {path: (min(vals), max(vals)) for path, vals in acc.items()}


def _time_varying_paths(pool: list[dict], *, min_cases: int = 5, vary_share: float = 0.10) -> set[str]:
    """Paths the COHORT shows to be time-varying, so they are never overlaid as constants.

    ``_series_constants`` decides constancy from one entity's own visits, where a path
    recorded exactly ONCE is trivially "identical everywhere it appears". That is right
    for a registration-only attribute (DOB, birth weight) and wrong for a clinical
    measure the child happened to have taken once: stamping that single reading onto
    every visit invents readings and flattens the child's curve — on the KMC cohort,
    18.9% of babies with a visit-weight had exactly one, producing 1,433 phantom weights,
    1,183 fake-flat growth series, and a clone that over-reported weight-consistency
    while under-reporting growth velocity (connect-labs#1189).

    Evidence beats per-entity coincidence: look across every entity that recorded the
    path at least twice. If a meaningful share of those vary, the path is time-varying
    for this programme and stays strictly per-visit. Absent evidence (no entity ever
    recorded it twice — the DOB case) the path keeps the old constant treatment, so
    #734's age-vs-weight anchor is preserved.
    """
    multi: dict[str, int] = {}
    varied: dict[str, int] = {}
    for series in pool:
        seen: dict[str, set[Any]] = {}
        for v in series.get("visits") or []:
            for path, val in (v.get("values") or {}).items():
                try:
                    seen.setdefault(path, set()).add(float(val))
                except (TypeError, ValueError):
                    continue
            # Categoricals get the same treatment: child_alive flipping to "no"
            # is time-varying and must never be stamped back across earlier visits.
            for path, val in (v.get("cats") or {}).items():
                seen.setdefault(path, set()).add(str(val))
        counts: dict[str, int] = {}
        for v in series.get("visits") or []:
            for path in list(v.get("values") or {}) + list(v.get("cats") or {}):
                counts[path] = counts.get(path, 0) + 1
        for path, n in counts.items():
            if n >= 2:
                multi[path] = multi.get(path, 0) + 1
                if len(seen.get(path, ())) > 1:
                    varied[path] = varied.get(path, 0) + 1
    return {path for path, n in multi.items() if n >= min_cases and (varied.get(path, 0) / n) >= vary_share}


def _series_constants(
    visits: list[dict], time_varying: set[str] | None = None
) -> tuple[dict[str, float], dict[str, int], dict[str, str]]:
    """Per-entity constant numeric values and date offsets to propagate to EVERY visit.

    A real KMC child records its birth weight and DOB only at registration, so a
    faithful per-visit replay leaves follow-up visits without them — and the field
    filler then fabricates a *different* value each visit, so the child's DOB/birth
    weight wobble across its own visits and an age-vs-weight curve collapses (#734).

    A field whose every *recorded* value across the entity's visits is identical is a
    per-child constant (birth weight, DOB, sex); we carry it onto all the entity's
    visits so age = visit_date - dob and the birth-weight anchor hold on every row. A
    time-varying measure (weight, MUAC) differs visit to visit and is never constant,
    so it is left strictly per-visit. Numerics compare on raw value; dates on their
    day-offset from the entity's first visit (a constant DOB → one offset → one date)."""
    val_seen: dict[str, set[float]] = {}
    date_seen: dict[str, set[int]] = {}
    cat_seen: dict[str, set[str]] = {}
    for v in visits:
        for path, val in (v.get("values") or {}).items():
            val_seen.setdefault(path, set()).add(float(val))
        for path, off in (v.get("dates") or {}).items():
            date_seen.setdefault(path, set()).add(int(off))
        for path, cval in (v.get("cats") or {}).items():
            cat_seen.setdefault(path, set()).add(str(cval))
    tv = time_varying or set()
    const_values = {p: next(iter(s)) for p, s in val_seen.items() if len(s) == 1 and p not in tv}
    const_dates = {p: next(iter(s)) for p, s in date_seen.items() if len(s) == 1 and p not in tv}
    # A categorical answered once and never contradicted (sex, birth location) is a
    # per-child constant; one that changes is excluded by `time_varying` above.
    const_cats = {p: next(iter(s)) for p, s in cat_seen.items() if len(s) == 1 and p not in tv}
    return const_values, const_dates, const_cats


def plan_mirror_visits(spec: LongitudinalSpec, *, seed: int) -> list[PlannedVisit]:
    """Replay each transplanted case as a stable entity.

    One entity per pool series: same owner, same first-visit date + day offsets
    (so visits/case, cases/FLW, and timing match the source exactly), with each
    numeric value jittered by ``jitter_frac`` of that field's range *within this
    case* and clamped back into that range, so a clone stays physiologically
    plausible per case while not being a verbatim copy.
    """
    rng = random.Random(seed ^ 0x713C10E)
    time_varying = _time_varying_paths(spec.transplant_pool)
    planned: list[PlannedVisit] = []
    for idx, series in enumerate(spec.transplant_pool, start=1):
        entity_id = str(uuid.UUID(int=rng.getrandbits(128)))  # one stable id per case
        entity_name = f"Beneficiary {idx}"
        owner = series["owner"]
        start = dt.date.fromisoformat(series["start_date"])
        series_visits = series["visits"]
        ranges = _series_ranges(series_visits)
        series_form_names = [v["form"] for v in series_visits if v.get("form")]
        const_values, const_dates, const_cats = _series_constants(series_visits, time_varying)
        for vj, visit in enumerate(sorted(series_visits, key=lambda v: v["day"]), start=1):
            vdate = start + dt.timedelta(days=int(visit["day"]))
            forced: dict[str, Any] = {}
            for path, val in (visit.get("values") or {}).items():
                lo, hi = ranges[path]
                span = hi - lo
                if span > 0 and spec.jitter_frac > 0:
                    jittered = float(val) + rng.gauss(0.0, spec.jitter_frac * span)
                    forced[path] = min(max(jittered, lo), hi)
                else:
                    forced[path] = float(val)
            # Date leaves are reconstructed as real ISO dates from their day-offset
            # (relative to this entity's first visit) and never jittered — a constant
            # DOB stays constant, so age = visit_date - dob is exact across the series.
            for path, offset in (visit.get("dates") or {}).items():
                forced[path] = (start + dt.timedelta(days=int(offset))).isoformat()
            # Categoricals are replayed VERBATIM — never jittered, never re-drawn.
            # These are the outcomes (child_alive, kmc_status, danger signs), and
            # re-drawing them from marginals is what decoupled a baby's death from
            # its own weight trajectory, FLW and timing.
            for path, cval in (visit.get("cats") or {}).items():
                forced[path] = cval
            # Overlay per-entity constants onto every visit — including ones where the
            # source recorded them only at registration — so birth weight and DOB are
            # identical across the child's whole series (kept exact, never jittered).
            for path, cval in const_values.items():
                forced[path] = cval
            for path, coff in const_dates.items():
                forced[path] = (start + dt.timedelta(days=int(coff))).isoformat()
            for path, cval in const_cats.items():
                forced[path] = cval
            # Structural fidelity. Clones carried no case block and no form name at
            # all — form.@name, form.case.@case_id and form.subcase_0.case.@case_id
            # were null on every synthetic row. That makes a whole class of pipeline
            # logic untestable on synthetic data, and it is the reason every
            # entity-join defect this cohort hit had to be found against production
            # (connect-labs#1224). Emit the same shape the source has:
            #   form.case.@case_id      the beneficiary, stable across its series
            #   form.subcase_0...       a per-visit case, distinct every visit
            if series_form_names:
                forced["form.@name"] = visit.get("form") or series_form_names[0]
            forced["form.case.@case_id"] = entity_id
            forced["form.subcase_0.case.@case_id"] = str(uuid.UUID(int=rng.getrandbits(128)))
            planned.append(PlannedVisit(entity_id, entity_name, idx, owner, vdate, vj, forced))
    return planned
