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


# --- Surrounding-ward control finder ---------------------------------------
# Match bands for the distribution-overlap score the "compare surrounding
# boundaries" tool ranks candidate control wards by. Overlap coefficient (OVL)
# of the two settlement-density distributions: 1.0 = identical shape, 0 =
# disjoint. We score the whole DISTRIBUTION, not the mean, precisely because a
# uniform ward and a bimodal urban+rural ward can share a mean settlement
# density yet be entirely different places — equal means don't make an
# exchangeable control; overlapping distributions do.
OVL_GOOD = 0.70
OVL_OK = 0.50
# Below this many valid clusters a "distribution" is meaningless — report
# insufficient data rather than a falsely precise overlap.
MIN_DIST_CLUSTERS = 3


def density_bin_edges(densities, bins: int = 12):
    """Fixed histogram bin edges anchored on the REFERENCE ward's density range.

    Passing these to every candidate's ``density_distribution_match`` puts all rows
    on one shared axis, so the reference (grey) bars are identical in every row and
    the candidate bars are directly comparable — and so the overlap a far-denser
    ward shows is honest. Returns None when the reference has too few points.

    The range is the reference's 2nd–98th percentile (not min/max): per-building
    k-NN density has a long upper tail (a few buildings in an ultra-dense core), and
    raw min/max would stretch the axis so far that all the real mass piles into the
    first bin. Out-of-range values clip to the nearest edge in ``_shared_hist``."""
    import numpy as np

    vals = np.asarray([x for x in (densities or []) if x and x > 0], dtype=float)
    if len(vals) < MIN_DIST_CLUSTERS:
        return None
    lo, hi = float(np.percentile(vals, 2)), float(np.percentile(vals, 98))
    if hi <= lo:
        lo, hi = float(vals.min()), float(vals.max())
        if hi <= lo:
            hi = lo + 1.0
    step = (hi - lo) / bins
    return [lo + step * i for i in range(bins + 1)]


def _shared_hist(a, b, bins: int = 12, edges=None):
    """Two density samples binned → ``(p_ref, p_cand, lo, hi)``, each normalised to
    sum 1 (or None when either side is empty). The single source for both the overlap
    score and the panel sparkline, so the number and the picture use the exact same
    bins. With ``edges`` given (reference-anchored), both arms are CLIPPED into that
    range — so the reference histogram is identical across rows and a candidate's
    out-of-range settlements pile at the nearest edge instead of vanishing."""
    import numpy as np

    a = np.asarray([x for x in a if x and x > 0], dtype=float)
    b = np.asarray([x for x in b if x and x > 0], dtype=float)
    if len(a) == 0 or len(b) == 0:
        return None
    if edges is not None:
        e = np.asarray(edges, dtype=float)
        lo, hi = float(e[0]), float(e[-1])
        a = np.clip(a, lo, hi)
        b = np.clip(b, lo, hi)
    else:
        lo = float(min(a.min(), b.min()))
        hi = float(max(a.max(), b.max()))
        if hi <= lo:
            hi = lo + 1.0  # both collapse to a single value → one bin holds all
        e = np.linspace(lo, hi, bins + 1)
    pa = np.histogram(a, bins=e)[0].astype(float)
    pb = np.histogram(b, bins=e)[0].astype(float)
    return pa / (pa.sum() or 1.0), pb / (pb.sum() or 1.0), lo, hi


def _overlap_coefficient(a, b, bins: int = 12, edges=None) -> float:
    """Histogram overlap (OVL) of two density samples: the area common to both
    normalised distributions, in [0, 1]."""
    import numpy as np

    h = _shared_hist(a, b, bins=bins, edges=edges)
    if h is None:
        return 0.0
    pa, pb, _lo, _hi = h
    return float(np.minimum(pa, pb).sum())


