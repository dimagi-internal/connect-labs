from connect_labs.supply_chain.models import Commodity
from connect_labs.supply_chain.procurement.services.compliance import check_compliance, spec_verdict
from connect_labs.supply_chain.tests.conftest import quote, wrap


def scale(requirements):
    return wrap(
        Commodity,
        {
            "slug": "infant-scale",
            "name": "Infant scale",
            "category": "equipment",
            "base_unit": "unit",
            "pack_unit": "box",
            "spec_requirements": requirements,
        },
    )


GRADUATION = {
    "field": "minimum_graduation_g",
    "operator": "<=",
    "value": 20,
    "unit": "g",
    "rationale": "100 g increments cannot record infant weight change",
}


def test_a_conforming_spec_passes():
    results = check_compliance(quote(stated_spec={"minimum_graduation_g": 10}), scale([GRADUATION]))
    assert [r.outcome for r in results] == ["pass"]


def test_a_coarse_scale_fails_and_says_why():
    results = check_compliance(quote(stated_spec={"minimum_graduation_g": 100}), scale([GRADUATION]))
    assert results[0].outcome == "fail"
    assert "100 g increments cannot record infant weight change" in results[0].message


def test_a_silent_quote_is_not_stated_rather_than_failing():
    results = check_compliance(quote(stated_spec={}), scale([GRADUATION]))
    assert results[0].outcome == "not_stated"
    assert results[0].spec_origin == "none"


def _scale_item(graduation):
    from connect_labs.supply_chain.models import Item

    return wrap(
        Item,
        {
            "sku": "bx-10",
            "name": "BX-10 infant scale",
            "commodity_slug": "infant-scale",
            "spec_attributes": {"minimum_graduation_g": graduation},
        },
        record_id=21,
    )


def test_the_item_spec_sheet_answers_a_quote_that_was_silent():
    """A confirmed item's specification is durable fact; use it."""
    results = check_compliance(quote(stated_spec={}), scale([GRADUATION]), item=_scale_item(10))
    assert results[0].outcome == "pass"
    assert results[0].spec_origin == "item"


def test_the_item_spec_sheet_beats_the_supplier_claim_and_flags_the_conflict():
    """The supplier claims 10 g; the item sheet says 100 g. Believe the sheet, and say so."""
    results = check_compliance(
        quote(stated_spec={"minimum_graduation_g": 10}),
        scale([GRADUATION]),
        item=_scale_item(100),
    )
    assert results[0].outcome == "fail"
    assert results[0].spec_origin == "item"
    assert results[0].claim_conflict is True


def test_agreement_between_item_and_quote_is_not_a_conflict():
    results = check_compliance(
        quote(stated_spec={"minimum_graduation_g": 10}),
        scale([GRADUATION]),
        item=_scale_item(10),
    )
    assert results[0].claim_conflict is False


def test_a_commodity_with_no_requirements_yields_no_results(rutf):
    assert check_compliance(quote(), rutf) == []


def test_an_unparseable_stated_value_is_not_stated():
    results = check_compliance(
        quote(stated_spec={"minimum_graduation_g": "about a tenth of a kilo"}), scale([GRADUATION])
    )
    assert results[0].outcome == "not_stated"


def test_an_unknown_operator_raises_rather_than_passing_silently():
    import pytest

    bad = dict(GRADUATION, operator="approximately")
    with pytest.raises(ValueError):
        check_compliance(quote(stated_spec={"minimum_graduation_g": 10}), scale([bad]))


# --- Finding 15: item master's "spec verdict" column ------------------------


def test_spec_verdict_with_no_requirements():
    assert spec_verdict({"minimum_graduation_g": 10}, []) == "No requirements"


def test_spec_verdict_when_the_item_meets_every_requirement():
    assert spec_verdict({"minimum_graduation_g": 10}, [GRADUATION]) == "Meets all 1"


def test_spec_verdict_when_the_item_fails_a_requirement():
    assert spec_verdict({"minimum_graduation_g": 100}, [GRADUATION]) == "1 of 1 fail"


def test_spec_verdict_when_the_item_never_states_the_attribute():
    assert spec_verdict({}, [GRADUATION]) == "1 of 1 not stated"


def test_spec_verdict_treats_none_attributes_as_empty():
    assert spec_verdict(None, [GRADUATION]) == "1 of 1 not stated"


def test_spec_verdict_does_not_raise_on_an_unrecognised_operator():
    """Unlike check_compliance, this renders a read-only table cell -- a bad
    operator on one commodity must not 500 the whole item list."""
    bad = dict(GRADUATION, operator="approximately")
    assert spec_verdict({"minimum_graduation_g": 10}, [bad]) == "1 of 1 not stated"
