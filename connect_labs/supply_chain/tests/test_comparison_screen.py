"""The comparison: facts as data, every quote offered on the award, gaps said as whose they are.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on rehearsal_world.
"""

import re
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.template import Context, Template
from django.urls import reverse

from connect_labs.supply_chain.models import Commitment, Quote
from connect_labs.supply_chain.procurement.services.comparison import (
    _with_incoterm,
    landed_basis_words,
    per_pack_note,
)
from connect_labs.supply_chain.procurement.services.pricing import compute_figures
from connect_labs.supply_chain.procurement.views import _without_empty_tail, missing_item, not_stated
from connect_labs.supply_chain.templatetags.supply_chain_extras import owes_supplier_facts, waits_only_on_round
from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import _comparable, _question, _quote, _set, _text, op

# The world's fixtures, shared rather than copied.
da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program
web = rehearsal_world.web


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


class TestOneOfferAndItsGaps:
    def test_one_offer_is_not_numbered_or_ranked(self, da, world, client_in_program):
        op(da, "quote_record", data=_comparable(world))
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        body = client_in_program.get(url).content.decode()
        # One offer is one row of facts, no rank, and no verdict on it: the count is data.
        assert body.count('data-testid="grid-quote"') == 1
        count = re.search(r'data-testid="quote-count"[^>]*>([^<]*)<', body)
        assert count is not None and " ".join(count.group(1).split()) == "1 quote · 1 with a landed price"
        assert 'data-testid="ranked-table"' not in body

    def test_a_gap_the_buyer_records_is_not_blamed_on_the_supplier(self):
        row = {
            "supplier_name": "Sahel",
            "blockers": [
                {"fact": "Quote is in EUR and no exchange rate was recorded", "label": "exchange rate"},
                {"fact": "Freight excluded", "label": "freight amount"},
            ],
        }
        assert not_stated(row) == "Sahel has not stated freight amount; no exchange rate recorded for the EUR quote"
        only_ours = {"supplier_name": "Sahel", "blockers": [row["blockers"][0]]}
        assert not_stated(only_ours) == "Sahel: no exchange rate recorded for the EUR quote"

    def test_an_empty_trailing_column_is_dropped_but_not_one_between_figures(self):
        rows = [{"figures": {"a": {"amount": "1"}, "b": {"amount": None}, "c": {"amount": "2"}, "d": {}}}]
        columns = [{"key": k} for k in "abcd"]
        assert [c["key"] for c in _without_empty_tail(rows, columns)] == ["a", "b", "c"]


class TestTheGridAndTheAward:
    def _page(self, client, world):
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        return client.get(url).content.decode()

    def test_the_grid_shows_the_gap_and_the_award_offers_every_quote(self, da, world, client_in_program):
        kanem = op(da, "quote_record", data=_comparable(world))
        northgate = op(
            da,
            "quote_record",
            data={
                **{k: v for k, v in _comparable(world).items() if k != "base_per_pack_stated"},
                "supplier_id": world["northgate"]["id"],
                "pack_spec_source": "not_stated",
            },
        )
        body = self._page(client_in_program, world)
        # The count is data -- quotes, and how many have a landed price -- not a verdict.
        assert re.search(r'data-testid="quote-count"[^>]*>2 quotes · 1 with a landed price<', body)
        grid = re.search(r'<table [^>]*data-testid="comparison-grid".*?</table>', body, re.S).group(0)
        assert re.search(r">[^<]+ · to ask<", grid)  # never asked since the quote came in
        pack = "".join(re.findall(r'<td [^>]*data-fact="pack"[^>]*>.*?</td>', grid, re.S))
        assert "not stated" in pack
        # Northgate's landed cell names the input its figure needs, as data.
        landed = re.search(
            rf'<td [^>]*data-fact="landed" data-quote-id="{northgate["id"]}"[^>]*>(.*?)</td>', grid, re.S
        ).group(1)
        assert re.search(r'data-testid="landed-needs">needs sachets per carton<', landed)
        for word in ("PROVISIONAL", "provisional", "beat", "omparable"):
            assert word not in grid
        button = " ".join(
            _text(re.search(r'<summary data-testid="award-open"[^>]*>(.*?)</summary>', body, re.S).group(1)).split()
        )
        assert button == "Award"
        # Both are offered, the one with a landed price first.
        assert re.search(rf'<option value="{kanem["id"]}" selected>Kanem', body)
        assert f'<option value="{northgate["id"]}">' in body

    def test_decided_on_is_empty_until_an_award_is_started(self, da, world, client_in_program):
        op(da, "quote_record", data=_comparable(world))
        body = self._page(client_in_program, world)
        field = re.search(r'<input type="date" id="award-decided-on"[^>]*>', body).group(0)
        assert 'value=""' in field
        rationale = re.search(r'<input type="text" id="award-rationale"[^>]*>', body, re.S).group(0)
        assert "oninput=" in rationale


