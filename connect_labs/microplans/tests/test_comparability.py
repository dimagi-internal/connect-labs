"""Tests for the pure arm-comparability helper (study groups + single-plan reuse)."""

from __future__ import annotations


def _square(x0, y0, s=0.1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s], [x0, y0]]]}


def test_arm_comparability_computes_area_density_and_matched():
    from connect_labs.microplans.core.comparability import arm_comparability

    out = arm_comparability(
        [
            {"arm": "intervention", "building_count": 100, "geometry": _square(8.0, 11.0)},
            {"arm": "control", "building_count": 110, "geometry": _square(8.3, 11.0)},
        ]
    )
    arms = {a["arm"]: a for a in out["arms"]}
    assert set(arms) == {"intervention", "control"}
    assert arms["intervention"]["area_km2"] > 0
    assert arms["intervention"]["density_per_km2"] > 0
    # near-equal counts + same-size areas → matched (within 1.5x)
    assert out["matched"] is True


def test_arm_comparability_flags_not_matched_when_counts_far_apart():
    from connect_labs.microplans.core.comparability import arm_comparability

    out = arm_comparability(
        [
            {"arm": "intervention", "building_count": 100, "geometry": _square(8.0, 11.0)},
            {"arm": "control", "building_count": 1000, "geometry": _square(8.3, 11.0)},
        ]
    )
    assert out["matched"] is False
    assert out["reasons"]  # explains why (building counts differ N×)


def test_arm_comparability_matched_none_with_one_arm():
    from connect_labs.microplans.core.comparability import arm_comparability

    out = arm_comparability([{"arm": "intervention", "building_count": 100, "geometry": _square(8.0, 11.0)}])
    assert out["matched"] is None  # nothing to compare against
    assert len(out["arms"]) == 1


# --- PSU-based comparability (the corrected metric: compares the SELECTED PSUs /
#     surveyed buildings via standardized mean difference, not whole-ward density) ---


def _arm(name, *, size, density, bldg_area, ward_density=0.0, n_psus=None):
    """An arm's stored sampling summary: (mean, sd) per metric + whole-ward context."""
    arm = {
        "arm": name,
        "psu_size": size,
        "psu_density": density,
        "bldg_area": bldg_area,
        "ward_density": ward_density,
    }
    if n_psus is not None:
        arm["n_psus"] = n_psus
    return arm


def test_psu_comparability_matched_on_settlement_structure_ignores_whole_ward():
    # Kauran-Mata-like: whole-ward density is 2.4x off (old metric would REJECT), but
    # the selected PSUs match on settlement density + size → matched on what matters.
    from connect_labs.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40), ward_density=200),
            _arm("control", size=(55, 21), density=(8200, 2600), bldg_area=(123, 41), ward_density=83),
        ]
    )
    assert out["matched"] is True  # core metrics (size + density) within tolerance
    m = {x["metric"]: x for x in out["metrics"]}
    assert m["psu_density"]["band"] == "good" and m["psu_size"]["band"] in ("good", "ok")


def test_psu_comparability_flags_settlement_density_mismatch():
    # Gora-like: settlements much denser/sparser than the intervention → not matched,
    # even if building counts are similar.
    from connect_labs.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40)),
            _arm("control", size=(60, 30), density=(2500, 1500), bldg_area=(130, 45)),
        ]
    )
    assert out["matched"] is False
    m = {x["metric"]: x for x in out["metrics"]}
    assert m["psu_density"]["band"] == "imbalanced"
    assert any("density" in r for r in out["reasons"])


def test_psu_comparability_building_area_is_an_advisory_flag_not_a_gate():
    # Matched on size + density but building stock differs (high SMD on bldg_area):
    # headline stays matched, but a flag is surfaced for analysis-time adjustment.
    from connect_labs.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 30)),
            _arm("control", size=(55, 21), density=(8100, 2550), bldg_area=(160, 32)),
        ]
    )
    assert out["matched"] is True  # size + density gate the headline
    m = {x["metric"]: x for x in out["metrics"]}
    assert m["bldg_area"]["band"] == "imbalanced"
    assert out["flags"]  # building-stock difference surfaced as an advisory flag


def test_psu_comparability_surfaces_sample_size_and_advisory_flag():
    # The panel states its own n (PSUs each SMD is computed over) and exposes
    # has_advisory so the template can render the demoted covariate block.
    from connect_labs.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40), n_psus=8),
            _arm("control", size=(55, 21), density=(8200, 2600), bldg_area=(123, 41), n_psus=8),
        ]
    )
    assert out["n_intervention"] == 8 and out["n_control"] == 8
    assert out["has_advisory"] is True  # PSU size + building footprint are always advisory rows
    assert all("n_psus" in a for a in out["arms"])


