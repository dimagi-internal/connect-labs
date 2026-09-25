"""Seeding an environment twice should leave what seeding it once did.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented.

A seeder gets run again constantly -- after a schema change, after somebody
edits the document, while iterating on a screen, when a demo needs resetting
an hour before a call. If the second run doubles the rounds and the orders,
the environment quietly stops describing anything real, and the person
running it finds out from a screen rather than from the seeder.

`ensure_demo` has a guard that REFUSES to seed a scope already holding rows,
and that remains the safety net. It is not the same thing as this: refusing
protects the data and leaves you with no way to converge, so anybody
iterating ends up purging the whole scope and losing the partner links and
their tokens along with it.

**What can be idempotent, and what honestly cannot.** Reference data has
natural keys and upserts: an organisation is its slug, a product is its slug,
a trade item is its SKU, a store is its slug. Lifecycle records have keys the
document supplies: a round is its label, an order is its reference, an
invoice is its reference. The LEDGER is different by design -- a movement, a
receipt, a stock count are events, and two identical receipts are a real
thing that can happen. Those cannot be deduplicated by inspection, so they
are keyed on the reference the document gives them, and anything the document
leaves unreferenced is seeded once per chain and skipped thereafter.

This test is the measurement. It seeds, counts every row, seeds again, and
reports exactly which models grew.
"""

import importlib.util
import pathlib

import pytest
from django.apps import apps

_SEED_REMOTE_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "scripts" / "walkthroughs" / "oes-demo" / "seed_remote.py"
)


