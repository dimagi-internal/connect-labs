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


def select_psus(
    psu_frame: pd.DataFrame, n_take: int, seed: int = 20250926, size_balance_bands: int = 0
) -> pd.DataFrame:
    """Return selected PSUs as a DataFrame [cluster, n_buildings, stratum, P_psu].

    `P_psu` is the PSU's inclusion probability (proportional to building count).

    `size_balance_bands > 1` switches on **size-stratified systematic PPS** (the DHS/MICS
    standard): split the PSU frame into that many building-count bands, allocate the draw
    evenly across bands, and run systematic PPS within each band. This draws a *matched
    size-mix* so two arms sampled the same way are comparable on cluster size by
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

    if size_balance_bands and size_balance_bands > 1:
        return _select_size_stratified(psu_frame, sizes, n_take, seed, size_balance_bands, cols)

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


def _hamilton_allocate(target: int, weights: np.ndarray) -> np.ndarray:
    """Largest-remainder (Hamilton) apportionment of `target` units across bins
    in proportion to `weights`. Returns an int array summing exactly to `target`."""
    weights = np.asarray(weights, dtype=float)
    total = weights.sum()
    if target <= 0 or total <= 0:
        return np.zeros(len(weights), dtype=int)
    exact = target * weights / total
    floor = np.floor(exact).astype(int)
    remainder = target - int(floor.sum())
    if remainder > 0:
        # Hand the leftover seats to the bins with the largest fractional parts.
        order = np.argsort(-(exact - floor))
        for i in order[:remainder]:
            floor[i] += 1
    return floor


def select_psus_matched(
    arm_frames: dict[str, pd.DataFrame],
    n_take: int,
    *,
    seed: int = 20250926,
    size_balance_bands: int = 0,
    density_bands: int = 12,
) -> dict:
    """Density-stratified MATCHED PPS across two (or more) arms drawn JOINTLY.

    Each arm's candidate ``psu_frame`` must carry a per-cluster ``density`` column
    (mean k-NN local building density of the cluster's buildings). The arms are
    selected together so their SELECTED PSUs share a density mix by construction —
    closing the drift that independent per-arm PPS leaves on settlement density.

    Method (the "matched-PPS" design):

    1. **Shared bands.** Compute density band edges from the INTERVENTION arm's
       candidate densities (anchor arm, like ``comparability.density_bin_edges``) and
       assign every arm's candidates to those shared bands.
    2. **Common support.** Only bands that have candidates in *every* arm get an
       allocation; bands present in one arm only are excluded and recorded
       (``excluded_bands``). The contrast is therefore defined on the
       *common-support population* — the density range both arms can actually cover.
    3. **Allocation rule (documented choice).** The target PSU count is split across
       the common bands *proportional to the INTERVENTION arm's candidate count per
       band* (largest-remainder apportionment), then the SAME per-band target is
       applied identically to every arm. Proportional-to-intervention makes the
       matched sample track the intervention's own density distribution (the
       estimand's reference population), rather than an even split that would
       over-represent thin bands.
    4. **Nesting (documented choice).** Within each density band we draw systematic
       PPS per arm. When ``size_balance_bands > 1`` the existing size-stratified
       draw is *nested inside* each density band (density×size joint balance);
       otherwise it's a plain systematic-PPS draw within the band.
    5. **Unbiasedness preserved.** ``P_psu`` is the within-stratum (density[×size])
       systematic-PPS inclusion probability exactly as before; the design weight
       ``1/Pi`` in ``sample_pins`` stays the Horvitz–Thompson weight for the
       common-support population. No weight math changes.

    Returns ``{"selected": {arm: DataFrame[cluster, n_buildings, stratum, P_psu]},
    "edges": [...], "common_bands": [...], "excluded_bands": [...],
    "restricted": bool}`` where ``restricted`` is True when common support is so thin
    that no band is shared (genuine incomparability — the caller should flag it rather
    than force a bad match).
    """
    arms = list(arm_frames.keys())
    # Anchor on the intervention arm (or the first arm) for the shared band edges.
    anchor = "intervention" if "intervention" in arm_frames else arms[0]
    anchor_frame = arm_frames[anchor]

    def _dens(frame: pd.DataFrame) -> np.ndarray:
        if frame is None or frame.empty or "density" not in frame.columns:
            return np.empty(0)
        return frame["density"].to_numpy(dtype=float)

    from commcare_connect.microplans.core.comparability import density_bin_edges

    edges = density_bin_edges([float(x) for x in _dens(anchor_frame)], bins=density_bands)
    cols = ["cluster", "n_buildings", "stratum", "P_psu"]

    # Single arm, or degenerate frames (too few candidates to band) → there's nothing
    # to coordinate; defer to the unchanged independent size-stratified PPS per arm.
    # Record restricted=False (this isn't an incomparability, just no joint draw).
    if (
        len(arms) < 2
        or edges is None
        or n_take <= 0
        or any(arm_frames[a] is None or arm_frames[a].empty for a in arms)
    ):
        selected = {
            a: select_psus(arm_frames[a], n_take=n_take, seed=seed + i, size_balance_bands=size_balance_bands)
            for i, a in enumerate(arms)
        }
        return {
            "selected": selected,
            "edges": edges,
            "common_bands": [],
            "excluded_bands": [],
            "restricted": False,
        }

    e = np.asarray(edges, dtype=float)
    nb = len(e) - 1

    def _band_index(frame: pd.DataFrame) -> np.ndarray:
        """Assign each candidate to an intervention-anchored density band, OR -1 when
        its density falls OUTSIDE the intervention's support range [e0, e_last]. We do
        NOT clip out-of-range candidates into the boundary bands — an all-denser
        control must read as out-of-support, not as sharing the intervention's top
        band. Out-of-support (-1) candidates are simply never selectable here."""
        d = _dens(frame)
        idx = np.clip(np.digitize(d, e[1:-1]), 0, nb - 1)
        out_of_range = (d < e[0]) | (d > e[-1])
        idx = idx.astype(int)
        idx[out_of_range] = -1
        return idx

    band_idx = {a: _band_index(arm_frames[a]) for a in arms}
    counts = {a: np.bincount(band_idx[a][band_idx[a] >= 0], minlength=nb) for a in arms}

    # Common support: a band counts only if EVERY arm has ≥1 candidate there.
    common = [b for b in range(nb) if all(counts[a][b] > 0 for a in arms)]
    excluded = [b for b in range(nb) if b not in common]

    if not common:
        # Genuine incomparability — the arms don't share density support. Don't force
        # a bad match: return empty selections + the restricted flag for the caller.
        return {
            "selected": {a: pd.DataFrame(columns=cols) for a in arms},
            "edges": edges,
            "common_bands": [],
            "excluded_bands": excluded,
            "restricted": True,
        }

    # Allocate the target PSU count across common bands ∝ the anchor (intervention)
    # arm's per-band candidate count (largest-remainder). One shared per-band target,
    # applied identically to every arm.
    anchor_counts = np.array([counts[anchor][b] for b in common], dtype=float)
    per_band = _hamilton_allocate(n_take, anchor_counts)

    selected: dict[str, pd.DataFrame] = {}
    for i, a in enumerate(arms):
        df = arm_frames[a].reset_index(drop=True)
        rng = np.random.default_rng(seed + i)
        chosen: list[int] = []
        pvals: list[float] = []
        for slot, b in enumerate(common):
            take = int(per_band[slot])
            if take <= 0:
                continue
            in_band = np.flatnonzero(band_idx[a] == b)
            if in_band.size == 0:
                continue
            bsizes = df["n_buildings"].to_numpy(dtype=float)[in_band]
            if in_band.size <= take:  # census this (density) band for this arm
                chosen.extend(int(j) for j in in_band)
                pvals.extend([1.0] * in_band.size)
                continue
            if size_balance_bands and size_balance_bands > 1:
                # Nest size-stratified PPS inside the density band: split the band by
                # size rank, even-allocate the band's take across size sub-bands,
                # systematic-PPS within each. Inclusion prob = within-(density×size) PPS.
                k = min(size_balance_bands, in_band.size)
                order = np.argsort(bsizes)
                size_sub = np.array_split(order, k)
                sub_take = _hamilton_allocate(take, np.array([s.size for s in size_sub], dtype=float))
                for sub, st in zip(size_sub, sub_take):
                    st = int(st)
                    if st <= 0 or sub.size == 0:
                        continue
                    sub_local = in_band[sub]
                    ssizes = df["n_buildings"].to_numpy(dtype=float)[sub_local]
                    if sub.size <= st:
                        chosen.extend(int(j) for j in sub_local)
                        pvals.extend([1.0] * sub.size)
                        continue
                    local, pi = _systematic_pps(ssizes, st, rng)
                    chosen.extend(int(sub_local[li]) for li in local)
                    pvals.extend(float(pi[li]) for li in local)
            else:
                local, pi = _systematic_pps(bsizes, take, rng)
                chosen.extend(int(in_band[li]) for li in local)
                pvals.extend(float(pi[li]) for li in local)
        out = df.iloc[chosen].copy()
        out["P_psu"] = pvals
        if "stratum" not in out.columns:
            out["stratum"] = "Low"
        selected[a] = out[cols].reset_index(drop=True)

    common_band_meta = [
        {"band": int(b), "lo": float(e[b]), "hi": float(e[b + 1]), "take": int(per_band[slot])}
        for slot, b in enumerate(common)
    ]
    excluded_band_meta = [
        {
            "band": int(b),
            "lo": float(e[b]),
            "hi": float(e[b + 1]),
            "counts": {a: int(counts[a][b]) for a in arms},
        }
        for b in excluded
        if any(counts[a][b] > 0 for a in arms)
    ]
    return {
        "selected": selected,
        "edges": edges,
        "common_bands": common_band_meta,
        "excluded_bands": excluded_band_meta,
        "restricted": False,
    }


@dataclass(frozen=True)
class PinConfig:
    n_primary: int = 8
    n_alternate: int = 8
    min_sep_m: float = 15.0
    seed: int = 20250927


def sample_pins(buildings: pd.DataFrame, selected_psus: pd.DataFrame, config: PinConfig | None = None) -> pd.DataFrame:
    """One row per sampled pin: cluster, lon, lat, area_m2, sample_type, order_in_cluster, weight.

    ``sample_type`` is ``"primary"`` (a unit to survey) or ``"alternate"`` (a ranked
    backup). ``selected_psus`` is the DataFrame from `select_psus` (carries
    n_buildings + P_psu).
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
                    "sample_type": "primary" if is_primary else "alternate",
                    "order_in_cluster": rank,
                    "weight": weight,
                }
            )
    return pd.DataFrame(out, columns=["cluster", "lon", "lat", "area_m2", "sample_type", "order_in_cluster", "weight"])


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
