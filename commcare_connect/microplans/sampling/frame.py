"""Frame orchestrator: drawn area(s) + config → footprints → PSUs → pins.

Ties the sampling stages together and emits GeoJSON the setup map renders
(cluster hulls + pins) plus per-arm stats. One pass per arm (intervention /
comparison); each arm's polygons are unioned into a single sampling area.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd
from shapely.geometry import MultiPoint, mapping
from shapely.ops import unary_union

from commcare_connect.microplans.core.area_input import resolve_area
from commcare_connect.microplans.core.filters import FilterConfig, apply_frame_filters
from commcare_connect.microplans.core.footprints import DEFAULT_SOURCES, fetch_buildings, source_counts
from commcare_connect.microplans.sampling.cluster import ClusterConfig, cluster_buildings
from commcare_connect.microplans.sampling.defaults import SAMPLING_DEFAULTS as _D
from commcare_connect.microplans.sampling.sample import PinConfig, sample_pins, select_psus, select_psus_matched

logger = logging.getLogger(__name__)


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _clampf(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass
class FrameConfig:
    # All defaults come from the single source of truth: sampling/defaults.py.
    target_clusters: int = _D["target_clusters"]
    primary_per_psu: int = _D["primary_per_psu"]
    alternates_per_psu: int = _D["alternates_per_psu"]
    min_confidence: float | None = _D["min_confidence"]
    area_min_m2: float = _D["area_min_m2"]
    area_max_m2: float = _D["area_max_m2"]
    # Building providers to sample from (Overture `dataset` names). Defaults to
    # Google Open Buildings, matching the rooftop pilot.
    sources: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))
    # Optional (lon, lat) of the verification reference point. When set, clusters
    # are stratified High/Medium/Low on distance_to_visit; otherwise single pool.
    reference_point: tuple[float, float] | None = None
    # Number of building-count bands for size-stratified systematic PPS (0/1 = plain
    # PPS). Banding draws a matched size-mix across arms so they're comparable on
    # cluster size by construction. See sample.select_psus. Default (3) = size-stratified
    # (the DHS/MICS standard) — good practice for a single plan, essential for two-arm.
    size_balance_bands: int = _D["size_balance_bands"]
    # PPS-draw + pin-placement seed. None → a fresh random draw each call, so
    # "Regenerate plan" re-rolls different PSUs + households every click. Pass an
    # int to pin a reproducible sample (tests, deterministic walkthrough capture).
    # The candidate-cluster frame stays seed-stable; only the draw and pins vary.
    seed: int | None = None

    @classmethod
    def from_payload(cls, d: dict) -> FrameConfig:
        rp = d.get("reference_point")
        conf = d.get("min_confidence")
        src = d.get("sources")
        # Accept the legacy `size_strata` key so studies persisted before the rename
        # keep their banded draw; new payloads use `size_balance_bands`.
        # Fallbacks all come from the single source (sampling/defaults.py) — a payload
        # that omits a key (e.g. the UI never sends size_balance_bands) gets the
        # canonical default, so the UI and the synthetic study draw the same way.
        bands = d.get("size_balance_bands", d.get("size_strata", _D["size_balance_bands"]))
        return cls(
            size_balance_bands=_clamp(int(bands if bands is not None else _D["size_balance_bands"]), 0, 20),
            # clamp to sane bounds so a malformed payload can't crash or stall sampling
            target_clusters=_clamp(int(d.get("target_clusters", _D["target_clusters"])), 1, 500),
            primary_per_psu=_clamp(int(d.get("primary_per_psu", _D["primary_per_psu"])), 1, 100),
            alternates_per_psu=_clamp(int(d.get("alternates_per_psu", _D["alternates_per_psu"])), 0, 100),
            min_confidence=(None if conf in (None, "", 0) else _clampf(float(conf), 0.0, 1.0)),
            area_min_m2=_clampf(float(d.get("area_min_m2", _D["area_min_m2"])), 0.0, 1e6),
            area_max_m2=_clampf(float(d.get("area_max_m2", _D["area_max_m2"])), 1.0, 1e7),
            # A non-empty list selects those providers; missing/empty falls back to
            # the pilot default so a sample is never silently empty.
            sources=([str(s) for s in src] if isinstance(src, list) and src else list(DEFAULT_SOURCES)),
            reference_point=(float(rp[0]), float(rp[1])) if rp else None,
            seed=(int(d["seed"]) if d.get("seed") not in (None, "") else None),
        )


@dataclass
class FrameResult:
    pins_geojson: dict
    hulls_geojson: dict
    stats: list[dict] = field(default_factory=list)


def _mean_sd(values) -> tuple[float, float]:
    import numpy as np

    a = np.asarray(list(values), dtype=float)
    if len(a) == 0:
        return (0.0, 0.0)
    return (float(a.mean()), float(a.std(ddof=1)) if len(a) >= 2 else 0.0)


def building_knn_density_array(buildings: pd.DataFrame, k: int = 8):
    """Per-building LOCAL building density (buildings per km²) via the k-nearest-
    neighbour intensity estimator ``λ̂ = k / (π · d_k²)``, where ``d_k`` is the
    distance (UTM metres) to the building's k-th nearest neighbour.

    This is the standard nonparametric estimate of a point pattern's intensity: a
    density measured AT each building from its actual neighbours, so it's robust to
    settlement shape and edge outliers — unlike a per-cluster convex-hull density,
    whose denominator is set by a handful of fringe buildings. It also needs no
    clustering, so the density no longer depends on where cluster boundaries land.

    Returns a numpy array aligned to ``buildings`` rows (NaN where undefined: fewer
    than two buildings, or a coincident neighbour at distance 0).
    """
    import numpy as np

    if buildings is None or buildings.empty:
        return np.empty(0)
    n = len(buildings)
    out = np.full(n, np.nan)
    if n < 2:
        return out

    from pyproj import Transformer
    from scipy.spatial import cKDTree

    from commcare_connect.microplans.core.geo import utm_epsg_for

    lon = buildings["lon"].to_numpy(dtype=float)
    lat = buildings["lat"].to_numpy(dtype=float)
    epsg = utm_epsg_for(float(np.nanmean(lon)), float(np.nanmean(lat)))
    tx, ty = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
    pts = np.column_stack([tx, ty])
    kk = min(int(k), n - 1)
    # query kk+1 — the first neighbour is the point itself (distance 0).
    dists, _ = cKDTree(pts).query(pts, k=kk + 1)
    dk = dists[:, kk]
    valid = dk > 0
    out[valid] = (kk / (np.pi * dk[valid] ** 2)) * 1e6  # buildings/m² → buildings/km²
    return out


def building_knn_densities(buildings: pd.DataFrame, k: int = 8) -> list[float]:
    """The finite per-building k-NN densities for a ward — the distribution the
    surrounding-ward comparison overlaps. See :func:`building_knn_density_array`."""
    import numpy as np

    arr = building_knn_density_array(buildings, k=k)
    return [float(v) for v in arr[np.isfinite(arr)]]


def cluster_density_map(buildings: pd.DataFrame) -> dict:
    """Per-CANDIDATE-cluster mean k-NN local density (buildings/km²).

    ``psu_summary`` measures density only over the SELECTED PSUs, which is too late
    to *stratify* on — the matched selector needs a density for every candidate
    cluster up front. This computes per-building k-NN density once over the whole arm
    (the same robust estimator the ward comparison uses), then averages it within each
    candidate cluster. Returns ``{cluster: mean_density}`` (clusters with no finite
    density are omitted).
    """
    import numpy as np

    if buildings is None or buildings.empty or "cluster" not in buildings.columns:
        return {}
    knn = building_knn_density_array(buildings)
    out: dict = {}
    clusters = buildings["cluster"].to_numpy()
    for c in pd.unique(clusters):
        vals = knn[(clusters == c)]
        vals = vals[np.isfinite(vals)]
        if vals.size:
            out[c] = float(np.mean(vals))
    return out


def attach_candidate_density(psu_frame: pd.DataFrame, buildings: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``psu_frame`` with a ``density`` column (mean k-NN local
    density of each candidate cluster's buildings) — the stratification axis the
    matched selector bands on. Clusters without a finite density get the frame's
    median density (so they still band somewhere rather than being dropped)."""
    import numpy as np

    out = psu_frame.copy()
    if out.empty:
        out["density"] = pd.Series(dtype=float)
        return out
    dmap = cluster_density_map(buildings)
    dens = out["cluster"].map(dmap).to_numpy(dtype=float)
    finite = dens[np.isfinite(dens)]
    fill = float(np.median(finite)) if finite.size else 0.0
    out["density"] = np.where(np.isfinite(dens), dens, fill)
    return out


