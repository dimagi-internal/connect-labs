"""Sophie's RUTF walkthrough, judged a ninth time: nothing spills, and one order for each card.

THIS REPOSITORY IS PUBLIC. Every company, product, figure and address here is invented.

Same fixtures as the earlier batches. What these pin: the overview's award price
stacks under "awarded to ..." a clause a line and is allowed to wrap, in a stage
column wide enough for it (it printed over "Waiting on"); every Needs-info card
reads in one order -- blocking, facts, question, draft email, the rest -- and the
draft email is the card's action at body size; the no-reply flag opens on when
each request went out and the rule; the timeline groups a day's changes under
one date, and the person's pill reads at body contrast; the listing preview says
where a supplier bids in plain words and its panel does not repeat the hero; the
ranked table is as wide as its figures; and the first award form is "#award".
"""

import datetime
import re

import pytest

from connect_labs.supply_chain.models import Tender
from connect_labs.supply_chain.tests import test_sophie_batch6 as batch6
from connect_labs.supply_chain.tests import test_sophie_judged_batch6 as judged6
from connect_labs.supply_chain.tests import test_sophie_judged_batch7 as judged7
from connect_labs.supply_chain.tests.test_history_timeline import AUG_20, AUG_28, _quote_with, op
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
owner = batch6.owner

_DELIVERED = batch6._DELIVERED
_text = batch6._text


def _stage_cell(standing):
    return re.search(r'<td class="px-4 py-2.5">\s*awarded to Northwind Foods(.*?)</td>', standing, re.S).group(1)


# ---- 1. the award price stacks in the stage cell ----------------------------


@pytest.mark.django_db
class TestTheAwardPriceStacks:
    def test_a_clause_a_line_under_the_stage_with_no_leading_dot(self, da, base, home_client):
        judged7._provisional(da, base)
        cell = _stage_cell(batch6._standing(_home(home_client)))
        price = re.search(r'<span data-testid="award-price" class="([^"]*)">', cell)
        assert {"block", "text-xs", "text-gray-600"} <= set(price.group(1).split())
        lines = re.findall(r'data-testid="award-price-line" class="block">(.*?)</span>', cell)
        assert lines == ["USD 42.50 per carton", "USD 25,500.00 landed (incl. freight and duties) for 600 cartons"]
        assert not any(line.startswith("·") for line in lines)

    def test_nothing_in_the_stage_cell_refuses_to_wrap(self, da, base, home_client):
        # The batch 8 defect: a whitespace-nowrap price ran out of its column and printed
        # over "Waiting on". Nothing in this cell may be held on one line.
        judged7._provisional(da, base)
        cell = _stage_cell(batch6._standing(_home(home_client)))
        assert "whitespace-nowrap" not in cell and "overflow-visible" not in cell

    def test_the_stage_column_has_room_and_the_columns_fill_the_table(self, da, base, home_client):
        # At 1440px the table is about 1,100px wide: 18% is ~200px less padding, which holds
        # "awarded to Northwind Foods" and "USD 25,500.00 for 600 cartons" at text-xs on a line
        # or two each, and it is fixed layout, so a long name wraps inside it, never past it.
        judged7._provisional(da, base)
        standing = batch6._standing(_home(home_client))
        assert "table-fixed" in re.search(r'data-testid="standing-table" class="([^"]*)"', standing).group(1)
        widths = {k: int(v) for k, v in re.findall(r'data-col="([a-z-]+)" style="width: (\d+)%"', standing)}
        # Unanswered-round 002: "Waiting on" stacks a line per owner, so it takes
        # room from the title column; stage keeps enough for the award line.
        assert widths["stage"] == 15 and widths["waiting"] > widths["title"] and sum(widths.values()) == 100


# ---- 2. one order on every Needs-info card ----------------------------------


def _order_of(card, hooks):
    found = [(card.index(f'data-testid="{hook}"'), hook) for hook in hooks if f'data-testid="{hook}"' in card]
    return [hook for _, hook in sorted(found)]


# Since DDD 002 batch 2 each gap and the question that clears it share a row, so the
# question comes before the facts the gaps are read against.
ORDER = ["blocking", "blocking-question", "card-facts", "draft-email-link", "also-confirm"]