def test_psu_comparability_n_defaults_zero_for_legacy_stats():
    # Stats persisted before n_psus was threaded through must not crash; n reads 0
    # so the template hides the sample-size line rather than asserting "n = 0".
    from connect_labs.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40)),
            _arm("control", size=(55, 21), density=(8200, 2600), bldg_area=(123, 41)),
        ]
    )
    assert out["n_intervention"] == 0 and out["n_control"] == 0


def test_psu_comparability_none_with_one_arm():
    from connect_labs.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu([_arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40))])
    assert out["matched"] is None


def test_psu_comparability_passes_name_through():
    # The shared panel renders each arm's display name (plan name on the group page,
    # ward name on the single-plan page), so the engine must echo it.
    from connect_labs.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40)) | {"name": "Attakar"},
            _arm("control", size=(55, 21), density=(8200, 2600), bldg_area=(123, 41)) | {"name": "Gura"},
        ]
    )
    names = {a["arm"]: a["name"] for a in out["arms"]}
    assert names["intervention"] == "Attakar" and names["control"] == "Gura"


def test_psu_arms_from_stats_builds_comparison_input():
    # Shared assembly: a list of per-arm sampling_stats dicts → the arms input
    # arm_comparability_psu consumes. Reused by the single-plan endpoint + group page.
    from connect_labs.microplans.core.comparability import arm_comparability_psu, psu_arms_from_stats

    stats = [
        {
            "arm": "intervention",
            "psu_size": (53, 20),
            "psu_density": (8000, 2500),
            "bldg_area": (120, 40),
            "n_psus": 8,
        },
        {"arm": "comparison", "psu_size": (55, 21), "psu_density": (8200, 2600), "bldg_area": (123, 41), "n_psus": 8},
    ]
    arms = psu_arms_from_stats(stats, names={"intervention": "Attakar", "comparison": "Gura"})
    assert {a["arm"] for a in arms} == {"intervention", "comparison"}
    out = arm_comparability_psu(arms)
    assert out["matched"] is True
    assert out["n_intervention"] == 8
    assert any(a["name"] == "Attakar" for a in out["arms"])


# --- Surrounding-ward control finder: distribution-overlap scoring -----------


def test_density_match_identical_distributions_high_overlap():
    from connect_labs.microplans.core.comparability import density_distribution_match

    d = [100, 200, 300, 400, 500, 600, 700, 800]
    out = density_distribution_match(d, list(d))
    assert out["band"] == "good"
    assert out["overlap"] == 1.0
    assert out["median_gap_pct"] == 0.0
    assert out["n_ref"] == out["n_cand"] == len(d)


def test_density_match_same_mean_different_shape_is_not_good():
    """A uniform ward and a bimodal urban+rural ward can share a mean yet not
    overlap — the whole point of scoring the distribution, not the mean."""
    from connect_labs.microplans.core.comparability import density_distribution_match

    uniform = [440, 450, 460, 455, 445, 448, 452, 458]  # tight around ~450
    bimodal = [100, 110, 90, 105, 800, 810, 790, 805]  # low + high, mean ~450
    out = density_distribution_match(uniform, bimodal)
    assert out["band"] in ("ok", "poor")
    assert out["overlap"] < 0.5


def test_density_match_disjoint_ranges_poor():
    from connect_labs.microplans.core.comparability import density_distribution_match

    out = density_distribution_match([100, 120, 140, 160], [900, 950, 1000, 1050])
    assert out["band"] == "poor"
    assert out["overlap"] == 0.0


def test_density_match_insufficient_clusters():
    from connect_labs.microplans.core.comparability import density_distribution_match

    out = density_distribution_match([100, 200], [300, 400, 500, 600])
    assert out["band"] == "insufficient"
    assert out["overlap"] is None
    assert out["smd"] is None


def test_rank_ward_matches_orders_best_first_errors_last():
    from connect_labs.microplans.tasks import _rank_ward_matches

    rows = [
        {"name": "lo", "overlap": 0.3},
        {"name": "err", "overlap": None, "status": "error"},
        {"name": "hi", "overlap": 0.9},
    ]
    ranked = _rank_ward_matches(rows)
    assert [r["name"] for r in ranked] == ["hi", "lo", "err"]


