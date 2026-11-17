"""Alias detection (case_model._fit_aliases): the vectorised fit equals the pairwise definition."""

import random
from collections import Counter

import pytest

from connect_labs.labs.synthetic.generator.fixtures import case_model as cm


def _fit_aliases_pairwise(series_list):
    """The definition: every pair of a visit's fields, compared one at a time."""
    seen: Counter = Counter()
    together: Counter = Counter()
    equal: Counter = Counter()
    for s in series_list:
        for v in s["visits"]:
            for kind in ("values", "dates", "cats"):
                fields = v.get(kind) or {}
                keys = sorted(fields)
                seen.update((kind, k) for k in keys)
                for i, a in enumerate(keys):
                    for b in keys[i + 1 :]:
                        together[(kind, a, b)] += 1
                        x, y = fields[a], fields[b]
                        same = abs(float(x) - float(y)) <= 1e-9 * max(1.0, abs(float(x))) if kind != "cats" else x == y
                        equal[(kind, a, b)] += same
    out: dict[str, str] = {}
    for (kind, a, b), n in together.items():
        if n < cm.K_MIN or equal[(kind, a, b)] < 0.99 * n:
            continue
        primary, alias = (a, b) if (seen[(kind, a)], b) >= (seen[(kind, b)], a) else (b, a)
        if n >= 0.95 * seen[(kind, alias)]:
            out.setdefault(alias, primary)
    for alias in list(out):
        root, hops = out[alias], 0
        while root in out and hops < len(out):
            root, hops = out[root], hops + 1
        out[alias] = root
    return {alias: root for alias, root in out.items() if alias != root}


def _series(seed: int) -> list[dict]:
    """Visits built to sit on every edge the rule has: exact and near copies, copies
    that miss 1% of the time, ties in how often two fields are recorded, chains, a copy
    with two candidate originals, small-integer fields equal by chance, sparse fields."""
    r = random.Random(seed)
    out = []
    for _ in range(r.randint(20, 60)):
        visits = []
        for _ in range(r.randint(1, 8)):
            values: dict = {}
            for j in range(r.randint(3, 9)):
                if r.random() < 0.85:
                    values[f"n{j}"] = float(r.choice([0, 1, 2])) if j % 2 else round(r.gauss(100, 20), 1)
            if "n0" in values:
                if r.random() < r.choice([1.0, 0.995, 0.97]):
                    values["copy_n0"] = values["n0"]
                if r.random() < 0.5:
                    values["near_n0"] = values["n0"] * (1 + r.choice([0, 1e-12, 1e-6]))
            if "copy_n0" in values and r.random() < 0.9:
                values["copy_copy_n0"] = values["copy_n0"]
            if "n1" in values and "n3" in values and values["n1"] == values["n3"]:
                values["copy_n1_or_n3"] = values["n1"]
            if r.random() < 0.05:
                values["rare"] = 1.0
            cats = {f"c{j}": r.choice(["yes", "no"] if j % 2 else ["yes"]) for j in range(4) if r.random() < 0.9}
            if "c0" in cats:
                cats["copy_c0"] = cats["c0"]
            dates = {"d0": r.randint(-9, 0)} if r.random() < 0.8 else {}
            if dates and r.random() < 0.9:
                dates["copy_d0"] = dates["d0"]
            visits.append({"values": values, "cats": cats, "dates": dates})
        out.append({"visits": visits})
    return out


@pytest.mark.parametrize("seed", range(60))
def test_vectorised_alias_fit_equals_the_pairwise_definition(seed):
    series = _series(seed)
    assert cm._fit_aliases(series) == _fit_aliases_pairwise(series)


def test_alias_fit_finds_a_copied_reading():
    visits = [{"values": {"w": float(1500 + i), "case.w": float(1500 + i)}} for i in range(20)]
    assert cm._fit_aliases([{"visits": visits}]) == {"w": "case.w"}
