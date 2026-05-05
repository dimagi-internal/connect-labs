"""Timeline → per-FLW visit schedule expansion.

Deterministic given the manifest's ``random_seed``. Archetype controls
how many visits an FLW produces relative to the cadence mean.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from .manifest import FlwPersona, Timeline


@dataclass(frozen=True)
class VisitSlot:
    flw_id: str
    visit_date: dt.date
    week_index: int  # 1-based


_ARCHETYPE_MULTIPLIER = {
    "rockstar": 1.20,
    "steady": 1.00,
    "struggling": 0.65,
    "new_hire": 0.55,
}


def expand_visit_schedule(
    timeline: Timeline,
    personas: list[FlwPersona],
    *,
    random_seed: int,
) -> list[VisitSlot]:
    rng = random.Random(random_seed)
    slots: list[VisitSlot] = []
    cadence_mean = timeline.visit_cadence_per_week_per_flw.mean
    cadence_std = timeline.visit_cadence_per_week_per_flw.stddev

    for week in range(1, timeline.weeks + 1):
        week_start = timeline.start_date + dt.timedelta(days=(week - 1) * 7)
        for persona in personas:
            mult = _ARCHETYPE_MULTIPLIER.get(persona.archetype, 1.0)
            mean = max(0.0, cadence_mean * mult)
            count = max(0, int(round(rng.gauss(mean, cadence_std))))
            for _ in range(count):
                day_offset = rng.randint(0, 6)
                slots.append(
                    VisitSlot(
                        flw_id=persona.id,
                        visit_date=week_start + dt.timedelta(days=day_offset),
                        week_index=week,
                    )
                )
    return slots
