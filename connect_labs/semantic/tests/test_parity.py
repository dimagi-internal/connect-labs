"""EXECUTE the compiled SQL and prove it equals an independent implementation.

The claim the semantic layer rests on: the numbers a report shows are the numbers
its definitions say. So the same fixture babies go through two paths -- the
compiled SQL against real Postgres, and a hand-written Python port of the rules
below -- and every one of the 30 KMC indicators must agree.

The reference is written from the RULES, not from the registry's SQL: Neal Lesh's
KMC metrics workbook as revised 2026-10-03 (Case properties + Case indicators),
which superseded the v3 compute spec #2004 had adopted, keeping v3's weight-series
rules (velocity window, thin / inconsistent / impossible, birthweight bands).
Deriving it from the YAML would prove only that the YAML agrees with itself. It
used to port the browser dashboard's JavaScript; that engine is gone, and so is
the workbook-vs-spec split that made two sets of rules necessary.

Every fixture baby exists to put one rule on a boundary, so that moving the rule
moves a number: a baby with exactly one follow-up and one with none (the started
threshold), one first seen 20 days before the report (the 28-day eligibility
gate) and one first seen 35 days before (the retired 42-day growth gate), early
deaths whose weighings are computable and a death ON day 28 (early_exit), a
referral on day 28 and one on day 29 (the referral window), one whose only third
weigh-day is the enrolment reading (thin), one whose weight step is legal per kg
of the pair mean and impossible per kg of the earlier reading.

Skipped when no Postgres is reachable; the structural tests in test_compiler.py
still run everywhere.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from connect_labs.semantic.compiler import compile_indicator_sql
from connect_labs.semantic.tests.pg import connect_or_skip

psycopg2 = pytest.importorskip("psycopg2")

REGISTRY = Path(__file__).resolve().parents[1] / "registry" / "kmc"

# The rules, restated for the reference (workbook 2026-10-03; the weight-series
# rules are v3 spec sections 2, 2b and 3, which the workbook keeps).
AS_OF = 200  # report date, days after 2026-01-01
AS_OF_SQL = f"(DATE '2026-01-01' + {AS_OF})"
STARTED_MIN = 1  # follow-up visits, registration excluded (v3 said 2)
ELIGIBLE_DAYS = 28  # THE one maturity gate, days from the FIRST VISIT (v3 waited 42 for growth)
REFERRAL_WINDOW = 28  # first referral within this many days of registration, inclusive
DIP_RANGE = (0.85, 0.99)  # expected dip: enrolment weight 1-15% below birthweight, inclusive
WMIN, WMAX = 250, 8000
WINDOW = 21  # days from the first MEASURED weighing
VELOCITY_MIN_SPAN = 5
THIN_MIN_DAYS = 3
INCONSISTENT_RATIO = 0.6
IMPOSSIBLE = (-20, 45)  # g/kg/day, per kg of the MEAN of the pair
IMPOSSIBLE_GAP = (1, 90)
GA_RANGE = (20, 45)
BANDS = [  # (upper bound exclusive, name, plausible lo, plausible hi)
    (1000, "<1000", 9, 30),
    (1500, "1000-1499", 6, 28),
    (2000, "1500-1999", 6, 24),
    (2500, "2000-2499", 5, 20),
    (math.inf, "2500+", 0, 18),
]


@pytest.fixture(scope="module")
def conn():
    c = connect_or_skip("the parity test")
    yield c
    c.close()


# ── The fixture: one baby per rule boundary ─────────────────────────────────
# (baby, day_offset, weight_g, alive, danger, referred, form_name,
#  days_discharge_to_reg, birth_weight_g, enrollment_weight_g)
# The registration row carries the registration fields; follow-ups leave them None.
R, F = "Registration", "Follow-up"
VISITS = [
    # h1 -- healthy growth: 11.3 g/kg/d over days 0-21, inside 6-24 for its band.
    # Enrolment weight == birthweight, so it is a birth copy and not a seed reading.
    ("h1", 0, 1500, "yes", "no", "no", R, 2.0, 1500.0, 1500.0),
    ("h1", 7, 1600, "yes", "no", "no", F, None, None, None),
    ("h1", 14, 1750, "yes", "no", "no", F, None, None, None),
    ("h1", 21, 1900, "yes", "no", "no", F, None, None, None),
    ("h1", 35, 2100, "yes", "no", "no", F, None, None, None),
    # s1 -- slow growth (2.4 g/kg/d against a floor of 6); a danger sign, referred.
    ("s1", 0, 1200, "yes", "no", "no", R, 1.0, 1200.0, 1250.0),
    ("s1", 10, 1230, "yes", "yes", "yes", F, None, None, None),
    ("s1", 20, 1260, "yes", "no", "no", F, None, None, None),
    ("s1", 40, 1400, "yes", "no", "no", F, None, None, None),
    # f1 -- fast growth (19.0 g/kg/d against a ceiling of 18); a danger sign, NOT referred.
    ("f1", 0, 2600, "yes", "no", "no", R, None, 2600.0, 2650.0),
    ("f1", 7, 3000, "yes", "yes", "no", F, None, None, None),
    ("f1", 14, 3400, "yes", "no", "no", F, None, None, None),
    ("f1", 30, 3500, "yes", "no", "no", F, None, None, None),
    # i1 -- thin: two measured weigh-days and the enrolment reading is a re-entry
    # (== birthweight), so there is no third day. Computable, not sufficient.
    ("i1", 0, 1500, "yes", "no", "no", R, None, 1500.0, 1500.0),
    ("i1", 8, 1600, "yes", "no", "no", F, None, None, None),
    ("i1", 50, None, "yes", "no", "no", F, None, None, None),
    # k1 -- the SEED READING decides thin. No weight at registration; the enrolment
    # weight (1400, not a copy of 1700) is a third measured day, so the baby is
    # sufficient and healthy. Ignore the seed and it reads thin -> incomplete.
    ("k1", 0, None, "yes", "no", "no", R, None, 1700.0, 1400.0),
    ("k1", 5, 1450, "yes", "no", "no", F, None, None, None),
    ("k1", 15, 1550, "yes", "no", "no", F, None, None, None),
    # x1 -- inconsistent: first weighing 1300 < 0.6 x 2400.
    ("x1", 0, 1300, "yes", "no", "no", R, None, 2400.0, 2300.0),
    ("x1", 7, 1400, "yes", "no", "no", F, None, None, None),
    ("x1", 14, 1500, "yes", "no", "no", F, None, None, None),
    # p1 -- a step ON the pair-mean boundary: +450 g in 6 days is 43.5 g/kg/d per kg
    # of the pair mean (legal) and 50 per kg of the earlier reading (impossible).
    ("p1", 0, 1500, "yes", "no", "no", R, None, 1500.0, 1520.0),
    ("p1", 6, 1950, "yes", "no", "no", F, None, None, None),
    ("p1", 13, 2050, "yes", "no", "no", F, None, None, None),
    ("p1", 20, 2150, "yes", "no", "no", F, None, None, None),
    # p2 -- a plainly impossible step (+800 g in 5 days, ~73 g/kg/d).
    ("p2", 0, 1800, "yes", "no", "no", R, None, 1800.0, 1800.0),
    ("p2", 5, 2600, "yes", "no", "no", F, None, None, None),
    ("p2", 12, 2700, "yes", "no", "no", F, None, None, None),
    # n1 -- no computable velocity: the only in-window pair is 3 days apart.
    # Discarded before the growth question; counted by pct_growth_computable.
    ("n1", 0, 1500, "yes", "no", "no", R, None, 1500.0, 1600.0),
    ("n1", 3, 1510, "yes", "no", "no", F, None, None, None),
    ("n1", 30, 1700, "yes", "no", "no", F, None, None, None),
    # m1 -- first seen 35 days before the report: eligible under the one 28-day
    # gate, so in the growth funnel; the retired 42-day growth gate would drop it.
    ("m1", 165, 1500, "yes", "no", "no", R, 0.0, 1500.0, 1600.0),
    ("m1", 172, 1600, "yes", "no", "no", F, None, None, None),
    ("m1", 180, 1700, "yes", "no", "no", F, None, None, None),
    ("m1", 190, 1800, "yes", "no", "no", F, None, None, None),
    # d1 -- died after ONE follow-up: started under the workbook's 1+ rule (v3's 2+
    # left it out of mortality); u1, registration only, is the other side of that
    # boundary. Died on day 9, an early exit: its two weighings are computable, so
    # it leaves the growth funnel only because of early_exit.
    ("d1", 0, 1200, "yes", "no", "no", R, None, 1200.0, 1210.0),
    ("d1", 9, 1150, "no", "no", "no", F, None, None, None),
    # d2 -- died on day 20 after two follow-ups: in mortality's numerator, and an
    # early exit with sufficient (slow) weight data. Danger sign, referred on day 10.
    ("d2", 0, 1300, "yes", "no", "no", R, None, 1300.0, 1310.0),
    ("d2", 10, 1350, "yes", "yes", "yes", F, None, None, None),
    ("d2", 20, 1300, "no", "no", "no", F, None, None, None),
    # l1 -- lost to follow-up: alive, last seen 12 days after the first visit.
    ("l1", 0, 1400, "yes", "no", "no", R, None, 1400.0, 1450.0),
    ("l1", 5, 1420, "yes", "no", "no", F, None, None, None),
    ("l1", 12, 1450, "yes", "no", "no", F, None, None, None),
    # g1 -- last visit at day 29: outcome known only because 29 >= 28. Carries an
    # implausible gestational age and an out-of-range reading (a raw reading all the
    # same, for the rounding rate; not a weigh-day).
    ("g1", 0, 1550, "yes", "no", "no", R, 4.0, 1550.0, 1560.0),
    ("g1", 10, 9000, "yes", "no", "no", F, None, None, None),
    ("g1", 29, 1800, "yes", "no", "no", F, None, None, None),
    # u1 -- registration only, never started.
    ("u1", 0, None, "yes", "no", "no", R, None, None, None),
]

# Babies the workbook's rules (2026-10-03) need on a boundary, appended for this
# test only: parity_fixture.py builds the engine-parity golden from VISITS, and that
# frozen comparison must not move when the definitions do.
WORKBOOK_VISITS = [
    # y1 -- started, but first seen 20 days before the report: NOT eligible, so in
    # no eligible base. Its growth is computable, so it does count toward the
    # data-quality rows over computable babies, which are not maturity-gated.
    ("y1", 180, 1500, "yes", "no", "no", R, None, 1500.0, 1550.0),
    ("y1", 186, 1600, "yes", "no", "no", F, None, None, None),
    ("y1", 193, 1700, "yes", "no", "no", F, None, None, None),
    # z1 -- died ON day 28 after the first visit: early_exit is strictly under 28,
    # so it stays in the growth funnel (healthy, 8.4 g/kg/d) and in mortality.
    # Referred on that same day 28: inside the inclusive referral window. Its
    # enrolment weight (1400 against 1600, 12.5% down) is an expected dip only
    # because the dip reaches 15%.
    ("z1", 0, 1600, "yes", "no", "no", R, None, 1600.0, 1400.0),
    ("z1", 7, 1700, "yes", "no", "no", F, None, None, None),
    ("z1", 14, 1800, "yes", "no", "no", F, None, None, None),
    ("z1", 28, None, "no", "yes", "yes", F, None, None, None),
    # r1 -- a danger sign and a referral on day 29 after registration: one day
    # outside the referral window, so not counted by C19. Enrolment weight 0.5%
    # under birthweight: credible (not a copy) but too small a loss to be a dip.
    ("r1", 0, 2100, "yes", "no", "no", R, None, 2100.0, 2090.0),
    ("r1", 10, 2250, "yes", "no", "no", F, None, None, None),
    ("r1", 20, 2400, "yes", "no", "no", F, None, None, None),
    ("r1", 29, 2500, "yes", "yes", "yes", F, None, None, None),
]
PARITY_VISITS = VISITS + WORKBOOK_VISITS

# Days from hospital discharge to registration, where a discharge DATE is recorded.
# The dates beat the app's own field: h1's app says 2 but the dates say 6.
DISCHARGE_TO_REG = {"h1": 6, "s1": 2, "k1": -1, "x1": 0, "p1": 3, "l1": 1, "m1": 1}
# Per-baby recorded values that vary, so the means and medians are not trivial.
GESTATIONAL_AGE = {"h1": 32, "s1": 30, "f1": 38, "i1": 34, "k1": 33, "x1": 36, "p1": 31, "p2": 35}
GESTATIONAL_AGE.update({"n1": 29, "m1": 34, "d1": 28, "d2": 27, "l1": 33, "g1": 50})
KMC_HOURS = {"h1": 6.0, "s1": 2.5, "f1": 8.0, "d2": 3.0, "l1": 1.5, "g1": 5.0, "x1": 4.0}
SELF_REFERRAL_VISITS = {("h1", 7), ("l1", 5), ("l1", 12), ("g1", 10)}

DDL = """
DROP TABLE IF EXISTS fixture_visits;
CREATE TEMP TABLE fixture_visits (
    baby_case_id text, visit_date timestamp, weight_g double precision,
    child_alive_no boolean, danger_sign_yes boolean, referred_yes boolean,
    self_referral_yes boolean, ebf_recorded boolean, form_name text,
    days_discharge_to_reg double precision, birth_weight_g double precision,
    enrollment_weight_g double precision, gestational_age_wks double precision, kmc_hours_mean double precision,
    reg_date timestamp, hospital_discharge_date timestamp,
    opportunity_id int, username text
);
"""


def _reg_day(baby):
    return min(off for b, off, *_ in PARITY_VISITS if b == baby)


def _load(conn):
    conn.rollback()
    cur = conn.cursor()
    cur.execute(DDL)
    for baby, off, w, alive, danger, ref, form, d2r, bw, ew in PARITY_VISITS:
        reg = _reg_day(baby)
        cur.execute(
            "INSERT INTO fixture_visits VALUES (%s, DATE '2026-01-01' + %s, %s, %s, %s, %s,"
            " %s, true, %s, %s, %s, %s, %s, %s, DATE '2026-01-01' + %s,"
            " DATE '2026-01-01' + %s - %s::int, 1, 'flw1')",
            (
                baby,
                off,
                w,
                alive == "no",
                danger == "yes",
                ref == "yes",
                (baby, off) in SELF_REFERRAL_VISITS,
                form,
                d2r,
                bw,
                ew,
                GESTATIONAL_AGE.get(baby),
                KMC_HOURS.get(baby),
                reg,
                reg,
                DISCHARGE_TO_REG.get(baby),
            ),
        )
    conn.commit()


# ── The reference: the rules, in Python ──────────────────────────────────────


def _band(bw):
    if bw is None or not (WMIN <= bw <= WMAX):
        return None
    return next((name, lo, hi) for upper, name, lo, hi in BANDS if bw < upper)


def _median(values):
    """The interpolated median (what PERCENTILE_CONT(0.5) is)."""
    v = sorted(values)
    if not v:
        return None
    mid = (len(v) - 1) / 2
    lo, hi = math.floor(mid), math.ceil(mid)
    return v[lo] + (v[hi] - v[lo]) * (mid - lo)


def _babies():
    out: dict[str, dict] = {}
    for baby, off, w, alive, danger, ref, form, d2r, bw, ew in PARITY_VISITS:
        b = out.setdefault(baby, {"rows": [], "d2r": None, "bw": None, "ew": None})
        b["rows"].append((off, w, alive, danger, ref, form))
        b["d2r"] = d2r if d2r is not None else b["d2r"]
        b["bw"] = bw if bw is not None else b["bw"]
        b["ew"] = ew if ew is not None else b["ew"]
    return out


def _weight_series(rows, bw, ew, reg_day):
    """Measured weigh-days (one per day, averaged), plus whether the enrolment
    reading counts as a further measured day."""
    by_day: dict[int, list[float]] = {}
    for off, w, *_ in rows:
        if w is not None and WMIN <= w <= WMAX:
            by_day.setdefault(off, []).append(w)
    measured = [(d, sum(ws) / len(ws)) for d, ws in sorted(by_day.items())]
    seed = (
        ew is not None
        and WMIN <= ew <= WMAX
        and not (bw is not None and abs(ew - bw) < 1)  # a copy of birthweight is a re-entry
        and reg_day not in by_day  # a visit weighed the same day wins
    )
    return measured, seed


def _properties(as_of=AS_OF):
    out = {}
    for name, b in _babies().items():
        rows, bw = b["rows"], b["bw"]
        offs = [r[0] for r in rows]
        first, last = min(offs), max(offs)
        followups = sum(1 for r in rows if "regist" not in r[5].lower())
        p = {"registered": any("regist" in r[5].lower() for r in rows), "followup_visits": followups}
        p["started"] = followups >= STARTED_MIN
        since = as_of - first
        p["eligible_28d"] = p["started"] and since >= ELIGIBLE_DAYS
        death_days = [r[0] for r in rows if r[2] == "no"]
        p["died"] = bool(death_days)
        p["outcome_known"] = p["died"] or (last - first) >= ELIGIBLE_DAYS
        # a modifier on the growth funnel only: died BEFORE the eligibility date
        p["early_exit"] = p["died"] and min(death_days) - first < ELIGIBLE_DAYS
        p["growth_eligible"] = p["eligible_28d"] and not p["early_exit"]

        measured, seed = _weight_series(rows, bw, b["ew"], _reg_day(name))
        p["n_measured_days"] = len(measured) + (1 if seed else 0)
        velocity, impossible = None, False
        if measured:
            anchor = measured[0][0]
            window = [(d, w) for d, w in measured if d - anchor <= WINDOW]
            span = window[-1][0] - window[0][0]
            mean = sum(w for _, w in window) / len(window)
            if len(window) >= 2 and span >= VELOCITY_MIN_SPAN and mean > 0:
                velocity = (window[-1][1] - window[0][1]) / span / (mean / 1000)
            for (d0, w0), (d1, w1) in zip(measured, measured[1:]):
                gap = d1 - d0
                if d1 - anchor <= WINDOW and IMPOSSIBLE_GAP[0] <= gap <= IMPOSSIBLE_GAP[1] and w0 > 0:
                    rate = (w1 - w0) / (((w1 + w0) / 2) / 1000) / gap
                    if not IMPOSSIBLE[0] <= rate <= IMPOSSIBLE[1]:
                        impossible = True
        first_w = round(measured[0][1]) if measured else None
        band = _band(bw)
        p["velocity"] = velocity
        p["impossible"] = impossible
        p["thin"] = p["n_measured_days"] < THIN_MIN_DAYS
        p["inconsistent"] = bw is not None and first_w is not None and first_w < INCONSISTENT_RATIO * bw
        # the workbook's three growth-data stages
        p["computable"] = band is not None and velocity is not None
        p["consistent"] = not (p["thin"] or p["inconsistent"] or impossible)
        p["sufficient"] = p["computable"] and p["consistent"]
        p["growth_class"] = None
        if p["sufficient"]:
            _, lo, hi = band
            p["growth_class"] = "slow" if velocity < lo else ("fast" if velocity > hi else "plausible")

        p["danger"] = any(r[3] == "yes" for r in rows)
        p["referred"] = any(r[4] == "yes" for r in rows)
        referral_days = [r[0] for r in rows if r[4] == "yes"]
        p["referred_within_28d"] = bool(referral_days) and min(referral_days) - _reg_day(name) <= REFERRAL_WINDOW
        p["self_referrals"] = sum(1 for r in rows if (name, r[0]) in SELF_REFERRAL_VISITS)
        p["kmc_hours"] = KMC_HOURS.get(name)
        ga = GESTATIONAL_AGE.get(name)
        p["ga"] = ga if ga is not None and GA_RANGE[0] <= ga <= GA_RANGE[1] else None
        p["bw"] = bw
        dated = DISCHARGE_TO_REG.get(name)
        dte = float(dated) if dated is not None else b["d2r"]
        p["days_to_enrolment"] = dte
        p["has_discharge"] = dte is not None
        p["within_3d"] = dte is not None and 0 <= dte <= 3
        ew = b["ew"]
        p["birth_copy"] = None if bw is None or ew is None else abs(bw - ew) < 1
        p["enrollment_credible"] = (
            bw is not None and ew is not None and WMIN <= bw <= WMAX and WMIN <= ew <= WMAX and abs(bw - ew) >= 1
        )
        p["expected_dip"] = p["enrollment_credible"] and DIP_RANGE[0] * bw <= ew <= DIP_RANGE[1] * bw
        raw = [r[1] for r in rows if r[1] is not None]
        p["readings"], p["round_readings"] = len(raw), sum(1 for w in raw if w % 100 == 0)
        out[name] = p
    return out


def _indicators(props):
    rows = list(props.values())

    def count(f):
        return float(sum(1 for r in rows if f(r)))

    def pct(num, den):
        base = [r for r in rows if den(r)]
        return 100.0 * sum(1 for r in base if num(r)) / len(base) if base else None

    def mean(val, den):
        vals = [val(r) for r in rows if den(r) and val(r) is not None]
        return sum(vals) / len(vals) if vals else None

    def funnel(r):
        return r["growth_eligible"]

    def judged(r):  # the base the three growth classes and the mean share
        return r["growth_eligible"] and r["sufficient"]

    def computable(r):
        return r["computable"]

    eligible = [r for r in rows if r["eligible_28d"]]
    return {
        "total_cases": float(len(rows)),
        "registered_cases": count(lambda r: r["registered"]),
        "started_cases": count(lambda r: r["started"]),
        "cumulative_svns_reached": count(lambda r: r["started"]),
        "median_gestational_age": _median([r["ga"] for r in rows if r["ga"] is not None]),
        "median_birthweight": _median([r["bw"] for r in rows if r["bw"] is not None]),
        "visits_per_case": sum(r["followup_visits"] for r in eligible) / len(eligible) if eligible else None,
        "pct_enrolled_within_3d": pct(lambda r: r["within_3d"], lambda r: r["started"] and r["has_discharge"]),
        "median_days_to_enrolment": _median(
            [r["days_to_enrolment"] for r in rows if r["started"] and r["has_discharge"]]
        ),
        "lost_by_day_28": pct(lambda r: not r["outcome_known"], lambda r: r["eligible_28d"]),
        # the chained growth funnel: each base is the previous stage's numerator
        "pct_growth_computable": pct(computable, funnel),
        "pct_growth_consistent": pct(lambda r: r["consistent"], lambda r: funnel(r) and r["computable"]),
        "pct_incomplete_growth_data": pct(lambda r: not r["consistent"], lambda r: funnel(r) and r["computable"]),
        "pct_growth_sufficient": pct(lambda r: r["sufficient"], funnel),
        "pct_healthy_growth": pct(lambda r: r["growth_class"] == "plausible", judged),
        "pct_slow_growth": pct(lambda r: r["growth_class"] == "slow", judged),
        "pct_fast_growth": pct(lambda r: r["growth_class"] == "fast", judged),
        "mean_early_growth_rate": mean(lambda r: r["velocity"], judged),
        # outcomes and care: eligible babies; death IS mortality's event, so no early_exit here
        "mortality": pct(lambda r: r["died"], lambda r: r["eligible_28d"] and r["outcome_known"]),
        "danger_sign_incidence": pct(lambda r: r["danger"], lambda r: r["eligible_28d"]),
        "pct_danger_signs_referred": pct(lambda r: r["referred_within_28d"], lambda r: r["eligible_28d"]),
        "self_referrals_per_100": mean(lambda r: r["self_referrals"] * 100, lambda r: r["eligible_28d"]),
        "mean_kmc_hours": mean(lambda r: r["kmc_hours"], lambda r: r["eligible_28d"]),
        # data quality: not maturity-gated
        "weight_rounding_rate": 100.0 * sum(r["round_readings"] for r in rows) / sum(r["readings"] for r in rows),
        "pct_impossible_weight_changes": pct(lambda r: r["impossible"], computable),
        "pct_thin": pct(lambda r: r["thin"], computable),
        "pct_inconsistent": pct(lambda r: r["inconsistent"], computable),
        "pct_enrollment_weight_credible": pct(lambda r: r["enrollment_credible"], computable),
        "pct_expected_dip": pct(lambda r: r["expected_dip"], lambda r: r["enrollment_credible"]),
        "birth_copy_rate": pct(lambda r: r["birth_copy"], lambda r: r["birth_copy"] is not None),
    }


def _programme_row(conn):
    props_doc = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    _load(conn)
    sql = compile_indicator_sql(
        props_doc, registry, "SELECT * FROM fixture_visits", scope="programme", as_of=AS_OF_SQL
    )
    cur = conn.cursor()
    cur.execute(sql)
    cols = [c.name for c in cur.description]
    return dict(zip(cols, cur.fetchone()))


def test_the_reference_covers_every_indicator_and_every_growth_branch():
    """A parity check over a fixture that leaves an indicator empty proves nothing
    about it, and a share that is always 0 or 100 cannot catch a moved boundary.
    So every indicator must have a value, every stage of the growth funnel must
    split its base, and every rule the workbook added must change at least one
    baby's membership somewhere."""
    props = _properties()
    ind = _indicators(props)
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    assert set(ind) == {m["meta"]["indicator"] for m in registry["measures"] if m.get("meta")}
    assert all(v is not None for v in ind.values()), {k for k, v in ind.items() if v is None}

    # the funnel: computable babies in it whose readings fail (None) and all three classes
    classes = {p["growth_class"] for p in props.values() if p["growth_eligible"] and p["computable"]}
    assert classes == {"slow", "plausible", "fast", None}, classes
    for share in (
        "pct_growth_computable",
        "pct_growth_consistent",
        "pct_incomplete_growth_data",
        "pct_growth_sufficient",
        "pct_slow_growth",
        "pct_healthy_growth",
        "pct_fast_growth",
    ):
        assert 0 < ind[share] < 100, share
    assert math.isclose(ind["pct_healthy_growth"] + ind["pct_slow_growth"] + ind["pct_fast_growth"], 100.0)
    assert math.isclose(ind["pct_growth_consistent"] + ind["pct_incomplete_growth_data"], 100.0)

    for rate in (
        "mortality",
        "lost_by_day_28",
        "pct_danger_signs_referred",
        "pct_impossible_weight_changes",
        "pct_thin",
        "pct_inconsistent",
        "pct_enrollment_weight_credible",
        "pct_expected_dip",
    ):
        assert 0 < ind[rate] < 100, rate

    by = props
    # started is 1+ follow-ups: d1 (exactly one) in, u1 (none) out
    assert by["d1"]["started"] and by["d1"]["followup_visits"] == 1 and not by["u1"]["started"]
    # the one 28-day gate: y1 started but not yet eligible; m1 eligible and in the funnel at 35 days
    assert by["y1"]["started"] and not by["y1"]["eligible_28d"] and by["y1"]["computable"]
    assert by["m1"]["growth_eligible"]
    # early_exit: early deaths with computable weight data leave the funnel, a death on day 28 does not
    assert by["d1"]["early_exit"] and by["d1"]["computable"] and not by["d1"]["growth_eligible"]
    assert by["d2"]["early_exit"] and by["d2"]["sufficient"] and not by["d2"]["growth_eligible"]
    assert by["z1"]["died"] and not by["z1"]["early_exit"] and by["z1"]["growth_eligible"]
    # ... and stay in mortality, where death is the event
    assert by["d2"]["eligible_28d"] and by["d2"]["outcome_known"]
    # the referral window: day 28 is in, day 29 is out
    assert by["z1"]["referred_within_28d"] and by["r1"]["referred"] and not by["r1"]["referred_within_28d"]
    # the dip: 12.5% down is one, 0.5% down is credible but not one, a copy is not credible
    assert by["z1"]["expected_dip"] and by["r1"]["enrollment_credible"] and not by["r1"]["expected_dip"]
    assert by["h1"]["birth_copy"] and not by["h1"]["enrollment_credible"]


