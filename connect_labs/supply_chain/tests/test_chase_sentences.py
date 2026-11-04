"""Every sentence on the chase list names who or what it is about (Overview review, S2)."""

import pytest

from connect_labs.supply_chain.portfolio.chase import ASK, _who, ask_for, chase_items


def _check(kind, audience="internal", label="flw_007", facts=None):
    return {
        "kind": kind,
        "audience": audience,
        "subject": {"type": "x", "id": 1, "label": label},
        "facts": facts or {},
        "days_open": 3,
    }


def _items(check):
    return chase_items(
        program_id=1,
        program_name="P",
        checks=[check],
        consignments=[],
        orders=[],
        suppliers={},
        places={},
        check_labels={},
    )


@pytest.mark.parametrize("kind", ["stock_variance", "stock_negative", "stock_never_reported"])
def test_a_stock_sentence_names_the_worker(kind):
    (item,) = _items(_check(kind))
    assert "flw_007" in item["ask"]


def test_the_old_blank_text_is_gone():
    (item,) = _items(_check("stock_variance"))
    assert item["ask"] == "Check the count with flw_007: it disagrees with the records"


def test_an_open_commitment_names_who_it_is_owed_to():
    check = _check("commitment_open", label="Acme — send the sample", facts={"owed_to": {"name": "Acme"}})
    (item,) = _items(check)
    assert item["ask"] == "Send Acme the answer we owe"


@pytest.mark.parametrize("kind", [k for k, v in ASK.items() if "{who}" in v])
@pytest.mark.parametrize("audience", ["supplier", "partner", "internal"])
def test_no_sentence_has_a_blank_subject(kind, audience):
    sentence = ask_for(kind, audience, _who(_check(kind, audience, label="Thing")), "label")
    assert "  " not in sentence and " :" not in sentence and "{" not in sentence
    assert "with :" not in sentence and not sentence.startswith(("Ask for", "Send the answer"))
