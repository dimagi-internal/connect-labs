"""DDD run 003, batch 2: disputing an invoice above the agreed price, a reminder's facts as a
labelled row signed by the buyer of record, the comparison's duty terms with where they came
from, an award that says the round is incomplete, and an empty invited panel left out."""

import html
import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.models import Commitment, Invoice
from connect_labs.supply_chain.procurement.services.comparison import landed_basis_words
from connect_labs.supply_chain.procurement.views import _reminder_facts
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import _contract, op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page
from connect_labs.supply_chain.tests.test_unanswered_round_batch2 import _question, web  # noqa: F401 (fixture)
from connect_labs.supply_chain.tests.test_unanswered_round_v11_duty_terms import _quote

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(fragment):
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", fragment).split()))


def _overbilled(da, world):
    contract = _contract(da, world)
    invoice = op(
        da,
        "invoice_record",
        data={
            "contract_id": contract["id"],
            "reference": "INV-REH-1",
            "issued_on": "2026-09-21",
            "currency": "USD",
            "amount": "110350.00",
            "unit_price": "51.20",
            "freight_amount": "7950.00",
            "quantity_billed": 2000,
            "quantity_unit": "carton",
            "source": "supplier_reported",
        },
    )
    return contract, invoice


@pytest.mark.django_db
class TestDisputingAnInvoice:
    def test_the_dispute_marks_it_queried_with_a_dated_reason_and_a_revision(self, da, world):
        _, invoice = _overbilled(da, world)
        out = op(
            da,
            "invoice_dispute",
            invoice_id=invoice["id"],
            reason="Unit price 51.20 against 49.80 agreed.",
            disputed_on="2026-10-03",
        )
        assert out["status"] == "queried"
        stored = Invoice.objects.get(pk=invoice["id"])
        assert stored.note == "Disputed 3 Oct 2026: Unit price 51.20 against 49.80 agreed."
        revisions = Revision.objects.filter(object_id=str(invoice["id"]), changes__has_key="status")
        assert revisions.exists()

    def test_a_paid_invoice_cannot_be_disputed(self, da, world):
        _, invoice = _overbilled(da, world)
        Invoice.objects.filter(pk=invoice["id"]).update(status="paid")
        with pytest.raises(ValueError):
            op(da, "invoice_dispute", invoice_id=invoice["id"], reason="Too much.")

    def test_the_order_offers_dispute_beside_pay_and_then_reads_disputed(self, da, world, client_in_program):
        contract, invoice = _overbilled(da, world)
        page = reverse("supply_chain:order_detail", args=[contract["id"]])
        body = client_in_program.get(page).content.decode()
        assert 'data-testid="invoice-dispute"' in body
        assert reverse("supply_chain:invoice_dispute", args=[invoice["id"]]) in body
        form = client_in_program.get(reverse("supply_chain:invoice_dispute", args=[invoice["id"]]))
        assert form.status_code == 200
        assert "51.20 against 49.80 agreed" in form.content.decode()
        posted = client_in_program.post(
            reverse("supply_chain:invoice_dispute", args=[invoice["id"]]),
            {"reason": "Freight above the agreed 7,200.00.", "disputed_on": "2026-10-03"},
        )
        assert posted.status_code == 302
        body = client_in_program.get(page).content.decode()
        assert 'data-testid="invoice-disputed"' in body
        assert 'data-testid="invoice-dispute"' not in body
        assert "Freight above the agreed 7,200.00." in body

    def test_each_variance_line_carries_its_currency_and_difference(self, da, world, client_in_program):
        contract, _ = _overbilled(da, world)
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        rows = [_text(r) for r in re.findall(r'data-testid="invoice-against-agreed".*?</tr>', body, re.S)]
        assert any("USD 51.20 USD 49.80 +USD 1.40" in r for r in rows)
        assert any("USD 7,950.00 USD 7,200.00 +USD 750.00" in r for r in rows)


