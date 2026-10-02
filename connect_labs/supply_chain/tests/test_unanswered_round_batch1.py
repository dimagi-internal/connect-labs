"""The unanswered round, judged batch 1: the tender and order pages read the round as it is.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented,
and every address uses `.example.invalid`.

Builds on test_tracking_reality's world: one open RUTF tender, Kanem invited,
Northgate a second supplier.
"""

import datetime
import re

import pytest
from django.template import Context, Template
from django.urls import reverse

from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import PROGRAM, _contract, _kanem_quote, op

# That module's fixtures, shared rather than copied.
da = reality.da
world = reality.world
client_in_program = reality.client_in_program


@pytest.fixture
def web(client_in_program, da, monkeypatch):
    """The same signed-in client, able to submit the write screens too."""
    from connect_labs.supply_chain import form_views

    monkeypatch.setattr(form_views, "_access", lambda request: da)
    monkeypatch.setattr(form_views, "has_program_context", lambda request: True)
    return client_in_program


def _text_of(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _tender_page(client, tender_id, query=""):
    return client.get(reverse("supply_chain:procurement_tender_detail", args=[tender_id]) + query).content.decode()


def _chase(da, outreach_id, day, recorded_at):
    with seed_overrides(PROGRAM, recorded_at=recorded_at):
        call_operation(
            "outreach_update", da, {"outreach_id": outreach_id, "data": {"last_reminder_on": day}}, channel="web"
        )


# ---- A: back to the row just saved


class TestBackToTheRowJustSaved:
    def test_saving_a_reply_returns_to_that_outreach_row_picked_out(self, da, world, web):
        outreach_id = world["outreach"]["id"]
        response = web.post(
            reverse("supply_chain:procurement_outreach_reply", args=[outreach_id]),
            {"last_reminder_on": "2026-07-13"},
        )
        tender_url = reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        assert response.status_code == 302
        assert response.url == f"{tender_url}?changed=outreach-{outreach_id}#outreach"

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


# ---- B, C, D, I, K: the tender page


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
        passed = re.search(r'data-testid="deadline-passed">([^<]*)<', body).group(1)
        assert passed.startswith("Open — deadline passed ") and passed.endswith("(3 days ago)")

    def test_a_deadline_still_ahead_reads_as_before(self, da, world, client_in_program):
        ahead = datetime.date.today() + datetime.timedelta(days=3)
        op(da, "tender_update", tender_id=world["tender"]["id"], data={"response_deadline": ahead.isoformat()})
        body = _tender_page(client_in_program, world["tender"]["id"])
        assert 'data-testid="deadline-passed"' not in body
        assert "response deadline" in body

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
        cell = body.split('data-testid="last-chased"', 1)[1].split("</td>", 1)[0]
        assert "2 Oct 2026" in cell and "2nd reminder" in cell

        # Read as of the first chase, it was the first.
        as_of = _tender_page(client_in_program, world["tender"]["id"], "?as_of=2026-10-01")
        cell = as_of.split('data-testid="last-chased"', 1)[1].split("</td>", 1)[0]
        assert "1 Oct 2026" in cell and "1st reminder" in cell

    def test_outreach_comes_before_the_invited_suppliers_and_delete_is_muted(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        assert body.index('id="outreach"') < body.index("Invited suppliers")
        delete = re.search(r'class="([^"]*)">Delete</a>', body).group(1)
        assert delete.split()[0] == "text-gray-600"  # red only on hover, not at rest


# ---- E, F: history in the words of the page


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
        assert "55.00 USD per carton (DDP Kano: freight and duty included)" in text

    def test_a_quote_with_no_incoterm_reads_as_before(self, da, world, client_in_program):
        op(da, "quote_record", data=_kanem_quote(world))
        text = re.sub(r"<[^>]+>", "", _tender_page(client_in_program, world["tender"]["id"]))
        assert "54.50 USD per carton (freight included)" in text


# ---- G: the specification chip


class TestSpecificationChip:
    @pytest.mark.parametrize(
        "count, expected",
        [(1, "Meets spec (1 requirement)"), (3, "Meets spec (3 requirements)")],
    )
    def test_a_passing_offer_says_how_many_requirements_it_meets(self, count, expected):
        rendered = Template(
            '{% include "supply_chain/procurement/_specification.html" with specification=spec %}'
        ).render(Context({"spec": {"outcome": "pass", "summary": f"Meets all {count}", "requirement_count": count}}))
        assert expected in rendered and "Spec:" not in rendered


# ---- H: the order page's invoices


class TestOrderInvoices:
    def test_an_advance_reads_as_one_and_the_invoice_says_how_far_above_it_is(self, da, world, client_in_program):
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
                "reference": "INV-REH-1",
                "issued_on": "2026-09-21",
                "currency": "USD",
                "amount": "110350.00",
                "quantity_billed": 2000,
                "quantity_unit": "carton",
                "acknowledges_payment_ids": [paid["id"]],
                "source": "supplier_reported",
            },
        )
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", body))
        # Its own row of the invoices table since batch 4.
        row = _text_of(re.search(r'<tr data-testid="advance-row">.*?</tr>', body, re.S).group(0))
        assert row.startswith("Advance paid 28 Jul 2026 USD 53,400.00")
        assert "applied to INV-REH-1" in row
        assert "confirmed by the payee 21 Sep 2026" in text
        marker = re.search(r'data-testid="invoice-above-agreed"[^>]*>(.*?)</div>', body, re.S).group(1)
        assert re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", marker)).strip() == "USD 3,550.00 above agreed"
        # At body size, with its tag (batch 2).
        assert 'data-testid="invoice-above-agreed" class="text-sm text-gray-900"' in body
        assert 'data-testid="above-agreed-tag"' in marker


# ---- J: the overview's waiting-on kinds in bold


def test_lead_in_bolds_the_kind_and_keeps_the_text():
    rendered = Template("{% load supply_chain_extras %}{{ line|lead_in }}|{{ plain|lead_in }}").render(
        Context({"line": "No reply: Plateau & Sons", "plain": "Nothing to chase"})
    )
    assert rendered == "<strong>No reply</strong>: Plateau &amp; Sons|Nothing to chase"
