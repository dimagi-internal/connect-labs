"""The fulfilment tier: buyer-aware landed cost, the three-way match, and
evidence.

The two behaviours worth the most here are refusals. A landed total computed
against a default buyer, and a duty relief honoured without a document, are
both numbers that look authoritative and are wrong -- and on the round this
was designed against, the whole price advantage of the chosen route rested on
exactly that relief.
"""

import base64
from datetime import date

import jsonschema
import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Movement
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10501
TODAY = date(2026, 9, 12)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def setup(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150},
    )
    item = op(
        da,
        "item_upsert",
        data={
            "sku": "dabs-rutf",
            "name": "DABS RUTF",
            "commodity_slug": "rutf",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
        },
    )
    supplier = op(da, "supplier_create", data={"name": "DABS", "type": "manufacturer"})
    partner = op(da, "org_upsert", data={"slug": "llo-kano", "name": "Kano partner", "kind": "partner_org"})
    store = op(
        da,
        "supply_point_upsert",
        data={"slug": "central-store", "name": "Central store", "kind": "central_store", "source": "we_recorded"},
    )
    return {"item": item, "supplier": supplier, "partner": partner, "store": store}


def _contract(da, setup, **overrides):
    data = {
        "commodity_slug": "rutf",
        "supplier_id": setup["supplier"]["id"],
        "item_id": setup["item"]["id"],
        "buyer_of_record": "partner_org",
        "buyer_org_id": setup["partner"]["id"],
        "source": "partner_reported",
        "quantity": "500",
        "quantity_unit": "carton",
        "unit_price": "52.42",
        "unit_price_unit": "per_pack",
        "currency": "USD",
        "freight_basis": "excluded",
        "freight_amount": "2186.58",
        "delivery_supply_point_id": setup["store"]["id"],
    }
    data.update(overrides)
    # A None override means "do not send this key at all", which is what a
    # caller omitting a field actually does.
    data = {key: value for key, value in data.items() if value is not None}
    return op(da, "contract_create", data=data)


@pytest.fixture
def chain(da, setup):
    """One row of every kind a document can be evidence for.

    Built through the operations so each id is a real, scoped row -- the
    point of the walking test is that a DECLARED target is actually
    writable, and a fixture that faked the ids would prove nothing.
    """
    round_ = op(
        da,
        "round_create",
        data={
            "label": "Round 1",
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
        },
    )
    op(da, "round_open", round_id=round_["id"])
    quote = op(
        da,
        "quote_record",
        data={
            "round_id": round_["id"],
            "supplier_id": setup["supplier"]["id"],
            "commodity_slug": "rutf",
            "as_quoted_amount": "52.42",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "500",
            "quantity_basis_unit": "carton",
            "pack_spec_source": "not_stated",
        },
    )
    award = op(da, "award_create", round_id=round_["id"], quote_id=quote["id"], rationale="only offer")
    contract = _contract(da, setup)
    shipment = op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "source": "partner_reported",
            "lines": [{"item_id": setup["item"]["id"], "quantity": "500", "quantity_unit": "carton"}],
        },
    )
    receipt = op(
        da,
        "receipt_record",
        data={
            "contract_id": contract["id"],
            "shipment_id": shipment["id"],
            "supply_point_id": setup["store"]["id"],
            "received_on": "2026-06-01",
            "source": "partner_reported",
            "lines": [{"item_id": setup["item"]["id"], "quantity_accepted": "500", "quantity_unit": "carton"}],
        },
    )
    invoice = op(
        da,
        "invoice_record",
        data={
            "contract_id": contract["id"],
            "amount": "28396.58",
            "currency": "USD",
            "source": "supplier_reported",
        },
    )
    payment = op(
        da,
        "payment_record",
        data={
            "invoice_id": invoice["id"],
            "amount": "28396.58",
            "currency": "USD",
            "paid_on": "2026-06-15",
            "source": "partner_reported",
        },
    )
    count = op(
        da,
        "stock_count_record",
        data={
            "supply_point_id": setup["store"]["id"],
            "commodity_slug": "rutf",
            "item_id": setup["item"]["id"],
            "kind": "physical_count",
            "counted_on": "2026-06-20",
            "quantity": "480",
            "quantity_unit": "carton",
            "source": "we_recorded",
        },
    )
    return {
        "round": round_["id"],
        "quote": quote["id"],
        "award": award["id"],
        "contract": contract["id"],
        "shipment": shipment["id"],
        "receipt": receipt["id"],
        "invoice": invoice["id"],
        "payment": payment["id"],
        "stock_count": count["id"],
        "supply_point": setup["store"]["id"],
        "supplier": setup["supplier"]["id"],
        "item": setup["item"]["id"],
    }


