"""Plan-grounded synthetic survey generator.

Given one plan's sampled work areas (real building-footprint centroids tagged
``primary`` / ``alternate`` and grouped by ``cluster``) plus a ``SimParams``,
produce a representative run of household survey records — one completed survey
per sampled primary slot, landing on the **primary** unit or a ranked
**alternate** per the surveyor's primary rate, with the captured GPS sitting on
the **real footprint centroid** plus a small offset.

Pure: no Django, no DB, no network. The output records carry every field the
``survey_quality`` metrics and the Verified Monitoring back-check / scorecard
assembly already consume, plus ``sample_type`` / ``cluster`` / ``wa_id`` so the
primary-vs-alternate mix can be measured and mapped.
"""

from __future__ import annotations

import math
import random

from commcare_connect.labs.synthetic.generator.core.survey_quality.stats import point_in_geom

from .params import SimParams

_M_PER_DEG = 111_320.0  # metres per degree latitude (good enough at ward scale)
_ROOF_TYPES = ["thatch", "metal sheet", "mud", "tile"]
_ROOF_WEIGHTS = [0.42, 0.34, 0.16, 0.08]


# --------------------------------------------------------------- geometry utils


def _offset(rng: random.Random, lat: float, lon: float, meters: float) -> tuple:
    """Move a point by ``meters`` in a random bearing (small-distance approx)."""
    bearing = rng.uniform(0, 2 * math.pi)
    dlat = (meters * math.cos(bearing)) / _M_PER_DEG
    dlon = (meters * math.sin(bearing)) / (_M_PER_DEG * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def _interp(a: float, b: float, i: int, n: int) -> float:
    return a if n <= 1 else a + (b - a) * (i / (n - 1))


# --------------------------------------------------------------- work-area prep


def _wa_lat_lon(wa: dict) -> tuple | None:
    """Centroid as (lat, lon). Accepts explicit lat/lon, a ``centroid`` [lon, lat]
    (GeoJSON order, as ``plan._centroid`` emits), or a Point ``geometry``."""
    if wa.get("lat") is not None and wa.get("lon") is not None:
        return float(wa["lat"]), float(wa["lon"])
    c = wa.get("centroid")
    if isinstance(c, (list, tuple)) and len(c) == 2:
        return float(c[1]), float(c[0])
    geom = wa.get("geometry") or {}
    if geom.get("type") == "Point":
        lon, lat = geom["coordinates"]
        return float(lat), float(lon)
    return None


def _clusters(work_areas: list) -> dict:
    """Group work areas into ``{cluster: {"primary": [...], "alternate": [...]}}``,
    each list ranked by ``order_in_cluster``. Units without a usable centroid are
    dropped."""
    out: dict = {}
    for wa in work_areas:
        latlon = _wa_lat_lon(wa)
        if latlon is None:
            continue
        props = wa.get("properties") or wa
        cluster = props.get("cluster") or wa.get("cluster") or "C0"
        sample_type = (props.get("sample_type") or wa.get("sample_type") or "primary").lower()
        order = props.get("order_in_cluster", wa.get("order_in_cluster", 0)) or 0
        wa_id = wa.get("wa_id") or wa.get("id") or wa.get("slug") or f"{cluster}-{sample_type}-{order}"
        bucket = "alternate" if sample_type == "alternate" else "primary"
        out.setdefault(cluster, {"primary": [], "alternate": []})[bucket].append(
            {"wa_id": str(wa_id), "lat": latlon[0], "lon": latlon[1], "sample_type": bucket, "order": int(order)}
        )
    for c in out.values():
        c["primary"].sort(key=lambda u: u["order"])
        c["alternate"].sort(key=lambda u: u["order"])
    return out


# --------------------------------------------------------------- generation


def simulate_plan(
    work_areas: list,
    params: SimParams,
    rng: random.Random,
    *,
    ward_name: str = "",
    ward_geom: dict | None = None,
    base_id: str = "r",
) -> list:
    """Generate primary survey records for one plan-arm-round.

    One completed survey per primary slot: the surveyor reaches the **primary**
    with probability ``surveyor_primary_rate`` else substitutes the next-ranked
    **alternate** in the same cluster. The capture is the visited unit's real
    centroid plus a near/far offset (near when an in-spec ``gps_within_15m`` draw
    succeeds). Surveyors own whole clusters, so substitution stays within a
    surveyor's own PSUs.
    """
    clusters = _clusters(work_areas)
    cluster_keys = sorted(clusters.keys())
    enums = list(params.enumerators) or ["S1"]

    # Stable assignments: each cluster -> one surveyor; each surveyor -> one rate.
    cluster_surveyor = {ck: enums[i % len(enums)] for i, ck in enumerate(cluster_keys)}
    surveyor_rate = {s: params.surveyor_primary_rate(s, rng) for s in enums}

    flagged = params.flagged or {}
    flag_id = flagged.get("id")
    coverage = _interp(params.coverage_start, params.coverage_end, params.round_idx, params.n_rounds)
    coverage = max(0.0, coverage + rng.gauss(0, params.coverage_noise))
    elig = params.eligibility
    dur = params.duration

    # Flatten primary slots (each carries its cluster's alternates + a substitution cursor).
    slots = []
    for ck in cluster_keys:
        c = clusters[ck]
        alts = c["alternate"]
        for prim in c["primary"]:
            slots.append({"cluster": ck, "primary": prim, "alternates": alts, "surveyor": cluster_surveyor[ck]})
    if params.n_surveys is not None and params.n_surveys < len(slots):
        slots = slots[: params.n_surveys]

    recs = []
    alt_cursor: dict = {}
    for j, slot in enumerate(slots):
        surveyor = slot["surveyor"]
        bad = surveyor == flag_id
        # Visit the primary, or substitute a ranked alternate in the same cluster.
        if slot["alternates"] and rng.random() >= surveyor_rate[surveyor]:
            k = alt_cursor.get(slot["cluster"], 0)
            visited = slot["alternates"][k % len(slot["alternates"])]
            alt_cursor[slot["cluster"]] = k + 1
        else:
            visited = slot["primary"]
        alat, alon = visited["lat"], visited["lon"]

        gps_p = flagged.get("gps_within_15m", params.gps_within_15m) if bad else params.gps_within_15m
        ev_p = flagged.get("evidence", params.evidence_complete) if bad else params.evidence_complete
        within = rng.random() < gps_p
        offset_m = rng.uniform(*params.gps_near_m) if within else rng.uniform(*params.gps_far_m)
        clat, clon = _offset(rng, alat, alon, offset_m)

        present = rng.random() < elig.get("present_rate", 0.99)
        age = rng.randint(elig.get("age_min_months", 6), elig.get("age_max_months", 59))
        eligible = present and (elig.get("age_min_months", 6) <= age <= elig.get("age_max_months", 59))
        received = bool(eligible and rng.random() < coverage)
        if rng.random() < dur.get("short_rate", 0.0):
            duration = round(rng.uniform(*dur.get("short_range", [1, 3])), 1)
        else:
            duration = round(max(dur.get("floor", 4), rng.gauss(dur["mean"], dur["sd"])), 1)

        rec = {
            "record_id": f"{base_id}-p{j}",
            "round": params.round_idx + 1,
            "form_type": "primary",
            "household_id": f"{base_id}-H{j:04d}",
            "ward": ward_name,
            "arm": params.arm,
            "enumerator_id": surveyor,
            "lat": round(clat, 6),
            "lon": round(clon, 6),
            "assigned_lat": round(alat, 6),
            "assigned_lon": round(alon, 6),
            "gps_offset_m": round(offset_m, 1),
            "in_ward": (point_in_geom(ward_geom, clat, clon) if ward_geom else True),
            "start_ts": 1_700_000_000 + params.round_idx * 5_000_000 + j * 900,
            "end_ts": 1_700_000_000 + params.round_idx * 5_000_000 + j * 900 + int(duration * 60),
            "duration_min": duration,
            "evidence_photo": rng.random() < ev_p,
            "child_present": present,
            "child_sex": rng.choice(["M", "F"]),
            "child_age_months": age,
            "roof_type": rng.choices(_ROOF_TYPES, weights=_ROOF_WEIGHTS, k=1)[0],
            "eligible": eligible,
            "vitamin_a_received": received,
            "dose_source": rng.choice(["campaign", "routine", "facility"]) if received else None,
            # provenance of the sampled unit this survey landed on
            "sample_type": visited["sample_type"],
            "cluster": slot["cluster"],
            "work_area_id": visited["wa_id"],
            "original_record_id": None,
            "original_enumerator_id": None,
        }
        if rng.random() > params.field_complete:
            rec[rng.choice(["evidence_photo", "child_age_months"])] = None
        recs.append(rec)
    return recs
