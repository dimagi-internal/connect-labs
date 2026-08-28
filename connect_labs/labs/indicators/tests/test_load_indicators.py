"""Which boundaries the population stage asks WorldPop for.

The stage was ADM1-only, which is the right default — but it made ADM2
unreachable, and where a method resolves at ADM2 a district with no population
of its own contributes no births. Population is a count, so it can never be
inherited from the province above: the only way to fill it is to fetch it.
"""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from connect_labs.labs.indicators.sources import worldpop
from connect_labs.labs.indicators.tests.test_resolve import make_boundary

pytestmark = pytest.mark.django_db


@pytest.fixture
def captured(monkeypatch):
    """Record the boundaries handed to WorldPop instead of fetching them."""
    seen: list = []

    def fake_load(boundaries, year=2020, max_workers=4, on_progress=None, sink=None):
        seen.extend(boundaries)
        return 0, []

    monkeypatch.setattr(worldpop, "load", fake_load)
    return seen


def _one_country():
    make_boundary("AGO", 1, "Moxico", "AGO-1-1", x=2)
    make_boundary("AGO", 2, "Alto Zambeze", "AGO-2-1", x=4)


def test_adm1_only_by_default(captured):
    _one_country()

    call_command("load_indicators", "--stage", "population", "--source", "worldpop", "--iso", "AGO")

    assert [b.admin_level for b in captured] == [1]


def test_adm2_is_reachable_when_asked_for(captured):
    _one_country()

    call_command("load_indicators", "--stage", "population", "--source", "worldpop", "--iso", "AGO", "--levels", "2")

    assert [b.admin_level for b in captured] == [2]


def test_both_levels_run_shallowest_first(captured):
    """Order matters on a quota: the base layer should not be left short."""
    _one_country()

    call_command("load_indicators", "--stage", "population", "--source", "worldpop", "--iso", "AGO", "--levels", "1,2")

    assert [b.admin_level for b in captured] == [1, 2]


def test_adm0_is_never_requested(captured):
    """A country outline is over the service's area cap, and its population is
    the sum of its regions — a second measurement could only disagree."""
    make_boundary("AGO", 0, "Angola", "AGO-0", x=0)
    _one_country()

    call_command("load_indicators", "--stage", "population", "--source", "worldpop", "--iso", "AGO", "--levels", "1,2")

    assert 0 not in [b.admin_level for b in captured]


def test_an_unsupported_level_is_refused(captured):
    _one_country()

    with pytest.raises(CommandError):
        call_command(
            "load_indicators", "--stage", "population", "--source", "worldpop", "--iso", "AGO", "--levels", "3"
        )
