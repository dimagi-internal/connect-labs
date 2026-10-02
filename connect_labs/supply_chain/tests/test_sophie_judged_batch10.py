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
from connect_labs.supply_chain.standing import AWARDED_GAP_RULE, BLOCKED_RULE, NO_REPLY_RULE
from connect_labs.supply_chain.tests import test_sophie_batch6 as batch6
from connect_labs.supply_chain.tests import test_sophie_judged_batch7 as judged7
from connect_labs.supply_chain.tests.test_history_timeline import AUG_20, AUG_28, _correct_pack, _quote_with, op
from connect_labs.supply_chain.tests.test_sophie_batch3 import _ALL_BUT_PACK, _home, _page, _with_spec

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
    def test_the_quoted_line_reads_currency_first(self, da, base, client_in_program):
        _with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        card = batch6._card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        price = _text(re.search(r'<p data-testid="as-quoted"[^>]*>(.*?)</p>', card, re.S).group(1))
        assert price == "Quoted USD 42.50 per carton"

    def test_a_per_sachet_price_and_its_conversion_read_the_same_way(self, da, base, client_in_program):
        quote = _quote_with(
            da,
            base["tender"]["id"],
            base["supplier"]["id"],
            AUG_20,
            {
                **_DELIVERED,
                "freight_basis": "not_specified",
                "as_quoted_amount": "0.29",
                "as_quoted_unit": "per_base_unit",
            },
        )
        card = batch6._card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        price = _text(re.search(r'<p data-testid="as-quoted"[^>]*>(.*?)</p>', card, re.S).group(1))
        # The stated price and what it comes to per carton, both currency first.
        assert price == "Quoted USD 0.29 per sachet = USD 43.50 per carton"
        assert not re.search(r"\d USD\b", _text(card))

    def test_no_amount_before_its_currency_anywhere_on_the_page(self, da, base, client_in_program):
        _with_spec(da)
        tender_id = base["tender"]["id"]
        _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, {**_DELIVERED, "as_quoted_amount": "41.00"})
        other = batch6._supplier(da, "Sahel Nutrition")
        _quote_with(da, tender_id, other["id"], AUG_20, _ALL_BUT_PACK)
        text = _text(_page(client_in_program, tender_id))
        assert "USD 41.00" in text and "USD 42.50 per carton" in text
        # An amount followed by "USD" that is not itself a "USD <amount>" (adjacent table
        # cells run "USD 0.273 USD 41.00" together in the page's text).
        assert not re.search(r"(?<!USD )\b\d[\d,]*\.\d+ USD\b", text)


# ---- 2. the ranking basis is marked on its column ---------------------------


@pytest.mark.django_db
class TestTheRankingBasisIsOnItsColumn:
    def test_the_header_says_it_and_nothing_floats_above_the_table(self, da, base, client_in_program):
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        body = _page(client_in_program, base["tender"]["id"])
        head = re.search(r"<thead>.*?</thead>", body, re.S).group(0)
        marked = re.findall(r"<th>([^<]*)<span data-testid=\"ranked-by-marker\"[^>]*>(.*?)</span></th>", head)
        assert len(marked) == 1
        label, marker = marked[0]
        assert label.startswith("Landed total")
        assert _text(marker) == "Ranked by this, lowest first"
        assert "Ranked by Landed total" not in body
        assert 'data-testid="ranked-by-fallback"' not in body


# ---- 3. a correction that answered a gap says so on the row ------------------


