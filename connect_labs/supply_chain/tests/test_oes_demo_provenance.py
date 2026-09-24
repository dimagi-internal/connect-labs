"""Three kinds of truth, and they must not read alike.

What the programme believes and typed in, what the programme did itself, and
what the partner entered through its own link are three different evidentiary
claims. The domain models them; this pins that the screen keeps them apart,
and that the OES demo's chain seeder actually produces all three.

THIS REPOSITORY IS PUBLIC. Every organisation, product, quantity and price
below is an invented placeholder. The real ones live in Drive and are read at
seed time (`connect_labs/labs/synthetic/seed_data.py`).
"""

import copy
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.fulfilment.services.landed import landed_total
from connect_labs.supply_chain.identity import WITNESSED_SOURCES
from connect_labs.supply_chain.models import Payment, Receipt, StockCount
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.values import Money

PROGRAM = 10629
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


# ---- the rule itself ----------------------------------------------------
#
# No database: `witnessed` is a property over one field, so an unsaved
# instance exercises it exactly as a stored row does.


@pytest.mark.parametrize("model", [Receipt, StockCount, Payment])
def test_our_own_hand_is_witnessed(model):
    assert model(source="we_recorded").witnessed is True
    assert model(source="document").witnessed is True


@pytest.mark.parametrize("model", [Receipt, StockCount, Payment])
def test_a_partners_word_is_not_witnessed(model):
    """A missing source is weaker than a partner's claim, not stronger."""
    assert model(source="partner_reported").witnessed is False
    assert model(source="supplier_reported").witnessed is False
    assert model(source="").witnessed is False


def test_what_we_wrote_down_for_a_partner_names_both():
    from connect_labs.supply_chain.templatetags.supply_chain_extras import told_by_for

    orgs = {1: {"name": "Dimagi"}, 2: {"name": "EHA Clinics"}}
    row = {"recorded_by_org_id": 1, "source": "partner_reported"}
    assert told_by_for(row, orgs, {"partner_reported": "EHA Clinics"}) == "Dimagi, for EHA Clinics (they told us)"


def test_what_the_partner_entered_names_only_the_partner():
    from connect_labs.supply_chain.templatetags.supply_chain_extras import told_by_for

    orgs = {1: {"name": "Dimagi"}, 2: {"name": "EHA Clinics"}}
    row = {"recorded_by_org_id": 2, "source": "partner_reported"}
    assert told_by_for(row, orgs, {"partner_reported": "EHA Clinics"}) == "EHA Clinics"


def test_a_partner_cannot_claim_it_saw_something_itself():
    """The refusal that makes the whole distinction trustworthy.

    `source` is derived from WHO is acting, not accepted from the payload:
    only the programme's own organisation records first-hand, and everyone
    else is reporting. Mutated `source_for` to return `"we_recorded"`
    unconditionally and watched this go red before trusting it.
    """
    from connect_labs.supply_chain.identity import DIMAGI_ORG_SLUG, source_for

    class Org:
        def __init__(self, slug):
            self.slug = slug

    assert source_for(Org("eha-clinics-reach")) == "partner_reported"
    assert source_for(Org(DIMAGI_ORG_SLUG)) == "we_recorded"


# ---- the seeder produces two of the three, and the third cannot be faked --

pytestmark_db = pytest.mark.django_db


def op(access, name, **payload):
    return call_operation(name, access, payload)


@pytest.fixture
def access():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