@pytest.mark.django_db
class TestOneOrderOnEveryCard:
    def _cards(self, da, base, client_in_program):
        _with_spec(da)
        tender_id = base["tender"]["id"]
        # One blocked on the pack (its requirement is a fact line), one on freight (its
        # stated figures are the fact lines): each used to put them somewhere else.
        judged6._outreach(da, tender_id, base["supplier"]["id"], datetime.date(2026, 8, 10), responded=True)
        pack = _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        other = batch6._supplier(da, "Sahel Nutrition")
        judged6._outreach(da, tender_id, other["id"], datetime.date(2026, 8, 10), responded=True)
        freight = _quote_with(da, tender_id, other["id"], AUG_20, {**_DELIVERED, "freight_basis": "not_specified"})
        body = _page(client_in_program, tender_id)
        return batch6._card(body, pack["id"]), batch6._card(body, freight["id"])

    def test_both_cards_read_in_the_same_order(self, da, base, client_in_program):
        pack, freight = self._cards(da, base, client_in_program)
        for card in (pack, freight):
            present = _order_of(card, ORDER)
            assert present == [hook for hook in ORDER if hook in present]
            assert {"blocking", "card-facts", "blocking-question"} <= set(present)
        # The pack card's requirement line and the freight card's stated figures are both facts.
        facts = re.search(r'data-testid="card-facts">(.*?)data-testid="draft-email-line"', pack, re.S)
        assert "Sachets per carton: not stated (tender requires 150)" in facts.group(1)
        facts = re.search(r'data-testid="card-facts">(.*?)data-testid="draft-email-line"', freight, re.S)
        assert 'data-testid="specification"' in facts.group(1)
        # Nothing of the specification after the email any more.
        assert 'data-testid="specification"' not in freight[freight.index('data-testid="draft-email-line"') :]

    def test_the_draft_email_is_the_card_s_action_at_body_size(self, da, base, client_in_program):
        pack, _ = self._cards(da, base, client_in_program)
        line = re.search(
            r'<p data-testid="draft-email-line" class="([^"]*)"><a data-testid="draft-email-link"[^>]*class="([^"]*)"',
            pack,
        )
        assert "text-sm" in line.group(1).split() and "text-xs" not in line.group(1).split()
        assert {"border", "rounded-md", "font-medium"} <= set(line.group(2).split())


# ---- 3. the no-reply flag opens on what it rests on --------------------------


@pytest.mark.django_db
class TestTheNoReplyFlagOpens:
    def test_when_it_was_asked_and_the_rule(self, da, base):
        judged7._provisional(da, base)
        # Since batch 10 the no-reply reminder comes after can't-compare.
        flag = next(f for f in judged7._tender_row().stale if f.startswith("No reply"))
        assert flag == "No reply in 33 days: 1 supplier"
        assert flag.heading == flag
        assert flag.lines == ("Plateau Mills — asked 10 Aug, 33 days ago", "Flagged after 14 days without a reply")

    def test_it_is_the_same_chip_and_folds_open(self, da, base, home_client):
        judged7._provisional(da, base)
        standing = batch6._standing(_home(home_client))
        flags = re.findall(r'<details data-testid="stale-flag".*?</details>', standing, re.S)
        no_reply = next(f for f in flags if "No reply in" in f)
        assert re.search(r'data-testid="flag-line"[^>]*>Plateau Mills — asked 10 Aug, \d+ days ago<', no_reply)
        assert re.search(r'data-testid="flag-line"[^>]*>Flagged after 14 days without a reply<', no_reply)
        assert "title=" in no_reply.split(">", 1)[0]


# ---- 4. the timeline: a day's changes under one date --------------------------


@pytest.mark.django_db
class TestTheTimelineGroupsByDay:
    def test_each_day_is_said_once(self, da, base, order, sophie, client_in_program):
        # A second change on 28 Aug, so one day holds two lines.
        op(
            da,
            "shipment_update",
            AUG_28,
            channel="web",
            actor=sophie,
            shipment_id=order["shipment"]["id"],
            data={"contract_id": order["contract"]["id"], "expected_on": "2026-09-20", "source": "supplier_reported"},
        )
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        history = body[body.index('id="history"') :]
        days = re.findall(r'<li data-testid="timeline-day"[^>]*>\s*<time[^>]*>(.*?)</time>', history)
        lines = re.findall(r'data-testid="revision-line"[^>]*data-when="([^"]*)"', history)
        assert days and len(days) == len(set(days))
        assert len(days) == len({when[:10] for when in lines}) < len(lines)
        # The date is the heading's, not repeated on each line.
        assert len(re.findall(r"<time ", history)) == len(days)

    def test_the_person_s_pill_reads_at_body_contrast(self, da, base, order, client_in_program):
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        pills = re.findall(r'data-testid="actor-(?:pill|badge)"(?! data-ai)[^>]*class="([^"]*)"', body)
        grey = [set(p.split()) for p in pills if "bg-gray-100" in p.split()]
        assert grey and all("text-gray-900" in p and "text-gray-700" not in p for p in grey)


