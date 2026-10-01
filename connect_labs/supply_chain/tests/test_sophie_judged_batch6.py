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
from connect_labs.supply_chain.tests.test_history_timeline import AUG_20, AUG_28, PROGRAM, _quote_with, op
from connect_labs.supply_chain.tests.test_sophie_batch3 import _compare, _home, _page, _row

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


@pytest.mark.django_db
class TestAProvisionalAwardWaitsOnAnswers:
    WHY = "Complete and costed; the other two have not answered yet"

    def _award(self, da, base):
        blocked = batch6._supplier(da, "Sahel Nutrition")
        _quote_with(da, base["tender"]["id"], blocked["id"], AUG_20, {})  # no freight or duties: blocked
        silent = batch6._supplier(da, "Plateau Mills")
        _outreach(da, base["tender"]["id"], silent["id"], datetime.date(2026, 8, 10))  # never replied
        chosen = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        op(da, "award_create", AUG_28, tender_id=base["tender"]["id"], quote_id=chosen["id"], rationale=self.WHY)

    def test_waiting_on_names_the_blocked_then_the_silent(self, da, base):
        self._award(da, base)
        row = _tender_row()
        # Since batch 7: two kinds of answer owed, a line each, and the silent
        # supplier's flag stays up while "waiting on" names it.
        assert row.waiting_lines == ("No reply: Plateau Mills", "Missing facts: Sahel Nutrition")
        assert "a contract" not in row.waiting_on
        # The flag names the same blocked supplier, with what it is missing.
        assert row.stale == [
            "No reply in 33 days: Plateau Mills",
            "Can't compare yet: Sahel Nutrition — missing: sachets per carton, freight, duties",
        ]

    def test_provisional_is_said_once_and_the_why_reads_at_body_size(self, da, base, home_client):
        self._award(da, base)
        assert _tender_row().stage == "awarded to Northwind Foods"
        standing = batch6._standing(_home(home_client))
        cell = re.search(r'<td class="px-4 py-2.5">\s*awarded to Northwind Foods(.*?)</td>', standing, re.S).group(1)
        assert batch6._text(cell).count("provisional") == 1
        # Since batch 7 the why is a full-width row under the tender's, not in the cell.
        assert "award-why" not in cell
        why = re.search(r'<p data-testid="award-why" class="([^"]*)"><span[^>]*>Why:</span> (.*?)</p>', standing)
        assert why.group(2) == self.WHY
        assert "text-sm" in why.group(1).split() and "text-xs" not in why.group(1).split()


@pytest.mark.django_db
class TestABlockedOpenTenderWaitsOnAnswers:
    def test_not_on_an_award_decision_nobody_can_make(self, da, base):
        op(da, "tender_open", AUG_20, tender_id=base["tender"]["id"])
        _outreach(da, base["tender"]["id"], base["supplier"]["id"], datetime.date(2026, 8, 10), responded=True)
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {})
        row = _tender_row()
        assert row.waiting_on == "Missing facts: Northwind Foods"
        assert row.waiting_detail == "1 of 1 replied"
        assert row.stale[0].lines == ("Northwind Foods — missing: sachets per carton, freight, duties",)


# ---- 5. a blocked card names every blocker, as the overview does ------------


@pytest.mark.django_db
class TestABlockedCardShowsEveryBlocker:
    def test_freight_and_quantity_both_block_and_both_are_said(self, da, base, home_client, client_in_program):
        quote = _quote_with(
            da,
            base["tender"]["id"],
            base["supplier"]["id"],
            AUG_20,
            {**_DELIVERED, "freight_basis": "not_specified", "quantity_basis": "300"},
        )
        row = _row(_compare(da, base["tender"]["id"]), quote["id"])
        assert [b["label"] for b in row["blockers"]] == row["gaps"]
        assert len(row["blockers"]) == 2

        card = batch6._card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        blocking = [batch6._text(b) for b in re.findall(r'data-testid="blocking"[^>]*>(.*?)</p>', card, re.S)]
        assert len(blocking) == 2 and all(b.startswith("Blocking: ") for b in blocking)
        assert any("freight" in b.lower() for b in blocking)
        assert any("300 cartons" in b for b in blocking)
        # The overview names the same two gaps for the same quote.
        (line,) = _tender_row().stale[0].lines
        assert line == f"Northwind Foods — missing: {', '.join(row['gaps'])}"

    def test_one_folded_list_of_what_does_not_block(self, da, base, client_in_program):
        batch6._with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, batch6._ALL_BUT_PACK)
        card = batch6._card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        assert card.count("<details") == 1
        summary = re.search(r'data-testid="also-confirm".*?<summary[^>]*>(.*?)</summary>', card, re.S).group(1)
        assert " ".join(re.sub(r"<[^>]+>", "", summary).split()) == "Other things to confirm (not blocking)"
        assert "Also confirm (" not in card and "Also not stated" not in card


# ---- 6. a ranked row: the quantity beside the total, provenance once --------


@pytest.mark.django_db
class TestARankedRowDetail:
    def test_the_landed_total_says_what_quantity_it_buys(self, da, base, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        detail = batch6._detail(_page(client_in_program, base["tender"]["id"]), quote["id"])
        # 42.50 a carton for 600 cartons, freight and duties included.
        assert re.search(r'data-testid="landed-quantity"[^>]*>Landed total USD 25,500.00 for 600 cartons<', detail)

    def test_a_corrected_figure_is_sourced_once(self, da, base, ace, client_in_program):
        batch6._with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, batch6._ALL_BUT_PACK)
        corrected = batch6._correct_pack(da, quote, ace)
        detail = batch6._detail(_page(client_in_program, base["tender"]["id"]), corrected["id"])
        assert 'data-testid="correction-source"' in detail
        assert "Sachets per carton: from" not in detail


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
            r'<div data-testid="revision-text"[^>]*>([^<]*)<span data-testid="eta-moved" class="([^"]*)"', body
        )
        assert " ".join(text.group(1).split()) == "Shipment · SH-1 · ETA 5 Sep → 19 Sep"
        classes = text.group(2).split()
        assert {"bg-gray-100", "text-gray-700"} <= set(classes)
        assert not any(c.startswith(("bg-amber", "text-amber", "border-amber")) for c in classes)

    def test_a_supplier_reported_shipment_names_its_supplier_as_sender(self, da, base, order, client_in_program):
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        heading = batch6._text(re.search(r'data-testid="source-heading"[^>]*>(.*?)</p>', body, re.S).group(1))
        assert heading.startswith("Email from Northwind Foods, recorded by")


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