# A chain with the same SHAPE as the Drive document's `chc_chain`, including
# the `_`-prefixed commentary its authors leave for each other, and with
# every name, quantity and price invented.
_CHAIN = {
    "_why": "commentary, and it must not reach any payload",
    "distributor_slug": "a-distributor-org",
    "programme_org_slug": "the-programme-org",
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
            "_indicative": True,
        }
    ],
    "awarded_quote_index": 0,
    "award_rationale": "The only quote that met the placeholder specification",
    "award_decided_by": "a placeholder buyer",
    "contract": {
        "reference": "PO-PLACEHOLDER-1",
        "buyer_of_record": "programme_org",
        "buyer_org_slug": "the-programme-org",
        "status": "placed",
        "quantity": "100",
        "quantity_unit": "box",
        "currency": "USD",
        "_indicative_money": True,
    },
    "warehouse": {
        "name": "A placeholder warehouse",
        "kind": "central_store",
        "managed_by_org_slug": "a-distributor-org",
        "slug": "a-placeholder-warehouse",
        "source": "we_recorded",
    },
    "partner_points": [
        {
            "org_slug": "a-collecting-org",
            "name": "A placeholder collecting store",
            "kind": "facility",
            "slug": "a-placeholder-store",
            "source": "we_recorded",
        }
    ],
    "reported_to_us": [
        {
            "_why": "Tier 1. They told us over a message; we typed it in.",
            "operation": "receipt_record",
            "source": "partner_reported",
            "data": {
                "reference": "GRN-PLACEHOLDER-1",
                "quantity_accepted": "100",
                "unit_basis": "pack",
                "batch": "PLACEHOLDER-1",
            },
        },
        {
            "_why": "Tier 1. A stock figure read off their own sheet.",
            "operation": "stock_count_record",
            "source": "partner_reported",
            "data": {"kind": "physical_count", "quantity": "80", "quantity_unit": "box"},
        },
        {
            "_why": (
                "Tier 1, with no `source` of its own. The real document states one on every row; "
                "this covers the other shape, so the tier's own default is exercised rather than "
                "shadowed by the row on every path."
            ),
            "operation": "stock_count_record",
            "data": {"kind": "self_reported", "quantity": "75", "quantity_unit": "box"},
        },
    ],
    "we_did": [
        {
            "_why": "Tier 2. We paid it ourselves, so we witnessed it.",
            "operation": "payment_record",
            "source": "we_recorded",
            "data": {"reference": "PAY-PLACEHOLDER-1", "_indicative_money": True},
        }
    ],
    "partner_entered": [
        {
            "_why": "Tier 3 -- seeded through the partner's own link, not here.",
            "operation": "movement_record",
            "data": {"kind": "transfer", "to_org_slug": "a-collecting-org", "quantity": "20", "quantity_unit": "box"},
        }
    ],
}

_DOCUMENT = {
    "orgs": [
        {"slug": "the-programme-org", "name": "A Placeholder Programme Org", "connect_organization_id": 359},
        {"slug": "a-distributor-org", "name": "A Placeholder Distributor", "connect_organization_id": None},
        {"slug": "a-collecting-org", "name": "A Placeholder Collecting Partner", "connect_organization_id": None},
    ],
    "commodities": [
        {
            "slug": "a-product",
            "name": "A Placeholder Product",
            "category": "consumable",
            "base_unit": "unit",
            "pack_unit": "box",
            "base_per_pack": 10,
        }
    ],
    "chc_chain": _CHAIN,
}


class _Recorded:
    """The real `call_operation`, with every payload kept for inspection."""

    def __init__(self):
        self.payloads = []

    def __call__(self, access, name, **payload):
        self.payloads.append((name, payload))
        return call_operation(name, access, payload)


@pytest.fixture
def seeded(access):
    module = _load_seed_remote()
    module.op = _Recorded()
    reference = module.seed_reference(access, _DOCUMENT)
    return module, reference, module.seed_chc_chain(access, _DOCUMENT, reference)


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


