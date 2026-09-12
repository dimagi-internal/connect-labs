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


@pytest.fixture
def da_without_program(monkeypatch):
    """No programme at all — the reference tier's fallback still has to work.

    Reference reads/writes use a fixed experiment key ("supply:reference"),
    never program_experiment, so program_id must not be needed there. But a
    round/quote/award/purchase has no meaning outside a programme, so the
    procurement tier must refuse rather than write to the literal string
    "None".
    """

    def fake_client(**kwargs):
        client = MagicMock()
        client.init_kwargs = kwargs
        client.get_records.return_value = []
        return client

    monkeypatch.setattr("connect_labs.supply_chain.data_access.LabsRecordAPIClient", fake_client)
    return SupplyDataAccess(access_token="t")


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
    # The `da` fixture never supplies an opportunity_id, so nothing should be
    # invented here either -- see the dedicated real-vs-synthetic tests below
    # for the case that actually caught the bug.
    assert da.program_client.init_kwargs.get("opportunity_id") is None


def test_a_real_opportunity_is_never_stamped_onto_the_programme_client(monkeypatch):
    """LabsRecordAPIClient cannot tell a routing hint from a real scope: once its
    opportunity_id is set, create_record stamps it onto every payload and
    get_records sends it on every read. A round written while a REAL opp A was
    selected would go silently unreadable -- empty list, no error -- the moment
    opp B in the same programme was selected instead.
    """
    made = []

    def fake_client(**kwargs):
        client = MagicMock()
        client.init_kwargs = kwargs
        client.get_records.return_value = []
        made.append(client)
        return client

    monkeypatch.setattr("connect_labs.supply_chain.data_access.LabsRecordAPIClient", fake_client)
    da = SupplyDataAccess(access_token="t", program_id=7, opportunity_id=4821)
    assert da.program_client.init_kwargs.get("opportunity_id") is None


def test_a_labs_only_opportunity_id_is_passed_through_for_routing(monkeypatch):
    """A synthetic (>= LABS_ONLY_OPP_ID_FLOOR) opportunity id IS the routing hint
    the parameter exists for: passing it through is what lets
    LabsRecordAPIClient dispatch to the local backend instead of prod.
    """
    from connect_labs.labs.synthetic.models import LABS_ONLY_OPP_ID_FLOOR

    def fake_client(**kwargs):
        client = MagicMock()
        client.init_kwargs = kwargs
        client.get_records.return_value = []
        return client

    monkeypatch.setattr("connect_labs.supply_chain.data_access.LabsRecordAPIClient", fake_client)
    da = SupplyDataAccess(
        access_token="t",
        program_id=LABS_ONLY_OPP_ID_FLOOR,
        opportunity_id=LABS_ONLY_OPP_ID_FLOOR,
    )
    assert da.program_client.init_kwargs["opportunity_id"] == LABS_ONLY_OPP_ID_FLOOR


def test_the_fallback_reference_tier_also_never_carries_a_real_opportunity(monkeypatch):
    """reference_client IS program_client in the slug/fallback case, so a real
    opportunity would otherwise get stamped onto commodities/items/suppliers
    whose own reference_scope field says "program" -- misdescribing the record
    for the future lift-migration that reference_scope exists to drive.
    """

    def fake_client(**kwargs):
        client = MagicMock()
        client.init_kwargs = kwargs
        client.get_records.return_value = []
        return client

    monkeypatch.setattr("connect_labs.supply_chain.data_access.LabsRecordAPIClient", fake_client)
    da = SupplyDataAccess(
        access_token="t",
        organization_id="labs-synthetic-nutrition-demo",
        program_id=7,
        opportunity_id=4821,
    )
    assert da.reference_scope == "program"
    assert da.reference_client is da.program_client
    assert da.reference_client.init_kwargs.get("opportunity_id") is None


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


def test_procurement_without_a_programme_refuses_rather_than_writing_to_none(da_without_program):
    """str(None) == "None" would silently pin every procurement record from every
    programme-less caller onto the same fake experiment -- a cross-programme
    leak, the exact failure the two-tier scoping exists to prevent.
    """
    with pytest.raises(ValueError, match="program_id"):
        da_without_program.list_rounds()


def test_the_reference_fallback_never_needs_a_programme_to_read_or_write(da_without_program):
    """The programme-scoped fallback tier (no numeric org) is the synthetic-
    environment path this app is actually developed in. It must not require a
    program_id either, because reference records key off a fixed experiment
    string, not program_experiment.
    """
    assert da_without_program.reference_scope == "program"
    da_without_program.reference_client.get_records.return_value = []
    da_without_program.upsert_commodity({"slug": "rutf", "name": "RUTF"})
    assert da_without_program.reference_client.create_record.called


