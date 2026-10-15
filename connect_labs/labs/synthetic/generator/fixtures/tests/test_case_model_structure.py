"""Structure a clone keeps from its source's cases, without copying any one of them:
which visits record a field, the first reading's tie to a per-case constant, and the
grid a reading is written on."""

import collections
import random

from connect_labs.labs.synthetic.generator.fixtures import case_model as cm

REG, VISIT = "Register", "Visit"


def _pool(seed=1, cases=300, copy_share=0.7):
    """Registration weight at registration; a visit weight on the first three follow-ups
    only, whatever the case's length; the first one copied from the registration weight
    in ``copy_share`` of cases, else within 200 g of it; later readings gram-precise."""
    rng = random.Random(seed)
    pool = []
    for c in range(cases):
        reg = 100 * round(rng.gauss(1900, 300) / 100)
        n = rng.choice([4, 5, 6])
        visits = [{"day": 0, "form": REG, "values": {"form.reg_w": float(reg)}}]
        first = reg if rng.random() < copy_share else reg + rng.choice([-200, -100, 100, 200])
        for i in range(1, n):
            day = 7 * i
            values = {}
            if i <= 3:
                values["form.w"] = float(round(first + 20 * (day - 7) + rng.gauss(0, 40))) if i > 1 else float(first)
            visits.append({"day": day, "form": VISIT, "values": values})
        pool.append({"owner": f"flw_{c % 6:03d}", "start_date": "2026-05-01", "visits": visits})
    return pool


def _weighed(case):
    return tuple("form.w" in v["values"] for v in case["visits"])


def test_a_case_records_a_field_on_the_visits_the_source_does():
    """The source weighs the first three follow-ups only; drawn visit by visit, a clone
    weighed some later visits and skipped some early ones."""
    real = _pool()
    out, _ = cm.synthesize_case_pool(real, seed=2)
    real_patterns = collections.Counter(_weighed(s) for s in real)
    out_patterns = collections.Counter(_weighed(s) for s in out)
    assert set(out_patterns) <= set(real_patterns)
    for pattern, c in real_patterns.items():
        assert abs(out_patterns[pattern] / len(out) - c / len(real)) < 0.08


def test_the_first_reading_is_entered_off_its_constant_at_the_sources_rate():
    real = _pool(copy_share=0.7)
    out, _ = cm.synthesize_case_pool(real, seed=3)

    def first_minus_reg(case):
        reg = case["visits"][0]["values"].get("form.reg_w")
        first = next((v["values"]["form.w"] for v in case["visits"] if "form.w" in v["values"]), None)
        return None if reg is None or first is None else first - reg

    diffs = [d for d in map(first_minus_reg, out) if d is not None]
    assert diffs
    assert 0.6 < sum(d == 0 for d in diffs) / len(diffs) < 0.8
    assert max(abs(d) for d in diffs) <= 300  # never a kilogram off, as an intercept of its own was


def test_a_reading_beyond_the_grids_ends_is_rounded_at_its_spacing():
    pm = cm._PathModel(kind="varying", whole=True, places=0, lo=0, hi=9000)
    pm.grid, pm.grid_share, pm.grid_step = (1500.0, 1600.0, 1700.0, 1800.0), {"*": 1.0}, 100.0
    rng = random.Random(0)
    assert cm._on_grid(1247.0, pm, "flw_001", rng) == 1200.0
    assert cm._on_grid(2361.0, pm, "flw_001", rng) == 2400.0
    assert cm._on_grid(1640.0, pm, "flw_001", rng) == 1600.0


def test_readings_are_rounded_to_each_unit_the_source_rounds_to():
    """A quarter of the readings whole hundreds, a further third whole tens, the rest as
    read: no one grid holds that, and a clone wrote 9% whole hundreds against 25%."""
    rng = random.Random(4)
    obs = []
    for _ in range(3000):
        x = rng.uniform(1200, 3500)
        r = rng.random()
        obs.append(round(x, -2) if r < 0.25 else round(x, -1) if r < 0.6 else round(x))
    levels = dict(cm._fit_heaping((), {"flw_001": obs}, 1.0)["*"])
    assert abs(levels.get(100.0, 0) - 0.25) < 0.04 and abs(levels.get(10.0, 0) - 0.35) < 0.05
    pm = cm._PathModel(kind="varying", whole=True, places=0, lo=0, hi=9000)
    pm.heaping = {"*": sorted(levels.items(), key=lambda kv: -kv[0])}
    out = [cm._on_grid(rng.uniform(1200, 3500), pm, "flw_002", rng) for _ in range(3000)]
    assert abs(sum(x % 100 == 0 for x in out) / len(out) - 0.25) < 0.04
    assert abs(sum(x % 10 == 0 for x in out) / len(out) - 0.60) < 0.05


def _outcome_pool(seed=5, cases=400):
    """Slow growers die; every form also carries a build number that rises now and then
    and says nothing about the baby. A case ends died (alive=no, status=closed) or
    discharged (status=discharged), never both."""
    rng = random.Random(seed)
    pool = []
    for c in range(cases):
        slope = rng.gauss(18, 6)
        died = rng.random() < (0.35 if slope < 14 else 0.03)
        discharged = not died and rng.random() < 0.3
        build = rng.choice([3, 4, 5])
        visits = []
        for i in range(5):
            day = 7 * i
            build += rng.random() < 0.15
            cats = {"form.alive": "yes", "form.status": "open"}
            if i == 4 and died:
                cats = {"form.alive": "no", "form.status": "closed"}
            elif i == 4 and discharged:
                cats = {"form.alive": "yes", "form.status": "discharged"}
            values = {"form.w": float(round(1600 + slope * day + rng.gauss(0, 20))), "form.@version": float(build)}
            visits.append({"day": day, "form": VISIT, "values": values, "cats": cats})
        pool.append({"owner": f"flw_{c % 8:03d}", "start_date": "2026-04-01", "visits": visits})
    return pool


def test_outcomes_follow_the_growth_they_depend_on_not_the_commonest_series():
    model = cm.fit_case_model(_outcome_pool())
    assert model.driver == "s:form.w"


def test_a_case_ends_on_one_of_the_sources_whole_combinations():
    real = _outcome_pool()
    out, _ = cm.synthesize_case_pool(real, seed=6)
    ends = collections.Counter(
        (s["visits"][-1].get("cats") or {}).get("form.alive", "-")
        + "/"
        + (s["visits"][-1].get("cats") or {}).get("form.status", "-")
        for s in out
    )
    assert set(ends) <= {"yes/open", "no/closed", "yes/discharged"}, ends
    real_died = sum(1 for s in real if s["visits"][-1]["cats"]["form.alive"] == "no") / len(real)
    assert abs(ends["no/closed"] / len(out) - real_died) < 0.04
