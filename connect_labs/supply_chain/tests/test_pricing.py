from decimal import Decimal

from connect_labs.supply_chain.procurement.services.pricing import compute_figures
from connect_labs.supply_chain.tests.conftest import quote
from connect_labs.supply_chain.values import Money, Unconfirmed


def _reasons(figure):
    assert isinstance(figure, Unconfirmed), f"expected Unconfirmed, got {figure!r}"
    return " | ".join(figure.reasons)


def test_a_fully_specified_quote_yields_every_figure(comparable_quote, rutf, round_2000_cartons):
    f = compute_figures(comparable_quote, rutf, round_2000_cartons)
    assert f.usd_per_pack_normalized == Money(Decimal("50.00"))
    assert f.usd_per_base_unit.amount == Decimal("50.00") / Decimal("150")
    assert f.usd_per_course == Money(Decimal("50.00"))
    assert f.landed_total_for_round_quantity == Money(Decimal("100000.00"))
    assert f.usd_per_child_treated == Money(Decimal("50.00"))


def test_an_unstated_pack_spec_blocks_per_sachet_and_per_course(rutf, round_2000_cartons):
    q = quote(pack_spec_source="not_stated", base_per_pack_stated=None)
    f = compute_figures(q, rutf, round_2000_cartons)
    assert "pack spec" in _reasons(f.usd_per_base_unit)
    assert "pack spec" in _reasons(f.usd_per_course)


def test_a_confirmed_trade_item_states_the_pack_spec(rutf, round_2000_cartons, item_150):
    """Naming the item IS telling us the pack configuration."""
    q = quote(pack_spec_source="trade_item_confirmed", base_per_pack_stated=None, item_id=11)
    f = compute_figures(q, rutf, round_2000_cartons, item=item_150)
    assert f.usd_per_base_unit.amount == Decimal("50.00") / Decimal("150")


def test_a_confirmed_item_overrides_the_commodity_when_they_disagree(rutf, round_2000_cartons, item_144):
    """The reason the item layer exists.

    The commodity says 150 per carton. This supplier's actual item is 144. The
    per-sachet figure must come from the item — deriving it from the commodity
    would be wrong by 4% and would look authoritative.
    """
    q = quote(pack_spec_source="trade_item_confirmed", base_per_pack_stated=None, item_id=12)
    f = compute_figures(q, rutf, round_2000_cartons, item=item_144)
    assert rutf.base_per_pack == 150
    assert f.usd_per_base_unit.amount == Decimal("50.00") / Decimal("144")


def test_a_quote_claiming_a_confirmed_item_without_one_supplied_is_unconfirmed(rutf, round_2000_cartons):
    """Fail closed: claiming an item we were not given is not a statement."""
    q = quote(pack_spec_source="trade_item_confirmed", base_per_pack_stated=None, item_id=99)
    f = compute_figures(q, rutf, round_2000_cartons, item=None)
    assert "pack spec" in _reasons(f.usd_per_base_unit)


def test_the_catalogue_pack_spec_is_never_substituted(rutf, round_2000_cartons):
    """The regression test for the bug this whole app exists to prevent.

    The commodity says 150 sachets per carton. The quote did not, and named no
    item. A per-sachet figure derived from the catalogue would look
    authoritative and be a guess, so it must not be produced at all.
    """
    q = quote(pack_spec_source="not_stated", base_per_pack_stated=None)
    f = compute_figures(q, rutf, round_2000_cartons)
    assert isinstance(f.usd_per_base_unit, Unconfirmed)
    assert rutf.base_per_pack == 150  # the tempting value is right there
    assert f.usd_per_pack_normalized == Money(Decimal("50.00"))  # per-pack is still fine


def test_a_per_sachet_quote_normalises_up_to_a_carton(rutf, round_2000_cartons):
    q = quote(as_quoted_unit="per_base_unit", as_quoted_amount="0.40")
    f = compute_figures(q, rutf, round_2000_cartons)
    assert f.usd_per_base_unit == Money(Decimal("0.40"))
    assert f.usd_per_pack_normalized == Money(Decimal("60.00"))


def test_a_quantity_basis_over_the_round_blocks_the_round_total(rutf, round_2000_cartons):
    q = quote(quantity_basis="2667", quantity_basis_unit="carton")
    f = compute_figures(q, rutf, round_2000_cartons)
    reasons = _reasons(f.landed_total_for_round_quantity)
    assert "2667" in reasons and "2000" in reasons
    # but the total for what they actually quoted is knowable
    assert isinstance(f.landed_total_as_quoted, Money)


def test_unspecified_freight_blocks_every_landed_figure(rutf, round_2000_cartons):
    q = quote(freight_basis="not_specified")
    f = compute_figures(q, rutf, round_2000_cartons)
    assert "freight" in _reasons(f.landed_total_for_round_quantity)
    assert "freight" in _reasons(f.landed_total_as_quoted)


def test_excluded_freight_with_an_amount_is_added_in(rutf, round_2000_cartons):
    q = quote(freight_basis="excluded", freight_amount="2000.00")
    f = compute_figures(q, rutf, round_2000_cartons)
    assert f.landed_total_for_round_quantity == Money(Decimal("102000.00"))