def psu_summary(buildings: pd.DataFrame, selected: pd.DataFrame) -> dict:
    """Per-arm balance summary over the SELECTED PSUs, as (mean, sd) tuples.

    Returns ``{"psu_size": (mean, sd), "psu_density": (mean, sd), "bldg_area":
    (mean, sd)}`` where psu_size is buildings per selected PSU, psu_density is the
    per-building LOCAL building density (k-NN intensity, buildings per km²) averaged
    over the buildings in the selected PSUs — restricted to where the survey actually
    samples — and bldg_area is the footprint area of those buildings.

    These feed ``comparability.arm_comparability_psu`` so two arms are compared on
    the settlements the survey visits rather than on whole-ward geography.
    """
    import numpy as np

    empty = {"psu_size": (0.0, 0.0), "psu_density": (0.0, 0.0), "bldg_area": (0.0, 0.0)}
    if selected is None or selected.empty or buildings is None or buildings.empty:
        return empty

    selected_clusters = selected["cluster"].tolist()
    sizes: list[int] = []
    areas: list[float] = []
    for cluster in selected_clusters:
        sub = buildings[buildings["cluster"] == cluster]
        n = len(sub)
        if n == 0:
            continue
        sizes.append(n)
        if "area_m2" in sub.columns:
            areas.extend(float(a) for a in sub["area_m2"].tolist() if a and a > 0)
    # Per-building local density (k-NN intensity) measured over the WHOLE arm, then
    # restricted to the selected PSUs' buildings — the same estimator the ward
    # comparison uses, so both surfaces measure density identically and robustly.
    knn = building_knn_density_array(buildings)
    in_selected = buildings["cluster"].isin(selected_clusters).to_numpy()
    sel_dens = knn[in_selected & np.isfinite(knn)]
    return {
        "psu_size": _mean_sd(sizes),
        "psu_density": _mean_sd(sel_dens),
        "bldg_area": _mean_sd(areas),
        # n selected PSUs the means/SDs (and hence every SMD) are computed over —
        # surfaced so the balance panel can state its own sample size, not assert
        # a standardized difference whose denominator is invisible.
        "n_psus": len(sizes),
    }