def matched_density_smd(ref_densities, cand_densities, *, edges=None, bins: int = 12) -> dict | None:
    """The cross-arm density SMD ACHIEVABLE AFTER matching on common support.

    The matched selector restricts BOTH arms to the density bands they share, then
    samples both there. So the balance the matched design will actually realise is the
    SMD between the two arms' densities *restricted to the common bands* — not the raw
    whole-ward SMD. This computes exactly that, so the control finder can rank by the
    balance you'd get, not the raw overlap.

    Returns ``{"matched_smd": float, "matched_band": good|ok|imbalanced,
    "common_fraction": float, "incomparable": bool}`` or ``None`` when either ward has
    too few points to band. ``incomparable`` is True when the wards share no density
    band at all (even the best match can't reach tolerance — pick a different control).
    """
    import numpy as np

    ref = np.asarray(list(ref_densities) if ref_densities is not None else [], dtype=float)
    cand = np.asarray(list(cand_densities) if cand_densities is not None else [], dtype=float)
    ref = ref[np.isfinite(ref) & (ref > 0)]
    cand = cand[np.isfinite(cand) & (cand > 0)]
    if len(ref) < MIN_DIST_CLUSTERS or len(cand) < MIN_DIST_CLUSTERS:
        return None

    e = edges if edges is not None else density_bin_edges([float(x) for x in ref], bins=bins)
    if e is None:
        return None
    e = np.asarray(e, dtype=float)
    lo, hi = float(e[0]), float(e[-1])
    nb = len(e) - 1

    def _bands_of(arr):
        # Out-of-support values (outside the intervention-anchored [lo, hi] range) get
        # band -1 — they are NOT clipped into the boundary bands, so an all-denser arm
        # reads as out-of-support rather than as sharing the top band.
        idx = np.clip(np.digitize(arr, e[1:-1]), 0, nb - 1).astype(int)
        idx[(arr < lo) | (arr > hi)] = -1
        return idx

    rb = _bands_of(ref)
    cb = _bands_of(cand)
    common = sorted((set(rb.tolist()) & set(cb.tolist())) - {-1})
    if not common:
        return {"matched_smd": None, "matched_band": "imbalanced", "common_fraction": 0.0, "incomparable": True}

    in_common_ref = np.isin(rb, common)
    in_common_cand = np.isin(cb, common)
    rsub, csub = ref[in_common_ref], cand[in_common_cand]
    smd = _smd(
        (float(rsub.mean()), float(rsub.std(ddof=1)) if len(rsub) > 1 else 0.0),
        (float(csub.mean()), float(csub.std(ddof=1)) if len(csub) > 1 else 0.0),
    )
    frac = float((in_common_ref.sum() + in_common_cand.sum()) / (len(ref) + len(cand)))
    return {
        "matched_smd": round(smd, 3),
        "matched_band": _band(smd),
        "common_fraction": round(frac, 3),
        "incomparable": False,
    }


def density_distribution_match(ref_densities, cand_densities, *, edges=None) -> dict:
    """Compare a candidate control ward's settlement-density distribution to the
    reference (intervention) ward's. Returns the overlap coefficient (the ranking
    key), the median density of each, their median gap %, the mean SMD (for
    continuity with the pairwise panel), and a band: ``good`` / ``ok`` / ``poor``
    (or ``insufficient`` when either ward has too few clusters to form a
    distribution).

    ``edges`` (from :func:`density_bin_edges` on the reference) anchors every
    candidate's histogram to the same axis, so the reference (grey) bars are
    identical across rows and the overlap/sparkline are directly comparable."""
    import numpy as np

    ref = np.asarray([x for x in (ref_densities or []) if x and x > 0], dtype=float)
    cand = np.asarray([x for x in (cand_densities or []) if x and x > 0], dtype=float)

    def _quartiles(arr):
        """[p25, p50, p75] as whole numbers — the spread the panel shows so the
        score is legible (two wards with equal medians but different IQRs read as
        different here, which is the whole point)."""
        if len(arr) == 0:
            return None
        p = np.percentile(arr, [25, 50, 75])
        return [int(round(float(p[0]))), int(round(float(p[1]))), int(round(float(p[2])))]

    q_ref, q_cand = _quartiles(ref), _quartiles(cand)

    if len(ref) < MIN_DIST_CLUSTERS or len(cand) < MIN_DIST_CLUSTERS:
        return {
            "overlap": None,
            "band": "insufficient",
            "median_ref": q_ref[1] if q_ref else None,
            "median_cand": q_cand[1] if q_cand else None,
            "median_gap_pct": None,
            "smd": None,
            "n_ref": int(len(ref)),
            "n_cand": int(len(cand)),
            "q_ref": q_ref,
            "q_cand": q_cand,
            "spark": None,
        }

    med_ref, med_cand = float(q_ref[1]), float(q_cand[1])
    gap = abs(med_ref - med_cand) / med_ref if med_ref > 0 else None
    smd = _smd(
        (float(ref.mean()), float(ref.std(ddof=1)) if len(ref) > 1 else 0.0),
        (float(cand.mean()), float(cand.std(ddof=1)) if len(cand) > 1 else 0.0),
    )
    # Overlap AND sparkline from ONE histogram over the (reference-anchored) bins, so
    # the number is exactly the shared area the picture shows.
    h = _shared_hist(ref, cand, edges=edges)
    overlap = 0.0
    spark = None
    if h is not None:
        pa, pb, lo, hi = h
        overlap = float(np.minimum(pa, pb).sum())
        spark = {
            "ref": [round(float(x), 3) for x in pa],
            "cand": [round(float(x), 3) for x in pb],
            "lo": int(round(lo)),
            "hi": int(round(hi)),
        }
    band = "good" if overlap >= OVL_GOOD else ("ok" if overlap >= OVL_OK else "poor")
    # The best-achievable matched balance: the cross-arm density SMD the matched
    # selector would realise after restricting to common support. This is the HEADLINE
    # ranking score for the control finder — rank candidates by the balance you'd
    # actually get, with raw distribution overlap kept as secondary context.
    matched = matched_density_smd(ref, cand, edges=edges) or {}
    return {
        "overlap": round(overlap, 3),
        "band": band,
        "median_ref": int(round(med_ref)),
        "median_cand": int(round(med_cand)),
        "median_gap_pct": round(gap * 100, 1) if gap is not None else None,
        "smd": round(smd, 2),
        "matched_smd": matched.get("matched_smd"),
        "matched_band": matched.get("matched_band"),
        "common_fraction": matched.get("common_fraction"),
        "incomparable": matched.get("incomparable", False),
        "n_ref": int(len(ref)),
        "n_cand": int(len(cand)),
        "q_ref": q_ref,
        "q_cand": q_cand,
        "spark": spark,
    }


