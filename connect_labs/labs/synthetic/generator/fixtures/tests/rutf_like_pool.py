"""A simulated RUTF-like (screen, then treat) transplant pool, for case-model tests.

Shaped like a community malnutrition programme seen through a fixed window: most
children are screened once and not enrolled; an enrolled child gets a follow-up form
roughly weekly, with sachets each time, until it exits (recovered, or defaulted). Only
an ENROLLED child is ever followed up, apart from a few children enrolled before the
window opened, whose first visit in the window is already a follow-up. Every case lies
inside the window [START, END]: a source never has a visit after its last day.
"""

from __future__ import annotations

import datetime as dt
import random

SCREEN = "Screening"
FOLLOW = "Visit Form"
ENROLLED = "form.screening_outcome.rutf_enrollment"
OUTCOME = "form.case_state.outcome_value"
SACHETS = "form.rutf_dispensing.rutf_sachets_dispensed"
MUAC = "form.muac_cm"
COUNTER = "form.visit_number"
FROZEN = {COUNTER}
START = dt.date(2026, 7, 1)
END = dt.date(2026, 9, 30)
CARRY_OVER_SHARE = 0.03


def rutf_like_pool(seed: int = 7, workers: int = 30) -> list[dict]:
    rng = random.Random(seed)
    window = (END - START).days
    pool = []
    for w in range(workers):
        owner = f"flw_{w + 1:03d}"
        for _ in range(rng.choice([20, 30, 40, 60])):
            carry_over = rng.random() < CARRY_OVER_SHARE
            enrolled = carry_over or rng.random() < 0.4
            start_day = rng.randint(0, window)
            muac0 = rng.uniform(10.5, 11.4) if enrolled else rng.uniform(11.5, 14.0)
            visits = [] if carry_over else [_screening(enrolled, muac0)]
            if enrolled:
                course = rng.randint(5, 10)  # follow-ups until exit
                exit_value = "recovered" if rng.random() < 0.8 else "defaulted"
                day = 0 if carry_over else 7
                for i in range(1, course + 1):
                    if start_day + day > window:
                        break  # the window closes on a child still in treatment
                    last = i == course
                    visits.append(
                        {
                            "day": day,
                            "form": FOLLOW,
                            "values": {SACHETS: float(rng.choice([12, 14, 14, 14])), MUAC: round(muac0 + 0.1 * i, 1)},
                            "cats": {OUTCOME: exit_value if last else "enrolled"},
                        }
                    )
                    day += rng.choice([6, 7, 7, 8])
            if not visits:
                continue
            for i, v in enumerate(visits, start=1):
                v["values"][COUNTER] = float(i)
            pool.append(
                {"owner": owner, "start_date": (START + dt.timedelta(days=start_day)).isoformat(), "visits": visits}
            )
    return pool


def _screening(enrolled: bool, muac: float) -> dict:
    return {
        "day": 0,
        "form": SCREEN,
        "values": {MUAC: round(muac, 1)},
        "cats": {ENROLLED: "yes" if enrolled else "no"},
    }
