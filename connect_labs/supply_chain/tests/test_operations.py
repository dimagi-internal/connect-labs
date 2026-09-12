from unittest.mock import MagicMock

import pytest

from connect_labs.supply_chain.operations import all_operations, call_operation, get_operation


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
    with pytest.raises(Exception):
        call_operation("supplier_create", access, {"unexpected_field": 1})
    assert not access.create_supplier.called


def test_a_valid_payload_reaches_the_data_access():
    access = MagicMock()
    access.create_supplier.return_value = MagicMock(id=3, name="Northwind Nutrition")
    call_operation("supplier_create", access, {"data": {"name": "Northwind Nutrition"}})
    assert access.create_supplier.called


def test_an_unknown_operation_raises_keyerror():
    with pytest.raises(KeyError):
        get_operation("not_an_operation")
