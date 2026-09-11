"""Phase 2's tiered execution-gap indicators (design brief §5, revised per
`CHC_Mopup_Phase2_Revisions.md`) — pure functions over plain per-work-area
dicts, no network/DB, so this is the piece that gets real `pytest` coverage
against synthetic data instead of manual live-browser checks.

Expected per-work-area input shape (one dict per WA; upstream data-access
layers are responsible for populating these — this module only computes):

    {
        "wa_id": str,
        "ward": str, "lga": str, "state": str,
        "flw_username": str,          # owner
        "lat": float, "lon": float,   # centroid, for the spatial neighbor graph
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
as its denominator; EVC shortfall's denominator is `expected_visit_count`.
NCF/inaccessible has no rate/threshold at all (see below) — a work area only
ever logs ONE NCF-or-Inaccessible visit, never a mix and never more than one
(confirmed: NCF can only be filled in once, and any HSD visit makes NCF
impossible thereafter), so "was this WA ever affected" is the natural signal.

Two-tier structure (per the revision doc): Tier 1 ("did we find/register
children at all" — EVC shortfall, NCF/inaccessible) generally outranks Tier 2
("for children we did find, did they get complete services" — deworming,
MUAC, vaccination), since a work area can never be BOTH NCF and Tier-2-flagged
(Tier 2 rates are only computable once a WA has escaped NCF). EVC shortfall is
the one indicator that can co-occur with NCF, or occur alone.

Candidacy floor: "This WA only" is now unconditional — any work area whose own
rate/presence breaches its own threshold on any enabled indicator is a
candidate, full stop, no neighbor/FLW corroboration required. Cluster-aware is
now a single, optional, spatial-only, post-hoc FILTER (not a per-indicator
candidacy gate) that can shorten an already-built candidate list: it keeps a
candidate if it has enough spatially-nearby neighbors that are THEMSELVES
floor-flagged on the same indicator, for at least one of its triggered
indicators — applied uniformly to every indicator, NCF/inaccessible included
(its "flagged" neighbor signal is presence, per `_ncf_affected`, rather than a
rate breach, but the same keep-if-any-triggered-indicator-corroborates rule
applies). When the filter is OFF, every indicator's count — NCF/inaccessible's
included — is simply the raw count of floor-triggered work areas, unfiltered.

The "Whole-FLW average" comparison scope, and the FLW-scoped variant of
Cluster-aware (used previously by the three data-quality indicators), have
been removed entirely — both diluted a temporary bad streak into invisibility
once an FLW self-corrected, which is exactly the pattern this system is meant
to catch.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Indicator definitions — direction is inherent to what's being measured, not
# user-configurable (only threshold VALUEs and the global cluster-aware filter
# toggle are).
# ---------------------------------------------------------------------------

EVC_SHORTFALL = "evc_shortfall"
NCF_INACCESSIBLE = "ncf_inaccessible_rate"
DEWORMING = "deworming"
MUAC = "muac"
VACCINATION = "vaccination"

ALL_INDICATORS = [EVC_SHORTFALL, NCF_INACCESSIBLE, DEWORMING, MUAC, VACCINATION]

# Candidate provenance discriminator (Phase 3's carry-forward hand-off keys
# off this): every candidate `evaluate_run` produces today comes from a
# flagged EXISTING work area.
SOURCE_EXISTING_WA = "existing_wa"

TIER_1_INDICATORS = {EVC_SHORTFALL, NCF_INACCESSIBLE}
TIER_2_INDICATORS = {DEWORMING, MUAC, VACCINATION}

# "below" = flagged when the rate is BELOW threshold (a shortfall). NCF/
# inaccessible has no threshold/direction — see _ncf_affected.
_DIRECTION = {
    EVC_SHORTFALL: "below",
    DEWORMING: "below",
    MUAC: "below",
    VACCINATION: "below",
}

_DQ_INDICATORS = TIER_2_INDICATORS

# Concluded statuses for the not-yet-visited EVC exclusion. A WA still
# NOT_VISITED or with a pending REQUEST_FOR_INACCESSIBLE hasn't necessarily
# failed — the campaign may just not have reached it yet.
_CONCLUDED_STATUSES = {"VISITED", "EXPECTED_VISIT_REACHED", "INACCESSIBLE"}


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
    """This WA's own (numerator, denominator) for a rate-based `indicator_key`
    (EVC shortfall or a data-quality indicator — NOT NCF/inaccessible, which
    has no rate; use `_ncf_affected` for that), or `None` if the indicator
    doesn't apply / isn't trustworthy for this WA (gated out)."""
    if indicator_key == EVC_SHORTFALL:
        if wa.get("expected_visit_count", 0) < global_config.get("min_evc_floor", 0):
            return None
        if wa.get("status") not in _CONCLUDED_STATUSES and not global_config.get("include_not_yet_visited", False):
            return None
        hsd_count = wa.get("approved_hsd_count", 0)
        # A WA with zero HSD visits is already explained by something else --
        # not-yet-visited (the check above), inaccessible, or NCF (a visit
        # happened, but it wasn't an HSD delivery) -- so it isn't a genuine
        # "we delivered less than expected" shortfall. Excluding it here (not
        # scoring it as rate 0.0) keeps EVC shortfall scoped to WAs where HSD
        # delivery actually happened, per product direction (2026-09-11).
        if hsd_count == 0:
            return None
        return hsd_count, wa.get("expected_visit_count", 0)

    if indicator_key in _DQ_INDICATORS:
        min_hsd = global_config.get("min_hsd_visits_floor", 1)
        hsd_count = wa.get("approved_hsd_count", 0)
        if hsd_count < min_hsd:
            return None
        return _dq_given_count(wa, indicator_key), hsd_count

    raise ValueError(f"{indicator_key!r} has no rate — use _ncf_affected for NCF/inaccessible")


def wa_rate(wa: dict, indicator_key: str, global_config: dict) -> float | None:
    """This WA's own rate for `indicator_key`, or `None` if the indicator
    doesn't apply / isn't trustworthy for this WA (gated out — never a guess)."""
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
    gated out by `min_building_count` — never a guess. This is the
    unconditional candidacy floor for NCF/inaccessible: no threshold, no
    rate — presence/absence is the signal (a WA logs at most one such visit
    ever)."""
    min_buildings = global_config.get("min_building_count", 1)
    if wa.get("building_count", 0) < min_buildings:
        return None
    return _ncf_visit_total(wa) > 0


