"""Gaussian copula: draw correlated field values that preserve each field's
marginal AND the Spearman rank-correlation captured by the profiler.

z ~ N(0, Sigma) via Cholesky of a PSD-projected Sigma; u = Phi(z); each component
is mapped back through its field's marginal inverse-CDF (numeric -> Normal ppf;
categorical -> cumulative-frequency threshold)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from .manifest import CategoricalDistribution, CorrelationSpec, NormalDistribution


def nearest_psd(matrix: np.ndarray) -> np.ndarray:
    """Project a symmetric matrix to the nearest PSD correlation matrix:
    clip negative eigenvalues to ~0, reconstruct, renormalize the diagonal to 1."""
    sym = (matrix + matrix.T) / 2.0
    eigvals, eigvecs = np.linalg.eigh(sym)
    eigvals = np.clip(eigvals, 1e-8, None)
    psd = (eigvecs * eigvals) @ eigvecs.T
    d = np.sqrt(np.diag(psd))
    d[d == 0] = 1.0
    psd = psd / np.outer(d, d)
    np.fill_diagonal(psd, 1.0)
    return psd


@dataclass
class NumericMargin:
    mean: float
    stddev: float

    def value_from_uniform(self, u: float) -> float:
        # u = Phi(z); Normal ppf gives back the standard normal, then scale/shift.
        z = norm.ppf(min(max(u, 1e-9), 1 - 1e-9))
        return self.mean + self.stddev * z


@dataclass
class CategoricalMargin:
    values: dict[str, float]

    def __post_init__(self):
        total = sum(self.values.values())
        items = sorted(self.values.items(), key=lambda kv: kv[0])
        cum = 0.0
        self._thresholds: list[tuple[float, str]] = []
        for name, rate in items:
            cum += rate / total
            self._thresholds.append((cum, name))

    def value_from_uniform(self, u: float) -> str:
        for threshold, name in self._thresholds:
            if u <= threshold:
                return name
        return self._thresholds[-1][1]


def _margin_for(dist):
    if isinstance(dist, NormalDistribution):
        return NumericMargin(dist.mean, dist.stddev)
    if isinstance(dist, CategoricalDistribution):
        return CategoricalMargin(dict(dist.values))
    return None  # uniform/binary excluded from the copula; drawn independently


class CopulaSampler:
    def __init__(self, fields: list[str], chol: np.ndarray, margins: list, seed: int):
        self.fields = fields
        self._chol = chol
        self._margins = margins
        self._rng = np.random.default_rng(seed)

    def draw(self) -> dict:
        z = self._chol @ self._rng.standard_normal(len(self.fields))
        u = norm.cdf(z)
        return {f: m.value_from_uniform(float(ui)) for f, m, ui in zip(self.fields, self._margins, u)}


def build_copula_sampler(
    correlation: CorrelationSpec | None,
    distributions: dict,
    *,
    seed: int,
) -> CopulaSampler | None:
    if correlation is None or not correlation.fields:
        return None
    fields, margins = [], []
    keep_idx = []
    for i, path in enumerate(correlation.fields):
        margin = _margin_for(distributions.get(path))
        if margin is None:
            continue
        fields.append(path)
        margins.append(margin)
        keep_idx.append(i)
    if not fields:
        return None
    mat = np.array(correlation.matrix, dtype=float)[np.ix_(keep_idx, keep_idx)]
    chol = np.linalg.cholesky(nearest_psd(mat))
    return CopulaSampler(fields, chol, margins, seed)
