"""Phase 2's execution-gap indicators (design brief §5) and cluster-aware
detection (§6) — pure functions over plain per-work-area dicts, no network/DB,
so this is the piece that gets real `pytest` coverage against synthetic data
instead of manual live-browser checks (the brief's core argument for a Django
rebuild over the old Workflow dashboard).

Expected per-work-area input shape (one dict per WA; upstream data-access
layers are responsible for populating these — this module only computes):

    {
        "wa_id": str,
        "ward": str, "lga": str, "state": str,
        "flw_username": str,          # owner, for whole-FLW-average + §6b
        "lat": float, "lon": float,   # centroid, for §6a's neighbor graph
        "status": str,                # NOT_VISITED/VISITED/EXPECTED_VISIT_REACHED/
                                       # REQUEST_FOR_INACCESSIBLE/INACCESSIBLE
        "building_count": int,
        "expected_visit_count": int,
        "approved_hsd_count": int,
        "approved_ncf_count": int,
        "approved_inaccessible_count": int,
        "deworming_given": int,       # of approved_hsd_count visits
        "muac_given": int,
        "vaccination_given": int,
        "boundary": dict | None,      # optional — GeoJSON, carried through onto
                                       # any resulting candidate, unused by the
                                       # math itself (see evaluate_run's own
                                       # comment on why)
    }

Every rate the three data-quality metrics compute shares `approved_hsd_count`
as its denominator (§5's explicit "share the same denominator" rule); NCF/
inaccessible shares total visits (HSD+NCF+Inaccessible) as its denominator;
EVC shortfall's denominator is `expected_visit_count`.

NCF/inaccessible is special-cased under `cluster_aware`/`flw_average` (see
`_ncf_affected`/`flw_average_rate`/`evaluate_run`): a work area only ever logs
ONE NCF-or-Inaccessible visit, never a mix and never more than one, so a
visit-mix RATIO isn't the right unit to corroborate across a population of
work areas the way it is for `wa_only`. Those two granularities instead work
in terms of "is/was this work area affected at all" (a WA-count-based
fraction or count), not "what fraction of this WA's own visits were bad."
`wa_only` keeps the original ratio unchanged.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Indicator definitions — direction is inherent to what's being measured, not
# user-configurable (only the threshold VALUE and granularity are, per §6's
# control inventory).
# ---------------------------------------------------------------------------

EVC_SHORTFALL = "evc_shortfall"
NCF_INACCESSIBLE = "ncf_inaccessible_rate"
DEWORMING = "deworming"
MUAC = "muac"
VACCINATION = "vaccination"

ALL_INDICATORS = [EVC_SHORTFALL, NCF_INACCESSIBLE, DEWORMING, MUAC, VACCINATION]

# Candidate provenance discriminator (Phase 3's carry-forward hand-off keys
# off this): every candidate `evaluate_run` produces today comes from a
# flagged EXISTING work area. A future planning-gap candidate type (brief §7,
# not yet built) would use SOURCE_PLANNING_GAP so both can share one table.
SOURCE_EXISTING_WA = "existing_wa"

# "below" = flagged when the rate is BELOW threshold (a shortfall);
# "above" = flagged when the rate is ABOVE threshold (too much of a bad thing).
_DIRECTION = {
    EVC_SHORTFALL: "below",
    NCF_INACCESSIBLE: "above",
    DEWORMING: "below",
    MUAC: "below",
    VACCINATION: "below",
}

_DQ_INDICATORS = {DEWORMING, MUAC, VACCINATION}

# Concluded statuses for the not-yet-visited EVC exclusion (§5). A WA still
# NOT_VISITED or with a pending REQUEST_FOR_INACCESSIBLE hasn't necessarily
# failed — the campaign may just not have reached it yet.
_CONCLUDED_STATUSES = {"VISITED", "EXPECTED_VISIT_REACHED", "INACCESSIBLE"}

GRANULARITY_WA_ONLY = "wa_only"
GRANULARITY_CLUSTER_AWARE = "cluster_aware"
GRANULARITY_FLW_AVERAGE = "flw_average"


def _safe_div(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return numerator / denominator


def _dq_given_count(wa: dict, indicator_key: str) -> int:
    return {
        DEWORMING: wa.get("deworming_given", 0),
        MUAC: wa.get("muac_given", 0),
        VACCINATION: wa.get("vaccination_given", 0),
    }[indicator_key]


def wa_numerator_denominator(wa: dict, indicator_key: str, global_config: dict) -> tuple[float, float] | None:
    """This WA's own (numerator, denominator) for `indicator_key`, or `None`
    if the indicator doesn't apply / isn't trustworthy for this WA (gated
    out). `wa_rate` derives its ratio from this so the gating logic lives in
    exactly one place; the raw pair is also what the candidate table shows
    next to each triggered indicator (e.g. "deworming (0.2; 2/10)")."""
    if indicator_key == EVC_SHORTFALL:
        if wa.get("status") not in _CONCLUDED_STATUSES and not global_config.get("include_not_yet_visited", False):
            return None
        return wa.get("approved_hsd_count", 0), wa.get("expected_visit_count", 0)

    if indicator_key == NCF_INACCESSIBLE:
        min_buildings = global_config.get("min_building_count", 1)
        if wa.get("building_count", 0) < min_buildings:
            return None
        total_visits = (
            wa.get("approved_hsd_count", 0)
            + wa.get("approved_ncf_count", 0)
            + wa.get("approved_inaccessible_count", 0)
        )
        return wa.get("approved_ncf_count", 0) + wa.get("approved_inaccessible_count", 0), total_visits

    if indicator_key in _DQ_INDICATORS:
        min_hsd = global_config.get("min_hsd_visits_floor", 1)
        hsd_count = wa.get("approved_hsd_count", 0)
        if hsd_count < min_hsd:
            return None
        return _dq_given_count(wa, indicator_key), hsd_count

    raise ValueError(f"unknown indicator: {indicator_key!r}")


def wa_rate(wa: dict, indicator_key: str, global_config: dict) -> float | None:
    """This WA's own rate for `indicator_key`, or `None` if the indicator
    doesn't apply / isn't trustworthy for this WA (gated out — never a guess,
    matching `ward_children_per_building`'s "0.0 is a real answer, None is
    'can't compute'" convention elsewhere in this app)."""
    pair = wa_numerator_denominator(wa, indicator_key, global_config)
    if pair is None:
        return None
    return _safe_div(*pair)


def is_flagged(rate: float | None, threshold: float, indicator_key: str) -> bool:
    if rate is None:
        return False
    direction = _DIRECTION[indicator_key]
    return rate < threshold if direction == "below" else rate > threshold


def _ncf_visit_total(wa: dict) -> int:
    return wa.get("approved_ncf_count", 0) + wa.get("approved_inaccessible_count", 0)


def _ncf_affected(wa: dict, global_config: dict) -> bool | None:
    """Does this WA have any NCF-or-Inaccessible visit at all? `None` if
    gated out by `min_building_count` (same gate `wa_rate`'s NCF branch uses)
    — never a guess. This is the unit `cluster_aware`/`flw_average` corroborate
    for NCF/inaccessible (see the module docstring): a WA logs at most ONE
    such visit ever, so presence/absence is the natural signal, not a ratio."""
    min_buildings = global_config.get("min_building_count", 1)
    if wa.get("building_count", 0) < min_buildings:
        return None
    return _ncf_visit_total(wa) > 0


def ncf_neighbor_affected_count(wa: dict, neighbor_ids: list[str], by_id: dict[str, dict], global_config: dict) -> int:
    """How many of `wa`'s spatial neighbors are themselves NCF/inaccessible
    -affected (per `_ncf_affected`) — compared against a raw count setting
    (`min_affected_neighbors_ncf`), not a rate, per the cluster-aware NCF
    redesign. A gated-out neighbor (`None`) simply doesn't count either way."""
    count = 0
    for nid in neighbor_ids:
        neighbor = by_id.get(nid)
        if neighbor is None:
            continue
        if _ncf_affected(neighbor, global_config) is True:
            count += 1
    return count


# ---------------------------------------------------------------------------
# §6a — spatial neighbor graph (all FLWs, for NCF/inaccessible + EVC-shortfall)
# ---------------------------------------------------------------------------

_EARTH_RADIUS_M = 6_371_000.0


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def build_neighbor_graph(work_areas: list[dict], distance_m: float) -> dict[str, list[str]]:
    """{wa_id: [neighbor_wa_id, ...]} for every pair of WAs whose centroids
    are within `distance_m` of each other. O(n^2) — fine at real mop-up scale
    (a ward's WAs, not a whole opportunity's); revisit with a spatial index if
    that assumption stops holding."""
    graph: dict[str, list[str]] = {wa["wa_id"]: [] for wa in work_areas}
    for i, a in enumerate(work_areas):
        for b in work_areas[i + 1 :]:
            if a.get("lat") is None or a.get("lon") is None or b.get("lat") is None or b.get("lon") is None:
                continue
            if haversine_distance_m(a["lat"], a["lon"], b["lat"], b["lon"]) <= distance_m:
                graph[a["wa_id"]].append(b["wa_id"])
                graph[b["wa_id"]].append(a["wa_id"])
    return graph


def neighborhood_rate(
    wa: dict,
    neighbor_ids: list[str],
    by_id: dict[str, dict],
    indicator_key: str,
    global_config: dict,
    min_neighbors: int,
) -> float | None:
    """The average rate among `wa`'s qualifying neighbors (excluding itself).
    `None` if fewer than `min_neighbors` neighbors have a computable rate —
    too small a sample to trust (§6a's explicit floor)."""
    rates = []
    for nid in neighbor_ids:
        neighbor = by_id.get(nid)
        if neighbor is None:
            continue
        r = wa_rate(neighbor, indicator_key, global_config)
        if r is not None:
            rates.append(r)
    if len(rates) < min_neighbors:
        return None
    return sum(rates) / len(rates)


# ---------------------------------------------------------------------------
# §6b — within-FLW clustering (data-quality metrics only)
# ---------------------------------------------------------------------------


def flw_portfolio_rate(
    wa: dict,
    flw_work_areas: list[dict],
    indicator_key: str,
    global_config: dict,
    min_portfolio_size: int,
) -> float | None:
    """The rate among this WA's own FLW's OTHER work areas (§6b) — same
    min-sample-size floor idea as `neighborhood_rate`, just scoped to one
    FLW's portfolio instead of spatial neighbors."""
    others = [w for w in flw_work_areas if w["wa_id"] != wa["wa_id"]]
    rates = [r for r in (wa_rate(w, indicator_key, global_config) for w in others) if r is not None]
    if len(rates) < min_portfolio_size:
        return None
    return sum(rates) / len(rates)


# ---------------------------------------------------------------------------
# §6, whole-FLW-average granularity — blend numerators/denominators once
# ---------------------------------------------------------------------------


def flw_average_rate(flw_work_areas: list[dict], indicator_key: str, global_config: dict) -> float | None:
    """Sum this FLW's numerators/denominators across ALL their work areas and
    divide once (the original, simpler view kept available per §6's "three-way
    view", not the cluster-aware default)."""
    if indicator_key == EVC_SHORTFALL:
        include_not_yet_visited = global_config.get("include_not_yet_visited", False)
        num = sum(
            w.get("approved_hsd_count", 0)
            for w in flw_work_areas
            if w.get("status") in _CONCLUDED_STATUSES or include_not_yet_visited
        )
        denom = sum(
            w.get("expected_visit_count", 0)
            for w in flw_work_areas
            if w.get("status") in _CONCLUDED_STATUSES or include_not_yet_visited
        )
        return _safe_div(num, denom)

    if indicator_key == NCF_INACCESSIBLE:
        # WA-count-based, not visit-count-based — see the module docstring's
        # note on why NCF/inaccessible can't use a visit-mix ratio here: a
        # work area logs at most ONE NCF-or-Inaccessible visit ever, so
        # "share of THIS FLW's work areas that were ever affected" is the
        # right unit, not "share of this FLW's total visits."
        min_buildings = global_config.get("min_building_count", 1)
        eligible = [w for w in flw_work_areas if w.get("building_count", 0) >= min_buildings]
        if not eligible:
            return None
        affected = sum(1 for w in eligible if _ncf_visit_total(w) > 0)
        return _safe_div(affected, len(eligible))

    if indicator_key in _DQ_INDICATORS:
        min_hsd = global_config.get("min_hsd_visits_floor", 1)
        eligible = [w for w in flw_work_areas if w.get("approved_hsd_count", 0) >= min_hsd]
        num = sum(_dq_given_count(w, indicator_key) for w in eligible)
        denom = sum(w.get("approved_hsd_count", 0) for w in eligible)
        return _safe_div(num, denom)

    raise ValueError(f"unknown indicator: {indicator_key!r}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

DEFAULT_GLOBAL_CONFIG = {
    "neighbor_distance_m": 200,
    "min_neighbor_count": 3,
    "min_neighborhood_size": 3,
    "min_hsd_visits_floor": 1,
    "min_building_count": 1,
    "include_not_yet_visited": False,
    # Cluster-aware NCF/inaccessible only (see _ncf_affected/
    # ncf_neighbor_affected_count) — a raw count of affected neighbors, not a
    # rate, so it's independent of NCF's own rate-threshold.
    "min_affected_neighbors_ncf": 1,
}

# Starting-point thresholds — every one of these is meant to be reviewer-
# tunable in the UI (§6's "per-indicator thresholds" requirement), not a
# fixed judgment call baked in here. Cluster-aware is the recommended
# granularity default per §6's three-way view.
DEFAULT_INDICATOR_CONFIGS = {
    EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": GRANULARITY_CLUSTER_AWARE},
    NCF_INACCESSIBLE: {"enabled": True, "threshold": 0.3, "granularity": GRANULARITY_CLUSTER_AWARE},
    DEWORMING: {"enabled": True, "threshold": 0.7, "granularity": GRANULARITY_CLUSTER_AWARE},
    MUAC: {"enabled": True, "threshold": 0.7, "granularity": GRANULARITY_CLUSTER_AWARE},
    VACCINATION: {"enabled": True, "threshold": 0.7, "granularity": GRANULARITY_CLUSTER_AWARE},
}


def evaluate_run(
    work_areas: list[dict],
    indicator_configs: dict[str, dict],
    global_config: dict | None = None,
) -> list[dict]:
    """Evaluate every enabled indicator against every work area and return
    the candidates: work areas triggered by at least one enabled indicator
    (union/OR, §6's explicit combination rule), each annotated with which
    indicators triggered and a plain severity count (§6c).

    `indicator_configs`: {indicator_key: {"enabled": bool, "threshold": float,
    "granularity": "wa_only"|"cluster_aware"|"flw_average"}} — only keys
    present AND enabled are evaluated; a disabled/absent indicator never
    contributes to severity or the union.

    Each returned candidate carries `building_count`/`expected_visit_count`
    (straight copy-through from the input row) and `source` (always
    `SOURCE_EXISTING_WA` today) alongside `boundary` — Phase 3's
    `carry_forward_features` needs all three to materialize a WorkArea
    feature without a second lookup.
    """
    config = dict(DEFAULT_GLOBAL_CONFIG)
    config.update(global_config or {})

    by_id = {wa["wa_id"]: wa for wa in work_areas}
    neighbor_graph = build_neighbor_graph(work_areas, config["neighbor_distance_m"])
    by_flw: dict[str, list[dict]] = {}
    for wa in work_areas:
        by_flw.setdefault(wa.get("flw_username", ""), []).append(wa)

    candidates = []
    for wa in work_areas:
        triggered = []
        detail = {}
        for indicator_key, ind_cfg in indicator_configs.items():
            if not ind_cfg.get("enabled"):
                continue
            threshold = ind_cfg["threshold"]
            granularity = ind_cfg.get("granularity", GRANULARITY_CLUSTER_AWARE)
            own_rate = wa_rate(wa, indicator_key, config)
            # Always this WA's OWN numerator/denominator, regardless of
            # granularity — cluster-aware/flw-average change what the rate is
            # COMPARED against, not what this specific work area's own visit
            # counts are. Shown next to the indicator name in the candidate
            # table (e.g. "deworming (0.20; 2/10)").
            own_pair = wa_numerator_denominator(wa, indicator_key, config)
            own_numerator, own_denominator = own_pair if own_pair is not None else (None, None)

            if granularity == GRANULARITY_WA_ONLY:
                flagged = is_flagged(own_rate, threshold, indicator_key)
                detail[indicator_key] = {
                    "granularity": granularity,
                    "rate": own_rate,
                    "own_numerator": own_numerator,
                    "own_denominator": own_denominator,
                }

            elif granularity == GRANULARITY_FLW_AVERAGE:
                blended = flw_average_rate(by_flw.get(wa.get("flw_username", ""), [wa]), indicator_key, config)
                flagged = is_flagged(blended, threshold, indicator_key)
                detail[indicator_key] = {
                    "granularity": granularity,
                    "rate": own_rate,
                    "own_numerator": own_numerator,
                    "own_denominator": own_denominator,
                    "blended_rate": blended,
                }

            elif granularity == GRANULARITY_CLUSTER_AWARE:
                if indicator_key == NCF_INACCESSIBLE:
                    # See the module docstring / _ncf_affected: presence, not
                    # a ratio, is the unit here — own_flagged is a boolean
                    # "was this WA itself affected," and the neighbor side is
                    # a raw affected-neighbor COUNT compared against
                    # min_affected_neighbors_ncf, not against `threshold`
                    # (which has no meaning for a boolean own-check).
                    own_affected = _ncf_affected(wa, config)
                    own_flagged = own_affected is True
                    neighbor_count = ncf_neighbor_affected_count(
                        wa, neighbor_graph.get(wa["wa_id"], []), by_id, config
                    )
                    neighborhood_flagged = neighbor_count >= config["min_affected_neighbors_ncf"]
                    flagged = own_flagged and neighborhood_flagged
                    detail[indicator_key] = {
                        "granularity": granularity,
                        "rate": own_rate,
                        "own_numerator": own_numerator,
                        "own_denominator": own_denominator,
                        "own_affected": own_affected,
                        "affected_neighbor_count": neighbor_count,
                        "is_isolated_outlier": own_flagged and not neighborhood_flagged,
                    }
                    if flagged:
                        triggered.append(indicator_key)
                    continue

                own_flagged = is_flagged(own_rate, threshold, indicator_key)
                if indicator_key in _DQ_INDICATORS:
                    neighborhood = flw_portfolio_rate(
                        wa,
                        by_flw.get(wa.get("flw_username", ""), []),
                        indicator_key,
                        config,
                        config["min_neighborhood_size"],
                    )
                else:
                    neighborhood = neighborhood_rate(
                        wa,
                        neighbor_graph.get(wa["wa_id"], []),
                        by_id,
                        indicator_key,
                        config,
                        config["min_neighbor_count"],
                    )
                neighborhood_flagged = is_flagged(neighborhood, threshold, indicator_key)
                flagged = own_flagged and neighborhood_flagged
                detail[indicator_key] = {
                    "granularity": granularity,
                    "rate": own_rate,
                    "own_numerator": own_numerator,
                    "own_denominator": own_denominator,
                    "neighborhood_rate": neighborhood,
                    "is_isolated_outlier": own_flagged and not neighborhood_flagged,
                }
            else:
                raise ValueError(f"unknown granularity: {granularity!r}")

            if flagged:
                triggered.append(indicator_key)

        if triggered:
            candidates.append(
                {
                    "wa_id": wa["wa_id"],
                    "ward": wa.get("ward", ""),
                    "lga": wa.get("lga", ""),
                    "state": wa.get("state", ""),
                    "flw_username": wa.get("flw_username", ""),
                    # Carried through, not computed here, so the map (§6's map
                    # cue) and Phase 3's lock hand-off (carry_forward_features
                    # needs a geometry + building_count per candidate) don't
                    # need a second lookup.
                    "boundary": wa.get("boundary"),
                    "building_count": wa.get("building_count", 0),
                    "expected_visit_count": wa.get("expected_visit_count", 0),
                    "source": SOURCE_EXISTING_WA,
                    "triggered_indicators": triggered,
                    "severity_count": len(triggered),
                    "detail": detail,
                }
            )

    return candidates
