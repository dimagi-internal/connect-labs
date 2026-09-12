"""Direct tests of procurement's operation handlers -- the layer that has to
validate at least as hard as every other write, and report the same
audience split the comparison snapshot already carries (findings 2 and 5
of the 2026-09-11 final review).
"""

from unittest.mock import MagicMock

import pytest

from connect_labs.supply_chain.procurement.operations import award_create, quote_get, quote_questions
from connect_labs.supply_chain.procurement.services.comparison import compare_round
from connect_labs.supply_chain.proxies import SupplierRecord
from connect_labs.supply_chain.tests.conftest import quote, wrap


def _access_for(round_, commodity, quotes, suppliers=(), quote_by_id=None):
    access = MagicMock()
    access.get_round.return_value = round_
    access.get_commodity.return_value = commodity
    access.list_quotes.return_value = quotes
    access.list_suppliers.return_value = list(suppliers)
    access.items_by_id.return_value = {}
    if quote_by_id is not None:
        access.get_quote.side_effect = lambda qid: quote_by_id.get(qid)
    return access


# --- Finding 2: quote_get and quote_questions must report audience --------


def test_quote_get_reports_the_audience_on_each_missing_fact(rutf, round_2000_cartons):
    q = quote(pack_spec_source="not_stated", base_per_pack_stated=None)
    access = MagicMock()
    access.get_quote.return_value = q
    access.get_round.return_value = round_2000_cartons
    access.get_commodity.return_value = rutf
    access.get_item.return_value = None

    result = quote_get(access, quote_id=q.id)

    assert result["missing"]
    assert all("audience" in fact for fact in result["missing"])
    pack_spec_fact = next(f for f in result["missing"] if f["key"] == "pack_spec")
    assert pack_spec_fact["audience"] == "supplier"


def test_quote_questions_reports_the_audience_on_each_missing_fact(rutf_without_course, round_2000_cartons):
    q = quote()
    access = MagicMock()
    access.get_quote.return_value = q
    access.get_round.return_value = round_2000_cartons
    access.get_commodity.return_value = rutf_without_course
    access.get_item.return_value = None

    result = quote_questions(access, quote_id=q.id)

    assert result
    assert all("audience" in fact for fact in result)
    course_fact = next(f for f in result if f["key"] == "course_definition")
    assert course_fact["audience"] == "internal"


# --- Finding 5: award_create must validate at least as hard as every other
# write, and a later quote must never change an already-frozen snapshot ----


def test_award_create_rejects_a_nonexistent_quote():
    access = MagicMock()
    access.get_quote.return_value = None

    with pytest.raises(ValueError, match="not found"):
        award_create(access, round_id=1, quote_id=999, rationale="cheapest defensible option")

    assert not access.create_award.called


def test_award_create_rejects_a_quote_from_a_different_round(rutf, round_2000_cartons):
    """A quote quoted for round 2 must not be awardable against round 1 --
    the frozen comparison_snapshot is built from round 1's own comparison
    and would never contain the quote it claims to have chosen."""
    q = quote(round_id=2)
    access = _access_for(round_2000_cartons, rutf, [q], quote_by_id={q.id: q})

    with pytest.raises(ValueError, match="belongs to round 2"):
        award_create(access, round_id=1, quote_id=q.id, rationale="cheapest defensible option")

    assert not access.create_award.called


def test_award_create_rejects_a_voided_quote(rutf, round_2000_cartons):
    q = quote(round_id=1, voided=True, void_reason="duplicate")
    access = _access_for(round_2000_cartons, rutf, [q], quote_by_id={q.id: q})

    with pytest.raises(ValueError, match="voided"):
        award_create(access, round_id=1, quote_id=q.id, rationale="cheapest defensible option")

    assert not access.create_award.called


def test_award_create_rejects_a_superseded_quote(rutf, round_2000_cartons):
    q = quote(round_id=1, superseded_by_quote_id=99)
    access = _access_for(round_2000_cartons, rutf, [q], quote_by_id={q.id: q})

    with pytest.raises(ValueError, match="superseded"):
        award_create(access, round_id=1, quote_id=q.id, rationale="cheapest defensible option")

    assert not access.create_award.called


def test_award_create_freezes_a_snapshot_a_later_quote_does_not_retroactively_change(rutf, round_2000_cartons):
    """Design doc section 15: 'an award.comparison_snapshot does not change
    when a later quote lands.' A cheaper quote landing on the same
    round/commodity AFTER the award must not retroactively alter the
    snapshot object already handed to access.create_award."""
    winner = quote(round_id=1, supplier_id=1, as_quoted_amount="50.00")
    northwind = wrap(SupplierRecord, {"name": "Northwind Nutrition"}, record_id=1)
    access = _access_for(round_2000_cartons, rutf, [winner], suppliers=[northwind], quote_by_id={winner.id: winner})

    award_create(access, round_id=1, quote_id=winner.id, rationale="cheapest defensible option")

    frozen = access.create_award.call_args[0][0]["comparison_snapshot"]
    assert frozen["comparable_count"] == 1
    assert frozen["comparable"][0]["supplier_id"] == 1

    # A cheaper quote lands on the same round/commodity after the award --
    # a FRESH compare_round call now ranks it first.
    later = quote(round_id=1, supplier_id=2, as_quoted_amount="1.00")
    fresh = compare_round(round_2000_cartons, rutf, [winner, later], {1: northwind})

    assert fresh.comparable_count == 2
    # The object already frozen and passed to create_award is untouched.
    assert frozen["comparable_count"] == 1
    assert len(frozen["comparable"]) == 1
