"""The unanswered round, judged batch 4.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented,
and every address uses `.example.invalid`.

Builds on test_tracking_reality's world: one open RUTF tender (2,000 cartons),
Kanem invited on 6 Jul, Northgate a second supplier.
"""

import re

from django.urls import reverse

from connect_labs.supply_chain.models import Quote
from connect_labs.supply_chain.procurement.services.pricing import compute_figures
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import _contract, _kanem_quote, op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page
from connect_labs.supply_chain.tests.test_unanswered_round_batch2 import _comparable

# That module's fixtures, shared rather than copied.
da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _row(body, outreach_id):
    return re.search(rf'<tr data-outreach-id="{outreach_id}".*?</tr>', body, re.S).group(0)


def _order(client, contract_id):
    return client.get(reverse("supply_chain:order_detail", args=[contract_id])).content.decode()


# ---- 1-3. the outreach table


class TestTheOutreachTable:
    def test_the_heading_counts_who_replied_by_answer_or_quote(self, da, world, client_in_program):
        tender_id = world["tender"]["id"]
        op(
            da,
            "outreach_log",
            data={"tender_id": tender_id, "supplier_id": world["northgate"]["id"], "sent_on": "2026-07-06"},
        )
        count = re.search(
            r'data-testid="outreach-replied-count"[^>]*>([^<]*)<', _tender_page(client_in_program, tender_id)
        )
        assert count.group(1) == "0 of 2 answered"
        # A quote is a reply, though nobody ticked "replied".
        op(da, "quote_record", data=_kanem_quote(world))
        count = re.search(
            r'data-testid="outreach-replied-count"[^>]*>([^<]*)<', _tender_page(client_in_program, tender_id)
        )
        assert count.group(1) == "1 of 2 answered"

    def test_silence_reads_as_muted_words_and_reply_kinds_read_as_words(self, da, world, client_in_program):
        outreach_id = world["outreach"]["id"]
        silent = _row(_tender_page(client_in_program, world["tender"]["id"]), outreach_id)
        # Since 2026-10-04 one word for silence on the page: the State chip, "Silent Nd";
        # the Replied cell beside it is a dash, not a second "No reply · N days".
        assert re.search(r'data-testid="supplier-state">Silent \d+d<', silent)
        assert "No reply" not in silent
        op(
            da,
            "outreach_update",
            outreach_id=outreach_id,
            data={"responded": True, "response_kind": "needs_info", "responded_on": "2026-07-09"},
        )
        replied = _row(_tender_page(client_in_program, world["tender"]["id"]), outreach_id)
        # One whose-move vocabulary on the row: a reply that was questions reads as questions
        # for us, not "Needs info" -- the comparison's word for a quote missing facts (DDD 003 b4).
        assert re.search(r'data-testid="supplier-state">Questions for us<', replied)

    def test_delete_is_in_the_row_menu_and_the_reply_is_the_visible_action(self, da, world, client_in_program):
        row = _row(_tender_page(client_in_program, world["tender"]["id"]), world["outreach"]["id"])
        menu = re.search(r'<details data-testid="row-more".*?</details>', row, re.S).group(0)
        assert ">Delete</a>" in menu
        assert row.replace(menu, "").count(">Delete</a>") == 0
        # No reply recorded: recording one is offered -- as the visible action, or in the menu
        # when the visible action is the drafted reminder.
        assert 'data-testid="supplier-action"' in row and "Record a reply" in row


# ---- 4. the history: replies only, and an email event set apart


