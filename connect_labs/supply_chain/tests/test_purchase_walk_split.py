"""Round 2, "Split tender page" and "Award page": the set is counted, the award is shown, words not codes.

Sophie Feintuch's walk of the demo (Nov 2026). THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain.procurement.services.award_orders import split_progress
from connect_labs.supply_chain.tests import purchase_walk_world as walk

pytestmark = pytest.mark.django_db

da = walk.da
account = walk.account
client_in_programme = walk.client_in_programme


@pytest.fixture
def world(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north", "river"))
    walk.deliver(da, world, "north")
    walk.in_transit(da, world, "river")
    return world


def _tender_page(client, world):
    response = client.get(reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]]))
    assert response.status_code == 200
    return response.content.decode()


def test_the_set_is_counted_with_each_partners_order_and_what_is_left(client_in_programme, world):
    body = _tender_page(client_in_programme, world)
    assert "Ordered 2 of 3 · Delivered 1 of 3" in body
    assert "To do: Savanna to order" in body
    assert "2 of 3 ordered" in body and "1 of 3 delivered" in body  # the stage bar's notes
    split = body.split('data-testid="partner-splits"', 1)[1].split("</section>", 1)[0]
    assert "Received" in split and "In transit" in split and "Not ordered yet" in split
    assert "North Star warehouse, Kano" in split and "ns-wh" not in split
    assert "900 cartons" in split
    assert "We buy and ship to the partner" in split and "programme buys" not in split.lower()


def test_the_tender_stage_is_not_done_until_every_partner_has_ordered_and_received(client_in_programme, world):
    body = _tender_page(client_in_programme, world)
    states = body.split('data-testid="stage-bar"', 1)[1].split("</ol>", 1)[0]
    assert states.count('data-stage-state="now"') == 1
    assert states.index('data-stage-state="now"') < states.index("Delivery")  # still at Ordered


def test_the_price_holding_for_the_split_is_said(client_in_programme, world):
    assert "Supplier confirmed the price holds for the split (3 purchases)" in _tender_page(client_in_programme, world)


def test_the_award_is_shown_on_the_tender_and_the_decide_menu_is_gone(client_in_programme, world):
    body = _tender_page(client_in_programme, world)
    link = reverse("supply_chain:award_detail", args=[world["award"]["id"]])
    assert 'data-testid="award-card"' in body and link in body
    assert "Awarded to Dabs Nutritional Products Ltd" in body
    assert "4 emails to send" in body
    assert "Nothing is due on this tender today" not in body
    assert "award emails on" in body
    assert "Extend or close" not in body


def test_the_history_reads_in_words(client_in_programme, world):
    body = _tender_page(client_in_programme, world)
    assert "DEMO-GRN-1" not in body
    assert "North Star warehouse" in body


def test_a_supplier_who_quoted_is_counted_in_the_suppliers_heading(client_in_programme, world):
    body = _tender_page(client_in_programme, world)
    assert "0 of 0 answered" not in body and "1 of 1 answered" in body


def test_split_progress_names_nothing_for_an_ordinary_tender(da, monkeypatch):
    from connect_labs.supply_chain.models import Tender

    world = walk.build(da, monkeypatch, ordered=())
    Tender.objects.filter(pk=world["tender"]["id"]).update(partner_splits=[])
    assert split_progress(Tender.objects.get(pk=world["tender"]["id"])) is None


def test_the_award_page_has_a_real_title_whole_cents_and_orders_named_by_partner(client_in_programme, world):
    response = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Award: Dabs Nutritional Products Ltd, Demo split tender" in body
    assert ">rutf<" not in body
    rows = body.split('data-testid="award-order-row"')[1:]
    assert len(rows) == 3  # Savanna's draft order was cancelled: it says so
    text = " ".join(rows)
    assert "North Star · 900 cartons" in text and "Riverbend · 600 cartons" in text
    assert "Received" in text and "In transit" in text and "Cancelled" in text
    assert "confirmed" not in text.lower()
    assert "at the full 168-sachet course" in body
    import re

    assert not re.search(r"USD [\d,]+\.\d{4}", body.split('data-testid="award-title"', 1)[1].split("Approvals", 1)[0])