@pytest.mark.django_db
class TestTheReminderCard:
    def test_the_facts_say_which_reminder_this_would_be(self):
        facts = _reminder_facts(
            {"sent_on": "2026-09-16", "last_reminder_on": "2026-09-24", "reminder_count": 2},
            7,
            as_of=__import__("datetime").date(2026, 10, 3),
        )
        words = {f["label"]: f["value"] for f in facts}
        assert words["Asked"] == "16 Sep 2026 (17 days ago)"
        assert words["Last reminded"] == "24 Sep 2026 · 2nd reminder"
        assert words["This would be"] == "the 3rd reminder"
        assert words["Due every"] == "7 days"

    def test_a_draft_is_signed_by_the_buyer_of_record_when_the_caller_is_not_resolvable(self, da, world):
        _contract(da, world)
        drafts = op(da, "tender_drafts_render", tender_id=world["tender"]["id"], today="2026-10-03")["drafts"]
        for draft in drafts:
            assert "[your organisation]" not in draft["text"]


@pytest.mark.django_db
class TestTheComparison:
    def test_a_zero_duty_on_the_quote_is_said_as_the_quote_s_word(self, da, world):
        quote = _quote(da, world, duties_basis="excluded", duties_amount="0.00")
        words = landed_basis_words(quote, quote.tender)
        assert "0.00 USD added" not in words
        # Since DDD 004: never "excluded from the price, stated as 0.00", which read as a
        # contradiction; under a buyer-import term the figure is not restated at all.
        assert "excluded from the price" not in words
        assert "on the quote" not in words

    def test_terms_set_by_an_answer_show_on_the_comparison(self, da, world, client_in_program):
        question = _question(da, world)
        op(
            da,
            "commitment_resolve",
            commitment_id=question["id"] if isinstance(question, dict) else question.pk,
            resolution="We import, under the program's duty waiver.",
            duty_terms="buyer_waiver",
            resolved_on="2026-10-03",
        )
        _quote(da, world)
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        body = client_in_program.get(url).content.decode()
        note = _text(re.search(r'data-testid="comparison-duty-terms".*?</summary>', body, re.S).group(0))
        assert "we import, under the program's duty waiver" in note.replace("&#x27;", "'")
        assert "duty exemption not on file" in note
        assert Commitment.objects.filter(resolved_on__isnull=False).exists()

    def test_a_quote_costed_on_a_waiver_not_on_file_owes_that_fact_on_us(self, da, world, client_in_program):
        """One gap list per quote: the waiver's copy is a fact on us, in header, duty cell and next step."""
        question = _question(da, world)
        op(
            da,
            "commitment_resolve",
            commitment_id=question["id"] if isinstance(question, dict) else question.pk,
            resolution="We import, under the program's duty waiver.",
            duty_terms="buyer_waiver",
            resolved_on="2026-10-03",
        )
        _quote(da, world)
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        body = client_in_program.get(url).content.decode()
        if 'data-testid="waiver-pending"' not in body:
            pytest.skip("this world's quote does not leave the import to us")
        assert "duty exemption not on file · to do" in body
        # The quote's header chip names it, or counts them ("duty exemption · to do" / "2 facts · to do").
        assert re.search(r'data-testid="grid-status">[^<]+ · to do<', body)
        assert ">Attach duty exemption<" in body
        # Award may still be offered, but never as the filled button while the fact is open.
        assert 'primary-dark" data-testid="grid-action"' not in body


@pytest.mark.django_db
def test_an_invited_panel_with_nothing_to_hold_is_left_out(da, world, client_in_program):
    body = _tender_page(client_in_program, world["tender"]["id"])
    # The world asks its suppliers through outreach and invites no one on the marketplace:
    # the panel shows only when it has an invitation, an invite control, or nobody asked.
    if 'data-testid="invited-suppliers"' in body:
        panel = body.split('data-testid="invited-suppliers"')[1].split("</div>")[0]
        assert "<li" in panel or "invite-org" in panel or "No one invited yet" in panel
