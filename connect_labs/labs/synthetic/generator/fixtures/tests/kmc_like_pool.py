"""A simulated KMC-like transplant pool with known per-case structure, for case-model tests.

Built to have exactly the relationships a real KMC programme shows and analyses rely
on: birth weight drives the growth slope, slow growers die more often (and their
visits stop), visit gaps widen over a case, each worker has its own caseload, and the
app computes a visit counter and the child's age from the case's timing.
"""

from __future__ import annotations

import datetime as dt
import random

REG = "Register KMC Beneficiary"
VISIT = "Record Visit Details"
WEIGHT = "form.child_weight_visit"
BIRTH_WEIGHT = "form.case.update.birth_weight"
COUNTER = "form.visit_number"
AGE = "form.child_age_days"
DOB = "form.case.update.child_dob"
ALIVE = "form.child_alive"
STATUS = "form.kmc_status"
FROZEN = {COUNTER, AGE}


def kmc_like_pool(seed: int = 7, workers: int = 40) -> list[dict]:
    rng = random.Random(seed)
    pool = []
    for w in range(workers):
        owner = f"flw_{w + 1:03d}"
        for _ in range(rng.choice([1, 2, 3, 5, 8, 12, 20, 30])):
            bw = max(900.0, rng.gauss(1800, 300))
            slope = max(-5.0, 15 + 0.012 * (bw - 1800) + rng.gauss(0, 5))
            age0 = rng.randint(1, 10)
            n = rng.randint(2, 12)
            p_die = 0.02 if slope > 14 else 0.10
            start = dt.date(2026, 3, 1) + dt.timedelta(days=rng.randint(0, 120))
            visits = []
            day = 0
            for i in range(1, n + 1):
                if i > 1:
                    day += max(1, int(rng.gauss(3 + i * 0.6, 1.2)))
                alive = "yes"
                if i > 1 and rng.random() < p_die:
                    alive = "no"
                v = {
                    "day": day,
                    "form": REG if i == 1 else VISIT,
                    "values": {
                        WEIGHT: round(bw + slope * day + rng.gauss(0, 25)),
                        COUNTER: float(i),
                        AGE: float(age0 + day),
                    },
                    "dates": {DOB: -age0},
                    "cats": {ALIVE: alive, STATUS: rng.choice(["active", "active", "active", "paused"])},
                }
                if i == 1:
                    v["values"][BIRTH_WEIGHT] = round(bw)
                visits.append(v)
                if alive == "no":
                    break
            pool.append({"owner": owner, "start_date": start.isoformat(), "visits": visits})
    return pool
