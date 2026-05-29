"""Planning-phase work-area editing — the LLO validation layer.

A microplan, once generated, is materialised into an editable **plan**: one work
area per generated cluster/pin, carrying the same mutable fields Connect's
``WorkArea`` tracks (status, group, assignee, expected visit count, exclusion
reason). The LLO reviews the draft and eliminates invalid areas, regroups,
resizes, and reassigns — all **before** the plan is uploaded to Connect.

Every edit appends an **audit event in Connect's pghistory shape** (the same
tracked field set), stamped ``phase="planning"`` — so the history is structurally
identical to Connect's execution-phase history and can be joined later by work
area id. Connect is untouched; nothing here is operational.

Pure (dict-in/dict-out); persistence lives in ``core.data_access``.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone

from shapely.geometry import shape

# Mirror Connect microplanning.models.WorkAreaStatus. Planning only sets
# UNASSIGNED (default) and EXCLUDED; the execution states (VISITED, …) are kept
# for value-compatibility once the plan reaches Connect.
STATUS_UNASSIGNED = "UNASSIGNED"
STATUS_EXCLUDED = "EXCLUDED"

PLANNING_PHASE = "planning"

# The fields Connect's @pghistory.track records on WorkArea. Our planning audit
# records changes to exactly these, so the two histories share one shape.
TRACKED_FIELDS = ("expected_visit_count", "work_area_group", "status", "opportunity_access", "excluded_reason")

ACTIONS = {"exclude", "unexclude", "resize", "regroup", "reassign"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _centroid(geometry: dict) -> list[float]:
    c = shape(geometry).centroid
    return [float(c.x), float(c.y)]


def _wa_id(props: dict, index: int) -> str:
    """Stable id for a work area across edits (mirrors the export slug)."""
    arm = (props.get("arm") or "intervention")[:3]
    cluster = props.get("cluster", f"c{index}")
    role = props.get("role")
    order = props.get("order_in_cluster")
    parts = [arm, str(cluster)]
    if role is not None:
        parts.append(f"{str(role)[:4]}-{order}")
    return "-".join(parts).lower()


def materialize_work_areas(mode: str, pins: dict, hulls: dict) -> list[dict]:
    """Build the editable work-area list from a generated frame.

    Coverage: one work area per cluster hull. Sampling: one tiny work area per
    pin. Each starts UNASSIGNED, grouped by its arm, with an empty audit log.
    """
    fc = hulls if mode == "coverage" else pins
    out: list[dict] = []
    for i, feat in enumerate(fc.get("features", [])):
        props = feat.get("properties", {}) or {}
        geom = feat.get("geometry")
        building_count = int(props.get("building_count", 1))
        # coverage carries expected_visit_count == building_count; sampling pin = 1 visit
        expected = int(props.get("expected_visit_count", building_count if mode == "coverage" else 1))
        out.append(
            {
                "id": _wa_id(props, i),
                "geometry": geom,
                "centroid": _centroid(geom) if geom else [0.0, 0.0],
                "building_count": building_count,
                "expected_visit_count": expected,
                "target_population": int(props.get("target_population", 0)),
                "status": STATUS_UNASSIGNED,
                "work_area_group": props.get("arm", "intervention"),  # default group = arm
                "opportunity_access": None,  # unassigned worker at planning time
                "excluded_by": "",
                "excluded_reason": "",
                "properties": dict(props),
                "audit": [],
            }
        )
    return out


def _tracked(wa: dict) -> dict:
    return {f: wa.get(f) for f in TRACKED_FIELDS}


def apply_action(wa: dict, action: str, params: dict, actor: str, now: str | None = None) -> dict:
    """Apply one edit to a work area in place and append a phase=planning audit
    event (Connect pghistory shape: old→new over the tracked fields). Returns wa."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")
    before = _tracked(wa)

    if action == "exclude":
        wa["status"] = STATUS_EXCLUDED
        wa["excluded_reason"] = str(params.get("reason", "")).strip()[:500]  # match Connect max_length
        wa["excluded_by"] = actor
    elif action == "unexclude":
        wa["status"] = STATUS_UNASSIGNED
        wa["excluded_reason"] = ""
        wa["excluded_by"] = ""
    elif action == "resize":
        try:
            wa["expected_visit_count"] = max(0, int(params["expected_visit_count"]))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError("resize requires a numeric expected_visit_count") from e
    elif action == "regroup":
        wa["work_area_group"] = str(params.get("work_area_group") or "").strip()[:255]
    elif action == "reassign":
        worker = params.get("opportunity_access")
        wa["opportunity_access"] = str(worker)[:255] if worker not in (None, "") else None

    after = _tracked(wa)
    changes = {f: [before[f], after[f]] for f in TRACKED_FIELDS if before[f] != after[f]}
    if changes:
        wa.setdefault("audit", []).append(
            {
                "ts": now or _now(),
                "actor": actor,
                "phase": PLANNING_PHASE,
                "action": action,
                "changes": changes,
            }
        )
    return wa