@pytest.mark.django_db
class TestTheRowSaysItJoinedTheRanking:
    def test_a_quote_the_correction_brought_in(self, da, base, ace, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        corrected = _correct_pack(da, quote, ace)
        ranked = _ranked(_page(client_in_program, base["tender"]["id"]), corrected["id"])
        line = re.search(r'<p data-testid="joined-ranking" class="([^"]*)">(.*?)</p>', ranked, re.S)
        assert _text(line.group(2)) == "Joined the ranking 28 Aug after ACE (agent) recorded the supplier's answer"
        # It sits under the supplier, in the supplier's cell, and wraps.
        assert ranked.index("Northwind Foods") < ranked.index('data-testid="joined-ranking"')
        assert "whitespace-normal" in line.group(1).split() and "whitespace-nowrap" not in line.group(1)

    def test_not_for_a_correction_to_a_figure_it_already_had(self, da, base, ace, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        corrected = op(
            da,
            "quote_correct",
            AUG_28,
            channel="mcp",
            actor=ace,
            quote_id=quote["id"],
            data={"as_quoted_amount": "40.00"},
            reason="the supplier revised its price",
        )
        ranked = _ranked(_page(client_in_program, base["tender"]["id"]), corrected["id"])
        assert 'data-testid="joined-ranking"' not in ranked

    def test_not_for_a_quote_that_was_never_corrected(self, da, base, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        assert 'data-testid="joined-ranking"' not in _ranked(
            _page(client_in_program, base["tender"]["id"]), quote["id"]
        )


# ---- 4. #award is the first awardable offer's row ----------------------------


@pytest.mark.django_db
class TestTheAwardAnchorIsTheRow:
    def test_the_row_carries_it_and_the_form_does_not(self, da, base, client_in_program):
        tender_id = base["tender"]["id"]
        first = _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, {**_DELIVERED, "as_quoted_amount": "41.00"})
        other = batch6._supplier(da, "Sahel Nutrition")
        _quote_with(da, tender_id, other["id"], AUG_20, {**_DELIVERED, "as_quoted_amount": "44.00"})
        body = _page(client_in_program, tender_id)
        assert body.count('id="award"') == 1
        row = re.search(r'<tr data-testid="ranked-row" data-quote-id="(\d+)" id="award" class="([^"]*)">', body)
        assert row.group(1) == str(first["id"]) and "scroll-mt-20" in row.group(2).split()
        # The form keeps its hook, and is the next one after the anchor.
        assert not re.search(r'<form [^>]*id="award"', body)
        form = re.search(r'<form [^>]*data-testid="award-form".*?</form>', body[row.start() :], re.S).group(0)
        assert f'name="quote_id" value="{first["id"]}"' in form

    def test_an_awarded_offer_hands_the_anchor_on(self, da, base, client_in_program):
        tender_id = base["tender"]["id"]
        first = _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, {**_DELIVERED, "as_quoted_amount": "41.00"})
        other = batch6._supplier(da, "Sahel Nutrition")
        second = _quote_with(da, tender_id, other["id"], AUG_20, {**_DELIVERED, "as_quoted_amount": "44.00"})
        op(da, "award_create", AUG_28, tender_id=tender_id, quote_id=first["id"], rationale="cheapest landed")
        body = _page(client_in_program, tender_id)
        assert re.search(rf'<tr data-testid="ranked-row" data-quote-id="{second["id"]}" id="award"', body)


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
        # The AI pill keeps its indigo, the person's its near-black.
        assert any({"bg-indigo-50", "text-indigo-800"} <= p for p in pills)
        assert any({"bg-gray-100", "text-gray-900"} <= p for p in pills)


# ---- 6. the overview's flags, by importance ----------------------------------


@pytest.mark.django_db
class TestTheFlagsRunByImportance:
    def test_awarded_then_cant_compare_then_no_reply(self, da, base, home_client):
        _with_spec(da)
        judged7._provisional(da, base)
        flags = judged7._tender_row().stale
        assert [f.rule for f in flags] == [AWARDED_GAP_RULE, BLOCKED_RULE, NO_REPLY_RULE]
        assert flags[0].startswith("Awarded quote:")
        # And on the page, in that order.
        standing = batch6._standing(_home(home_client))
        at = [standing.index(text) for text in ("Awarded quote:", "Can&#x27;t compare yet", "No reply in")]
        assert at == sorted(at)


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
    for hook in ("ranked-row", "ranked-row-detail", "ranked-table", "correction-source", "award-form"):
        assert f'data-testid="{hook}"' in body