class TestLandedCost:
    def test_a_relief_claimed_without_a_document_is_unconfirmed_not_zero(self, da, setup):
        contract = _contract(da, setup, duty_relief_claimed=True, duties_basis="excluded")
        costed = op(da, "contract_landed_cost", contract_id=contract["id"])

        assert "unconfirmed" in costed["duty"]
        assert any("asserted, not evidenced" in r for r in costed["duty"]["unconfirmed"])
        assert "unconfirmed" in costed["landed_total"]
        assert costed["duty_relief_evidenced"] is False

    def test_a_relief_with_its_document_attached_is_honoured(self, da, setup):
        contract = _contract(da, setup, duty_relief_claimed=True, duties_basis="excluded")
        document = op(
            da,
            "document_attach",
            data={
                "kind": "duty_exemption",
                "title": "Exemption certificate",
                "source": "document",
                "contract_id": contract["id"],
                "filename": "exemption.pdf",
                "content_base64": base64.b64encode(b"a scanned certificate").decode(),
            },
        )
        op(
            da,
            "contract_update",
            contract_id=contract["id"],
            data={"duty_relief_document_id": document["id"], "vat_basis": "included"},
        )

        costed = op(da, "contract_landed_cost", contract_id=contract["id"])
        assert costed["duty"]["amount"] == "0"
        # 500 x 52.42 + 2186.58 freight
        assert costed["landed_total"]["amount"] == "28396.58"
        assert costed["duty_relief_evidenced"] is True

    def test_the_same_contract_costs_differently_under_each_buyer(self, da, setup):
        contract = _contract(
            da, setup, duties_basis="excluded", duties_amount="3000", vat_basis="excluded", vat_amount="1966.50"
        )
        costed = op(da, "contract_landed_cost", contract_id=contract["id"], compare_buyers=True)

        # We import: duty and VAT fall due and cannot be reclaimed.
        assert costed["by_buyer"]["programme_org"]["amount"] == "33363.08"
        # An agency imports under its own status, price all-in.
        assert costed["by_buyer"]["agency"]["amount"] == "28396.58"
        # The partner here has claimed no relief, so the stated amounts stand.
        assert costed["by_buyer"]["partner_org"]["amount"] == "33363.08"

    def test_a_contract_that_does_not_say_whether_freight_is_included_is_unconfirmed(self, da, setup):
        contract = _contract(
            da,
            setup,
            freight_basis="not_specified",
            freight_amount=None,
            duties_basis="included",
            vat_basis="included",
        )
        costed = op(da, "contract_landed_cost", contract_id=contract["id"])
        assert "unconfirmed" in costed["landed_total"]
        assert any("freight" in r for r in costed["landed_total"]["unconfirmed"])

    def test_the_landed_total_always_names_the_buyer_it_assumed(self, da, setup):
        contract = _contract(da, setup, duties_basis="included", vat_basis="included")
        costed = op(da, "contract_landed_cost", contract_id=contract["id"])
        assert costed["buyer_of_record"] == "partner_org"


