from connect_labs.supply_chain.procurement.services.questions import initial_request_facts, missing_facts
from connect_labs.supply_chain.tests.conftest import quote


def test_a_comparable_quote_is_missing_nothing(comparable_quote, rutf, round_2000_cartons):
    assert missing_facts(comparable_quote, rutf, round_2000_cartons) == []


def test_a_silent_quote_is_missing_the_pack_spec_and_the_bases(rutf, round_2000_cartons):
    q = quote(
        pack_spec_source="not_stated",
        base_per_pack_stated=None,
        freight_basis="not_specified",
        duties_basis="not_specified",
    )
    keys = [f.key for f in missing_facts(q, rutf, round_2000_cartons)]
    assert "pack_spec" in keys
    assert "freight_basis" in keys
    assert "duties_basis" in keys


def test_each_missing_fact_is_named_once_even_if_several_figures_block_on_it(rutf, round_2000_cartons):
    q = quote(pack_spec_source="not_stated", base_per_pack_stated=None)
    keys = [f.key for f in missing_facts(q, rutf, round_2000_cartons)]
    assert keys.count("pack_spec") == 1


def test_missing_facts_read_as_questions_to_a_supplier(rutf, round_2000_cartons):
    q = quote(pack_spec_source="not_stated", base_per_pack_stated=None)
    fact = next(f for f in missing_facts(q, rutf, round_2000_cartons) if f.key == "pack_spec")
    assert "?" in fact.question


def test_a_not_stated_spec_requirement_becomes_a_question(round_2000_cartons):
    from connect_labs.supply_chain.models import CommodityRecord
    from connect_labs.supply_chain.tests.conftest import wrap

    scale = wrap(
        CommodityRecord,
        {
            "slug": "infant-scale",
            "name": "Infant scale",
            "base_unit": "unit",
            "pack_unit": "box",
            "spec_requirements": [
                {
                    "field": "minimum_graduation_g",
                    "operator": "<=",
                    "value": 20,
                    "unit": "g",
                    "rationale": "infant weight change",
                }
            ],
        },
    )
    q = quote(commodity_slug="infant-scale", stated_spec={})
    keys = [f.key for f in missing_facts(q, scale, round_2000_cartons)]
    assert "spec:minimum_graduation_g" in keys


def test_the_initial_request_asks_for_everything(rutf, round_2000_cartons):
    keys = [f.key for f in initial_request_facts(rutf, round_2000_cartons)]
    for expected in ("pack_spec", "freight_basis", "duties_basis", "shelf_life", "moq", "lead_time"):
        assert expected in keys


def test_the_initial_request_quotes_the_round_quantity_and_destination(rutf, round_2000_cartons):
    text = " ".join(f.question for f in initial_request_facts(rutf, round_2000_cartons))
    assert "2000" in text
    assert "Kano" in text


# Not in the brief. Task 4's fix round added a _base_unit_grams helper whose
# Unconfirmed reason ("unit weight not stated on the quote...") the brief's
# _REASON_QUESTIONS table never listed a fragment for — meaning a real missing
# fact (the supplier never stated the weight of one base unit) would silently
# produce no question at all. This pins the fix: a per-metric-tonne quote with
# no stated weight and no confirming item must surface an "unit_weight" fact.
def test_a_metric_tonne_quote_with_no_stated_weight_is_missing_the_unit_weight(rutf, round_2000_cartons):
    q = quote(
        as_quoted_unit="per_metric_tonne",
        quantity_basis="1",
        quantity_basis_unit="metric_tonne",
        base_unit_grams_stated=None,
    )
    keys = [f.key for f in missing_facts(q, rutf, round_2000_cartons)]
    assert "unit_weight" in keys
