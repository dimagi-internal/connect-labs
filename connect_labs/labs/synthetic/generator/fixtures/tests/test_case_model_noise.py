"""A few real data-entry slips must not set the noise on every synthetic weighing."""

import random

from connect_labs.labs.synthetic.generator.fixtures import case_model as cm


def _pool(n_cases=200, typos=6, seed=3):
    rng = random.Random(seed)
    pool = []
    for c in range(n_cases):
        start = rng.uniform(1400, 2200)
        visits = []
        for i, day in enumerate((0, 7, 14, 21, 28)):
            w = start + 25 * day + rng.gauss(0, 30)
            visits.append({"day": day, "form": "visit", "values": {"form.w": round(w)}})
        pool.append({"owner": f"flw_{c % 10:03d}", "start_date": "2026-06-01", "visits": visits})
    # Real slips: a weight typed with an extra zero, or in kilograms.
    for c in range(typos):
        pool[c]["visits"][2]["values"]["form.w"] *= 10 if c % 2 else 0.001
    return pool


def test_residual_spread_ignores_a_few_typos():
    assert cm._robust_sd([1.0, -1.0, 2.0, -2.0, 0.0] * 20 + [50000.0, -40000.0]) < 3


def test_synthetic_weights_stay_plausible_when_the_source_has_typos():
    pool = _pool()
    out, _report = cm.synthesize_case_pool(pool, seed=11)
    weights = [v["values"]["form.w"] for case in out for v in case["visits"] if "form.w" in v.get("values", {})]
    assert weights
    implausible = sum(1 for w in weights if w < 800 or w > 5000) / len(weights)
    # The source itself is 6 slips in 1,000 weighings; the clone must stay of that order.
    assert implausible < 0.05, f"{implausible:.0%} of synthetic weights implausible"
