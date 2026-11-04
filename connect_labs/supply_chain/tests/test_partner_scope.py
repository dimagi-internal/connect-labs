"""Per partner first, with a roll-up: the partner switcher, `?partner=` on Network, Stock and Workers, and the
Overview's open issues. Placeholder names only (public repo).
"""

from datetime import date

import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.issues import open_issues
from connect_labs.supply_chain.models import Commodity, StockIssue, SupplyPoint
from connect_labs.supply_chain.partners import scope

pytestmark = pytest.mark.django_db

PROGRAM = 10984


@pytest.fixture
def two_partners():
    a = LabsOrg.objects.create(slug="scope-a", name="Scope Partner A")
    b = LabsOrg.objects.create(slug="scope-b", name="Scope Partner B")
    store_a = SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="store-a",
        name="Store A",
        kind="central_store",
        managed_by_org=a,
        source="we_recorded",
    )
    flw_a = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="flw-a", name="Flw A", kind="user_held", parent=store_a,
        connect_username="flw-a", source="we_recorded",
    )  # fmt: skip
    store_b = SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="store-b",
        name="Store B",
        kind="central_store",
        managed_by_org=b,
        source="we_recorded",
    )
    return a, b, store_a, flw_a, store_b


def test_a_partners_points_include_the_workers_below_its_places(two_partners):
    a, b, store_a, flw_a, store_b = two_partners
    assert scope.point_ids_of(PROGRAM, a.pk) == {store_a.pk, flw_a.pk}
    assert scope.point_ids_of(PROGRAM, b.pk) == {store_b.pk}


def test_the_switcher_lists_the_partners_and_keeps_the_rest_of_the_query(two_partners, rf):
    request = rf.get("/supply/stock/", {"item_id": "7", "partner": str(two_partners[0].pk)})
    out = scope.switcher(request, PROGRAM)
    assert [p["name"] for p in out["partner_options"]] == ["Scope Partner A", "Scope Partner B"]
    assert out["partner_id"] == two_partners[0].pk and out["partner_name"] == "Scope Partner A"
    assert out["partner_all_href"] == "/supply/stock/?item_id=7"
    assert (
        "item_id=7" in out["partner_options"][1]["href"]
        and f"partner={two_partners[1].pk}" in out["partner_options"][1]["href"]
    )


def test_the_network_filter_keeps_only_the_chosen_partners_tree():
    from connect_labs.supply_chain.network.views import _only_partner

    groups = [{"org_id": 1, "name": "A"}, {"org_id": 2, "name": "B"}, {"org_id": None, "name": "none"}]
    assert _only_partner(groups, None) == groups
    assert _only_partner(groups, 2) == [{"org_id": 2, "name": "B"}]


def test_open_issues_name_the_partner_and_link_to_its_page(two_partners):
    a, b, store_a, flw_a, store_b = two_partners
    commodity = Commodity.objects.create(
        scope_key=f"prog:{PROGRAM}", slug="scope-product", name="Scope Product", base_unit="sachet"
    )
    StockIssue.objects.create(
        program_id=PROGRAM, supply_point=store_a, kind="missing", occurred_on=date.today(), commodity=commodity,
        quantity=3, quantity_unit="carton", source="partner_reported",
    )  # fmt: skip
    StockIssue.objects.create(
        program_id=PROGRAM, supply_point=store_b, kind="damaged", occurred_on=date.today(), commodity=commodity,
        quantity=1, quantity_unit="carton", source="partner_reported", status="resolved",
    )  # fmt: skip
    rows = open_issues(PROGRAM)
    assert len(rows) == 1
    assert rows[0]["partner"] == "Scope Partner A" and rows[0]["title"].startswith("Missing: 3 carton")
    assert rows[0]["href"].endswith(f"?partner={a.pk}")
    assert reverse("supply_chain:issues") in rows[0]["href"]