# --- Matched-design control finder + panel (the methodology change) ----------


def test_matched_density_smd_lower_than_raw_on_partial_overlap():
    """The best-achievable matched balance restricts to common support, so on two wards
    that overlap in a broad middle range but each carries an exclusive tail, the matched
    SMD is materially better than the raw whole-ward SMD."""
    import numpy as np

    from connect_labs.microplans.core.comparability import _smd, matched_density_smd

    rng = np.random.default_rng(0)
    shared = rng.normal(5000, 600, 60)  # the broad common support both wards populate
    ref = np.concatenate([shared, rng.normal(2200, 200, 30)])  # ref also has a sparse tail
    cand = np.concatenate([shared + rng.normal(0, 50, 60), rng.normal(8500, 200, 30)])  # cand a dense tail
    out = matched_density_smd(list(ref), list(cand))
    assert out is not None and not out["incomparable"]
    raw = _smd(
        (float(ref.mean()), float(ref.std(ddof=1))),
        (float(cand.mean()), float(cand.std(ddof=1))),
    )
    # Matched (restricted to the shared ~5000 band) is much better than raw whole-ward.
    assert out["matched_smd"] < raw
    assert out["matched_smd"] < 0.25
    assert 0.0 < out["common_fraction"] < 1.0  # a real subset, not everything


def test_matched_density_smd_flags_incomparable_on_disjoint_support():
    """When two wards share no density band, the matched score is flagged incomparable
    — even the best match can't reach tolerance; the finder must surface that."""
    from connect_labs.microplans.core.comparability import matched_density_smd

    out = matched_density_smd([100, 120, 140, 160, 180], [900, 950, 1000, 1050, 1100])
    assert out is not None
    assert out["incomparable"] is True
    assert out["common_fraction"] == 0.0


def test_density_match_surfaces_matched_smd_as_headline():
    """density_distribution_match now carries the best-achievable matched SMD so the
    control finder can rank by it (the balance you'd actually get)."""
    import numpy as np

    from connect_labs.microplans.core.comparability import density_distribution_match

    rng = np.random.default_rng(1)
    ref = list(rng.normal(5000, 800, 80))
    cand = list(rng.normal(5200, 800, 80))  # broadly overlapping → a real matched score
    out = density_distribution_match(ref, cand)
    assert "matched_smd" in out and out["matched_smd"] is not None
    assert "matched_band" in out and "common_fraction" in out


def test_rank_ward_matches_ranks_by_matched_smd_then_overlap():
    """The control finder's headline key is best-achievable matched balance (lower
    matched SMD first); incomparable / unscored rows sink."""
    from connect_labs.microplans.tasks import _rank_ward_matches

    rows = [
        {"name": "decent_overlap_bad_match", "overlap": 0.8, "matched_smd": 0.30, "incomparable": False},
        {"name": "best_match", "overlap": 0.6, "matched_smd": 0.05, "incomparable": False},
        {"name": "incomparable", "overlap": 0.4, "matched_smd": None, "incomparable": True},
        {"name": "errored", "overlap": None, "status": "error"},
    ]
    ranked = [r["name"] for r in _rank_ward_matches(rows)]
    # Lowest matched SMD leads, even though it has lower raw overlap. Incomparable +
    # errored sink to the bottom.
    assert ranked[0] == "best_match"
    assert ranked[1] == "decent_overlap_bad_match"
    assert set(ranked[2:]) == {"incomparable", "errored"}


def _matched_arm(name, *, size, density, bldg_area, matched_meta=None):
    arm = {"arm": name, "psu_size": size, "psu_density": density, "bldg_area": bldg_area, "ward_density": 0.0}
    if matched_meta is not None:
        arm["matched"] = matched_meta
    return arm


def test_panel_states_matched_design_and_estimand_note():
    """When the joint matched selector ran, the panel reports density is balanced by
    design and carries the common-support estimand note."""
    from connect_labs.microplans.core.comparability import arm_comparability_psu

    meta = {"common_bands": [{"band": 3}], "excluded_bands": [], "restricted": False}
    out = arm_comparability_psu(
        [
            _matched_arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40), matched_meta=meta),
            _matched_arm("control", size=(55, 21), density=(8050, 2550), bldg_area=(123, 41), matched_meta=meta),
        ]
    )
    assert out["matched"] is True
    assert out["matched_design"] is True
    assert out["incomparable"] is False
    assert out["estimand_note"] and "common-support" in out["estimand_note"]


