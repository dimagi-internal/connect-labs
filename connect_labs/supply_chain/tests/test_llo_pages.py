"""Round 3, step 2: Stock by LLO and This week, one page per local partner.

Sophie Feintuch's walk of the demo (Nov 2026). THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import purchase_walk_world as walk
from connect_labs.supply_chain.tests.chlorine_world import op

pytestmark = pytest.mark.django_db

OPPORTUNITY = 4245

da = walk.da
account = walk.account
client_in_programme = walk.client_in_programme


def _flw(da, world, key, name):
    return op(
        da,
        "supply_point_upsert",
        data={
            "slug": name,
            "name": name,
            "kind": "user_held",
            "parent_supply_point_id": world["places"][key]["id"],
            "connect_username": name,
            "opportunity_id": OPPORTUNITY,
            "source": "we_recorded",
        },
    )


def _plan(da, world, key, flw, *, day=None, reference="Monday handout"):
    return op(
        da,
        "distribution_plan",
        data={
            "supply_point_id": world["places"][key]["id"],
            "opportunity_id": OPPORTUNITY,
            "commodity_slug": "rutf",
            "distributed_on": (day or date.today()).isoformat(),
            "reference": reference,
            "source": "we_recorded",
            "lines": [{"to_supply_point_id": flw["id"], "quantity": "2", "quantity_unit": "carton"}],
        },
    )


@pytest.fixture
def world(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north", "river"))
    walk.deliver(da, world, "north")
    walk.in_transit(da, world, "river", expected="2026-11-20")
    return world


def test_the_menu_has_stock_by_llo_and_this_week(client_in_programme, world):
    body = client_in_programme.get(reverse("supply_chain:stock_by_llo")).content.decode()
    assert ">Stock by LLO<" in body and ">This week<" in body


def test_stock_by_llo_lists_each_llo_with_a_roll_up_and_a_map_switch(client_in_programme, world):
    body = client_in_programme.get(reverse("supply_chain:stock_by_llo")).content.decode()
    rows = body.split('data-testid="llo-table"')[1]
    assert "North Star" in rows and "Riverbend" in rows
    assert reverse("supply_chain:llo_stock", args=[world["orgs"]["north"]["id"]]) in rows
    assert 'data-testid="llo-roll-up"' in body
    # the list/map switch: the list is here, the map one click away
    assert reverse("supply_chain:programme_map") in body
    # North Star has stock and Riverbend has none yet
    north = rows.split('data-testid="llo-row"')[1]
    assert "carton" in north
    # Riverbend's order is on the way, expected on the 20th
    assert "20 Nov 2026" in rows


def test_an_llo_page_shows_its_stock_locations_flws_and_open_issues(client_in_programme, da, world):
    _flw(da, world, "north", "flw_001")
    op(
        da,
        "stock_issue_report",
        data={
            "supply_point_id": world["places"]["north"]["id"],
            "kind": "damaged",
            "occurred_on": date.today().isoformat(),
            "commodity_slug": "rutf",
            "quantity": "3",
            "quantity_unit": "carton",
            "source": "partner_reported",
        },
    )
    url = reverse("supply_chain:llo_stock", args=[world["orgs"]["north"]["id"]])
    body = client_in_programme.get(url).content.decode()
    assert 'data-testid="llo-title"' in body and "North Star" in body
    assert "North Star warehouse" in body.split('data-testid="llo-places"')[1]
    assert "flw_001" in body.split('data-testid="llo-flws"')[1]
    issues = body.split('data-testid="llo-issues"')[1].split("</section>")[0]
    assert "Damaged" in issues and "3 carton" in issues
    # restocking a store is here, in one place: send stock from a location, suggested deliveries
    menu = body.split('data-testid="send-stock-menu"')[1].split("</details>")[0]
    assert "from_supply_point=" in menu and "Suggested deliveries" in menu
    assert "Suggested deliveries" in body


def test_an_llo_page_lists_deliveries_on_the_way(client_in_programme, world):
    body = client_in_programme.get(
        reverse("supply_chain:llo_stock", args=[world["orgs"]["river"]["id"]])
    ).content.decode()
    deliveries = body.split('data-testid="llo-deliveries"')[1]
    assert "expected 20 Nov 2026" in deliveries


def test_an_llo_that_holds_nothing_is_a_404(client_in_programme, world):
    assert client_in_programme.get(reverse("supply_chain:llo_stock", args=[987654])).status_code == 404
    assert client_in_programme.get(reverse("supply_chain:llo_week", args=[987654])).status_code == 404


def test_this_week_lists_each_llo_with_what_is_to_confirm(client_in_programme, da, world):
    flw = _flw(da, world, "north", "flw_001")
    _plan(da, world, "north", flw)
    body = client_in_programme.get(reverse("supply_chain:this_week")).content.decode()
    rows = body.split('data-testid="week-table"')[1]
    north = rows.split('data-testid="week-row"')[1].split("</tr>")[0]
    assert "North Star" in north
    assert reverse("supply_chain:llo_week", args=[world["orgs"]["north"]["id"]]) in north


def test_an_llos_week_has_the_handout_with_buttons_that_come_back_here(client_in_programme, da, world):
    flw = _flw(da, world, "north", "flw_001")
    run = _plan(da, world, "north", flw)
    url = reverse("supply_chain:llo_week", args=[world["orgs"]["north"]["id"]])
    body = client_in_programme.get(url).content.decode()
    assert 'data-testid="week-title"' in body and "North Star: this week" in body
    handouts = body.split('data-testid="week-handouts"')[1]
    assert "Monday handout" in handouts and "planned, not yet confirmed" in handouts
    assert reverse("supply_chain:distribution_confirm_as_planned", args=[run["id"]]) in handouts
    assert f'name="next" value="{url}"' in handouts
    assert reverse("supply_chain:distribution_record") in body and reverse("supply_chain:distribution_plan") in body
    # one place: the plan (what to give each FLW, with the working) and the handouts, not two pages
    assert 'data-testid="week-plan"' in body and "What to give each FLW" in body


def test_confirming_from_this_week_returns_to_this_week(client_in_programme, da, world):
    flw = _flw(da, world, "north", "flw_001")
    run = _plan(da, world, "north", flw)
    url = reverse("supply_chain:llo_week", args=[world["orgs"]["north"]["id"]])
    response = client_in_programme.post(
        reverse("supply_chain:distribution_confirm_as_planned", args=[run["id"]]), {"next": url}
    )
    assert response.status_code == 302 and response["Location"] == url
    # an address that is not ours is not followed
    again = client_in_programme.post(
        reverse("supply_chain:distribution_confirm_as_planned", args=[run["id"]]),
        {"next": "https://elsewhere.example/"},
    )
    assert again["Location"] == reverse("supply_chain:distribution")


def test_an_old_confirmed_handout_is_left_off_the_week(client_in_programme, da, world):
    flw = _flw(da, world, "north", "flw_001")
    _plan(da, world, "north", flw, day=date.today() - timedelta(days=40), reference="Old handout")
    body = client_in_programme.get(
        reverse("supply_chain:llo_week", args=[world["orgs"]["north"]["id"]])
    ).content.decode()
    # unconfirmed, so it is a flag to follow up even though it is old
    assert "Old handout" in body


def test_the_compact_layout_keeps_the_menu_it_chose(monkeypatch):
    """The chlorine programs chose the compact menu; round 3's RUTF pages are not added to it."""
    from types import SimpleNamespace

    from connect_labs.supply_chain import navigation

    request = SimpleNamespace(labs_context={})
    monkeypatch.setattr(navigation, "compact_layout", lambda request: True)
    assert navigation.LLO_TABS <= navigation.hidden_tabs(request)
    monkeypatch.setattr(navigation, "compact_layout", lambda request: False)
    assert not navigation.LLO_TABS & navigation.hidden_tabs(request)
