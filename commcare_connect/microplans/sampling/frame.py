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
from commcare_connect.microplans.core.footprints import fetch_buildings
from commcare_connect.microplans.sampling.cluster import ClusterConfig, cluster_buildings
from commcare_connect.microplans.sampling.sample import PinConfig, sample_pins, select_psus

logger = logging.getLogger(__name__)


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _clampf(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass
class FrameConfig:
    target_clusters: int = 25
    primary_per_psu: int = 8
    alternates_per_psu: int = 8
    min_confidence: float | None = 0.7
    area_min_m2: float = 9.0
    area_max_m2: float = 330.0
    # Optional (lon, lat) of the verification reference point. When set, clusters
    # are stratified High/Medium/Low on distance_to_visit; otherwise single pool.
    reference_point: tuple[float, float] | None = None

    @classmethod
    def from_payload(cls, d: dict) -> FrameConfig:
        rp = d.get("reference_point")
        conf = d.get("min_confidence")
        return cls(
            # clamp to sane bounds so a malformed payload can't crash or stall sampling
            target_clusters=_clamp(int(d.get("target_clusters", 25)), 1, 500),
            primary_per_psu=_clamp(int(d.get("primary_per_psu", 8)), 1, 100),
            alternates_per_psu=_clamp(int(d.get("alternates_per_psu", 8)), 0, 100),
            min_confidence=(None if conf in (None, "", 0) else _clampf(float(conf), 0.0, 1.0)),
            area_min_m2=_clampf(float(d.get("area_min_m2", 9)), 0.0, 1e6),
            area_max_m2=_clampf(float(d.get("area_max_m2", 330)), 1.0, 1e7),
            reference_point=(float(rp[0]), float(rp[1])) if rp else None,
        )


@dataclass
class FrameResult:
    pins_geojson: dict
    hulls_geojson: dict
    stats: list[dict] = field(default_factory=list)


def generate_frame(areas: list[dict], config: FrameConfig) -> FrameResult:
    """areas: [{"arm": "intervention"|"comparison", "geometry": <GeoJSON>}, ...].

    Each area may supply a ``geometry`` (drawn polygon or resolved admin area) or
    a ``circle`` ({lon, lat, radius_m}); see core.area_input.resolve_area.
    """
    by_arm: dict[str, list] = {}
    for a in areas:
        by_arm.setdefault(a.get("arm", "intervention"), []).append(resolve_area(a))

    pin_features: list[dict] = []
    hull_features: list[dict] = []
    stats: list[dict] = []

    for arm, geoms in by_arm.items():
        area = unary_union(geoms)
        buildings = fetch_buildings(area, min_confidence=config.min_confidence)
        filtered = apply_frame_filters(
            buildings, FilterConfig(area_min_m2=config.area_min_m2, area_max_m2=config.area_max_m2)
        )
        clustered = cluster_buildings(
            filtered.buildings,
            ClusterConfig(target_psus=config.target_clusters),
            reference_point=config.reference_point,
        )
        selected = select_psus(clustered.psu_frame, n_take=config.target_clusters)
        pins = sample_pins(
            clustered.buildings,
            selected,
            PinConfig(n_primary=config.primary_per_psu, n_alternate=config.alternates_per_psu),
        )
        stratum_by_cluster = dict(zip(selected["cluster"], selected["stratum"]))

        for _, p in pins.iterrows():
            pin_features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                    "properties": {
                        "arm": arm,
                        "cluster": p["cluster"],
                        "role": p["role"],
                        "order_in_cluster": int(p["order_in_cluster"]),
                        "stratum": stratum_by_cluster.get(p["cluster"], "Low"),
                        "weight": None if pd.isna(p["weight"]) else round(float(p["weight"]), 4),
                    },
                }
            )

        for cluster in selected["cluster"].tolist():
            pts = clustered.buildings[clustered.buildings["cluster"] == cluster]
            if len(pts) >= 3:
                hull = MultiPoint(list(zip(pts["lon"], pts["lat"]))).convex_hull
                hull_features.append(
                    {"type": "Feature", "geometry": mapping(hull), "properties": {"arm": arm, "cluster": cluster}}
                )

        stratum_counts = clustered.psu_frame["stratum"].value_counts().to_dict() if len(clustered.psu_frame) else {}
        stats.append(
            {
                "arm": arm,
                "fetched": filtered.n_in,
                "after_filters": filtered.n_out,
                "removed_tiny_isolated": filtered.removed_tiny_isolated,
                "removed_large": filtered.removed_large,
                "clusters_formed": len(clustered.psu_frame),
                "strata": {k: int(v) for k, v in stratum_counts.items()},
                "psus_selected": len(selected),
                "pins": len(pins),
                "primaries": int((pins["role"] == "primary").sum()) if len(pins) else 0,
                "alternates": int((pins["role"] == "alternate").sum()) if len(pins) else 0,
            }
        )
        logger.info("rooftop frame arm=%s: %s", arm, stats[-1])

    return FrameResult(
        pins_geojson={"type": "FeatureCollection", "features": pin_features},
        hulls_geojson={"type": "FeatureCollection", "features": hull_features},
        stats=stats,
    )