# ---------------------------------------------------------------------------
# Spatial neighbor graph — shared by the optional cluster-aware filter
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


def ncf_neighbor_affected_count(wa: dict, neighbor_ids: list[str], by_id: dict[str, dict], global_config: dict) -> int:
    """How many of `wa`'s spatial neighbors are themselves NCF/inaccessible
    -affected (per `_ncf_affected`). A gated-out neighbor (`None`) simply
    doesn't count either way. This is NCF/inaccessible's corroboration signal
    for the cluster-aware filter (same role `flagged_neighbor_count` plays for
    every other indicator) — also exposed for the candidate-detail display."""
    count = 0
    for nid in neighbor_ids:
        neighbor = by_id.get(nid)
        if neighbor is None:
            continue
        if _ncf_affected(neighbor, global_config) is True:
            count += 1
    return count


def flagged_neighbor_count(
    wa: dict,
    neighbor_ids: list[str],
    by_id: dict[str, dict],
    indicator_key: str,
    threshold: float,
    global_config: dict,
) -> int:
    """How many of `wa`'s spatial neighbors are THEMSELVES floor-flagged
    (own rate breaches `threshold`) on `indicator_key` — the unified
    cluster-aware corroboration signal for EVC shortfall and the three
    data-quality indicators (same mechanism NCF/inaccessible already used via
    `ncf_neighbor_affected_count`, generalized to rate-based indicators)."""
    count = 0
    for nid in neighbor_ids:
        neighbor = by_id.get(nid)
        if neighbor is None:
            continue
        rate = wa_rate(neighbor, indicator_key, global_config)
        if is_flagged(rate, threshold, indicator_key):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

