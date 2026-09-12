from connect_labs.supply_chain.models import CommodityRecord
from connect_labs.supply_chain.procurement.services.compliance import check_compliance
from connect_labs.supply_chain.tests.conftest import quote, wrap


def scale(requirements):
    return wrap(
        CommodityRecord,
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
    from connect_labs.supply_chain.models import ItemRecord

    return wrap(
        ItemRecord,
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
