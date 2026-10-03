"""The unanswered round, judged batch 3: the round says whose move it is, one email reads as one event.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented,
and every address uses `.example.invalid`.

Builds on test_tracking_reality's world: one open RUTF tender, Kanem invited
on 6 Jul, Northgate a second supplier.
"""

import datetime
import re

from django.urls import reverse

from connect_labs.supply_chain.standing import INVOICE_ABOVE_FLAG, standing_rows
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import (
    PROGRAM,
    _contract,
    _held_on_our_form_m,
    _kanem_quote,
    op,
)
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page
from connect_labs.supply_chain.tests.test_unanswered_round_batch2 import _comparable

# That module's fixtures, shared rather than copied.
da = reality.da
world = reality.world
client_in_program = reality.client_in_program

REPLY_REF = "<reply-1@kanem.example.invalid>"


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _row(body, outreach_id):
    return re.search(rf'<tr data-outreach-id="{outreach_id}".*?</tr>', body, re.S).group(0)


# ---- 1. a round with silent suppliers leads with chasing them


class TestASilentRound:
    def test_draft_reminders_leads_and_compare_steps_back(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        lead = re.search(r'<a data-testid="draft-reminders" href="#drafts"[^>]*class="([^"]*)"', body, re.S)
        assert lead is not None and "bg-brand-indigo" in lead.group(1)
        compare = re.search(r'<a data-testid="compare-quotes"[^>]*class="([^"]*)"', body, re.S).group(1)
        assert "bg-brand-indigo" not in compare and "border" in compare

    def test_once_everyone_has_replied_compare_leads_again(self, da, world, client_in_program):
        op(
            da,
            "outreach_update",
            outreach_id=world["outreach"]["id"],
            data={"responded": True, "response_kind": "quote", "responded_on": "2026-07-09"},
        )
        body = _tender_page(client_in_program, world["tender"]["id"])
        assert 'data-testid="draft-reminders"' not in body
        compare = re.search(r'<a data-testid="compare-quotes"[^>]*class="([^"]*)"', body, re.S).group(1)
        assert "bg-brand-indigo" in compare

    def test_a_silent_row_says_how_long_and_a_replied_row_offers_another_reply(self, da, world, client_in_program):
        outreach_id = world["outreach"]["id"]
        body = _tender_page(client_in_program, world["tender"]["id"])
        silent = _row(body, outreach_id)
        days = (datetime.date.today() - datetime.date(2026, 7, 6)).days
        assert re.search(rf'data-testid="no-reply"[^>]*>No reply · {days} days<', silent)
        assert ">No<" not in silent and "Record a reply" in silent
        assert "Days waiting" not in body
        for cell in ("replied", "last-chased"):
            assert "whitespace-nowrap" in re.search(rf'<td class="([^"]*)" data-testid="{cell}"', silent).group(1)

        op(
            da,
            "outreach_update",
            outreach_id=outreach_id,
            data={"responded": True, "response_kind": "needs_info", "responded_on": "2026-07-09"},
        )
        replied = _row(_tender_page(client_in_program, world["tender"]["id"]), outreach_id)
        assert (
            "Record a reply" in replied
            and "Record another reply" not in replied
            and 'data-testid="no-reply"' not in replied
        )

    def test_a_chase_does_not_restart_the_silence(self, da, world, client_in_program):
        # A reminder chases the same request; the silence still counts from the ask, as the overview does.
        three_days_ago = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
        op(da, "outreach_update", outreach_id=world["outreach"]["id"], data={"last_reminder_on": three_days_ago})
        row = _row(_tender_page(client_in_program, world["tender"]["id"]), world["outreach"]["id"])
        days = (datetime.date.today() - datetime.date(2026, 7, 6)).days
        assert re.search(rf'data-testid="no-reply"[^>]*>No reply · {days} days<', row)
        assert "No reply · 3 days" not in row

    def test_the_supply_banner_steps_back_on_the_tender(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        banner = re.search(r'<div data-testid="supply-banner" class="([^"]*)"', body).group(1)
        assert "py-2" in banner.split()
        assert "Source commodities, compare quotes on one basis" not in body


# ---- 2. a reply that was questions: the move is ours


def test_a_supplier_whose_questions_are_open_waits_on_us(da, world, client_in_program):
    from connect_labs.supply_chain.models import Supplier

    org = Supplier.objects.get(pk=world["kanem"]["id"]).org
    for raised, text in (("2026-09-18", "One warehouse?"), ("2026-09-20", "Importer?"), ("2026-09-21", "Labels?")):
        op(
            da,
            "commitment_record",
            data={
                "kind": "question",
                "owed_to_org_id": org.pk,
                "tender_id": world["tender"]["id"],
                "text": text,
                "raised_on": raised,
                "source": "supplier_reported",
            },
        )
    row = _row(_tender_page(client_in_program, world["tender"]["id"]), world["outreach"]["id"])
    said = re.search(r'data-testid="waiting-on-us-row"[^>]*>([^<]*)<', row).group(1)
    assert said == "waiting on us — 3 questions since 18 Sep"


# ---- 3. one email, one event


class TestOneEmailOneEvent:
    def _reply_with_quote(self, da, world):
        source = {"ref": REPLY_REF, "excerpt": "Our price is USD 54.50 a carton, CPT Kano.", "sender": "Grace"}
        op(
            da,
            "outreach_update",
            source=source,
            outreach_id=world["outreach"]["id"],
            data={"responded": True, "response_kind": "quote", "responded_on": "2026-07-09"},
        )
        op(da, "quote_record", source=source, data=_kanem_quote(world))

    def test_the_reply_and_its_quote_read_as_one_email(self, da, world, client_in_program):
        self._reply_with_quote(da, world)
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        events = re.findall(r'<li data-testid="email-event".*?</ol>\s*</li>', timeline, re.S)
        assert len(events) == 1
        event = events[0]
        assert event.count('data-testid="source-toggle"') == 1
        assert 'data-testid="source-excerpt"' in event
        # The head names sender and recorder once; no second line repeats it (DDD 002 batch 1).
        assert 'data-testid="source-heading"' not in event
        lines = re.findall(r'<li data-testid="revision-line".*?</li>', event, re.S)
        assert len(lines) == 2
        # The reply first, then what it carried.
        assert "Replied with a quote" in _text(lines[0]) and "Quote" in _text(lines[1])
        # Correct and Void on the quote's own line, not beside the excerpt.
        assert 'data-testid="quote-fixes"' in lines[1]
        assert "source-toggle" not in "".join(lines)
        # Nothing recorded from it is listed again outside the event.
        outside = timeline.replace(event, "")
        assert "Replied with a quote" not in outside

    def test_a_lone_line_with_a_source_stays_a_line(self, da, world, client_in_program):
        op(
            da,
            "quote_record",
            source={"ref": REPLY_REF, "excerpt": "USD 54.50 a carton."},
            data=_kanem_quote(world),
        )
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        assert 'data-testid="email-event"' not in timeline
        assert 'data-testid="source-toggle"' in timeline


# ---- 4. the comparison


class TestTheComparison:
    def _page(self, client, world):
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        return client.get(url).content.decode()

    def test_the_banner_states_what_is_missing_and_the_button_says_it(self, da, world, client_in_program):
        op(da, "quote_record", data=_comparable(world))
        op(
            da,
            "quote_record",
            data={
                **{k: v for k, v in _comparable(world).items() if k != "base_per_pack_stated"},
                "supplier_id": world["northgate"]["id"],
                "pack_spec_source": "not_stated",
            },
        )
        body = self._page(client_in_program, world)
        banner = _text(re.search(r'<div data-testid="comparison-banner"[^>]*>(.*?)</div>', body, re.S).group(1))
        assert banner.startswith("1 of 2 quotes can be compared on ")
        assert "Northgate Rehearsal Commodities sachets per carton" in banner
        for word in ("PROVISIONAL", "provisional", "beat"):
            assert word not in banner
        button = _text(
            re.search(r'<summary data-testid="award-open" data-anyway[^>]*>(.*?)</summary>', body, re.S).group(1)
        )
        assert button.startswith("Award Kanem ") and button.endswith(" anyway (1 quote still incomplete)")

    def test_decided_on_is_empty_until_an_award_is_started(self, da, world, client_in_program):
        op(da, "quote_record", data=_comparable(world))
        body = self._page(client_in_program, world)
        field = re.search(r'<input type="date" id="decided-on-\d+"[^>]*>', body).group(0)
        assert 'value=""' in field
        rationale = re.search(r'<input type="text" id="rationale-\d+"[^>]*>', body, re.S).group(0)
        assert "oninput=" in rationale


# ---- 5. the order page


class TestTheOrderPage:
    def test_a_document_hold_is_listed_as_something_we_owe(self, da, world, client_in_program):
        contract, _ = _held_on_our_form_m(da, world)
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        owed = re.search(r'data-testid="owed">(.*?)</div>\s*(?:<details|<div class="mb)', body, re.S).group(1)
        hold = _text(re.search(r'data-testid="owed-hold"[^>]*>(.*?)</div>', owed, re.S).group(1))
        # Since DDD 003 batch 8 the hold carries the action that clears it.
        assert hold == "Import permit — the shipment is held until we provide it Mark provided"
        assert "?kind=import_permit" in owed
        assert "Nothing owed" not in owed
        assert _text(re.search(r'<h3 id="owed".*?</h3>', body, re.S).group(0)) == (
            # Since DDD 005 batch 3 the section names who the document goes through.
            "What we owe — to clear the shipment (via Crescent Rehearsal Freight) — 1 open"
        )
        banner = re.search(r'<div data-testid="waiting-on-us" class="([^"]*)"', body).group(1)
        assert "text-gray-900" in banner.split() and "text-message-warning-text" not in banner.split()

    def test_the_unit_price_against_agreed_has_its_own_row(self, da, world, client_in_program):
        contract = _contract(da, world)
        op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "reference": "INV-REH-1",
                "issued_on": "2026-09-21",
                "currency": "USD",
                "amount": "110350.00",
                "unit_price": "51.20",
                "quantity_billed": 2000,
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        sub = re.search(r'<tr data-testid="invoice-against-agreed".*?</tr>', body, re.S).group(0)
        assert "Unit price per carton USD 51.20 USD 49.80 +USD 1.40" in _text(sub)
        assert 'data-testid="above-agreed-tag"' in sub
        amount_cell = re.search(r'data-testid="invoice-above-agreed"[^>]*>(.*?)</td>', body, re.S).group(1)
        assert "unit price" not in amount_cell

    def test_a_payment_an_invoice_acknowledges_reads_as_linked(self, da, world, client_in_program):
        contract = _contract(da, world, payment_terms="advance")
        paid = op(
            da,
            "payment_record",
            channel="web",
            data={
                "contract_id": contract["id"],
                "paid_on": "2026-07-28",
                "amount": "53400.00",
                "source": "we_recorded",
            },
        )
        op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "reference": "INV-HT-26-0912",
                "issued_on": "2026-09-21",
                "amount": "110350.00",
                "acknowledges_payment_ids": [paid["id"]],
                "source": "supplier_reported",
            },
        )
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        history = _text(body.split('data-testid="timeline"', 1)[1])
        assert "Payment of 28 Jul linked to INV-HT-26-0912" in history


# ---- 6. the overview


class TestTheOverview:
    def test_an_open_tender_past_its_deadline_says_so_in_its_stage(self, da, world):
        op(da, "tender_update", tender_id=world["tender"]["id"], data={"response_deadline": "2026-09-29"})
        row = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 10, 2)) if r.kind == "tender")
        assert row.stage == "open, deadline passed 29 Sep"
        before = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 9, 28)) if r.kind == "tender")
        assert before.stage == "open"

    def test_an_order_billed_above_agreed_is_flagged(self, da, world):
        contract = _contract(da, world)
        op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "reference": "INV-REH-1",
                "issued_on": "2026-09-21",
                "currency": "USD",
                "amount": "110350.00",
                "quantity_billed": 2000,
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )
        row = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 10, 2)) if r.kind == "order")
        assert INVOICE_ABOVE_FLAG in row.stale

    def test_an_order_billed_at_the_agreed_price_is_not(self, da, world):
        contract = _contract(da, world)
        op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "reference": "INV-REH-1",
                "issued_on": "2026-09-21",
                "currency": "USD",
                "amount": "106800.00",
                "quantity_billed": 2000,
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )
        row = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 10, 2)) if r.kind == "order")
        assert INVOICE_ABOVE_FLAG not in row.stale
