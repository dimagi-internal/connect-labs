"""History in the page's words: one email reads as one event, a record line names its kind.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on rehearsal_world.
"""

import datetime
import html
import re

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.history.labels import _required_documents_clause, quote_commercial_terms, sentence
from connect_labs.supply_chain.history.timeline import Entry, _drop_own_day, email_events
from connect_labs.supply_chain.models import Tender
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.templatetags.supply_chain_extras import dot_parts, record_kind_lead
from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import (
    FORWARDER,
    PROGRAM,
    _chase,
    _contract,
    _held_by_the_forwarders_email,
    _held_on_our_form_m,
    _kanem_quote,
    _order,
    _reply_today,
    _tender_page,
    _text,
    op,
)

# The world's fixtures, shared rather than copied.
da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program
web = rehearsal_world.web


class TestHistoryWording:
    def test_a_chase_reads_as_last_chased(self, da, world, client_in_program):
        outreach_id = world["outreach"]["id"]
        _chase(da, outreach_id, "2026-09-23", datetime.datetime(2026, 9, 23, 9, tzinfo=datetime.UTC))
        _chase(da, outreach_id, "2026-10-02", datetime.datetime(2026, 10, 2, 9, tzinfo=datetime.UTC))
        body = _tender_page(client_in_program, world["tender"]["id"])
        assert "Last chased 23 Sep → 2 Oct" in re.sub(r"<[^>]+>", "", body)
        assert "Last reminder on" not in body

    def test_a_quote_on_an_incoterm_says_the_term_and_what_it_includes(self, da, world, client_in_program):
        op(
            da,
            "quote_record",
            data=_kanem_quote(world, as_quoted_amount="55.00", incoterm="DDP Kano", duties_basis="included"),
        )
        text = re.sub(r"<[^>]+>", "", _tender_page(client_in_program, world["tender"]["id"]))
        assert "USD 55.00 per carton (DDP Kano: freight and duty included)" in text

    def test_a_quote_with_no_incoterm_reads_as_before(self, da, world, client_in_program):
        op(da, "quote_record", data=_kanem_quote(world))
        text = re.sub(r"<[^>]+>", "", _tender_page(client_in_program, world["tender"]["id"]))
        assert "USD 54.50 per carton (freight included)" in text


class TestTheHistoryLine:
    def test_the_source_has_its_own_toggle_and_the_badge_is_a_label(self, da, world, client_in_program):
        op(
            da,
            "quote_record",
            source={"ref": "<q-1@kanem.example.invalid>", "excerpt": "USD 54.50 per carton, CPT Kano."},
            data=_kanem_quote(world),
        )
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        # Since unanswered round 1004 b3 an AI-recorded email is always an email event: the
        # badge in its head, the toggle its own, the quote a record under it.
        event = timeline.split('data-testid="email-event"', 1)[1]
        assert "Quote" in event and 'data-testid="revision-line"' in event
        assert re.search(r'<span data-testid="actor-badge" data-ai ', event)
        toggle = re.search(r'<summary data-testid="source-toggle".*?</summary>', event, re.S).group(0)
        assert "Source email" in toggle and "actor-badge" not in toggle
        assert 'data-testid="source-excerpt"' in event and 'data-testid="source-heading"' not in event

    def test_an_invitation_sent_and_a_chase_are_set_back(self, da, world, client_in_program):
        with seed_overrides(PROGRAM, recorded_at=datetime.datetime(2026, 10, 1, 10, tzinfo=datetime.UTC)):
            call_operation(
                "outreach_update",
                da,
                {"outreach_id": world["outreach"]["id"], "data": {"last_reminder_on": "2026-10-01"}},
                channel="web",
            )
        op(da, "quote_record", data=_kanem_quote(world))
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        texts = re.findall(r'<div data-testid="revision-text"([^>]*)>(.*?)</div>', timeline, re.S)
        kept = {_text(t): attrs for attrs, t in texts}
        sent = next(attrs for text, attrs in kept.items() if text.startswith("Invitation") or " sent " in text)
        chased = next(attrs for text, attrs in kept.items() if "Last chased" in text)
        quote = next(attrs for text, attrs in kept.items() if text.startswith("Quote"))
        assert "data-bookkeeping" in sent and "text-xs" in sent
        assert "data-bookkeeping" in chased
        assert "data-bookkeeping" not in quote and "text-sm" in quote

    def test_a_voided_ai_quote_says_why_it_offers_no_fix(self, da, world, client_in_program):
        quote = op(da, "quote_record", data=_kanem_quote(world))
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        fixes = re.search(r'data-testid="quote-fixes"[^>]*>(.*?)</div>', timeline, re.S).group(1)
        assert "Correct" in fixes and "Void" in fixes
        op(da, "quote_void", quote_id=quote["id"], reason="sent twice")
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        fixes = re.findall(r'data-testid="quote-fixes"[^>]*>(.*?)</div>', timeline, re.S)
        assert fixes and all(_text(f) == "voided" for f in fixes)