class TestReceiptsAndStock:
    def _ship_and_receive(self, da, setup, contract, accepted="300", rejected="0"):
        shipment = op(
            da,
            "shipment_record",
            data={
                "contract_id": contract["id"],
                "reference": "SH-1",
                "status": "delivered",
                "dispatched_on": "2026-06-01",
                "source": "supplier_reported",
                "lines": [
                    {
                        "item_id": setup["item"]["id"],
                        "batch": "DB-2605-A",
                        "expiry": "2028-05-31",
                        "quantity": "300",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        receipt = op(
            da,
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "shipment_id": shipment["id"],
                "supply_point_id": setup["store"]["id"],
                "received_on": "2026-06-12",
                "reference": "GRN-001",
                "source": "partner_reported",
                "lines": [
                    {
                        "item_id": setup["item"]["id"],
                        "batch": "DB-2605-A",
                        "expiry": "2028-05-31",
                        "quantity_accepted": accepted,
                        "quantity_rejected": rejected,
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        return shipment, receipt

    def test_a_receipt_creates_stock_and_a_shipment_does_not(self, da, setup):
        contract = _contract(da, setup)
        shipment = op(
            da,
            "shipment_record",
            data={
                "contract_id": contract["id"],
                "status": "at_customs",
                "source": "supplier_reported",
                "lines": [{"item_id": setup["item"]["id"], "quantity": "200", "quantity_unit": "carton"}],
            },
        )
        assert shipment["is_in_transit"] is True
        position = op(da, "stock_position", supply_point_id=setup["store"]["id"], item_id=setup["item"]["id"])
        assert position["on_hand"]["amount"] == "0", "goods in transit were counted as stock"
        assert position["in_transit"]["amount"] == "200"

        self._ship_and_receive(da, setup, contract)
        after = op(da, "stock_position", supply_point_id=setup["store"]["id"], item_id=setup["item"]["id"])
        assert after["on_hand"]["amount"] == "300"

    def test_rejected_quantity_never_enters_the_ledger(self, da, setup):
        contract = _contract(da, setup)
        self._ship_and_receive(da, setup, contract, accepted="280", rejected="20")
        position = op(da, "stock_position", supply_point_id=setup["store"]["id"], item_id=setup["item"]["id"])
        assert position["on_hand"]["amount"] == "280"
        assert Movement.objects.filter(kind="receipt").count() == 1

    def test_a_receipt_advances_the_contract_status_from_what_arrived(self, da, setup):
        contract = _contract(da, setup)
        self._ship_and_receive(da, setup, contract, accepted="300")
        assert op(da, "contract_get", contract_id=contract["id"])["status"] == "part_received"

        op(
            da,
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "supply_point_id": setup["store"]["id"],
                "received_on": "2026-07-20",
                "source": "partner_reported",
                "lines": [{"item_id": setup["item"]["id"], "quantity_accepted": "200", "quantity_unit": "carton"}],
            },
        )
        assert op(da, "contract_get", contract_id=contract["id"])["status"] == "received"

    def test_a_receipt_needs_at_least_one_line(self, da, setup):
        contract = _contract(da, setup)
        with pytest.raises(jsonschema.ValidationError):
            op(
                da,
                "receipt_record",
                data={
                    "contract_id": contract["id"],
                    "supply_point_id": setup["store"]["id"],
                    "received_on": "2026-06-12",
                    "source": "partner_reported",
                    "lines": [],
                },
            )


class TestThreeWayMatch:
    def test_an_invoice_for_more_than_arrived_is_caught(self, da, setup):
        contract = _contract(da, setup)
        op(
            da,
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "supply_point_id": setup["store"]["id"],
                "received_on": "2026-06-12",
                "source": "partner_reported",
                "lines": [{"item_id": setup["item"]["id"], "quantity_accepted": "300", "quantity_unit": "carton"}],
            },
        )
        op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "reference": "INV-1",
                "issued_on": "2026-06-15",
                "amount": "28396.58",
                "quantity_billed": "500",
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )

        match = op(da, "contract_match", contract_id=contract["id"])
        assert match["ordered"]["amount"] == "500"
        assert match["received"]["amount"] == "300"
        assert match["invoiced"]["amount"] == "500"
        assert match["over_invoiced"]["amount"] == "200"
        assert match["status"] == "over_invoiced"
        assert match["matches"] is False
        # 300 cartons x 52.42 -- what arrived, not what was billed.
        assert match["payable_now"]["amount"] == "15726"

    def test_a_clean_delivery_matches(self, da, setup):
        contract = _contract(da, setup)
        op(
            da,
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "supply_point_id": setup["store"]["id"],
                "received_on": "2026-06-12",
                "source": "we_recorded",
                "lines": [{"item_id": setup["item"]["id"], "quantity_accepted": "500", "quantity_unit": "carton"}],
            },
        )
        op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "amount": "28396.58",
                "quantity_billed": "500",
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )

        match = op(da, "contract_match", contract_id=contract["id"])
        assert match["status"] == "fully_received"
        assert match["matches"] is True

    def test_a_payment_moves_the_invoice_status_and_reduces_what_is_payable(self, da, setup):
        contract = _contract(da, setup)
        op(
            da,
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "supply_point_id": setup["store"]["id"],
                "received_on": "2026-06-12",
                "source": "we_recorded",
                "lines": [{"item_id": setup["item"]["id"], "quantity_accepted": "500", "quantity_unit": "carton"}],
            },
        )
        invoice = op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "amount": "26210.00",
                "quantity_billed": "500",
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )

        op(
            da,
            "payment_record",
            data={
                "invoice_id": invoice["id"],
                "paid_on": "2026-07-01",
                "amount": "10000.00",
                "source": "partner_reported",
            },
        )
        assert op(da, "invoice_list", contract_id=contract["id"])[0]["status"] == "part_paid"

        op(
            da,
            "payment_record",
            data={
                "invoice_id": invoice["id"],
                "paid_on": "2026-07-15",
                "amount": "16210.00",
                "source": "partner_reported",
            },
        )
        assert op(da, "invoice_list", contract_id=contract["id"])[0]["status"] == "paid"

        match = op(da, "contract_match", contract_id=contract["id"])
        assert match["paid_amount"]["amount"] == "26210"
        assert match["payable_now"]["amount"] == "0"


