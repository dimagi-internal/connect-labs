"""PPS selection + within-PSU pin sampling — port of the R selection stage.

1. `select_psus`: systematic PPS draw of PSUs with probability proportional to
   building count (UPsystematic, with a weighted-without-replacement fallback if
   the systematic pass lands off the requested count).
2. `sample_pins`: inside each selected PSU, iteratively pick buildings that are
   at least `min_sep_m` apart (spatial thinning), taking `n_primary` then
   `n_alternate`. Primaries are the targets; alternates are the 15m-substitution
   fallbacks the FLW uses when a primary is non-residential/unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def select_psus(psu_frame: pd.DataFrame, n_take: int, seed: int = 20250926) -> list[str]:
    if psu_frame.empty or n_take <= 0:
        return []
    sizes = psu_frame["n_buildings"].to_numpy(dtype=float)
    clusters = psu_frame["cluster"].tolist()
    n = len(sizes)
    if n_take >= n:
        return clusters

    rng = np.random.default_rng(seed)
    pi = n_take * sizes / sizes.sum()
    pi = np.clip(pi, 1e-9, 1 - 1e-9)
    u = rng.random()
    cs = np.concatenate([[0.0], np.cumsum(pi)])
    sel = np.floor(cs[1:] + u) > np.floor(cs[:-1] + u)

    if int(sel.sum()) != n_take:
        idx = rng.choice(n, size=n_take, replace=False, p=sizes / sizes.sum())
        return [clusters[i] for i in sorted(idx)]
    return [clusters[i] for i in np.flatnonzero(sel)]


@dataclass(frozen=True)
class PinConfig:
    n_primary: int = 8
    n_alternate: int = 8
    min_sep_m: float = 15.0
    seed: int = 20250927


def sample_pins(
    buildings: pd.DataFrame, selected_clusters: list[str], config: PinConfig | None = None
) -> pd.DataFrame:
    """Return one row per sampled pin: cluster, lon, lat, area_m2, role, order_in_cluster."""
    config = config or PinConfig()
    out = []
    rng = np.random.default_rng(config.seed)
    target_n = config.n_primary + config.n_alternate

    for cluster in selected_clusters:
        sub = buildings[buildings["cluster"] == cluster].reset_index(drop=True)
        if sub.empty:
            continue
        pts = sub[["x_m", "y_m"]].to_numpy()
        picked = _thin_to_separated(pts, target_n, config.min_sep_m, rng)
        for rank, i in enumerate(picked, start=1):
            out.append(
                {
                    "cluster": cluster,
                    "lon": float(sub.loc[i, "lon"]),
                    "lat": float(sub.loc[i, "lat"]),
                    "area_m2": float(sub.loc[i, "area_m2"]) if "area_m2" in sub.columns else None,
                    "role": "primary" if rank <= config.n_primary else "alternate",
                    "order_in_cluster": rank,
                }
            )
    return pd.DataFrame(out, columns=["cluster", "lon", "lat", "area_m2", "role", "order_in_cluster"])


def _thin_to_separated(pts: np.ndarray, target_n: int, min_sep_m: float, rng: np.random.Generator) -> list[int]:
    n = len(pts)
    target = min(n, target_n)
    selected: list[int] = []
    available = np.arange(n)
    while len(selected) < target and available.size > 0:
        choice = rng.choice(available)
        selected.append(int(choice))
        available = available[available != choice]
        if len(selected) > 0 and available.size > 0:
            sel_pts = pts[selected]
            avail_pts = pts[available]
            # min distance from each available point to any selected point
            d = np.sqrt(((avail_pts[:, None, :] - sel_pts[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
            available = available[d >= min_sep_m]
    return selected
