"""DDD v11: a round's import-duty terms, set by answering a supplier or on the edit page,
decide how every quote's duty is costed; and an answer written into a reply draft reads
as drafted until that reply is marked sent."""

import html
import re
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.models import Commitment, Quote, Tender
from connect_labs.supply_chain.procurement.services.pricing import Money, Unconfirmed, _extras, basis_gaps
from connect_labs.supply_chain.procurement.services.questions import (
    INTERNAL,
    SUPPLIER,
    initial_request_facts,
    missing_facts,
)
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import _kanem_quote, op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page
from connect_labs.supply_chain.tests.test_unanswered_round_batch2 import _question, web  # noqa: F401 (fixture)

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _quote(da, world, **overrides):
    """Kanem's quote: freight included, duties excluded with no amount."""
    recorded = op(da, "quote_record", data=_kanem_quote(world, **overrides))
    return Quote.objects.select_related("tender", "commodity").get(pk=recorded["id"])


def _set(da, world, terms, estimate=None):
    op(da, "tender_set_duty_terms", tender_id=world["tender"]["id"], duty_terms=terms, duty_estimate_percent=estimate)
    return Tender.objects.get(pk=world["tender"]["id"])


def _fresh(quote):
    return Quote.objects.select_related("tender", "commodity").get(pk=quote.pk)


def _duty_facts(quote):
    tender = quote.tender
    return [f for f in missing_facts(quote, quote.commodity, tender) if "dut" in f.key]


@pytest.mark.django_db
class TestSettingTheTerms:
    def test_the_operation_sets_them_once_with_a_revision_and_the_day(self, da, world):
        tender = _set(da, world, "buyer_waiver")
        assert tender.duty_terms == "buyer_waiver" and tender.duty_terms_set_on is not None
        revisions = Revision.objects.filter(
            object_id=str(tender.pk), changes__has_key="duty_terms", action="update"
        ).count()
        assert revisions == 1
        _set(da, world, "buyer_waiver")  # idempotent: nothing changes, nothing recorded
        assert (
            Revision.objects.filter(object_id=str(tender.pk), changes__has_key="duty_terms", action="update").count()
            == 1
        )

    def test_unknown_terms_are_refused(self, da, world):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            _set(da, world, "free")

    def test_the_terms_box_sets_them_and_marks_the_line_new(self, da, world, web):  # noqa: F811
        url = reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        response = web.post(
            reverse("supply_chain:procurement_tender_duty_terms", args=[world["tender"]["id"]]),
            {"duty_terms": "buyer_waiver", "next": f"{url}#duty-terms"},
        )
        assert response.status_code == 302 and "duty_terms=changed" in response["Location"]
        assert response["Location"].endswith("#duty-terms")
        assert Tender.objects.get(pk=world["tender"]["id"]).duty_terms == "buyer_waiver"

    def test_the_answer_form_no_longer_sets_the_terms(self, da, world, web):  # noqa: F811
        asked = _question(da, world)
        body = web.get(reverse("supply_chain:commitment_resolve", args=[asked["id"]])).content.decode()
        assert 'data-testid="answer-duty-terms"' not in body and 'name="duty_terms"' not in body
        web.post(
            reverse("supply_chain:commitment_resolve", args=[asked["id"]]),
            {"resolution": "We import.", "resolved_on": "2026-10-02", "duty_terms": "buyer_waiver"},
        )
        assert Commitment.objects.get(pk=asked["id"]).resolution == "We import."
        assert Tender.objects.get(pk=world["tender"]["id"]).duty_terms == ""

    def test_an_answer_without_terms_leaves_them_unsettled(self, da, world, web):  # noqa: F811
        asked = _question(da, world)
        web.post(
            reverse("supply_chain:commitment_resolve", args=[asked["id"]]),
            {"resolution": "We are.", "resolved_on": "2026-10-02"},
        )
        assert Tender.objects.get(pk=world["tender"]["id"]).duty_terms == ""

    def test_the_edit_page_sets_them_through_the_same_setter(self, da, world):
        op(da, "tender_update", tender_id=world["tender"]["id"], data={"duty_terms": "buyer_pays"})
        tender = Tender.objects.get(pk=world["tender"]["id"])
        assert tender.duty_terms == "buyer_pays" and tender.duty_terms_set_on is not None


