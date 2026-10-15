"""The tender page: its header and terms, the Suppliers table, and the row just saved.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on rehearsal_world.
"""

import datetime
import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import Commodity, Tender
from connect_labs.supply_chain.procurement.services.render import _product
from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import (
    _chase,
    _contract,
    _kanem_quote,
    _reply_today,
    _row,
    _set,
    _tender_page,
    _text,
    _waiver,
    op,
)

# The world's fixtures, shared rather than copied.
da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program
web = rehearsal_world.web


class TestBackToTheRowJustSaved:
    def test_saving_a_reply_returns_to_that_outreach_row_picked_out(self, da, world, web):
        outreach_id = world["outreach"]["id"]
        response = web.post(
            reverse("supply_chain:procurement_outreach_reply", args=[outreach_id]),
            {"last_reminder_on": "2026-07-13"},
        )
        tender_url = reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        assert response.status_code == 302
        assert response.url == f"{tender_url}?changed=outreach-{outreach_id}#outreach-{outreach_id}"

        body = _tender_page(web, world["tender"]["id"], f"?changed=outreach-{outreach_id}")
        row = re.search(rf'<tr data-outreach-id="{outreach_id}"[^>]*>', body).group(0)
        assert "data-changed" in row and "bg-yellow-50" in row
        # Only on arrival from the form.
        plain = _tender_page(web, world["tender"]["id"])
        assert "data-changed" not in re.search(rf'<tr data-outreach-id="{outreach_id}"[^>]*>', plain).group(0)

    def test_resolving_a_question_returns_to_what_we_owe_with_it_picked_out(self, da, world, web):
        asked = op(
            da,
            "commitment_record",
            data={
                "kind": "question",
                "supplier_id": world["northgate"]["id"],
                "tender_id": world["tender"]["id"],
                "text": "Who is the importer of record?",
                "raised_on": "2026-07-11",
                "source": "supplier_reported",
            },
        )
        response = web.post(
            reverse("supply_chain:commitment_resolve", args=[asked["id"]]),
            {"resolution": "We are.", "resolved_on": "2026-07-12"},
        )
        tender_url = reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        assert response.url == f"{tender_url}?changed=commitment-{asked['id']}#owed"
        body = _tender_page(web, world["tender"]["id"], f"?changed=commitment-{asked['id']}")
        row = re.search(rf'<div [^>]*data-commitment-id="{asked["id"]}"[^>]*>', body).group(0)
        assert "data-changed" in row

    def test_resolving_a_promise_on_an_order_returns_to_the_orders_owed(self, da, world, web):
        contract = _contract(da, world)
        promised = op(
            da,
            "commitment_record",
            data={
                "kind": "promise",
                "supplier_id": world["kanem"]["id"],
                "contract_id": contract["id"],
                "text": "Send the import permit.",
                "raised_on": "2026-08-01",
                "source": "we_recorded",
            },
        )
        response = web.post(
            reverse("supply_chain:commitment_resolve", args=[promised["id"]]),
            {"resolution": "Sent.", "resolved_on": "2026-08-03"},
        )
        order_url = reverse("supply_chain:order_detail", args=[contract["id"]])
        assert response.url == f"{order_url}?changed=commitment-{promised['id']}#owed"
        body = web.get(response.url.split("#")[0]).content.decode()
        assert 'id="owed"' in body
        row = re.search(rf'<div [^>]*data-commitment-id="{promised["id"]}"[^>]*>', body).group(0)
        assert "data-changed" in row


