from decimal import Decimal

import pytest

from connect_labs.supply_chain.values import (
    Money,
    Unconfirmed,
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


def test_packs_to_base_units():
    assert packs_to_base_units(Decimal("500"), 150) == Decimal("75000")


def test_metric_tonnes_to_base_units_uses_the_sachet_weight():
    # 1 tonne of 92 g sachets
    assert metric_tonnes_to_base_units(Decimal("1"), 92) == Decimal("10869")


def test_metric_tonnes_to_base_units_rejects_a_zero_weight():
    with pytest.raises(ValueError):
        metric_tonnes_to_base_units(Decimal("1"), 0)