def test_upserting_a_commodity_returns_the_typed_record_re_read_after_the_write(da):
    """create_record/update_record return a bare LocalLabsRecord (no model_class
    param): a caller doing .name on the "typed" return value would raise
    AttributeError despite the -> CommodityRecord annotation, unless the write
    is re-read through get_commodity.
    """
    from connect_labs.supply_chain.models import CommodityRecord
    from connect_labs.supply_chain.tests.conftest import wrap

    da.reference_client.get_records.return_value = [
        wrap(CommodityRecord, {"slug": "rutf", "name": "RUTF"}, record_id=9)
    ]
    result = da.upsert_commodity({"slug": "rutf", "name": "RUTF"})
    assert result.name == "RUTF"


def test_upserting_an_item_returns_the_typed_record_re_read_after_the_write(da):
    from connect_labs.supply_chain.models import ItemRecord
    from connect_labs.supply_chain.tests.conftest import wrap

    da.reference_client.get_records.return_value = [
        wrap(ItemRecord, {"sku": "x", "commodity_slug": "rutf"}, record_id=9)
    ]
    result = da.upsert_item({"sku": "x", "commodity_slug": "rutf"})
    assert result.sku == "x"


def test_updating_a_supplier_also_stamps_which_tier_wrote_it(da):
    """create_supplier already stamps reference_scope; update_supplier is the
    one reference write that didn't -- harmless today, but it breaks the rule
    the future lift-migration depends on (every reference record says which
    tier wrote it).
    """
    from connect_labs.supply_chain.models import SupplierRecord
    from connect_labs.supply_chain.tests.conftest import wrap

    existing = wrap(SupplierRecord, {"name": "Northwind Nutrition"}, record_id=3)
    da.reference_client.get_record_by_id.return_value = existing
    da.update_supplier(3, {"name": "Northwind Nutrition Ltd"})
    data = da.reference_client.update_record.call_args.kwargs["data"]
    assert data["reference_scope"] == "organization"


def test_updating_a_round_passes_the_already_fetched_record_to_skip_a_second_get(da):
    """update_record accepts current_record specifically to avoid re-fetching a
    record the caller already holds. Every update site here has `existing` in
    hand, so every one of them should pass it through.
    """
    from connect_labs.supply_chain.models import RoundRecord
    from connect_labs.supply_chain.tests.conftest import wrap

    existing = wrap(RoundRecord, {"label": "Round 1", "status": "draft"}, record_id=1)
    da.program_client.get_record_by_id.return_value = existing
    da.update_round(1, {"label": "Round 1 revised"})
    assert da.program_client.update_record.call_args.kwargs["current_record"] is existing


def test_creating_a_quote_for_a_nonexistent_round_is_refused(da):
    da.program_client.get_record_by_id.return_value = None
    with pytest.raises(ValueError, match="round"):
        da.create_quote({"round_id": 999, "commodity_slug": "rutf"})
    assert not da.program_client.create_record.called


def test_creating_a_quote_for_a_nonexistent_commodity_is_refused(da):
    """The round resolves (the fixture's default get_record_by_id mock is
    truthy), but the commodity slug doesn't -- get_commodity's own default
    (get_records returning []) already reads as "not found".
    """
    with pytest.raises(ValueError, match="commodity"):
        da.create_quote({"round_id": 1, "commodity_slug": "does-not-exist"})
    assert not da.program_client.create_record.called


def test_creating_outreach_for_a_nonexistent_round_is_refused(da):
    da.program_client.get_record_by_id.return_value = None
    with pytest.raises(ValueError, match="round"):
        da.create_outreach({"round_id": 999, "supplier_id": 1})
    assert not da.program_client.create_record.called


def test_creating_an_award_for_a_nonexistent_round_is_refused(da):
    da.program_client.get_record_by_id.return_value = None
    with pytest.raises(ValueError, match="round"):
        da.create_award({"round_id": 999, "rationale": "lowest landed cost"})
    assert not da.program_client.create_record.called


def test_creating_a_purchase_for_a_nonexistent_round_is_refused(da):
    da.program_client.get_record_by_id.return_value = None
    with pytest.raises(ValueError, match="round"):
        da.create_purchase({"round_id": 999, "commodity_slug": "rutf"})
    assert not da.program_client.create_record.called


def test_creating_a_purchase_for_a_nonexistent_commodity_is_refused(da):
    with pytest.raises(ValueError, match="commodity"):
        da.create_purchase({"round_id": 1, "commodity_slug": "does-not-exist"})
    assert not da.program_client.create_record.called


def test_supersede_quote_logs_and_reraises_if_the_back_link_update_fails(da, caplog):
    """If the replacement write succeeds but the back-link update on the
    original fails, both versions currently read as live and the superseded
    quote silently re-enters comparisons. That has to be loud, not swallowed.
    """
    import logging

    from connect_labs.supply_chain.models import QuoteRecord
    from connect_labs.supply_chain.tests.conftest import wrap

    existing = wrap(QuoteRecord, {"round_id": 1, "version": 1}, record_id=5)
    da.program_client.get_record_by_id.return_value = existing
    created = wrap(QuoteRecord, {"version": 2}, record_id=6)
    da.program_client.create_record.return_value = created
    da.program_client.update_record.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            da.supersede_quote(5, {}, reason="typo fix")

    assert "back-link" in caplog.text
