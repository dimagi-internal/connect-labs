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

from shapely.geometry import mapping, shape

# Mirror Connect microplanning.models.WorkAreaStatus. Planning only sets
# UNASSIGNED (default) and EXCLUDED; the execution states (VISITED, …) are kept
# for value-compatibility once the plan reaches Connect.
STATUS_UNASSIGNED = "UNASSIGNED"
STATUS_EXCLUDED = "EXCLUDED"

PLANNING_PHASE = "planning"

# The fields Connect's @pghistory.track records on WorkArea. Our planning audit
# records changes to exactly these, so the two histories share one shape.
TRACKED_FIELDS = (
    "expected_visit_count",
    "work_area_group",
    "status",
    "opportunity_access",
    "excluded_reason",
)

ACTIONS = {"exclude", "unexclude", "resize", "regroup", "reassign"}

# ---- plan-level lifecycle (distinct from the per-work-area status above) ----
# A plan is a candidate region within a program. It moves through Planning
# (draft → in_review → approved) — labs owns this — and becomes Live (deployed)
# when pushed to a Connect opp, which Connect then executes. Archived = a
# candidate region not chosen.
PLAN_DRAFT = "draft"
PLAN_IN_REVIEW = "in_review"
PLAN_APPROVED = "approved"
PLAN_DEPLOYED = "deployed"
PLAN_ARCHIVED = "archived"
PLAN_STATUSES = (
    PLAN_DRAFT,
    PLAN_IN_REVIEW,
    PLAN_APPROVED,
    PLAN_DEPLOYED,
    PLAN_ARCHIVED,
)
PLANNING_STATUSES = (PLAN_DRAFT, PLAN_IN_REVIEW, PLAN_APPROVED)
PLAN_STATUS_LABELS = {
    PLAN_DRAFT: "Draft",
    PLAN_IN_REVIEW: "In review",
    PLAN_APPROVED: "Approved",
    PLAN_DEPLOYED: "Deployed",
    PLAN_ARCHIVED: "Archived",
}
# Allowed transitions. Planning states move freely among themselves; archive is
# reachable from any planning state and reversible to draft; deploy is the
# one-way Planning→Live handoff (and requires a bound opportunity).
PLAN_TRANSITIONS = {
    PLAN_DRAFT: {PLAN_IN_REVIEW, PLAN_ARCHIVED},
    PLAN_IN_REVIEW: {PLAN_DRAFT, PLAN_APPROVED, PLAN_ARCHIVED},
    PLAN_APPROVED: {PLAN_IN_REVIEW, PLAN_DEPLOYED, PLAN_ARCHIVED},
    PLAN_DEPLOYED: set(),  # terminal in labs — execution lives in Connect
    PLAN_ARCHIVED: {PLAN_DRAFT},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def can_transition(frm: str, to: str) -> bool:
    return to in PLAN_TRANSITIONS.get(frm, set())


def transition_plan(
    plan_data: dict, to: str, actor: str, opportunity_id=None, now: str | None = None
) -> dict:
    """Move a plan to status ``to`` in place, appending a status_log entry.
    Deploying requires an opportunity_id (the live Connect opp the plan binds to).
    Raises ValueError on an illegal transition."""
    frm = plan_data.get("status", PLAN_DRAFT)
    if to not in PLAN_STATUSES:
        raise ValueError(f"unknown status: {to}")
    if to != frm and not can_transition(frm, to):
        raise ValueError(f"illegal transition {frm} -> {to}")
    if to == PLAN_DEPLOYED:
        opp = opportunity_id or plan_data.get("opportunity_id")
        if not opp:
            raise ValueError(
                "deploying requires an opportunity_id (the live Connect opp)"
            )
        plan_data["opportunity_id"] = opp
    plan_data["status"] = to
    plan_data.setdefault("status_log", []).append(
        {
            "ts": now or _now(),
            "actor": actor,
            "from": frm,
            "to": to,
            "phase": PLANNING_PHASE
            if to in PLANNING_STATUSES
            else ("deploy" if to == PLAN_DEPLOYED else to),
        }
    )
    return plan_data


def _centroid(geometry: dict) -> list[float]:
    try:
        c = shape(geometry).centroid
        if c.is_empty:
            return [0.0, 0.0]
        return [float(c.x), float(c.y)]
    except Exception:  # noqa: BLE001
        return [0.0, 0.0]


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


def materialize_work_areas(
    mode: str, pins: dict, hulls: dict, grouping: dict | None = None
) -> list[dict]:
    """Build the editable work-area list from a generated frame.

    Coverage: one work area per grid cell, auto-grouped via ``grouping`` (default
    BFS adjacency — Connect-GIS-style, building-balanced). Sampling: one tiny
    work area per pin, grouped by arm. Each starts UNASSIGNED with an empty
    audit log.

    ``grouping`` is a config dict consumed by ``grouping.GroupingConfig.from_payload``.
    Defaults to BFS adjacency for coverage (Connect-GIS parity); ``None`` falls back
    to that default.
    """
    from commcare_connect.microplans.core import grouping as grouping_lib
    from commcare_connect.microplans.core.workarea import footprint_boundary_shape

    fc = hulls if mode == "coverage" else pins
    out: list[dict] = []
    # Sampling groups neutrally by PSU (arm + cluster → an arm-free "PSU N" label) so
    # the study arm never appears as a group. Arm itself is stored as a LABS-SIDE
    # field (and stripped from the shared `properties`), keeping the LLO review +
    # any Connect push blind to which arm a work area belongs to.
    psu_group: dict = {}
    for i, feat in enumerate(fc.get("features", [])):
        props = feat.get("properties", {}) or {}
        geom = feat.get("geometry")
        # Sampling pins arrive as Points. The WorkArea an FLW receives must be a
        # polygon, so we swap each pin for its real building footprint (lightly
        # buffered), falling back to a small square when the pin has no footprint.
        # (Coverage cells are already polygons — leave them be.)
        if mode != "coverage" and geom and geom.get("type") == "Point":
            lon, lat = geom["coordinates"]
            geom = mapping(
                footprint_boundary_shape(props.get("geom_json"), lon, lat, 3.0, 8.0)
            )
        building_count = int(props.get("building_count", 1))
        # coverage carries expected_visit_count == building_count; sampling pin = 1 visit
        expected = int(
            props.get(
                "expected_visit_count", building_count if mode == "coverage" else 1
            )
        )
        arm = props.get("arm", "")
        if mode == "coverage":
            group = props.get(
                "arm", "intervention"
            )  # placeholder; overridden by grouping below
        else:
            key = (arm, props.get("cluster", ""))
            if key not in psu_group:
                psu_group[key] = f"PSU {len(psu_group) + 1}"
            group = psu_group[key]
        out.append(
            {
                "id": _wa_id(props, i),
                "geometry": geom,
                "centroid": _centroid(geom) if geom else [0.0, 0.0],
                "building_count": building_count,
                "expected_visit_count": expected,
                "target_population": int(props.get("target_population", 0)),
                "status": STATUS_UNASSIGNED,
                "work_area_group": group,
                "arm": arm,  # labs-side analysis metadata only — never shared/pushed
                "opportunity_access": None,  # unassigned worker at planning time
                "excluded_by": "",
                "excluded_reason": "",
                # geom_json was consumed into `geometry` above; drop it from the
                # shared bucket (no raw footprint duplicated into Connect-facing props).
                "properties": {
                    k: v for k, v in props.items() if k not in ("arm", "geom_json")
                },
                "audit": [],
            }
        )
    if mode == "coverage" and out:
        cfg = grouping_lib.GroupingConfig.from_payload(grouping or {})
        grouping_lib.group_work_areas(out, cfg)
    return out


def _tracked(wa: dict) -> dict:
    return {f: wa.get(f) for f in TRACKED_FIELDS}


def apply_action(
    wa: dict, action: str, params: dict, actor: str, now: str | None = None
) -> dict:
    """Apply one edit to a work area in place and append a phase=planning audit
    event (Connect pghistory shape: old→new over the tracked fields). Returns wa."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")
    before = _tracked(wa)

    if action == "exclude":
        wa["status"] = STATUS_EXCLUDED
        wa["excluded_reason"] = str(params.get("reason", "")).strip()[
            :500
        ]  # match Connect max_length
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
        # Reassign only touches opportunity_access. Work-area-group is independent:
        # the LLO may pre-group cells (regroup) then assign CHWs to groups, or skip
        # grouping entirely (color-by-CHW falls back to opportunity_access on the map).
        worker = params.get("opportunity_access")
        wa["opportunity_access"] = (
            str(worker)[:255] if worker not in (None, "") else None
        )

    after = _tracked(wa)
    changes = {
        f: [before[f], after[f]] for f in TRACKED_FIELDS if before[f] != after[f]
    }
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
            a = agg.setdefault(
                str(k), {"work_areas": 0, "buildings": 0, "expected_visits": 0}
            )
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
    # Centroids are server-computed [lon, lat] floats, but guard malformed/short
    # input so one bad work area can't blow up the whole plan's KPI computation.
    try:
        lon1, lat1, lon2, lat2 = map(
            math.radians, [float(p[0]), float(p[1]), float(q[0]), float(q[1])]
        )
    except (TypeError, ValueError, IndexError):
        return 0.0
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    a = min(1.0, max(0.0, a))  # guard float error so asin(sqrt) can't domain-error
    return 2 * _EARTH_KM * math.asin(math.sqrt(a))


def _territory_diameter_km(centroids: list[list[float]]) -> float:
    """Max pairwise great-circle distance between an FLW's work-area centroids —
    the territory *diameter* (matches Neal's calculate_max_distance), not a radius.
    0 for a single area.

    The diameter always lies between two convex-hull vertices, so for large
    territories we hull-reduce first and do the O(h²) pairwise max over the few
    hull points instead of O(n²) over every cell — pre-assignment a coverage plan
    collapses to one territory of up to MAX_WORK_AREAS centroids, and this runs
    per-plan on every workspace load. Result is identical (exact, not sampled)."""
    n = len(centroids)
    if n < 2:
        return 0.0
    pts = centroids
    if n > 50:
        try:
            from shapely.geometry import MultiPoint

            hull = MultiPoint(
                [(float(c[0]), float(c[1])) for c in centroids]
            ).convex_hull
            if hull.geom_type == "Polygon":
                pts = [list(xy) for xy in hull.exterior.coords]
            elif hull.geom_type == "LineString":
                pts = [list(xy) for xy in hull.coords]
            elif hull.geom_type == "Point":
                return 0.0  # all coincident
        except Exception:  # noqa: BLE001 — never let a hull edge case break KPIs
            pts = centroids
    m = len(pts)
    if m < 2:
        return 0.0
    return max(_haversine_km(pts[i], pts[j]) for i in range(m) for j in range(i + 1, m))


def _imbalance_pct(values: list[float], target: float) -> float | None:
    # (max - min) / target * 100 — Neal's pop_imbalance_pct (range vs target).
    if not values or target <= 0:
        return None
    return round((max(values) - min(values)) / target * 100, 1)


def _std(values: list[float]) -> float | None:
    return round(statistics.pstdev(values), 1) if len(values) >= 1 else None


def plan_kpis(
    work_areas: list[dict],
    input_areas: list[dict] | None = None,
    area_buildings: int | None = None,
) -> dict:
    """Plan-quality KPIs (Neal Lesh's microplan spec): per-FLW territory spread +
    population/building balance + exclusions.

    ``input_areas`` is the plan's source admin boundaries (or None for legacy plans
    that were created before populations were tracked). When supplied, the plan's
    ``total_population`` reports the sum of the source boundaries' population
    estimates — the "real" area population, not a bottom-up sum of per-work-area
    apportionments. The per-territory population balance math still uses the
    work-area population field (that's a different concept — population assigned
    to each worker).

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
                "expected_visits": sum(
                    int(w.get("expected_visit_count", 0)) for w in was
                ),
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
        "pop_imbalance_pct": _imbalance_pct(pops, pop_total / n)
        if (n and has_pop)
        else None,
        "pop_std": _std(pops) if has_pop else None,
        "target_buildings_per_unit": round(bld_total / n) if n else 0,
        "building_imbalance_pct": _imbalance_pct(blds, bld_total / n) if n else None,
        "building_std": _std(blds),
        # Plan-level workload totals (active only; excluded reported separately
        # in the `excluded` block below). Surfaced on the compare-page table so
        # the reader can see the underlying workload alongside the derived
        # spread / balance / coverage metrics. `pop_per_building` is the
        # per-structure population estimate — a sanity check on the input.
        #
        # `total_population` reads the source admin boundary population from
        # `input_areas` when available (top-down — "the area's known
        # population"). Falls back to the bottom-up sum of work-area
        # populations for legacy plans that don't carry boundary population
        # on input_areas yet.
        "total_population": (
            sum(
                int(ia.get("population") or 0)
                for ia in (input_areas or [])
                if isinstance(ia, dict)
            )
            if input_areas
            and any(isinstance(ia, dict) and ia.get("population") for ia in input_areas)
            else pop_total
        ),
        # "Buildings" + "Pop / building" describe the AREA's footprint universe (the
        # per-structure population sanity check). For a COVERAGE plan that's bld_total
        # — the work areas tile the whole area, so summing their building_count gives
        # the area total. For a SAMPLING plan the work areas are sampled pins
        # (building_count 1 each), so bld_total is the SAMPLE SIZE, not the area's
        # buildings; dividing whole-area population by it gave nonsense (e.g. 57
        # people/"building"). The caller passes `area_buildings` — the footprint count
        # the sample was drawn from — so this stays a real per-structure estimate.
        # (Per-worker building balance below still uses bld_total: a worker's workload
        # is the sampled buildings assigned to them, not the area's whole footprint set.)
        "sampled_buildings": bld_total,
        "total_buildings": area_buildings if area_buildings else bld_total,
        "pop_per_building": None,  # set after total_population is known, below
    }
    if plan["total_buildings"]:
        plan["pop_per_building"] = round(
            plan["total_population"] / plan["total_buildings"], 2
        )

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


# Composite "fit score" was tried and removed — it forced travel/balance/coverage
# into a single weighted number that read as a black box. The honest comparison is
# the metrics themselves: worst travel, workload imbalance, coverage, exclusions.
# The share + compare UIs show those directly and let the reader decide.


def derive_lga_state(plan_data: dict) -> tuple[str, str]:
    """Best (LGA, State) labels for a plan's Connect work-area export.

    ⚠ NIGERIA-HARDCODED: "LGA"/"State" are Nigeria's ADM2/ADM1 tiers, hardcoded to
    match Connect's importer column names. Labs is country-generic internally
    (canonical admin levels) — generalize this (and ``workarea.CSV_HEADERS``,
    ``models.PlanRecord.lga/state``) once Connect generalizes its importer. See
    the note on ``workarea.CSV_HEADERS`` and ``microplans/CONNECT_IMPORT_CONTRACT.md``.

    Connect's WorkAreaCSVImporter REQUIRES both LGA and State to be non-empty on
    every row (see ``microplans/CONNECT_IMPORT_CONTRACT.md``); a blank value gets
    the whole file rejected. We resolve them from the plan with this precedence:

      1. explicit ``lga`` / ``state`` stored on the plan (captured at creation),
      2. the plan's ``region`` label as a fallback for LGA (the region a plan is
         drawn from is, in practice, its LGA — e.g. "Kano North LGA"),
      3. empty string if nothing is known (State has no safe fallback — callers
         must surface that rather than invent a value).

    Returned values are stripped. State may still be "" for plans created before
    it was captured; callers should treat an empty State as "must be supplied".
    """
    lga = (plan_data.get("lga") or plan_data.get("region") or "").strip()
    state = (plan_data.get("state") or "").strip()
    return lga, state


def plan_sample_areas(input_areas: list, arm, resolve_boundary) -> list[dict]:
    """Turn a plan's stored ``input_areas`` into the ``[{arm, geometry}]`` list the
    sampling engine consumes, tagging every area with the plan's study ``arm``.

    Areas drawn/pinned already carry an inline ``geometry``; admin-boundary areas
    carry only a ``boundary_id`` (the engine's ``resolve_area`` can't read that), so
    ``resolve_boundary(boundary_id) -> geojson | None`` is injected to fetch the
    polygon (a DB lookup in production, a stub in tests). Unresolvable areas are
    skipped."""
    out = []
    for a in input_areas or []:
        if a.get("geometry"):
            out.append({"arm": arm, "geometry": a["geometry"]})
        elif a.get("boundary_id"):
            geom = resolve_boundary(a["boundary_id"])
            if geom:
                out.append({"arm": arm, "geometry": geom})
    return out


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
