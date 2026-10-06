"""Editing a table cell in place (cells.py): typed text in, the record's own operation out."""

import json
from datetime import date
from unittest import mock

import pytest
from django.template import Context, Template

from connect_labs.supply_chain import cells
from connect_labs.supply_chain.operations import get_operation

# ---- the registry is the contract ----------------------------------------------


@pytest.mark.parametrize(
    "kind,name",
    [(kind, name) for kind, spec in cells.KINDS.items() for name in spec.fields],
)
def test_every_editable_field_is_one_its_operation_accepts(kind, name):
    """A field the registry lists but the operation's schema does not would be refused on every edit.

    The tender's duty terms and import estimates have operations of their own, so they are
    checked against those.
    """
    own = {
        "duty_terms": ("tender_set_duty_terms", None),
        "clearing_estimate_per_unit": ("tender_set_import_estimates", None),
        "freight_estimate_per_unit": ("tender_set_import_estimates", None),
    }
    operation_name, _ = own.get(name) if kind == "tender" and name in own else (cells.KINDS[kind].operation, None)
    schema = get_operation(operation_name).input_schema["properties"]
    accepted = schema["data"]["properties"] if "data" in schema else schema
    assert name in accepted, f"{operation_name} does not take {name}"


def test_a_choice_cell_offers_only_values_its_schema_allows():
    for kind, spec in cells.KINDS.items():
        for name, cell in spec.fields.items():
            if cell.type != cells.CHOICE or (kind, name) == ("tender", "duty_terms"):
                continue
            props = get_operation(spec.operation).input_schema["properties"]["data"]["properties"]
            assert {v for v, _ in cell.choices} <= set(props[name]["enum"]), (kind, name)


# ---- typed text → a value -----------------------------------------------------


def test_money_is_sent_as_a_decimal_string_with_the_currency_and_commas_dropped():
    assert cells.coerce(cells.Cell("price", cells.MONEY), "USD 1,200.50") == "1200.5"
    assert cells.coerce(cells.Cell("price", cells.MONEY), "0.3495") == "0.3495"


def test_a_whole_number_refuses_words_and_names_the_field():
    with pytest.raises(ValueError, match="Pack size must be a whole number"):
        cells.coerce(cells.Cell("pack size", cells.INT), "abc")
    assert cells.coerce(cells.Cell("pack size", cells.INT), "150") == 150


def test_a_date_reads_the_way_the_page_writes_days():
    cell = cells.Cell("replied", cells.DATE)
    assert cells.coerce(cell, "6 Oct 2026") == "2026-10-06"
    assert cells.coerce(cell, "2026-10-06") == "2026-10-06"
    with pytest.raises(ValueError, match="must be a date"):
        cells.coerce(cell, "next week")


def test_an_emptied_cell_clears_only_where_clearing_is_allowed():
    assert cells.coerce(cells.Cell("valid until", cells.DATE, clearable=True), "  ") is None
    with pytest.raises(ValueError, match="Price cannot be empty"):
        cells.coerce(cells.Cell("price", cells.MONEY), "")


def test_a_choice_outside_its_list_is_refused():
    cell = cells.KINDS["outreach"].fields["response_kind"]
    with pytest.raises(ValueError, match="Choose one"):
        cells.coerce(cell, "maybe")


# ---- each cell runs its record's operation --------------------------------------


@pytest.fixture
def ran():
    with mock.patch("connect_labs.supply_chain.cells.call_operation") as call:
        call.return_value = {"id": 99}
        yield call


def test_a_quote_cell_is_a_correction_with_a_reason(ran):
    result = cells.apply("access", "quote", 7, "as_quoted_amount", "52.00", was="55")
    name, _, payload = ran.call_args.args
    assert name == "quote_correct"
    assert payload == {
        "quote_id": 7,
        "data": {"as_quoted_amount": "52"},
        "reason": "price 55 → 52",
    }
    # The correction is a new version: the page marks the NEW quote's cell.
    assert result == {"key": "quote:99:as_quoted_amount"}


def test_a_correction_reason_names_the_field_and_both_values(ran):
    """Not "Corrected in a table": the reason says what changed, read off the record before the edit."""

    class Access:
        def get_quote(self, quote_id):
            return mock.Mock(received_on=date(2026, 10, 6), base_per_pack_stated=None)

    cells.apply(Access(), "quote", 7, "received_on", "5 Oct 2026", was="2026-10-01")
    assert ran.call_args.args[2]["reason"] == "received 6 Oct → 5 Oct"
    cells.apply(Access(), "quote", 7, "base_per_pack_stated", "150")
    assert ran.call_args.args[2]["reason"] == "pack size not stated → 150"


def test_a_pack_figure_typed_in_is_recorded_as_stated_on_the_quote(ran):
    cells.apply("access", "quote", 7, "base_per_pack_stated", "150")
    assert ran.call_args.args[2]["data"] == {"base_per_pack_stated": 150, "pack_spec_source": "stated_on_quote"}


