from decimal import Decimal

from connect_labs.supply_chain.models import Round, Supplier
from connect_labs.supply_chain.procurement.services.comparison import compare_round
from connect_labs.supply_chain.tests.conftest import quote, wrap
from connect_labs.supply_chain.values import Unconfirmed


def suppliers():
    return {
        1: wrap(Supplier, {"name": "Northwind Nutrition"}, record_id=1),
        2: wrap(Supplier, {"name": "Harmattan Foods"}, record_id=2),
    }


def _column(comparison, key):
    return next(c for c in comparison.columns if c.key == key)


def test_two_complete_quotes_are_both_comparable_and_ranked(rutf, round_2000_cartons):
    quotes = [
        quote(supplier_id=1, as_quoted_amount="50.00"),
        quote(supplier_id=2, as_quoted_amount="52.00"),
    ]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())
    assert comparison.comparable_count == 2
    assert comparison.total_count == 2
    assert comparison.blocked == []
    assert _column(comparison, "usd_per_pack_normalized").rankable is True


def test_an_incomplete_quote_is_blocked_not_ranked(rutf, round_2000_cartons):
    quotes = [
        quote(supplier_id=1, as_quoted_amount="50.00"),
        quote(supplier_id=2, pack_spec_source="not_stated", base_per_pack_stated=None),
    ]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())
    assert comparison.comparable_count == 1
    assert comparison.total_count == 2
    assert [r.supplier_name for r in comparison.comparable] == ["Northwind Nutrition"]
    assert [r.supplier_name for r in comparison.blocked] == ["Harmattan Foods"]


def test_a_column_is_rankable_over_the_comparable_subset(rutf, round_2000_cartons):
    """Rule 6 as amended: the ranking covers what can be defended, not nothing."""
    quotes = [
        quote(supplier_id=1, as_quoted_amount="50.00"),
        quote(supplier_id=2, pack_spec_source="not_stated", base_per_pack_stated=None),
    ]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())
    column = _column(comparison, "usd_per_base_unit")
    assert column.rankable is True
    assert "Harmattan Foods" in column.blocked_by


def test_a_blocked_row_keeps_the_figures_it_does_have(rutf, round_2000_cartons):
    quotes = [quote(supplier_id=2, pack_spec_source="not_stated", base_per_pack_stated=None)]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())
    row = comparison.blocked[0]
    assert row.figures["usd_per_pack_normalized"].amount == Decimal("50.00")
    assert isinstance(row.figures["usd_per_base_unit"], Unconfirmed)


def test_column_labels_come_from_the_commodity_not_a_hardcoded_noun(rutf, round_2000_cartons):
    comparison = compare_round(round_2000_cartons, rutf, [quote(supplier_id=1)], suppliers())
    assert _column(comparison, "usd_per_base_unit").label == "USD per sachet"
    assert _column(comparison, "usd_per_pack_normalized").label == "USD per carton"


def test_nothing_is_rankable_when_every_candidate_is_blocked(rutf, round_2000_cartons):
    quotes = [quote(supplier_id=2, pack_spec_source="not_stated", base_per_pack_stated=None)]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())
    assert comparison.comparable_count == 0
    assert _column(comparison, "usd_per_base_unit").rankable is False


def test_voided_and_superseded_quotes_are_excluded(rutf, round_2000_cartons):
    quotes = [
        quote(supplier_id=1, as_quoted_amount="50.00"),
        quote(supplier_id=2, voided=True, void_reason="duplicate"),
        quote(supplier_id=2, superseded_by_quote_id=99),
    ]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())
    assert comparison.total_count == 1
    assert len(comparison.all_rows) == 1  # the flat view agrees with the counts


def test_every_blocked_row_carries_its_outstanding_questions(rutf, round_2000_cartons):
    quotes = [quote(supplier_id=2, pack_spec_source="not_stated", base_per_pack_stated=None)]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())
    assert any(f.key == "pack_spec" for f in comparison.blocked[0].questions)


def test_a_confirmed_trade_item_makes_a_quote_comparable(rutf, round_2000_cartons, item_144):
    """The item layer earning its place: no pack-spec figure typed, still comparable."""
    quotes = [
        quote(
            supplier_id=2,
            pack_spec_source="trade_item_confirmed",
            base_per_pack_stated=None,
            item_id=12,
        )
    ]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers(), items_by_id={12: item_144})
    assert comparison.comparable_count == 1
    assert comparison.comparable[0].figures["usd_per_base_unit"].amount == Decimal("50.00") / Decimal("144")


def test_the_snapshot_is_json_serialisable_and_keeps_the_reasons(rutf, round_2000_cartons):
    import json

    quotes = [quote(supplier_id=2, pack_spec_source="not_stated", base_per_pack_stated=None)]
    snapshot = compare_round(round_2000_cartons, rutf, quotes, suppliers()).to_snapshot()
    text = json.dumps(snapshot)
    assert "pack spec" in text


def test_the_cheaper_landed_total_sorts_first(rutf, round_2000_cartons):
    quotes = [
        quote(supplier_id=1, as_quoted_amount="50.00"),
        quote(supplier_id=2, as_quoted_amount="48.00"),
    ]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())
    assert comparison.ranked_by == "landed_total_for_round_quantity"
    assert [r.supplier_name for r in comparison.comparable] == [
        "Harmattan Foods",
        "Northwind Nutrition",
    ]


