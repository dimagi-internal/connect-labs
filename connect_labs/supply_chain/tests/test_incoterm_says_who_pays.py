"""An Incoterm is a statement about who pays freight and duties. Read it.

THIS REPOSITORY IS PUBLIC. Every figure here is invented.

The domain already stored an Incoterm on every quote and contract, rendered
all eleven in words on the order page, and then ignored it when deriving a
landed cost -- which read `freight_basis` and `duties_basis`, two flags
somebody had to set by hand. So a quote marked DDP, which means the seller
delivers duty paid, still came back "duties basis not specified on the quote"
and refused to cost.

That is the wrong kind of refusal. This domain withholds a figure when nobody
has said, and a supplier quoting DDP HAS said -- in the most standard
vocabulary the trade has.

Three rules, and the second is the one that keeps this honest:

  1. an explicitly recorded basis always wins, because somebody typed it;
  2. an Incoterm fills a basis nobody recorded, and the reason says it was
     read from the term rather than stated outright;
  3. an Incoterm that CONTRADICTS a recorded basis is not silently resolved
     either way -- it is surfaced, because one of the two is wrong and
     picking one would hide a real disagreement about money.
"""

import pytest

from connect_labs.supply_chain.records import freight_and_duties_for_incoterm

pytestmark = pytest.mark.django_db


class TestTheTermSaysWhoPays:
    @pytest.mark.parametrize(
        "term,freight,duties",
        [
            # The seller delivers, duty paid: the one term where the seller carries both.
            ("DDP", "included", "included"),
            # Delivered at place: seller pays carriage, buyer clears import.
            ("DAP", "included", "excluded"),
            ("DPU", "included", "excluded"),
            ("CPT", "included", "excluded"),
            ("CIP", "included", "excluded"),
            ("CFR", "included", "excluded"),
            ("CIF", "included", "excluded"),
            # Ex works: the buyer collects and carries everything.
            ("EXW", "excluded", "excluded"),
            ("FCA", "excluded", "excluded"),
            ("FAS", "excluded", "excluded"),
            ("FOB", "excluded", "excluded"),
        ],
    )
    def test_each_of_the_eleven_says_who_carries_what(self, term, freight, duties):
        assert freight_and_duties_for_incoterm(term) == (freight, duties)

    def test_the_named_place_rides_along_and_is_ignored(self):
        """An Incoterm is written with its place: "DAP Kano"."""
        assert freight_and_duties_for_incoterm("DAP Kano") == ("included", "excluded")
        assert freight_and_duties_for_incoterm("ddp lagos") == ("included", "included")

    def test_something_that_is_not_an_incoterm_says_nothing(self):
        """Never guessed at. A term outside the eleven is not a statement."""
        assert freight_and_duties_for_incoterm("negotiable") == (None, None)
        assert freight_and_duties_for_incoterm("") == (None, None)
        assert freight_and_duties_for_incoterm(None) == (None, None)


class TestItIsTheTradesOwnMeaning:
    def test_DDP_is_the_only_term_where_the_seller_pays_import_duty(self):
        """The distinction the whole table exists for.

        DAP and DDP differ by exactly this, it is the single most expensive
        difference between two otherwise identical quotes, and a buyer who
        reads them as the same thing has mispriced the cheaper one.
        """
        from connect_labs.supply_chain.records import INCOTERM_RESPONSIBILITY

        pays_duty = {t for t, (_, d) in INCOTERM_RESPONSIBILITY.items() if d == "included"}
        assert pays_duty == {"DDP"}

    def test_every_term_the_order_page_explains_is_also_costed(self):
        """The two tables must not drift: one explains, the other computes."""
        from connect_labs.supply_chain.records import INCOTERM_RESPONSIBILITY
        from connect_labs.supply_chain.templatetags.supply_chain_extras import INCOTERMS

        assert set(INCOTERMS) == set(INCOTERM_RESPONSIBILITY)


# ---------------------------------------------------------------------------
# The three rules, against real quotes through the real derivation.
# ---------------------------------------------------------------------------

from decimal import Decimal  # noqa: E402

from connect_labs.labs.access.scopes import SYSTEM  # noqa: E402
from connect_labs.supply_chain.data_access import SupplyDataAccess  # noqa: E402
from connect_labs.supply_chain.operations import call_operation  # noqa: E402
from connect_labs.supply_chain.values import Unconfirmed  # noqa: E402

PROGRAM = 10954


@pytest.fixture
def world():
    access = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
    call_operation(
        "commodity_upsert",
        access,
        {
            "data": {
                "slug": "a-placeholder-good",
                "name": "A placeholder good",
                "base_unit": "unit",
                "pack_unit": "box",
            }
        },
    )
    supplier = call_operation("supplier_create", access, {"data": {"name": "A Placeholder Seller"}})
    tender = call_operation(
        "tender_create",
        access,
        {
            "data": {
                "label": "A placeholder tender",
                "lines": [{"commodity_slug": "a-placeholder-good", "quantity": "10", "quantity_unit": "box"}],
            }
        },
    )
    return access, supplier, tender