class TestDocuments:
    def test_a_document_needs_a_location(self, da, setup):
        with pytest.raises(ValueError, match="either content_base64"):
            op(da, "document_attach", data={"kind": "invoice", "source": "document"})

    def test_two_locations_for_one_document_is_refused(self, da, setup):
        with pytest.raises(ValueError, match="not both"):
            op(
                da,
                "document_attach",
                data={
                    "kind": "invoice",
                    "source": "document",
                    "external_url": "https://example.test/a.pdf",
                    "content_base64": base64.b64encode(b"x").decode(),
                },
            )

    def test_an_uploaded_file_is_stored_and_hashed(self, da, setup):
        document = op(
            da,
            "document_attach",
            data={
                "kind": "certificate_of_analysis",
                "title": "CoA lot DB-2605-A",
                "filename": "coa.pdf",
                "source": "document",
                "content_base64": base64.b64encode(b"certificate bytes").decode(),
            },
        )
        assert document["is_stored"] is True
        assert len(document["sha256"]) == 64
        assert document["size_bytes"] == len(b"certificate bytes")

    def test_a_link_is_kept_as_a_link(self, da, setup):
        document = op(
            da,
            "document_attach",
            data={
                "kind": "purchase_order",
                "source": "partner_reported",
                "external_url": "https://drive.example.test/po-123",
            },
        )
        assert document["is_stored"] is False
        assert document["external_url"] == "https://drive.example.test/po-123"

    def test_a_document_linked_to_a_missing_contract_is_refused(self, da, setup):
        with pytest.raises(ValueError, match="contract 9999 does not exist"):
            op(
                da,
                "document_attach",
                data={
                    "kind": "invoice",
                    "source": "document",
                    "contract_id": 9999,
                    "external_url": "https://example.test/a.pdf",
                },
            )

    def test_an_oversized_upload_names_the_alternative(self, da, setup):
        from connect_labs.supply_chain.fulfilment.repository import MAX_UPLOAD_BYTES

        with pytest.raises(ValueError, match="attach it with external_url instead"):
            op(
                da,
                "document_attach",
                data={
                    "kind": "other",
                    "source": "document",
                    "content_base64": base64.b64encode(b"x" * (MAX_UPLOAD_BYTES + 1)).decode(),
                },
            )


