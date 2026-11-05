"""The award page offers the selection emails and the trade item its winning quote describes.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented. Builds on rehearsal_world.
"""

from connect_labs.supply_chain.tests import rehearsal_world

da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program


def test_the_award_page_offers_the_emails_and_the_trade_item(da, world, client_in_program, monkeypatch):
    from django.urls import reverse

    from connect_labs.supply_chain.reference import views as reference_views
    from connect_labs.supply_chain.tests.rehearsal_world import op as rop

    monkeypatch.setattr(reference_views, "_access", lambda request: da)
    quote = rop(
        da,
        "quote_record",
        data={
            "tender_id": world["tender"]["id"],
            "commodity_slug": "rutf",
            "supplier_id": world["kanem"]["id"],
            "as_quoted_amount": "50",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "2000",
            "quantity_basis_unit": "carton",
            "base_per_pack_stated": 150,
        },
    )
    award = rop(da, "award_create", tender_id=world["tender"]["id"], quote_id=quote["id"])
    body = client_in_program.get(reverse("supply_chain:award_detail", args=[award["id"]])).content.decode()
    assert 'data-testid="selection-drafts"' in body and "it was selected" in body
    assert 'data-testid="item-offer"' in body and f"from_quote={quote['id']}" in body
    form = client_in_program.get(reverse("supply_chain:item_create") + f"?from_quote={quote['id']}").content.decode()
    assert 'value="150"' in form


def test_an_order_has_one_status_on_the_award_page_and_the_overview(da, world, client_in_program, monkeypatch):
    """Riverbend's order was "confirmed" on the award page and "in transit" on the Overview: one label now."""
    from datetime import date

    from django.urls import reverse

    from connect_labs.supply_chain.reference import views as reference_views
    from connect_labs.supply_chain.standing import order_status_labels, standing_rows
    from connect_labs.supply_chain.tests.rehearsal_world import PROGRAM
    from connect_labs.supply_chain.tests.rehearsal_world import op as rop

    monkeypatch.setattr(reference_views, "_access", lambda request: da)
    quote = rop(
        da,
        "quote_record",
        data={
            "tender_id": world["tender"]["id"],
            "commodity_slug": "rutf",
            "supplier_id": world["kanem"]["id"],
            "as_quoted_amount": "50",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "2000",
            "quantity_basis_unit": "carton",
            "base_per_pack_stated": 150,
        },
    )
    award = rop(da, "award_create", tender_id=world["tender"]["id"], quote_id=quote["id"])
    order = rop(
        da,
        "contract_create",
        data={
            "award_id": award["id"],
            "tender_id": world["tender"]["id"],
            "commodity_slug": "rutf",
            "supplier_id": world["kanem"]["id"],
            "buyer_of_record": "programme_org",
            "buyer_org_id": world["us"]["id"],
            "reference": "PO-RB-1",
            "quantity": "600",
            "quantity_unit": "carton",
            "status": "confirmed",
            "source": "we_recorded",
        },
    )
    rop(
        da,
        "shipment_record",
        data={
            "contract_id": order["id"],
            "reference": "SH-RB-1",
            "status": "dispatched",
            "dispatched_on": "2026-11-01",
            "expected_on": "2026-11-20",
            "source": "supplier_reported",
        },
    )

    label = order_status_labels(PROGRAM, [order["id"]], today=date(2026, 11, 5))[order["id"]]
    assert label == "In transit · expected 20 Nov"
    overview = next(r for r in standing_rows(PROGRAM, date(2026, 11, 5)) if r.contract_id == order["id"])
    assert overview.label == label  # the Overview says it the same way

    body = client_in_program.get(reverse("supply_chain:award_detail", args=[award["id"]])).content.decode()
    assert 'data-testid="award-order-status"' in body and "In transit · expected 20 Nov" in body
    assert "· Confirmed" not in body and "· confirmed" not in body
