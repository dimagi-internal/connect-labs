"""DDD 003 batch 8: under an Incoterm that makes the import ours, duty is the round's to cost, never the
supplier's to state; a document a held shipment needs carries its kind, so it can be provided."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from connect_labs.supply_chain.fulfilment.services.holds import Hold
from connect_labs.supply_chain.procurement.services.comparison import gap_word, round_duty_words
from connect_labs.supply_chain.procurement.services.pricing import ROUND_DUTY_TERMS_REASON, _extras
from connect_labs.supply_chain.procurement.services.questions import (
    INTERNAL,
    audience_for_reason,
    key_for_reason,
)
from connect_labs.supply_chain.values import Money, Unconfirmed


def _quote(incoterm, *, duties_basis="", duties_amount=None, freight_basis="included", freight_amount=None):
    return SimpleNamespace(
        incoterm=incoterm,
        duties_basis=duties_basis,
        duties_amount=duties_amount,
        freight_basis=freight_basis,
        freight_amount=freight_amount,
        delivery_mode="delivered",
        tender_id=None,
    )


UNSETTLED = SimpleNamespace(duty_terms="", duty_estimate_percent=None)


@pytest.mark.parametrize("incoterm", ["EXW Niamey", "FCA Niamey", "FOB Lagos", "CPT Kano", "DAP Kano"])
def test_a_buyer_import_term_with_no_duty_amount_waits_on_the_round(incoterm):
    extras = _extras(_quote(incoterm, freight_amount=Decimal("10")), UNSETTLED)
    assert isinstance(extras, Unconfirmed)
    assert list(extras.reasons) == [ROUND_DUTY_TERMS_REASON]
    # Ours to settle: never a question in the supplier's email.
    assert audience_for_reason(ROUND_DUTY_TERMS_REASON) == INTERNAL
    assert key_for_reason(ROUND_DUTY_TERMS_REASON) == "duty_terms"
    assert gap_word(ROUND_DUTY_TERMS_REASON) == "round duty terms"
    assert round_duty_words(_quote(incoterm), UNSETTLED) == "ours to cost (round terms not settled)"


def test_a_stated_duty_amount_waits_on_the_round_too():
    # DDD 004: one rule. A supplier's stated duty figure never settles a buyer-import quote.
    extras = _extras(_quote("CPT Kano", duties_basis="excluded", duties_amount=Decimal("0")), UNSETTLED)
    assert isinstance(extras, Unconfirmed) and list(extras.reasons) == [ROUND_DUTY_TERMS_REASON]
    assert (
        round_duty_words(_quote("CPT Kano", duties_basis="excluded", duties_amount=Decimal("0")), UNSETTLED)
        == "ours to cost (round terms not settled)"
    )


def test_delivered_duty_paid_and_unknown_terms_still_ask_the_supplier():
    ddp_round = SimpleNamespace(duty_terms="supplier_ddp", duty_estimate_percent=None)
    extras = _extras(_quote("EXW Niamey", freight_amount=Decimal("10")), ddp_round)
    assert isinstance(extras, Unconfirmed) and ROUND_DUTY_TERMS_REASON not in extras.reasons
    # No Incoterm: the quote's own "excluded" is a figure the supplier owes.
    extras = _extras(_quote("", duties_basis="excluded"), UNSETTLED)
    assert ROUND_DUTY_TERMS_REASON not in extras.reasons


def test_the_waiver_still_settles_it():
    waiver = SimpleNamespace(duty_terms="buyer_waiver", duty_estimate_percent=None)
    assert isinstance(_extras(_quote("EXW Niamey", freight_amount=Decimal("10")), waiver), Money)
    assert round_duty_words(_quote("EXW Niamey"), waiver) == "waived (our import)"


def test_a_held_document_carries_its_kind():
    hold = Hold(what="customs declaration", since=None, shipment_id=4, name="PAAR", kind="customs_declaration")
    assert hold.as_dict()["kind"] == "customs_declaration"
