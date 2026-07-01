"""Reusable binary-outcome field type for the synthetic generator.

A `binary` distribution draws 1 at a target rate (0 otherwise), with optional
per-period (week_index) rate overrides — so an outcome like vitamin-A confirmed
lands a controlled coverage rate that can vary round to round.
"""

from __future__ import annotations

import random

from connect_labs.labs.synthetic.generator.fixtures.fields import _draw
from connect_labs.labs.synthetic.generator.fixtures.manifest import BinaryDistribution


def test_binary_draws_at_base_rate():
    rng = random.Random(0)
    d = BinaryDistribution(distribution="binary", rate=0.7)
    n = 4000
    trues = sum(_draw(d, rng) for _ in range(n))
    assert 0.67 <= trues / n <= 0.73


def test_binary_rate_for_period_uses_per_period_override():
    d = BinaryDistribution(distribution="binary", rate=0.5, period_rates={6: 0.9, 1: 0.1})
    assert d.rate_for_period(6) == 0.9
    assert d.rate_for_period(1) == 0.1
    # Falls back to the base rate for an unspecified period.
    assert d.rate_for_period(3) == 0.5