class TestPartnerWritesThroughTheSameSurface:
    def test_a_partner_reported_row_is_marked_as_a_claim_not_an_observation(self, da, setup):
        contract = _contract(da, setup, source="partner_reported", recorded_by_org_id=setup["partner"]["id"])
        assert contract["source"] == "partner_reported"
        assert contract["recorded_by_org_id"] == setup["partner"]["id"]
        assert contract["witnessed"] is False

    def test_our_own_record_is_an_observation(self, da, setup):
        contract = _contract(da, setup, source="we_recorded")
        assert contract["witnessed"] is True

    def test_every_fulfilment_write_requires_a_source(self, da, setup):
        contract = _contract(da, setup)
        for name, data in [
            ("shipment_record", {"contract_id": contract["id"]}),
            ("invoice_record", {"contract_id": contract["id"], "amount": "1.00"}),
            ("document_attach", {"kind": "invoice", "external_url": "https://e.test/a"}),
        ]:
            with pytest.raises(jsonschema.ValidationError):
                op(da, name, data=data)


class TestDocumentTargets:
    """What a document can be evidence for.

    A quote could not carry one, which is what prompted this: EHA's price
    cites "Pro-Forma Invoice SO239306" and that invoice had nowhere to sit.
    Nor could a payment, an award, a distribution, a stock count or an item --
    so there was no home for a remittance advice, a worker's confirmation
    that stock arrived, or a photograph of the product.
    """

    def _doc(self, da, **links):
        payload = {"kind": "other", "title": "evidence", "source": "we_recorded", **links}
        return call_operation("document_attach", da, {"data": payload})

    def test_every_declared_target_can_actually_be_written(self, da, chain):
        """The declaration, the model fields, the schema and the query have to
        agree. Three of those four used to be maintained by hand, so this
        walks the declared list rather than a list written here -- a target
        added to records with no field or no schema entry fails here.
        """
        from connect_labs.supply_chain import records

        assert len(records.DOCUMENT_LINKS) == 14
        for name in records.DOCUMENT_LINKS:
            target_id = chain.get(name)
            if target_id is None:
                continue
            doc = self._doc(da, **{f"{name}_id": target_id, "external_url": "https://example.test/e.pdf"})
            assert doc["links"][f"{name}_id"] == target_id, name
            assert call_operation("document_list", da, {f"{name}_id": target_id}), name

    def test_a_quote_carries_its_own_evidence(self, da, chain):
        """The case that started this."""
        doc = self._doc(
            da,
            kind="pro_forma_invoice",
            quote_id=chain["quote"],
            external_url="https://example.test/SO239306.pdf",
        )
        assert doc["links"]["quote_id"] == chain["quote"]
        found = call_operation("document_list", da, {"quote_id": chain["quote"]})
        assert [d["id"] for d in found] == [doc["id"]]

    def test_a_document_evidences_one_thing(self, da, chain):
        """Two links is one row claiming to be evidence for two facts, which
        is two documents that can later disagree."""
        with pytest.raises(ValueError, match="one thing"):
            self._doc(
                da,
                quote_id=chain["quote"],
                contract_id=chain["contract"],
                external_url="https://example.test/e.pdf",
            )

    def test_a_programme_level_document_needs_no_target(self, da):
        """None is legitimate -- a framework agreement or a tax ruling belongs
        to the programme rather than to one row in it."""
        doc = self._doc(da, external_url="https://example.test/policy.pdf")
        assert all(doc["links"][f"{n}_id"] is None for n in ("quote", "contract", "supplier"))