class TestTheHoldInTheHistory:
    def test_the_line_that_asked_for_our_document_says_we_are_waited_on(self, da, world, client_in_program):
        contract, _ = _held_on_our_form_m(da, world)
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        holds = re.findall(r'data-testid="revision-hold"[^>]*>([^<]*)<', body)
        assert holds == ["Waiting on us: import permit"]

    def test_once_it_is_on_file_the_marker_goes(self, da, world, client_in_program):
        from connect_labs.supply_chain.models import Shipment

        contract, shipment = _held_on_our_form_m(da, world)
        op(
            da,
            "document_attach",
            channel="web",
            data={
                "shipment_id": shipment["id"],
                "kind": "import_permit",
                "title": "Form M",
                "external_url": "https://example.invalid/form-m.pdf",
                "source": "we_recorded",
            },
        )
        assert Shipment.objects.get(pk=shipment["id"]).documents.exists()
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert 'data-testid="revision-hold"' not in body


REPLY_REF = "<reply-1@kanem.example.invalid>"


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

    def test_a_lone_email_is_an_event_of_one(self, da, world, client_in_program):
        # Since unanswered round 1004 b3 every email reads in the one event shape, even when
        # one record came of it; the toggle is the event's.
        op(
            da,
            "quote_record",
            source={"ref": REPLY_REF, "excerpt": "USD 54.50 a carton."},
            data=_kanem_quote(world),
        )
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        assert timeline.count('data-testid="email-event"') == 1
        event = timeline.split('data-testid="email-event"', 1)[1]
        assert 'data-testid="source-toggle"' in event


def test_a_payment_an_invoice_acknowledges_reads_as_linked(da, world, client_in_program):
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


def test_a_reply_recorded_the_day_it_came_in_does_not_repeat_its_day():
    when = timezone.make_aware(datetime.datetime(2026, 10, 4, 10, 0))
    day = date_format(timezone.localtime(when), "j M Y")
    entry = Entry(
        when=when,
        sentence=f"Replied with a quote on {day}",
        actor="ACE (agent)",
        is_ai=True,
        excerpt="",
        source_ref="",
        entity="Outreach",
        identity="Sahel Co",
        what=f"Replied with a quote on {day}",
    )
    _drop_own_day(entry)
    assert entry.what == "Replied with a quote" and entry.sentence == "Replied with a quote"
    later = Entry(
        when=when,
        sentence="Replied with a quote on 1 Oct 2026",
        actor="Sophie",
        is_ai=False,
        excerpt="",
        source_ref="",
        what="Replied with a quote on 1 Oct 2026",
    )
    _drop_own_day(later)
    # Recorded on a later day, the day it came in is the line's to say.
    assert later.what == "Replied with a quote on 1 Oct 2026"