def psu_arms_from_stats(
    stats: list[dict],
    *,
    names: dict | None = None,
    ward_density: dict | None = None,
) -> list[dict]:
    """Map per-arm sampling-stats dicts to the ``arms`` input ``arm_comparability_psu``
    consumes — the single assembly both comparability surfaces share.

    Each ``stats`` entry carries its own ``arm`` plus the sampling summary
    (``psu_size``/``psu_density``/``bldg_area`` as ``(mean, sd)``, ``n_psus``). The
    single-plan review endpoint passes the plan's per-arm ``sampling_stats`` directly;
    the study-group page passes one entry per member plan, tagged with that plan's arm.
    ``names``/``ward_density`` are optional per-arm maps for the row label + the
    context-only whole-ward density line.
    """
    names = names or {}
    ward_density = ward_density or {}
    arms: list[dict] = []
    for s in stats or []:
        if not isinstance(s, dict):
            continue
        arm = s.get("arm", "intervention")
        arms.append(
            {
                "arm": arm,
                "psu_size": s.get("psu_size", (0, 0)),
                "psu_density": s.get("psu_density", (0, 0)),
                "bldg_area": s.get("bldg_area", (0, 0)),
                "n_psus": s.get("n_psus") or 0,
                "ward_density": ward_density.get(arm, 0.0),
                "name": names.get(arm, ""),
                # Matched-design diagnostics the joint selector stamps on each arm's
                # stats (common/excluded density bands, restricted flag). Present →
                # the selection was coordinated; absent → an independent (legacy) draw.
                "matched": s.get("matched"),
            }
        )
    return arms


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
            # Display name for the arm's row (plan name on the group page, ward name on
            # the single-plan page). Echoed so the shared panel can label each row.
            "name": a.get("name", ""),
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
    gating = next((m for m in metrics if m["core"]), None)

    # Matched-design state. When the joint matched selector ran, it stamps each arm's
    # stats with a ``matched`` block (common/excluded density bands + restricted flag).
    # Presence of that block means the density SMD is in tolerance BY CONSTRUCTION
    # (the arms were sampled on shared density bands), so the panel can say so and
    # carry the common-support estimand note. ``restricted`` (no shared band at all)
    # is genuine incomparability — distinct from the old unconditional fail.
    matched_meta = next((a.get("matched") for a in arms if a.get("matched")), None)
    matched_design = matched_meta is not None
    restricted = bool(matched_meta.get("restricted")) if matched_meta else False
    excluded_bands = matched_meta.get("excluded_bands", []) if matched_meta else []
    # Genuine incomparability: the matched draw found no shared density support, OR it
    # ran but density still reads imbalanced (the best match can't reach tolerance).
    density_metric = next((m for m in metrics if m["metric"] == "psu_density"), None)
    density_imbalanced = bool(density_metric and density_metric["band"] == "imbalanced")
    incomparable = restricted or (matched_design and density_imbalanced)
    if incomparable:
        matched = False
    # The estimand the matched contrast targets: the common-support population (the
    # density range both arms share), stated as a one-liner the panel renders.
    estimand_note = (
        "Contrast is on the common-support population — the settlement-density range both arms share."
        if matched_design
        else None
    )
    return {
        "arms": out_arms,
        "metrics": metrics,
        "matched": matched,
        "reasons": reasons,
        "flags": flags,
        # Matched-design surface for the panel.
        "matched_design": matched_design,
        "incomparable": incomparable,
        "estimand_note": estimand_note,
        "excluded_bands": excluded_bands,
        # Sample size the SMDs are computed over (0 = legacy stats; template hides the
        # line). Lets the panel state its own n instead of asserting an SMD whose
        # denominator is invisible — the M&E-reviewer ask.
        "n_intervention": n_iv,
        "n_control": n_ct,
        "has_advisory": any(not m["core"] for m in metrics),
        # The gating metric's SMD + band, surfaced so the panel can state the explicit
        # decision ("keep / reject this control") in plain language with the number,
        # and so a 0.1<=SMD<0.25 pass is labelled "acceptable, marginal" rather than
        # dressed as a close match.
        "gating_smd": gating["smd"] if gating else None,
        "gating_band": gating["band"] if gating else None,
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