def ward_density_distribution(geometry: dict, config: FrameConfig) -> dict:
    """Local building-density distribution for ONE ward, WITHOUT the PPS draw.

    Fetch footprints → filter → (cluster, for the PSU count) → per-BUILDING k-NN
    density over every building in the ward. This is the structural fingerprint the
    surrounding-ward control finder compares: two wards are exchangeable controls
    when these distributions overlap, regardless of equal means. Skips
    ``select_psus``/``sample_pins`` — only the Overture fetch + clustering + k-NN —
    so it's the cheap path used to score several neighbours at once.

    Returns ``{"densities": [per-building…], "n_clusters", "n_buildings",
    "psu_density": (mean, sd)}`` (``n_clusters`` = candidate PSUs formed; the density
    itself is per building, independent of the clustering).
    """
    area = resolve_area({"geometry": geometry})
    all_buildings = fetch_buildings(area, min_confidence=config.min_confidence, with_geom=False)
    buildings = (
        all_buildings
        if not config.sources
        else all_buildings[all_buildings["dataset"].isin(config.sources)].reset_index(drop=True)
    )
    filtered = apply_frame_filters(
        buildings,
        FilterConfig(area_min_m2=config.area_min_m2, area_max_m2=config.area_max_m2),
    )
    clustered = cluster_buildings(filtered.buildings, ClusterConfig(target_psus=config.target_clusters))
    densities = building_knn_densities(clustered.buildings)
    return {
        "densities": densities,
        "n_clusters": len(clustered.psu_frame),
        "n_buildings": int(filtered.n_out),
        "psu_density": _mean_sd(densities),
    }


