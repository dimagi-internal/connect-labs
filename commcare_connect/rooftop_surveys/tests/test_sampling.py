"""Property tests for the Stage A sampling engine (pure; no network, no DB).

We can't byte-match the R pipeline (different k-means implementation, and the
pilot's R output CSVs aren't archived), so these assert the *invariants* the
methodology guarantees: filtering rules, every building in exactly one PSU,
merge floor, PPS count, 8+8 roles, and the 15m separation gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from commcare_connect.rooftop_surveys.sampling.cluster import ClusterConfig, cluster_buildings
from commcare_connect.rooftop_surveys.sampling.filters import FilterConfig, apply_frame_filters
from commcare_connect.rooftop_surveys.sampling.geo import project_to_meters
from commcare_connect.rooftop_surveys.sampling.sample import PinConfig, sample_pins, select_psus

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


class TestSelectPSUs:
    def test_selects_requested_count(self):
        psu = pd.DataFrame({"cluster": [f"C{i}" for i in range(10)], "n_buildings": [20] * 10})
        sel = select_psus(psu, n_take=4, seed=42)
        assert len(sel) == 4
        assert len(set(sel)) == 4  # no dupes
        assert set(sel).issubset(set(psu["cluster"]))

    def test_returns_all_when_take_exceeds_available(self):
        psu = pd.DataFrame({"cluster": ["C0", "C1"], "n_buildings": [20, 30]})
        assert set(select_psus(psu, n_take=5)) == {"C0", "C1"}

    def test_empty(self):
        assert select_psus(pd.DataFrame(columns=["cluster", "n_buildings"]), n_take=5) == []


class TestSamplePins:
    def _clustered(self, n, cluster="C0", seed=3):
        df = _scatter(n, spread_m=300, seed=seed)
        x, y, _ = project_to_meters(df["lon"].to_numpy(), df["lat"].to_numpy())
        df["x_m"], df["y_m"] = x, y
        df["cluster"] = cluster
        return df

    def test_role_counts(self):
        df = self._clustered(60)
        pins = sample_pins(df, ["C0"], PinConfig(n_primary=8, n_alternate=8, min_sep_m=15))
        assert (pins["role"] == "primary").sum() <= 8
        assert len(pins) <= 16
        # With 60 buildings over 300m there's room for 16 at >=15m apart.
        assert (pins["role"] == "primary").sum() == 8

    def test_min_separation_enforced(self):
        df = self._clustered(80, seed=7)
        pins = sample_pins(df, ["C0"], PinConfig(min_sep_m=15))
        pts = pins[["lon", "lat"]].to_numpy()
        x, y, _ = project_to_meters(pts[:, 0], pts[:, 1])
        P = np.column_stack([x, y])
        d = np.sqrt(((P[:, None] - P[None]) ** 2).sum(axis=2))
        np.fill_diagonal(d, np.inf)
        assert d.min() >= 15.0 - 1e-6

    def test_handles_sparse_cluster(self):
        # Only 3 buildings, all far apart → at most 3 pins, no crash.
        df = pd.DataFrame([_at(0, 0, 40), _at(0, 200, 40), _at(200, 0, 40)])
        x, y, _ = project_to_meters(df["lon"].to_numpy(), df["lat"].to_numpy())
        df["x_m"], df["y_m"], df["cluster"] = x, y, "C0"
        pins = sample_pins(df, ["C0"], PinConfig(min_sep_m=15))
        assert len(pins) == 3
