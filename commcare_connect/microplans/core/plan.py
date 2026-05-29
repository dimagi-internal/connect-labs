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
        wa["excluded_reason"] = str(params.get("reason", "")).strip()
        wa["excluded_by"] = actor
    elif action == "unexclude":
        wa["status"] = STATUS_UNASSIGNED
        wa["excluded_reason"] = ""
        wa["excluded_by"] = ""
    elif action == "resize":
        wa["expected_visit_count"] = max(0, int(params["expected_visit_count"]))
    elif action == "regroup":
        wa["work_area_group"] = str(params.get("work_area_group") or "").strip()
    elif action == "reassign":
        worker = params.get("opportunity_access")
        wa["opportunity_access"] = str(worker) if worker not in (None, "") else None

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
