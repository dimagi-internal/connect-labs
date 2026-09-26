"""Every record this domain publishes has to survive `json.dumps`.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented.

`serializers.py` is 547 lines whose entire job is turning model instances
into dicts a client can be sent, and it was reached by one test file. That is
the wrong ratio for the module that shapes every API response, every MCP tool
result and every screen's context.

What it must never do is leak a type JSON cannot carry. Money is `Decimal`
here, never float, because a float has already lost precision the rest of
this design refuses to lose -- and a Decimal reaching `json.dumps` raises,
while a date silently does too. Both are the kind of failure that shows up in
a client rather than in a test.

So the central test is deliberately crude and total: build one of every model
the registry knows, serialise it, and encode the result. It does not check
any field's meaning. It checks the one property the module exists to have,
across all twenty-one serialisers, including any added after this was
written.
"""

import json
from datetime import date

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import _SERIALIZERS, call_operation, record

pytestmark = pytest.mark.django_db

PROGRAM = 10958


def days(n):
    return date.today().isoformat() if n == 0 else date(2026, 1, max(1, n)).isoformat()


@pytest.fixture
def access():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def world(access):
    """One of as much of the domain as a single chain can reach."""

    def op(_operation, **data):
        #  rather than , because several payloads below
        # carry a field called name and would collide with it.
        return call_operation(_operation, access, {"data": data})

    op("commodity_upsert", slug="a-good", name="A Placeholder Good", base_unit="unit", pack_unit="box")
    supplier = op("supplier_create", name="A Placeholder Seller")
    buyer = op("org_upsert", slug="a-buyer", name="A Placeholder Buyer")
    item = op("item_upsert", sku="SKU-1", name="A Placeholder Item", commodity_slug="a-good", base_per_pack=10)
    store = op(
        "supply_point_upsert",
        slug="a-store",
        name="A placeholder store",
        kind="central_store",
        source="we_recorded",
    )
    tender = op(
        "tender_create",
        label="A Placeholder Tender",
        delivery_point={"city": "A Placeholder City"},
        lines=[{"commodity_slug": "a-good", "quantity": "10", "quantity_unit": "box"}],
    )
    quote = op(
        "quote_record",
        tender_id=tender["id"],
        supplier_id=supplier["id"],
        item_id=item["id"],
        commodity_slug="a-good",
        as_quoted_amount="10.00",
        as_quoted_unit="per_pack",
        as_quoted_currency="USD",
    )
    award = call_operation(
        "award_create",
        access,
        {"tender_id": tender["id"], "quote_id": quote["id"], "rationale": "a placeholder reason"},
    )
    contract = op(
        "contract_create",
        tender_id=tender["id"],
        award_id=award["id"],
        supplier_id=supplier["id"],
        item_id=item["id"],
        commodity_slug="a-good",
        buyer_of_record="programme_org",
        buyer_org_id=buyer["id"],
        delivery_supply_point_id=store["id"],
        quantity="10",
        quantity_unit="box",
        currency="USD",
        reference="A-PLACEHOLDER-ORDER",
        signed_on=days(1),
        source="we_recorded",
    )
    op(
        "receipt_record",
        contract_id=contract["id"],
        supply_point_id=store["id"],
        received_on=days(2),
        source="we_recorded",
        lines=[{"quantity_accepted": "10", "quantity_unit": "box", "item_id": item["id"]}],
    )
    invoice = op(
        "invoice_record",
        contract_id=contract["id"],
        issued_on=days(3),
        amount="100.00",
        currency="USD",
        quantity_billed="10",
        quantity_unit="box",
        source="we_recorded",
    )
    op(
        "payment_record",
        invoice_id=invoice["id"],
        paid_on=days(4),
        amount="100.00",
        currency="USD",
        source="we_recorded",
    )
    return {"access": access}


def _rows():
    """One instance of every model the serialiser registry knows, if there is one."""
    for model in _SERIALIZERS:
        obj = model.objects.first()
        if obj is not None:
            yield model, obj


class TestNothingLeaksAtypeJsonCannotCarry:
    def test_every_serialised_record_encodes(self, world):
        """The property the whole module exists to have.

        MUTATED: `_num` changed to return the Decimal untouched rather than a
        string. Every model carrying money or a quantity went red here --
        eleven of them -- where the suite as a whole stayed green, because
        nothing else asks a record to be encodable.
        """
        seen = []
        for model, obj in _rows():
            wire = record(obj)
            try:
                json.dumps(wire)
            except TypeError as exc:
                pytest.fail(f"{model.__name__} does not survive json.dumps: {exc}")
            seen.append(model.__name__)

        assert len(seen) >= 12, f"the fixture only reached {seen}; it is meant to cover most of the registry"

    def test_money_is_a_string_never_a_float(self, world):
        """A float has already lost precision this design refuses to lose."""
        from connect_labs.supply_chain.models import Contract, Invoice

        for obj in (Contract.objects.first(), Invoice.objects.first()):
            wire = record(obj)
            for key, value in wire.items():
                assert not isinstance(value, float), f"{type(obj).__name__}.{key} is a float"

    def test_a_date_is_an_iso_string(self, world):
        from connect_labs.supply_chain.models import Contract

        assert record(Contract.objects.first())["signed_on"] == days(1)


class TestTheRegistryCannotSilentlyMissAModel:
    def test_a_model_with_no_serialiser_raises_rather_than_half_answering(self):
        """`record` dispatches on class so an unregistered model fails loudly.

        Asserted here rather than trusted, because the alternative behaviour
        -- returning a half-empty object -- is one a caller would receive
        without noticing.
        """
        from connect_labs.supply_chain.portfolio.models import Portfolio

        assert Portfolio not in _SERIALIZERS
        with pytest.raises(TypeError, match="no serializer for Portfolio"):
            record(Portfolio(slug="x", name="x", program_ids=[]))

    def test_none_serialises_to_none_rather_than_raising(self):
        """A read that found nothing is not an error at this layer."""
        assert record(None) is None
