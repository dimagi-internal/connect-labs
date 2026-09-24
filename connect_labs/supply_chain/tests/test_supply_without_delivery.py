"""An organisation that buys through us but runs its own last mile.

Most of a funder's portfolio does not use Connect for verified service
delivery. This is that organisation, and its chain genuinely stops at the
last store: `summary._deliver()` keys off `opportunity_id` and
`kind="user_held"`, so a program with neither reports no reach to a
beneficiary. That is the honest half of the demo's close (design section 6,
beat 9), and it has to be real behaviour rather than a staged screen.

**What makes these assertions worth anything.** "No user-held points exist in
program 10671" is green on an empty database, and green is exactly what a
seeder that never ran would produce. So every zero below is paired with the
same figure taken from a world that HAS the binding -- one store bound to an
opportunity, one not -- read off the same `_deliver()` call. A summary that
had stopped counting user-held points at all would pass the zeros and fail
the pair.

THIS REPOSITORY IS PUBLIC. Every organisation, product and quantity here is
an invented placeholder; the real ones live in Drive and are read at seed
time.
"""

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from connect_labs.supply_chain.models import Contract, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.summary import chain_summary
from connect_labs.supply_chain.values import Quantity

PROGRAM = 10671
# Any opportunity id at all. The point is that NO point in this program
# carries one, so which one is asked about cannot matter.
AN_OPPORTUNITY = 4242

_SEED_REMOTE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "walkthroughs" / "oes-demo" / "seed_remote.py"