@pytest.mark.django_db
class TestTheChainCarriesItsProvenance:
    def test_what_they_told_us_is_recorded_by_us_and_not_witnessed(self, seeded):
        """Tier 1: our hand, their word. The row the demo exists to show."""
        _, reference, chain = seeded
        us = reference["orgs"]["the-programme-org"]["id"]

        receipt = Receipt.objects.get(pk=chain["reported_to_us"][0]["id"])
        count = StockCount.objects.get(pk=chain["reported_to_us"][1]["id"])

        for row in (receipt, count):
            assert row.source == "partner_reported"
            assert row.recorded_by_org_id == us
            assert row.witnessed is False

    def test_what_we_did_ourselves_is_witnessed(self, seeded):
        """Tier 2: same hand, different claim -- and the screen must differ."""
        _, reference, chain = seeded
        us = reference["orgs"]["the-programme-org"]["id"]

        payment = Payment.objects.get(pk=chain["we_did"][0]["id"])
        assert payment.source == "we_recorded"
        assert payment.recorded_by_org_id == us
        assert payment.witnessed is True

    def test_both_tiers_hang_off_the_one_order(self, seeded):
        """One order carries them, or the contrast is two screens apart."""
        _, _, chain = seeded
        contract_id = chain["contract"]["id"]

        assert Receipt.objects.get(pk=chain["reported_to_us"][0]["id"]).contract_id == contract_id
        assert Payment.objects.get(pk=chain["we_did"][0]["id"]).invoice.contract_id == contract_id

    def test_the_distributor_is_one_body_in_both_its_roles(self, seeded):
        """It sells us the goods AND runs the warehouse they land in.

        Two rows for one organisation is what makes its update link see
        neither the orders it supplies nor the store it manages.
        """
        _, reference, chain = seeded
        distributor = reference["orgs"]["a-distributor-org"]["id"]

        assert chain["supplier"]["org_id"] == distributor
        assert chain["warehouse"]["managed_by_org_id"] == distributor

    def test_the_partner_points_are_keyed_by_organisation_slug(self, seeded):
        """What a row naming `to_org_slug` is resolved through."""
        _, reference, chain = seeded

        point = chain["partner_points"]["a-collecting-org"]
        assert point["managed_by_org_id"] == reference["orgs"]["a-collecting-org"]["id"]

    def test_the_stated_quantity_lands_in_the_unit_the_ledger_holds(self, seeded):
        """`unit_basis: pack` is not a unit; "box" is."""
        _, _, chain = seeded

        (line,) = Receipt.objects.get(pk=chain["reported_to_us"][0]["id"]).lines.all()
        assert line.quantity_unit == "box"
        assert str(line.quantity_accepted) == "100.0000"

    def test_the_invoice_bills_exactly_what_the_order_page_says_the_goods_cost(self, access, seeded):
        """One cost rule, not two.

        No invoice value is invented -- the document holds none and says so --
        and the figure is not worked out here either. It is
        `landed.landed_total(contract)["goods"]`, the same call the order page
        makes, so the invoice and the goods line beside it cannot disagree.
        """
        _, _, chain = seeded

        # 100 boxes at the quoted 10.00 per pack.
        goods = landed_total(access.get_contract(chain["contract"]["id"]))["goods"]
        assert goods == Money(Decimal("1000"), "USD")
        assert chain["invoice"]["amount"] == "1000"
        assert Payment.objects.get(pk=chain["we_did"][0]["id"]).amount == Decimal("1000")

    def test_the_authors_commentary_never_reaches_a_payload(self, seeded):
        """`_why` and `_indicative` are notes to a human, not fields.

        Asserted over every payload the seed sent, not over the records that
        came back: most of these operations ignore a key they do not know, so
        a record that reads correctly is no evidence the commentary was
        dropped -- it would sit in the next JSONField that accepts anything.
        """
        module, _, _ = seeded

        leaked = {(name, key) for name, payload in module.op.payloads for key in _keys(payload) if key.startswith("_")}
        assert leaked == set()

    def test_the_row_says_how_we_knew_where_it_says_so(self, seeded):
        """And the tier says it where the row does not.

        Mutated `seed_chain` to stamp tier 1 with tier 2's provenance: the
        third `reported_to_us` row (which states no source of its own) came
        back `we_recorded` and this went red.
        """
        _, _, chain = seeded

        stated, unstated = (StockCount.objects.get(pk=chain["reported_to_us"][i]["id"]) for i in (1, 2))
        assert stated.source == "partner_reported"
        assert unstated.source == "partner_reported"


def _tier_one_row(source=None, data=None):
    row = {
        "operation": "stock_count_record",
        "data": data if data is not None else {"kind": "self_reported", "quantity": "1", "quantity_unit": "box"},
    }
    if source is not None:
        row["source"] = source
    return row


_WIRING_CONTEXT = {"warehouse": {"id": 1}, "commodity": {"slug": "a-product"}, "item": {"id": 2}}
_THEIR_WORD = {"source": "partner_reported", "recorded_by_org_id": 3}


@pytest.mark.parametrize("claimed", sorted(WITNESSED_SOURCES))
def test_a_tier_one_row_cannot_claim_we_saw_it_ourselves(claimed):
    """The dangerous direction, and the one the real document could take.

    Every row in the Drive document states its own `source`, so the tier's
    stamp is always shadowed in production -- which means `reported_to_us`
    is a heading with no force unless something checks what its rows claim.
    A row there saying `we_recorded` would render as witnessed, inverting the
    very distinction section 5a exists to draw.
    """
    module = _load_seed_remote()

    with pytest.raises(ValueError) as caught:
        module.wired(_tier_one_row(claimed), _WIRING_CONTEXT, _THEIR_WORD, may_witness=False)

    message = str(caught.value)
    assert "stock_count_record" in message and claimed in message
    assert "reported_to_us" in message