class TestTheTenderPage:
    def test_an_open_tender_past_its_deadline_says_so(self, da, world, client_in_program):
        three_days_ago = datetime.date.today() - datetime.timedelta(days=3)
        op(
            da,
            "tender_update",
            tender_id=world["tender"]["id"],
            data={"response_deadline": three_days_ago.isoformat()},
        )
        body = _tender_page(client_in_program, world["tender"]["id"])
        passed = re.search(r'data-testid="deadline-passed"[^>]*>(.*?)</span>', body, re.S).group(1)
        assert " ".join(passed.split()) == "3 days past"

    def test_a_deadline_still_ahead_reads_as_before(self, da, world, client_in_program):
        ahead = datetime.date.today() + datetime.timedelta(days=3)
        op(da, "tender_update", tender_id=world["tender"]["id"], data={"response_deadline": ahead.isoformat()})
        body = _tender_page(client_in_program, world["tender"]["id"])
        assert 'data-testid="deadline-passed"' not in body
        terms = body.split('data-testid="tender-terms"', 1)[1]
        assert "Deadline" in terms and f"{ahead.day} {ahead.strftime('%b')}" in terms

    def test_the_invite_select_starts_empty_and_leaves_out_who_was_already_asked(self, da, world, client_in_program):
        from connect_labs.labs.models import LabsOrg
        from connect_labs.supply_chain.models import Supplier, SupplierProfile

        kanem_org = Supplier.objects.get(pk=world["kanem"]["id"]).org
        northgate_org = Supplier.objects.get(pk=world["northgate"]["id"]).org
        for org in (kanem_org, northgate_org):
            SupplierProfile.objects.get_or_create(org=org)
        other = LabsOrg.objects.create(slug="sahel-reh", name="Sahel Rehearsal Nutrition")
        SupplierProfile.objects.get_or_create(org=other)

        body = _tender_page(client_in_program, world["tender"]["id"])
        select = re.search(r'<select id="invite-org".*?</select>', body, re.S).group(0)
        options = re.findall(r"<option[^>]*>([^<]*)</option>", select)
        assert options[0] == "Choose a supplier…"
        assert re.search(r'<option value="" selected>', select)
        # Kanem was asked on the outreach; Northgate was not.
        assert "Kanem Foods Rehearsal" not in options
        assert "Northgate Rehearsal Commodities" in options and "Sahel Rehearsal Nutrition" in options

    def test_last_chased_counts_the_reminders_from_the_history(self, da, world, client_in_program):
        outreach_id = world["outreach"]["id"]
        # Recorded after the world (1 Oct, 09:00), each on the day it was sent.
        _chase(da, outreach_id, "2026-10-01", datetime.datetime(2026, 10, 1, 10, tzinfo=datetime.UTC))
        _chase(da, outreach_id, "2026-10-02", datetime.datetime(2026, 10, 2, 8, tzinfo=datetime.UTC))
        body = _tender_page(client_in_program, world["tender"]["id"])
        cell = body.split('data-testid="supplier-chased"', 1)[1].split("</td>", 1)[0]
        assert "2 Oct" in cell and "2nd reminder" in cell

        # Read as of the first chase, it was the first.
        as_of = _tender_page(client_in_program, world["tender"]["id"], "?as_of=2026-10-01")
        cell = as_of.split('data-testid="supplier-chased"', 1)[1].split("</td>", 1)[0]
        assert "1 Oct" in cell and "1st reminder" in cell


class TestTheChangedRow:
    def test_the_saved_outreach_row_has_an_accent_bar_and_says_so(self, da, world, client_in_program):
        outreach_id = world["outreach"]["id"]
        body = _tender_page(client_in_program, world["tender"]["id"], f"?changed=outreach-{outreach_id}")
        row = re.search(rf'<tr data-outreach-id="{outreach_id}".*?</tr>', body, re.S).group(0)
        assert "border-l-4" in row and 'data-testid="changed-chip"' in row
        # The marker sits in the cell that changed (DDD 002 batch 1): a reply moves "Replied",
        # not beside the supplier's name.
        # The day a reply came in has its own Replied cell again (2026-10-05): the marker goes there.
        replied = re.search(r'<td [^>]*data-testid="replied-on"[^>]*>.*?</td>', row, re.S).group(0)
        assert 'data-testid="changed-chip"' in replied
        chased = _tender_page(
            client_in_program, world["tender"]["id"], f"?changed=outreach-{outreach_id}&cell=last_chased"
        )
        row = re.search(rf'<tr data-outreach-id="{outreach_id}".*?</tr>', chased, re.S).group(0)
        cell = re.search(r'<td [^>]*data-testid="supplier-chased"[^>]*>.*?</td>', row, re.S).group(0)
        assert 'data-testid="changed-chip"' in cell and "font-semibold" in cell
        assert row.count('data-testid="changed-chip"') == 1
        plain = _tender_page(client_in_program, world["tender"]["id"])
        assert 'data-testid="changed-chip"' not in plain