def _load_seed_remote():
    """`scripts/walkthroughs/oes-demo/` is a script directory, not a package.

    The hyphen in `oes-demo` cannot be a Python package name, so the module
    under test is loaded by file path rather than imported.
    """
    spec = importlib.util.spec_from_file_location("oes_demo_seed_remote", _SEED_REMOTE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- the document ------------------------------------------------------
#
# The same SHAPE as the Drive document: four sections over one shared product
# list, of which only `supply_only` is a full chain here -- the other three
# exist because `seed_scopes` resolves every section before the first write,
# and they are seeded by their own tasks.

_PRODUCT = {
    "slug": "a-product",
    "name": "A Placeholder Product",
    "category": "consumable",
    "base_unit": "unit",
    "pack_unit": "box",
    "base_per_pack": 10,
}

# No `opportunity_id` on any point, and no point of kind `user_held`. That is
# the whole content of "runs its own last mile", and it is DATA -- there is
# no code here making the chain stop.
_SUPPLY_ONLY = {
    "_why": "commentary, and it must not reach any payload",
    "programme_org_slug": "the-other-implementer",
    "distributor_slug": "a-distributor-org",
    "round": {
        "label": "A placeholder basket",
        "delivery_point": {"city": "A Placeholder City"},
        "lines": [{"commodity_slug": "a-product", "quantity": "100", "quantity_unit": "box"}],
    },
    "quotes": [
        {
            "commodity_slug": "a-product",
            "item": {"sku": "placeholder-sku", "name": "A Placeholder Trade Item"},
            "as_quoted_amount": "10.00",
            "as_quoted_unit": "per_pack",
            "as_quoted_currency": "USD",
        }
    ],
    "awarded_quote_index": 0,
    "award_rationale": "The only quote that met the placeholder specification",
    "contract": {
        "reference": "PO-PLACEHOLDER-9",
        "buyer_of_record": "programme_org",
        "buyer_org_slug": "the-other-implementer",
        "status": "placed",
        "quantity": "100",
        "quantity_unit": "box",
        "currency": "USD",
    },
    "warehouse": {
        "name": "A placeholder central store",
        "kind": "central_store",
        "managed_by_org_slug": "the-other-implementer",
        "slug": "a-placeholder-central-store",
        "source": "we_recorded",
    },
    # Its own last mile is its own: the chain has no collecting partner in
    # this domain and no worker holding stock.
    "partner_points": [],
    "reported_to_us": [],
    "we_did": [
        {
            "_why": "The goods landed and we received them ourselves, so there IS stock at the last store.",
            "operation": "receipt_record",
            "source": "we_recorded",
            "data": {
                "reference": "GRN-PLACEHOLDER-9",
                "quantity_accepted": "100",
                "unit_basis": "pack",
                "batch": "PLACEHOLDER-9",
            },
        }
    ],
}

_A_MINIMAL_SECTION = {"round": {"lines": [{"commodity_slug": "a-product", "quantity": "1", "quantity_unit": "box"}]}}

_DOCUMENT = {
    "orgs": [
        {"slug": "the-other-implementer", "name": "A Placeholder Implementer", "connect_organization_id": None},
        {"slug": "a-distributor-org", "name": "A Placeholder Distributor", "connect_organization_id": None},
    ],
    "commodities": [_PRODUCT],
    "chc_chain": _A_MINIMAL_SECTION,
    "rutf_rounds": _A_MINIMAL_SECTION,
    "chlorine_blocked": _A_MINIMAL_SECTION,
    "supply_only": _SUPPLY_ONLY,
}


@pytest.fixture
def seeded(db):
    module = _load_seed_remote()
    scopes = module.seed_scopes(_DOCUMENT)
    return module, module.seed_supply_only(_DOCUMENT, scopes)


def _access(module):
    return module.access_for(module.SUPPLY_ONLY_PROGRAM_ID)


def _deliver(module, **kwargs):
    """What the landing page reads, for this program's one product.

    Through `chain_summary` rather than `summary._deliver` directly: the
    commodity and the single trade item are resolved there, and `on_hand` is
    only a knowable quantity once they are.
    """
    return chain_summary(_access(module), commodity_slug="a-product", **kwargs)["deliver"]


def _a_worker_holding_stock(module, **extra):
    """One user-held point, bound to an opportunity, in this same program.

    The control. Everything asserted to be zero below is asserted again with
    this present, so a zero means "the data does not say it" rather than
    "the summary stopped looking".
    """
    return call_operation(
        "supply_point_upsert",
        _access(module),
        {
            "data": {
                "slug": "a-placeholder-worker",
                "name": "A Placeholder Worker's Own Holding",
                "kind": "user_held",
                "connect_username": "a-placeholder-worker",
                "opportunity_id": AN_OPPORTUNITY,
                "source": "we_recorded",
                **extra,
            }
        },
    )


# ---- the program is the one the seeder seeds ---------------------------


def test_the_program_under_test_is_the_one_the_seeder_writes_to():
    """Otherwise every assertion below is about an empty program.

    A test that names a program the seeder does not use passes by describing
    nothing, which is the exact failure mode this file was written to avoid.
    """
    module = _load_seed_remote()

    assert module.SUPPLY_ONLY_PROGRAM_ID == PROGRAM
    assert module.SCOPES["supply_only"]["program_id"] == PROGRAM


# ---- the summary can see the binding, when there is one ----------------


def test_the_summary_counts_a_bound_user_held_point_and_an_unbound_store(db):
    """The calibration for every zero in this file.

    Two points in one program -- one store bound to nothing, one worker's
    holding bound to an opportunity -- read through the same `_deliver()`
    the supply-only chain is read through. Without this, "user_held: 0"
    would be evidence of nothing.
    """
    module = _load_seed_remote()
    access = _access(module)
    call_operation(
        "supply_point_upsert",
        access,
        {
            "data": {
                "slug": "a-placeholder-store",
                "name": "A Placeholder Store",
                "kind": "facility",
                "source": "we_recorded",
            }
        },
    )
    _a_worker_holding_stock(module)

    whole_network = chain_summary(access)["deliver"]["network"]
    assert whole_network["supply_points"] == 2
    assert whole_network["user_held"] == 1

    through_the_opportunity = chain_summary(access, opportunity_id=AN_OPPORTUNITY)["deliver"]["network"]
    assert through_the_opportunity["supply_points"] == 1
    assert through_the_opportunity["user_held"] == 1


# ---- and the supply-only chain gives it nothing to see -----------------


def test_the_chain_reaches_a_store_and_stops_there(seeded):
    """Supply works; delivery is simply not claimed.

    What it still gets is the whole point of the beat: a store, and stock
    standing in it. What it does not get is anyone holding that stock.
    """
    module, _ = seeded

    deliver = _deliver(module)
    assert deliver["network"]["supply_points"] == 1
    assert deliver["network"]["user_held"] == 0
    # The goods landed, so the last store is not an empty screen. A
    # `Quantity`, because the unit travels with the number in this domain.
    assert deliver["on_hand"] == Quantity(Decimal("100.0000"), "box")


def test_none_of_its_points_carries_an_opportunity(seeded):
    """The first of the two fields `_deliver()` keys off."""
    module, _ = seeded

    points = SupplyPoint.objects.filter(program_id=PROGRAM)
    assert points.exists()
    assert not points.exclude(opportunity_id=None).exists()

    # And read the way the screen reads it: asked about an opportunity, this
    # network answers with nothing at all.
    assert _deliver(module, opportunity_id=AN_OPPORTUNITY)["network"] == {
        "supply_points": 0,
        "user_held": 0,
        "never_reported": 0,
    }


def test_it_makes_no_claim_that_anything_reached_a_beneficiary(seeded):
    """The honest half of the close, stated as figures rather than prose."""
    module, _ = seeded

    deliver = _deliver(module)
    assert deliver["distributions"]["runs"] == 0
    # Zero of something, not an absence: the unit is known -- nothing has
    # been consumed out of this network, which is a stronger statement than
    # "the figure is missing".
    assert deliver["consumed"] == Quantity(Decimal("0"), "box")


def test_a_worker_holding_stock_would_have_been_counted(seeded):
    """The mutation, kept as a test.

    Give this program one bound, user-held point and every figure above
    moves. That is what makes the zeros a statement about this
    organisation's world rather than about the summary's blindness -- and it
    is the assertion that goes red if the supply-only section ever gains an
    `opportunity_id` or a `user_held` point.
    """
    module, _ = seeded
    _a_worker_holding_stock(module)

    deliver = _deliver(module)
    assert deliver["network"]["supply_points"] == 2
    assert deliver["network"]["user_held"] == 1
    assert _deliver(module, opportunity_id=AN_OPPORTUNITY)["network"]["supply_points"] == 1


def test_it_is_seeded_into_its_own_program_and_not_the_chc_one(seeded):
    """A different organisation's program, not a section of this operation's.

    `scopes["chc"]` is the nearest thing to hand at the call site, and
    seeding the second organisation's chain into it would put its order on
    the program team's own screens.
    """
    module, result = seeded

    assert result["program_id"] == PROGRAM
    assert Contract.objects.filter(program_id=PROGRAM).count() == 1
    assert not Contract.objects.filter(program_id=module.CHC_PROGRAM_ID).exists()
