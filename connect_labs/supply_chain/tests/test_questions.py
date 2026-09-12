import logging

from connect_labs.supply_chain.procurement.services.questions import initial_request_facts, missing_facts
from connect_labs.supply_chain.tests.conftest import quote, wrap


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
    assert "2,000" in text
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


# --- Ruling 1: acceptance facts (shelf life, MOQ, lead time, validity) ------
#
# These block no derived figure, so pricing.py/compliance.py never surface
# their absence — missing_facts has to check the quote directly. Restored
# after the first pass wrongly deleted the check instead of fixing the
# fixture that made it look wrong (conftest.quote() now states all four by
# default, so "comparable" is honestly comparable AND acceptable).


def test_a_quote_silent_on_shelf_life_is_missing_it(rutf, round_2000_cartons):
    q = quote(shelf_life_months_stated=None)
    keys = [f.key for f in missing_facts(q, rutf, round_2000_cartons)]
    assert "shelf_life" in keys


def test_the_shelf_life_question_omits_the_broken_clause_when_no_minimum_is_set(rutf_without_course):
    """Finding 8: when neither the round nor the commodity carries
    shelf_life_months_minimum, _context()'s "" fallback used to put a
    broken sentence -- "We need at least  months." -- straight into the
    RFQ. Neither this round nor rutf_without_course sets a minimum."""
    from connect_labs.supply_chain.models import RoundRecord

    round_no_minimum = wrap(RoundRecord, {"lines": [], "delivery_point": {"city": "Kano", "country": "NG"}})
    fact = next(f for f in initial_request_facts(rutf_without_course, round_no_minimum) if f.key == "shelf_life")
    assert "We need at least" not in fact.question
    assert "  " not in fact.question
    assert "shelf life" in fact.question.lower()


def test_the_shelf_life_question_states_the_minimum_when_one_exists(rutf, round_2000_cartons):
    fact = next(f for f in initial_request_facts(rutf, round_2000_cartons) if f.key == "shelf_life")
    assert "We need at least 18 months." in fact.question


def test_a_quote_silent_on_moq_is_missing_it(rutf, round_2000_cartons):
    q = quote(moq=None, moq_unit=None)
    keys = [f.key for f in missing_facts(q, rutf, round_2000_cartons)]
    assert "moq" in keys


def test_a_quote_silent_on_lead_time_is_missing_it(rutf, round_2000_cartons):
    q = quote(lead_time_days=None)
    keys = [f.key for f in missing_facts(q, rutf, round_2000_cartons)]
    assert "lead_time" in keys


def test_a_quote_silent_on_validity_is_missing_it(rutf, round_2000_cartons):
    q = quote(validity_until=None)
    keys = [f.key for f in missing_facts(q, rutf, round_2000_cartons)]
    assert "validity" in keys


def test_acceptance_facts_do_not_reappear_once_stated(comparable_quote, rutf, round_2000_cartons):
    keys = [f.key for f in missing_facts(comparable_quote, rutf, round_2000_cartons)]
    assert "shelf_life" not in keys
    assert "moq" not in keys
    assert "lead_time" not in keys
    assert "validity" not in keys


# --- Ruling 2: audience, and no reason reaches the mapping unmatched -------


def test_a_supplier_question_defaults_to_the_supplier_audience(rutf, round_2000_cartons):
    q = quote(pack_spec_source="not_stated", base_per_pack_stated=None)
    fact = next(f for f in missing_facts(q, rutf, round_2000_cartons) if f.key == "pack_spec")
    assert fact.audience == "supplier"


def test_a_fact_only_we_can_fix_is_tagged_internal_not_emailed_to_the_supplier(
    rutf_without_course, round_2000_cartons
):
    """Missing the treatment protocol is real, but no supplier reply can supply
    it — it must still be named (not silently dropped), tagged so a renderer
    can leave it out of anything sent externally.
    """
    q = quote()
    facts = missing_facts(q, rutf_without_course, round_2000_cartons)
    fact = next(f for f in facts if f.key == "course_definition")
    assert fact.audience == "internal"


