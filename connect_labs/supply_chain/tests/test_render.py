from connect_labs.supply_chain.models import SupplierRecord
from connect_labs.supply_chain.procurement.services.render import render_followup, render_initial_request
from connect_labs.supply_chain.tests.conftest import quote, wrap


def supplier():
    return wrap(SupplierRecord, {"name": "Northwind Nutrition", "country": "KE"})


def test_the_initial_request_states_quantity_destination_and_every_question(rutf, round_2000_cartons):
    text = render_initial_request(rutf, round_2000_cartons, supplier())
    assert "Northwind Nutrition" in text
    assert "2,000" in text
    assert "Kano" in text
    assert "minimum order quantity" in text
    assert "shelf life" in text.lower()


def test_the_initial_request_numbers_its_questions(rutf, round_2000_cartons):
    text = render_initial_request(rutf, round_2000_cartons, supplier())
    assert "1." in text and "2." in text


def test_a_followup_asks_only_what_is_missing(rutf, round_2000_cartons):
    q = quote(pack_spec_source="not_stated", base_per_pack_stated=None, shelf_life_months_stated=24)
    text = render_followup(q, rutf, round_2000_cartons, supplier())
    assert "how many sachets" in text.lower()
    assert "minimum order quantity" not in text


def test_a_followup_on_a_complete_quote_says_nothing_is_outstanding(comparable_quote, rutf, round_2000_cartons):
    q = quote(shelf_life_months_stated=24)
    text = render_followup(q, rutf, round_2000_cartons, supplier())
    assert "nothing outstanding" in text.lower()


def test_a_followup_does_not_ask_for_a_pack_spec_the_item_already_states(rutf, round_2000_cartons, item_144):
    """A supplier who named their trade item has already answered that question."""
    q = quote(
        pack_spec_source="trade_item_confirmed",
        base_per_pack_stated=None,
        item_id=12,
        shelf_life_months_stated=24,
    )
    text = render_followup(q, rutf, round_2000_cartons, supplier(), item=item_144)
    assert "nothing outstanding" in text.lower()


def test_a_followup_never_asks_a_supplier_to_enter_our_treatment_protocol(rutf_without_course, round_2000_cartons):
    """The scenario the audience amendment exists for: missing course_definition
    is a real gap (test_questions.py's test_a_fact_only_we_can_fix_is_tagged_internal_
    not_emailed_to_the_supplier pins it as audience="internal"), but it is ours to
    fix, not the supplier's, and must never reach an email to a manufacturer.
    """
    q = quote(shelf_life_months_stated=24)
    text = render_followup(q, rutf_without_course, round_2000_cartons, supplier())
    assert "treatment protocol" not in text.lower()
    assert "nothing outstanding" in text.lower()
