"""How a figure is written for a person: the one quantity rule, the one money rule.

Stated in values.py and nowhere else; these pin what they say.
"""

import pytest

from connect_labs.supply_chain.templatetags.supply_chain_extras import (
    figure_text,
    money,
    money_text,
    qty,
    quantity_text,
    unit_plural,
    words,
)
from connect_labs.supply_chain.values import money_digits, quantity_digits, quantity_phrase, unit_noun


class TestQuantities:
    @pytest.mark.parametrize(
        "raw,shown",
        [
            ("30000.0000", "30,000"),
            ("83.7209", "83.72"),
            ("0.5000", "0.5"),
            ("1.0000", "1"),
            ("1234567", "1,234,567"),
            ("-12.005", "-12.01"),
        ],
    )
    def test_grouped_at_most_two_places_and_whole_numbers_have_no_point(self, raw, shown):
        assert quantity_digits(raw) == shown

    def test_the_unit_is_a_word_and_plural_unless_exactly_one(self):
        assert qty("3", "jerry_can") == "3 jerry cans"
        assert qty("1.0000", "carton") == "1 carton"
        assert qty("83.7209", "jerry_can") == "83.72 jerry cans"
        assert qty("12", "L") == "12 L"
        assert qty("2", "box") == "2 boxes"
        assert unit_plural("sachet") == "sachets"
        assert unit_noun("jerry_can") == "jerry can"

    def test_the_rfq_writes_quantities_by_the_same_rule(self):
        assert quantity_phrase(2000, "carton") == "2,000 cartons"
        assert quantity_phrase(1, "carton") == "1 carton"

    def test_a_derived_quantity_cell(self):
        assert figure_text({"amount": "612000", "unit": "co-pack"}) == "612,000 co-packs"
        assert quantity_text({"amount": "3033.3333", "unit": "carton"}) == "3,033.33 cartons"


class TestMoney:
    def test_grouped_with_two_places(self):
        assert money_digits("612000") == "612,000.00"
        assert money("18000.0000") == "18,000.00"
        assert money_text({"amount": "18000.0000", "currency": "USD"}) == "USD 18,000.00"
        assert figure_text({"amount": "612000", "currency": "NGN"}) == "NGN 612,000.00"

    def test_two_different_per_unit_prices_never_read_the_same(self):
        """CodeRabbit on #1975: rounding to cents made 0.3333 and 0.3350 per
        sachet both "0.33" -- two offers shown as one price."""
        a = money_text({"amount": "0.3333", "currency": "USD"})
        b = money_text({"amount": "0.3350", "currency": "USD"})
        assert a == "USD 0.3333"
        assert b == "USD 0.335"
        assert a != b

    def test_a_price_with_only_cents_keeps_two_places(self):
        assert money_text({"amount": "0.6000", "currency": "USD"}) == "USD 0.60"


class TestWords:
    @pytest.mark.parametrize(
        "code,said",
        [
            ("programme_org", "the programme"),
            ("stock_below_minimum", "Below its own minimum"),
            ("at_customs", "at customs"),
            ("per_base_unit", "per single unit"),
            ("not_specified", "not stated"),
            ("user_held", "held by a field worker"),
            ("certificate_of_analysis", "certificate of analysis"),
            ("donor", "donor"),
        ],
    )
    def test_a_code_reads_as_words(self, code, said):
        assert words(code) == said
