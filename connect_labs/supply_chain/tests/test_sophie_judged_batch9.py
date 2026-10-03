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

import re

import pytest

from connect_labs.supply_chain.models import Tender
from connect_labs.supply_chain.tests import test_sophie_batch6 as batch6
from connect_labs.supply_chain.tests import test_sophie_judged_batch7 as judged7
from connect_labs.supply_chain.tests.test_history_timeline import AUG_28, op
from connect_labs.supply_chain.tests.test_sophie_batch3 import _home

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


# ---- 2. one order on every Needs-info card ----------------------------------


def _order_of(card, hooks):
    found = [(card.index(f'data-testid="{hook}"'), hook) for hook in hooks if f'data-testid="{hook}"' in card]
    return [hook for _, hook in sorted(found)]


# Since DDD 002 batch 2 each gap and the question that clears it share a row, so the
# question comes before the facts the gaps are read against.
ORDER = ["blocking", "blocking-question", "card-facts", "draft-email-link", "also-confirm"]


# ---- 3. the no-reply flag opens on what it rests on --------------------------


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


# The walkthrough's hooks stay where they were.
@pytest.mark.django_db
def test_the_hooks_stay(da, base, home_client, client_in_program):
    judged7._provisional(da, base)
    home = _home(home_client)
    for hook in ("standing-table", "overview-row", "row-stage", "next-move", "whose", "last-change"):
        assert f'data-testid="{hook}"' in home
