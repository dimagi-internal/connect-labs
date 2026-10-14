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


# ---- fidelity to the source's shape (2026-10-14 batch) -------------------------------


def _kmc_like_pool(seed=7, n_cases=600):
    """A small programme with the shapes the real KMC data has: a protocol schedule that
    ends near day 28, smooth growth, workers who round to 100 g (half of them always),
    and workers who copy the birth weight into the enrolment weight."""
    rng = random.Random(seed)
    pool = []
    for c in range(n_cases):
        owner = f"flw_{c % 6:03d}"
        rounder = c % 6 < 3  # flw_000..flw_002 always round
        copier = c % 6 in (0, 3)  # flw_000 and flw_003 copy birth weight half the time
        days = [0, rng.choice([2, 3, 4]), rng.choice([7, 8]), rng.choice([13, 14, 15]), rng.choice([27, 28, 29])]
        bw = rng.uniform(1300, 2300)
        enrol = bw if (copier and rng.random() < 0.5) else bw * rng.uniform(0.88, 0.99)
        rate = rng.uniform(12, 22)  # g/kg/day
        w = enrol
        visits = []
        for i, d in enumerate(days):
            if i:
                w = w + rate * (w / 1000) * (d - days[i - 1]) + rng.gauss(0, 15)
            reading = round(w / 100) * 100 if rounder else round(w)
            values = {"form.w": float(reading)}
            if i == 0:
                values["form.bw"] = float(round(bw / 100) * 100 if rounder else round(bw))
                values["form.enrol"] = float(values["form.bw"] if enrol == bw else round(enrol))
            visits.append({"day": d, "form": "Register" if i == 0 else "Visit", "values": values})
        pool.append({"owner": owner, "start_date": f"2026-0{1 + c % 6}-{1 + c % 27:02d}", "visits": visits})
    return pool


def _shares(pool):
    spans = [c["visits"][-1]["day"] - c["visits"][0]["day"] for c in pool]
    pairs = [
        (v["values"]["form.bw"], v["values"]["form.enrol"])
        for c in pool
        for v in c["visits"]
        if "form.bw" in v["values"] and "form.enrol" in v["values"]
    ]
    impossible = 0
    for c in pool:
        pts = [(v["day"], v["values"]["form.w"]) for v in c["visits"] if "form.w" in v["values"]]
        for (d0, w0), (d1, w1) in zip(pts, pts[1:]):
            if d1 > d0 and not (-20 <= (w1 - w0) / ((w0 + w1) / 2000) / (d1 - d0) <= 45):
                impossible += 1
                break
    return {
        "span_28": sum(1 for s in spans if s >= 27) / len(spans),
        "copied": sum(1 for a, b in pairs if abs(a - b) < 1) / max(len(pairs), 1),
        "impossible": impossible / len(pool),
    }


def test_a_modelled_programme_keeps_the_sources_shape():
    """Schedule length, field copies and step plausibility all come through. Before this
    batch: span_28 fell from 100% to 78% and copying from 17% to 2%."""
    real_pool = _kmc_like_pool()
    real = _shares(real_pool)
    out, _report = cm.synthesize_case_pool(real_pool, seed=2)
    syn = _shares(out)
    assert syn["span_28"] >= real["span_28"] - 0.1, (real, syn)
    assert abs(syn["copied"] - real["copied"]) <= 0.08, (real, syn)
    assert syn["impossible"] <= real["impossible"] + 0.08, (real, syn)
