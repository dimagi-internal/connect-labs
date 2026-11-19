"""Have the slow part of a scenario's answer made before anyone asks for it.

A scenario's answer is fast (a few hundred milliseconds) once ``people.people_by_place`` has resolved its places: that
step reads every region and every measure, takes about seven seconds for all of Africa by country, and is cached per
release of the data and the code. So the first person to open a page after a deploy, or after the cache lapses, waits
for it, and so does a group's "check against today's data" (six scenarios, one wait each at worst). This makes those
answers ahead of time: the questions every page opens on, and the ones the saved scenarios ask. ``tasks.warm_people``
runs it every ten minutes; when everything is already there it costs a few cache reads.
"""

from __future__ import annotations

import logging
import time

from connect_labs.effectiveness import explore, people
from connect_labs.effectiveness.models import SavedScenario
from connect_labs.effectiveness.scenario import GRAINS, Scenario

logger = logging.getLogger(__name__)

#: How many distinct saved questions are kept ready (the most recently changed first).
MOST_SAVED = 50


def questions() -> list[tuple]:
    """The ``(question, grain)`` pairs worth having ready, each once: Africa-wide at every grain (what a page opens
    on), then what each recently changed saved scenario asks."""
    found: dict[tuple, tuple] = {}

    def add(scenario: Scenario) -> None:
        question, _ = people.question_for(scenario)
        found.setdefault((question.cache_key(), scenario.grain), (question, scenario.grain))

    for grain in GRAINS:
        add(Scenario.from_params({"grain": grain}))
    for params in SavedScenario.objects.order_by("-updated_at").values_list("params", flat=True)[: MOST_SAVED * 4]:
        if len(found) >= len(GRAINS) + MOST_SAVED:
            break
        try:
            add(Scenario.from_params({k: str(v) for k, v in params.items()}))
        except explore.ExploreError:
            continue  # a saved scenario the models can no longer read: nothing to prepare for it
    return list(found.values())


def warm() -> list[dict]:
    """Make each question's people-by-place now (a no-op for one already kept). Returns what each took, in seconds."""
    out = []
    for question, grain in questions():
        started = time.monotonic()
        people.people_by_place(question, grain)
        out.append({"grain": grain, "question": question.cache_key(), "seconds": round(time.monotonic() - started, 2)})
    return out
