"""Arm comparability — is the control a fair counterfactual for the intervention?

Pure helper extracted so both the single-plan review page (``ArmComparabilityView``)
and the study-group page can ask the same question: given each arm's sampled
building count + its geometry, compute an accurate (UTM) area and density, then
flag whether the arms are within tolerance on building count and density.
"""

from __future__ import annotations

# Arms within this ratio on both building count and density read as "matched".
RATIO_TOLERANCE = 1.5

# Standardized-mean-difference balance bands (matching/propensity-score convention,
# Austin 2009 / Stuart 2010): |SMD| < 0.1 negligible, < 0.25 acceptable, else flagged.
SMD_GOOD = 0.10
SMD_OK = 0.25

# The metrics shown in the corrected PSU balance panel. The headline verdict gates on
# the ONE axis a matched-control design can't fix after the fact — settlement density,
# the structure of the places the survey actually visits. PSU size (a precision /
# variance driver) and building footprint size (an outcome covariate) are surfaced as
# advisory flags: a DiD analysis adjusts for measured baseline differences, so they're
# "flag and adjust", not "reject the control" — per CONSORT-style baseline balance.
_PSU_METRICS = [
    ("psu_density", "settlement density", True),
    ("psu_size", "PSU size", False),
    ("bldg_area", "building footprint size", False),
]


def _ratio(x: float, y: float) -> float:
    lo, hi = sorted((float(x), float(y)))
    return (hi / lo) if lo > 0 else float("inf")


def _smd(a: tuple, b: tuple) -> float:
    """Standardized mean difference between two (mean, sd) summaries."""
    import math

    (ma, sa), (mb, sb) = a, b
    pooled = math.sqrt((float(sa) ** 2 + float(sb) ** 2) / 2)
    return abs(float(ma) - float(mb)) / pooled if pooled > 0 else 0.0


def _band(smd: float) -> str:
    return "good" if smd < SMD_GOOD else ("ok" if smd < SMD_OK else "imbalanced")


def arm_comparability_psu(arms: list[dict]) -> dict:
    """Corrected arm comparability: compare the SELECTED PSUs / surveyed buildings,
    not whole-ward geography.

    Each arm carries the sampling summary ``{"arm", "psu_size": (mean, sd),
    "psu_density": (mean, sd), "bldg_area": (mean, sd), "ward_density": float}``.
    Returns per-metric standardized mean differences with balance bands, a headline
    ``matched`` gated on the two core metrics (settlement density + PSU size), and
    advisory ``flags`` for any imbalanced metric (e.g. building stock differing).
    ``ward_density`` is echoed as context only — never gates.

    The survey only ever visits the selected PSUs, so a control ward that looks
    mismatched on whole-ward density can still be a fair counterfactual when its
    settlements match the intervention's; this function measures that directly.
    """
    arms = [a for a in arms if a]
    out_arms = [
        {
            "arm": a.get("arm", "intervention"),
            "psu_size_mean": int(round(float(a.get("psu_size", (0, 0))[0]))),
            "psu_density_mean": int(round(float(a.get("psu_density", (0, 0))[0]))),
            "bldg_area_mean": int(round(float(a.get("bldg_area", (0, 0))[0]))),
            "ward_density": int(round(float(a.get("ward_density", 0) or 0))),
            # n selected PSUs each arm's means/SMDs are computed over (0 for legacy
            # stats persisted before n_psus was threaded through — the panel omits
            # the sample-size line when it's absent rather than asserting "n = 0").
            "n_psus": int(a.get("n_psus") or 0),
        }
        for a in arms
    ]
    if len(arms) < 2:
        return {"arms": out_arms, "metrics": [], "matched": None, "reasons": [], "flags": []}

    by_arm = {a.get("arm", "intervention"): a for a in arms}
    interv = by_arm.get("intervention", arms[0])
    other = next((a for a in arms if a.get("arm") in ("control", "comparison")), arms[1])

    metrics, reasons, flags = [], [], []
    matched = True
    for key, label, is_core in _PSU_METRICS:
        iv_pair, ct_pair = interv.get(key, (0, 0)), other.get(key, (0, 0))
        smd = _smd(iv_pair, ct_pair)
        band = _band(smd)
        metrics.append(
            {
                "metric": key,
                "label": label,
                # Display as whole numbers — these are counts/densities/areas; the
                # one-decimal "2041.0" read as false precision.
                "iv": int(round(float(iv_pair[0]))),
                "ct": int(round(float(ct_pair[0]))),
                "smd": round(smd, 2),
                "band": band,
                "core": is_core,
            }
        )
        if band == "imbalanced":
            if is_core:
                matched = False
                reasons.append(f"{label} differs (SMD {smd:.2f})")
            else:
                # Advisory: a baseline covariate to adjust for at analysis — flagged,
                # not failed. Never flips the headline verdict.
                flags.append(f"{label} (SMD {smd:.2f}) — adjust at analysis")
    n_iv = next((a["n_psus"] for a in out_arms if a["arm"] == "intervention"), 0)
    n_ct = next((a["n_psus"] for a in out_arms if a["arm"] in ("control", "comparison")), 0)
    return {
        "arms": out_arms,
        "metrics": metrics,
        "matched": matched,
        "reasons": reasons,
        "flags": flags,
        # Sample size the SMDs are computed over (0 = legacy stats; template hides the
        # line). Lets the panel state its own n instead of asserting an SMD whose
        # denominator is invisible — the M&E-reviewer ask.
        "n_intervention": n_iv,
        "n_control": n_ct,
        "has_advisory": any(not m["core"] for m in metrics),
    }


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