@pytest.mark.django_db
class TestTheTenderPageLine:
    def test_not_settled_then_the_waiver_marked_new(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        pattern = r'data-testid="tender-duty-terms"[^>]*>(.*?)<span class="text-gray-600">Deadline'
        line = re.search(pattern, body, re.S).group(1)
        assert " ".join(re.sub(r"<[^>]+>", "", line).split()).startswith("Not settled")
        _set(da, world, "buyer_waiver")
        body = _tender_page(client_in_program, world["tender"]["id"], "?duty_terms=changed")
        line = re.search(pattern, body, re.S).group(1)
        text = html.unescape(" ".join(re.sub(r"<[^>]+>", " ", line).split()))
        assert text.startswith("We import, under the program's duty waiver")
        assert " Set " in text and 'data-testid="changed-chip"' in line


@pytest.mark.django_db
class TestComparisonByTerms:
    def test_unsettled_blocks_on_the_duty_amount_and_asks_the_supplier(self, da, world):
        quote = _quote(da, world)
        assert isinstance(_extras(quote, quote.tender), Unconfirmed)
        assert basis_gaps(quote) == ["duties amount"]
        assert [f.audience for f in _duty_facts(quote)] == [SUPPLIER]

    def test_the_waiver_counts_duty_as_zero_and_asks_nobody(self, da, world):
        quote = _quote(da, world)
        _set(da, world, "buyer_waiver")
        quote = _fresh(quote)
        assert _extras(quote, quote.tender) == Money(Decimal("0"))
        assert basis_gaps(quote) == []
        assert _duty_facts(quote) == []
        assert not [f for f in initial_request_facts(quote.commodity, quote.tender) if f.key == "duties_basis"]
        snapshot = op(da, "tender_compare", tender_id=world["tender"]["id"], commodity_slug="rutf")
        rows = snapshot["comparable"] + snapshot["blocked"]
        assert any("duty waived (our import)" in (r.get("landed_basis") or "") for r in rows)

    def test_buyer_pays_needs_our_estimate_not_the_supplier(self, da, world):
        quote = _quote(da, world)
        _set(da, world, "buyer_pays")
        quote = _fresh(quote)
        assert isinstance(_extras(quote, quote.tender), Unconfirmed)
        facts = _duty_facts(quote)
        assert [(f.key, f.audience) for f in facts] == [("duty_estimate", INTERNAL)]
        _set(da, world, "buyer_pays", estimate="10")
        quote = _fresh(quote)
        assert _extras(quote, quote.tender) == Money(Decimal("0"))
        assert _duty_facts(quote) == []

    def test_supplier_ddp_reads_the_quote_as_before(self, da, world):
        quote = _quote(da, world)
        _set(da, world, "supplier_ddp")
        quote = _fresh(quote)
        assert basis_gaps(quote) == ["duties amount"]
        assert [f.audience for f in _duty_facts(quote)] == [SUPPLIER]

    def test_the_comparison_states_the_round_s_terms(self, da, world, client_in_program):
        _quote(da, world)
        _set(da, world, "buyer_waiver")
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        body = client_in_program.get(url).content.decode()
        note = re.search(r'data-testid="comparison-duty-terms"[^>]*>(.*?)</summary>', body, re.S).group(1)
        assert "under the program's duty waiver" in note.replace("&#x27;", "'")


@pytest.mark.django_db
class TestTheDraftedAnswer:
    def test_an_answer_in_an_unsent_reply_reads_drafted_until_the_reply_is_sent(self, da, world, client_in_program):
        asked = _question(da, world)
        op(da, "commitment_resolve", channel="web", commitment_id=asked["id"], resolution="We are.")
        body = _tender_page(client_in_program, world["tender"]["id"])
        row = re.search(rf'data-commitment-id="{asked["id"]}".*?</div>', body, re.S).group(0)
        text = " ".join(re.sub(r"<[^>]+>", " ", row).split())
        assert "Answer drafted · goes out with your reply to Northgate Rehearsal Commodities" in text
        assert "Answered" not in text
        op(da, "commitment_reply_sent", channel="web", commitment_ids=[asked["id"]])
        body = _tender_page(client_in_program, world["tender"]["id"])
        row = re.search(rf'data-commitment-id="{asked["id"]}".*?</div>', body, re.S).group(0)
        assert "Answered" in row and "Answer drafted" not in row

    def test_marking_an_open_question_sent_is_refused(self, da, world):
        asked = _question(da, world)
        with pytest.raises(ValueError):
            op(da, "commitment_reply_sent", commitment_ids=[asked["id"]])