def test_panel_flags_genuine_incomparability_when_no_shared_support():
    """A matched run that found no shared density band (restricted) is genuine
    incomparability — a distinct state from the old unconditional fail."""
    from connect_labs.microplans.core.comparability import arm_comparability_psu

    meta = {"common_bands": [], "excluded_bands": [{"band": 0}, {"band": 11}], "restricted": True}
    out = arm_comparability_psu(
        [
            _matched_arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40), matched_meta=meta),
            _matched_arm("control", size=(55, 21), density=(2200, 800), bldg_area=(123, 41), matched_meta=meta),
        ]
    )
    assert out["incomparable"] is True
    assert out["matched"] is False
    assert out["matched_design"] is True


def test_panel_no_matched_design_when_meta_absent_is_backward_compatible():
    """Legacy stats with no matched block behave exactly as before — matched gated on
    density SMD, no estimand note, no incomparable state."""
    from connect_labs.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _matched_arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40)),
            _matched_arm("control", size=(55, 21), density=(8200, 2600), bldg_area=(123, 41)),
        ]
    )
    assert out["matched"] is True
    assert out["matched_design"] is False
    assert out["estimand_note"] is None
    assert out["incomparable"] is False


def test_density_match_returns_quartiles_and_sparkline():
    from connect_labs.microplans.core.comparability import density_distribution_match

    out = density_distribution_match([100, 200, 300, 400, 500, 600], [150, 250, 350, 450, 550, 650])
    assert out["q_ref"] and len(out["q_ref"]) == 3 and out["q_ref"][0] <= out["q_ref"][1] <= out["q_ref"][2]
    assert out["q_cand"] and len(out["q_cand"]) == 3
    assert out["spark"] is not None
    assert len(out["spark"]["ref"]) == len(out["spark"]["cand"])  # same shared bins
    assert abs(sum(out["spark"]["ref"]) - 1.0) < 0.02  # normalised (rounded to 3dp per bin)
    assert out["spark"]["lo"] <= out["spark"]["hi"]


# --- matched-DRAW estimate (band-mix rebalancing) --------------------------
# These pin the corrected metric: it must simulate the matched SELECTION (both arms
# reweighted to the intervention's band proportions), not just the all-points
# common-band SMD. The band-MIX imbalance is what matching removes.


def _all_points_common_band_smd(ref, cand, edges=None):
    """The OLD metric: SMD over every point that lands in a shared band, keeping each
    arm's own band mix. Kept here as the baseline the corrected metric must beat."""
    import numpy as np

    from connect_labs.microplans.core.comparability import _smd, density_bin_edges

    ref = np.asarray([x for x in ref if x and x > 0], dtype=float)
    cand = np.asarray([x for x in cand if x and x > 0], dtype=float)
    e = np.asarray(edges if edges is not None else density_bin_edges(list(ref)), dtype=float)
    nb = len(e) - 1

    def _bands(arr):
        idx = np.clip(np.digitize(arr, e[1:-1]), 0, nb - 1).astype(int)
        idx[(arr < e[0]) | (arr > e[-1])] = -1
        return idx

    rb, cb = _bands(ref), _bands(cand)
    common = sorted((set(rb.tolist()) & set(cb.tolist())) - {-1})
    rsub, csub = ref[np.isin(rb, common)], cand[np.isin(cb, common)]
    return _smd(
        (float(rsub.mean()), float(rsub.std(ddof=1))),
        (float(csub.mean()), float(csub.std(ddof=1))),
    )


def test_matched_draw_estimate_lower_than_all_points_when_band_mix_differs():
    """(a) Two arms share the SAME density support but differ in band-MIX (ref mostly
    low-density, cand mostly high-density). The matched selector reweights BOTH arms to
    the intervention's band mix, removing that mix imbalance — so the corrected
    matched-draw estimate must be MATERIALLY lower than the old all-points common-band
    SMD (which still carries the mix imbalance)."""
    import numpy as np

    from connect_labs.microplans.core.comparability import density_bin_edges, matched_density_smd

    rng = np.random.default_rng(3)
    # Same two density modes for both arms (so the support — and bands — are shared),
    # but opposite mixes: ref is 80% low / 20% high, cand is 20% low / 80% high.
    ref = list(rng.normal(2000, 150, 80)) + list(rng.normal(6000, 150, 20))
    cand = list(rng.normal(2000, 150, 20)) + list(rng.normal(6000, 150, 80))
    edges = density_bin_edges(ref)

    out = matched_density_smd(ref, cand, edges=edges)
    old = _all_points_common_band_smd(ref, cand, edges=edges)

    assert out is not None and not out["incomparable"]
    # The mix imbalance dominates `old`; the matched draw removes it.
    assert out["matched_smd"] < old - 0.5
    # And the within-band residual is small enough to read as a usable control.
    assert out["matched_smd"] < 0.25


