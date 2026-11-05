"""Round 2, "Award emails", "Purchase sizing" and the comparison: what the emails say and promise.

Sophie Feintuch's walk of the demo (Nov 2026). THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain.procurement.services import selection
from connect_labs.supply_chain.tests import purchase_walk_world as walk
from connect_labs.supply_chain.tests.chlorine_world import op

pytestmark = pytest.mark.django_db

da = walk.da
account = walk.account
client_in_programme = walk.client_in_programme


def _drafts(da, world):
    return op(da, "selection_drafts_render", award_id=world["award"]["id"])["drafts"]


def test_the_selection_email_asks_for_a_certificate_per_batch_and_the_shelf_life(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=())
    text = _drafts(da, world)[0]["text"]
    assert "certificate of analysis for each batch" in text
    assert "at least 18 months" in text
    # up front: before the partners are introduced
    assert text.index("certificate of analysis") < text.index("partner organisation")


def test_the_shelf_life_follows_the_tender_when_it_names_one(da, monkeypatch):
    from connect_labs.supply_chain.models import Tender

    world = walk.build(da, monkeypatch, ordered=())
    Tender.objects.filter(pk=world["tender"]["id"]).update(shelf_life_months_minimum=24)
    assert "at least 24 months" in _drafts(da, world)[0]["text"]


def test_partners_are_not_called_copied_when_there_is_no_address(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=(), contacts=False)
    selection_email = _drafts(da, world)[0]
    assert "copied here" not in selection_email["text"]
    assert "we will introduce you to each by email" in selection_email["text"]
    assert not selection_email["cc"]


def test_partners_with_an_address_are_copied_and_said_to_be(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=())
    selection_email = _drafts(da, world)[0]
    assert "(they are copied here)" in selection_email["text"]
    assert {"north@example.org", "river@example.org", "savanna@example.org"} <= set(selection_email["cc"])


def test_a_partial_set_names_only_who_is_copied(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=(), contacts=False)
    from connect_labs.supply_chain.models import Tender

    tender = Tender.objects.get(pk=world["tender"]["id"])
    splits = tender.partner_splits
    splits[0]["receiver_contact"] = "north@example.org"
    Tender.objects.filter(pk=tender.pk).update(partner_splits=splits)
    text = _drafts(da, world)[0]["text"]
    assert "(North Star is copied here; we will introduce you to the others)" in text


def test_a_handover_goes_to_the_partners_lead_with_the_receiver_copied(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=())
    monkeypatch.setattr(
        selection,
        "_partner_lead",
        lambda tender, split: {
            "name": "Ngozi Eze",
            "email": "ngozi@northstar.example",
            "copy_to": ["ops@northstar.example"],
        },
    )
    handover = next(d for d in _drafts(da, world) if d["kind"] == "handover")
    assert handover["to"] == "ngozi@northstar.example"
    assert set(handover["cc"]) == {"north@example.org", "ops@northstar.example"}
    assert handover["text"].startswith("Dear Ngozi Eze,")


def test_a_handover_with_no_lead_still_reaches_the_receiver(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=())
    handover = next(d for d in _drafts(da, world) if d["kind"] == "handover")
    assert handover["to"] == "north@example.org" and not handover["cc"]


def test_each_handover_says_what_to_do_on_arrival_when_to_expect_it_and_the_suppliers_phone(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north",))
    walk.in_transit(da, world, "north", expected="2026-11-20")
    handovers = [d for d in _drafts(da, world) if d["kind"] == "handover"]
    north = next(h for h in handovers if "North Star" in h["label"])
    assert "North Star's share is below" in north["text"] and "This is what" not in north["text"]
    assert "count the cartons" in north["text"] and "batch number and expiry" in north["text"]
    assert "confirm receipt to us" in north["text"]
    assert "Expected: 20 Nov 2026" in north["text"]
    assert "Supplier phone: +234 800 000 0000" in north["text"]


def test_a_handover_without_a_recorded_shipment_says_the_date_is_only_the_quoted_lead_time(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=())
    from connect_labs.supply_chain.models import Quote

    Quote.objects.filter(pk=world["quote"]["id"]).update(lead_time_days=30)
    from datetime import date

    from connect_labs.supply_chain.models import Award

    Award.objects.filter(pk=world["award"]["id"]).update(decided_on=date(2026, 10, 1))
    handover = next(d for d in _drafts(da, world) if d["kind"] == "handover")
    assert "Expected: about 31 Oct 2026 (the quoted lead time)" in handover["text"]


def test_the_award_page_renders_the_emails(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north",))
    response = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "certificate of analysis for each batch" in body and "North Star&#x27;s share is below" in body


def test_the_lead_and_copies_come_from_the_partners_card(da, monkeypatch):
    from connect_labs.labs.models import LabsOrg
    from connect_labs.supply_chain.partners.services import set_profile

    world = walk.build(da, monkeypatch, ordered=())
    set_profile(
        da.program_id,
        LabsOrg.objects.get(pk=world["orgs"]["north"]["id"]),
        contact_name="Ngozi Eze",
        contact_email="ngozi@northstar.example",
        copy_to=[{"name": "Ops", "email": "ops@northstar.example"}],
    )
    drafts = _drafts(da, world)
    north = next(d for d in drafts if d["kind"] == "handover" and "North Star" in d["label"])
    assert north["to"] == "ngozi@northstar.example"
    assert set(north["cc"]) == {"north@example.org", "ops@northstar.example"}
    assert {"ngozi@northstar.example", "ops@northstar.example"} <= set(drafts[0]["cc"])
