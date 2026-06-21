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

from .manifest import LongitudinalSpec


@dataclass(frozen=True)
class PlannedVisit:
    entity_id: str
    entity_name: str
    beneficiary_idx: int  # 1-based; indexes household GPS placement
    owner: str  # persona id
    visit_date: dt.date
    visit_index: int  # 1-based position within this entity's series
    forced_values: dict[str, float]  # numeric field overrides for this visit


def _series_ranges(visits: list[dict]) -> dict[str, tuple[float, float]]:
    """Per-field (min, max) across a case's own series, for jitter clamping."""
    acc: dict[str, list[float]] = {}
    for v in visits:
        for path, val in (v.get("values") or {}).items():
            acc.setdefault(path, []).append(float(val))
    return {path: (min(vals), max(vals)) for path, vals in acc.items()}


def plan_mirror_visits(spec: LongitudinalSpec, *, seed: int) -> list[PlannedVisit]:
    """Replay each transplanted case as a stable entity.

    One entity per pool series: same owner, same first-visit date + day offsets
    (so visits/case, cases/FLW, and timing match the source exactly), with each
    numeric value jittered by ``jitter_frac`` of that field's range *within this
    case* and clamped back into that range, so a clone stays physiologically
    plausible per case while not being a verbatim copy.
    """
    rng = random.Random(seed ^ 0x713C10E)
    planned: list[PlannedVisit] = []
    for idx, series in enumerate(spec.transplant_pool, start=1):
        entity_id = str(uuid.UUID(int=rng.getrandbits(128)))  # one stable id per case
        entity_name = f"Beneficiary {idx}"
        owner = series["owner"]
        start = dt.date.fromisoformat(series["start_date"])
        series_visits = series["visits"]
        ranges = _series_ranges(series_visits)
        for vj, visit in enumerate(sorted(series_visits, key=lambda v: v["day"]), start=1):
            vdate = start + dt.timedelta(days=int(visit["day"]))
            forced: dict[str, float] = {}
            for path, val in (visit.get("values") or {}).items():
                lo, hi = ranges[path]
                span = hi - lo
                if span > 0 and spec.jitter_frac > 0:
                    jittered = float(val) + rng.gauss(0.0, spec.jitter_frac * span)
                    forced[path] = min(max(jittered, lo), hi)
                else:
                    forced[path] = float(val)
            planned.append(PlannedVisit(entity_id, entity_name, idx, owner, vdate, vj, forced))
    return planned