class TestTheHistory:
    def test_the_replies_only_toggle_and_what_it_hides(self, da, world, client_in_program):
        source = {"ref": "<r-1@kanem.example.invalid>", "excerpt": "USD 54.50 a carton.", "sender": "Grace"}
        op(
            da,
            "outreach_update",
            source=source,
            outreach_id=world["outreach"]["id"],
            data={"responded": True, "response_kind": "quote", "responded_on": "2026-07-09"},
        )
        op(da, "quote_record", source=source, data=_kanem_quote(world))
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        toggle = re.search(r'<input type="checkbox" data-testid="history-replies-only"[^>]*>', timeline, re.S).group(0)
        assert "checked" not in toggle.split("onchange", 1)[0]  # off by default
        assert "[data-no-source]" in toggle
        # Lines no email stands behind carry the mark the toggle hides by; the email does not.
        event_html = re.search(r'<li data-testid="email-event".*?</ol>\s*</li>', timeline, re.S).group(0)
        loose = re.findall(r'<li data-testid="revision-line"[^>]*>', timeline.replace(event_html, ""))
        assert loose and all("data-no-source" in li for li in loose)
        event = re.search(r'<li data-testid="email-event"[^>]*>', timeline).group(0)
        assert "data-no-source" not in event and "border-l-2" in event

    def test_lines_inside_an_email_event_are_not_hidden_with_the_bookkeeping(self, da, world, client_in_program):
        source = {"ref": "<r-2@kanem.example.invalid>", "excerpt": "USD 54.50 a carton.", "sender": "Grace"}
        op(
            da,
            "outreach_update",
            source=source,
            outreach_id=world["outreach"]["id"],
            data={"responded": True, "response_kind": "quote", "responded_on": "2026-07-09"},
        )
        op(da, "quote_record", source=source, data=_kanem_quote(world))
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        event = re.search(r'<li data-testid="email-event".*?</ol>\s*</li>', timeline, re.S).group(0)
        assert "data-no-source" not in event


# ---- 5. the comparison


class TestTheComparison:
    def _page(self, client, world):
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        return client.get(url).content.decode()

    def test_the_award_fields_wait_behind_the_award_button(self, da, world, client_in_program):
        op(da, "quote_record", data=_comparable(world))
        body = self._page(client_in_program, world)
        start = re.search(r'<details [^>]*data-testid="award-start"[^>]*>(.*?)</details>', body, re.S)
        assert start is not None and " open" not in start.group(0).split(">", 1)[0]
        inner = start.group(1)
        assert re.search(r'<summary data-testid="award-open"[^>]*>Award Kanem[^<]*</summary>', inner)
        form = re.search(r'<form [^>]*data-testid="award-form".*?</form>', inner, re.S).group(0)
        assert 'name="rationale"' in form and 'data-testid="award-submit"' in form

    def test_a_link_to_the_award_step_opens_the_fields(self, da, world, client_in_program):
        op(da, "quote_record", data=_comparable(world))
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]])
        body = client_in_program.get(url + "?commodity=rutf&step=award").content.decode()
        tag = re.search(r'<details [^>]*data-testid="award-start"[^>]*>', body).group(0)
        assert " open" in tag


# ---- 6. what we owe: by supplier, one pill


class TestWhatWeOweBySupplier:
    def _ask(self, da, world, text, raised):
        return op(
            da,
            "commitment_record",
            data={
                "kind": "question",
                "supplier_id": world["northgate"]["id"],
                "tender_id": world["tender"]["id"],
                "text": text,
                "raised_on": raised,
                "source": "supplier_reported",
            },
        )

    def test_one_header_per_supplier(self, da, world, client_in_program):
        for text, raised in (("Importer?", "2026-09-18"), ("Labels?", "2026-09-19"), ("Warehouse?", "2026-09-20")):
            self._ask(da, world, text, raised)
        body = _tender_page(client_in_program, world["tender"]["id"])
        heads = re.findall(r'data-testid="owed-group-head"[^>]*>(.*?)</div>', body, re.S)
        assert [_text(h) for h in heads] == ["Northgate Rehearsal Commodities · asked 18 Sep 2026 · 3 questions"]
        owed = body.split('data-testid="owed"', 1)[1].split('id="drafts"', 1)[0]
        assert owed.count("Northgate Rehearsal Commodities") == 1

    def test_the_just_answered_row_wears_one_pill(self, da, world, client_in_program):
        asked = self._ask(da, world, "Importer?", "2026-09-18")
        op(da, "commitment_resolve", channel="web", commitment_id=asked["id"], resolution="We are.")
        body = _tender_page(client_in_program, world["tender"]["id"], f"?changed=commitment-{asked['id']}")
        row = re.search(
            rf'<div [^>]*data-commitment-id="{asked["id"]}".*?data-testid="owed-answer"', body, re.S
        ).group(0)
        # One line since DDD 002 batch 1: "Answered by Sophie · 2 Oct 2026 (just now): We are."
        answer = re.search(
            r'data-testid="owed-answer".*?</div>', body[body.index(f'data-commitment-id="{asked["id"]}"') :], re.S
        ).group(0)
        # Since DDD v11 an answer written into a reply not yet sent reads as drafted.
        assert re.search(r'data-testid="owed-status"[^>]*>Answer drafted<', answer)
        assert "(just now): We are." in re.sub(r"<[^>]+>", "", answer)
        assert 'data-testid="changed-chip"' not in row