class TestASilentRound:
    def test_a_silent_supplier_is_on_suppliers_with_a_remind_action(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        assert 'data-testid="draft-reminders"' not in body
        theirs = body.split('data-testid="on-suppliers"', 1)[1].split("</section>", 1)[0]
        assert 'data-rule="no reply"' in theirs

    def test_once_everyone_has_replied_nothing_is_on_suppliers(self, da, world, client_in_program):
        op(
            da,
            "outreach_update",
            outreach_id=world["outreach"]["id"],
            data={"responded": True, "response_kind": "quote", "responded_on": "2026-07-09"},
        )
        body = _tender_page(client_in_program, world["tender"]["id"])
        theirs = body.split('data-testid="on-suppliers"', 1)[1].split("</section>", 1)[0]
        assert 'data-rule="no reply"' not in theirs

    def test_a_silent_row_says_how_long_and_a_replied_row_offers_another_reply(self, da, world, client_in_program):
        outreach_id = world["outreach"]["id"]
        body = _tender_page(client_in_program, world["tender"]["id"])
        silent = _row(body, outreach_id)
        days = (datetime.date.today() - datetime.date(2026, 7, 6)).days
        # One word for silence on the tender page (2026-10-04): the State chip.
        assert re.search(rf'data-testid="supplier-state">Silent {days}d<', silent)
        assert ">No<" not in silent and "No reply" not in silent and "Record a reply" in silent
        assert "Days waiting" not in body
        # A silent row has no reply day (its Replied cell is empty, ready to type one in), and one
        # never chased says so with a dash in a Last chased cell that takes a day typed into it.
        assert 'data-testid="replied"' not in silent
        cell = re.search(r'<td [^>]*data-testid="supplier-chased"[^>]*>(.*?)</td>', silent, re.S)
        assert f'data-edit="outreach:{outreach_id}:last_reminder_on"' in cell.group(0)
        assert re.sub(r"<[^>]+>", "", cell.group(1)).strip() == "—"

        op(
            da,
            "outreach_update",
            outreach_id=outreach_id,
            data={"responded": True, "response_kind": "needs_info", "responded_on": "2026-07-09"},
        )
        replied = _row(_tender_page(client_in_program, world["tender"]["id"]), outreach_id)
        # A reply is recorded: the menu edits it rather than offering to record one.
        assert "Record a reply" not in replied and 'data-testid="row-edit"' in replied
        assert "Silent" not in replied

    def test_a_chase_does_not_restart_the_silence(self, da, world, client_in_program):
        # A reminder chases the same request; the silence still counts from the ask, as the overview does.
        three_days_ago = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
        op(da, "outreach_update", outreach_id=world["outreach"]["id"], data={"last_reminder_on": three_days_ago})
        row = _row(_tender_page(client_in_program, world["tender"]["id"]), world["outreach"]["id"])
        days = (datetime.date.today() - datetime.date(2026, 7, 6)).days
        assert re.search(rf'data-testid="supplier-state">Silent {days}d<', row)
        assert "Silent 3d" not in row


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


@pytest.mark.django_db
def test_a_restricted_tender_says_so_once_in_its_summary(da, world, client_in_program):

    Tender.objects.filter(pk=world["tender"]["id"]).update(visibility="private")
    body = _tender_page(client_in_program, world["tender"]["id"])
    summary = re.search(r'data-testid="tender-summary".*?</div>', body, re.S).group(0)
    assert "Restricted to" in summary
    assert "only the suppliers below" not in body


@pytest.mark.django_db
def test_a_reply_reads_as_one_sentence_with_its_day(da, world, client_in_program):
    op(
        da,
        "outreach_update",
        outreach_id=world["outreach"]["id"],
        data={"responded": True, "response_kind": "quote", "responded_on": "2026-07-09"},
    )
    body = _tender_page(client_in_program, world["tender"]["id"])
    assert re.search(r"Replied with a quote on 9 Jul 20\d\d", body)
    assert "Responded on" not in body


@pytest.mark.django_db
class TestTheTenderHeader:
    def test_the_facts_are_a_label_value_block(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        facts = re.search(r'data-testid="tender-facts".*?</dl>', body, re.S).group(0)
        for label in ("Where the goods go", "Visibility"):
            assert f">{label}</dt>" in facts
        terms = _text(re.search(r'data-testid="tender-terms".*?</section>', body, re.S).group(0))
        # The day asked is the stage bar's, not repeated on the terms line.
        for label in ("Import duty", "Deadline"):
            assert label in terms

    def test_the_waiver_carries_its_evidence(self, da, world, client_in_program):
        _set(da, world, "buyer_waiver")
        body = _tender_page(client_in_program, world["tender"]["id"])
        terms = _text(re.search(r'data-testid="tender-terms".*?</section>', body, re.S).group(0))
        assert "Duty exemption not on file" in terms


@pytest.mark.django_db
class TestOneHeaderOneTable:
    def test_what_the_round_buys_sits_in_the_header_not_a_card_of_its_own(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        header = body.split('data-testid="tender-facts"')[1].split("</dl>")[0]
        assert 'data-testid="tender-buys"' in header
        assert "What this tender buys" not in body

    def test_inviting_a_supplier_is_one_control_beside_outreach(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        # Everyone is asked through outreach and no one through the marketplace: no second panel.
        assert 'data-testid="invited-suppliers"' not in body
        outreach_head = body.split('id="outreach"')[1].split("<table")[0]
        assert "+ Record an invitation" in outreach_head
        if 'id="invite-org"' in body:
            assert 'id="invite-org"' in outreach_head

    def test_the_draft_card_ends_with_recording_the_chase(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        drafts = body.split('id="drafts"')[1]
        card = drafts.split('data-testid="draft"')[1]
        if 'data-testid="chase-form"' in card:
            assert card.index('data-testid="draft-text"') < card.index('data-testid="chase-form"')
        if 'data-testid="copy-to"' in card:
            assert 'data-testid="open-in-mail"' in card
            assert "mailto:" in card


def test_a_product_reads_lower_case_inside_a_sentence_but_an_acronym_keeps_its_capitals():
    assert _product(Commodity(slug="rutf", name="Ready-to-use therapeutic food")) == "ready-to-use therapeutic food"
    assert _product(Commodity(slug="rutf", name="RUTF sachets")) == "RUTF sachets"
    assert _product(Commodity(slug="ors", name="ORS")) == "ORS"


@pytest.mark.django_db
class TestTheSuppliersTableFits:
    def test_a_sheet_of_one_value_columns_across_the_page(self, da, world, client_in_program):
        """Superseded the five wrapping columns beside a side rail (2026-10-05): the suppliers read as a
        sheet across the page, one stored value to a column, so each can be edited where it sits."""
        body = _tender_page(client_in_program, world["tender"]["id"])
        table = re.search(r'<table [^>]*data-testid="supplier-table".*?</table>', body, re.S).group(0)
        head = _text(re.search(r"<thead.*?</thead>", table, re.S).group(0))
        for column in ("Supplier", "State", "Asked", "Replied", "Last chased", "Price", "Delivery term", "Pack"):
            assert column in head
        assert 'class="sheet"' in table
        # Nothing beside it: the moves sit above the sheet, not in a rail that squeezes it.
        assert body.index('id="tender-moves"') < body.index('id="outreach"')

    def test_a_reply_day_has_its_own_editable_cell(self, da, world, client_in_program):
        _reply_today(da, world)
        body = _tender_page(client_in_program, world["tender"]["id"])
        outreach_id = world["outreach"]["id"]
        row = re.search(rf'<tr data-outreach-id="{outreach_id}".*?</tr>', body, re.S).group(0)
        cell = re.search(r'<td [^>]*data-testid="replied-on"[^>]*>.*?</td>', row, re.S).group(0)
        assert f'data-edit="outreach:{outreach_id}:responded_on"' in cell
        assert 'data-testid="replied"' in cell


@pytest.mark.django_db
def test_a_quote_s_open_facts_are_the_suppliers_sheet_s_not_history_s(da, world, client_in_program):
    _waiver(da, world)
    body = _tender_page(client_in_program, world["tender"]["id"])
    history = body[body.index('id="history"') :]
    assert 'data-fact="' not in history
    sheet = body[body.index('data-testid="supplier-table"') : body.index('id="history"')]
    # Ours are the sheet's fact chip, linking to the comparison that names each one.
    on_us = _text(re.search(r'data-testid="supplier-on-us"[^>]*>(.*?)</a>', sheet, re.S).group(1))
    assert on_us == "duty exemption · ours to attach" or re.match(r"\d+ facts · ours to fill$", on_us)
