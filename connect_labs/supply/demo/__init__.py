"""The Operation End Starvation demo world.

Three modules, split by what changes for what reason:

* :mod:`.data`        — the narrative itself: which suppliers exist, which
  corridors they serve, what is in flight. Edit this to change the story.
* :mod:`.procurement` — writes suppliers, EOI rounds, solicitations, awards.
* :mod:`.execution`   — writes nodes, contracts, shipments and their events.

The management command is a thin wrapper over :func:`seed_demo_world`.
"""
import random

from django.db import transaction

from .data import SEED, STAFF, SUPPLIER_LOGIN, demo_password
from .execution import reset_execution, seed_execution
from .procurement import ProcurementSeeder

__all__ = [
    "seed_demo_world",
    "reset_execution",
    "seed_execution",
    "ProcurementSeeder",
    "demo_password",
    "STAFF",
    "SUPPLIER_LOGIN",
    "SEED",
]


@transaction.atomic
def seed_demo_world(reset=False):
    """Build the whole demo world and return a summary of what was written.

    Deterministic: one PRNG, seeded once, shared by both halves so the world is
    identical on every run.
    """
    rng = random.Random(SEED)
    return ProcurementSeeder(rng).seed(reset=reset)
