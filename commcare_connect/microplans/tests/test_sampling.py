"""Property tests for the Stage A sampling engine (pure; no network, no DB).

We can't byte-match the R pipeline (different k-means implementation, and the
pilot's R output CSVs aren't archived), so these assert the *invariants* the
methodology guarantees: filtering rules, every building in exactly one PSU,
merge floor, PPS count, 8+8 roles, and the 15m separation gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from commcare_connect.microplans.core.filters import FilterConfig, apply_frame_filters
from commcare_connect.microplans.core.geo import project_to_meters
from commcare_connect.microplans.sampling.cluster import ClusterConfig, cluster_buildings
from commcare_connect.microplans.sampling.sample import PinConfig, sample_pins, select_psus

# Maiduguri-ish anchor so UTM projection picks a sane zone.
LON0, LAT0 = 13.155, 11.832
M_PER_DEG = 111_320.0


def _scatter(n, *, spread_m=400, area_m2=40.0, seed=0):
    rng = np.random.default_rng(seed)
    dlat = rng.uniform(-spread_m, spread_m, n) / M_PER_DEG
    dlon = rng.uniform(-spread_m, spread_m, n) / (M_PER_DEG * np.cos(np.radians(LAT0)))
    return pd.DataFrame({"lon": LON0 + dlon, "lat": LAT0 + dlat, "area_m2": np.full(n, area_m2), "confidence": 0.8})


def _at(dn_m, de_m, area_m2):
    """A building offset (dn_m north, de_m east) from the anchor."""
    return {
        "lon": LON0 + de_m / (M_PER_DEG * np.cos(np.radians(LAT0))),
        "lat": LAT0 + dn_m / M_PER_DEG,
        "area_m2": area_m2,
        "confidence": 0.8,
    }


class TestFilters:
    def test_drops_large_roofs(self):
        df = pd.DataFrame([_at(0, 0, 40), _at(0, 50, 500), _at(0, 100, 331)])
        res = apply_frame_filters(df, FilterConfig())
        assert res.removed_large == 2  # 500 and 331 both exceed 330
        assert res.n_out == 1

    def test_keeps_clustered_tiny_roofs_drops_isolated(self):
        # Three tiny roofs within 12m of each other (a real compound) + one isolated tiny roof.
        clustered = [_at(0, 0, 5), _at(0, 5, 5), _at(5, 0, 5)]
        isolated = [_at(0, 500, 5)]
        normal = [_at(0, 510, 40), _at(0, 520, 40)]
        res = apply_frame_filters(pd.DataFrame(clustered + isolated + normal), FilterConfig())
        assert res.kept_tiny_clustered == 3
        assert res.removed_tiny_isolated == 1
        kept_areas = sorted(res.buildings["area_m2"].tolist())
        assert kept_areas == [5, 5, 5, 40, 40]

    def test_empty_input(self):
        res = apply_frame_filters(pd.DataFrame(columns=["lon", "lat", "area_m2"]), FilterConfig())
        assert res.n_out == 0


class TestCluster:
    def test_every_building_assigned_exactly_one_cluster(self):
        df = _scatter(300, seed=1)
        res = cluster_buildings(df, ClusterConfig(target_psus=5, seed=1))
        assert len(res.buildings) == 300
        assert res.buildings["cluster"].notna().all()
        # psu_frame building counts sum back to the total
        assert res.psu_frame["n_buildings"].sum() == 300

    def test_merge_enforces_min_size(self):
        df = _scatter(300, seed=2)
        res = cluster_buildings(df, ClusterConfig(target_psus=5, min_cluster_size=16, seed=2))
        sizes = res.psu_frame["n_buildings"].to_numpy()
        # Either a single merged cluster, or every surviving cluster clears the floor.
        assert len(sizes) == 1 or sizes.min() >= 16

    def test_empty_input(self):
        res = cluster_buildings(pd.DataFrame(columns=["lon", "lat", "area_m2"]), ClusterConfig())
        assert res.k_used == 0
        assert res.psu_frame.empty


def _psu_df(clusters, n_buildings, stratum="Low", p_psu=0.5):
    return pd.DataFrame(
        {
            "cluster": clusters,
            "n_buildings": n_buildings,
            "stratum": [stratum] * len(clusters),
            "P_psu": [p_psu] * len(clusters),
        }
    )


def test_frame_config_seed_is_optional_and_parsed():
    # seed=None (default) → generate_frame re-rolls a fresh random draw each call,
    # so "Regenerate plan" yields different PSUs/households. An explicit int pins a
    # reproducible sample (tests, deterministic walkthrough capture).
    from commcare_connect.microplans.sampling.frame import FrameConfig

    assert FrameConfig.from_payload({}).seed is None
    assert FrameConfig.from_payload({"seed": ""}).seed is None
    assert FrameConfig.from_payload({"seed": None}).seed is None
    assert FrameConfig.from_payload({"seed": 12345}).seed == 12345
    assert FrameConfig.from_payload({"seed": "777"}).seed == 777


def test_select_psus_draw_varies_with_seed_but_is_seed_reproducible():
    # Re-rolling with a new seed must be able to pick a different PSU set (the basis
    # for "Regenerate plan" producing different PSUs), while a given seed stays
    # reproducible (so a pinned/saved sample is stable).
    frame = _psu_df([f"C{i}" for i in range(12)], list(range(10, 34, 2)))
    draws = {tuple(sorted(select_psus(frame, n_take=4, seed=s)["cluster"])) for s in range(8)}
    assert len(draws) > 1  # different seeds → different draws
    assert sorted(select_psus(frame, n_take=4, seed=3)["cluster"]) == sorted(
        select_psus(frame, n_take=4, seed=3)["cluster"]
    )


class TestSelectPSUs:
    def test_selects_requested_count_with_inclusion_prob(self):
        psu = _psu_df([f"C{i}" for i in range(10)], [20] * 10)
        sel = select_psus(psu, n_take=4, seed=42)
        assert len(sel) == 4
        assert sel["cluster"].nunique() == 4  # no dupes
        assert set(sel["cluster"]).issubset(set(psu["cluster"]))
        # equal sizes, take 4 of 10 → each inclusion prob ~0.4
        assert np.allclose(sel["P_psu"], 0.4, atol=1e-6)

    def test_returns_all_when_take_exceeds_available(self):
        sel = select_psus(_psu_df(["C0", "C1"], [20, 30]), n_take=5)
        assert set(sel["cluster"]) == {"C0", "C1"}
        assert (sel["P_psu"] == 1.0).all()  # census → inclusion prob 1

    def test_empty(self):
        assert select_psus(pd.DataFrame(columns=["cluster", "n_buildings", "stratum"]), n_take=5).empty


class TestSamplePins:
    def _clustered(self, n, cluster="C0", seed=3):
        df = _scatter(n, spread_m=300, seed=seed)
        x, y, _ = project_to_meters(df["lon"].to_numpy(), df["lat"].to_numpy())
        df["x_m"], df["y_m"] = x, y
        df["cluster"] = cluster
        return df

    def test_role_counts(self):
        df = self._clustered(60)
        sel = _psu_df(["C0"], [60])
        pins = sample_pins(df, sel, PinConfig(n_primary=8, n_alternate=8, min_sep_m=15))
        assert len(pins) <= 16
        assert (pins["sample_type"] == "primary").sum() == 8

    def test_min_separation_enforced(self):
        df = self._clustered(80, seed=7)
        pins = sample_pins(df, _psu_df(["C0"], [80]), PinConfig(min_sep_m=15))
        pts = pins[["lon", "lat"]].to_numpy()
        x, y, _ = project_to_meters(pts[:, 0], pts[:, 1])
        P = np.column_stack([x, y])
        d = np.sqrt(((P[:, None] - P[None]) ** 2).sum(axis=2))
        np.fill_diagonal(d, np.inf)
        assert d.min() >= 15.0 - 1e-6

    def test_design_weights_on_primaries_only(self):
        df = self._clustered(60)
        # P_psu = 0.5, N_buildings = 60, m_eff = 8 → Pi = 0.5*8/60, weight = 1/Pi = 15
        sel = pd.DataFrame({"cluster": ["C0"], "n_buildings": [60], "stratum": ["Low"], "P_psu": [0.5]})
        pins = sample_pins(df, sel, PinConfig(n_primary=8, n_alternate=8, min_sep_m=15))
        primaries = pins[pins["sample_type"] == "primary"]
        alternates = pins[pins["sample_type"] == "alternate"]
        assert np.allclose(primaries["weight"], 15.0, atol=1e-6)
        assert alternates["weight"].isna().all()  # alternates carry no inclusion weight

    def test_handles_sparse_cluster(self):
        df = pd.DataFrame([_at(0, 0, 40), _at(0, 200, 40), _at(200, 0, 40)])
        x, y, _ = project_to_meters(df["lon"].to_numpy(), df["lat"].to_numpy())
        df["x_m"], df["y_m"], df["cluster"] = x, y, "C0"
        pins = sample_pins(df, _psu_df(["C0"], [3]), PinConfig(min_sep_m=15))
        assert len(pins) == 3


class TestConfigAndGuards:
    def test_frame_config_clamps_bad_inputs(self):
        from commcare_connect.microplans.sampling.frame import FrameConfig

        cfg = FrameConfig.from_payload(
            {
                "target_clusters": -5,
                "primary_per_psu": 0,
                "alternates_per_psu": -3,
                "min_confidence": 2.5,
                "area_min_m2": -10,
            }
        )
        assert cfg.target_clusters == 1  # clamped up from -5
        assert cfg.primary_per_psu == 1  # clamped up from 0
        assert cfg.alternates_per_psu == 0  # clamped up from -3
        assert cfg.min_confidence == 1.0  # clamped down from 2.5
        assert cfg.area_min_m2 == 0.0  # clamped up from -10

    def test_size_balance_bands_reads_new_key_and_legacy_alias(self):
        from commcare_connect.microplans.sampling.frame import FrameConfig

        # New canonical key.
        assert FrameConfig.from_payload({"size_balance_bands": 3}).size_balance_bands == 3
        # Legacy `size_strata` key (studies persisted before the rename) still maps over.
        assert FrameConfig.from_payload({"size_strata": 4}).size_balance_bands == 4
        # New key wins when both are present.
        assert FrameConfig.from_payload({"size_balance_bands": 2, "size_strata": 9}).size_balance_bands == 2
        # Absent → plain PPS (0).
        assert FrameConfig.from_payload({}).size_balance_bands == 0

    def test_fetch_buildings_rejects_oversized_area(self):
        import pytest
        from shapely.geometry import box

        from commcare_connect.microplans.core import footprints

        huge = box(0, 0, 40, 40)  # ~tens of millions of km² bbox — way over the cap
        with pytest.raises(ValueError, match="too large"):
            footprints.fetch_buildings(huge)  # raises before any S3 query


class TestStratification:
    def _grid(self, n_side, spacing_m, area_m2=40.0):
        # a regular grid of buildings around the anchor, in a single dense block
        rows = []
        for i in range(n_side):
            for j in range(n_side):
                rows.append(_at(i * spacing_m, j * spacing_m, area_m2))
        return pd.DataFrame(rows)

    def test_no_reference_point_means_single_low_pool(self):
        res = cluster_buildings(_scatter(200, seed=5), ClusterConfig(target_psus=4, seed=5))
        assert (res.psu_frame["stratum"] == "Low").all()
        assert "pct_50" not in res.psu_frame.columns  # no coverage metrics without distance

    def test_reference_point_computes_distance_and_strata(self):
        df = self._grid(20, 6)  # 400 buildings, ~6m apart, tight block
        # reference point right at the anchor → buildings are very close to it
        res = cluster_buildings(df, ClusterConfig(target_psus=3, seed=1), reference_point=(LON0, LAT0))
        assert "distance_to_visit" in res.buildings.columns
        assert {"pct_50", "pct_75", "pct_le_400"}.issubset(res.psu_frame.columns)
        assert res.psu_frame["stratum"].isin({"High", "Medium", "Low"}).all()


def test_psu_summary_reports_size_density_and_area_over_selected_psus():
    """The per-arm PSU balance summary (mean, sd) used by corrected comparability."""
    import pandas as pd

    from commcare_connect.microplans.sampling.frame import psu_summary

    # Two compact PSUs of known size + building footprint, projected coords included.
    c0 = _scatter(20, spread_m=60, seed=1).assign(cluster=0)
    c1 = _scatter(30, spread_m=60, seed=2).assign(cluster=1)
    buildings = pd.concat([c0, c1], ignore_index=True)
    buildings["area_m2"] = 100.0
    selected = pd.DataFrame(
        {"cluster": [0, 1], "n_buildings": [20, 30], "stratum": ["Low", "Low"], "P_psu": [0.5, 0.7]}
    )

    s = psu_summary(buildings, selected)
    assert round(s["psu_size"][0]) == 25  # mean of 20 and 30
    assert s["psu_size"][1] > 0  # sd present
    assert s["psu_density"][0] > 0  # buildings per km² within the PSU hulls
    assert round(s["bldg_area"][0]) == 100  # mean footprint area


def test_psu_summary_empty_is_safe():
    import pandas as pd

    from commcare_connect.microplans.sampling.frame import psu_summary

    s = psu_summary(pd.DataFrame(columns=["lon", "lat", "cluster", "area_m2"]), pd.DataFrame(columns=["cluster"]))
    assert s["psu_size"] == (0.0, 0.0) and s["psu_density"] == (0.0, 0.0)


def test_select_psus_size_stratified_spreads_across_size_range():
    """R2: size-stratified systematic PPS draws a matched size-mix across strata,
    instead of plain PPS concentrating the draw on the largest PSUs."""
    import numpy as np
    import pandas as pd

    from commcare_connect.microplans.sampling.sample import select_psus

    # 30 PSUs with sizes 16..45; plain PPS over-weights the big end.
    frame = pd.DataFrame({"cluster": range(30), "n_buildings": np.arange(16, 46), "stratum": "Low"})
    plain = select_psus(frame, n_take=12, size_balance_bands=0)
    strat = select_psus(frame, n_take=12, size_balance_bands=3)

    assert len(plain) == 12 and len(strat) == 12
    # Stratified reaches further into the small-PSU end than plain PPS.
    assert strat["n_buildings"].min() <= plain["n_buildings"].min()
    # Every stratum is represented (the draw is not all from one size band).
    assert strat["n_buildings"].min() < 26 and strat["n_buildings"].max() > 35
    # Inclusion probabilities are still present and in (0, 1].
    assert (strat["P_psu"] > 0).all() and (strat["P_psu"] <= 1).all()
