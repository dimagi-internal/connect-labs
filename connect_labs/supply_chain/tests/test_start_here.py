"""The "Start here" page: tab groups, steps by moment ticked from the records, entry points by role.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain.start_here import MOMENTS, ROLES, moments_for
from connect_labs.supply_chain.tests import test_workflow_views as pinned

client_in_programme = pinned.client_in_programme
da = pinned.da


def _states(facts):
    return {s.key: s.done for m in moments_for(facts) for s in m["steps"]}


def test_a_new_programme_has_nothing_ticked_and_each_moment_points_at_its_first_step():
    moments = moments_for({})
    assert [m["key"] for m in moments] == ["setup", "purchase", "week", "month"]
    assert [m["next"].key for m in moments[:3]] == ["partners", "size", "plan"]
    # The monthly checks are recurring: nothing to tick, so nothing is "next".
    assert moments[3]["next"] is None and all(s.done is None for s in moments[3]["steps"])


def test_steps_tick_from_what_the_records_hold():
    states = _states({"partner_plans": 2, "suppliers": 3, "product": 1, "tenders": 1, "tender_sized": 1, "quotes": 2})
    assert states["partners"] and states["suppliers"] and states["product"]
    assert states["size"] and states["tender"] and states["quotes"]
    assert states["compare"] is False and states["orders"] is False


def test_every_step_names_a_real_page():
    for _, _, _, steps in MOMENTS:
        for _, _, _, view in steps:
            reverse(view)
    for _, _, _, pages in ROLES:
        for view, _ in pages:
            reverse(view)


@pytest.mark.django_db
def test_the_page_loads_with_every_section(client_in_programme):
    body = client_in_programme.get(reverse("supply_chain:start_here")).content.decode()
    assert 'data-testid="start-here"' in body
    assert body.count('data-testid="start-group"') == 4
    assert body.count('data-testid="start-moment"') == 4
    assert body.count('data-testid="start-role"') == 3
    for title in ("Setting up", "Each purchase", "Every week", "Every month or so"):
        assert title in body
    for role in ("Program lead", "LLO manager", "Supervisor"):
        assert role in body
    assert reverse("supply_chain:plan") in body


@pytest.mark.django_db
def test_the_side_menu_offers_it_and_marks_it_current(client_in_programme):
    body = client_in_programme.get(reverse("supply_chain:start_here")).content.decode()
    menu = body.split('data-testid="supply-tabs"', 1)[1].split("</nav>", 1)[0]
    assert "Start here" in menu and 'aria-current="page"' in menu.split("Start here")[0].rsplit("<a", 1)[1]


@pytest.mark.django_db
def test_the_page_ticks_what_the_programme_has(client_in_programme):
    from connect_labs.supply_chain.models import Tender

    Tender.objects.create(program_id=pinned.PROGRAM, label="Round 1", status="open")
    body = client_in_programme.get(reverse("supply_chain:start_here")).content.decode()
    assert body.count('data-state="done"') >= 1
    assert "1 tender" in body


def test_the_supply_icon_in_the_sidebar_toggles_the_rail(client_in_programme):
    """Like the Marketplace icon for its menu: on a supply page the sidebar's Supply icon opens
    or closes the rail rather than reloading the home page (Jonathan, 2026-11-06)."""
    body = client_in_programme.get(reverse("supply_chain:start_here")).content.decode()
    assert 'data-testid="supply-rail"' in body
    assert f'var HOME = "{reverse("supply_chain:home")}";' in body
    assert "#labs-sidenav a[href=" in body