def test_sql_matches_the_reference_implementation(conn):
    row = _programme_row(conn)
    expected = _indicators(_properties())
    mismatches = []
    for ind, want in expected.items():
        got = row.get(ind)
        if want is None and got is None:
            continue
        if want is None or got is None:
            mismatches.append(f"{ind}: sql={got!r} ref={want!r}")
        elif not math.isclose(float(got), float(want), rel_tol=1e-6, abs_tol=1e-6):
            mismatches.append(f"{ind}: sql={float(got):.6f} ref={float(want):.6f}")
    assert not mismatches, "SQL and the reference disagree:\n  " + "\n  ".join(mismatches)


def test_denominators_are_reported_alongside_values(conn):
    """No bare numbers: every indicator's denominator comes back with it."""
    props_doc = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    _load(conn)
    sql = compile_indicator_sql(
        props_doc,
        registry,
        "SELECT * FROM fixture_visits",
        scope="programme",
        as_of=AS_OF_SQL,
    )
    cur = conn.cursor()
    cur.execute(sql)
    cols = {c.name for c in cur.description}
    for m in registry["measures"]:
        if m.get("meta"):
            assert f"{m['name']}_denominator" in cols


def test_rollup_equals_per_scope_queries(conn):
    """One GROUPING SETS pass must give exactly what N separate queries gave.

    This is the guard on the optimisation: the reason to collapse the scopes into
    one pass is speed, and the only way that is a win rather than a regression is
    if the numbers are untouched.
    """
    from connect_labs.semantic.compiler import compile_rollup_sql

    props_doc = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    _load(conn)
    visit_sql = "SELECT * FROM fixture_visits"
    as_of = AS_OF_SQL
    scopes = ["programme", "opportunity", "flw", "month"]

    per_scope = {}
    cur = conn.cursor()
    for sc in scopes:
        cur.execute(compile_indicator_sql(props_doc, registry, visit_sql, scope=sc, as_of=as_of))
        cols = [c.name for c in cur.description]
        per_scope[sc] = [dict(zip(cols, r)) for r in cur.fetchall()]

    cur.execute(compile_rollup_sql(props_doc, registry, visit_sql, scopes=scopes, as_of=as_of))
    rcols = [c.name for c in cur.description]
    rolled: dict[str, list[dict]] = {s: [] for s in scopes}
    for r in cur.fetchall():
        row = dict(zip(rcols, r))
        rolled.setdefault(row["scope"], []).append(row)

    def key(sc, row):
        return tuple(str(row.get(c)) for c in ("opportunity_id", "username", "cohort_month"))

    for sc in scopes:
        assert len(rolled[sc]) == len(per_scope[sc]), f"{sc}: row count differs"
        a = {key(sc, r): r for r in per_scope[sc]}
        b = {key(sc, r): r for r in rolled[sc]}
        assert set(a) == set(b), f"{sc}: grouping keys differ"
        for k in a:
            for m in registry["measures"]:
                n = m["name"]
                x, y = a[k].get(n), b[k].get(n)
                if x is None and y is None:
                    continue
                assert x is not None and y is not None, f"{sc}/{k}/{n}: {x!r} vs {y!r}"
                assert math.isclose(
                    float(x), float(y), rel_tol=1e-9, abs_tol=1e-9
                ), f"{sc}/{k}/{n}: per-scope={x} rollup={y}"


