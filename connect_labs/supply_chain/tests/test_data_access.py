from unittest.mock import MagicMock

import pytest

from connect_labs.supply_chain import records
from connect_labs.supply_chain.data_access import SupplyDataAccess


@pytest.fixture
def da_without_org(monkeypatch):
    """No organisation at all — the reference tier must still work."""

    def fake_client(**kwargs):
        client = MagicMock()
        client.init_kwargs = kwargs
        client.get_records.return_value = []
        return client

    monkeypatch.setattr("connect_labs.supply_chain.data_access.LabsRecordAPIClient", fake_client)
    return SupplyDataAccess(access_token="t", program_id=7)


@pytest.fixture
def da(monkeypatch):
    """A data access whose two clients are mocks we can inspect."""
    made = []

    def fake_client(**kwargs):
        client = MagicMock(name=f"client{kwargs}")
        client.init_kwargs = kwargs
        client.get_records.return_value = []
        made.append(client)
        return client

    monkeypatch.setattr("connect_labs.supply_chain.data_access.LabsRecordAPIClient", fake_client)
    access = SupplyDataAccess(access_token="t", organization_id=42, program_id=7)
    access.made = made
    return access


def test_reference_and_procurement_use_separate_clients(da):
    assert da.reference_client is not da.program_client


def test_the_reference_client_carries_only_the_organisation(da):
    kwargs = da.reference_client.init_kwargs
    assert kwargs["organization_id"] == 42
    assert kwargs.get("program_id") is None
    assert kwargs.get("opportunity_id") is None
    assert da.reference_scope == "organization"


def test_a_slug_organisation_falls_back_to_programme_scope(monkeypatch):
    """A labs-only synthetic org id is a slug, and int() on it would raise.

    context.py hands us org["id"], which is a slug for synthetic organisations —
    the environment this app is developed and demoed in. Falling back keeps the
    registry per-programme instead of crashing.
    """
    made = []

    def fake_client(**kwargs):
        client = MagicMock()
        client.init_kwargs = kwargs
        client.get_records.return_value = []
        made.append(client)
        return client

    monkeypatch.setattr("connect_labs.supply_chain.data_access.LabsRecordAPIClient", fake_client)
    da = SupplyDataAccess(access_token="t", organization_id="labs-synthetic-nutrition-demo", program_id=7)
    assert da.reference_scope == "program"
    assert da.reference_client.init_kwargs.get("organization_id") is None
    assert da.reference_client.init_kwargs["program_id"] == 7


def test_an_absent_organisation_also_falls_back(da_without_org):
    assert da_without_org.reference_scope == "program"


def test_reference_writes_record_which_tier_produced_them(da):
    da.reference_client.get_records.return_value = []
    da.upsert_commodity({"slug": "rutf", "name": "RUTF"})
    data = da.reference_client.create_record.call_args.kwargs["data"]
    assert data["reference_scope"] == "organization"


def test_an_item_with_a_valid_gtin_is_written(da):
    from connect_labs.supply_chain import gs1

    da.reference_client.get_records.return_value = []
    gtin = gs1.make_gtin("0123456", "7890")
    da.upsert_item({"sku": "northwind-rutf-92g", "commodity_slug": "rutf", "gtin_pack": gtin})
    assert da.reference_client.create_record.called


def test_an_item_with_a_bad_gtin_check_digit_is_refused(da):
    from connect_labs.supply_chain import gs1

    da.reference_client.get_records.return_value = []
    gtin = gs1.make_gtin("0123456", "7890")
    broken = gtin[:-1] + str((int(gtin[-1]) + 1) % 10)
    with pytest.raises(ValueError, match="gtin_pack"):
        da.upsert_item({"sku": "x", "commodity_slug": "rutf", "gtin_pack": broken})
    assert not da.reference_client.create_record.called


def test_an_item_without_any_gtin_is_fine(da):
    """Most items will have no GTIN for a long time. That must not block them."""
    da.reference_client.get_records.return_value = []
    da.upsert_item({"sku": "x", "commodity_slug": "rutf"})
    assert da.reference_client.create_record.called


def test_the_procurement_client_carries_the_programme(da):
    assert da.program_client.init_kwargs["program_id"] == 7


def test_commodity_lookup_filters_on_a_bare_kwarg_not_a_data_prefix(da):
    """get_records() prepends data__ itself; passing data__slug would double it."""
    da.get_commodity("rutf")
    kwargs = da.reference_client.get_records.call_args.kwargs
    assert kwargs["slug"] == "rutf"
    assert "data__slug" not in kwargs
    assert kwargs["type"] == records.COMMODITY_TYPE


def test_quotes_are_read_from_the_programme_client(da):
    da.list_quotes(round_id=3)
    assert da.program_client.get_records.called
    assert da.program_client.get_records.call_args.kwargs["type"] == records.QUOTE_TYPE


def test_supplier_search_filters_client_side_on_name(da):
    from connect_labs.supply_chain.models import SupplierRecord
    from connect_labs.supply_chain.tests.conftest import wrap

    da.reference_client.get_records.return_value = [
        wrap(SupplierRecord, {"name": "Northwind Nutrition"}, record_id=1),
        wrap(SupplierRecord, {"name": "Harmattan Foods"}, record_id=2),
    ]
    results = da.list_suppliers(search="northwind")
    assert [s.name for s in results] == ["Northwind Nutrition"]


def test_voiding_a_quote_records_the_reason_and_keeps_the_record(da):
    from connect_labs.supply_chain.models import QuoteRecord
    from connect_labs.supply_chain.tests.conftest import wrap

    existing = wrap(QuoteRecord, {"round_id": 1, "as_quoted_amount": "50"}, record_id=5)
    da.program_client.get_record_by_id.return_value = existing
    da.void_quote(5, reason="duplicate of quote 4")

    data = da.program_client.update_record.call_args.kwargs["data"]
    assert data["voided"] is True
    assert data["void_reason"] == "duplicate of quote 4"
    assert data["as_quoted_amount"] == "50"  # the original statement is untouched
    assert not da.program_client.delete_record.called


def test_superseding_a_quote_creates_a_new_version_and_links_both_ways(da):
    from connect_labs.supply_chain.models import QuoteRecord
    from connect_labs.supply_chain.tests.conftest import wrap

    existing = wrap(QuoteRecord, {"round_id": 1, "version": 1, "as_quoted_amount": "50"}, record_id=5)
    da.program_client.get_record_by_id.return_value = existing
    created = wrap(QuoteRecord, {"version": 2}, record_id=6)
    da.program_client.create_record.return_value = created

    da.supersede_quote(5, {"as_quoted_amount": "48"}, reason="supplier corrected the figure")

    new_data = da.program_client.create_record.call_args.kwargs["data"]
    assert new_data["version"] == 2
    assert new_data["supersedes_quote_id"] == 5
    assert new_data["correction_reason"] == "supplier corrected the figure"
    old_data = da.program_client.update_record.call_args.kwargs["data"]
    assert old_data["superseded_by_quote_id"] == 6


def test_opening_a_round_without_a_delivery_point_is_refused(da):
    from connect_labs.supply_chain.models import RoundRecord
    from connect_labs.supply_chain.tests.conftest import wrap

    da.program_client.get_record_by_id.return_value = wrap(
        RoundRecord, {"label": "Round 2", "status": "draft", "delivery_point": {}}, record_id=9
    )
    with pytest.raises(ValueError, match="delivery point"):
        da.open_round(9)
