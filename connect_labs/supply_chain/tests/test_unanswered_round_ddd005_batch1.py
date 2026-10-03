"""DDD 005 batch 1: the round's buyer-import duty terms reach only quotes we import.

A DDP quote is the supplier's import: its own duty figure stands under any round terms,
and it is never asked to restate its price without duty. The comparison groups a quote
held only by the round's own decision apart from "Needs info", and a per-base-unit price
in a foreign currency already reads per pack when the pack is stated.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from connect_labs.supply_chain.procurement.services.comparison import (
    landed_basis_words,
    per_pack_note,
    round_duty_words,
)
from connect_labs.supply_chain.procurement.services.pricing import _extras, buyer_imports, round_duty_applies
from connect_labs.supply_chain.procurement.services.questions import needs_duty_restated
from connect_labs.supply_chain.templatetags.supply_chain_extras import owes_supplier_facts, waits_only_on_round
from connect_labs.supply_chain.values import Money, Unconfirmed


def _quote(incoterm, *, duties_basis="", duties_amount=None):
    return SimpleNamespace(
        incoterm=incoterm,
        duties_basis=duties_basis,
        duties_amount=duties_amount,
        freight_basis="included",
        freight_amount=None,
        delivery_mode="delivered",
        delivery_point_keys=[],
        as_quoted_currency="USD",
        tender_id=None,
    )


def _round(terms, estimate=None):
    return SimpleNamespace(duty_terms=terms, duty_estimate_percent=estimate, delivery_points=[])


@pytest.mark.parametrize("terms", ["buyer_waiver", "buyer_pays"])
def test_a_ddp_quote_keeps_its_own_duty_under_buyer_import_terms(terms):
    ddp = _quote("DDP Kano", duties_basis="included")
    tender = _round(terms, Decimal("5"))
    assert not buyer_imports(ddp)
    assert not round_duty_applies(ddp, tender)
    assert not needs_duty_restated(ddp, tender)
    assert round_duty_words(ddp, tender) == ""
    basis = landed_basis_words(ddp, tender)
    assert "duties included" in basis
    assert "waived" not in basis and "our duty estimate" not in basis
    assert _extras(ddp, tender) == Money(Decimal("0"))


def test_a_ddp_quote_with_a_stated_duty_amount_keeps_that_amount_under_the_waiver():
    ddp = _quote("DDP Kano", duties_basis="excluded", duties_amount=Decimal("125.00"))
    # Excluded under DDP is the costly contradiction, reported -- never silently waived.
    assert isinstance(_extras(ddp, _round("buyer_waiver")), Unconfirmed)


def test_a_buyer_import_quote_saying_duty_included_is_still_asked_to_restate():
    cpt = _quote("CPT Kano", duties_basis="included")
    assert needs_duty_restated(cpt, _round("buyer_waiver"))
    assert round_duty_words(_quote("EXW Niamey"), _round("buyer_waiver")) == "waived (our import)"


def test_rows_held_only_by_the_rounds_decision_are_grouped_apart():
    round_only = {"blockers": [{"question": {"key": "duty_terms", "audience": "internal"}}]}
    owes = {
        "blockers": [
            {"question": {"key": "duty_terms", "audience": "internal"}},
            {"question": {"key": "freight_amount", "audience": "supplier"}},
        ]
    }
    rows = [round_only, owes]
    assert waits_only_on_round(rows) == [round_only]
    assert owes_supplier_facts(rows) == [owes]


def test_a_foreign_currency_price_reads_per_pack_once_the_pack_is_stated():
    fx = {"usd_per_pack_normalized": SimpleNamespace(amount=None, reasons=("Quote is in EUR and no exchange rate",))}
    note = per_pack_note(fx, "sachet", "carton", quoted_per_pack="EUR 46.50 per carton (150 sachets)")
    assert note == "= EUR 46.50 per carton (150 sachets); in USD once an exchange rate is recorded"
