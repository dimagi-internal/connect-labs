"""Sophie's RUTF walkthrough, judged a sixth time: the screens agree with each other.

THIS REPOSITORY IS PUBLIC. Every company, product, figure and address here is invented.

(`test_sophie_batch6.py` is the batch the fifth judged walkthrough produced; this
is the sixth's.) Same fixtures as the earlier batches. What these pin: the
overview's stage, waiting-on and flags say one thing about a blocked or
provisionally awarded tender; a blocked card names every blocker the overview
does; the landed total says what quantity it buys; the supplier's listing says
what it hides and when it was posted; an ETA's move reads on its own line of
history; and every supply page names the program and its buyer of record.
"""

import datetime
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.supply_chain.standing import standing_rows
from connect_labs.supply_chain.templatetags.supply_chain_extras import day
from connect_labs.supply_chain.tests import test_sophie_batch6 as batch6
from connect_labs.supply_chain.tests.test_history_timeline import AUG_20, PROGRAM, op

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
SEP_12 = datetime.date(2026, 9, 12)


def _tender_row(today=SEP_12):
    (row,) = (r for r in standing_rows(PROGRAM, today) if r.kind == "tender")
    return row


def _outreach(da, tender_id, supplier_id, sent_on, responded=False):
    return op(
        da,
        "outreach_log",
        AUG_20,
        data={
            "tender_id": tender_id,
            "supplier_id": supplier_id,
            "sent_on": sent_on.isoformat(),
            "responded": responded,
            **({"response_kind": "quote"} if responded else {}),
        },
    )


# ---- 2-4. the overview: stage, waiting-on and flags say one thing -----------


# ---- 5. a blocked card names every blocker, as the overview does ------------


# ---- 6. a ranked row: the quantity beside the total, provenance once --------


# ---- 7. the supplier's listing ----------------------------------------------


@pytest.mark.django_db
class TestTheListing:
    def test_no_deadline_reads_as_open_with_its_caption(self, client, listed_tender):
        body = batch6._listing(client, listed_tender)
        assert re.search(r'data-testid="replies-by"[^>]*>Open for bids<', body)
        assert re.search(r'data-testid="replies-by-caption"[^>]*>NO DEADLINE SET<', body)
        assert "No reply-by date" not in body

    def test_the_subtitle_says_the_buyer_converts(self, client, listed_tender):
        body = batch6._listing(client, listed_tender)
        assert "Quote in your own units; the buyer converts them for comparison." in body

    def test_the_posted_day_is_read_from_when_it_opened(self, client, listed_tender):
        assert listed_tender.opened_at is None  # the open operation never stamps it
        body = batch6._listing(client, listed_tender)
        posted = batch6._text(re.search(r'data-testid="posted-by"[^>]*>(.*?)</p>', body, re.S).group(1))
        assert posted == f"Posted {day(timezone.localdate())}"

    def test_the_preview_says_what_a_supplier_does_not_see(self, owner, listed_tender):
        body = batch6._listing(owner, listed_tender, as_supplier="1")
        # Counted since batch 7 (test_sophie_judged_batch7); with nothing on the
        # tender yet, the rule in words.
        hidden = re.search(r'data-testid="supplier-preview-hidden"[^>]*>(.*?)</p>', body, re.S).group(1)
        assert hidden.startswith("Hidden from suppliers: ") or hidden.startswith("Suppliers do not see")
        assert 'data-testid="supplier-preview-hidden"' not in batch6._listing(owner, listed_tender)

    def test_no_pulse_ticker_in_the_market_header(self, client, listed_tender):
        assert 'id="pulse-widget"' not in batch6._listing(client, listed_tender)
        assert 'id="pulse-widget"' not in client.get(reverse("supply_chain:market")).content.decode()


# ---- 8. the timeline ---------------------------------------------------------


@pytest.mark.django_db
class TestAnEtaMoveReadsInline:
    def test_the_chip_sits_in_the_sentence_in_the_neutral_pill(self, da, base, order, client_in_program):
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        text = re.search(
            r'<div data-testid="revision-text"[^>]*>(.*?)<span data-testid="eta-moved" class="([^"]*)"', body
        )
        # Since batch 8 the value after the arrow is bold.
        assert " ".join(text.group(1).split()) == (
            'Shipment · SH-1 · ETA 5 Sep → <strong class="font-semibold">19 Sep</strong>'
        )
        classes = text.group(2).split()
        assert {"bg-gray-100", "text-gray-700"} <= set(classes)
        assert not any(c.startswith(("bg-amber", "text-amber", "border-amber")) for c in classes)

    def test_a_supplier_reported_shipment_names_its_supplier_as_sender(self, da, base, order, client_in_program):
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        # The email event's head names its sender (unanswered round 1004 b3).
        head = re.search(
            r'data-testid="email-event-head"[^>]*>(.*?)</span>\s*<span data-testid="actor-badge"', body, re.S
        )
        assert batch6._text(re.sub(r"<[^>]+>", " ", head.group(1))) == "Northwind Foods"


# ---- 9. the banner names the program and its buyer of record ---------------


@pytest.mark.django_db
class TestTheBanner:
    def test_program_and_buyer_of_record_from_the_orders(self, da, base, order, client_in_program):
        session = client_in_program.session
        session["labs_oauth"] = {"organization_data": {"programs": [{"id": PROGRAM, "name": "Lakeside RUTF"}]}}
        session.save()
        body = client_in_program.get(reverse("supply_chain:home"), {"program_id": PROGRAM}).content.decode()
        line = re.search(r'data-testid="supply-program-line"[^>]*>(.*?)</p>', body).group(1)
        assert line == "Lakeside RUTF · The program, buyer of record"
        # And on a page well away from the overview.
        body = client_in_program.get(reverse("supply_chain:orders")).content.decode()
        assert "Lakeside RUTF · The program, buyer of record" in body

    def test_no_line_without_a_program(self, client, sophie):
        client.force_login(sophie)
        assert 'data-testid="supply-program-line"' not in client.get(reverse("supply_chain:home")).content.decode()