def _load_seed_remote():
    spec = importlib.util.spec_from_file_location("oes_demo_seed_remote_idempotent", _SEED_REMOTE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _counts():
    """Every supply row there is, by model."""
    out = {}
    for model in apps.get_app_config("supply_chain").get_models():
        out[model.__name__] = model.objects.count()
    return out


def _grew(before, after):
    return {name: (before[name], after[name]) for name in after if after[name] != before.get(name, 0)}


# A document with one COMPLETE chain: enough to drive seed_chain end to end,
# and small enough to read. Shaped like the real one, named like nothing.
_ORGS = [
    {"slug": "the-programme-org", "name": "A Placeholder Programme Org", "country": "NG"},
    {"slug": "the-distributor", "name": "A Placeholder Distributor", "country": "NG"},
    {"slug": "a-partner", "name": "A Placeholder Partner", "country": "NG"},
]

_CHAIN = {
    "programme_org_slug": "the-programme-org",
    "distributor_slug": "the-distributor",
    "round": {
        "label": "A Placeholder Round",
        "delivery_point": {"city": "A Placeholder City"},
        "lines": [{"commodity_slug": "a-product", "quantity": "100", "quantity_unit": "carton"}],
    },
    "quotes": [
        {
            "commodity_slug": "a-product",
            "item": {"sku": "SKU-1", "name": "A Placeholder Item", "base_per_pack": 10},
            "as_quoted_amount": "10.00",
            "as_quoted_unit": "per_pack",
            "as_quoted_currency": "USD",
            "pack_spec_source": "trade_item_confirmed",
            "freight_basis": "included",
            "duties_basis": "included",
            "quantity_basis": "100",
            "quantity_basis_unit": "carton",
        }
    ],
    "awarded_quote_index": 0,
    "award_rationale": "A placeholder reason",
    "contract": {
        "reference": "A-PLACEHOLDER-ORDER",
        "buyer_of_record": "programme_org",
        "buyer_org_slug": "the-programme-org",
        "status": "placed",
        "quantity": "100",
        "quantity_unit": "carton",
        "currency": "USD",
    },
    "warehouse": {
        "name": "A placeholder warehouse",
        "kind": "central_store",
        "managed_by_org_slug": "the-distributor",
        "slug": "a-warehouse",
        "source": "we_recorded",
    },
    "partner_points": [
        {
            "org_slug": "a-partner",
            "name": "A placeholder partner store",
            "kind": "facility",
            "slug": "a-partner-store",
            "source": "we_recorded",
        }
    ],
    "reported_to_us": [
        {
            "operation": "receipt_record",
            "source": "partner_reported",
            "data": {"reference": "A-PLACEHOLDER-GRN", "quantity_accepted": "100", "unit_basis": "pack"},
        }
    ],
    "we_did": [
        {
            "operation": "payment_record",
            "source": "we_recorded",
            "data": {"reference": "A-PLACEHOLDER-PAYMENT"},
        }
    ],
    "partner_entered": [],
}

_DOCUMENT = {
    "orgs": _ORGS,
    "commodities": [
        {
            "slug": "a-product",
            "name": "A Placeholder Product",
            "category": "consumable",
            "base_unit": "unit",
            "pack_unit": "carton",
            "base_per_pack": 10,
        }
    ],
    "chc_chain": _CHAIN,
    # Each scope's catalogue is derived from its own section, so a section
    # that names no product gets an empty catalogue -- which is correct, and
    # means a test seeding into that scope has to say what it buys.
    "rutf_rounds": {
        "round_one": {
            "round": {"lines": [{"commodity_slug": "a-product", "quantity": "1", "quantity_unit": "carton"}]}
        }
    },
    "chlorine_blocked": {"round": {"lines": []}},
    "supply_only": {"round": {"lines": []}},
}


@pytest.fixture
def seeded(db):
    module = _load_seed_remote()
    scopes = module.seed_scopes(_DOCUMENT)
    chc = scopes["chc"]
    module.seed_chain(chc["access"], _CHAIN, chc["reference"])
    return module, scopes


def test_seeding_the_reference_data_again_adds_nothing(db):
    """Orgs, products, items, suppliers and stores all key on something."""
    module = _load_seed_remote()
    module.seed_scopes(_DOCUMENT)
    before = _counts()
    module.seed_scopes(_DOCUMENT)

    assert _grew(before, _counts()) == {}


def test_seeding_a_whole_chain_again_adds_nothing(seeded):
    """The measurement this file exists for.

    Everything a chain writes -- the round, its quotes, the award, the order,
    the invoice, the stores, and the ledger rows the document references --
    should be found rather than made a second time.
    """
    module, scopes = seeded
    chc = scopes["chc"]
    before = _counts()

    module.seed_chain(chc["access"], _CHAIN, chc["reference"])

    grew = _grew(before, _counts())
    assert grew == {}, "these models gained rows on the second run: " + ", ".join(
        f"{name} {was}->{now}" for name, (was, now) in sorted(grew.items())
    )


# ---------------------------------------------------------------------------
# The other seeders. A chain is not the only thing that writes.
# ---------------------------------------------------------------------------

_RUTF_TWO = {
    "round": {
        "label": "A Placeholder Second Round",
        "delivery_point": {"city": "A Placeholder City"},
        "lines": [{"commodity_slug": "a-product", "quantity": "10", "quantity_unit": "carton"}],
    },
    "quotes": [
        {
            "supplier_label": "A Placeholder Bidder",
            "commodity_slug": "a-product",
            "as_quoted_amount": "9.00",
            "as_quoted_unit": "per_pack",
            "as_quoted_currency": "USD",
        }
    ],
}

_AWAITING = {
    "supplier_label": "A Placeholder Bidder",
    "programme_org_slug": "the-programme-org",
    "round": {
        "label": "A Placeholder Awaiting Round",
        "delivery_point": {"city": "A Placeholder City"},
        "lines": [{"commodity_slug": "a-product", "quantity": "5", "quantity_unit": "carton"}],
    },
    "quotes": [
        {
            "commodity_slug": "a-product",
            "item": {"sku": "SKU-1", "name": "A Placeholder Item", "base_per_pack": 10},
            "as_quoted_amount": "9.50",
            "as_quoted_unit": "per_pack",
            "as_quoted_currency": "USD",
        }
    ],
    "awarded_quote_index": 0,
    "award_rationale": "A placeholder reason",
    "awarded_days_ago": 5,
    "approval": {"approver_org_slug": "a-partner", "role": "technical", "requested_days_ago": 4},
}


def test_seeding_an_open_round_again_adds_nothing(db):
    """Round two has quotes and no award, so it keys differently."""
    module = _load_seed_remote()
    scopes = module.seed_scopes(_DOCUMENT)
    access = scopes["rutf"]["access"]
    module.seed_rutf_round_two(access, _RUTF_TWO)
    before = _counts()

    module.seed_rutf_round_two(access, _RUTF_TWO)

    grew = _grew(before, _counts())
    assert grew == {}, "second run grew: " + ", ".join(f"{n} {a}->{b}" for n, (a, b) in sorted(grew.items()))


def test_seeding_an_award_awaiting_approval_again_adds_nothing(db):
    """An approval asked for twice would read as two approvers waiting."""
    module = _load_seed_remote()
    scopes = module.seed_scopes(_DOCUMENT)
    chc = scopes["chc"]
    module.seed_awaiting_approval(chc["access"], {"awaiting_approval": _AWAITING}, chc["reference"])
    before = _counts()

    module.seed_awaiting_approval(chc["access"], {"awaiting_approval": _AWAITING}, chc["reference"])

    grew = _grew(before, _counts())
    assert grew == {}, "second run grew: " + ", ".join(f"{n} {a}->{b}" for n, (a, b) in sorted(grew.items()))


# ---------------------------------------------------------------------------
# The duplicate finder. A detector that cannot detect reports a clean
# environment in exactly the same words as a clean one.
# ---------------------------------------------------------------------------


def _load_finder():
    path = _SEED_REMOTE_PATH.parent / "find_duplicates.py"
    source = path.read_text()
    # The file reports on import, which is what makes it runnable through
    # `manage.py shell`. Only the grouping is wanted here.
    namespace = {}
    body = source.split("total, found = report()")[0]
    exec(compile(body, str(path), "exec"), namespace)  # noqa: S102
    return namespace


class _Row:
    def __init__(self, pk, key):
        self.pk = pk
        self._key = key


def test_the_duplicate_finder_finds_duplicates():
    """MUTATED: `duplicates` changed to return {} always. Red. Reverted."""
    duplicates = _load_finder()["duplicates"]

    rows = [_Row(3, "a"), _Row(1, "a"), _Row(2, "b")]
    found = duplicates(rows, lambda r: r._key)

    assert found == {"a": [1, 3]}, "two rows share a key; the lower id is kept"


def test_the_duplicate_finder_is_quiet_when_there_is_nothing_to_find():
    """The other side, so it cannot pass by reporting everything."""
    duplicates = _load_finder()["duplicates"]

    assert duplicates([_Row(1, "a"), _Row(2, "b")], lambda r: r._key) == {}


def test_a_row_with_no_natural_key_is_never_a_duplicate():
    """`<none:pk>` keys make unidentifiable rows unique by construction.

    A row nobody can name is not one this script should offer to delete, and
    two of them are not evidence of anything.
    """
    duplicates = _load_finder()["duplicates"]

    rows = [_Row(1, "<none:1>"), _Row(2, "<none:2>")]
    assert duplicates(rows, lambda r: r._key) == {}


_ON_THE_ROAD = [
    {
        "to_org_slug": "a-partner",
        "quantity": "10",
        "quantity_unit": "carton",
        "dispatched_days_ago": 6,
        "expected_days_ago": 1,
        "reference": "A-PLACEHOLDER-CONSIGNMENT",
        "carrier": "A Placeholder Carrier",
    }
]


def test_seeding_stock_on_the_road_again_adds_nothing(seeded):
    """A consignment dispatched twice is two lorries that never existed.

    Worse than an extra row: a consignment moves stock into the in-transit
    point and out of the warehouse, so a second one takes the warehouse
    balance down again for goods that only ever left once.
    """
    module, scopes = seeded
    chc = scopes["chc"]
    document = {**_DOCUMENT, "chc_chain": {**_CHAIN, "on_the_road": _ON_THE_ROAD}}
    chain = module.seed_chain(chc["access"], _CHAIN, chc["reference"])
    module.seed_on_the_road(chc["access"], document, chc["reference"], chain)
    before = _counts()

    module.seed_on_the_road(chc["access"], document, chc["reference"], chain)

    grew = _grew(before, _counts())
    assert grew == {}, "second run grew: " + ", ".join(f"{n} {a}->{b}" for n, (a, b) in sorted(grew.items()))
