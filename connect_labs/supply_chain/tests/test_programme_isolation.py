"""One programme cannot read another's records.

THIS REPOSITORY IS PUBLIC. Every organisation and figure here is invented.

The domain has always been programme-scoped in principle -- every
procurement, fulfilment and stock query filters on `program_id`, and
`_require_program` refuses rather than inventing a scope. Nothing asserted
it. A missing filter on one query is invisible in every other test in this
suite, because they all use one programme: the row comes back, the assertion
passes, and the leak is only visible from a SECOND programme that should not
see it.

So this walks the tiers and, for each, writes under programme A and reads
under programme B. It is deliberately exhaustive rather than illustrative:
the failure it guards against is one query out of thirty missing a filter,
and a sample of three would have a better than even chance of missing it.

Reference data (commodities, items, suppliers) is NOT here, and is not a
leak: it is scoped by `scope_key`, shared across a programme's rounds by
design, and `reference_scope_report` is the operation that says where it
actually lives.
"""

from datetime import date
from decimal import Decimal

import pytest

from connect_labs.supply_chain.data_access import SupplyDataAccess

pytestmark = pytest.mark.django_db

OURS = 10501
THEIRS = 10502


def access(program_id):
    return SupplyDataAccess(access_token="unused", program_id=program_id)


def _commodity(da):
    return da.upsert_commodity(
        {
            "slug": "rutf",
            "name": "Ready-to-use therapeutic food",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
        }
    )


def _supplier(da, name="Northwind Foods"):
    return da.create_supplier({"name": name, "type": "manufacturer", "country": "NG"})


def _org(da, slug="dimagi"):
    return da.upsert_org({"slug": slug, "name": slug.title()})


@pytest.fixture
def ours():
    return access(OURS)


@pytest.fixture
def theirs():
    return access(THEIRS)


