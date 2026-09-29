"""A rule's lines against one visit's answers. Pure: no database.

THIS REPOSITORY IS PUBLIC. Every answer here is invented.
"""

from decimal import Decimal

import pytest

from connect_labs.supply_chain.models import Commodity, Item
from connect_labs.supply_chain.stock.services.dispensing import (
    DISPENSED,
    NO_ANSWER,
    NOT_APPLICABLE,
    NOTHING_GIVEN,
    UNIT_REFUSED,
    UNMAPPED,
    evaluate,
    read_number,
)

SCREENING = "http://openrosa.org/formdesigner/screening-invented"
VISIT = "http://openrosa.org/formdesigner/visit-invented"


def _item(base_per_pack=150, unit="sachet"):
    commodity = Commodity(
        scope_key="prog:1", slug="x", name="X", base_unit=unit, pack_unit="carton", base_per_pack=base_per_pack
    )
    return Item(
        scope_key="prog:1",
        sku="x",
        name="X 150",
        commodity=commodity,
        base_unit=unit,
        pack_unit="carton",
        base_per_pack=base_per_pack,
    )


RUTF = [
    {
        "kind": "stated",
        "paths": ["form.screening_outcome.rutf_stock_deduction"],
        "unit": "sachet",
        "forms": [SCREENING],
    },
    {"kind": "stated", "paths": ["form.rutf_dispensing.rutf_sachets_dispensed"], "unit": "sachet", "forms": [VISIT]},
    {"kind": "stated", "paths": ["form.var.appetite_test_stock_deduction"], "unit": "sachet", "forms": [VISIT]},
]
VITA = [
    {
        "kind": "protocol",
        "given_paths": ["form.vita_group.va_delivered"],
        "given_values": ["child_fine"],
        "requires_paths": ["form.prepare_vita_dosage.va_eligible_dose_6mo_to_11mo"],
        "quantity": "1",
        "unit": "capsule",
    }
]
AMOX = [
    {
        "kind": "value_map",
        "paths": ["form.visit_1.dosage_pneumonia", "form.visit_2_or_greater.dosage_pneumonia"],
        "map": {"1 tablet every 12 hours (total 10 tablets)": 10, "2 tablets every 12 hours(total 20 tablets)": 20},
        "unit": "tablet",
    }
]
MRDT = [
    {
        "kind": "protocol",
        "given_paths": ["form.fever.mrdt_result"],
        "given_values": None,
        "quantity": "1",
        "unit": "sachet",
    }
]


def form(xmlns=None, **answers):
    """{"form": {...}}; keys use __ for dots."""
    root: dict = {}
    if xmlns:
        root["@xmlns"] = xmlns
    for key, value in answers.items():
        node = root
        parts = key.split("__")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return {"id": "xf-1", "form": root}


def test_visit_form_sums_ration_and_appetite_test():
    result = evaluate(
        RUTF,
        form(VISIT, rutf_dispensing__rutf_sachets_dispensed="14", var__appetite_test_stock_deduction="0"),
        _item(),
    )
    assert (result.outcome, result.quantity, result.unit, result.estimated) == (
        DISPENSED,
        Decimal("14"),
        "sachet",
        False,
    )
    assert result.answers == {
        "form.rutf_dispensing.rutf_sachets_dispensed": "14",
        "form.var.appetite_test_stock_deduction": "0",
    }


def test_screening_reads_only_its_own_total():
    # The visit-form answers are present too, but the Screening form must not read them.
    result = evaluate(
        RUTF,
        form(SCREENING, screening_outcome__rutf_stock_deduction="14", rutf_dispensing__rutf_sachets_dispensed="14"),
        _item(),
    )
    assert result.quantity == Decimal("14")


def test_a_form_no_line_applies_to_is_not_applicable():
    result = evaluate(RUTF, form("http://openrosa.org/formdesigner/other", var__x="1"), _item())
    assert result.outcome == NOT_APPLICABLE


def test_form_matches_by_name_and_rule_default_forms():
    data = {"form": {"@name": "Visit Form", "rutf_dispensing": {"rutf_sachets_dispensed": "3"}}}
    lines = [{"kind": "stated", "paths": ["form.rutf_dispensing.rutf_sachets_dispensed"], "unit": "sachet"}]
    assert evaluate(lines, data, _item(), rule_forms=["Visit Form"]).quantity == Decimal("3")
    assert evaluate(lines, data, _item(), rule_forms=["Screening"]).outcome == NOT_APPLICABLE


def test_a_stated_line_answered_blank_or_absent_is_no_answer():
    assert evaluate(RUTF, form(VISIT), _item()).outcome == NO_ANSWER
    assert evaluate(RUTF, form(VISIT, rutf_dispensing__rutf_sachets_dispensed=" "), _item()).outcome == NO_ANSWER


