from decimal import Decimal

import pytest

from connect_labs.supply_chain.values import (
    Money,
    Unconfirmed,
    confirmed,
    merge,
    metric_tonnes_to_base_units,
    packs_to_base_units,
    unconfirmed,
)


def test_money_is_frozen_and_defaults_to_usd():
    m = Money(Decimal("52.42"))
    assert m.currency == "USD"
    with pytest.raises(Exception):
        m.amount = Decimal("1")


def test_unconfirmed_carries_its_reasons_in_order():
    u = unconfirmed("pack spec not stated on quote", "freight basis not specified")
    assert u.reasons == ("pack spec not stated on quote", "freight basis not specified")


def test_merge_returns_none_when_everything_is_confirmed():
    assert merge(Money(Decimal("1")), Money(Decimal("2"))) is None


def test_merge_combines_reasons_from_every_unconfirmed_input():
    result = merge(
        Money(Decimal("1")),
        unconfirmed("a"),
        unconfirmed("b", "c"),
    )
    assert isinstance(result, Unconfirmed)
    assert result.reasons == ("a", "b", "c")


def test_merge_deduplicates_repeated_reasons():
    result = merge(unconfirmed("same"), unconfirmed("same"))
    assert result.reasons == ("same",)


def test_confirmed_is_a_pass_through_merge_accepts_directly():
    # confirmed() exists so a plain domain value can ride into merge() without a
    # throwaway Money(...) stand-in obscuring the call site's intent.
    assert confirmed(150) == 150
    reason = unconfirmed("no quantity basis recorded on the quote")
    assert confirmed(reason) is reason
    assert merge(Money(Decimal("1")), confirmed(150)) is None
    assert merge(Money(Decimal("1")), confirmed(reason)) == reason


def test_packs_to_base_units():
    assert packs_to_base_units(Decimal("500"), 150) == Decimal("75000")


def test_packs_to_base_units_rejects_a_zero_pack_size():
    """Finding 21: both unit-ladder conversions now share one positivity
    guard (values._require_positive) rather than each carrying its own copy."""
    with pytest.raises(ValueError, match="base_per_pack must be positive"):
        packs_to_base_units(Decimal("500"), 0)


def test_metric_tonnes_to_base_units_uses_the_sachet_weight():
    # 1 tonne of 92 g sachets
    assert metric_tonnes_to_base_units(Decimal("1"), 92) == Decimal("10869")


def test_metric_tonnes_to_base_units_rejects_a_zero_weight():
    with pytest.raises(ValueError):
        metric_tonnes_to_base_units(Decimal("1"), 0)


class TestPublishedPrecision:
    """A derived price is rounded on the way out, not on the way through.

    A $50 carton at 150 sachets reported
    `0.33333333333333333333333333 USD` per sachet on the quote page -- 26
    digits off a two-decimal input, asserting a precision nobody has.

    Quantizing at the DIVISION was the first attempt and was wrong: a figure
    here is often the input to the next one, so rounding per-sachet made cost
    per course 49.9950 rather than 50.00. The rounding belongs at the
    boundary.
    """

    def test_a_published_price_carries_the_money_scale(self):
        from decimal import Decimal

        from connect_labs.supply_chain.values import MONEY_SCALE, Money, to_wire

        wire = to_wire(Money(Decimal("50.00") / Decimal("150")))
        assert wire["amount"] == "0.3333"
        assert MONEY_SCALE == Decimal("0.0001")

    def test_a_whole_amount_is_not_padded_to_the_scale(self):
        """decimal_string still trims: 288 must not publish as 288.0000, or
        the two producers of a figure describe one value two ways."""
        from decimal import Decimal

        from connect_labs.supply_chain.values import Money, to_wire

        assert to_wire(Money(Decimal("288")))["amount"] == "288"

    def test_derivations_downstream_keep_full_precision(self):
        """The reason the rounding is at the boundary. Cost per course is the
        per-sachet price times the ration, and must not inherit the rounding
        of the figure it is computed from."""
        from decimal import Decimal

        from connect_labs.supply_chain.values import Money, to_wire

        per_sachet = Decimal("50.00") / Decimal("150")
        assert to_wire(Money(per_sachet * Decimal("150")))["amount"] == "50"
