"""PPS selection + within-PSU pin sampling — port of the R selection stage.

1. `select_psus`: systematic PPS draw of PSUs with probability proportional to
   building count (UPsystematic, with a weighted-without-replacement fallback).
   Returns the selected PSUs *with* their inclusion probability `P_psu`.
2. `sample_pins`: inside each selected PSU, iteratively pick buildings ≥ min_sep_m
   apart (spatial thinning), taking `n_primary` then `n_alternate`. Primaries are
   the targets; alternates are the 15m-substitution fallbacks.

Design-based inclusion weights (primaries only), matching the R pipeline so
downstream coverage estimates are unbiased:
    P_build_given_psu = m_eff / N_buildings
    Pi                = P_psu * P_build_given_psu
    weight            = 1 / Pi
where `m_eff` is the effective number of primaries actually placed in the PSU.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def select_psus(psu_frame: pd.DataFrame, n_take: int, seed: int = 20250926, size_strata: int = 0) -> pd.DataFrame:
    """Return selected PSUs as a DataFrame [cluster, n_buildings, stratum, P_psu].

    `P_psu` is the PSU's inclusion probability (proportional to building count).

    `size_strata > 1` switches on **R2 size-stratified systematic PPS** (the DHS/MICS
    standard): split the PSU frame into that many size bands, allocate the draw evenly
    across bands, and run systematic PPS within each band. This draws a *matched
    size-mix* so two arms sampled the same way are comparable on PSU size by
    construction, instead of plain PPS concentrating each arm's draw on its own
    largest settlements. Inclusion probabilities (and hence design weights 1/Pi) are
    computed per band so estimates stay design-unbiased. `0`/`1` = plain PPS.
    """
    cols = ["cluster", "n_buildings", "stratum", "P_psu"]
    if psu_frame.empty or n_take <= 0:
        return pd.DataFrame(columns=cols)

    sizes = psu_frame["n_buildings"].to_numpy(dtype=float)
    n = len(sizes)

    if n_take >= n:
        out = psu_frame.copy()
        out["P_psu"] = 1.0
        if "stratum" not in out.columns:
            out["stratum"] = "Low"
        return out[cols].reset_index(drop=True)

    if size_strata and size_strata > 1:
        return _select_size_stratified(psu_frame, sizes, n_take, seed, size_strata, cols)

    pi = np.clip(n_take * sizes / sizes.sum(), 1e-9, 1 - 1e-9)
    rng = np.random.default_rng(seed)
    u = rng.random()
    cs = np.concatenate([[0.0], np.cumsum(pi)])
    sel = np.floor(cs[1:] + u) > np.floor(cs[:-1] + u)
    if int(sel.sum()) != n_take:
        chosen = np.sort(rng.choice(n, size=n_take, replace=False, p=sizes / sizes.sum()))
    else:
        chosen = np.flatnonzero(sel)

    out = psu_frame.iloc[chosen].copy()
    out["P_psu"] = pi[chosen]
    if "stratum" not in out.columns:
        out["stratum"] = "Low"
    return out[cols].reset_index(drop=True)


def _systematic_pps(sizes: np.ndarray, take: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Systematic-PPS draw of `take` indices ∝ sizes; returns (local_idx, inclusion_pi)."""
    pi = np.clip(take * sizes / sizes.sum(), 1e-9, 1 - 1e-9)
    u = rng.random()
    cs = np.concatenate([[0.0], np.cumsum(pi)])
    sel = np.floor(cs[1:] + u) > np.floor(cs[:-1] + u)
    local = np.flatnonzero(sel)
    if len(local) != take:
        local = np.sort(rng.choice(len(sizes), size=take, replace=False, p=sizes / sizes.sum()))
    return local, pi


def _select_size_stratified(psu_frame, sizes, n_take, seed, k, cols):
    """R2: stratify PSUs into `k` size bands, allocate the draw evenly across bands,
    systematic-PPS within each. Inclusion prob is the within-band PPS probability."""
    df = psu_frame.reset_index(drop=True)
    order = np.argsort(sizes)
    bands = np.array_split(order, k)
    per = [n_take // k] * k
    for i in range(n_take - sum(per)):  # spread the remainder onto the smaller bands
        per[i] += 1
    rng = np.random.default_rng(seed)
    chosen: list[int] = []
    pvals: list[float] = []
    for band_idx, take in zip(bands, per):
        band_idx = np.asarray(band_idx)
        if take <= 0 or band_idx.size == 0:
            continue
        bsizes = sizes[band_idx]
        if band_idx.size <= take:  # census this band
            chosen.extend(int(i) for i in band_idx)
            pvals.extend([1.0] * band_idx.size)
            continue
        local, pi = _systematic_pps(bsizes, take, rng)
        chosen.extend(int(band_idx[li]) for li in local)
        pvals.extend(float(pi[li]) for li in local)
    out = df.iloc[chosen].copy()
    out["P_psu"] = pvals
    if "stratum" not in out.columns:
        out["stratum"] = "Low"
    return out[cols].reset_index(drop=True)


@dataclass(frozen=True)
class PinConfig:
    n_primary: int = 8
    n_alternate: int = 8
    min_sep_m: float = 15.0
    seed: int = 20250927


def sample_pins(buildings: pd.DataFrame, selected_psus: pd.DataFrame, config: PinConfig | None = None) -> pd.DataFrame:
    """One row per sampled pin: cluster, lon, lat, area_m2, role, order_in_cluster, weight.

    `selected_psus` is the DataFrame from `select_psus` (carries n_buildings + P_psu).
    """
    config = config or PinConfig()
    out = []
    rng = np.random.default_rng(config.seed)
    target_n = config.n_primary + config.n_alternate
    meta = {r["cluster"]: (int(r["n_buildings"]), float(r["P_psu"])) for _, r in selected_psus.iterrows()}

    for cluster in selected_psus["cluster"].tolist():
        sub = buildings[buildings["cluster"] == cluster].reset_index(drop=True)
        if sub.empty:
            continue
        pts = sub[["x_m", "y_m"]].to_numpy()
        picked = _thin_to_separated(pts, target_n, config.min_sep_m, rng)
        n_buildings, p_psu = meta[cluster]
        m_eff = min(config.n_primary, len(picked))
        for rank, i in enumerate(picked, start=1):
            is_primary = rank <= config.n_primary
            if is_primary and n_buildings > 0 and p_psu > 0:
                p_build = m_eff / n_buildings
                pi = p_psu * p_build
                weight = 1.0 / pi if pi > 0 else np.nan
            else:
                weight = np.nan  # alternates have no inclusion weight
            out.append(
                {
                    "cluster": cluster,
                    "lon": float(sub.loc[i, "lon"]),
                    "lat": float(sub.loc[i, "lat"]),
                    "area_m2": float(sub.loc[i, "area_m2"]) if "area_m2" in sub.columns else None,
                    "role": "primary" if is_primary else "alternate",
                    "order_in_cluster": rank,
                    "weight": weight,
                }
            )
    return pd.DataFrame(out, columns=["cluster", "lon", "lat", "area_m2", "role", "order_in_cluster", "weight"])


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
            d = np.sqrt(((avail_pts[:, None, :] - sel_pts[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
            available = available[d >= min_sep_m]
    return selected
