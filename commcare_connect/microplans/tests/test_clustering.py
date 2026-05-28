"""Tests for the shared clustering strategies (pure; no network/DB)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from commcare_connect.microplans.core import clustering

LON0, LAT0 = 13.155, 11.832
M_PER_DEG = 111_320.0


def _scatter(n, spread_m=400, seed=0):
    rng = np.random.default_rng(seed)
    dlat = rng.uniform(-spread_m, spread_m, n) / M_PER_DEG
    dlon = rng.uniform(-spread_m, spread_m, n) / (M_PER_DEG * np.cos(np.radians(LAT0)))
    return pd.DataFrame({"lon": LON0 + dlon, "lat": LAT0 + dlat, "area_m2": 40.0})


class TestKmeansMerge:
    def test_assigns_all_and_builds_base_frame(self):
        out = clustering.kmeans_merge(_scatter(300, seed=1), k=15, min_cluster_size=16, seed=1)
        assert len(out.buildings) == 300
        assert out.buildings["cluster"].notna().all()
        assert {"cluster", "n_buildings", "centroid_lon", "centroid_lat", "radius95_m"}.issubset(out.psu_frame.columns)
        assert "stratum" not in out.psu_frame.columns  # base frame has no strata
        sizes = out.psu_frame["n_buildings"].to_numpy()
        assert len(sizes) == 1 or sizes.min() >= 16  # merge floor

    def test_empty(self):
        out = clustering.kmeans_merge(pd.DataFrame(columns=["lon", "lat"]), k=5)
        assert out.k_used == 0 and out.psu_frame.empty


class TestBalancedKmeans:
    def test_n_clusters_yields_balanced_sizes(self):
        out = clustering.balanced_kmeans(_scatter(120, seed=2), n_clusters=6, balance_tolerance=0.1, seed=2)
        sizes = out.psu_frame["n_buildings"].to_numpy()
        assert len(sizes) == 6
        # 120/6 = 20 each; within +/-10% → all in [18, 22]
        assert sizes.min() >= 18 and sizes.max() <= 22
        assert int(sizes.sum()) == 120

    def test_buildings_per_cluster(self):
        out = clustering.balanced_kmeans(
            _scatter(100, seed=3), buildings_per_cluster=25, balance_tolerance=0.1, seed=3
        )
        # 100/25 = 4 clusters of ~25
        assert len(out.psu_frame) == 4
        assert out.psu_frame["n_buildings"].max() <= 28


class TestGridClusters:
    def test_assigns_all_and_is_deterministic(self):
        df = _scatter(300, spread_m=500, seed=4)
        out = clustering.grid_clusters(df, cell_size_m=200)
        again = clustering.grid_clusters(df, cell_size_m=200)
        assert len(out.buildings) == 300
        assert out.buildings["cluster"].notna().all()
        # every building counted once across cells
        assert int(out.psu_frame["n_buildings"].sum()) == 300
        # deterministic: same cells, same sizes (no random seed)
        assert out.psu_frame["n_buildings"].tolist() == again.psu_frame["n_buildings"].tolist()

    def test_smaller_cells_make_more_clusters(self):
        df = _scatter(300, spread_m=600, seed=5)
        coarse = clustering.grid_clusters(df, cell_size_m=400)
        fine = clustering.grid_clusters(df, cell_size_m=100)
        assert len(fine.psu_frame) > len(coarse.psu_frame)

    def test_empty(self):
        out = clustering.grid_clusters(pd.DataFrame(columns=["lon", "lat"]), cell_size_m=200)
        assert out.k_used == 0 and out.psu_frame.empty