def test_a_reply_day_marks_the_invitation_replied(ran):
    cells.apply("access", "outreach", 3, "responded_on", "23 Sep 2026")
    assert ran.call_args.args[:2] == ("outreach_update", "access")
    assert ran.call_args.args[2] == {"outreach_id": 3, "data": {"responded_on": "2026-09-23", "responded": True}}


def test_no_reply_as_a_kind_is_not_a_reply(ran):
    cells.apply("access", "outreach", 3, "response_kind", "3")  # its position in the list
    assert ran.call_args.args[2]["data"] == {"response_kind": "no_reply", "responded": False}


def test_import_duty_is_settled_by_its_own_operation(ran):
    cells.apply("access", "tender", 5, "duty_terms", "1")
    assert ran.call_args.args[0] == "tender_set_duty_terms"
    assert ran.call_args.args[2] == {"tender_id": 5, "duty_terms": "buyer_waiver"}


def test_an_emptied_estimate_is_cleared_not_kept(ran):
    """tender_set_import_estimates reads an omitted value as keep and '' as clear."""
    cells.apply("access", "tender", 5, "freight_estimate_per_unit", "")
    assert ran.call_args.args[2] == {"tender_id": 5, "freight_estimate_per_unit": ""}


def test_a_field_not_in_the_registry_is_refused_before_anything_runs(ran):
    with pytest.raises(ValueError, match="not editable"):
        cells.apply("access", "quote", 7, "tender_id", "1")
    ran.assert_not_called()


# ---- the template tag --------------------------------------------------------------


def _render(context):
    return Template('{% load supply_chain_extras %}<td {% edit_cell "quote" q_id "validity_until" v %}>').render(
        Context(context)
    )


def test_the_tag_marks_a_cell_with_its_key_type_and_stored_value():
    html = _render({"q_id": 7, "v": date(2026, 12, 1)})
    assert 'data-edit="quote:7:validity_until"' in html
    assert 'data-edit-type="date"' in html
    assert 'data-edit-value="2026-12-01"' in html


def test_the_tag_marks_nothing_on_a_page_showing_the_past():
    assert "data-edit" not in _render({"q_id": 7, "v": None, "supply_as_of": date(2026, 9, 1)})


def test_a_choice_reaches_the_page_as_its_position_and_words_never_its_code():
    html = Template('{% load supply_chain_extras %}<td {% edit_cell "contract" 2 "status" v %}>').render(
        Context({"v": "part_received"})
    )
    assert "part_received" not in html
    assert 'data-edit-value="3"' in html
    assert "part received" in html


def test_the_tag_escapes_a_stored_value():
    html = Template('{% load supply_chain_extras %}<td {% edit_cell "quote" 7 "supplier_reference" v %}>').render(
        Context({"v": '"><script>'})
    )
    assert "<script>" not in html
    assert "&quot;&gt;&lt;script&gt;" in html


# ---- the endpoint ------------------------------------------------------------------


@pytest.mark.django_db
def test_the_endpoint_returns_the_refusal_for_the_cell_to_show(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="jo", password="x", email="jo@dimagi.com"))
    with (
        mock.patch("connect_labs.supply_chain.cells.has_program_context", return_value=True),
        mock.patch("connect_labs.supply_chain.cells._access", return_value="access"),
        mock.patch("connect_labs.supply_chain.cells.call_operation") as call,
    ):
        refused = client.post(
            "/supply/cells/", json.dumps({"cell": "quote:7:lead_time_days", "value": "soon"}), "application/json"
        )
        saved = client.post(
            "/supply/cells/", json.dumps({"cell": "quote:7:lead_time_days", "value": "30"}), "application/json"
        )
    assert refused.status_code == 400
    assert refused.json() == {"error": "Lead time (days) must be a whole number, not 'soon'."}
    assert saved.status_code == 200
    assert saved.json()["ok"] is True
    assert call.call_count == 1


@pytest.mark.django_db
def test_the_endpoint_refuses_a_malformed_cell(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="jo", password="x", email="jo@dimagi.com"))
    with mock.patch("connect_labs.supply_chain.cells.has_program_context", return_value=True):
        response = client.post("/supply/cells/", json.dumps({"cell": "quote:seven"}), "application/json")
    assert response.status_code == 400


def test_an_invoice_status_reads_disputed_as_the_page_does_and_sends_queried():
    html = Template('{% load supply_chain_extras %}<td {% edit_cell "invoice" 4 "status" v %}>').render(
        Context({"v": "queried"})
    )
    assert "disputed" in html and "queried" not in html
    position = [code for code, _ in cells.KINDS["invoice"].fields["status"].choices].index("queried")
    assert cells.coerce(cells.KINDS["invoice"].fields["status"], str(position)) == "queried"