def test_matched_draw_estimate_equals_all_points_when_band_mix_already_matches():
    """(b) When the two arms already share the intervention's band mix, there is nothing
    for matching to rebalance — the corrected estimate must equal the all-points
    common-band SMD."""
    import numpy as np

    from connect_labs.microplans.core.comparability import density_bin_edges, matched_density_smd

    rng = np.random.default_rng(11)
    # Same mix (same per-mode counts) for both arms, shifted slightly so there IS a
    # within-band difference to measure (otherwise both numbers are ~0 trivially).
    ref = list(rng.normal(2000, 120, 50)) + list(rng.normal(6000, 120, 50))
    cand = list(rng.normal(2120, 120, 50)) + list(rng.normal(6120, 120, 50))
    edges = density_bin_edges(ref)

    out = matched_density_smd(ref, cand, edges=edges)
    old = _all_points_common_band_smd(ref, cand, edges=edges)
    assert out is not None and not out["incomparable"]
    # Equal band mix → reweighting is a no-op → the two estimates agree closely.
    assert abs(out["matched_smd"] - round(old, 3)) < 0.1


def test_matched_draw_estimate_incomparable_on_disjoint_support():
    """(c) Disjoint density support → no shared band → incomparable (the matched draw
    can't be simulated at all)."""
    from connect_labs.microplans.core.comparability import matched_density_smd

    out = matched_density_smd([100, 120, 140, 160, 180], [900, 950, 1000, 1050, 1100])
    assert out is not None and out["incomparable"] is True
    assert out["matched_smd"] is None and out["common_fraction"] == 0.0


def test_matched_draw_estimate_tracks_brute_force_resample_oracle():
    """(d) Oracle check: brute-force the proportional matched draw with numpy — for each
    common band, resample BOTH arms to the intervention's per-band proportion, pool, and
    take the SMD of the two resampled arms. The closed-form estimate must track that
    resample SMD in the same direction and magnitude."""
    import numpy as np

    from connect_labs.microplans.core.comparability import density_bin_edges, matched_density_smd

    rng = np.random.default_rng(7)
    ref = list(rng.normal(2000, 200, 70)) + list(rng.normal(6000, 200, 30))
    cand = list(rng.normal(2050, 220, 25)) + list(rng.normal(6080, 230, 75))
    edges = np.asarray(density_bin_edges(ref), dtype=float)
    nb = len(edges) - 1

    def _bands(arr):
        arr = np.asarray(arr, dtype=float)
        idx = np.clip(np.digitize(arr, edges[1:-1]), 0, nb - 1).astype(int)
        idx[(arr < edges[0]) | (arr > edges[-1])] = -1
        return arr, idx

    ra, rb = _bands(ref)
    ca, cb = _bands(cand)
    common = sorted((set(rb.tolist()) & set(cb.tolist())) - {-1})
    # Intervention band weights (the proportion the selector imposes on BOTH arms).
    ref_common_n = int(np.isin(rb, common).sum())
    weights = {b: np.count_nonzero(rb == b) / ref_common_n for b in common}

    # Brute-force matched draw: build a big resample of each arm honouring `weights`.
    N = 40000
    oracle = np.random.default_rng(99)

    def _resample(vals, bands):
        out = []
        for b in common:
            pool = vals[bands == b]
            k = int(round(weights[b] * N))
            if k > 0 and len(pool):
                out.append(oracle.choice(pool, size=k, replace=True))
        return np.concatenate(out)

    rs, cs = _resample(ra, rb), _resample(ca, cb)
    from connect_labs.microplans.core.comparability import _smd

    oracle_smd = _smd(
        (float(rs.mean()), float(rs.std(ddof=1))),
        (float(cs.mean()), float(cs.std(ddof=1))),
    )
    out = matched_density_smd(ref, cand, edges=edges)
    assert out is not None and not out["incomparable"]
    # Same ballpark as the brute-force resample (both estimate the matched-draw SMD).
    assert abs(out["matched_smd"] - oracle_smd) < 0.1
