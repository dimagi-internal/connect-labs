"""Market pages never show history: no revisions, no source excerpts, no as-of.

THIS REPOSITORY IS PUBLIC. Every id, name and figure below is invented.

Design §4.5: /supply/market/ shows only what a public tender publishes. An
AI-entered quote carries a `source_excerpt` -- the private text an agent read
to type it in -- and that must never reach a market page, in any of its
forms: the raw excerpt text, the price it was carrying, or the `_timeline.html`
partial itself (its `data-timeline` root attribute is the marker), for a
signed-in visitor or an anonymous one.

See docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §4.5.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Supplier, Tender
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10501
PRIVATE_PRICE = "913.47"
PRIVATE_EXCERPT = "PRIVATE-EXCERPT"


def op(name, *, channel=None, source=None, **payload):
    full_payload = dict(payload)
    if source is not None:
        full_payload["source"] = source
    return call_operation(name, SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM), full_payload, channel=channel)


@pytest.fixture
def open_tender():
    op(
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF", "category": "therapeutic_food", "base_unit": "sachet"},
    )
    made = op(
        "tender_create",
        data={
            "label": "RUTF tender leak check",
            "delivery_point": {"name": "Central store", "city": "Kano", "country_name": "Nigeria"},
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        },
    )
    op("tender_open", tender_id=made["id"])
    return Tender.objects.get(pk=made["id"])


@pytest.fixture
def ai_entered_quote(open_tender):
    """A quote an AI agent typed in from a private email, over MCP."""
    org = LabsOrg.objects.create(slug="theirs-supply-ltd", name="Theirs Supply Ltd")
    link = Supplier.objects.enrol(f"prog:{PROGRAM}", org=org, type="manufacturer")
    return op(
        "quote_record",
        channel="mcp",
        source={"ref": "email-2026-09-26-001", "excerpt": PRIVATE_EXCERPT},
        data={
            "tender_id": open_tender.pk,
            "commodity_slug": "rutf",
            "supplier_id": link.pk,
            "as_quoted_amount": PRIVATE_PRICE,
            "as_quoted_unit": "per_pack",
            "as_quoted_currency": "USD",
        },
    )


class TestMarketPagesLeakNothing:
    def test_the_tender_page_has_neither_the_excerpt_nor_the_price_nor_a_timeline(
        self, client, open_tender, ai_entered_quote
    ):
        body = client.get(reverse("supply_chain:market_tender", args=[open_tender.pk])).content.decode()

        assert PRIVATE_EXCERPT not in body
        assert PRIVATE_PRICE not in body
        assert "data-timeline" not in body

    def test_the_market_listing_has_neither_the_excerpt_nor_the_price_nor_a_timeline(
        self, client, open_tender, ai_entered_quote
    ):
        body = client.get(reverse("supply_chain:market")).content.decode()

        assert PRIVATE_EXCERPT not in body
        assert PRIVATE_PRICE not in body
        assert "data-timeline" not in body

    def test_an_anonymous_visitor_to_the_market_listing_sees_neither(self, client, open_tender, ai_entered_quote):
        """Same request, made explicit: no session, no login, nothing extra given."""
        assert client.session.get("labs_oauth") is None

        body = client.get(reverse("supply_chain:market")).content.decode()

        assert PRIVATE_EXCERPT not in body
        assert PRIVATE_PRICE not in body
        assert "data-timeline" not in body