def test_a_tier_one_row_may_still_pass_on_a_suppliers_word():
    """The refusal is about first-hand claims, not about disagreeing with us."""
    module = _load_seed_remote()

    data = module.wired(_tier_one_row("supplier_reported"), _WIRING_CONTEXT, _THEIR_WORD, may_witness=False)
    assert data["source"] == "supplier_reported"


@pytest.mark.parametrize("key", ["source", "recorded_by_org_id"])
def test_provenance_inside_a_rows_payload_is_refused(key):
    """One route in, so there is one thing to check.

    `data` is the fact; `source` is how we knew it. A `source` spread out of
    a payload used to land on top of the tier's stamp -- a second, silent
    override beside the deliberate one, and one the witnessed check would
    then have been reading rather than the row's own claim.
    """
    module = _load_seed_remote()
    row = _tier_one_row(data={"kind": "self_reported", "quantity": "1", "quantity_unit": "box", key: "we_recorded"})

    with pytest.raises(ValueError) as caught:
        module.wired(row, _WIRING_CONTEXT, _THEIR_WORD, may_witness=False)
    assert key in str(caught.value)


def test_a_row_that_states_its_own_source_keeps_it():
    """The tier is a default, not an override.

    The document states `source` on every row today, and every one of them
    agrees with its tier -- so nothing in the seeded chain can tell the two
    apart. This can: a row whose stated source differs from its tier's keeps
    what it said. Mutated `wired` to ignore `row["source"]` and watched it go
    red.
    """
    module = _load_seed_remote()
    row = {
        "operation": "stock_count_record",
        "source": "supplier_reported",
        "data": {"kind": "self_reported", "quantity": "1", "quantity_unit": "box"},
    }
    context = {
        "warehouse": {"id": 1},
        "commodity": {"slug": "a-product"},
        "item": {"id": 2},
    }

    data = module.wired(row, context, {"source": "partner_reported", "recorded_by_org_id": 3})
    assert data["source"] == "supplier_reported"
    assert data["recorded_by_org_id"] == 3


@pytest.mark.django_db
def test_the_chain_itself_refuses_a_second_hand_row_that_claims_first_hand(access):
    """The guard is wired at the tier-1 call site, not merely available.

    `wired()` only refuses a witnessed claim when it is told which tier it is
    filling. Testing `wired` alone cannot tell whether `seed_chain` passes
    `may_witness=False`, so this drives the whole seeder over a document
    whose `reported_to_us` contains exactly the row section 5a must never
    show. Mutated the call site back to the plain `wired(row, context,
    their_word)` and watched this go red.
    """
    document = copy.deepcopy(_DOCUMENT)
    document["chc_chain"]["reported_to_us"][1]["source"] = "we_recorded"

    module = _load_seed_remote()
    reference = module.seed_reference(access, document)

    with pytest.raises(ValueError) as caught:
        module.seed_chc_chain(access, document, reference)
    assert "reported_to_us" in str(caught.value)


@pytest.mark.django_db
def test_a_price_per_single_unit_is_billed_the_way_the_domain_bills_it(access):
    """The seam where the two cost rules actually diverged.

    `landed._line_total` multiplies `per_pack`, `per_base_unit` AND
    `per_metric_tonne`. The seeder's own arithmetic multiplied only the first
    and refused the other two -- so on this document it would have raised
    where the order page would have shown a goods line perfectly happily.
    """
    document = copy.deepcopy(_DOCUMENT)
    document["chc_chain"]["quotes"][0]["as_quoted_unit"] = "per_base_unit"

    module = _load_seed_remote()
    reference = module.seed_reference(access, document)
    chain = module.seed_chc_chain(access, document, reference)

    goods = landed_total(access.get_contract(chain["contract"]["id"]))["goods"]
    assert goods == Money(Decimal("1000"), "USD")
    assert chain["invoice"]["amount"] == "1000"


@pytest.mark.django_db
def test_an_operation_the_seed_cannot_place_is_refused_not_guessed(seeded):
    """Tier 3 is not seeded here, and the refusal says so rather than shrugging.

    A movement recorded from this seeder would carry OUR organisation and so
    would read as ours, which is precisely the substitution the three tiers
    exist to prevent. It has to go through the partner's own link.
    """
    module, _, chain = seeded
    row = _CHAIN["partner_entered"][0]
    context = {"contract": chain["contract"], "warehouse": chain["warehouse"]}

    with pytest.raises(ValueError) as caught:
        module.wired(row, context, {"source": "we_recorded", "recorded_by_org_id": 1})
    assert "movement_record" in str(caught.value)
