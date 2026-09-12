from decimal import Decimal

from connect_labs.supply_chain.models import RoundRecord, SupplierRecord
from connect_labs.supply_chain.procurement.services.comparison import compare_round
from connect_labs.supply_chain.tests.conftest import quote, wrap
from connect_labs.supply_chain.values import Unconfirmed


def suppliers():
    return {
        1: wrap(SupplierRecord, {"name": "Northwind Nutrition"}, record_id=1),
        2: wrap(SupplierRecord, {"name": "Harmattan Foods"}, record_id=2),
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


def test_ranked_by_falls_back_to_per_pack_when_the_round_has_no_line_for_the_commodity(rutf):
    round_no_line = wrap(RoundRecord, {"lines": []})
    comparison = compare_round(round_no_line, rutf, [quote(supplier_id=1)], suppliers())
    assert comparison.ranked_by == "usd_per_pack_normalized"


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