@dataclass
class ArmFrame:
    """A single arm's CANDIDATE PSU frame — everything up to (but not including) the
    PPS draw. The composable middle of ``generate_frame``: build one of these per arm,
    then run a single-arm OR a joint cross-arm matched selector over them, then place
    pins + stats per arm. ``psu_frame`` carries a per-candidate ``density`` column so
    the matched selector can band on it."""

    arm: str
    buildings: pd.DataFrame  # clustered buildings (with projected coords)
    psu_frame: pd.DataFrame  # candidate clusters + n_buildings + stratum + density
    geom_by_coord: dict
    src_counts: dict
    filtered_n_in: int
    filtered_n_out: int
    removed_tiny_isolated: int
    removed_large: int


def build_arm_frame(arm: str, geoms: list, config: FrameConfig) -> ArmFrame:
    """Stage (a): footprints → filter → cluster → per-candidate density, for ONE arm.
    No PPS draw here — that's the selection stage, which may be joint across arms."""
    area = unary_union(geoms)
    # Fetch once across all providers (confidence-filtered) so we can report the
    # per-source breakdown, then sample only from the chosen sources.
    all_buildings = fetch_buildings(area, min_confidence=config.min_confidence, with_geom=True)
    src_counts = source_counts(all_buildings)
    geom_by_coord = {
        (round(float(lo), 7), round(float(la), 7)): gj
        for lo, la, gj in zip(all_buildings["lon"], all_buildings["lat"], all_buildings["geom_json"])
    }
    buildings = (
        all_buildings
        if not config.sources
        else all_buildings[all_buildings["dataset"].isin(config.sources)].reset_index(drop=True)
    )
    filtered = apply_frame_filters(
        buildings,
        FilterConfig(area_min_m2=config.area_min_m2, area_max_m2=config.area_max_m2),
    )
    clustered = cluster_buildings(
        filtered.buildings,
        ClusterConfig(target_psus=config.target_clusters),
        reference_point=config.reference_point,
    )
    # Per-candidate-cluster density is the shared stratification axis for matched PPS.
    psu_frame = attach_candidate_density(clustered.psu_frame, clustered.buildings)
    return ArmFrame(
        arm=arm,
        buildings=clustered.buildings,
        psu_frame=psu_frame,
        geom_by_coord=geom_by_coord,
        src_counts=src_counts,
        filtered_n_in=filtered.n_in,
        filtered_n_out=filtered.n_out,
        removed_tiny_isolated=filtered.removed_tiny_isolated,
        removed_large=filtered.removed_large,
    )


def _render_arm(arm_frame: ArmFrame, selected: pd.DataFrame, config: FrameConfig, base_seed: int, arm_idx: int):
    """Stage (c): place pins + build geojson features + per-arm stats for ONE arm's
    selected PSUs. Returns (pin_features, hull_features, stats_dict)."""
    arm = arm_frame.arm
    buildings = arm_frame.buildings
    geom_by_coord = arm_frame.geom_by_coord
    pins = sample_pins(
        buildings,
        selected,
        PinConfig(
            n_primary=config.primary_per_psu,
            n_alternate=config.alternates_per_psu,
            seed=base_seed + 1000 + arm_idx,
        ),
    )
    stratum_by_cluster = dict(zip(selected["cluster"], selected["stratum"]))

    pin_features: list[dict] = []
    for _, p in pins.iterrows():
        pin_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                "properties": {
                    "arm": arm,
                    "cluster": p["cluster"],
                    "sample_type": p["sample_type"],
                    "order_in_cluster": int(p["order_in_cluster"]),
                    "stratum": stratum_by_cluster.get(p["cluster"], "Low"),
                    "weight": None if pd.isna(p["weight"]) else round(float(p["weight"]), 4),
                    "geom_json": geom_by_coord.get((round(float(p["lon"]), 7), round(float(p["lat"]), 7))),
                },
            }
        )

    hull_features: list[dict] = []
    for cluster in selected["cluster"].tolist():
        pts = buildings[buildings["cluster"] == cluster]
        if len(pts) >= 3:
            hull = MultiPoint(list(zip(pts["lon"], pts["lat"]))).convex_hull
            hull_features.append(
                {"type": "Feature", "geometry": mapping(hull), "properties": {"arm": arm, "cluster": cluster}}
            )

    psu_frame = arm_frame.psu_frame
    stratum_counts = psu_frame["stratum"].value_counts().to_dict() if len(psu_frame) else {}
    stats = {
        "arm": arm,
        "sources_used": list(config.sources),
        "source_counts": arm_frame.src_counts,
        "fetched": arm_frame.filtered_n_in,
        "after_filters": arm_frame.filtered_n_out,
        "removed_tiny_isolated": arm_frame.removed_tiny_isolated,
        "removed_large": arm_frame.removed_large,
        "clusters_formed": len(psu_frame),
        "strata": {k: int(v) for k, v in stratum_counts.items()},
        "psus_selected": len(selected),
        "pins": len(pins),
        "primaries": int((pins["sample_type"] == "primary").sum()) if len(pins) else 0,
        "alternates": int((pins["sample_type"] == "alternate").sum()) if len(pins) else 0,
        **psu_summary(buildings, selected),
    }
    logger.info("rooftop frame arm=%s: %s", arm, stats)
    return pin_features, hull_features, stats


