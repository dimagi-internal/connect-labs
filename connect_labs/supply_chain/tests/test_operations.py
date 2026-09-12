from unittest.mock import MagicMock

import jsonschema
import pytest

from connect_labs.supply_chain.models import Contract, Outreach, Quote, Supplier
from connect_labs.supply_chain.operations import all_operations, call_operation, get_operation
from connect_labs.supply_chain.tests.conftest import RUTF as _RUTF


def test_the_registry_is_not_empty():
    assert len(all_operations()) >= 15


def test_the_reference_tier_operations_are_registered_at_the_root():
    """Reference data is shared: tracking will want the item list too."""
    for name in ("commodity_list", "commodity_upsert", "item_list", "item_get", "item_upsert"):
        assert name in all_operations()


def test_every_operation_declares_an_object_schema_with_no_extra_properties():
    for name, operation in all_operations().items():
        assert operation.input_schema["type"] == "object", name
        assert operation.input_schema.get("additionalProperties") is False, name


def test_every_operation_has_a_summary_that_could_brief_an_agent():
    for name, operation in all_operations().items():
        assert len(operation.summary) > 20, name


def test_write_operations_are_flagged_as_writes():
    for name in (
        "supplier_create",
        "round_create",
        "quote_record",
        "award_create",
        "item_upsert",
    ):
        assert get_operation(name).is_write is True


def test_read_operations_are_not_flagged_as_writes():
    for name in ("supplier_list", "round_list", "quote_list", "round_compare", "item_list"):
        assert get_operation(name).is_write is False


def test_a_payload_that_violates_the_schema_is_rejected_before_the_handler_runs():
    access = MagicMock()
    # Narrow on purpose: pytest.raises(Exception) also passes when the payload
    # reaches the handler and blows up there, which is the bug this is meant
    # to catch rather than tolerate.
    with pytest.raises(jsonschema.ValidationError):
        call_operation("supplier_create", access, {"unexpected_field": 1})
    assert not access.create_supplier.called


def test_a_valid_payload_reaches_the_data_access():
    access = MagicMock()
    # A real (unsaved) model, because the handler serialises what it gets back
    # and a MagicMock is not JSON.
    access.create_supplier.return_value = Supplier(id=3, name="Northwind Nutrition")
    call_operation("supplier_create", access, {"data": {"name": "Northwind Nutrition"}})
    assert access.create_supplier.called


def test_an_unknown_operation_raises_keyerror():
    with pytest.raises(KeyError):
        get_operation("not_an_operation")


def _quote_payload(**overrides):
    data = {
        "round_id": 1,
        "supplier_id": 2,
        "commodity_slug": "rutf",
        "as_quoted_unit": "per_pack",
        "quantity_basis": "10",
    }
    data.update(overrides)
    return {"data": data}


def test_a_float_money_amount_is_rejected():
    """A JSON Schema `pattern` is a no-op against a non-string instance, so a money
    field typed ["number", "string"] with a decimal pattern lets a float straight
    through. Money is Decimal, never float — a float that reaches the handler has
    already lost precision this schema exists to refuse."""
    access = MagicMock()
    with pytest.raises(jsonschema.ValidationError):
        call_operation("quote_record", access, _quote_payload(as_quoted_amount=12.50))
    assert not access.create_quote.called


def test_the_equivalent_string_money_amount_is_accepted():
    access = MagicMock()
    access.create_quote.return_value = Quote(id=7, commodity=_RUTF)
    call_operation("quote_record", access, _quote_payload(as_quoted_amount="12.50"))
    assert access.create_quote.called


def test_a_zero_as_quoted_amount_is_rejected():
    """MONEY's own pattern accepts "0"/"0.00" and yields a *confirmed*
    Money(0) that sorts first in a comparison. as_quoted_amount is the
    headline price -- unlike free freight or a waived duty (real facts with
    their own basis flag), a $0 quote is not something this domain can
    represent honestly, so it gets the nonzero variant."""
    access = MagicMock()
    for zero in ("0", "0.00", "0.0"):
        with pytest.raises(jsonschema.ValidationError):
            call_operation("quote_record", access, _quote_payload(as_quoted_amount=zero))
    assert not access.create_quote.called


def _contract_payload(**overrides):
    data = {
        "round_id": 1,
        "supplier_id": 2,
        "commodity_slug": "rutf",
        "supplier_id": 2,
        "buyer_of_record": "partner_org",
        "buyer_party_id": 1,
        "source": "partner_reported",
        "quantity": "10",
        "unit_price": "10.00",
        "currency": "USD",
    }
    data.update(overrides)
    return {"data": data}


def test_a_zero_unit_price_is_rejected():
    access = MagicMock()
    for zero in ("0", "0.00"):
        with pytest.raises(jsonschema.ValidationError):
            call_operation("contract_create", access, _contract_payload(unit_price=zero))
    assert not access.create_contract.called


def test_a_contract_must_name_its_buyer_of_record():
    """The field has no default on purpose: duty and VAT depend on who
    imports, so a landed cost derived without it hides an assumption."""
    access = MagicMock()
    for missing in ("buyer_of_record", "buyer_party_id"):
        payload = _contract_payload()
        del payload["data"][missing]
        with pytest.raises(jsonschema.ValidationError):
            call_operation("contract_create", access, payload)
    assert not access.create_contract.called


def test_a_contract_must_say_who_told_us():
    """Provenance is compulsory below the contract -- five of the stages
    there are ones we do not witness."""
    access = MagicMock()
    payload = _contract_payload()
    del payload["data"]["source"]
    with pytest.raises(jsonschema.ValidationError):
        call_operation("contract_create", access, payload)
    assert not access.create_contract.called


