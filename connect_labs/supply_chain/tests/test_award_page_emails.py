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