def test_appetite_test_alone_stands_when_no_ration_was_dispensed():
    result = evaluate(RUTF, form(VISIT, var__appetite_test_stock_deduction="1"), _item())
    assert (result.outcome, result.quantity) == (DISPENSED, Decimal("1"))


@pytest.mark.parametrize("answer", ["two", "-3", "NaN", "Infinity", True])
def test_an_unreadable_answer_is_unknown_not_zero(answer):
    result = evaluate(
        RUTF,
        form(VISIT, rutf_dispensing__rutf_sachets_dispensed=answer, var__appetite_test_stock_deduction="1"),
        _item(),
    )
    assert result.outcome == NO_ANSWER
    assert "form.rutf_dispensing.rutf_sachets_dispensed" in result.reasons[0]


def test_zero_answered_is_nothing_given():
    assert (
        evaluate(
            RUTF,
            form(VISIT, rutf_dispensing__rutf_sachets_dispensed="0", var__appetite_test_stock_deduction="0"),
            _item(),
        ).outcome
        == NOTHING_GIVEN
    )


def test_protocol_given_is_estimated():
    result = evaluate(
        VITA,
        form(vita_group__va_delivered="child_fine", prepare_vita_dosage__va_eligible_dose_6mo_to_11mo="x"),
        _item(unit="capsule"),
    )
    assert (result.outcome, result.quantity, result.unit, result.estimated) == (
        DISPENSED,
        Decimal("1"),
        "capsule",
        True,
    )


def test_multi_select_matches_any_token():
    result = evaluate(
        VITA,
        form(vita_group__va_delivered="referred child_fine", prepare_vita_dosage__va_eligible_dose_6mo_to_11mo="x"),
        _item(unit="capsule"),
    )
    assert result.outcome == DISPENSED


def test_given_something_else_is_nothing_given():
    result = evaluate(
        VITA,
        form(vita_group__va_delivered="child_unwell", prepare_vita_dosage__va_eligible_dose_6mo_to_11mo="x"),
        _item(unit="capsule"),
    )
    assert (result.outcome, result.quantity) == (NOTHING_GIVEN, Decimal("0"))


def test_an_absent_given_path_is_not_given_not_no_answer():
    assert evaluate(VITA, form(), _item()).outcome == NOTHING_GIVEN


def test_requires_paths_must_all_be_answered():
    assert evaluate(VITA, form(vita_group__va_delivered="child_fine"), _item()).outcome == NOTHING_GIVEN


def test_any_answer_counts_when_no_given_values():
    assert evaluate(MRDT, form(fever__mrdt_result="invalid"), _item()).quantity == Decimal("1")


def test_value_map_looks_the_answer_up_exactly_and_is_estimated():
    answer = "2 tablets every 12 hours(total 20 tablets)"
    result = evaluate(AMOX, form(visit_2_or_greater__dosage_pneumonia=f" {answer} "), _item(unit="tablet"))
    assert (result.outcome, result.quantity, result.estimated) == (DISPENSED, Decimal("20"), True)


def test_value_map_first_path_wins():
    result = evaluate(
        AMOX,
        form(
            visit_1__dosage_pneumonia="1 tablet every 12 hours (total 10 tablets)",
            visit_2_or_greater__dosage_pneumonia="2 tablets every 12 hours(total 20 tablets)",
        ),
        _item(unit="tablet"),
    )
    assert result.quantity == Decimal("10")


def test_value_map_unseen_answer_is_unmapped_and_carries_it():
    result = evaluate(AMOX, form(visit_1__dosage_pneumonia="3 tablets daily"), _item(unit="tablet"))
    assert result.outcome == UNMAPPED
    assert result.unmapped == (("form.visit_1.dosage_pneumonia", "3 tablets daily"),)
    assert result.quantity == Decimal("0")


def test_value_map_absent_is_not_given():
    assert evaluate(AMOX, form(), _item(unit="tablet")).outcome == NOTHING_GIVEN


def test_lines_in_cartons_are_counted_in_sachets():
    lines = [{"kind": "stated", "paths": ["form.cartons"], "unit": "carton"}]
    assert evaluate(lines, form(cartons="1"), _item()).quantity == Decimal("150")


def test_a_unit_the_item_cannot_convert_is_refused():
    lines = [{"kind": "stated", "paths": ["form.cartons"], "unit": "carton"}]
    result = evaluate(lines, form(cartons="1"), _item(base_per_pack=None))
    assert result.outcome == UNIT_REFUSED
    assert result.reasons


@pytest.mark.parametrize(
    "value,expected",
    [
        ("14", Decimal("14")),
        (3, Decimal("3")),
        ("0.5", Decimal("0.5")),
        (None, None),
        (True, None),
        ("1e999", None),
        (" 2 ", Decimal("2")),
        ("", None),
    ],
)
def test_read_number(value, expected):
    assert read_number(value) == expected