DEFAULT_GLOBAL_CONFIG = {
    "evc_neighbor_distance_m": 200,
    "evc_min_neighbor_count": 3,
    # New: a plain worth-visiting cutoff, not a statistical-confidence
    # adjustment — excludes work areas whose EXPECTED count itself is too
    # small for a shortfall there to mean anything.
    "min_evc_floor": 5,
    "include_not_yet_visited": False,
    "min_building_count": 1,
    "ncf_neighbor_distance_m": 200,
    "min_affected_neighbors_ncf": 2,
    "tier2_neighbor_distance_m": 200,
    "tier2_min_neighbor_count": 2,
    "min_hsd_visits_floor": 5,
    # Optional, post-hoc, spatial-only list-shortening filter — NOT a
    # candidacy gate (see module docstring). Applies uniformly to every
    # indicator, including NCF/inaccessible. Defaults on per product decision.
    "cluster_aware_filter_enabled": True,
}

# Starting-point thresholds — every one of these is meant to be reviewer-
# tunable in the UI. NCF/inaccessible has no threshold (see module docstring).
DEFAULT_INDICATOR_CONFIGS = {
    EVC_SHORTFALL: {"enabled": True, "threshold": 0.5},
    NCF_INACCESSIBLE: {"enabled": True},
    DEWORMING: {"enabled": True, "threshold": 0.7},
    MUAC: {"enabled": True, "threshold": 0.7},
    VACCINATION: {"enabled": True, "threshold": 0.7},
}

# Which global-config keys hold each indicator's own neighbor distance/count,
# for the cluster-aware filter. NCF isn't listed here — its own equivalent
# settings (`ncf_neighbor_distance_m`/`min_affected_neighbors_ncf`) are read
# directly in evaluate_run's NCF branch instead, since its corroboration
# signal (presence, via `ncf_neighbor_affected_count`) isn't the generic
# rate-threshold one `flagged_neighbor_count` computes for every other
# indicator.
_TIER2_NEIGHBOR_SETTINGS = ("tier2_neighbor_distance_m", "tier2_min_neighbor_count")
_NEIGHBOR_SETTINGS_BY_INDICATOR = {
    EVC_SHORTFALL: ("evc_neighbor_distance_m", "evc_min_neighbor_count"),
    DEWORMING: _TIER2_NEIGHBOR_SETTINGS,
    MUAC: _TIER2_NEIGHBOR_SETTINGS,
    VACCINATION: _TIER2_NEIGHBOR_SETTINGS,
}