def test_every_place_that_knows_the_document_targets_reads_the_declaration():
    """The same list lived in SEVEN places, and adding one target found each
    of them in turn, as a different failure:

      model fields          -- no column
      _DOCUMENT_LINKS       -- the query silently ignored the filter
      document_attach schema -- "additionalProperties" naming nothing
      document_list schema  -- same
      _resolve_document_link -- KeyError: 'quote'
      the serializer        -- the id never came back
      the data access       -- no scoped getter, so no way to check the
                               target belongs to this programme

    Each was found by a test failing, one at a time, which is six rounds of
    the same discovery. So this asserts the agreement directly: every
    declared target has a model field, a scoped getter, a slot in both
    schemas, and a key in the published shape.
    """
    from connect_labs.supply_chain import records
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.models import Document
    from connect_labs.supply_chain.operations import get_operation
    from connect_labs.supply_chain.serializers import document as serialize

    declared = set(records.DOCUMENT_LINKS)
    assert declared, "no document targets declared"

    fields = {f.name for f in Document._meta.get_fields() if f.many_to_one}
    assert declared <= fields, f"declared with no model field: {sorted(declared - fields)}"

    missing_getters = {n for n in declared if not hasattr(SupplyDataAccess, f"get_{n}")}
    assert not missing_getters, f"declared with no scoped getter: {sorted(missing_getters)}"

    attach = get_operation("document_attach").input_schema["properties"]["data"]["properties"]
    listing = get_operation("document_list").input_schema["properties"]
    for name in declared:
        assert f"{name}_id" in attach, f"{name} cannot be attached"
        assert f"{name}_id" in listing, f"{name} cannot be filtered on"

    published = serialize(Document(kind="other"))["links"]
    assert set(published) == {f"{n}_id" for n in declared}


class TestDocumentPanelScope:
    """The supplier panel shows that supplier's documents and no others.

    `list_documents` treats a None link id as "no filter", so passing a
    missing supplier id straight through would render every document in the
    programme under one supplier's name. `Quote.supplier` is non-nullable, so
    that exact path is unreachable -- this pins the property anyway, because
    the widening is silent and a nullable column later would turn it into a
    quiet disclosure rather than an error.
    """

    def test_a_none_link_id_does_not_widen_to_every_document(self, da, chain):
        from connect_labs.supply_chain.operations import call_operation as call

        call(
            "document_attach",
            da,
            {
                "data": {
                    "kind": "invoice",
                    "source": "we_recorded",
                    "contract_id": chain["contract"],
                    "external_url": "https://example.test/unrelated.pdf",
                }
            },
        )
        # What the view does when there is no supplier: ask for nothing.
        everything = call("document_list", da, {})
        assert everything, "fixture did not attach anything"
        assert call("document_list", da, {"supplier_id": chain["supplier"]}) == []

    def test_only_that_suppliers_documents_come_back(self, da, chain, setup):
        from connect_labs.supply_chain.operations import call_operation as call

        other = call("supplier_create", da, {"data": {"name": "Someone else"}})
        mine = call(
            "document_attach",
            da,
            {
                "data": {
                    "kind": "other",
                    "source": "we_recorded",
                    "supplier_id": chain["supplier"],
                    "external_url": "https://example.test/mine.pdf",
                }
            },
        )
        call(
            "document_attach",
            da,
            {
                "data": {
                    "kind": "other",
                    "source": "we_recorded",
                    "supplier_id": other["id"],
                    "external_url": "https://example.test/theirs.pdf",
                }
            },
        )
        found = call("document_list", da, {"supplier_id": chain["supplier"]})
        assert [d["id"] for d in found] == [mine["id"]]
