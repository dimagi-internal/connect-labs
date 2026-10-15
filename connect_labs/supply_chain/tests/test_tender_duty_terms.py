"""One duty rule for every buyer-import quote.

Under an Incoterm that puts the import on the buyer, duty is the buyer's cost, set by the
ROUND's duty terms -- never by a figure the supplier wrote. Unsettled terms hold every such
quote alike; the waiver counts zero for every one; buyer-pays adds our estimate; a round on
delivered-duty-paid terms reads each quote's own figure. A DDP quote is the supplier's import:
its own duty figure stands under any round terms.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from connect_labs.supply_chain.procurement.services.comparison import (
    gap_word,
    import_is_ours,
    landed_basis_words,
    round_duty_words,
)
from connect_labs.supply_chain.procurement.services.pricing import (
    ROUND_DUTY_TERMS_REASON,
    _extras,
    buyer_imports,
    round_duty_applies,
)
from connect_labs.supply_chain.procurement.services.questions import (
    DUTY_RESTATE_KEY,
    INTERNAL,
    audience_for_reason,
    key_for_reason,
    needs_duty_restated,
)
from connect_labs.supply_chain.values import Money, Unconfirmed, possessive

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


def _quote(incoterm, *, duties_basis="", duties_amount=None, freight_amount=None):
    return SimpleNamespace(
        incoterm=incoterm,
        duties_basis=duties_basis,
        duties_amount=duties_amount,
        freight_basis="included",
        freight_amount=freight_amount,
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
    assert round_duty_words(quote, _round("")) == "ours to cost (tender terms not settled)"


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


UNSETTLED = SimpleNamespace(duty_terms="", duty_estimate_percent=None)


@pytest.mark.parametrize("incoterm", ["EXW Niamey", "FCA Niamey", "FOB Lagos", "CPT Kano", "DAP Kano"])
def test_a_buyer_import_term_with_no_duty_amount_waits_on_the_round(incoterm):
    extras = _extras(_quote(incoterm, freight_amount=Decimal("10")), UNSETTLED)
    assert isinstance(extras, Unconfirmed)
    assert list(extras.reasons) == [ROUND_DUTY_TERMS_REASON]
    # Ours to settle: never a question in the supplier's email.
    assert audience_for_reason(ROUND_DUTY_TERMS_REASON) == INTERNAL
    assert key_for_reason(ROUND_DUTY_TERMS_REASON) == "duty_terms"
    assert gap_word(ROUND_DUTY_TERMS_REASON) == "tender duty terms"
    assert round_duty_words(_quote(incoterm), UNSETTLED) == "ours to cost (tender terms not settled)"


def test_delivered_duty_paid_and_unknown_terms_still_ask_the_supplier():
    ddp_round = SimpleNamespace(duty_terms="supplier_ddp", duty_estimate_percent=None)
    extras = _extras(_quote("EXW Niamey", freight_amount=Decimal("10")), ddp_round)
    assert isinstance(extras, Unconfirmed) and ROUND_DUTY_TERMS_REASON not in extras.reasons
    # No Incoterm: the quote's own "excluded" is a figure the supplier owes.
    extras = _extras(_quote("", duties_basis="excluded"), UNSETTLED)
    assert ROUND_DUTY_TERMS_REASON not in extras.reasons


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


def test_duty_included_under_the_waiver_needs_restating():
    waiver = SimpleNamespace(duty_terms="buyer_waiver")
    assert needs_duty_restated(SimpleNamespace(duties_basis="included"), waiver)
    assert not needs_duty_restated(SimpleNamespace(duties_basis="excluded"), waiver)
    assert not needs_duty_restated(SimpleNamespace(duties_basis="included"), SimpleNamespace(duty_terms=""))
    assert DUTY_RESTATE_KEY == "duty_restated"


def test_an_origin_incoterm_is_a_handover_not_a_delivery():
    tender = SimpleNamespace(delivery_points=[{"key": "kano", "name": "Kano"}], duty_terms="")
    quote = SimpleNamespace(
        delivery_mode="delivered",
        delivery_point_keys=[],
        incoterm="EXW Niamey",
        freight_basis="",
        freight_amount=None,
        duties_basis="",
        duties_amount=None,
        as_quoted_currency="EUR",
    )
    words = landed_basis_words(quote, tender)
    assert words.startswith("Ex works Niamey — delivery to ") and "requested" in words
    assert "Delivered to" not in words