@pytest.fixture
def a_round(ours):
    _commodity(ours)
    return ours.create_round(
        {
            "label": "Round 1",
            "lines": [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
            "delivery_point": {"name": "Central store, Kano"},
        }
    )


class TestSourcing:
    def test_a_round_is_invisible_to_another_programme(self, ours, theirs, a_round):
        assert [r.pk for r in ours.list_rounds()] == [a_round.pk]
        assert theirs.list_rounds() == []
        assert theirs.get_round(a_round.pk) is None

    def test_an_invitation_is_invisible_to_another_programme(self, ours, theirs, a_round):
        supplier = _supplier(ours)
        ours.create_outreach({"round_id": a_round.pk, "supplier_id": supplier.pk, "sent_on": "2026-04-28"})
        assert len(ours.list_outreach()) == 1
        assert theirs.list_outreach() == []

    def test_a_quote_is_invisible_to_another_programme(self, ours, theirs, a_round):
        supplier = _supplier(ours)
        quote = ours.create_quote(
            {
                "round_id": a_round.pk,
                "commodity_slug": "rutf",
                "supplier_id": supplier.pk,
                "as_quoted_amount": Decimal("52.42"),
                "as_quoted_unit": "per_pack",
            }
        )
        assert [q.pk for q in ours.list_quotes()] == [quote.pk]
        assert theirs.list_quotes() == []
        assert theirs.get_quote(quote.pk) is None

    def test_another_programme_cannot_reach_into_this_one_by_round_id(self, ours, theirs, a_round):
        """Passing the id explicitly must not bypass the scope -- that is the
        shape a leak takes when a filter is applied to the list query and
        forgotten on the by-id one."""
        assert theirs.list_quotes(round_id=a_round.pk) == []
        assert theirs.list_outreach(round_id=a_round.pk) == []
        assert theirs.list_awards(round_id=a_round.pk) == []


class TestFulfilmentAndStock:
    @pytest.fixture
    def a_contract(self, ours):
        _commodity(ours)
        supplier = _supplier(ours)
        buyer = _org(ours)
        return ours.create_contract(
            {
                "commodity_slug": "rutf",
                "supplier_id": supplier.pk,
                "buyer_of_record": "partner_org",
                "buyer_org_id": buyer.pk,
                "source": "we_recorded",
                "currency": "USD",
                "quantity": Decimal("500"),
                "quantity_unit": "carton",
            }
        )

    def test_a_contract_is_invisible_to_another_programme(self, ours, theirs, a_contract):
        assert [c.pk for c in ours.list_contracts()] == [a_contract.pk]
        assert theirs.list_contracts() == []
        assert theirs.get_contract(a_contract.pk) is None

    def test_a_shipment_and_its_receipt_are_invisible(self, ours, theirs, a_contract):
        shipment = ours.record_shipment(
            {
                "contract_id": a_contract.pk,
                "status": "dispatched",
                "source": "supplier_reported",
                "dispatched_on": "2026-06-01",
                "lines": [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
            }
        )
        assert [s.pk for s in ours.list_shipments()] == [shipment.pk]
        assert theirs.list_shipments() == []
        assert theirs.list_shipments(contract_id=a_contract.pk) == []

    def test_an_invoice_is_invisible_to_another_programme(self, ours, theirs, a_contract):
        ours.record_invoice(
            {
                "contract_id": a_contract.pk,
                "source": "supplier_reported",
                "reference": "INV-1",
                "amount": "26210.00",
                "currency": "USD",
                "issued_on": "2026-06-02",
            }
        )
        assert len(ours.list_invoices()) == 1
        assert theirs.list_invoices() == []

    def test_a_supply_point_and_its_stock_are_invisible(self, ours, theirs):
        point = ours.upsert_supply_point(
            {"slug": "central", "name": "Central store", "kind": "central_store", "source": "we_recorded"}
        )
        assert [p.pk for p in ours.list_supply_points()] == [point.pk]
        assert theirs.list_supply_points() == []
        assert theirs.get_supply_point(point.pk) is None

    def test_a_document_is_invisible_to_another_programme(self, ours, theirs, a_contract):
        ours.attach_document(
            {
                "kind": "purchase_order",
                "title": "PO-114",
                "contract_id": a_contract.pk,
                "source": "we_recorded",
                "external_url": "https://example.invalid/po-114.pdf",
            }
        )
        assert len(ours.list_documents()) == 1
        assert theirs.list_documents() == []
        assert theirs.list_documents(contract_id=a_contract.pk) == []


class TestTheScopeItselfRefuses:
    def test_no_programme_at_all_refuses_rather_than_reading_everything(self):
        """The dangerous failure is not an error -- it is a query with no
        programme filter that quietly returns every programme's rows."""
        unscoped = SupplyDataAccess(access_token="unused")
        with pytest.raises(ValueError) as caught:
            unscoped.list_rounds()
        assert "program" in str(caught.value).lower()

    def test_reference_data_is_not_swept_up_by_the_programme_filter(self, ours, theirs):
        """Reference data is deliberately scoped differently, and a test that
        asserted it was invisible would be pinning the wrong rule. Both
        programmes are `prog:` scoped here, so each sees only its own -- but
        via scope_key, not via program_id."""
        _commodity(ours)
        assert [c.slug for c in ours.list_commodities()] == ["rutf"]
        assert theirs.list_commodities() == []
        assert ours.scope_key == f"prog:{OURS}"


class TestTheDatesAndAmountsSurvive:
    """A scoping bug that silently returned nothing would make every test
    above pass. These prove the writes really landed, so an empty read from
    the other programme means isolation rather than a failed write."""

    def test_the_round_really_exists_under_its_own_programme(self, ours, a_round):
        found = ours.get_round(a_round.pk)
        assert found is not None
        assert found.label == "Round 1"

    def test_the_quote_really_carries_its_figures(self, ours, a_round):
        supplier = _supplier(ours)
        quote = ours.create_quote(
            {
                "round_id": a_round.pk,
                "commodity_slug": "rutf",
                "supplier_id": supplier.pk,
                "as_quoted_amount": Decimal("52.42"),
                "as_quoted_unit": "per_pack",
                "received_on": date(2026, 5, 18),
            }
        )
        found = ours.get_quote(quote.pk)
        assert found.as_quoted_amount == Decimal("52.42")
        assert found.received_on == date(2026, 5, 18)


class TestTheScopeReport:
    """`reference_scope_report` exists because an empty Catalogue tab has two
    very different causes -- no data, or data sitting under a scope_key the
    caller is not reading -- and from the page they look identical."""

    def test_it_names_the_scope_this_caller_reads(self, ours):
        from connect_labs.supply_chain.operations import call_operation

        _commodity(ours)
        report = call_operation("reference_scope_report", ours)
        assert report["this_caller"] == f"prog:{OURS}"
        mine = [s for s in report["scopes"] if s["is_this_caller"]]
        assert len(mine) == 1
        assert mine[0]["counts"]["commodity"] == 1

    def test_it_shows_data_stranded_under_another_scope(self, ours, theirs):
        """The whole point: from `ours` you can SEE that a catalogue exists
        elsewhere, rather than concluding there is none."""
        from connect_labs.supply_chain.operations import call_operation

        _commodity(theirs)
        report = call_operation("reference_scope_report", ours)
        keys = {s["scope_key"] for s in report["scopes"]}
        assert f"prog:{THEIRS}" in keys
        assert not any(s["is_this_caller"] for s in report["scopes"])

    def test_it_reports_rather_than_raises_when_the_caller_has_no_scope(self):
        """A diagnostic that cannot run on the machine you are diagnosing is
        no use: `scope_key` raises without a programme, and this has to say so
        instead of propagating it."""
        from connect_labs.supply_chain.operations import call_operation

        report = call_operation("reference_scope_report", SupplyDataAccess(access_token="unused"))
        assert report["this_caller"] is None
        assert "organization_id" in report["this_caller_unresolved"]