def test_an_unknown_buyer_of_record_is_rejected():
    access = MagicMock()
    with pytest.raises(jsonschema.ValidationError):
        call_operation("contract_create", access, _contract_payload(buyer_of_record="whoever"))
    assert not access.create_contract.called


def test_a_negative_or_zero_quantity_is_rejected_on_both_the_string_and_the_number_branch():
    access = MagicMock()
    for bad_quantity in (0, -1, "0", "0.0", "-1"):
        with pytest.raises(jsonschema.ValidationError):
            call_operation("contract_create", access, _contract_payload(quantity=bad_quantity))
    assert not access.create_contract.called


def test_a_positive_quantity_is_accepted_as_either_a_string_or_a_number():
    access = MagicMock()
    access.create_contract.return_value = Contract(id=1, commodity=_RUTF)
    for good_quantity in ("10", 10, 10.5, "0.5"):
        call_operation("contract_create", access, _contract_payload(quantity=good_quantity))
    assert access.create_contract.call_count == 4


# --- Finding 4: every write operation's data schema names its required
# fields, so a missing one is a 400 naming the field, not a KeyError deep
# inside data_access (a 500 that names nothing) ------------------------------


def test_quote_record_rejects_data_missing_round_id_or_commodity_slug():
    access = MagicMock()
    base = _quote_payload()["data"]
    for missing in ("round_id", "commodity_slug", "supplier_id"):
        data = {k: v for k, v in base.items() if k != missing}
        with pytest.raises(jsonschema.ValidationError):
            call_operation("quote_record", access, {"data": data})
    assert not access.create_quote.called


def test_item_upsert_rejects_data_missing_sku():
    access = MagicMock()
    with pytest.raises(jsonschema.ValidationError):
        call_operation("item_upsert", access, {"data": {"commodity_slug": "rutf"}})
    assert not access.upsert_item.called


def test_commodity_upsert_rejects_data_missing_slug():
    access = MagicMock()
    with pytest.raises(jsonschema.ValidationError):
        call_operation("commodity_upsert", access, {"data": {"name": "RUTF"}})
    assert not access.upsert_commodity.called


def test_outreach_log_rejects_data_missing_round_id():
    access = MagicMock()
    with pytest.raises(jsonschema.ValidationError):
        call_operation("outreach_log", access, {"data": {"supplier_id": 1}})
    assert not access.create_outreach.called


def test_contract_create_rejects_data_missing_its_commodity():
    """A contract without a commodity cannot be matched to a receipt, so the
    goods arriving under it would land nowhere."""
    access = MagicMock()
    payload = _contract_payload()
    del payload["data"]["commodity_slug"]
    with pytest.raises(jsonschema.ValidationError):
        call_operation("contract_create", access, payload)
    assert not access.create_contract.called


# --- Regression (final review, item A): required on _QUOTE_DATA/_OUTREACH_DATA
# must not land on the schemas SHARED with quote_correct/outreach_update --
# both are partial updates (data_access merges {**existing.data, **data}), so
# round_id/commodity_slug already live on the existing record. Forcing a
# caller to resupply them on a correction is not just friction: a wrong
# resupplied value merges straight into the record. quote_record/outreach_log
# alone use the *_CREATE variant that carries the requirement.


def test_quote_correct_succeeds_with_a_partial_payload_naming_only_the_fix():
    """A correction to a transcribed amount must not need round_id/
    commodity_slug re-supplied -- those already live on the existing quote."""
    access = MagicMock()
    access.supersede_quote.return_value = Quote(id=8, commodity=_RUTF)
    call_operation(
        "quote_correct",
        access,
        {"quote_id": 1, "data": {"as_quoted_amount": "52.42"}, "reason": "transcription error"},
    )
    assert access.supersede_quote.called


def test_outreach_update_succeeds_with_a_partial_payload_naming_only_the_response():
    """outreach_update's own summary is 'typically to record that a supplier
    responded, and how' -- exactly a responded/response_kind-only payload,
    naming neither round_id nor supplier_id."""
    access = MagicMock()
    access.update_outreach.return_value = Outreach(id=9)
    call_operation(
        "outreach_update",
        access,
        {"outreach_id": 1, "data": {"responded": True, "response_kind": "quote"}},
    )
    assert access.update_outreach.called


def test_a_none_valued_parameter_is_treated_as_not_supplied():
    """A caller computing `commodity_slug = request.GET.get(...)` and passing
    it through should not have to strip its own Nones, and widening every
    optional parameter to accept null would weaken the contract an agent
    reads to say the same thing."""
    access = MagicMock()
    access.list_contracts.return_value = []
    assert call_operation("contract_list", access, {"round_id": None, "status": None}) == []
    access.list_contracts.assert_called_once_with(round_id=None, status=None)


def test_a_none_inside_a_data_payload_is_still_passed_through():
    """Only the top level is stripped. A None inside `data` is meaningful
    where the schema says so: detaching a document wrongly attached as a duty
    exemption is a real operation, and `duty_relief_document_id` is declared
    NULLABLE_ID for it."""
    access = MagicMock()
    access.update_contract.return_value = Contract(id=1, commodity=_RUTF)
    call_operation("contract_update", access, {"contract_id": 1, "data": {"duty_relief_document_id": None}})
    assert access.update_contract.call_args[0][1] == {"duty_relief_document_id": None}