class TestTheAwardFold:
    def _page(self, client, world):
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        return client.get(url).content.decode()

    def test_the_award_fields_wait_behind_the_award_button(self, da, world, client_in_program):
        op(da, "quote_record", data=_comparable(world))
        body = self._page(client_in_program, world)
        start = re.search(r'<details [^>]*data-testid="award-start"[^>]*>(.*?)</details>', body, re.S)
        assert start is not None and " open" not in start.group(0).split(">", 1)[0]
        inner = start.group(1)
        assert re.search(r'<summary data-testid="award-open"[^>]*>Award[^<]*</summary>', inner)
        form = re.search(r'<form [^>]*data-testid="award-form".*?</form>', inner, re.S).group(0)
        assert 'name="rationale"' in form and 'data-testid="award-submit"' in form
        assert re.search(r'<option value="\d+" selected>Kanem', form)

    def test_a_link_to_the_award_step_opens_the_fields(self, da, world, client_in_program):
        op(da, "quote_record", data=_comparable(world))
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]])
        body = client_in_program.get(url + "?commodity=rutf&step=award").content.decode()
        tag = re.search(r'<details [^>]*data-testid="award-start"[^>]*>', body).group(0)
        assert " open" in tag


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


def test_an_unranked_quote_does_not_claim_it_fed_the_ranking():
    no_figure = {"usd_per_pack_normalized": SimpleNamespace(amount=None, reasons=("something else",))}
    assert per_pack_note(no_figure, "sachet", "carton") == ""
    fx = {"usd_per_pack_normalized": SimpleNamespace(amount=None, reasons=("Quote is in EUR and no exchange rate",))}
    assert per_pack_note(fx, "sachet", "carton") == "(per carton once an exchange rate is recorded)"
    known = {"usd_per_pack_normalized": SimpleNamespace(amount=Decimal("46.50"), currency="USD", reasons=())}
    assert per_pack_note(known, "sachet", "carton").startswith("= USD 46.50")


@pytest.mark.django_db
class TestTheComparisonUnderTheWaiver:
    def test_the_blocked_card_says_duty_is_waived(self, da, world, client_in_program):
        _quote(da, world, pack_spec_source="not_stated")
        _set(da, world, "buyer_waiver")
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        body = client_in_program.get(url).content.decode()
        note = _text(re.search(r'data-testid="comparison-duty-terms".*?</summary>', body, re.S).group(0))
        assert "we import under the duty waiver" in note
        grid = _text(re.search(r'data-testid="comparison-grid".*?</table>', body, re.S).group(0))
        assert "our terms: not settled" not in grid

    def test_the_banner_lists_each_supplier_s_missing_facts(self):
        row = {
            "supplier_name": "Sahel",
            "blockers": [
                {"label": "freight amount", "fact": "Freight excluded"},
                {"label": "exchange rate", "fact": "Quote is in EUR and no exchange rate was recorded"},
            ],
        }
        assert missing_item(row) == "Sahel: freight amount, exchange rate (EUR)"


@pytest.mark.django_db
class TestTheTermsOnTheComparison:
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
        assert "we import under the duty waiver" in note
        assert "Duty exemption not on file" in note
        assert Commitment.objects.filter(resolved_on__isnull=False).exists()


def test_rows_held_only_by_the_rounds_decision_are_grouped_apart():
    round_only = {"blockers": [{"question": {"key": "duty_terms", "audience": "internal"}}]}
    owes = {
        "blockers": [
            {"question": {"key": "duty_terms", "audience": "internal"}},
            {"question": {"key": "freight_amount", "audience": "supplier"}},
        ]
    }
    rows = [round_only, owes]
    assert waits_only_on_round(rows) == [round_only]
    assert owes_supplier_facts(rows) == [owes]


def test_a_foreign_currency_price_reads_per_pack_once_the_pack_is_stated():
    fx = {"usd_per_pack_normalized": SimpleNamespace(amount=None, reasons=("Quote is in EUR and no exchange rate",))}
    note = per_pack_note(fx, "sachet", "carton", quoted_per_pack="EUR 46.50 per carton (150 sachets)")
    assert note == "= EUR 46.50 per carton (150 sachets); in USD once an exchange rate is recorded"


def test_a_delivered_line_leads_with_the_quoted_incoterm():
    quote = SimpleNamespace(incoterm="CPT Kano")
    assert _with_incoterm("Delivered to Kano · freight included, per quote", quote) == (
        "CPT Kano · delivered to Kano · freight included, per quote"
    )
    assert _with_incoterm("Ex works Niamey — delivery to Kano requested", quote) == (
        "Ex works Niamey — delivery to Kano requested"
    )
    assert _with_incoterm("Delivered to Kano", SimpleNamespace(incoterm="")) == "Delivered to Kano"