@pytest.mark.django_db
class TestTheEmailEventHead:
    def _event(self, client, world):
        timeline = _tender_page(client, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        return re.search(r'<li data-testid="email-event".*?</ol>\s*</li>', timeline, re.S).group(0)

    def test_one_head_line_and_no_preamble(self, da, world, client_in_program):
        _reply_today(da, world)
        event = self._event(client_in_program, world)
        head = event.split("<details", 1)[0]
        text = _text(head)
        assert "Email forwarded" not in text and "recorded by" not in text and "Email from" not in text
        assert 'data-testid="email-event-head"' in head and 'data-testid="actor-badge"' in head
        # No date in the head: the day heading above it carries it.
        assert not re.search(r"\d{1,2} [A-Z][a-z]{2} \d{4}", text)

    def test_the_reply_line_does_not_say_its_day_again(self, da, world, client_in_program):
        _reply_today(da, world)
        event = self._event(client_in_program, world)
        lines = [_text(li) for li in re.findall(r'<li data-testid="revision-line".*?</li>', event, re.S)]
        assert any("Replied with a quote" in line for line in lines)
        assert not any(re.search(r"Replied with a quote on ", line) for line in lines)

    def test_the_mail_domain_opens_with_the_email(self, da, world, client_in_program):
        _reply_today(da, world)
        event = self._event(client_in_program, world)
        details = re.search(r"<details.*?</details>", event, re.S).group(0)
        assert 'data-testid="email-arrival"' in details and "mail.kanem.example.invalid" in details


class TestOneShapeForEveryEmail:
    def test_a_lone_email_line_is_an_event_of_one_and_a_document_stays_a_line(self):
        email = Entry(
            when=None, sentence="x", actor="ACE (agent)", is_ai=True, excerpt="HELD", source_ref="<a@b.invalid>"
        )
        email.source_kind = "Email"
        document = Entry(when=None, sentence="y", actor="Sophie", is_ai=False, excerpt="p. 3", source_ref="scan-7")
        document.source_kind = "Document"
        folded = email_events([email, document])
        assert folded[0].is_group and folded[0].members == [email]
        assert folded[1] is document

    def test_the_forwarders_email_reads_as_an_event_on_the_order(self, da, world, client_in_program):
        contract = _held_by_the_forwarders_email(da, world)
        body = _order(client_in_program, contract["id"])
        history = body[body.index('id="history"') :]
        event = next(
            e for e in re.findall(r'<li data-testid="email-event".*?</ol>\s*</li>', history, re.S) if "Status" in e
        )
        head = re.search(
            r'data-testid="email-event-head"[^>]*>(.*?)</span>\s*<span data-testid="actor-badge"', event, re.S
        )
        assert html.unescape(_text(head.group(1))) == FORWARDER
        assert 'data-testid="source-toggle"' in event and "CONSIGNEE TO PROVIDE FORM M" in event
        # No "Email from ..., recorded by the AI assistant on ..." line: the head says it.
        assert "Email from" not in history and 'data-testid="source-heading"' not in history
        hold = re.search(r'data-testid="revision-hold"[^>]*>(.*?)</div>', event, re.S).group(1)
        assert _text(hold).startswith("Waiting on us: import permit (Form M) and customs declaration (PAAR)")
        reason = re.search(r'<span data-testid="hold-reason" class="([^"]*)" title="([^"]+)">([^<]*)<', hold)
        assert reason.group(3) == "consignee: us" and "status-chip" in reason.group(1).split()
        assert "we are the consignee" not in body


def test_the_duty_terms_history_line_reads_as_a_sentence():
    line = sentence(
        Tender, "update", {"duty_terms": ["", "buyer_waiver"], "duty_terms_set_on": [None, "2026-10-03"]}, None
    )
    assert line == "Set the tender's import duties: we import under the duty waiver"


def test_a_record_line_under_an_email_sets_its_kind_apart():
    out = str(record_kind_lead("Outreach · Sahel & Co · Replied with a quote on 3 Oct 2026"))
    assert out.startswith('<span data-testid="record-kind" class="font-semibold">Outreach</span> · ')
    assert "Sahel &amp; Co" in out
    assert str(record_kind_lead("Quote recorded: 42.50 USD")) == "Quote recorded: 42.50 USD"


def test_the_quote_line_carries_its_commercial_terms():
    words = quote_commercial_terms(
        {
            "validity_until": "2026-11-02",
            "lead_time_days": 35,
            "moq": 500,
            "moq_unit": "carton",
            "shelf_life_months_stated": 24,
        }
    )
    assert words == "valid to 2 Nov 2026 · lead time 5 weeks · minimum order 500 cartons · shelf life 24 months"
    assert quote_commercial_terms({}) == ""
    assert quote_commercial_terms({"lead_time_days": 10}) == "lead time 10 days"


def test_a_line_under_an_email_does_not_repeat_its_sender():
    line = "Quote · Sahel Nutrition Industries · recorded: EUR 0.31 per sachet"
    html = str(record_kind_lead(line, "Amadou Issoufou, Sahel Nutrition Industries"))
    assert "Sahel Nutrition Industries" not in html and "EUR 0.31 per sachet" in html
    assert "Sahel Nutrition Industries" in str(record_kind_lead(line, "Someone, Other Co"))


def test_a_recorded_quote_leads_with_its_price_then_lists_its_terms():
    line = (
        "Quote · Sahel Co · recorded: EUR 0.31 per sachet (EXW Niamey: freight and duty excluded)"
        " — you asked CPT Kano · 150 x 92 g sachets per carton · valid to 2 Nov 2026"
        " · lead time 5 weeks · minimum order 500 cartons · shelf life 24 months"
    )
    html = str(record_kind_lead(line, "A Person, Sahel Co"))
    assert "recorded:" not in html
    price = re.search(r'<span data-testid="quote-price"[^>]*>(.*?)</span>', html, re.S).group(1)
    assert price == "EUR 0.31 per sachet"
    terms = [_text(c) for c in re.findall(r'<span data-testid="quote-term".*?</span></span>', html, re.S)]
    assert terms == [
        "Incoterm EXW Niamey: freight and duty excluded — you asked CPT Kano",
        "Pack 150 x 92 g sachets per carton",
        "Valid to 2 Nov 2026",
        "Lead time 5 weeks",
        "Minimum order 500 cartons",
        "Shelf life 24 months",
    ]


def test_an_owed_line_does_not_stack_colons():
    html = str(record_kind_lead("Owed · recorded: they asked: Who imports?", "X, Sahel Co"))
    assert _text(html) == "Owed · they asked: Who imports?"


def test_other_record_lines_are_not_chipped():
    html = str(record_kind_lead("Outreach · Sahel Co · Replied with a quote on 3 Oct 2026", "X, Sahel Co"))
    assert 'data-testid="quote-term"' not in html


def test_required_documents_are_named():
    new = [
        {"kind": "import_permit", "name": "Form M"},
        {"kind": "customs_declaration", "name": "PAAR"},
    ]
    assert (
        _required_documents_clause([], new)
        == "Required documents now: import permit (Form M), customs declaration (PAAR)"
    )
    assert _required_documents_clause(new, []) == "Required documents cleared"


def test_a_quote_card_lists_its_terms_in_one_fixed_order_and_marks_what_was_not_stated():
    html = str(
        record_kind_lead("Quote · Kanem · recorded: USD 55.00 per carton (DDP Kano) · lead time 6 weeks", "X, Kanem")
    )
    terms = [_text(c) for c in re.findall(r'<span data-testid="quote-term".*?</span></span>', html, re.S)]
    assert terms == [
        "Incoterm DDP Kano",
        "Pack not stated",
        "Valid to not stated",
        "Lead time 6 weeks",
        "Minimum order not stated",
        "Shelf life not stated",
    ]


def test_dot_parts_splits_on_the_separator():
    assert dot_parts("USD 50.10 / carton · CPT Kano") == ["USD 50.10 / carton", "CPT Kano"]
    assert dot_parts("") == []