def test_ranked_by_is_none_when_the_round_has_no_line_for_the_commodity(rutf):
    """Ruling 22: the old "usd_per_pack_normalized" fallback was dead code --
    a round with no line for this commodity makes landed_total_for_round_quantity
    Unconfirmed for every quote, so nothing is ever comparable in this case and
    the fallback key never actually ranked anything. Nothing comparable means
    nothing to rank by: ranked_by is None, not a quieter figure standing in."""
    round_no_line = wrap(Round, {"lines": []})
    comparison = compare_round(round_no_line, rutf, [quote(supplier_id=1)], suppliers())
    assert comparison.comparable_count == 0
    assert comparison.ranked_by is None


def test_provisional_is_true_when_anything_is_blocked(rutf, round_2000_cartons):
    quotes = [
        quote(supplier_id=1, as_quoted_amount="50.00"),
        quote(supplier_id=2, pack_spec_source="not_stated", base_per_pack_stated=None),
    ]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())
    assert comparison.provisional is True


def test_provisional_is_false_when_nothing_is_blocked(rutf, round_2000_cartons):
    quotes = [
        quote(supplier_id=1, as_quoted_amount="50.00"),
        quote(supplier_id=2, as_quoted_amount="52.00"),
    ]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())
    assert comparison.provisional is False


def test_ranked_by_and_provisional_are_present_in_the_snapshot(rutf, round_2000_cartons):
    quotes = [
        quote(supplier_id=1, as_quoted_amount="50.00"),
        quote(supplier_id=2, pack_spec_source="not_stated", base_per_pack_stated=None),
    ]
    snapshot = compare_round(round_2000_cartons, rutf, quotes, suppliers()).to_snapshot()
    assert snapshot["ranked_by"] == "landed_total_for_round_quantity"
    assert snapshot["provisional"] is True


def test_blocked_by_dedupes_a_supplier_with_two_blocked_quotes(rutf, round_2000_cartons):
    quotes = [
        quote(supplier_id=2, pack_spec_source="not_stated", base_per_pack_stated=None),
        quote(supplier_id=2, pack_spec_source="not_stated", base_per_pack_stated=None),
    ]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())
    column = _column(comparison, "usd_per_base_unit")
    assert column.blocked_by.count("Harmattan Foods") == 1


def test_our_own_missing_ration_table_does_not_block_a_supplier(rutf_without_course, round_2000_cartons):
    """A supplier who answered every question we could ask used to come back
    BLOCKED, because comparability was gated on cost per course -- which needs
    the commodity's ration table, our treatment protocol, not anything a
    supplier states. They had no way to close that gap, and the only
    outstanding question on the row had audience `internal`.

    Measured before the fix: a fully specified quote against a commodity with
    no ration table reported 0 of 1 comparable.
    """
    comparison = compare_round(round_2000_cartons, rutf_without_course, [quote(supplier_id=1)], suppliers())

    assert comparison.comparable_count == 1, "a perfect quote was blocked by our own gap"
    assert comparison.blocked == []
    assert comparison.provisional is False


def test_the_uncomputable_columns_are_reported_once_as_ours(rutf_without_course, round_2000_cartons):
    """The signal is not lost, only re-attributed: a figure no row can compute
    is stated at the top as our gap, instead of appearing as every supplier's
    fault."""
    comparison = compare_round(round_2000_cartons, rutf_without_course, [quote(supplier_id=1)], suppliers())

    assert set(comparison.unavailable) == {"usd_per_course", "usd_per_child_treated"}
    reasons = comparison.unavailable["usd_per_course"]["reasons"]
    assert any("course definition" in reason for reason in reasons)
    assert comparison.to_snapshot()["unavailable"], "the frozen snapshot should carry it too"


def test_a_figure_missing_on_only_one_row_is_that_suppliers_gap_not_ours(rutf, round_2000_cartons):
    """The distinction the two reports rest on. One supplier's silence is
    theirs; a figure nobody can compute is ours."""
    quotes = [quote(supplier_id=1), quote(supplier_id=2, pack_spec_source="not_stated", base_per_pack_stated=None)]
    comparison = compare_round(round_2000_cartons, rutf, quotes, suppliers())

    assert comparison.comparable_count == 1
    assert len(comparison.blocked) == 1
    assert comparison.unavailable == {}, "a per-supplier gap was reported as ours"


def test_a_column_nobody_can_compute_is_not_rankable(rutf_without_course, round_2000_cartons):
    """`rankable` used to be "is anything comparable", so a column no row
    could compute still claimed to be sortable -- offering a sort that
    silently does nothing."""
    comparison = compare_round(round_2000_cartons, rutf_without_course, [quote(supplier_id=1)], suppliers())

    by_key = {c.key: c for c in comparison.columns}
    assert by_key["landed_total_for_round_quantity"].rankable is True
    assert by_key["usd_per_course"].rankable is False
    assert by_key["usd_per_child_treated"].rankable is False
