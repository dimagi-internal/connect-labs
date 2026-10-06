"""The supply chain seeded behind a synthetic clone of the real RUTF opportunity (demo/clone_supply.py).

THIS REPOSITORY IS PUBLIC. The visits here are the invented stock-from-visits
world, which uses the released app's form paths -- the same shape a clone has.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.supply_chain.demo import clone_supply
from connect_labs.supply_chain.demo.stock_from_visits import WORKERS, build_world
from connect_labs.supply_chain.models import DispensingRule, Distribution, Movement, SupplyPoint

START = date(2026, 8, 3)
TODAY = START + timedelta(weeks=8, days=-1)
FETCH = "connect_labs.supply_chain.stock.services.visit_source.fetch_visits"


def test_each_issue_covers_the_next_fortnight_by_a_factor_of_the_workers_own():
    monday = date(2026, 8, 3)
    dispensed = {
        "a": {monday: Decimal(20), monday + timedelta(weeks=1): Decimal(10), monday + timedelta(weeks=2): Decimal(7)},
        "b": {monday + timedelta(weeks=3): Decimal(14)},
    }
    issues = clone_supply.plan_distributions(dispensed, first=monday, last=monday + timedelta(weeks=3))

    assert [i["on"] for i in issues] == [monday, monday + timedelta(weeks=2)]
    first, second = issues
    # Worker a: 30 given out over the first fortnight, issued 0.8x-1.6x of it, in tens.
    assert Decimal(24) <= first["lines"]["a"] <= Decimal(50) and first["lines"]["a"] % 10 == 0
    # Nothing to give out in a fortnight, nothing issued: b only appears once it dispenses.
    assert "b" not in first["lines"]
    assert set(second["lines"]) == {"a", "b"}


@pytest.fixture
def clone(db):
    """A registered labs-only opportunity, its own programme, serving the invented world's visits."""
    opp = SyntheticOpportunity.objects.create(
        opportunity_id=SyntheticOpportunity.next_labs_only_opp_id(),
        labs_only=True,
        enabled=True,
        label="Clone of the RUTF opportunity (test)",
        org_name="Test org",
        program_name="Test programme",
        gdrive_folder_id="",
    )
    registry.invalidate_cache()
    world = build_world(START)
    with patch(FETCH, return_value=world.visits):
        yield {"opp": opp.opportunity_id, "world": world}
    registry.invalidate_cache()


def test_the_clone_is_seeded_end_to_end_and_every_invented_figure_says_so(clone):
    opp = clone["opp"]
    with patch(FETCH, return_value=clone["world"].visits):
        summary = clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)

    assert summary["seeded"] is True
    assert DispensingRule.objects.filter(program_id=opp, opportunity_id=opp).count() == 1
    workers = SupplyPoint.objects.filter(program_id=opp, kind="user_held")
    assert {p.connect_username for p in workers} == set(WORKERS)

    runs = Distribution.objects.filter(program_id=opp)
    assert runs.exists()
    assert all(r.note.startswith("Invented for the demo") for r in runs)
    issued = sum(m.quantity for m in Movement.objects.filter(program_id=opp, kind="distribution"))
    assert issued == Decimal(summary["sachets_issued"])

    consumed = sum(
        m.quantity for m in Movement.objects.filter(program_id=opp, kind="consumption", reverses__isnull=True)
    )
    # What the reader posted is what the seeder sized the issues from (bar reversals of rejected visits).
    assert consumed >= Decimal(summary["sachets_dispensed"]) - Decimal(200)
    assert consumed > 0


def test_a_second_run_changes_nothing_without_reset(clone):
    opp = clone["opp"]
    with patch(FETCH, return_value=clone["world"].visits):
        clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)
        before = Movement.objects.filter(program_id=opp).count()
        again = clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)

    assert again["seeded"] is False
    assert Movement.objects.filter(program_id=opp).count() == before


def test_a_real_programme_is_refused_before_anything_is_read(db):
    with patch(FETCH) as fetch, pytest.raises(ValueError, match="refusing"):
        clone_supply.seed(program_id=263, opportunity_id=2230)
    fetch.assert_not_called()