def _extras_for(world, **quote_fields):
    from connect_labs.supply_chain.procurement.services.pricing import _extras

    access, supplier, tender = world
    quote = call_operation(
        "quote_record",
        access,
        {
            "data": {
                "tender_id": tender["id"],
                "supplier_id": supplier["id"],
                "commodity_slug": "a-placeholder-good",
                "as_quoted_amount": "10.00",
                "as_quoted_unit": "per_pack",
                "as_quoted_currency": "USD",
                **quote_fields,
            }
        },
    )
    from connect_labs.supply_chain.models import Quote

    return _extras(Quote.objects.get(pk=quote["id"]))


class TestAnIncotermFillsAGapNobodyRecorded:
    def test_DDP_costs_without_either_flag_being_set(self, world):
        """MUTATED: `_extras` reverted to ignoring the Incoterm. Red. Reverted."""
        extras = _extras_for(world, incoterm="DDP Kano")
        assert not isinstance(extras, Unconfirmed), getattr(extras, "reasons", extras)
        assert extras.amount == Decimal("0"), "DDP means the seller carries freight and duty; nothing to add"

    def test_EXW_still_refuses_because_the_buyer_pays_an_amount_nobody_stated(self, world):
        """Reading the term is not the same as knowing the number.

        EXW says the buyer pays freight and duty. It does not say how much,
        and this domain does not invent one -- so the figure stays Unconfirmed
        and the reason says where "excluded" came from.
        """
        extras = _extras_for(world, incoterm="EXW Lagos")
        assert isinstance(extras, Unconfirmed)
        assert any("read from Incoterm" in reason for reason in extras.reasons), extras.reasons

    def test_DAP_carries_freight_and_leaves_duty_to_the_buyer(self, world):
        extras = _extras_for(world, incoterm="DAP Kano")
        assert isinstance(extras, Unconfirmed)
        joined = " ".join(extras.reasons)
        assert "duties" in joined
        assert "freight" not in joined, "DAP includes freight; it should not be asked about"


class TestWhereOnlyOneOfTheTwoSpeaks:
    def test_recorded_flags_with_no_recognised_term_are_used(self, world):
        """Somebody read the supplier's email and typed it. That is evidence.

        The Incoterm here is deliberately NOT one of the eleven, so the term
        says nothing and the flags are the only statement. An earlier draft
        of this test used EXW with both flags "included" and asserted the
        flags won -- which contradicted the contradiction rule below. Two
        rules cannot both be "always"; the disagreement case belongs to
        rule three, and this one is about a term that does not speak.
        """
        extras = _extras_for(world, incoterm="negotiable", freight_basis="included", duties_basis="included")
        assert not isinstance(extras, Unconfirmed), getattr(extras, "reasons", extras)
        assert extras.amount == Decimal("0")

    def test_flags_that_agree_with_the_term_are_not_a_contradiction(self, world):
        extras = _extras_for(world, incoterm="DDP Kano", freight_basis="included", duties_basis="included")
        assert not isinstance(extras, Unconfirmed), getattr(extras, "reasons", extras)
        assert extras.amount == Decimal("0")


class TestAContradictionIsReportedNotResolved:
    def test_a_quote_that_disagrees_with_its_own_incoterm_says_so(self, world):
        """The rule that stops this from papering over real money.

        DDP means the seller clears the import. A quote marked DDP whose
        duties are recorded as excluded contains a contradiction worth real
        money, and picking either side would hide it.

        MUTATED: the contradiction branch was removed so the recorded basis
        simply won. This test went red; the two above stayed green, which is
        why it is written separately.
        """
        extras = _extras_for(world, incoterm="DDP Kano", duties_basis="excluded", duties_amount="5.00")
        assert isinstance(extras, Unconfirmed)
        assert any("charge for it twice" in reason for reason in extras.reasons), extras.reasons

    def test_the_harmless_direction_is_left_alone(self, world):
        """ "Included" under a term that says the buyer pays is usually right.

        A health programme is very often duty-exempt, so there is nothing to
        add. Recording that as "included" adds nothing to the total, so
        believing it cannot overcharge anybody -- and refusing to cost the
        quote over it would withhold a figure the domain actually has.

        Found by an existing fixture (DAP with duties included), not by
        reasoning ahead: the first version of this rule flagged both
        directions and broke three unrelated tests that were describing a
        perfectly ordinary exempt purchase.
        """
        extras = _extras_for(world, incoterm="DAP Kano", duties_basis="included")
        assert not isinstance(extras, Unconfirmed), getattr(extras, "reasons", extras)
        assert extras.amount == Decimal("0")