# ---- 5. the listing preview: words, not a dead button; no repeated hero ------


@pytest.mark.django_db
class TestTheListingPreview:
    def test_the_preview_says_where_suppliers_bid_in_plain_words(self, owner, listed_tender):
        body = batch6._listing(owner, listed_tender, as_supplier="1")
        bid = re.search(r'<(\w+) data-testid="preview-bid"([^>]*)>(.*?)</\1>', body)
        assert bid.group(1) == "p" and bid.group(3) == "Suppliers place their bid from this page."
        classes = set(re.search(r'class="([^"]*)"', bid.group(2)).group(1).split())
        assert not classes & {"border", "rounded", "bg-gray-50", "cursor-not-allowed", "pg-link"}
        assert "Suppliers bid here" not in body

    def test_the_panel_does_not_repeat_the_hero(self, client, listed_tender):
        Tender.objects.filter(pk=listed_tender.pk).update(delivery_points=[{"city": "Kano"}])
        body = batch6._listing(client, listed_tender)
        panel = re.search(r'<aside data-testid="request-summary".*?</aside>', body, re.S).group(0)
        assert re.search(r'data-testid="asked-for"[^>]*>500 cartons<', body)
        assert re.search(r'data-testid="delivered-to"[^>]*>Kano<', body)
        assert ">Quantity<" not in panel and ">Delivered to<" not in panel and "500 cartons" not in panel

    def test_a_named_store_is_still_said(self, client, listed_tender):
        # "Central store, Lakeside" says more than the hero's "Lakeside": it stays.
        panel = re.search(
            r'<aside data-testid="request-summary".*?</aside>', batch6._listing(client, listed_tender), re.S
        ).group(0)
        assert ">Delivered to<" in panel and "Central store" in panel


# ---- 6-7. the ranked table is as wide as its figures; #award lands on a form --


@pytest.mark.django_db
class TestTheRankedTable:
    def _body(self, da, base, client_in_program):
        tender_id = base["tender"]["id"]
        first = _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, {**_DELIVERED, "as_quoted_amount": "41.00"})
        other = batch6._supplier(da, "Sahel Nutrition")
        second = _quote_with(da, tender_id, other["id"], AUG_20, {**_DELIVERED, "as_quoted_amount": "44.00"})
        return _page(client_in_program, tender_id), first, second

    def test_the_table_spans_the_page_like_the_cards_below_it(self, da, base, client_in_program):
        # Full width since the unanswered round's batch 2, matching the Needs-info cards
        # (empty trailing columns are dropped instead). The full-width rows under each
        # offer still contribute no width (w-0 min-w-full) and wrap inside it.
        body, _, _ = self._body(da, base, client_in_program)
        table = set(re.search(r'<table data-testid="ranked-table" class="([^"]*)"', body).group(1).split())
        assert {"base-table", "w-full"} <= table and "w-auto" not in table
        fill = re.findall(r'data-testid="detail-fill" class="([^"]*)"', body)
        assert fill and all({"w-0", "min-w-full"} <= set(f.split()) for f in fill)
        actions = re.findall(r'<div class="([^"]*)">\s*(?:<div data-testid="row-questions"|<form)', body)
        assert actions and all({"w-0", "min-w-full"} <= set(a.split()) for a in actions)

    def test_the_first_award_form_is_the_award_anchor(self, da, base, client_in_program):
        body, first, second = self._body(da, base, client_in_program)
        # Since batch 10 the anchor is the first awardable offer's ranked ROW (batch10 tests).
        assert body.count('id="award"') == 1
        ranked = re.findall(r'<tr data-testid="ranked-row" data-quote-id="(\d+)"', body)
        assert re.search(rf'<tr data-testid="ranked-row" data-quote-id="{ranked[0]}" id="award"', body)


# The walkthrough's hooks stay where they were.
@pytest.mark.django_db
def test_the_hooks_stay(da, base, home_client, client_in_program):
    judged7._provisional(da, base)
    home = _home(home_client)
    for hook in ("overview-row", "stale-flag", "waiting-line", "award-price", "provisional-caveat", "award-why-row"):
        assert f'data-testid="{hook}"' in home
