"""A request for quotes is a tender (2026-09-25); it used to be a "round".

Pins the two things a rename can quietly leave behind: an operation (and so an
MCP tool) still under the old name, and a shared link to the old address.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain.operations import all_operations

pytestmark = pytest.mark.django_db


def test_no_operation_is_still_called_a_round():
    assert not [name for name in all_operations() if "round" in name]
    assert {"tender_create", "tender_open", "tender_compare"} <= set(all_operations())


@pytest.mark.parametrize(
    "old, new",
    [
        ("/supply/procurement/rounds/7/", "/supply/procurement/tenders/7/"),
        (
            "/supply/procurement/rounds/7/compare/?commodity=rutf",
            "/supply/procurement/tenders/7/compare/?commodity=rutf",
        ),
        ("/supply/market/rounds/7/", "/supply/market/tenders/7/"),
    ],
)
def test_an_old_round_link_lands_on_the_tender(client, old, new):
    response = client.get(old)
    assert response.status_code == 301
    assert response.url == new


def test_the_new_addresses_are_the_tender_ones():
    assert reverse("supply_chain:procurement_tender_detail", args=[7]) == "/supply/procurement/tenders/7/"
    assert reverse("supply_chain:market_tender", args=[7]) == "/supply/market/tenders/7/"
