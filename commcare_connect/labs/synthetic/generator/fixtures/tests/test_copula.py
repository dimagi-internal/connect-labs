# test_copula.py
import numpy as np

from commcare_connect.labs.synthetic.generator.fixtures.copula import build_copula_sampler, nearest_psd
from commcare_connect.labs.synthetic.generator.fixtures.manifest import (
    CategoricalDistribution,
    CorrelationSpec,
    NormalDistribution,
)


def test_nearest_psd_fixes_non_psd_matrix():
    bad = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])
    fixed = nearest_psd(bad)
    eigvals = np.linalg.eigvalsh(fixed)
    assert (eigvals >= -1e-9).all()
    assert np.allclose(np.diag(fixed), 1.0, atol=1e-9)


def test_copula_reproduces_correlation_and_margins():
    corr = CorrelationSpec(fields=["a", "b"], matrix=[[1.0, 0.8], [0.8, 1.0]])
    dists = {
        "a": NormalDistribution(mean=10.0, stddev=2.0),
        "b": NormalDistribution(mean=-5.0, stddev=1.0),
    }
    sampler = build_copula_sampler(corr, dists, seed=42)
    rows = [sampler.draw() for _ in range(4000)]
    a = np.array([r["a"] for r in rows])
    b = np.array([r["b"] for r in rows])
    # Marginals preserved within sampling tolerance
    assert abs(a.mean() - 10.0) < 0.2
    assert abs(a.std() - 2.0) < 0.2
    # Positive correlation recovered (Pearson ~ Spearman for gaussian margins)
    assert np.corrcoef(a, b)[0, 1] > 0.7


def test_copula_categorical_margin_frequencies():
    corr = CorrelationSpec(fields=["g"], matrix=[[1.0]])
    dists = {"g": CategoricalDistribution(distribution="categorical", values={"m": 0.7, "f": 0.3})}
    sampler = build_copula_sampler(corr, dists, seed=1)
    rows = [sampler.draw()["g"] for _ in range(5000)]
    frac_m = rows.count("m") / len(rows)
    assert 0.66 < frac_m < 0.74


def test_build_returns_none_without_correlation():
    assert build_copula_sampler(None, {}, seed=1) is None


def test_numeric_margin_clamps_to_bounds():
    """The copula margin clamps to [lo, hi] like the independent draw path."""
    from commcare_connect.labs.synthetic.generator.fixtures.copula import NumericMargin

    m = NumericMargin(mean=13.5, stddev=12.9, lo=0.0, hi=60.0)
    # u near 0 -> very negative z -> would be negative without clamping.
    assert m.value_from_uniform(1e-6) == 0.0
    assert m.value_from_uniform(1 - 1e-6) == 60.0
    assert 0.0 <= m.value_from_uniform(0.5) <= 60.0
