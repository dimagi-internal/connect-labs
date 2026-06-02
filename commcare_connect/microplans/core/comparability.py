"""Arm comparability — is the control a fair counterfactual for the intervention?

Pure helper extracted so both the single-plan review page (``ArmComparabilityView``)
and the study-group page can ask the same question: given each arm's sampled
building count + its geometry, compute an accurate (UTM) area and density, then
flag whether the arms are within tolerance on building count and density.
"""

from __future__ import annotations

# Arms within this ratio on both building count and density read as "matched".
RATIO_TOLERANCE = 1.5


def _ratio(x: float, y: float) -> float:
    lo, hi = sorted((float(x), float(y)))
    return (hi / lo) if lo > 0 else float("inf")


def arm_comparability(arms: list[dict], ratio_tolerance: float = RATIO_TOLERANCE) -> dict:
    """``arms = [{"arm", "building_count", "geometry"}]`` → per-arm area/density + match.

    Returns ``{"arms": [{arm, building_count, area_km2, density_per_km2}], "matched":
    bool|None, "reasons": [str]}``. ``matched`` is ``None`` when fewer than two arms
    are present. The intervention arm is compared against the other arm (``control``
    or the legacy ``comparison``)."""
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform, unary_union

    from commcare_connect.microplans.core.geo import utm_epsg_for

    by_arm: dict[str, list] = {}
    counts: dict[str, int] = {}
    for a in arms:
        arm = a.get("arm", "intervention")
        try:
            by_arm.setdefault(arm, []).append(shape(a["geometry"]))
        except (KeyError, TypeError, ValueError):
            continue
        counts[arm] = counts.get(arm, 0) + int(a.get("building_count") or 0)

    out = []
    for arm, geoms in by_arm.items():
        try:
            geom = unary_union(geoms)
            c = geom.centroid
            tf = Transformer.from_crs(4326, utm_epsg_for(c.x, c.y), always_xy=True).transform
            area_km2 = transform(tf, geom).area / 1e6
        except Exception:  # noqa: BLE001
            area_km2 = 0.0
        bc = counts.get(arm, 0)
        density = round(bc / area_km2, 1) if area_km2 > 0 else 0.0
        out.append({"arm": arm, "building_count": bc, "area_km2": round(area_km2, 3), "density_per_km2": density})

    matched = None
    reasons: list[str] = []
    if len(out) >= 2:
        interv = next((x for x in out if x["arm"] == "intervention"), out[0])
        other = next((x for x in out if x["arm"] in ("control", "comparison")), out[1])
        bc_r = _ratio(interv["building_count"], other["building_count"])
        d_r = _ratio(interv["density_per_km2"], other["density_per_km2"])
        matched = bc_r <= ratio_tolerance and d_r <= ratio_tolerance
        if bc_r > ratio_tolerance:
            reasons.append(f"building counts differ {bc_r:.1f}×")
        if d_r > ratio_tolerance:
            reasons.append(f"densities differ {d_r:.1f}×")

    return {"arms": out, "matched": matched, "reasons": reasons}