def test_every_declared_scope_actually_executes(conn):
    """Compiling is not the bar -- the SQL has to RUN.

    `llo` compiled cleanly for weeks and failed at execution with "column
    props.llo does not exist". A test that only asserted compilation could never
    have caught it, so this one executes every scope in SCOPES against Postgres.
    Scopes needing a caller-supplied column are driven with one.
    """
    from connect_labs.semantic.compiler import INTRINSIC_SCOPE_COLUMNS, SCOPES

    props_doc = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    _load(conn)
    cur = conn.cursor()
    as_of = AS_OF_SQL

    for scope in SCOPES:
        needs_llo = "llo" in SCOPES[scope]
        sql = compile_indicator_sql(
            props_doc,
            registry,
            "SELECT * FROM fixture_visits",
            scope=scope,
            as_of=as_of,
            llo_map={1: "PIPN"} if needs_llo else None,
        )
        try:
            cur.execute(sql)
            cur.fetchall()
        except Exception as exc:  # pragma: no cover - the point is the message
            conn.rollback()
            raise AssertionError(f"scope {scope!r} compiled but did not run: {exc}") from exc

    # And the intrinsic set really is intrinsic: no map needed.
    for scope in SCOPES:
        if set(SCOPES[scope]) <= set(INTRINSIC_SCOPE_COLUMNS):
            cur.execute(
                compile_indicator_sql(
                    props_doc,
                    registry,
                    "SELECT * FROM fixture_visits",
                    scope=scope,
                    as_of=as_of,
                )
            )
            cur.fetchall()