def find(work_areas: list[dict], wa_id: str) -> dict | None:
    return next((w for w in work_areas if w.get("id") == wa_id), None)


def summarize(work_areas: list[dict]) -> dict:
    """Headline counts for the review UI: status tallies + per-worker / per-group
    workload (active = not excluded)."""
    active = [w for w in work_areas if w.get("status") != STATUS_EXCLUDED]
    excluded = [w for w in work_areas if w.get("status") == STATUS_EXCLUDED]

    def _load(key):
        agg: dict[str, dict] = {}
        for w in active:
            k = w.get(key) or "(unassigned)"
            a = agg.setdefault(str(k), {"work_areas": 0, "buildings": 0, "expected_visits": 0})
            a["work_areas"] += 1
            a["buildings"] += int(w.get("building_count", 0))
            a["expected_visits"] += int(w.get("expected_visit_count", 0))
        return agg

    return {
        "total": len(work_areas),
        "active": len(active),
        "excluded": len(excluded),
        "buildings_active": sum(int(w.get("building_count", 0)) for w in active),
        "by_worker": _load("opportunity_access"),
        "by_group": _load("work_area_group"),
    }


_EARTH_KM = 6371.0088


def _haversine_km(p: list[float], q: list[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, [p[0], p[1], q[0], q[1]])
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    a = min(1.0, max(0.0, a))  # guard float error so asin(sqrt) can't domain-error
    return 2 * _EARTH_KM * math.asin(math.sqrt(a))


def _territory_diameter_km(centroids: list[list[float]]) -> float:
    """Max pairwise great-circle distance between an FLW's work-area centroids —
    the territory *diameter* (matches Neal's calculate_max_distance), not a radius.
    0 for a single area."""
    n = len(centroids)
    if n < 2:
        return 0.0
    return max(_haversine_km(centroids[i], centroids[j]) for i in range(n) for j in range(i + 1, n))


def _imbalance_pct(values: list[float], target: float) -> float | None:
    # (max - min) / target * 100 — Neal's pop_imbalance_pct (range vs target).
    if not values or target <= 0:
        return None
    return round((max(values) - min(values)) / target * 100, 1)


def _std(values: list[float]) -> float | None:
    return round(statistics.pstdev(values), 1) if len(values) >= 1 else None


def plan_kpis(work_areas: list[dict]) -> dict:
    """Plan-quality KPIs (Neal Lesh's microplan spec): per-FLW territory spread +
    population/building balance + exclusions.

    Territories group by ``opportunity_access`` (the worker). Before any worker is
    assigned, falls back to ``work_area_group`` so the metrics are still meaningful
    (``dimension`` says which). Excluded work areas are dropped from the active
    metrics and reported separately.
    """
    active = [w for w in work_areas if w.get("status") != STATUS_EXCLUDED]
    excluded = [w for w in work_areas if w.get("status") == STATUS_EXCLUDED]

    assigned = any(w.get("opportunity_access") for w in active)
    dimension = "worker" if assigned else "group"
    key = "opportunity_access" if assigned else "work_area_group"

    groups: dict[str, list] = {}
    for w in active:
        groups.setdefault(str(w.get(key) or "(unassigned)"), []).append(w)

    territories = []
    for name, was in sorted(groups.items()):
        cents = [w["centroid"] for w in was if w.get("centroid")]
        territories.append(
            {
                "name": name,
                "work_areas": len(was),
                "buildings": sum(int(w.get("building_count", 0)) for w in was),
                "population": sum(int(w.get("population") or 0) for w in was),
                "expected_visits": sum(int(w.get("expected_visit_count", 0)) for w in was),
                "spread_km": round(_territory_diameter_km(cents), 2),
            }
        )

    n = len(territories)
    spreads = [t["spread_km"] for t in territories]
    pops = [t["population"] for t in territories]
    blds = [t["buildings"] for t in territories]
    pop_total, bld_total = sum(pops), sum(blds)
    has_pop = pop_total > 0

    plan = {
        "dimension": dimension,
        "territory_count": n,
        # travel burden (per-FLW territory diameter)
        "max_spread_km": round(max(spreads), 2) if spreads else 0.0,
        "mean_spread_km": round(statistics.mean(spreads), 2) if spreads else 0.0,
        "min_spread_km": round(min(spreads), 2) if spreads else 0.0,
        "std_spread_km": _std(spreads),
        # workload balance — population if we have it, buildings always
        "has_population": has_pop,
        "target_population_per_unit": round(pop_total / n) if n and has_pop else 0,
        "pop_imbalance_pct": _imbalance_pct(pops, pop_total / n) if (n and has_pop) else None,
        "pop_std": _std(pops) if has_pop else None,
        "target_buildings_per_unit": round(bld_total / n) if n else 0,
        "building_imbalance_pct": _imbalance_pct(blds, bld_total / n) if n else None,
        "building_std": _std(blds),
    }

    excl_bld = sum(int(w.get("building_count", 0)) for w in excluded)
    excluded_block = {
        "count": len(excluded),
        "buildings": excl_bld,
        "population": sum(int(w.get("population") or 0) for w in excluded),
    }
    total_bld = bld_total + excl_bld
    return {
        "dimension": dimension,
        "territories": territories,
        "plan": plan,
        "excluded": excluded_block,
        "coverage_pct": round(100.0 * bld_total / total_bld, 1) if total_bld else 100.0,
    }


# Composite weights for cross-plan ranking. The source assigner never combined
# spread + balance (it optimized spread alone), so the weighting is made explicit
# here. Travel dominates; balance next; coverage rounds it out.
COMPOSITE_WEIGHTS = {"spread": 0.5, "balance": 0.3, "coverage": 0.2}


def _plan_metric_triplet(kpis: dict) -> tuple[float, float | None, float]:
    p = kpis.get("plan", {})
    balance = p.get("pop_imbalance_pct") if p.get("has_population") else p.get("building_imbalance_pct")
    return (
        float(p.get("max_spread_km") or 0.0),  # lower better
        (float(balance) if balance is not None else None),  # lower better
        float(kpis.get("coverage_pct") or 0.0),  # higher better
    )


def score_plans(entries: list[dict]) -> list[dict]:
    """Add a 0–100 composite `composite` to each entry by min-max normalising the
    rankable KPIs across the compared set (so it's only meaningful relative to the
    others). entries: [{"plan_id", "kpis", ...}]. Higher = better. With <2 plans
    the composite is None (nothing to normalise against)."""
    triplets = [_plan_metric_triplet(e.get("kpis", {})) for e in entries]
    if len(entries) < 2:
        for e in entries:
            e["composite"] = None
        return entries

    spreads = [t[0] for t in triplets]
    balances = [t[1] for t in triplets if t[1] is not None]
    covs = [t[2] for t in triplets]

    def norm(val, lo, hi, higher_better):
        if val is None or hi == lo:
            return 1.0  # all tied (or missing) → neutral-best, don't penalise
        frac = (val - lo) / (hi - lo)
        return frac if higher_better else 1 - frac

    for e, (sp, bal, cov) in zip(entries, triplets):
        ns = norm(sp, min(spreads), max(spreads), higher_better=False)
        nb = norm(bal, min(balances), max(balances), higher_better=False) if balances else 1.0
        nc = norm(cov, min(covs), max(covs), higher_better=True)
        w = COMPOSITE_WEIGHTS
        e["composite"] = round(100 * (w["spread"] * ns + w["balance"] * nb + w["coverage"] * nc), 1)
    return entries


def to_workarea_payloads(work_areas: list[dict], lga: str = "", state: str = ""):
    """Non-excluded work areas → WorkAreaPayload list (for CSV / the Connect API),
    honoring LLO edits (group→ward, expected_visit_count, exclusions)."""
    from commcare_connect.microplans.core.workarea import WorkAreaPayload

    payloads = []
    for w in work_areas:
        if w.get("status") == STATUS_EXCLUDED:
            continue
        lon, lat = w.get("centroid", [0.0, 0.0])
        cp = dict(w.get("properties", {}))
        cp.update(
            {
                "status": w.get("status", STATUS_UNASSIGNED),
                "opportunity_access": w.get("opportunity_access"),
                "lga": lga,
                "state": state,
            }
        )
        payloads.append(
            WorkAreaPayload(
                slug=w["id"],
                ward=str(w.get("work_area_group") or ""),
                centroid_lon=float(lon),
                centroid_lat=float(lat),
                boundary_wkt=shape(w["geometry"]).wkt if w.get("geometry") else "",
                building_count=int(w.get("building_count", 0)),
                expected_visit_count=int(w.get("expected_visit_count", 0)),
                target_population=int(w.get("target_population", 0)),
                case_properties=cp,
            )
        )
    return payloads