# ---- 7. the advance is a row of the invoices table


def test_an_advance_is_its_own_invoice_table_row(da, world, client_in_program):
    contract = _contract(da, world, payment_terms="advance")
    paid = op(
        da,
        "payment_record",
        channel="web",
        data={"contract_id": contract["id"], "paid_on": "2026-07-28", "amount": "53400.00", "source": "we_recorded"},
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
    body = _order(client_in_program, contract["id"])
    row = re.search(r'<tr data-testid="advance-row">(.*?)</tr>', body, re.S).group(1)
    cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
    assert cells[:5] == ["Advance paid", "28 Jul 2026", "USD 53,400.00", "—", "applied to INV-HT-26-0912"]
    assert "text-xs" not in row.split("</td>", 1)[0]
    # Not repeated as a footnote under the invoice.
    assert "(before this invoice)" not in body


# ---- 9. duty relief with nothing on file


class TestDutyRelief:
    def test_a_waived_duty_with_no_document_is_conditional(self, da, world, client_in_program):
        contract = _contract(da, world, duties_basis="excluded", duties_amount="0.00")
        body = _order(client_in_program, contract["id"])
        line = re.search(r'data-testid="duty-relief-unevidenced"[^>]*>(.*?)</span>\s*</td>', body, re.S)
        assert line is not None
        assert _text(line.group(1)) == "USD 0.00 if duty relief is documented (no document on file)"

    def test_the_rule_is_the_costing_s_own(self, da, world):
        """The order reads the one predicate (pricing.relief_unevidenced) off contract_landed_cost."""
        from connect_labs.supply_chain.fulfilment.services import landed
        from connect_labs.supply_chain.models import Contract

        contract = _contract(da, world, duties_basis="excluded", duties_amount="0.00")
        assert landed.relief_unevidenced(Contract.objects.get(pk=contract["id"]))
        included = _contract(da, world, duties_basis="included")
        assert not landed.relief_unevidenced(Contract.objects.get(pk=included["id"]))


# ---- 10. part paid is not unpaid


def test_the_chain_counts_part_paid_apart():
    from connect_labs.supply_chain.templatetags.supply_chain_extras import _invoiced_note

    assert _invoiced_note({"count": 1, "unpaid": 0, "part_paid": 1}) == "1 part paid"
    assert _invoiced_note({"count": 3, "unpaid": 2, "part_paid": 1}) == "2 unpaid · 1 part paid"


def test_the_summary_does_not_count_a_part_paid_invoice_as_unpaid(da, world):
    from connect_labs.supply_chain.models import Invoice
    from connect_labs.supply_chain.summary import _order

    contract = _contract(da, world)
    invoice = op(
        da,
        "invoice_record",
        data={"contract_id": contract["id"], "reference": "INV-1", "amount": "100.00", "source": "supplier_reported"},
    )
    Invoice.objects.filter(pk=invoice["id"]).update(status="part_paid")
    invoiced = _order(da)["invoiced"]
    assert (invoiced["unpaid"], invoiced["part_paid"]) == (0, 1)


# ---- batch 4, item 6 (rehearsal finding 5): the quantity check uses the stated pack


class TestQuantityThroughTheStatedPack:
    def _quote(self, da, world, sachets):
        made = op(
            da,
            "quote_record",
            data={
                **{k: v for k, v in _comparable(world).items() if not k.startswith("quantity_basis")},
                "quantity_basis": sachets,
                "quantity_basis_unit": "sachet",
            },
        )
        return Quote.objects.get(pk=made["id"])

    def _figures(self, quote):
        return compute_figures(quote, quote.commodity, quote.tender, quote.item)

    def test_300000_sachets_at_150_a_carton_is_the_tenders_2000_cartons(self, da, world):
        figures = self._figures(self._quote(da, world, 300000))
        assert figures.landed_total_for_tender_quantity == figures.landed_total_as_quoted
        assert not any("tender is" in r for r in getattr(figures.landed_total_for_tender_quantity, "reasons", ()))

    def test_299850_sachets_is_not(self, da, world):
        figures = self._figures(self._quote(da, world, 299850))
        reasons = getattr(figures.landed_total_for_tender_quantity, "reasons", ())
        assert any("tender is 2,000 cartons" in r for r in reasons)
