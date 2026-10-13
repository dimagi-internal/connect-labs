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


def test_a_rare_outcome_survives_modelling():
    """child_alive flips to "no" for ~6% of babies -- under the 10% "time-varying" share --
    and a modelled cohort must still record about that many deaths (it recorded ~10%)."""
    rng = random.Random(5)
    pool = []
    for c in range(1000):
        n = rng.choice([3, 4, 5, 6, 7, 8])
        dies = rng.random() < 0.06
        visits = [
            {
                "day": i * 7,
                "form": "Register" if i == 0 else "Visit",
                "values": {"form.w": 1500 + 25 * i * 7 + rng.gauss(0, 30)},
                "cats": {"form.child_alive": "no" if (dies and i == n - 1) else "yes"},
            }
            for i in range(n)
        ]
        pool.append({"owner": f"flw_{c % 10:03d}", "start_date": "2026-06-01", "visits": visits})
    real = sum(1 for c in pool if c["visits"][-1]["cats"]["form.child_alive"] == "no")

    out, _report = cm.synthesize_case_pool(pool, seed=1)
    died = [c for c in out if any((v.get("cats") or {}).get("form.child_alive") == "no" for v in c["visits"])]

    assert 0.6 * real <= len(died) <= 1.4 * real, f"{len(died)} synthetic deaths for {real} real"
    # A death is the case's LAST visit, never stamped across earlier ones.
    for c in died:
        alive = [(v.get("cats") or {}).get("form.child_alive") for v in c["visits"]]
        assert alive[-1] == "no" and "no" not in alive[:-1]