def test_excluded_freight_without_an_amount_is_unconfirmed(rutf, round_2000_cartons):
    q = quote(freight_basis="excluded", freight_amount=None)
    f = compute_figures(q, rutf, round_2000_cartons)
    assert "freight" in _reasons(f.landed_total_for_round_quantity)


def test_excluded_duties_without_an_amount_is_unconfirmed(rutf, round_2000_cartons):
    q = quote(duties_basis="excluded", duties_amount=None)
    f = compute_figures(q, rutf, round_2000_cartons)
    assert "duties" in _reasons(f.landed_total_for_round_quantity)


def test_a_non_usd_quote_without_a_rate_blocks_everything(rutf, round_2000_cartons):
    q = quote(as_quoted_currency="NGN", fx_rate_to_usd=None)
    f = compute_figures(q, rutf, round_2000_cartons)
    assert "NGN" in _reasons(f.usd_per_pack_normalized)
    assert "exchange rate" in _reasons(f.usd_per_pack_normalized)


def test_a_non_usd_quote_with_a_rate_converts(rutf, round_2000_cartons):
    q = quote(as_quoted_currency="NGN", as_quoted_amount="80000", fx_rate_to_usd="0.000625")
    f = compute_figures(q, rutf, round_2000_cartons)
    assert f.usd_per_pack_normalized == Money(Decimal("50.000000"))


def test_a_commodity_without_a_course_definition_blocks_per_course(
    comparable_quote, rutf_without_course, round_2000_cartons
):
    f = compute_figures(comparable_quote, rutf_without_course, round_2000_cartons)
    assert "course" in _reasons(f.usd_per_course)
    assert "course" in _reasons(f.usd_per_child_treated)
    # the honest figures are unaffected
    assert f.usd_per_pack_normalized == Money(Decimal("50.00"))


def test_a_missing_amount_blocks_everything(rutf, round_2000_cartons):
    q = quote(as_quoted_amount=None)
    f = compute_figures(q, rutf, round_2000_cartons)
    assert "amount" in _reasons(f.usd_per_pack_normalized)


def test_reasons_accumulate_when_several_facts_are_missing(rutf, round_2000_cartons):
    q = quote(pack_spec_source="not_stated", base_per_pack_stated=None, freight_basis="not_specified")
    f = compute_figures(q, rutf, round_2000_cartons)
    reasons = _reasons(f.usd_per_course)
    assert "pack spec" in reasons


def test_the_catalogue_unit_weight_is_never_substituted(rutf, round_2000_cartons):
    """The unit-weight sibling of the pack-spec regression test.

    The commodity says 92 g per sachet. The quote did not state a weight and
    named no item. A per-tonne conversion derived from the catalogue would
    look authoritative and be a guess, so it must not be produced at all.
    """
    q = quote(
        as_quoted_unit="per_metric_tonne",
        as_quoted_amount="5000.00",
        quantity_basis="27.6",
        quantity_basis_unit="metric_tonne",
        base_unit_grams_stated=None,
    )
    f = compute_figures(q, rutf, round_2000_cartons)
    assert rutf.base_unit_grams == 92  # the tempting value is right there
    assert "unit weight" in _reasons(f.usd_per_base_unit)
    assert "unit weight" in _reasons(f.landed_total_as_quoted)


def test_a_confirmed_item_states_the_unit_weight_when_the_quote_does_not(rutf, round_2000_cartons, item_100g):
    """The commodity says 92 g. This supplier's actual item is 100 g.

    Deriving the per-tonne conversion from the commodity would be wrong by
    ~8.7% and would look authoritative — it must come from the item.
    """
    q = quote(
        as_quoted_unit="per_metric_tonne",
        as_quoted_amount="5000.00",
        quantity_basis="27.6",
        quantity_basis_unit="metric_tonne",
        base_unit_grams_stated=None,
        item_id=13,
    )
    f = compute_figures(q, rutf, round_2000_cartons, item=item_100g)
    assert rutf.base_unit_grams == 92
    # 1,000,000 g / 100 g = 10,000 sachets per tonne, exactly
    assert f.usd_per_base_unit == Money(Decimal("0.50"))


def test_a_per_tonne_quote_against_a_tonne_basis_is_exact(rutf, round_2000_cartons):
    q = quote(
        as_quoted_unit="per_metric_tonne",
        as_quoted_amount="5000.00",
        quantity_basis="27.6",
        quantity_basis_unit="metric_tonne",
        base_unit_grams_stated=92,
    )
    f = compute_figures(q, rutf, round_2000_cartons)
    assert f.landed_total_as_quoted == Money(Decimal("138000.00"))


def test_a_lot_total_quote_landed_total_is_exact(rutf, round_2000_cartons):
    q = quote(as_quoted_unit="per_lot_total", as_quoted_amount="100000.00")
    f = compute_figures(q, rutf, round_2000_cartons)
    assert f.landed_total_as_quoted == Money(Decimal("100000.00"))


def test_a_missing_quantity_basis_names_the_missing_fact_not_the_word_none(rutf, round_2000_cartons):
    """A supplier-facing reason must never read like "covers None carton" —
    that is internal absence-representation leaking into product copy."""
    q = quote(quantity_basis=None)
    f = compute_figures(q, rutf, round_2000_cartons)
    reasons = _reasons(f.landed_total_for_round_quantity)
    assert "None" not in reasons
    assert "no quantity basis recorded" in reasons