def generate_frame(areas: list[dict], config: FrameConfig) -> FrameResult:
    """areas: [{"arm": "intervention"|"comparison", "geometry": <GeoJSON>}, ...].

    Each area may supply a ``geometry`` (drawn polygon or resolved admin area) or
    a ``circle`` ({lon, lat, radius_m}); see core.area_input.resolve_area.

    Decomposed into composable stages: build each arm's candidate PSU frame
    (footprints → cluster → per-candidate density), select PSUs, then render pins +
    stats. When TWO arms are present, the PPS draw is COORDINATED — a joint
    density-stratified matched selection over both arms' candidate frames so the
    selected PSUs share a settlement-density mix by construction (closing the drift
    that independent per-arm PPS leaves). A single arm draws exactly as before
    (size-stratified PPS, unchanged). Matched-design diagnostics (common/excluded
    density bands, restricted flag) are echoed on each arm's stats under ``matched``.
    """
    by_arm: dict[str, list] = {}
    for a in areas:
        by_arm.setdefault(a.get("arm", "intervention"), []).append(resolve_area(a))

    # One base seed per call drives the PPS draw + pin placement. None in the config
    # means re-roll: pick a fresh random base so each "Regenerate" yields a different
    # sample.
    import secrets

    base_seed = config.seed if config.seed is not None else secrets.randbelow(2_000_000_000)

    # Stage (a): build every arm's candidate frame.
    arm_frames = {arm: build_arm_frame(arm, geoms, config) for arm, geoms in by_arm.items()}
    arms = list(arm_frames.keys())

    # Stage (b): select PSUs. Two arms → JOINT matched selection on shared density
    # bands; single arm → the unchanged size-stratified PPS path.
    matched_meta: dict | None = None
    if len(arms) >= 2:
        result = select_psus_matched(
            {a: arm_frames[a].psu_frame for a in arms},
            n_take=config.target_clusters,
            seed=base_seed,
            size_balance_bands=config.size_balance_bands,
        )
        selected_by_arm = result["selected"]
        matched_meta = {
            "common_bands": result["common_bands"],
            "excluded_bands": result["excluded_bands"],
            "restricted": result["restricted"],
        }
    else:
        selected_by_arm = {
            arms[0]: select_psus(
                arm_frames[arms[0]].psu_frame,
                n_take=config.target_clusters,
                seed=base_seed,
                size_balance_bands=config.size_balance_bands,
            )
        }

    # Stage (c): render pins + stats per arm.
    pin_features: list[dict] = []
    hull_features: list[dict] = []
    stats: list[dict] = []
    for arm_idx, arm in enumerate(arms):
        pf, hf, s = _render_arm(arm_frames[arm], selected_by_arm[arm], config, base_seed, arm_idx)
        if matched_meta is not None:
            s["matched"] = matched_meta
        pin_features.extend(pf)
        hull_features.extend(hf)
        stats.append(s)

    return FrameResult(
        pins_geojson={"type": "FeatureCollection", "features": pin_features},
        hulls_geojson={"type": "FeatureCollection", "features": hull_features},
        stats=stats,
    )