def test_every_scope_executes_with_the_suppression_gates_on(conn):
    """Every scope must RUN with the shipped settings, not just without them.

    The sibling test above executes every scope but passes no `settings`, so no
    suppression column is ever emitted and it cannot see this class at all. A
    suppression rule is scoped by `llo`, and the emitted predicate referenced
    `props.llo` bare -- legal only where llo is a grouping column. So the moment
    the deployment facts were actually wired through, `scopes=programme`,
    `opportunity` and `flw` each returned a raw Postgres "must appear in the GROUP
    BY clause" 400 instead of a number, while `llo` and any set CONTAINING llo
    kept working. The dashboard asks for a set containing llo, which is the only
    reason this was survivable long enough to reach production.
    """
    from connect_labs.semantic.compiler import SCOPES
    from connect_labs.semantic.runtime import load_deployment

    props_doc = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    llo_map, settings = load_deployment("kmc")
    _load(conn)
    cur = conn.cursor()

    for scope in SCOPES:
        sql = compile_indicator_sql(
            props_doc,
            registry,
            "SELECT * FROM fixture_visits",
            scope=scope,
            as_of=AS_OF_SQL,
            llo_map=llo_map,
            settings=settings,
        )
        assert "_suppressed" in sql, f"scope {scope!r} emitted no gate at all"
        try:
            cur.execute(sql)
            cur.fetchall()
        except Exception as exc:  # pragma: no cover - the point is the message
            conn.rollback()
            raise AssertionError(f"scope {scope!r} did not run with the gates on: {exc}") from exc


def test_suppression_marks_the_non_credible_llo(conn):
    """Mortality must come back flagged for an LLO the settings say is not credible.

    The registry declared these rules and the compiler ignored them, so a
    mortality figure would have been published for an LLO the workbook says does
    not record deaths credibly -- the exact gap this project raised about the
    other implementation.
    """
    props_doc = yaml.safe_load((REGISTRY / "properties.yml").read_text())
    registry = yaml.safe_load((REGISTRY / "indicators.yml").read_text())
    _load(conn)
    cur = conn.cursor()
    cur.execute(
        compile_indicator_sql(
            props_doc,
            registry,
            "SELECT * FROM fixture_visits",
            scope="llo",
            as_of=AS_OF_SQL,
            llo_map={1: "GHI"},
            settings={"mortality_recording_credible": {"PIPN": True, "GHI": False}},
        )
    )
    cols = [c.name for c in cur.description]
    row = dict(zip(cols, cur.fetchone()))
    assert "mortality_suppressed" in cols, "suppression column was not emitted"
    assert row["mortality_suppressed"] is True, "GHI is not credible but mortality came back unsuppressed"
    assert row["mortality"] is not None, "the value is still computed -- suppression is display, not deletion"