def evaluate_run(
    work_areas: list[dict],
    indicator_configs: dict[str, dict],
    global_config: dict | None = None,
) -> list[dict]:
    """Evaluate every enabled indicator against every work area and return
    the candidates: work areas triggered by at least one enabled indicator
    (union/OR), each annotated with which indicators triggered, a plain
    severity count, and a tier (1 if any Tier-1 indicator triggered, else 2).

    `indicator_configs`: {indicator_key: {"enabled": bool, "threshold": float}}
    — `threshold` is ignored/optional for NCF_INACCESSIBLE. Only keys present
    AND enabled are evaluated; a disabled/absent indicator never contributes.

    Candidacy is now an unconditional per-WA floor (see module docstring) —
    the optional cluster-aware filter, if enabled, is applied AFTER the full
    floor-triggered list is built, and can only DROP a whole candidate (never
    prune its `triggered_indicators`): a candidate survives the filter if at
    least one of its triggered indicators — any of them, NCF/inaccessible
    included — has enough spatially-nearby neighbors that are themselves
    floor-flagged (or, for NCF, themselves affected) on that same indicator.
    When the filter is disabled, every indicator's count is simply its raw
    floor-triggered count, NCF/inaccessible included.

    Each returned candidate carries `building_count`/`expected_visit_count`
    (straight copy-through from the input row) and `source` (always
    `SOURCE_EXISTING_WA` today) alongside `boundary` — Phase 3's
    `carry_forward_features` needs all three to materialize a WorkArea
    feature without a second lookup.
    """
    config = dict(DEFAULT_GLOBAL_CONFIG)
    config.update(global_config or {})

    by_id = {wa["wa_id"]: wa for wa in work_areas}

    # Neighbor graphs are only needed by the optional filter, and only for
    # indicators actually enabled — build lazily, memoized by distance value
    # (the three settings default to the same 200m, so this often reuses one
    # graph instead of building three identical ones).
    graph_cache: dict[float, dict[str, list[str]]] = {}

    def _graph_for(distance_m: float) -> dict[str, list[str]]:
        if distance_m not in graph_cache:
            graph_cache[distance_m] = build_neighbor_graph(work_areas, distance_m)
        return graph_cache[distance_m]

    candidates = []
    for wa in work_areas:
        triggered = []
        detail = {}
        for indicator_key, ind_cfg in indicator_configs.items():
            if not ind_cfg.get("enabled"):
                continue

            if indicator_key == NCF_INACCESSIBLE:
                own_affected = _ncf_affected(wa, config)
                flagged = own_affected is True
                neighbor_ids = _graph_for(config["ncf_neighbor_distance_m"]).get(wa["wa_id"], [])
                neighbor_count = ncf_neighbor_affected_count(wa, neighbor_ids, by_id, config)
                detail[indicator_key] = {
                    "own_affected": own_affected,
                    "own_ncf_form": wa.get("approved_ncf_count", 0) > 0,
                    "own_inaccessible_form": wa.get("approved_inaccessible_count", 0) > 0,
                    # Same corroboration signal every other indicator uses —
                    # decides survival below when the cluster-aware filter is
                    # enabled, and is purely informational (never drops
                    # anything) when it's disabled, same as every indicator.
                    "affected_neighbor_count": neighbor_count,
                    "corroborated": neighbor_count >= config["min_affected_neighbors_ncf"],
                }
            else:
                threshold = ind_cfg["threshold"]
                own_rate = wa_rate(wa, indicator_key, config)
                own_pair = wa_numerator_denominator(wa, indicator_key, config)
                own_numerator, own_denominator = own_pair if own_pair is not None else (None, None)
                flagged = is_flagged(own_rate, threshold, indicator_key)
                distance_key, count_key = _NEIGHBOR_SETTINGS_BY_INDICATOR[indicator_key]
                neighbor_ids = _graph_for(config[distance_key]).get(wa["wa_id"], [])
                neighbor_count = flagged_neighbor_count(wa, neighbor_ids, by_id, indicator_key, threshold, config)
                detail[indicator_key] = {
                    "rate": own_rate,
                    "own_numerator": own_numerator,
                    "own_denominator": own_denominator,
                    # Informational unless the cluster-aware filter is
                    # enabled, in which case it also decides survival below.
                    "flagged_neighbor_count": neighbor_count,
                    "corroborated": neighbor_count >= config[count_key],
                }

            if flagged:
                triggered.append(indicator_key)

        if not triggered:
            continue

        survives_filter = True
        if config["cluster_aware_filter_enabled"]:
            survives_filter = any(detail[key]["corroborated"] for key in triggered)

        if not survives_filter:
            continue

        tier = 1 if any(key in TIER_1_INDICATORS for key in triggered) else 2

        candidates.append(
            {
                "wa_id": wa["wa_id"],
                "ward": wa.get("ward", ""),
                "lga": wa.get("lga", ""),
                "state": wa.get("state", ""),
                "flw_username": wa.get("flw_username", ""),
                # Carried through, not computed here, so the map and Phase 3's
                # lock hand-off (carry_forward_features needs a geometry +
                # building_count per candidate) don't need a second lookup.
                "boundary": wa.get("boundary"),
                "building_count": wa.get("building_count", 0),
                "expected_visit_count": wa.get("expected_visit_count", 0),
                "source": SOURCE_EXISTING_WA,
                "triggered_indicators": triggered,
                "severity_count": len(triggered),
                "tier": tier,
                "detail": detail,
            }
        )

    return candidates
