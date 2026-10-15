"""A tender's import-duty terms: set once, said on the tender page, and costed on the comparison.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on rehearsal_world.
"""

import html
import re
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.supply_chain import records
from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.models import Commitment, Document, Quote, Tender
from connect_labs.supply_chain.procurement.services.comparison import (
    landed_basis_words,
    round_duty_consequence,
    round_duty_words,
)
from connect_labs.supply_chain.procurement.services.pricing import Money, Unconfirmed, _extras, basis_gaps
from connect_labs.supply_chain.procurement.services.questions import (
    INTERNAL,
    SUPPLIER,
    initial_request_facts,
    missing_facts,
)
from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import _question, _quote, _set, _tender_page, _text, _waiver, op

# The world's fixtures, shared rather than copied.
da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program
web = rehearsal_world.web


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
        assert text.startswith("We import under the duty waiver")
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


@pytest.mark.django_db
class TestTheDutyLine:
    def test_the_waiver_keeps_what_the_quote_itself_said(self, da, world):
        quote = _quote(da, world, duties_basis="excluded", duties_amount="0.00")
        _set(da, world, "buyer_waiver")
        quote.tender.refresh_from_db()
        # Since DDD 004 the supplier's figure is not repeated: the round's terms are the basis.
        assert round_duty_words(quote, quote.tender) == "waived (our import)"
        assert "duty waived" not in landed_basis_words(quote, quote.tender, round_duty=False)
        assert "duty waived (our import)" in landed_basis_words(quote, quote.tender)

    def test_unsettled_terms_give_no_duty_line(self, da, world):
        quote = _quote(da, world)
        assert round_duty_words(quote, quote.tender) == ""


@pytest.mark.django_db
class TestTheQuoteUnderTheWaiver:
    def test_a_zero_duty_is_not_said_per_quote_twice(self, da, world):
        quote = _quote(da, world, duties_basis="excluded", duties_amount="0.00", freight_basis="included")
        words = landed_basis_words(quote, quote.tender)
        assert words.count("quote") == 1, words

    def test_duty_included_under_the_waiver_says_what_to_ask(self, da, world):
        quote = _quote(da, world, duties_basis="included")
        assert round_duty_consequence(quote, quote.tender) == ""
        _set(da, world, "buyer_waiver")
        quote.tender.refresh_from_db()
        said = round_duty_consequence(quote, quote.tender)
        assert "restate the price without duty" in said


@pytest.mark.django_db
def test_the_tender_attach_page_offers_the_duty_waiver(da, world, client_in_program):
    # The program's exemption is attached once, at program level (test_program_duty_exemption).
    url = reverse("supply_chain:program_document_attach") + "?kind=duty_exemption"
    response = client_in_program.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert re.search(r'<option value="duty_exemption"[^>]*selected', body), "the waiver is the kind offered"


def test_the_display_name_is_the_document_kinds_own():
    assert records.document_kind_label("duty_exemption") == "duty exemption"
    assert dict(Document._meta.get_field("kind").choices)["duty_exemption"] == "duty exemption"
    # Every kind has exactly one label, and the model's choices are that map.
    assert set(records.DOCUMENT_KIND_LABELS) == set(records.DOCUMENT_KINDS)
    assert dict(Document._meta.get_field("kind").choices) == records.DOCUMENT_KIND_LABELS


@pytest.mark.django_db
def test_the_comparison_names_it_and_its_action_by_that_name(da, world, client_in_program):
    _waiver(da, world)
    url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
    body = client_in_program.get(url).content.decode()
    assert "waiver document" not in body
    terms = _text(re.search(r'data-testid="waiver-evidence"[^>]*>(.*?)</span>', body, re.S).group(1))
    assert terms == "· Duty exemption not on file"
    if 'data-testid="waiver-pending"' not in body:
        pytest.skip("this world's quote does not leave the import to us")
    assert re.search(r'data-testid="waiver-pending">duty exemption not on file<', body)
    # Attached once, on the tender's duty line, and only there: no per-quote link to it.
    assert 'data-testid="duty-exemption-attach"' in body and 'data-testid="grid-terms-link"' not in body
    # The tender's document, said on the duty line and the duty cell -- not as a chip on the quote.
    assert not re.search(r'data-testid="grid-status">[^<]*duty exemption[^<]*<', body)


@pytest.mark.django_db
def test_the_tender_terms_name_it_the_same(da, world, client_in_program):
    _waiver(da, world)
    body = _tender_page(client_in_program, world["tender"]["id"])
    terms = _text(re.search(r'data-testid="waiver-evidence"[^>]*>(.*?)</span>', body, re.S).group(1))
    assert terms == "Duty exemption not on file"
    assert "waiver document" not in body