def test_no_pricing_reason_reaches_the_mapping_unmatched(rutf, rutf_without_course, round_2000_cartons, caplog):
    """Every Unconfirmed reason pricing.compute_figures can currently produce
    must match some fragment in _REASON_QUESTIONS — as a supplier fact or an
    internal one. An unmatched reason is a real missing fact nobody is ever
    told about, logged as a warning so it cannot pass silently; this walks
    every reason-producing path in pricing.py and asserts none of them do.
    """
    from connect_labs.supply_chain.models import CommodityRecord
    from connect_labs.supply_chain.tests.conftest import wrap

    unlisted_commodity = wrap(
        CommodityRecord,
        {
            "slug": "unlisted-commodity",
            "name": "Unlisted commodity",
            "base_unit": "unit",
            "pack_unit": "pack",
            "base_per_pack": 10,
            "base_unit_grams": 10,
            "course_definition": {"base_units_per_course": 1},
        },
    )

    broken = [
        (quote(pack_spec_source="not_stated", base_per_pack_stated=None), rutf),
        (
            quote(
                as_quoted_unit="per_metric_tonne",
                quantity_basis="1",
                quantity_basis_unit="metric_tonne",
                base_unit_grams_stated=None,
            ),
            rutf,
        ),
        (quote(as_quoted_currency="EUR", fx_rate_to_usd=None), rutf),
        (quote(freight_basis="not_specified"), rutf),
        (quote(duties_basis="not_specified"), rutf),
        (quote(freight_basis="excluded", freight_amount=None), rutf),
        (quote(duties_basis="excluded", duties_amount=None), rutf),
        (quote(as_quoted_amount=None), rutf),
        (quote(quantity_basis=None), rutf),
        (quote(quantity_basis_unit=None), rutf),
        (quote(quantity_basis_unit="each"), rutf),
        (quote(as_quoted_unit="per_widget"), rutf),
        (quote(quantity_basis="500", quantity_basis_unit="carton"), rutf),
        (quote(pack_spec_source="trade_item_confirmed", base_per_pack_stated=None, item_id=None), rutf),
        (quote(), rutf_without_course),
        (quote(commodity_slug="unlisted-commodity"), unlisted_commodity),
    ]

    with caplog.at_level(logging.WARNING):
        for q, commodity in broken:
            missing_facts(q, commodity, round_2000_cartons)

    unmapped = [r.message for r in caplog.records if "unmapped Unconfirmed reason" in r.message]
    assert unmapped == []


def test_an_unmapped_reason_is_logged_exactly_once_not_once_per_figure(monkeypatch, rutf, round_2000_cartons, caplog):
    """Proves the warning mechanism itself fires, not just that it stays quiet
    on the known set above — by removing the mapping and checking a reason
    that would otherwise vanish is logged.

    An unstated pack spec blocks all six of a quote's QuoteFigures fields
    (usd_per_base_unit, usd_per_pack_normalized, usd_per_course,
    landed_total_as_quoted, landed_total_for_round_quantity,
    usd_per_child_treated all carry the identical reason string), so this
    also pins the fix for the burst-of-identical-WARNINGs bug: one gap must
    log once, not once per figure it happens to block.
    """
    import connect_labs.supply_chain.procurement.services.questions as questions_module

    monkeypatch.setattr(questions_module, "_REASON_QUESTIONS", ())
    q = quote(pack_spec_source="not_stated", base_per_pack_stated=None)

    with caplog.at_level(logging.WARNING):
        facts = missing_facts(q, rutf, round_2000_cartons)

    assert facts == []
    warnings = [r.message for r in caplog.records if "unmapped Unconfirmed reason" in r.message]
    assert len(warnings) == 1, warnings


def test_an_unrecognised_spec_operator_does_not_leak_into_the_question(round_2000_cartons):
    """initial_request_facts calls _spec_fact directly, never through
    check_compliance's own operator guard -- so an operator outside the
    five _spec_fact translates (spec_requirements is unconstrained by
    _COMMODITY_DATA) is reachable here. The raw symbol must never appear in
    a supplier-facing question."""
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
                    "operator": "!=",
                    "value": 20,
                    "unit": "g",
                }
            ],
        },
    )
    fact = next(f for f in initial_request_facts(scale, round_2000_cartons) if f.key == "spec:minimum_graduation_g")
    assert "!=" not in fact.question
    assert "?" in fact.question


def test_no_two_reason_questions_rows_share_a_key():
    """Guards the defect this module's own docstring says it exists to
    prevent: _QUESTION_BY_KEY is built as a dict comprehension over
    _REASON_QUESTIONS, so two rows sharing a key silently collapse to one —
    last-write-wins — and the first becomes unreachable through that index
    even though it still fires from the raw tuple iteration in
    missing_facts. A duplicate key here is exactly "a question string
    written in two places" one level removed.
    """
    from connect_labs.supply_chain.procurement.services.questions import _REASON_QUESTIONS

    keys = [key for _fragment, key, _template, _audience in _REASON_QUESTIONS]
    assert len(keys) == len(set(keys)), f"duplicate keys in _REASON_QUESTIONS: {keys}"
