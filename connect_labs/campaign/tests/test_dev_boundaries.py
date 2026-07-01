"""Dev boundary-seeder tests (local scale stand-in for the real GeoPoDe data)."""
from __future__ import annotations

import pytest

from connect_labs.campaign.services import dev_boundaries, geography

pytestmark = pytest.mark.django_db


def test_seeds_national_hierarchy():
    info = dev_boundaries.seed_demo_boundaries(lgas_per_state=2, wards_per_lga=2)
    assert info["states"] == 37
    assert info["lgas"] == 74
    assert info["wards"] == 148
    assert geography.is_loaded() is True
    states = geography.states()
    assert len(states) == 37
    assert {s.name for s in states} >= {"Kano", "Lagos", "Federal Capital Territory"}
    # hierarchy walks: each state -> 2 LGAs -> 2 wards each
    lgas = geography.lgas(states[0])
    assert len(lgas) == 2
    assert len(geography.wards(lgas[0])) == 2
