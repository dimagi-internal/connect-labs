"""DDD 004: one duty rule for every buyer-import quote.

Under an Incoterm that puts the import on the buyer, duty is the buyer's cost, set by the
ROUND's duty terms -- never by a figure the supplier wrote. Unsettled terms hold every such
quote alike; the waiver counts zero for every one; buyer-pays adds our estimate; a round on
delivered-duty-paid terms reads each quote's own figure.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from connect_labs.supply_chain.procurement.services.comparison import (
    import_is_ours,
    landed_basis_words,
    round_duty_words,
)
from connect_labs.supply_chain.procurement.services.pricing import ROUND_DUTY_TERMS_REASON, _extras
from connect_labs.supply_chain.values import Money, Unconfirmed, possessive

# One per Incoterm group: carriage paid (C-terms), delivered at place (D minus DDP), origin (E/F).
BUYER_IMPORT = [
    "CPT Kano",
    "CIP Kano",
    "CFR Lagos",
    "CIF Lagos",
    "DAP Kano",
    "DPU Kano",
    "EXW Niamey",
    "FCA Niamey",
    "FAS Lagos",
    "FOB Lagos",
]
STATED = [None, Decimal("0"), Decimal("125.00")]


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


@pytest.mark.parametrize("incoterm", BUYER_IMPORT)
@pytest.mark.parametrize("stated", STATED)
def test_unsettled_terms_hold_every_buyer_import_quote_whatever_it_states(incoterm, stated):
    quote = _quote(incoterm, duties_basis="excluded" if stated is not None else "", duties_amount=stated)
    extras = _extras(quote, _round(""))
    assert isinstance(extras, Unconfirmed)
    assert list(extras.reasons) == [ROUND_DUTY_TERMS_REASON]
    assert import_is_ours(quote)
    assert round_duty_words(quote, _round("")) == "ours to cost (round terms not settled)"


@pytest.mark.parametrize("incoterm", BUYER_IMPORT)
@pytest.mark.parametrize("stated", STATED)
def test_the_waiver_counts_zero_for_every_buyer_import_quote(incoterm, stated):
    quote = _quote(incoterm, duties_basis="excluded" if stated is not None else "", duties_amount=stated)
    assert _extras(quote, _round("buyer_waiver")) == Money(Decimal("0"))
    assert round_duty_words(quote, _round("buyer_waiver")) == "waived (our import)"
    assert "supplier states" not in landed_basis_words(quote, _round("buyer_waiver"))


@pytest.mark.parametrize("incoterm", BUYER_IMPORT)
def test_buyer_pays_adds_our_estimate_not_the_supplier_figure(incoterm):
    quote = _quote(incoterm, duties_basis="excluded", duties_amount=Decimal("125.00"))
    # The supplier's 125.00 is not added: our own estimate is (in compute_figures).
    assert _extras(quote, _round("buyer_pays", Decimal("5"))) == Money(Decimal("0"))
    assert round_duty_words(quote, _round("buyer_pays", Decimal("5"))) == "our estimate of 5% added (our import)"
    # With no estimate recorded the round, not the supplier, owes the figure.
    extras = _extras(quote, _round("buyer_pays"))
    assert isinstance(extras, Unconfirmed)
    assert "no duty estimate" in " ".join(extras.reasons)


def test_supplier_ddp_terms_read_the_quote_s_own_figure():
    stated = _quote("CPT Kano", duties_basis="excluded", duties_amount=Decimal("125.00"))
    assert _extras(stated, _round("supplier_ddp")) == Money(Decimal("125.00"))
    unstated = _quote("CPT Kano")
    extras = _extras(unstated, _round("supplier_ddp"))
    assert isinstance(extras, Unconfirmed) and ROUND_DUTY_TERMS_REASON not in extras.reasons


@pytest.mark.parametrize("terms", ["", "buyer_waiver", "buyer_pays", "supplier_ddp"])
def test_a_ddp_quote_never_waits_on_the_round(terms):
    quote = _quote("DDP Kano")
    assert not import_is_ours(quote)
    extras = _extras(quote, _round(terms, Decimal("5")))
    assert not (isinstance(extras, Unconfirmed) and ROUND_DUTY_TERMS_REASON in extras.reasons)


def test_a_zero_without_an_incoterm_is_said_without_contradiction():
    words = landed_basis_words(_quote("", duties_basis="excluded", duties_amount=Decimal("0")), _round(""))
    assert "excluded from the price" not in words


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Northgate Commodities", "Northgate Commodities'"),
        ("Harmattan Therapeutics", "Harmattan Therapeutics'"),
        ("Kanem Foods Ltd", "Kanem Foods Ltd's"),
        ("", ""),
    ],
)
def test_possessive(name, expected):
    assert possessive(name) == expected
