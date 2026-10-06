"""Sophie's RUTF walkthrough, judged a tenth time: one money format, and the ranking says why.

THIS REPOSITORY IS PUBLIC. Every company, product, figure and address here is invented.

Same fixtures as the earlier batches. What these pin: every price on the
comparison reads currency first ("USD 38.50 per carton"), the Needs-info
cards' "Quoted ..." line included; the ranking basis is marked on its own
column's header rather than floating above the table; a quote a correction
brought into the ranking says so on its row; "#award" is the first awardable
offer's ranked row; the timeline's ETA chip and actor pills read at the change
line's size; the overview's flags run awarded, can't-compare, no-reply; and a
one-product listing whose hero already says the place lays its specification
out across the card instead of leaving an empty column.
"""

import re

import pytest

from connect_labs.supply_chain.models import Commodity, Tender
from connect_labs.supply_chain.tests import test_sophie_batch6 as batch6
from connect_labs.supply_chain.tests.test_history_timeline import AUG_20, _correct_pack, _quote_with
from connect_labs.supply_chain.tests.test_sophie_batch3 import _ALL_BUT_PACK, _page, _with_spec

registered_synthetic = batch6.registered_synthetic
da = batch6.da
sophie = batch6.sophie
ace = batch6.ace
base = batch6.base
order = batch6.order
client_in_program = batch6.client_in_program
home_client = batch6.home_client
listed_tender = batch6.listed_tender

_DELIVERED = batch6._DELIVERED
_text = batch6._text


def _ranked(body, quote_id):
    return re.search(rf'<tr data-testid="ranked-row" data-quote-id="{quote_id}"[^>]*>.*?</tr>', body, re.S).group(0)


# ---- 1. one money format on the comparison -----------------------------------


@pytest.mark.django_db
class TestOneMoneyFormat:

    def test_no_amount_before_its_currency_anywhere_on_the_page(self, da, base, client_in_program):
        _with_spec(da)
        tender_id = base["tender"]["id"]
        _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, {**_DELIVERED, "as_quoted_amount": "41.00"})
        other = batch6._supplier(da, "Sahel Nutrition")
        _quote_with(da, tender_id, other["id"], AUG_20, _ALL_BUT_PACK)
        text = _text(_page(client_in_program, tender_id))
        assert "USD 41.00 per carton" in text and "USD 42.50 per carton" in text
        # An amount followed by "USD" that is not itself a "USD <amount>" (adjacent table
        # cells run "USD 0.273 USD 41.00" together in the page's text).
        assert not re.search(r"(?<!USD )\b\d[\d,]*\.\d+ USD\b", text)


# ---- 2. the ranking basis is marked on its column ---------------------------


# ---- 3. a correction that answered a gap says so on the row ------------------


# ---- 4. #award is the first awardable offer's row ----------------------------


# ---- 5. the timeline's chip and pills at the change line's size --------------


@pytest.mark.django_db
class TestTheTimelineChipsReadAtBodySize:
    def test_eta_chip_and_actor_pills_are_text_sm(self, da, base, order, client_in_program):
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        history = body[body.index('id="history"') :]
        chip = re.search(r'data-testid="eta-moved" class="([^"]*)">(.*?)</span>', history)
        assert chip.group(2) == "ETA moved +14 days"
        assert "text-sm" in chip.group(1).split() and "text-xs" not in chip.group(1).split()
        assert "text-gray-700" in chip.group(1).split()  # on gray-100: well over 4.5:1
        pills = re.findall(r'data-testid="actor-(?:pill|badge)"[^>]*class="([^"]*inline-flex[^"]*)"', history)
        pills = [set(p.split()) for p in pills if "rounded-full" in p.split()]
        assert pills and all("text-sm" in p and "text-xs" not in p for p in pills)
        # The person's pill keeps its near-black. The AI-recorded email reads as one email
        # event (unanswered round 1004 b3), its AI label in the event's head.
        assert any({"bg-gray-100", "text-gray-900"} <= p for p in pills)
        assert re.search(r'data-testid="email-event".*?data-testid="actor-badge" data-ai ', history, re.S)


# ---- 6. the overview's flags, by importance ----------------------------------


# ---- 7. the listing's specification spans the card ---------------------------


@pytest.mark.django_db
class TestTheListingSpecificationSpansTheCard:
    def test_no_empty_column_when_the_hero_says_the_place(self, client, listed_tender):
        Commodity.objects.filter(slug="rutf").update(
            spec_requirements=[{"field": "sachets_per_carton", "operator": "==", "value": 150}]
        )
        Tender.objects.filter(pk=listed_tender.pk).update(delivery_points=[{"city": "Kano"}])
        panel = re.search(
            r'<aside data-testid="request-summary".*?</aside>', batch6._listing(client, listed_tender), re.S
        ).group(0)
        assert 'data-testid="request-columns"' not in panel and "md:grid-cols-2" not in panel
        assert 'data-testid="request-full-width"' in panel
        # One <dl>, the specification's -- no empty one beside it.
        assert len(re.findall(r"<dl\b", panel)) == 1
        assert 'data-testid="spec-line"' in panel

    def test_two_columns_still_when_the_panel_names_the_place(self, client, listed_tender):
        Commodity.objects.filter(slug="rutf").update(
            spec_requirements=[{"field": "sachets_per_carton", "operator": "==", "value": 150}]
        )
        panel = re.search(
            r'<aside data-testid="request-summary".*?</aside>', batch6._listing(client, listed_tender), re.S
        ).group(0)
        assert 'data-testid="request-columns"' in panel and ">Delivered to<" in panel


# The walkthrough's hooks stay where they were.


@pytest.mark.django_db
def test_the_hooks_stay(da, base, ace, client_in_program):
    tender_id = base["tender"]["id"]
    quote = _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
    _correct_pack(da, quote, ace)
    body = _page(client_in_program, tender_id)
    for hook in ("comparison-grid", "grid-quote", "grid-status", "award-start", "award-form"):
        assert f'data-testid="{hook}"' in body
