"""The vectorised privacy gate decides exactly what the per-pair definition does."""

import math
import random

from connect_labs.labs.synthetic.generator.fixtures import case_model as cm


def _reference_nn(real):
    """The gate's nearest-neighbour distances, one pair at a time with ``_distance``."""
    grouped = {}
    for s in real:
        if len(s["visits"]) >= cm._GATE_MIN_VISITS:
            grouped.setdefault(tuple(int(v["day"]) for v in s["visits"]), []).append(s)
    out = {}
    for shape, cases in grouped.items():
        for i, r in enumerate(cases):
            others = [d for j, o in enumerate(cases) if j != i and (d := cm._distance(r, o)) is not None]
            out[id(r)] = min(others) if others else math.inf
    return grouped, out


def _reference_too_close(grouped, nn, case):
    if len(case["visits"]) < cm._GATE_MIN_VISITS:
        return False
    for real in grouped.get(tuple(v["day"] for v in case["visits"]), []):
        d = cm._distance(case, real)
        if d is not None and d <= cm._GATE_REL_TOL and d < cm._GATE_NN_SHARE * nn[id(real)]:
            return True
    return False


def _case(rng, days, paths, base, jitter, drop=0.15):
    visits = []
    for vi, day in enumerate(days):
        values = {}
        for p in paths:
            if rng.random() < drop:
                continue
            values[p] = round(base[p] * (1 + rng.uniform(-jitter, jitter)) + vi * 10, 3)
        visits.append({"day": day, "values": values})
    return {"owner": "flw_001", "visits": visits}


def test_the_vectorised_gate_matches_the_per_pair_definition():
    rng = random.Random(7)
    paths = ["w", "h", "muac"]
    shapes = [(0, 7, 14), (0, 7, 14, 21), (0, 3, 10), (0, 7)]
    real = []
    for _ in range(150):
        base = {"w": rng.uniform(1500, 2500), "h": rng.uniform(40, 50), "muac": rng.uniform(8, 11)}
        real.append(_case(rng, rng.choice(shapes), paths, base, jitter=0.002))
    # A few cases with an all-zero path, so the floored denominator is exercised.
    real.append({"owner": "x", "visits": [{"day": d, "values": {"w": 0.0}} for d in (0, 7, 14)]})
    real.append({"owner": "x", "visits": [{"day": d, "values": {"w": 0.0}} for d in (0, 7, 14)]})

    gate = cm._Gate(real)
    grouped, nn = _reference_nn(real)
    # Nearest neighbours agree for every real case.
    for shape, cases in grouped.items():
        _, _, _, got = gate._shapes[shape]
        for i, r in enumerate(cases):
            want = nn[id(r)]
            assert (math.isinf(want) and math.isinf(got[i])) or math.isclose(got[i], want, rel_tol=1e-9, abs_tol=1e-12)

    # And the verdict agrees for sampled cases: some copies of real ones, some far away.
    probes = []
    for r in real[:60]:
        probes.append({"owner": "flw_001", "visits": [dict(v, values=dict(v["values"])) for v in r["visits"]]})
    for _ in range(60):
        base = {"w": rng.uniform(1500, 2500), "h": rng.uniform(40, 50), "muac": rng.uniform(8, 11)}
        probes.append(_case(rng, rng.choice(shapes), paths, base, jitter=0.05))
    verdicts = [gate.too_close(p) for p in probes]
    assert verdicts == [_reference_too_close(grouped, nn, p) for p in probes]
    assert any(verdicts) and not all(verdicts)


def test_a_case_of_an_unseen_shape_or_too_few_visits_is_never_too_close():
    real = [{"owner": "a", "visits": [{"day": d, "values": {"w": 2000.0}} for d in (0, 7, 14)]}] * 3
    gate = cm._Gate(real)
    assert not gate.too_close({"visits": [{"day": d, "values": {"w": 2000.0}} for d in (0, 5, 14)]})
    assert not gate.too_close({"visits": [{"day": d, "values": {"w": 2000.0}} for d in (0, 7)]})
